"""Configuración central: se lee desde backend/.env (nunca se hardcodea)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres@127.0.0.1:5433/agenda_dev"
    jwt_secret: str = "dev-only-secret-0123456789abcdef0123456789abcdef"
    jwt_ttl_dias: int = 30
    frontend_origin: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
