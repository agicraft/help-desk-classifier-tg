import os
import asyncio
import aiohttp
import logging
from typing import Optional, Dict, Any, List
from telebot.async_telebot import AsyncTeleBot
from dotenv import load_dotenv
from aiohttp import ClientTimeout, TCPConnector
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class Config:
    def __init__(self):  
        load_dotenv()
        self.BOT_TOKEN: str = os.getenv('BOT_TOKEN', '')
        self.API_URL: str = os.getenv('API_URL', '')
        self.MAX_RETRIES: int = int(os.getenv('MAX_RETRIES', '3'))
        self.TIMEOUT: int = int(os.getenv('TIMEOUT', '120'))  # Увеличенный таймаут
        self.MAX_TEXT_LENGTH: int = int(os.getenv('MAX_TEXT_LENGTH', '4096'))
        self.RETRY_DELAY: int = int(os.getenv('RETRY_DELAY', '5'))
        
        if not self.BOT_TOKEN:
            raise ValueError("BOT_TOKEN не найден в .env файле")

config = Config()
bot = AsyncTeleBot(config.BOT_TOKEN)

class APIResponse:
    def __init__(self, success: bool, data: Optional[Dict] = None, error: Optional[str] = None):
        self.success = success
        self.data = data or {}
        self.error = error

class APIClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.timeout = ClientTimeout(total=config.TIMEOUT)

    async def classify_message(self, text: str) -> APIResponse:
        """Отправляет запрос на классификацию сообщения."""
        if len(text) > config.MAX_TEXT_LENGTH:
            logger.warning(f"Текст сообщения превышает {config.MAX_TEXT_LENGTH} символов и будет обрезан")
            text = text[:config.MAX_TEXT_LENGTH]

        data = {
            "text": text,
            "name": None,
            "topic": None,
            "generate_answer": True
        }
        
        try:
            async with self.session.post(
                config.API_URL,
                json=data,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout
            ) as response:
                response_text = await response.text()
                logger.debug(f"API ответ: {response_text}")
                
                if response.status == 200:
                    result = await response.json()
                    return APIResponse(success=True, data=result)
                    
                return APIResponse(
                    success=False, 
                    error=f"API вернул статус {response.status}: {response_text}"
                )
                
        except asyncio.TimeoutError:
            return APIResponse(success=False, error="Превышено время ожидания ответа")
        except Exception as e:
            return APIResponse(success=False, error=str(e))

class MessageFormatter:
    @staticmethod
    def format_successful_classification(attributes: List[Dict[str, str]], keywords: Optional[List[str]] = None) -> str:
        """Форматирует успешный ответ классификации."""
        attr_names = {
            "equipment_type": "Тип оборудования",
            "failure_point": "Тип неисправности",
            "serial_number": "Серийный номер"
        }
        
        reply = "✅ Заявка успешно классифицирована:\n\n"
        
        for attr in attributes:
            name = attr_names.get(attr['name'], attr['name'])
            reply += f"🔹 {name}: {attr['value']}\n"
        
        if keywords:
            reply += f"\n🔑 Ключевые слова: {', '.join(keywords)}"
            
        return reply

    @staticmethod
    def format_missing_info(missing_attrs: List[str], recognized_attrs: Optional[List[Dict[str, str]]] = None) -> str:
        """Форматирует ответ с запросом дополнительной информации."""
        attr_names = {
            "equipment_type": "тип оборудования",
            "failure_point": "тип неисправности",
            "serial_number": "серийный номер"
        }
        
        reply = "⚠️ Для обработки заявки требуется дополнительная информация\n\n"
        
        if recognized_attrs:
            reply += "✓ Уже распознано:\n"
            for attr in recognized_attrs:
                name = attr_names.get(attr['name'], attr['name'])
                reply += f"- {name}: {attr['value']}\n"
            reply += "\n"
            
        reply += "❗️ Пожалуйста, уточните:\n"
        for attr in missing_attrs:
            readable_name = attr_names.get(attr, attr)
            reply += f"- {readable_name}\n"
            
        return reply

async def process_api_response(response: APIResponse) -> str:
    """Обрабатывает ответ API и формирует сообщение для пользователя."""
    if not response.success:
        logger.error(f"Ошибка API: {response.error}")
        return "Произошла ошибка при обработке заявки. Пожалуйста, попробуйте позже."

    data = response.data
    
    # Если есть готовый ответ от API
    if data.get('answer'):
        return data['answer']
        
    # Нормализуем атрибуты
    if isinstance(data.get('attributes'), dict):
        data['attributes'] = [
            {'name': name, 'value': value}
            for name, value in data['attributes'].items()
            if value is not None
        ]
    
    if data.get('valid'):
        return MessageFormatter.format_successful_classification(
            data.get('attributes', []),
            data.get('keywords', [])
        )
    else:
        return MessageFormatter.format_missing_info(
            data.get('missingAttributes', []),
            data.get('attributes', [])
        )

@bot.message_handler(commands=['start', 'help'])
async def start_message(message):
    """Обработчик команд /start и /help."""
    logger.info(f"Пользователь {message.from_user.id} запустил команду {message.text}")
    await bot.reply_to(message, 
        "Привет! Я бот для классификации заявок в техподдержку. 🤖\n\n"
        "Для корректной классификации укажите в заявке:\n"
        "- Тип оборудования (например: ноутбук, сервер, коммутатор)\n"
        "- Тип неисправности (например: не включается, не работает экран, зависает)\n"
        "- Серийный номер (если есть)\n\n"
        "Пример заявки:\n"
        "'Не работает ноутбук HP, серийный номер ABC123. Не включается совсем.'\n\n"
        "Отправьте мне описание вашей проблемы, и я помогу её классифицировать! 👍"
    )

@bot.message_handler(func=lambda message: True)
async def handle_message(message):
    """Обработчик всех текстовых сообщений."""
    user_id = message.from_user.id
    logger.info(f"Получено сообщение от пользователя {user_id}: {message.text}")
    
    connector = TCPConnector(force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        api_client = APIClient(session)
        
        for attempt in range(config.MAX_RETRIES):
            try:
                response = await api_client.classify_message(message.text)
                reply = await process_api_response(response)
                await bot.reply_to(message, reply)
                break
            except Exception as e:
                logger.error(f"Попытка {attempt + 1} не удалась: {str(e)}")
                if attempt == config.MAX_RETRIES - 1:
                    await bot.reply_to(
                        message,
                        "Извините, произошла ошибка при обработке вашего сообщения. "
                        "Пожалуйста, попробуйте позже."
                    )
                else:
                    await asyncio.sleep(config.RETRY_DELAY)

async def main():
    """Основная функция запуска бота."""
    logger.info("Запуск бота...")
    while True:
        try:
            await bot.polling(non_stop=True, timeout=60)
        except Exception as e:
            logger.error(f"Ошибка в работе бота: {str(e)}", exc_info=True)
            await asyncio.sleep(5)  # Пауза перед перезапуском
            logger.info("Перезапуск бота...")

if __name__ == "__main__":
    asyncio.run(main())