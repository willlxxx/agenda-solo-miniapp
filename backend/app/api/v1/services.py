"""CRUD de servicios del doctor. Todo scope-ado por doctor_id."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_doctor_id, get_session
from app.models import Service
from app.schemas import ServiceIn, ServicePatch, ServicePublic

router = APIRouter(prefix="/services", tags=["services"])


async def _obtener(session: AsyncSession, service_id: uuid.UUID, doctor_id: uuid.UUID) -> Service:
    service = await session.get(Service, service_id)
    if service is None or service.doctor_id != doctor_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Servicio no encontrado")
    return service


@router.get("", response_model=list[ServicePublic])
async def listar(
    doctor_id: uuid.UUID = Depends(get_current_doctor_id),
    session: AsyncSession = Depends(get_session),
):
    filas = await session.scalars(
        select(Service).where(Service.doctor_id == doctor_id).order_by(Service.orden, Service.nombre)
    )
    return list(filas)


@router.post("", response_model=ServicePublic, status_code=status.HTTP_201_CREATED)
async def crear(
    datos: ServiceIn,
    doctor_id: uuid.UUID = Depends(get_current_doctor_id),
    session: AsyncSession = Depends(get_session),
):
    service = Service(doctor_id=doctor_id, **datos.model_dump())
    session.add(service)
    await session.commit()
    await session.refresh(service)
    return service


@router.patch("/{service_id}", response_model=ServicePublic)
async def actualizar(
    service_id: uuid.UUID,
    datos: ServicePatch,
    doctor_id: uuid.UUID = Depends(get_current_doctor_id),
    session: AsyncSession = Depends(get_session),
):
    service = await _obtener(session, service_id, doctor_id)
    cambios = datos.model_dump(exclude_unset=True)
    if not cambios:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No llegó ningún campo")
    for campo, valor in cambios.items():
        setattr(service, campo, valor)
    await session.commit()
    await session.refresh(service)
    return service


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def desactivar(
    service_id: uuid.UUID,
    doctor_id: uuid.UUID = Depends(get_current_doctor_id),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Borrado suave: un servicio con citas futuras no puede desaparecer del histórico."""
    service = await _obtener(session, service_id, doctor_id)
    service.activo = False
    await session.commit()
