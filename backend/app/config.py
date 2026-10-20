"""Configuración central: se lee desde backend/.env (nunca se hardcodea)."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

# El valor por defecto es a propósito reconocible: `app.arranque` se niega a arrancar un despliegue
# público que lo siga usando, en vez de firmar sesiones con una clave que está en el repositorio.
SECRETO_DE_DESARROLLO = "dev-only-secret-0123456789abcdef0123456789abcdef"

# asyncpg es el único driver instalado y Alembic migra con `async_engine_from_config` (`env.py`), así
# que una URL sin `+asyncpg` no llega a conectar nunca. Los proveedores de base de datos entregan
# justamente eso: Railway, Neon, Supabase o Render dan `postgresql://user:pass@host:6543/db?sslmode=require`
# y la dan con el nombre `DATABASE_URL`, que es el de esta app. Dos fallos en el primer arranque:
#
#   - sin `+asyncpg` SQLAlchemy busca psycopg, que no está instalado;
#   - con `+asyncpg` puesto, `sslmode` se pasa tal cual a `asyncpg.connect()`, que no tiene ese
#     argumento: `TypeError: connect() got an unexpected keyword argument 'sslmode'` antes de abrir
#     el socket. `ssl` sí acepta los modos de libpq (asyncpg los parsea con su enum `SSLMode`).
#
# Se normaliza aquí y no en el panel del host porque el valor que inyecta el proveedor no se puede
# editar: o lo traduce la app, o el despliegue se cae en la migración, que es lo primero que corre.
_NOMBRES_DE_POSTGRES = {"postgres", "postgresql"}


def normalizar_url_database(valor: str) -> str:
    """La URL en la forma que esta app puede abrir. Lo que no es PostgreSQL se queda igual.

    sqlite y compañía no se tocan a propósito: el guardia de arranque tiene que poder enseñar el valor
    original en su mensaje, y reescribirlo sería arreglar por casualidad algo que está mal.
    """
    try:
        url = make_url(valor)
    except Exception:
        return valor  # ilegible: que lo diga `app.arranque` con el texto tal cual
    if (url.drivername or "").split("+", 1)[0] not in _NOMBRES_DE_POSTGRES:
        return valor
    consulta = dict(url.query)
    modo = consulta.pop("sslmode", None)
    if modo and "ssl" not in consulta:
        consulta["ssl"] = modo
    return url.set(drivername="postgresql+asyncpg", query=consulta).render_as_string(hide_password=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # La del host tal cual: `postgresql://…?sslmode=require` también vale, se normaliza abajo.
    database_url: str = "postgresql+asyncpg://postgres@127.0.0.1:5433/agenda_dev"
    jwt_secret: str = SECRETO_DE_DESARROLLO
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

    # Carpeta con el `vite build` (frontend/build en el monorepo). Si no existe, la API no sirve
    # interfaz y sigue respondiendo solo por /api: así el backend se puede levantar solo, sin Node.
    frontend_build: str | None = None

    # 'desarrollo' | 'produccion'. Es el interruptor de las comprobaciones de `app.arranque` y de la
    # cookie de sesión: un despliegue público con este valor sin cambiar se levanta con los secretos
    # de desarrollo, que es justo el caso que no se puede colar.
    entorno: str = "desarrollo"

    @field_validator("database_url", mode="before")
    @classmethod
    def _al_drivers_de_asyncpg(cls, valor: object) -> object:
        """Vale tanto la URL de siempre como la que inyecta el host; se guarda en la forma usable."""
        return normalizar_url_database(valor) if isinstance(valor, str) else valor

    @property
    def entorno_normalizado(self) -> str:
        return self.entorno.strip().lower().replace("ó", "o").replace("-", "_")

    @property
    def es_produccion(self) -> bool:
        return self.entorno_normalizado in {"produccion", "prod"}

    @property
    def es_desarrollo(self) -> bool:
        return self.entorno_normalizado == "desarrollo"


@lru_cache
def get_settings() -> Settings:
    return Settings()
