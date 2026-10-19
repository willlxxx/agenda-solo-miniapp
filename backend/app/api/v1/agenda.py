"""Agenda del doctor: las citas que entraron por su enlace público, con su estado.

Todo se lee y se pinta en el huso del doctor, pero en la base el instante sigue en UTC.
La única escritura es el estado: confirmada -> completada / no_vino / cancelada, y volver a
confirmada solo si el hueco sigue libre (una cancelación puede haber sido sustituida mientras
tanto por otra reserva del mismo día).
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_doctor, get_session
from app.models import UNIQUE_CONFIRMADA, Appointment, Doctor, Patient, Service
from app.schemas import AgendaVentana, CitaAgenda, EstadoIn, PacienteAgenda

router = APIRouter(prefix="/appointments", tags=["appointments"])

# Un trimestre hacia atrás como mucho: la retención GDPR manda borrar antes de cumplir el año.
MAX_DIAS_DE_HISTORICO = 92
MAX_DIAS_POR_VENTANA = 30
COLOR_AUSENTE = "#94a3b8"


def _zona(doctor: Doctor) -> ZoneInfo:
    try:
        return ZoneInfo(doctor.timezone)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"El huso horario guardado ({doctor.timezone}) no es válido",
        ) from exc


def _pintar(
    cita: Appointment,
    zona: ZoneInfo,
    nombre_servicio: str | None,
    color_servicio: str | None,
    paciente: Patient | None,
) -> CitaAgenda:
    """UTC -> hora local del doctor. La duración congelada manda sobre la del servicio."""
    local = cita.fecha_hora.astimezone(zona)
    fin = local + timedelta(minutes=cita.duracion_minutos)
    return CitaAgenda(
        id=cita.id,
        inicio_utc=cita.fecha_hora,
        fecha=local.date(),
        hora=local.time().replace(second=0, microsecond=0),
        fin=fin.time().replace(second=0, microsecond=0),
        duracion_minutos=cita.duracion_minutos,
        estado=cita.estado,
        servicio=nombre_servicio,
        color=color_servicio or COLOR_AUSENTE,
        paciente=(
            None
            if paciente is None
            else PacienteAgenda(nombre=paciente.nombre, telefono=paciente.telefono, email=paciente.email)
        ),
    )


async def _ventana(session: AsyncSession, doctor_id: uuid.UUID, zona: ZoneInfo, desde: date, dias: int):
    """Citas del rango, con su servicio y su paciente, ordenadas por instante real."""
    # Los bordes se construyen como instantes conscientes: no hace falta colchón de un día.
    inicio = datetime.combine(desde, time.min, tzinfo=zona).astimezone(UTC)
    fin = datetime.combine(desde + timedelta(days=dias), time.min, tzinfo=zona).astimezone(UTC)
    filas = (
        await session.execute(
            select(Appointment, Service.nombre, Service.color, Patient)
            .outerjoin(Service, Service.id == Appointment.service_id)
            .outerjoin(Patient, Patient.id == Appointment.patient_id)
            .where(Appointment.doctor_id == doctor_id, Appointment.fecha_hora >= inicio, Appointment.fecha_hora < fin)
            .order_by(Appointment.fecha_hora)
        )
    ).all()
    return [_pintar(cita, zona, nombre, color, paciente) for cita, nombre, color, paciente in filas]


@router.get("", response_model=AgendaVentana)
async def agenda(
    desde: date | None = Query(default=None, description="Primer día, en el calendario del doctor"),
    dias: int = Query(default=7, ge=1, le=MAX_DIAS_POR_VENTANA),
    doctor: Doctor = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_session),
):
    zona = _zona(doctor)
    hoy = datetime.now(zona).date()
    if desde is None:
        desde = hoy
    if desde > hoy + timedelta(days=365):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Fecha demasiado lejana")
    if desde < hoy - timedelta(days=MAX_DIAS_DE_HISTORICO):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Fuera del histórico conservado")

    citas = await _ventana(session, doctor.id, zona, desde, dias)
    return AgendaVentana(
        timezone=doctor.timezone,
        desde=desde,
        hasta=desde + timedelta(days=dias - 1),
        total=len(citas),
        confirmadas=sum(1 for c in citas if c.estado == UNIQUE_CONFIRMADA),
        citas=citas,
    )


@router.patch("/{cita_id}", response_model=CitaAgenda)
async def cambiar_estado(
    cita_id: uuid.UUID,
    datos: EstadoIn,
    doctor: Doctor = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_session),
):
    zona = _zona(doctor)
    cita = await session.get(Appointment, cita_id)
    if cita is None or cita.doctor_id != doctor.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cita no encontrada")

    if cita.estado != datos.estado and datos.estado == UNIQUE_CONFIRMADA:
        await _asegurar_hueco_libre(session, doctor.id, cita)

    cita.estado = datos.estado

    # Última visita: solo se apunta cuando el instante de la cita ya pasó de verdad. Compara el
    # instante, no el día: el doctor puede marcar "Completada" una cita de hoy que aún no ha
    # llegado (se adelanta, o la reubica) y eso no debe inventar una visita futura.
    if datos.estado == "completada" and cita.patient_id is not None:
        paciente = await session.get(Patient, cita.patient_id)
        if paciente is not None:
            if cita.fecha_hora <= datetime.now(UTC):
                paciente.ultima_visita = cita.fecha_hora.astimezone(zona).date()

    await session.commit()
    await session.refresh(cita)

    servicio = await session.get(Service, cita.service_id) if cita.service_id else None
    paciente = await session.get(Patient, cita.patient_id) if cita.patient_id else None
    return _pintar(cita, zona, servicio.nombre if servicio else None, servicio.color if servicio else None, paciente)


async def _asegurar_hueco_libre(session: AsyncSession, doctor_id: uuid.UUID, cita: Appointment) -> None:
    """Reactivar una cita no puede montar dos confirmadas encima.

    Comprobamos solape directo contra las demás confirmadas del día de alrededor, en Python y
    con duraciones congeladas: la misma aritmética que usa el motor de disponibilidad.
    """
    inicio, fin = cita.fecha_hora, cita.fecha_hora + timedelta(minutes=cita.duracion_minutos)
    cercanas = await session.scalars(
        select(Appointment).where(
            Appointment.doctor_id == doctor_id,
            Appointment.id != cita.id,
            Appointment.estado == UNIQUE_CONFIRMADA,
            Appointment.fecha_hora >= inicio - timedelta(days=1),
            Appointment.fecha_hora < fin + timedelta(days=1),
        )
    )
    for otra in cercanas:
        fin_otra = otra.fecha_hora + timedelta(minutes=otra.duracion_minutos)
        if otra.fecha_hora < fin and inicio < fin_otra:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="Ese hueco ya lo ocupa otra cita confirmada.",
            )
