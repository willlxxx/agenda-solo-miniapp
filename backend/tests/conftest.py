"""Fixtures de prueba.

Usan una base PostgreSQL real (agenda_test) migrada con la migración 0001: el motor de
disponibilidad de la Semana 2 depende de comportamiento concreto de PG (citext, índices
parciales, advisory locks), así que no simulamos la base de datos.
"""

import asyncio
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

DEFAULT_URLS = {
    "admin": "postgresql://postgres@127.0.0.1:5433/postgres",
    "test": "postgresql+asyncpg://postgres@127.0.0.1:5433/agenda_test",
}


def _derive(url_base: str, database: str, scheme: str) -> str:
    partes = urlsplit(url_base)
    return urlunsplit(partes._replace(scheme=scheme, path=f"/{database}"))


# En local usamos los defaults; en CI basta con exportar DATABASE_URL y derivamos
# usuario, host y puerto desde ahí (solo cambia el nombre de la base).
_semilla = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or DEFAULT_URLS["test"]
TEST_URL = _semilla if "agenda_test" in _semilla else _derive(_semilla, "agenda_test", "postgresql+asyncpg")
ADMIN_URL = os.environ.get("TEST_ADMIN_URL") or _derive(TEST_URL, "postgres", "postgresql")

# Debe definirse ANTES de importar la app: Settings lee DATABASE_URL del entorno.
os.environ["DATABASE_URL"] = TEST_URL

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]

# NullPool: pytest-asyncio crea un event loop por test y las conexiones no pueden viajar entre loops.


@pytest.fixture(scope="session")
def _database() -> str:
    """Crea agenda_test de cero y le aplica la migración 0001."""

    async def preparar() -> None:
        import asyncpg

        conn = await asyncpg.connect(ADMIN_URL)
        existe = await conn.fetchval("select 1 from pg_database where datname='agenda_test'")
        if existe:
            await conn.execute("DROP DATABASE agenda_test WITH (FORCE)")
        await conn.execute("CREATE DATABASE agenda_test")
        await conn.close()

    asyncio.run(preparar())
    # Alembic en proceso: evitar depender del PATH del ejecutable (Windows).
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(cfg, "head")
    return TEST_URL


@pytest.fixture
async def limpio(_database: str) -> None:
    """Tablas vacías antes de cada test: el orden de ejecución deja de importar."""
    from sqlalchemy import text

    engine = create_async_engine(_database, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE appointments, waitlist, patients, services, availability, blocked_dates, doctors CASCADE")
        )
    await engine.dispose()


@pytest.fixture
async def client(limpio, _database: str):
    """AsyncClient sobre la app real, con la sesión apuntando a agenda_test."""
    from app.api import deps
    from app.main import app

    engine = create_async_engine(_database, poolclass=NullPool)
    deps.SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://app.test") as c:
        yield c

    await engine.dispose()


# --- Helpers ---

DOCTOR_GARCIA = {
    "nombre": "Dra. García Peña",
    "email": "garcia@example.com",
    "password": "senya-de-prueba-1",
    "timezone": "Europe/Madrid",
}

DOCTOR_LOPEZ = {
    "nombre": "Dr Lopez",
    "email": "lopez@example.com",
    "password": "otra-senya-prueba-2",
    "timezone": "Europe/Madrid",
}


async def registrar(client: AsyncClient, datos: dict | None = None) -> dict:
    resp = await client.post("/api/v1/auth/registro", json=datos or DOCTOR_GARCIA)
    assert resp.status_code == 201, resp.text
    return resp.json()


def nueva_sesion() -> AsyncClient:
    """Cliente HTTP aparte: simula el navegador de otro doctor sin compartir cookies."""
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://app.test")


async def insertar_cita(
    doctor_id: str,
    *,
    inicio_utc,
    duracion: int = 30,
    estado: str = "confirmada",
) -> None:
    """Cita directa en BD.

    Semana 2 todavía no tiene endpoint de reserva, y el motor de disponibilidad necesita
    citas ya puestas para poder comprobar que las descuenta.
    """
    from app.api import deps
    from app.models import Appointment

    identificador = doctor_id if isinstance(doctor_id, uuid.UUID) else uuid.UUID(str(doctor_id))
    async with deps.SessionLocal() as session:
        session.add(
            Appointment(
                doctor_id=identificador,
                fecha_hora=inicio_utc,
                duracion_minutos=duracion,
                estado=estado,
                consentimiento_ok_en=inicio_utc,
            )
        )
        await session.commit()


async def sql_filas(consulta: str, **params) -> list[tuple]:
    """Lectura directa en agenda_test para afirmar sobre lo que realmente quedó en BD."""
    from sqlalchemy import text

    from app.api import deps

    async with deps.SessionLocal() as session:
        resultado = await session.execute(text(consulta), params)
        return [tuple(fila) for fila in resultado]


@pytest.fixture
async def doctor(client: AsyncClient) -> dict:
    return await registrar(client)
