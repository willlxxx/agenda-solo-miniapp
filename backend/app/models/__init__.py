"""Modelos SQLAlchemy — las 7 tablas del plan (migración 0001)."""

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

UNIQUE_CONFIRMADA = "confirmada"
ESTADOS_CITA = ("confirmada", "completada", "no_vino", "cancelada")

# La lista de espera es una petición, no una reserva: su estado solo dice si el doctor ya llamó.
UNIQUE_EN_ESPERA = "en_espera"
ESTADOS_LISTA = ("en_espera", "contactada")

# De dónde salió una cita: el enlace público o el panel del médico reservando por teléfono.
# Cambia cómo se obtuvo el consentimiento, no el estado de la agenda.
ORIGEN_PUBLICO = "publico"
ORIGEN_PANEL = "panel"
ORIGENES_CITA = (ORIGEN_PUBLICO, ORIGEN_PANEL)


class Doctor(Base):
    __tablename__ = "doctors"
    __table_args__ = (
        # La banda de la regla de minimización: fuera de 6-12 el dato o se pierde antes de tiempo o
        # se queda de más. La app normaliza el valor, esta trampa lo garantiza para un INSERT a mano.
        CheckConstraint("retencion_meses >= 6 AND retencion_meses <= 12", name="ck_doctor_retencion"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    telefono: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="Europe/Madrid")
    # Cuánto tarda en borrarse sola una cita cerrada. Defecto 12: se borra lo mínimo hasta que el
    # médico diga lo contrario.
    retencion_meses: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("12"))
    # slug_unico alimenta el enlace público tudominio.com/d/{slug}
    slug_unico: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    services: Mapped[list["Service"]] = relationship(back_populates="doctor", cascade="all, delete-orphan")
    availability: Mapped[list["Availability"]] = relationship(back_populates="doctor", cascade="all, delete-orphan")
    blocked_dates: Mapped[list["BlockedDate"]] = relationship(back_populates="doctor", cascade="all, delete-orphan")


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (CheckConstraint("duracion_minutos >= 5 AND duracion_minutos <= 240", name="ck_service_duracion"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    duracion_minutos: Mapped[int] = mapped_column(Integer, nullable=False)
    color: Mapped[str] = mapped_column(Text, nullable=False, server_default="#2563eb")
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    orden: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    doctor: Mapped[Doctor] = relationship(back_populates="services")


class Availability(Base):
    """Franja semanal recurrente. 0 = lunes ... 6 = domingo."""

    __tablename__ = "availability"
    __table_args__ = (
        CheckConstraint("dia_semana >= 0 AND dia_semana <= 6", name="ck_availability_dia"),
        CheckConstraint("hora_fin > hora_inicio", name="ck_availability_horas"),
        UniqueConstraint("doctor_id", "dia_semana", "hora_inicio", name="uq_availability_inicio"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False)
    dia_semana: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    hora_inicio: Mapped[time] = mapped_column(Time, nullable=False)
    hora_fin: Mapped[time] = mapped_column(Time, nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    doctor: Mapped[Doctor] = relationship(back_populates="availability")


class BlockedDate(Base):
    """Vacaciones y festivos: un día completo sin reserva."""

    __tablename__ = "blocked_dates"
    __table_args__ = (UniqueConstraint("doctor_id", "fecha", name="uq_blocked_fecha"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    motivo: Mapped[str | None] = mapped_column(Text)

    doctor: Mapped[Doctor] = relationship(back_populates="blocked_dates")


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint("estado IN ('confirmada','completada','no_vino','cancelada')", name="ck_appointment_estado"),
        CheckConstraint(f"origen IN ('{ORIGEN_PUBLICO}','{ORIGEN_PANEL}')", name="ck_appointment_origen"),
        # Índice parcial: base del motor de disponibilidad y de la protección anti-dobles-reservas.
        Index(
            "ix_appointments_doctor_fecha_confirmada",
            "doctor_id",
            "fecha_hora",
            postgresql_where=text(f"estado = '{UNIQUE_CONFIRMADA}'"),
        ),
        # El barrido del cron de recordatorios va por fecha sin conocer al doctor.
        Index(
            "ix_appointments_fecha_hora_confirmada",
            "fecha_hora",
            postgresql_where=text(f"estado = '{UNIQUE_CONFIRMADA}'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False)
    service_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("services.id"))
    patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patients.id"))
    # fecha_hora en UTC siempre; el timezone del doctor solo se usa al pintar y al calcular huecos.
    fecha_hora: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Congelado al reservar: editar el servicio después no altera citas históricas.
    duracion_minutos: Mapped[int] = mapped_column(Integer, nullable=False)
    estado: Mapped[str] = mapped_column(Text, nullable=False, server_default=UNIQUE_CONFIRMADA)
    # 'publico' = el paciente la autoclictó desde su enlace; 'panel' = la hizo el médico por él.
    origen: Mapped[str] = mapped_column(Text, nullable=False, server_default=ORIGEN_PUBLICO)
    # GDPR: evidencia de consentimiento explícito en el momento de la reserva.
    consentimiento_ok_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recordatorio_24h_enviado: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    recordatorio_1h_enviado: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Cuándo salió cada aviso de verdad. Nullables: una cita antigua ya tenía su booleano sin fecha.
    recordatorio_24h_enviado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recordatorio_1h_enviado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Intentos fallidos acumulados: el despacho se rinde tras unos pocos, no reintenta cada pasada.
    recordatorio_intentos: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = (UniqueConstraint("doctor_id", "nombre", "telefono", name="uq_patient_identidad"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    telefono: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    ultima_visita: Mapped[date | None] = mapped_column(Date)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Waitlist(Base):
    """Pacientes que quieren un hueco que ahora no existe.

    No reserva nada: es una demanda apuntada, así que un apuntado nunca tapa un hueco de la
    disponibilidad. `fecha` admite NULL porque hay quien pide "cualquier día", y `estado` solo
    registra si el doctor ya le avisó.
    """

    __tablename__ = "waitlist"
    __table_args__ = (
        CheckConstraint(f"estado IN ('{UNIQUE_EN_ESPERA}','contactada')", name="ck_waitlist_estado"),
        # El backstop de los duplicados; el caso fecha IS NULL lo resuelve la app dentro del lock.
        UniqueConstraint("doctor_id", "patient_id", "fecha", name="uq_waitlist_paciente_dia"),
        Index(
            "ix_waitlist_doctor_estado",
            "doctor_id",
            "creado_en",
            postgresql_where=text(f"estado = '{UNIQUE_EN_ESPERA}'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False)
    service_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("services.id"))
    patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patients.id"))
    # Día que quería el paciente, en su calendario local del doctor; None = le da igual.
    fecha: Mapped[date | None] = mapped_column(Date)
    # Textual porque la columna ya existía así: 'manana' | 'tarde' | 'cualquier_hora'.
    preferencia_horaria: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(Text, nullable=False, server_default=UNIQUE_EN_ESPERA)
    # El hueco concreto que le ofrecimos y cuándo. Nullables: nadie avisa dos veces sin querer,
    # y si reabrimos la fila (cambia de día) se limpian para volver a contarla como pendiente.
    avisado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hueco_ofrecido: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
