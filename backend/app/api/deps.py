"""Dependencias de FastAPI: sesión de BD y doctor autenticado."""

import uuid
from collections.abc import AsyncIterator

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Doctor
from app.security import decodificar_token


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
