"""Autenticación del doctor + onboarding mágico (cero configuración inicial)."""

import uuid
from datetime import time

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_doctor, get_session
from app.models import Availability, Doctor, Service
from app.schemas import AccesoIn, CambioContrasenaIn, DoctorPatch, DoctorPublic, RegistroIn
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


async def _lo_tiene_otra_cuenta(
    session: AsyncSession, columna: ColumnElement[str], valor: str, doctor_id: uuid.UUID
) -> bool:
    """Si ese email o ese enlace ya es de otro consultorio. El propio no cuenta.

    El `Doctor.id != doctor_id` es lo que evita el 409 absurdo: `email` y `slug_unico` son CITEXT, así
    que volver a guardar tu propio valor --o escribirlo con otras mayúsculas-- es un no-op, no una
    colisión con uno mismo.
    """
    otro = await session.scalar(select(Doctor.id).where(columna == valor, Doctor.id != doctor_id))
    return otro is not None


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


@router.patch("/yo", response_model=DoctorPublic)
async def actualizar_yo(
    datos: DoctorPatch,
    doctor: Doctor = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_session),
) -> Doctor:
    """Editar la ficha del consultorio: nombre, contacto, huso y enlace público.

    El huso es el campo delicado de la pantalla: decide la franja de silencio de los avisos y la hora
    de pared a la que se lee cada cita, así que `DoctorPatch` lo valida contra la base IANA antes de
    escribir. Las citas ya guardadas mantienen su instante exacto; cambiar el huso no las mueve en el
    tiempo, solo cambia la hora con la que se pintan.

    Cambiar el enlace no redirige al viejo: quien lo tenga apuntado se queda fuera hasta que le pases
    el nuevo. Es reversible (se puede poner el anterior), pero conviene decirlo antes de pulsar.
    """
    cambios = datos.model_dump(exclude_unset=True)
    if not cambios:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No llegó ningún campo")

    if "email" in cambios and await _lo_tiene_otra_cuenta(session, Doctor.email, cambios["email"], doctor.id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Ese email ya lo usa otra cuenta")

    if "slug_unico" in cambios and await _lo_tiene_otra_cuenta(
        session, Doctor.slug_unico, cambios["slug_unico"], doctor.id
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Ese enlace ya lo usa otra consulta")

    for campo, valor in cambios.items():
        setattr(doctor, campo, valor)
    await session.commit()
    return doctor


@router.post("/contrasena", status_code=status.HTTP_204_NO_CONTENT)
async def cambiar_contrasena(
    datos: CambioContrasenaIn,
    doctor: Doctor = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Cambiar la clave con la sesión ya abierta, corroborando la anterior.

    Pedirla aunque la cookie sea válida es para el portátil desatendido: que una sesión abierta no
    bastase para tomar el control de la cuenta. El fallo es 403, no 401, porque la sesión sí es
    correcta; lo que no cuadra es la confirmación.

    Lo que este cambio NO hace: cerrar las demás sesiones. No hay lista de revocación, así que una
    cookie firmada antes del cambio sigue entrando hasta que expire (`JWT_TTL_DIAS`).
    """
    if not verify_password(doctor.password_hash, datos.actual):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="La contraseña actual no es correcta")
    doctor.password_hash = hash_password(datos.nueva)
    await session.commit()
