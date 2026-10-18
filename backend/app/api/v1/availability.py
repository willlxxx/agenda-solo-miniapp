"""CRUD de disponibilidad semanal, con validación de solapamientos."""

import uuid
from datetime import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_doctor_id, get_session
from app.models import Availability
from app.schemas import AvailabilityIn, AvailabilityPatch, AvailabilityPublic

router = APIRouter(prefix="/availability", tags=["availability"])


def _validar_rango(hora_inicio: time, hora_fin: time) -> None:
    if hora_fin <= hora_inicio:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="La hora de fin debe ser posterior a la de inicio",
        )


async def _rechazar_inicio_repetido(
    session: AsyncSession,
    doctor_id: uuid.UUID,
    dia_semana: int,
    hora_inicio: time,
    excluir_id: uuid.UUID | None = None,
) -> None:
    """(doctor, día, hora de inicio) es único en BD; lo detectamos antes de insertar.

    Incluye las franjas inactivas: una franja desactivada sigue ocupando su hora de
    inicio, y dejar pasar el INSERT terminaría en un error 500 por unique violation.
    """
    duplicado = await session.scalar(
        select(Availability.id).where(
            Availability.doctor_id == doctor_id,
            Availability.dia_semana == dia_semana,
            Availability.hora_inicio == hora_inicio,
        )
    )
    if duplicado is not None and duplicado != excluir_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Ya existe una franja que empieza a las {hora_inicio} ese día",
        )


def _se_solapan(a_inicio: time, a_fin: time, b_inicio: time, b_fin: time) -> bool:
    """Dos franjas se solapan si cada una empieza antes de que la otra termine."""
    return a_inicio < b_fin and b_inicio < a_fin


async def _rechazar_solapamiento(
    session: AsyncSession,
    doctor_id: uuid.UUID,
    dia_semana: int,
    hora_inicio: time,
    hora_fin: time,
    excluir_id: uuid.UUID | None = None,
) -> None:
    """Solo cuentan las franjas activas: una inactiva no bloquea el hueco."""
    filas = await session.scalars(
        select(Availability).where(
            Availability.doctor_id == doctor_id,
            Availability.dia_semana == dia_semana,
            Availability.activo.is_(True),
        )
    )
    for franja in filas:
        if excluir_id is not None and franja.id == excluir_id:
            continue
        if _se_solapan(hora_inicio, hora_fin, franja.hora_inicio, franja.hora_fin):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Se solapa con la franja existente de {franja.hora_inicio} a {franja.hora_fin}",
            )


async def _obtener(session: AsyncSession, franja_id: uuid.UUID, doctor_id: uuid.UUID) -> Availability:
    franja = await session.get(Availability, franja_id)
    if franja is None or franja.doctor_id != doctor_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Franja no encontrada")
    return franja


@router.get("", response_model=list[AvailabilityPublic])
async def listar(
    doctor_id: uuid.UUID = Depends(get_current_doctor_id),
    session: AsyncSession = Depends(get_session),
):
    filas = await session.scalars(
        select(Availability)
        .where(Availability.doctor_id == doctor_id)
        .order_by(Availability.dia_semana, Availability.hora_inicio)
    )
    return list(filas)


@router.post("", response_model=AvailabilityPublic, status_code=status.HTTP_201_CREATED)
async def crear(
    datos: AvailabilityIn,
    doctor_id: uuid.UUID = Depends(get_current_doctor_id),
    session: AsyncSession = Depends(get_session),
):
    _validar_rango(datos.hora_inicio, datos.hora_fin)
    await _rechazar_inicio_repetido(session, doctor_id, datos.dia_semana, datos.hora_inicio)
    if datos.activo:
        await _rechazar_solapamiento(session, doctor_id, datos.dia_semana, datos.hora_inicio, datos.hora_fin)
    franja = Availability(doctor_id=doctor_id, **datos.model_dump())
    session.add(franja)
    await session.commit()
    await session.refresh(franja)
    return franja


@router.patch("/{franja_id}", response_model=AvailabilityPublic)
async def actualizar(
    franja_id: uuid.UUID,
    datos: AvailabilityPatch,
    doctor_id: uuid.UUID = Depends(get_current_doctor_id),
    session: AsyncSession = Depends(get_session),
):
    franja = await _obtener(session, franja_id, doctor_id)
    cambios = datos.model_dump(exclude_unset=True)
    if not cambios:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No llegó ningún campo")

    resultante = {
        "dia_semana": franja.dia_semana,
        "hora_inicio": franja.hora_inicio,
        "hora_fin": franja.hora_fin,
        "activo": franja.activo,
        **cambios,
    }
    _validar_rango(resultante["hora_inicio"], resultante["hora_fin"])
    if "hora_inicio" in cambios or "dia_semana" in cambios:
        await _rechazar_inicio_repetido(
            session,
            doctor_id,
            resultante["dia_semana"],
            resultante["hora_inicio"],
            excluir_id=franja.id,
        )
    if resultante["activo"]:
        await _rechazar_solapamiento(
            session,
            doctor_id,
            resultante["dia_semana"],
            resultante["hora_inicio"],
            resultante["hora_fin"],
            excluir_id=franja.id,
        )
    for campo, valor in cambios.items():
        setattr(franja, campo, valor)
    await session.commit()
    await session.refresh(franja)
    return franja


@router.delete("/{franja_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar(
    franja_id: uuid.UUID,
    doctor_id: uuid.UUID = Depends(get_current_doctor_id),
    session: AsyncSession = Depends(get_session),
) -> None:
    franja = await _obtener(session, franja_id, doctor_id)
    await session.delete(franja)
    await session.commit()
