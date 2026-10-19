"""Configuración central: se lee desde backend/.env (nunca se hardcodea)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres@127.0.0.1:5433/agenda_dev"
    jwt_secret: str = "dev-only-secret-0123456789abcdef0123456789abcdef"
    jwt_ttl_dias: int = 30
    frontend_origin: str = "http://localhost:5173"

    # Recordatorios. Por defecto NO se manda nada fuera: 'simulado' guarda lo que habría enviado.
    # 'smtp' exige host y remitente; las credenciales van en .env, que nunca se sube.
    recordatorios_proveedor: str = "simulado"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_usuario: str | None = None
    smtp_contrasena: str | None = None
    smtp_de: str | None = None
    smtp_ssl: bool = False
    # Lo que manda el cron externo en la cabecera x-cron-secret. Sin valor, el endpoint está apagado.
    cron_secret: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
