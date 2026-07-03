"""
Production configuration for Vama Backend.
Load from environment variables or .env file.
"""
import os
from typing import Optional
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """Environment configuration"""
    
    # Database
    database_url: str = os.getenv("DATABASE_URL", "postgresql://localhost/vama")
    
    # Security
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-secret-key")
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24
    
    # Email
    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from_name: str = "Vama Academy"
    smtp_from_email: str = os.getenv("SMTP_FROM_EMAIL", "noreply@vama.example.com")
    
    # Razorpay
    razorpay_key_id: str = os.getenv("RAZORPAY_KEY_ID", "")
    razorpay_key_secret: str = os.getenv("RAZORPAY_KEY_SECRET", "")
    
    # Frontend
    frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost:5173")
    cors_origins: list = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
    ]
    
    # App
    api_title: str = "Vama Academy API"
    api_version: str = "1.0.0"
    environment: str = os.getenv("ENVIRONMENT", "development")
    debug: bool = environment != "production"
    
    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()
