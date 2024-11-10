from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Telegram settings
    TELEGRAM_TOKEN: str = ""
    
    # API settings
    API_BASE_URL: str = "http://localhost:8080"
    API_ENDPOINT: str = "/api/analyze"
    
    class Config:
        env_file = ".env"

settings = Settings()