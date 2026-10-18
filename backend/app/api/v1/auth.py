"""Autenticación del doctor + onboarding mágico (cero configuración inicial)."""

from datetime import time

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_doctor, get_session
from app.models import Availability, Doctor, Service
from app.schemas import AccesoIn, DoctorPublic, RegistroIn
from app.security import (
    clear_session_cookie,
    crear_token,
    hash_password,
    set_session_cookie,
    verify_password,
)
from app.utils import normalizar_slug, slug_disponible

router = APIRouter(prefix="/auth", tags=["auth"])

# Servicios predefinidos del documento (Fase 2: "cero configuración inicial").
SERVICIOS_INICIALES = [
    ("Revisión", 30, "#2563eb"),
    ("Limpieza", 30, "#0891b2"),
    ("Empaste", 45, "#7c3aed"),
    ("Urgencia", 30, "#dc2626"),
    ("Endodoncia inicial", 60, "#ea580c"),
]

# Horario por defecto: lunes a viernes, mañana y tarde.
HORARIO_INICIAL = [
    (dia, inicio, fin) for dia in range(5) for inicio, fin in ((time(9, 0), time(14, 0)), (time(16, 0), time(20, 0)))
]


async def _slug_o_email_en_uso(session: AsyncSession, email: str) -> bool:
    return (await session.scalar(select(Doctor.id).where(Doctor.email == email))) is not None


@router.post("/registro", response_model=DoctorPublic, status_code=status.HTTP_201_CREATED)
async def registro(datos: RegistroIn, response: Response, session: AsyncSession = Depends(get_session)) -> Doctor:
    if await _slug_o_email_en_uso(session, datos.email):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Ese email ya está registrado")

    async def slug_ocupado(candidate: str) -> bool:
        return (await session.scalar(select(Doctor.id).where(Doctor.slug_unico == candidate))) is not None

    doctor = Doctor(
        nombre=datos.nombre,
        email=datos.email,
        password_hash=hash_password(datos.password),
        telefono=datos.telefono,
        timezone=datos.timezone,
        slug_unico=await slug_disponible(normalizar_slug(datos.nombre), slug_ocupado),
    )
    session.add(doctor)
    await session.flush()  # obtiene doctor.id

    # Onboarding mágico: el panel ya funciona sin que el doctor configure nada.
    for orden, (nombre, duracion, color) in enumerate(SERVICIOS_INICIALES):
        session.add(
            Service(
                doctor_id=doctor.id,
                nombre=nombre,
                duracion_minutos=duracion,
                color=color,
                orden=orden,
            )
        )
    for dia, inicio, fin in HORARIO_INICIAL:
        session.add(Availability(doctor_id=doctor.id, dia_semana=dia, hora_inicio=inicio, hora_fin=fin))

    await session.commit()
    set_session_cookie(response, crear_token(doctor.id))
    return doctor


@router.post("/acceso", response_model=DoctorPublic)
async def acceso(datos: AccesoIn, response: Response, session: AsyncSession = Depends(get_session)) -> Doctor:
    doctor = await session.scalar(select(Doctor).where(Doctor.email == datos.email))
    # Credenciales inválidas o email inexistente devuelven el mismo mensaje:
    # no revelamos qué emails están registrados.
    if doctor is None or not verify_password(doctor.password_hash, datos.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Email o contraseña incorrectos")
    set_session_cookie(response, crear_token(doctor.id))
    return doctor


@router.post("/salida", status_code=status.HTTP_204_NO_CONTENT)
async def salida(response: Response) -> None:
    clear_session_cookie(response)


@router.get("/yo", response_model=DoctorPublic)
async def yo(doctor: Doctor = Depends(get_current_doctor)) -> Doctor:
    return doctor
