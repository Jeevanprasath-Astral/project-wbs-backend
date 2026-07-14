from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/project_wbs"
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    APP_NAME: str = "Project WBS"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    DEBUG: bool = True
    FRONTEND_URL: str = "http://localhost:5173"
    MAIL_USERNAME: str = "contact@connectome.co.in"
    MAIL_PASSWORD: str = ""          # Set MAIL_PASSWORD env var on Render
    MAIL_FROM: str = "contact@connectome.co.in"
    MAIL_PORT: int = 587
    MAIL_SERVER: str = "smtp.office365.com"
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False
    MAIL_ENABLED: bool = True
    RESEND_API_KEY: str = ""          # Set RESEND_API_KEY env var on Render
    REDIS_URL: str = "redis://localhost:6379/0"

    # Cloudinary — file storage for attachments and cost documents
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""

    class Config:
        env_file = ".env"

settings = Settings()
