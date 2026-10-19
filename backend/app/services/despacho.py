"""Despacho de recordatorios: lo que ejecuta el cron externo en cada pasada.

Separa dos cosas que no deben mezclarse: **decidir** qué avisos tocan (dominio puro,
`app/domain/recordatorios.py`) y **escribir + entregar** (aquí). La decisión se prueba sin reloj
de entorno ni proveedor; esto necesita la base de datos real porque la entrega tiene que quedar
marcada junto a la cita, y una bandera que se escribe dos veces es un paciente avisado dos veces.

Las reglas de escritura:

- **Un advisory lock por doctor** (`recordatorios:`) durante su tanda. Clave distinta de la de las
  citas: el cron no reserva huecos, solo marca banderas, y no tiene por qué esperar a que alguien
  reserve. El lock sirve para que dos pasadas solapadas --o un cron duplicado-- no manden el mismo
  aviso.
- **Se revalidan las banderas dentro del lock.** Lo que leyó `plan_de_envios` pudo cambiar entre la
  lectura y la escritura; dentro del lock se vuelve a leer y lo ya marcado se omite, no se pisa.
- **El fallo no marca nada.** Si el proveedor no entrega, la bandera sigue en false y sumamos un
  intento; tras `MAX_INTENTOS` esa cita se da por perdida. Reintentar cada pasada contra un
  proveedor caído sería infinito, y marcar como enviado un correo que no salió es mentir en el
  historial.

`ahora` es UTC y viene de fuera: sin esa costura los tests no podrían afirmar sobre una pasada que
ocurre "dentro de tres días", y el huso de cada doctor se aplica solo al mirar la hora de silencio.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Row, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.recordatorios import (
    TIPO_24H,
    CitaParaRecordar,
    adelantos_posibles,
    plan_de_envios,
)
from app.models import UNIQUE_CONFIRMADA, Appointment, Doctor, Patient, Service
from app.services.correo import Correo, ErrorEnvio, Proveedor

# Pasadas sin conseguir entregar una cita: después de tres dejamos de intentarlo.
MAX_INTENTOS = 3


@dataclass
class Resumen:
    """Lo que pasó en la pasada, tal cual lo va a leer quien monte el cron."""

    proveedor: str
    candidatas: int = 0
    # Citas del barrido sin email: por aquí no se les puede escribir (el canal es correo).
    sin_correo: int = 0
    planificados: int = 0
    enviados: int = 0
    enviados_24h: int = 0
    enviados_1h: int = 0
    # El dominio quería avisar y la bandera ya estaba puesta: otra pasada ganó.
    omitidos: int = 0
    # Fallos del proveedor: la bandera queda sin marcar y suma un intento.
    errores: int = 0
    # Avisos descartados por llevar demasiados intentos a cuestas.
    agotados: int = 0
    # Citas cuyo doctor tiene un huso guardado inválido: no sabemos a qué hora escribirle.
    husos_invalidos: int = 0


@dataclass(frozen=True)
class _Fila:
    """La cita ya convertida a lo que pide el dominio, con el contador que solo mira el despacho."""

    cita: CitaParaRecordar
    intentos: int


async def _lectura(session: AsyncSession, ahora: datetime, margen: timedelta) -> Sequence[Row]:
    """Citas confirmadas cuyo adelanto cae cerca del rango de alguna ventana, en scalares.

    Se lee un poco más ancho que las ventanas (`margen`) a propósito: decide el dominio al
    milímetro, así que el borde no depende del segundo exacto en que saltó el cron.
    """
    minimo, maximo = adelantos_posibles()
    consulta = (
        select(
            Appointment.id,
            Appointment.doctor_id,
            Appointment.fecha_hora,
            Appointment.recordatorio_24h_enviado,
            Appointment.recordatorio_1h_enviado,
            Appointment.recordatorio_intentos,
            Doctor.nombre.label("doctor_nombre"),
            Doctor.telefono.label("doctor_telefono"),
            Doctor.timezone.label("timezone"),
            Patient.nombre.label("paciente_nombre"),
            Patient.email.label("email"),
            Service.nombre.label("servicio"),
        )
        .select_from(Appointment)
        .join(Doctor, Doctor.id == Appointment.doctor_id)
        .outerjoin(Patient, Patient.id == Appointment.patient_id)
        .outerjoin(Service, Service.id == Appointment.service_id)
        .where(
            Appointment.estado == UNIQUE_CONFIRMADA,
            Appointment.fecha_hora >= ahora + minimo - margen,
            Appointment.fecha_hora <= ahora + maximo + margen,
        )
        .order_by(Appointment.fecha_hora, Appointment.id)
    )
    return (await session.execute(consulta)).all()


def _cita(fila: Row, zona: ZoneInfo) -> CitaParaRecordar:
    """La fila de la BD en los scalares que espera el dominio.

    `servicio` vacío lo traduce el dominio por "tu cita": una cita sin servicio sigue siendo una
    cita que hay que recordar. Y `paciente` puede no existir (cita borrada de la ficha), en cuyo
    caso el email vacío ya descarta el envío.
    """
    return CitaParaRecordar(
        id=fila.id,
        doctor_nombre=fila.doctor_nombre,
        doctor_telefono=fila.doctor_telefono,
        zona=zona,
        inicio_utc=fila.fecha_hora,
        servicio=fila.servicio or "",
        paciente_nombre=fila.paciente_nombre or "",
        email=fila.email or "",
        enviado_24h=fila.recordatorio_24h_enviado,
        enviado_1h=fila.recordatorio_1h_enviado,
    )


def _agrupar(filas: Sequence[Row], resumen: Resumen) -> dict[uuid.UUID, list[_Fila]]:
    """Una tanda por doctor, con su huso resuelto una sola vez por grupo."""
    grupos: dict[uuid.UUID, list[_Fila]] = {}
    for fila in filas:
        try:
            zona = ZoneInfo(fila.timezone)
        except (ZoneInfoNotFoundError, KeyError, ValueError):
            # Sin huso no hay hora local correcta que ofrecer: mejor callar que escribir a las 3 am.
            resumen.husos_invalidos += 1
            continue
        grupos.setdefault(fila.doctor_id, []).append(_Fila(cita=_cita(fila, zona), intentos=fila.recordatorio_intentos))
    return grupos


async def _marcar(session: AsyncSession, cita_id: uuid.UUID, tipo: str, ahora: datetime, *, exito: bool) -> None:
    """Escribe la bandera con su fecha, o el intento fallido, dentro de la transacción en curso."""
    if exito:
        valores = (
            {"recordatorio_24h_enviado": True, "recordatorio_24h_enviado_en": ahora}
            if tipo == TIPO_24H
            else {"recordatorio_1h_enviado": True, "recordatorio_1h_enviado_en": ahora}
        )
    else:
        valores = {"recordatorio_intentos": Appointment.recordatorio_intentos + 1}
    await session.execute(update(Appointment).where(Appointment.id == cita_id).values(**valores))


async def _estado(session: AsyncSession, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, tuple[bool, bool]]:
    """Las banderas RELEVADAS dentro del lock: `(24h, 1h)` por cita."""
    filas = await session.execute(
        select(
            Appointment.id,
            Appointment.recordatorio_24h_enviado,
            Appointment.recordatorio_1h_enviado,
        ).where(Appointment.id.in_(ids))
    )
    return {fila.id: (fila.recordatorio_24h_enviado, fila.recordatorio_1h_enviado) for fila in filas}


async def _tanda(
    session: AsyncSession,
    proveedor: Proveedor,
    resumen: Resumen,
    doctor_id: uuid.UUID,
    filas: Sequence[_Fila],
    ahora: datetime,
) -> None:
    """Los avisos de un doctor: lock, revalidar, entregar y marcar en una sola transacción."""
    avisos = plan_de_envios([f.cita for f in filas], ahora)
    resumen.planificados += len(avisos)
    if not avisos:
        return

    intentos = {f.cita.id: f.intentos for f in filas}
    por_enviar = [aviso for aviso in avisos if intentos[aviso.cita_id] < MAX_INTENTOS]
    resumen.agotados += len(avisos) - len(por_enviar)
    if not por_enviar:
        return

    await session.rollback()
    async with session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:clave, 0))"),
            {"clave": f"recordatorios:{doctor_id}"},
        )
        marcadas = await _estado(session, [aviso.cita_id for aviso in por_enviar])
        for aviso in por_enviar:
            ya_24h, ya_1h = marcadas.get(aviso.cita_id, (False, False))
            if ya_24h if aviso.tipo == TIPO_24H else ya_1h:
                resumen.omitidos += 1
                continue
            try:
                await proveedor.enviar(Correo(para=aviso.para, asunto=aviso.asunto, cuerpo=aviso.cuerpo))
            except ErrorEnvio:
                resumen.errores += 1
                await _marcar(session, aviso.cita_id, aviso.tipo, ahora, exito=False)
                continue
            await _marcar(session, aviso.cita_id, aviso.tipo, ahora, exito=True)
            resumen.enviados += 1
            if aviso.tipo == TIPO_24H:
                resumen.enviados_24h += 1
            else:
                resumen.enviados_1h += 1


async def despachar(
    session: AsyncSession,
    proveedor: Proveedor,
    *,
    ahora: datetime | None = None,
    margen: timedelta = timedelta(minutes=5),
) -> Resumen:
    """Una pasada del cron: lee, decide por doctor y entrega lo que toque."""
    momento = ahora or datetime.now(UTC)
    if momento.tzinfo is None:
        raise ValueError("El despacho trabaja en UTC: `ahora` tiene que venir con huso")
    resumen = Resumen(proveedor=proveedor.nombre)

    filas = await _lectura(session, momento, margen)
    resumen.candidatas = len(filas)
    resumen.sin_correo = sum(1 for fila in filas if not (fila.email or "").strip())

    for doctor_id, grupo in _agrupar(filas, resumen).items():
        await _tanda(session, proveedor, resumen, doctor_id, grupo, momento)

    return resumen
