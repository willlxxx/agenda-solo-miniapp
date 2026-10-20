"""La ficha del paciente: leerla, exportarla y suprimirla.

Dos escrituras y una sola delicadeza. Al suprimir no borramos las citas: soltamos la identidad
(`patient_id` a NULL) y conservamos la fila, porque esa fila es lo que hace que el hueco siga
ocupado y lo que guarda la prueba del consentimiento. Todo va en una transacción con el mismo
`pg_advisory_xact_lock('agenda-cita:{doctor}')` que usan las reservas, para no soltar una
identidad mientras alguien está escribiendo una cita encima de esa misma ficha.

El export es la otra cara del mismo derecho (art. 15 y 20): antes de borrar, el médico puede
entregarle al paciente un JSON con exactamente lo que hay guardado sobre él. Nada de diagnósticos
ni notas: aquí no se guarda eso, y el export lo dice explícitamente.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.borrado import confirmacion
from app.models import (
    ORIGEN_PUBLICO,
    UNIQUE_CONFIRMADA,
    UNIQUE_EN_ESPERA,
    Appointment,
    Doctor,
    Patient,
    Service,
    Waitlist,
)

# Una ficha con miles de citas no es un dentista unipersonal: es un error o un ataque. El panel y
# el export se cortan antes de crecer sin control; el histórico que pinta la agenda ya tiene su
# propio límite de retención (MAX_DIAS_DE_HISTORICO).
MAX_FILA_DE_CITAS = 500
MAX_FILA_DE_PETICIONES = 200


class ErrorFicha(Exception):
    """No se puede actuar sobre la ficha; el router lo traduce a 4xx."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class FichaInexistente(ErrorFicha):
    """La ficha no existe o es de otro doctor: 404 sin decir cuál de los dos, para no enumerar ids."""

    def __init__(self) -> None:
        super().__init__("Paciente no encontrado")


@dataclass(frozen=True)
class CitaDeFicha:
    """Una cita como la ve el médico: su hora local y quién la escribió."""

    id: uuid.UUID
    inicio_utc: datetime
    fecha: date
    hora: time
    fin: time
    duracion_minutos: int
    estado: str
    origen: str
    servicio: str | None
    consentimiento_en: datetime
    recordatorio_24h_en: datetime | None
    recordatorio_1h_en: datetime | None
    recordatorio_intentos: int


@dataclass(frozen=True)
class PeticionDeFicha:
    id: uuid.UUID
    fecha: date | None
    preferencia: str | None
    estado: str
    servicio: str | None
    pedido_utc: datetime
    avisado_utc: datetime | None


@dataclass
class Ficha:
    id: uuid.UUID
    nombre: str
    telefono: str | None
    email: str | None
    ultima_visita: date | None
    creado_en: datetime
    citas: list[CitaDeFicha] = field(default_factory=list)
    peticiones: list[PeticionDeFicha] = field(default_factory=list)

    @property
    def por_llegar(self) -> int:
        """Citas confirmadas cuyo instante aún no ha pasado: las que quedarían sin nombre 'vivas'."""
        ahora = datetime.now(UTC)
        return sum(1 for c in self.citas if c.estado == UNIQUE_CONFIRMADA and c.inicio_utc > ahora)


@dataclass(frozen=True)
class ResultadoSupresion:
    paciente: str
    citas_sin_identidad: int
    citas_por_llegar: int
    peticiones_sin_identidad: int
    aviso: str


def _hora_local(inicio: datetime, zona: ZoneInfo, duracion: int) -> tuple[date, time, time]:
    """UTC -> día y horas del consultorio, con la duración congelada de la cita."""
    local = inicio.astimezone(zona)
    fin = local + timedelta(minutes=duracion)
    # Los segundos no existen en la rejilla: se pintan truncados, igual que en la agenda.
    return local.date(), local.time().replace(second=0, microsecond=0), fin.time().replace(second=0, microsecond=0)


async def _filla(session: AsyncSession, doctor_id: uuid.UUID, paciente_id: uuid.UUID) -> Patient:
    """La ficha, solo si es del médico que pregunta.

    El filtro de `doctor_id` va en la propia consulta: un id de otra cuenta devuelve 404 y no hay
    ninguna rama posterior que pueda olvidarse de comprobarlo.
    """
    fila = await session.scalar(select(Patient).where(Patient.id == paciente_id, Patient.doctor_id == doctor_id))
    if fila is None:
        raise FichaInexistente()
    return fila


async def _citas(session: AsyncSession, paciente_id: uuid.UUID, zona: ZoneInfo) -> list[CitaDeFicha]:
    filas = (
        await session.execute(
            select(Appointment, Service.nombre)
            .outerjoin(Service, Service.id == Appointment.service_id)
            .where(Appointment.patient_id == paciente_id)
            .order_by(Appointment.fecha_hora.desc())
            .limit(MAX_FILA_DE_CITAS)
        )
    ).all()
    salida: list[CitaDeFicha] = []
    for cita, nombre_servicio in filas:
        fecha, hora, fin = _hora_local(cita.fecha_hora, zona, cita.duracion_minutos)
        salida.append(
            CitaDeFicha(
                id=cita.id,
                inicio_utc=cita.fecha_hora,
                fecha=fecha,
                hora=hora,
                fin=fin,
                duracion_minutos=cita.duracion_minutos,
                estado=cita.estado,
                origen=cita.origen,
                servicio=nombre_servicio,
                consentimiento_en=cita.consentimiento_ok_en,
                recordatorio_24h_en=cita.recordatorio_24h_enviado_en,
                recordatorio_1h_en=cita.recordatorio_1h_enviado_en,
                recordatorio_intentos=cita.recordatorio_intentos,
            )
        )
    return salida


async def _peticiones(session: AsyncSession, paciente_id: uuid.UUID) -> list[PeticionDeFicha]:
    filas = (
        await session.execute(
            select(Waitlist, Service.nombre)
            .outerjoin(Service, Service.id == Waitlist.service_id)
            .where(Waitlist.patient_id == paciente_id)
            .order_by(Waitlist.creado_en.desc())
            .limit(MAX_FILA_DE_PETICIONES)
        )
    ).all()
    return [
        PeticionDeFicha(
            id=entrada.id,
            fecha=entrada.fecha,
            preferencia=entrada.preferencia_horaria,
            estado=entrada.estado,
            servicio=nombre,
            pedido_utc=entrada.creado_en,
            avisado_utc=entrada.avisado_en,
        )
        for entrada, nombre in filas
    ]


async def leer(session: AsyncSession, doctor_id: uuid.UUID, paciente_id: uuid.UUID, zona: ZoneInfo) -> Ficha:
    """La ficha entera, con sus citas y sus peticiones, pintada en el huso del médico."""
    fila = await _filla(session, doctor_id, paciente_id)
    return Ficha(
        id=fila.id,
        nombre=fila.nombre,
        telefono=fila.telefono,
        email=fila.email,
        ultima_visita=fila.ultima_visita,
        creado_en=fila.creado_en,
        # Las lecturas van dentro de la misma sesión: dos consultas sueltas podrían pintarse con
        # una cita de por medio y el médico vería una ficha cruzada.
        citas=await _citas(session, fila.id, zona),
        peticiones=await _peticiones(session, fila.id),
    )


async def desde_cita(session: AsyncSession, doctor_id: uuid.UUID, cita_id: uuid.UUID, zona: ZoneInfo) -> Ficha:
    """La ficha de una cita de la agenda.

    La lista de citas no lleva el id del paciente a propósito —la agenda pinta nombre y teléfono y
    nada más—, así que la pregunta natural del médico («¿quién es este de las 9?») se resuelve aquí,
    comprobando además que la cita es suya.
    """
    paciente_id = await session.scalar(
        select(Appointment.patient_id).where(Appointment.id == cita_id, Appointment.doctor_id == doctor_id)
    )
    if paciente_id is None:
        # O la cita no existe, o no es de este médico, o ya no tiene identidad: mismo 404 callado.
        raise FichaInexistente()
    return await leer(session, doctor_id, paciente_id, zona)


def export(ficha: Ficha, doctor: Doctor, zona: str) -> dict:
    """El JSON de descarga: lo que hay guardado sobre esa persona y qué pasaría al suprimirla.

    Escrito a mano en vez de volcar los modelos, por dos razones: el archivo se entiende sin saber
    qué es `consentimiento_ok_en`, y una columna nueva no se cuela en la descarga de un paciente sin
    que nadie lo haya decidido.
    """

    def cita(c: CitaDeFicha) -> dict:
        return {
            "fecha": c.fecha.isoformat(),
            "hora": c.hora.isoformat(),
            "duracion_minutos": c.duracion_minutos,
            "servicio": c.servicio,
            "estado": c.estado,
            # 'publico' = la clicó el paciente desde su enlace; 'panel' = la escribió el médico.
            "como_entró": "el paciente desde su enlace" if c.origen == ORIGEN_PUBLICO else "el médico desde el panel",
            "consentimiento_guardado_el": c.consentimiento_en.isoformat(),
            "recordatorio_24h_enviado_el": c.recordatorio_24h_en.isoformat() if c.recordatorio_24h_en else None,
            "recordatorio_1h_enviado_el": c.recordatorio_1h_en.isoformat() if c.recordatorio_1h_en else None,
            "intentos_de_aviso_fallidos": c.recordatorio_intentos,
        }

    def peticion(p: PeticionDeFicha) -> dict:
        return {
            "pidio_el_dia": p.fecha.isoformat() if p.fecha else "le daba igual el día",
            "preferencia": p.preferencia,
            "servicio": p.servicio,
            "estado": "sigue en la lista" if p.estado == UNIQUE_EN_ESPERA else "ya fue avisado",
            "pedido_el": p.pedido_utc.isoformat(),
            "avisado_el": p.avisado_utc.isoformat() if p.avisado_utc else None,
        }

    return {
        "generado_el": datetime.now(UTC).isoformat(),
        "de_quien_son_los_datos": ficha.nombre,
        "consultorio": {"nombre": doctor.nombre, "enlace": doctor.slug_unico, "timezone": zona},
        "ficha": {
            "nombre": ficha.nombre,
            "telefono": ficha.telefono,
            "email": ficha.email,
            "ultima_visita": ficha.ultima_visita.isoformat() if ficha.ultima_visita else None,
            "alta_en": ficha.creado_en.isoformat(),
        },
        "citas": [cita(c) for c in ficha.citas],
        "lista_de_espera": [peticion(p) for p in ficha.peticiones],
        "no_se_guarda": "Diagnósticos, notas clínicas, radiografías ni ningún dato de salud.",
        "al_suprimir": confirmacion(ficha.nombre, len(ficha.citas), ficha.por_llegar, len(ficha.peticiones)),
    }


async def suprimir(session: AsyncSession, doctor_id: uuid.UUID, paciente_id: uuid.UUID) -> ResultadoSupresion:
    """Suelta la identidad de citas y peticiones y borra la ficha, todo en la misma transacción.

    El orden importa: primero `UPDATE`, después `DELETE`. Las FK hacia `patients` no tienen
    `ondelete`, así que en el orden contrario PostgreSQL cortaría la operación con un `IntegrityError`
    en cuanto la ficha tuviera una sola cita — que es exactamente el caso que el médico necesita
    resolver cuando alguien pide que le borremos los datos.
    """
    await session.rollback()
    async with session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:clave, 0))"),
            {"clave": f"agenda-cita:{doctor_id}"},
        )
        nombre = await session.scalar(
            select(Patient.nombre).where(Patient.id == paciente_id, Patient.doctor_id == doctor_id)
        )
        if nombre is None:
            raise FichaInexistente()

        # Las cuentas se hacen dentro del candado, sobre la foto que vamos a escribir: el aviso que
        # lee el médico tiene que ser lo que pasó, no lo que parecía antes de pulsar.
        por_llegar = int(
            await session.scalar(
                select(func.count())
                .select_from(Appointment)
                .where(
                    Appointment.patient_id == paciente_id,
                    Appointment.estado == UNIQUE_CONFIRMADA,
                    Appointment.fecha_hora >= func.now(),
                )
            )
            or 0
        )
        sueltas = await session.execute(
            update(Appointment)
            .where(Appointment.patient_id == paciente_id)
            .values(patient_id=None)
            .execution_options(synchronize_session=False)
        )
        pedidas = await session.execute(
            update(Waitlist)
            .where(Waitlist.patient_id == paciente_id)
            .values(patient_id=None)
            .execution_options(synchronize_session=False)
        )
        baja = await session.execute(delete(Patient).where(Patient.id == paciente_id))
        if int(baja.rowcount or 0) != 1:
            # Con el candado y el filtro de médico no debería pasar nunca; si pasa, el médico tiene
            # que saber que no se borró nada, no que se borró a medias.
            raise ErrorFicha("No se pudo borrar la ficha. Vuelve a intentarlo o revisa la sesión.")

        citas = int(sueltas.rowcount or 0)
        peticiones = int(pedidas.rowcount or 0)

    return ResultadoSupresion(
        paciente=nombre,
        citas_sin_identidad=citas,
        citas_por_llegar=min(por_llegar, citas),
        peticiones_sin_identidad=peticiones,
        aviso=confirmacion(nombre, citas, min(por_llegar, citas), peticiones),
    )
