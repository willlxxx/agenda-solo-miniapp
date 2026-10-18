"""Bloqueo de días (vacaciones y festivos)."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_doctor_id, get_session
from app.models import BlockedDate
from app.schemas import BlockedDateIn, BlockedDatePublic

router = APIRouter(prefix="/blocked-dates", tags=["blocked-dates"])


@router.get("", response_model=list[BlockedDatePublic])
async def listar(
    doctor_id: uuid.UUID = Depends(get_current_doctor_id),
    session: AsyncSession = Depends(get_session),
):
    filas = await session.scalars(
        select(BlockedDate).where(BlockedDate.doctor_id == doctor_id).order_by(BlockedDate.fecha)
    )
    return list(filas)


@router.post("", response_model=BlockedDatePublic, status_code=status.HTTP_201_CREATED)
async def crear(
    datos: BlockedDateIn,
    doctor_id: uuid.UUID = Depends(get_current_doctor_id),
    session: AsyncSession = Depends(get_session),
):
    if datos.fecha < date.today():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="No se puede bloquear el pasado")
    duplicado = await session.scalar(
        select(BlockedDate.id).where(BlockedDate.doctor_id == doctor_id, BlockedDate.fecha == datos.fecha)
    )
    if duplicado:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Ese día ya está bloqueado")
    registro = BlockedDate(doctor_id=doctor_id, **datos.model_dump())
    session.add(registro)
    await session.commit()
    await session.refresh(registro)
    return registro


@router.delete("/{blocked_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar(
    blocked_id: uuid.UUID,
    doctor_id: uuid.UUID = Depends(get_current_doctor_id),
    session: AsyncSession = Depends(get_session),
) -> None:
    registro = await session.get(BlockedDate, blocked_id)
    if registro is None or registro.doctor_id != doctor_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Día bloqueado no encontrado")
    await session.delete(registro)
    await session.commit()
