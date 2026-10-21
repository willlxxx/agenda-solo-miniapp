"""Autenticación del doctor + onboarding mágico (cero configuración inicial)."""

import uuid
from datetime import datetime, time

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_ahora,
    get_canal_correo,
    get_current_doctor,
    get_proveedor_correo,
    get_session,
)
from app.config import get_settings
from app.domain.recuperacion import disponible_recuperacion
from app.models import Availability, Doctor, Service
from app.schemas import (
    AccesoIn,
    CambioContrasenaIn,
    DisponibilidadRecuperacion,
    DoctorPatch,
    DoctorPublic,
    RecuperarConfirmacionIn,
    RecuperarSolicitudIn,
    RegistroIn,
    SolicitudRecuperacionOut,
)
from app.security import (
    clear_session_cookie,
    crear_token,
    hash_password,
    set_session_cookie,
    verify_password,
)
from app.services import recuperacion
from app.services.correo import Canal, Proveedor
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


# Lo que se lee en la pantalla cuando no hay canal: dicho sin rodeos y con la salida que sí existe.
SIN_CANAL_DE_CORREO = (
    "Este despliegue todavía no puede mandar correos, así que no hay forma honesta de devolverte el "
    "acceso por email. Si tienes la sesión abierta en otro aparato, puedes cambiar la contraseña desde "
    "el panel; si no, hace falta configurar el correo del consultorio."
)


def _estado_recuperacion(canal: Canal) -> tuple[bool, str | None]:
    """Si este despliegue puede ofrecer recuperar el acceso, y por qué no si no puede.

    Es una función y no dos trozos de `if` repetidos: el mismo criterio lo lee la pantalla para pintar
    el botón y lo aplica el endpoint para negarse. Si se separan, la interfaz promete algo que la API
    rechaza.
    """
    return disponible_recuperacion(canal_listo=canal.listo, es_desarrollo=get_settings().es_desarrollo)


@router.get("/recuperar/disponible", response_model=DisponibilidadRecuperacion)
async def recuperar_disponible(canal: Canal = Depends(get_canal_correo)) -> DisponibilidadRecuperacion:
    """La pregunta que hace la pantalla de acceso antes de ofrecer nada.

    Devuelve 200 en los dos casos: no es un error que el canal no esté, es una circunstancia del
    despliegue que la interfaz tiene que poder explicar. Es pública porque la hace quien no puede
    entrar, que es justamente para quien existe esta ruta.
    """
    disponible, razon = _estado_recuperacion(canal)
    return DisponibilidadRecuperacion(disponible=disponible, razon=razon)


@router.post("/recuperar/solicitar", response_model=SolicitudRecuperacionOut, status_code=status.HTTP_202_ACCEPTED)
async def recuperar_solicitar(
    datos: RecuperarSolicitudIn,
    session: AsyncSession = Depends(get_session),
    canal: Canal = Depends(get_canal_correo),
    proveedor: Proveedor = Depends(get_proveedor_correo),
    ahora: datetime = Depends(get_ahora),
) -> SolicitudRecuperacionOut:
    """Pedir el enlace. La respuesta es siempre la misma y siempre en 202.

    No hay 404 para un email que no es de ninguna consulta, ni un "te lo hemos mandado" que solo suena
    cuando existe: contar la diferencia sería publicar qué correos tienen agenda montada. Lo que sí hace
    el 202 es compromiso de intento, nunca de entrega, y así está escrito en la pantalla.

    El 503 de "no hay canal" no filtra cuentas: depende solo de la configuración del despliegue, que es
    la misma para todos los emails del mundo.
    """
    disponible, _ = _estado_recuperacion(canal)
    if not disponible:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=SIN_CANAL_DE_CORREO)
    await recuperacion.solicitar(
        session, proveedor, origen=canal.origen_enlace, email=datos.email, ahora=ahora
    )
    return SolicitudRecuperacionOut()


@router.post("/recuperar/confirmar", status_code=status.HTTP_204_NO_CONTENT)
async def recuperar_confirmar(
    datos: RecuperarConfirmacionIn,
    session: AsyncSession = Depends(get_session),
    ahora: datetime = Depends(get_ahora),
) -> None:
    """Consumir el enlace y escribir la clave nueva. No abre sesión: deja que el médico entre él.

    Abrir la sesión desde aquí sería peor de dos maneras: el navegador que consume el enlace puede ser
    el del teléfono donde alguien leyó el correo, y una contraseña que se acaba de recuperar no debería
    repartir cookies sin que su dueño pida entrar.

    El fallo es un solo mensaje para los tres casos (no existe, caducó, ya se usó) por lo mismo que el
    pedido no distingue emails: aquí tampoco se le cuenta a nadie cómo va su intento.
    """
    if not await recuperacion.confirmar(session, token=datos.token, nueva=datos.nueva, ahora=ahora):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Ese enlace no sirve: caducó, ya se usó o no es de esta agenda. Pide uno nuevo.",
        )
