"""Configuration from environment variables."""

import os
from typing import Optional
from pydantic_settings import BaseSettings
from pathlib import Path


# Get base directory (backend folder)
BASE_DIR = Path(__file__).resolve().parent.parent
# Project root (one level up from backend)
PROJECT_ROOT = BASE_DIR.parent


class Settings(BaseSettings):
    """Application settings."""

    # AI Provider (required)
    AI_BASE_URL: str = ""
    AI_API_KEY: str = ""
    AI_MODEL: str = "gpt-4o-mini"
    AI_TEMPERATURE: float = 0.7
    AI_MAX_TOKENS: int = 1000

    # GigaChat specific (for auto token refresh)
    GIGACHAT_CREDENTIALS: Optional[str] = None

    # Storage
    STORAGE_TYPE: str = "json"  # json, sqlite, postgres
    DATABASE_URL: Optional[str] = None

    # Supabase live data (optional)
    SUPABASE_URL: Optional[str] = None
    SUPABASE_SERVICE_ROLE_KEY: Optional[str] = None
    SUPABASE_TABLE_PREFIX: str = "aftora_"
    SUPABASE_TABLE_DOORS: str = "aftora_doors"
    SUPABASE_TABLE_PROMOTIONS: str = ""
    SUPABASE_TABLE_COMPANY: str = ""
    SUPABASE_CONTEXT_MAX_ITEMS: int = 5
    SUPABASE_CACHE_TTL_SECONDS: int = 120
    SUPABASE_TIMEOUT_SECONDS: int = 20

    # Telegram Alerts (optional)
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    TELEGRAM_TRANSCRIPT_ENABLED: bool = True

    # Server
    PORT: int = 8080
    DEBUG: bool = False
    CORS_ORIGINS: str = "*"
    CORS_ORIGIN: Optional[str] = None

    # Paths (can be overridden via env for Docker)
    KNOWLEDGE_PATH: Optional[str] = None
    DATA_PATH: Optional[str] = None

    # RAG / vector search
    ENABLE_RAG: bool = True
    RAG_TOP_K: int = 5
    RAG_CHUNK_SIZE: int = 900
    RAG_CHUNK_OVERLAP: int = 120
    RAG_EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    RAG_USE_ZVEC: bool = True
    RAG_FALLBACK_MAX_CHARS: int = 12000

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Set paths after initialization
        # In Docker: /app/knowledge, /app/data
        # Local: project_root/knowledge, project_root/data
        if not self.KNOWLEDGE_PATH:
            # Check if running in Docker (backend/app is at /app/app)
            if BASE_DIR == Path("/app"):
                self.KNOWLEDGE_PATH = "/app/knowledge"
            else:
                self.KNOWLEDGE_PATH = str(PROJECT_ROOT / "knowledge")
        if not self.DATA_PATH:
            if BASE_DIR == Path("/app"):
                self.DATA_PATH = "/app/data"
            else:
                self.DATA_PATH = str(PROJECT_ROOT / "data")

        # Backward compatibility for single-origin env name used in some deploy setups.
        if self.CORS_ORIGIN and (not self.CORS_ORIGINS or self.CORS_ORIGINS == "*"):
            self.CORS_ORIGINS = self.CORS_ORIGIN

    class Config:
        env_file = str(BASE_DIR / ".env")  # Use absolute path
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"  # Ignore extra fields in .env


settings = Settings()


def validate_settings():
    """Validate required settings."""
    errors = []

    if not settings.AI_BASE_URL:
        errors.append("AI_BASE_URL is required")
    if not settings.AI_API_KEY:
        errors.append("AI_API_KEY is required")

    if settings.STORAGE_TYPE == "postgres" and not settings.DATABASE_URL:
        errors.append("DATABASE_URL is required for postgres storage")

    if errors:
        raise ValueError("Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors))
