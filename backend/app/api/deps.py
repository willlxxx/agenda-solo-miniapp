"""Dependencias de FastAPI: sesión de BD y doctor autenticado."""

import uuid
from collections.abc import AsyncIterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Doctor
from app.security import decodificar_token


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
