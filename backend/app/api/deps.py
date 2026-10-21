"""Dependencias de FastAPI: sesión de BD y doctor autenticado."""

import hmac
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.models import Doctor
from app.security import decodificar_token
from app.services.correo import Canal, Proveedor, canal_desde_configuracion, proveedor_desde_configuracion


async def get_ahora() -> datetime:
    """El reloj de la pasada, en UTC.

    Es dependencia por una razón práctica: el despacho decide qué se manda según la hora, y sin esta
    costura no se podría probar una pasada de las 03:00 de la madrugada sin esperar a que el CI corra
    a las 03:00. El valor real es `now()`: nadie la pisa en producción.
    """
    return datetime.now(UTC)


async def get_proveedor_correo() -> Proveedor:
    """El canal de salida de los recordatorios, el que diga `.env`.

    Es dependencia y no una llamada suelta dentro del endpoint por los tests: así pueden inyectar
    el proveedor simulado y probar el despacho entero sin credenciales ni mensajes reales. Un SMTP
    mal configurado es 503, no 500: el cron tiene que leer qué le falta al despliegue.
    """
    try:
        return proveedor_desde_configuracion()
    except ValueError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


async def get_canal_correo() -> Canal:
    """El canal dicho sin conectar y sin fallar: es lo que lee la pantalla de acceso.

    Hace falta una dependencia aparte porque `get_proveedor_correo` construye el proveedor real, y eso
    en un despliegue con `smtp` a medias lanza. La recuperación quiere preguntar "¿puedo ofrecerte esto?"
    antes de que nadie pulse el botón, y esa pregunta no debe abrir un socket ni devolver un 503.
    """
    return canal_desde_configuracion()


def comprobar_secreto_cron(cabecera: str | None, *, apagado: str) -> None:
    """La puerta de las rutas internas: mismo secreto, mismo comportamiento en todas.

    Está compartido porque las dos pasadas (recordatorios y limpieza) son iguales desde fuera: un
    `curl` con una cabecera. Si el secreto no está configurado se responde 503 en vez de dejar la
    ruta abierta --es una ruta que escribe y borra en la agenda de los médicos-- y la comparación es
    `compare_digest` para que el tiempo que se tarda en rechazar no dependa de cuántos aciertos haya
    por delante. En bytes, porque con caracteres fuera de ASCII `compare_digest` lanza TypeError.
    """
    secreto = (get_settings().cron_secret or "").strip()
    if not secreto:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=apagado)
    if not cabecera or not hmac.compare_digest(cabecera.encode(), secreto.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Secreto de cron incorrecto")


def zona_del(doctor: Doctor) -> ZoneInfo:
    """El huso guardado del doctor.

    Sin él no se puede ni pintar la agenda, y ofrecer una hora en otro huso sería peor que un
    error: citas a la hora equivocada. El 500 dice exactamente qué campo está roto.
    """
    try:
        return ZoneInfo(doctor.timezone)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"El huso horario guardado ({doctor.timezone}) no es válido",
        ) from exc


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def get_current_doctor_id(agenda_sesion: str | None = Cookie(default=None)) -> uuid.UUID:
    if not agenda_sesion:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="No autenticado")
    doctor_id = decodificar_token(agenda_sesion)
    if doctor_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o expirada")
    return doctor_id


async def get_current_doctor(
    doctor_id: uuid.UUID = Depends(get_current_doctor_id),
    session: AsyncSession = Depends(get_session),
) -> Doctor:
    doctor = await session.get(Doctor, doctor_id)
    if doctor is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Cuenta no encontrada")
    return doctor
