"""Núcleo de reserva: la única forma que tiene el backend de escribir una cita.

La usan los dos sitios que reservan: el paciente desde su enlace público y el médico desde el
panel cuando avisa a alguien de la lista de espera y deja el hueco él mismo. Tienen que respetar
las mismas reglas --el hueco revalidado dentro del advisory lock, la identidad del paciente, la
duración congelada-- así que viven en un solo sitio. Dos implementaciones parecidas son justo la
forma de que dos citas ocupen el mismo hueco.

Quien llama aporta scalares ya validados (`Destino`), porque el `rollback` de aquí expira todo
objeto ORM cargado antes. Los fallos son excepciones propias: el código HTTP y el mensaje al
paciente los decide cada router.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.disponibilidad import CitaLocal, DiaDisponible, Franja, calcular_disponibilidad
from app.models import UNIQUE_CONFIRMADA, Appointment, Availability, BlockedDate, Patient

MAX_DIAS = 60
PASO_MINUTOS = 15
# Margen para que se pueda reservar "hoy a las X" sin reservar en la hora en curso. El panel
# reserva con las mismas reglas que el enlace: lo que se ofrece y lo que se escribe cuadran.
ANTICIPACION_MINUTOS = 30
# Cuántos huecos se le enseñan por día: ni el paciente ni el médico verían bien un muro de horas.
HUECOS_POR_DIA = 60


class ErrorReserva(Exception):
    """Algo impide escribir la cita; el router lo traduce a 4xx."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class DiaFueraDeHorizonte(ErrorReserva):
    """El día pedido ya pasó o queda más allá de lo que se deja reservar."""


class HuecoNoDisponible(ErrorReserva):
    """El hueco ya no está libre: alguien lo ocupó mientras se escribía."""

    def __init__(self) -> None:
        super().__init__("Ese hueco ya no está libre. Elige otra hora.")


class PacienteFueraDeSitio(ErrorReserva):
    """El id de paciente no es del doctor que reserva: nunca escribimos sobre datos ajenos."""

    def __init__(self) -> None:
        super().__init__("Ese paciente no pertenece a esta agenda")


@dataclass(frozen=True)
class Destino:
    """Doctor y servicio de la reserva, en scalares y con su huso ya resuelto."""

    doctor_id: uuid.UUID
    nombre_doctor: str
    servicio_id: uuid.UUID
    nombre_servicio: str
    duracion_minutos: int
    zona: ZoneInfo


@dataclass(frozen=True)
class PacienteNuevo:
    """Identidad tal como la escribió quien pide la cita."""

    nombre: str
    telefono: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class ResultadoReserva:
    id: uuid.UUID
    paciente_id: uuid.UUID
    fecha: date
    hora: time
    fin: time
    duracion_minutos: int
    servicio: str
    doctor: str
    inicio_utc: datetime


def ahora_local(zona: ZoneInfo) -> datetime:
    """Hora del doctor SIN zona: el motor de disponibilidad trabaja en hora local."""
    return datetime.now(zona).replace(tzinfo=None)


async def insumos(
    session: AsyncSession, doctor_id: uuid.UUID, zona: ZoneInfo, fechas: list[date]
) -> tuple[list[Franja], list[CitaLocal], set[date]]:
    """Franjas activas, citas confirmadas y días bloqueados del horizonte pedido."""
    franjas = [
        Franja(dia_semana=f.dia_semana, hora_inicio=f.hora_inicio, hora_fin=f.hora_fin)
        for f in await session.scalars(
            select(Availability).where(Availability.doctor_id == doctor_id, Availability.activo.is_(True))
        )
    ]
    # Colchón de un día a cada lado de la ventana UTC: cubre cualquier desfase horaria (±14 h).
    inicio_consulta = datetime.combine(fechas[0], time.min, tzinfo=UTC) - timedelta(days=1)
    fin_consulta = datetime.combine(fechas[-1] + timedelta(days=1), time.min, tzinfo=UTC) + timedelta(days=1)
    citas = [
        CitaLocal(inicio=c.fecha_hora.astimezone(zona).replace(tzinfo=None), duracion_minutos=c.duracion_minutos)
        for c in await session.scalars(
            select(Appointment).where(
                Appointment.doctor_id == doctor_id,
                Appointment.estado == UNIQUE_CONFIRMADA,
                Appointment.fecha_hora >= inicio_consulta,
                Appointment.fecha_hora < fin_consulta,
            )
        )
    ]
    bloqueados = set(
        await session.scalars(
            select(BlockedDate.fecha).where(
                BlockedDate.doctor_id == doctor_id,
                BlockedDate.fecha >= fechas[0],
                BlockedDate.fecha <= fechas[-1],
            )
        )
    )
    return franjas, citas, bloqueados


def huecos_de_dia(
    *,
    franjas: list[Franja],
    citas: list[CitaLocal],
    bloqueados: set[date],
    duracion_minutos: int,
    fechas: list[date],
    ahora: datetime,
    paso_minutos: int = PASO_MINUTOS,
) -> list[DiaDisponible]:
    """Disponibilidad en las mismas condiciones con las que se escribe la reserva."""
    return calcular_disponibilidad(
        franjas=franjas,
        citas=citas,
        bloqueados=bloqueados,
        duracion_minutos=duracion_minutos,
        fechas=fechas,
        ahora=ahora,
        paso_minutos=paso_minutos,
        anticipacion_minutos=ANTICIPACION_MINUTOS,
    )


def validar_dia(fecha: date, ahora: datetime) -> None:
    if fecha < ahora.date():
        raise DiaFueraDeHorizonte("Ese día ya pasó")
    if (fecha - ahora.date()).days > MAX_DIAS:
        raise DiaFueraDeHorizonte(f"Solo se reserva con {MAX_DIAS} días de antelación")


async def buscar_paciente(
    session: AsyncSession,
    doctor_id: uuid.UUID,
    nombre: str,
    telefono: str | None,
    correo: str | None,
) -> Patient | None:
    """La ficha del paciente con la identidad que traía la petición.

    La unicalidad de `patients` es (doctor, nombre, teléfono), y PG no deduplica NULL: si llega
    solo email buscamos por email, si no cada intento crearía una ficha paralela.
    """
    si_telefono = (telefono or "").strip()
    if si_telefono:
        return await session.scalar(
            select(Patient).where(
                Patient.doctor_id == doctor_id, Patient.nombre == nombre, Patient.telefono == si_telefono
            )
        )
    if correo:
        return await session.scalar(
            select(Patient).where(
                Patient.doctor_id == doctor_id,
                Patient.nombre == nombre,
                # lower() en los dos lados: una reserva antigua pudo guardar el email tal cual se
                # escribió, y si no lo igualamos así crearíamos una ficha paralela por una mayúscula.
                func.lower(Patient.email) == correo,
                Patient.telefono.is_(None),
            )
        )
    return await session.scalar(
        select(Patient).where(Patient.doctor_id == doctor_id, Patient.nombre == nombre, Patient.telefono.is_(None))
    )


async def _paciente_para_reserva(
    session: AsyncSession, destino: Destino, paciente: uuid.UUID | PacienteNuevo
) -> uuid.UUID:
    if isinstance(paciente, uuid.UUID):
        fila = await session.scalar(
            select(Patient).where(Patient.id == paciente, Patient.doctor_id == destino.doctor_id)
        )
        if fila is None:
            raise PacienteFueraDeSitio()
        return fila.id

    nombre = paciente.nombre.strip()
    telefono = (paciente.telefono or "").strip() or None
    correo = paciente.email.lower() if paciente.email else None
    fila = await buscar_paciente(session, destino.doctor_id, nombre, telefono, correo)
    if fila is not None:
        return fila.id
    nuevo = Patient(
        doctor_id=destino.doctor_id,
        nombre=nombre,
        telefono=telefono,
        email=correo,
    )
    session.add(nuevo)
    await session.flush()
    return nuevo.id


async def reservar_hueco(
    session: AsyncSession,
    *,
    destino: Destino,
    fecha: date,
    hora: time,
    paciente: uuid.UUID | PacienteNuevo,
    origen: str,
) -> ResultadoReserva:
    """Escribe la cita dentro de una transacción con `pg_advisory_xact_lock` por doctor.

    Revalida el hueco DESPUÉS de coger el lock, con el mismo motor que se lo mostró al paciente,
    para que lectura y escritura no puedan divergir. Lanza `ErrorReserva` si algo lo impide.
    """
    ahora = ahora_local(destino.zona)
    validar_dia(fecha, ahora)

    # fold=0: en la única hora repetida del cambio de horario (domingo de octubre) nos quedamos
    # con la primera de las dos, que es la que ofreció el motor.
    inicio_local = datetime.combine(fecha, hora, tzinfo=destino.zona)
    inicio_utc = inicio_local.astimezone(UTC)

    # Cerramos la tx implícita de las lecturas de arriba para abrir la nuestra con el advisory lock.
    await session.rollback()
    async with session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:clave, 0))"),
            {"clave": f"agenda-cita:{destino.doctor_id}"},
        )
        franjas, citas, bloqueados = await insumos(session, destino.doctor_id, destino.zona, [fecha])
        ofrecidos = huecos_de_dia(
            franjas=franjas,
            citas=citas,
            bloqueados=bloqueados,
            duracion_minutos=destino.duracion_minutos,
            fechas=[fecha],
            ahora=ahora,
        )
        if not ofrecidos or all(h.inicio != hora for h in ofrecidos[0].huecos):
            raise HuecoNoDisponible()

        paciente_id = await _paciente_para_reserva(session, destino, paciente)
        cita = Appointment(
            doctor_id=destino.doctor_id,
            service_id=destino.servicio_id,
            patient_id=paciente_id,
            fecha_hora=inicio_utc,
            # Congelamos la duración: si mañana el doctor cambia el servicio, esta cita no se reescribe.
            duracion_minutos=destino.duracion_minutos,
            estado=UNIQUE_CONFIRMADA,
            origen=origen,
            # El consentimiento se prueba en el momento de la reserva, no en la hora de la cita.
            consentimiento_ok_en=datetime.now(UTC),
        )
        session.add(cita)
        await session.flush()
        cita_id = cita.id

    fin = inicio_local + timedelta(minutes=destino.duracion_minutos)
    return ResultadoReserva(
        id=cita_id,
        paciente_id=paciente_id,
        fecha=fecha,
        hora=hora,
        fin=fin.time(),
        duracion_minutos=destino.duracion_minutos,
        servicio=destino.nombre_servicio,
        doctor=destino.nombre_doctor,
        inicio_utc=inicio_utc,
    )
