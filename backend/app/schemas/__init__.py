"""Pydantic v2: entrada/salida de la API."""

from datetime import date, datetime, time
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class DoctorPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nombre: str
    email: EmailStr
    telefono: str | None
    timezone: str
    slug_unico: str


class RegistroIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    telefono: str | None = Field(default=None, max_length=40)
    timezone: str = Field(default="Europe/Madrid", min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _huso_real(cls, valor: str) -> str:
        """Si no existe en la base IANA, el motor de disponibilidad se caería en junio."""
        try:
            ZoneInfo(valor)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Huso horario desconocido: {valor}") from exc
        return valor


class AccesoIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ServiceIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    duracion_minutos: int = Field(ge=5, le=240)
    color: str = Field(default="#2563eb", pattern=r"^#[0-9a-fA-F]{6}$")
    activo: bool = True
    orden: int = 0


class ServicePatch(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=80)
    duracion_minutos: int | None = Field(default=None, ge=5, le=240)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    activo: bool | None = None
    orden: int | None = None


class ServicePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nombre: str
    duracion_minutos: int
    color: str
    activo: bool
    orden: int


class AvailabilityIn(BaseModel):
    dia_semana: int = Field(ge=0, le=6, description="0 = lunes")
    hora_inicio: time
    hora_fin: time
    activo: bool = True


class AvailabilityPatch(BaseModel):
    dia_semana: int | None = Field(default=None, ge=0, le=6)
    hora_inicio: time | None = None
    hora_fin: time | None = None
    activo: bool | None = None


class AvailabilityPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dia_semana: int
    hora_inicio: time
    hora_fin: time
    activo: bool


class BlockedDateIn(BaseModel):
    fecha: date
    motivo: str | None = Field(default=None, max_length=120)


class BlockedDatePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    fecha: date
    motivo: str | None


# --- Página pública de reservas (sin sesión del doctor) ---
# Minimización GDPR: aquí no sale el email ni el teléfono del doctor.


class DoctorResumen(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    nombre: str
    slug_unico: str
    timezone: str


class FichaPublica(BaseModel):
    doctor: DoctorResumen
    servicios: list[ServicePublic]


class HuecoPublic(BaseModel):
    inicio: time
    fin: time


class DiaDisponiblePublic(BaseModel):
    fecha: date
    huecos: list[HuecoPublic]


class DisponibilidadPublic(BaseModel):
    doctor: DoctorResumen
    servicio: ServicePublic
    paso_minutos: int
    anticipacion_minutos: int
    dias: list[DiaDisponiblePublic]


class CitaIn(BaseModel):
    """Reserva del paciente.

    `fecha` y `hora` son el día y la hora LOCAL DEL DOCTOR, tal como las vio el paciente en
    su enlace. Convertimos nosotros con su huso: si confiáramos en el reloj del navegador,
    un paciente viajando por otro continente reservaría la hora equivocada.
    """

    servicio: UUID
    fecha: date
    hora: time
    nombre: str = Field(min_length=2, max_length=120)
    telefono: str | None = Field(default=None, max_length=40)
    email: EmailStr | None = None
    consentimiento: bool = Field(description="Casilla obligatoria de GDPR; false se rechaza")


class CitaPublica(BaseModel):
    id: UUID
    fecha: date
    hora: time
    fin: time
    duracion_minutos: int
    servicio: str
    doctor: str


# --- Agenda del doctor (con sesión) ---


class PacienteAgenda(BaseModel):
    """Datos de contacto: es el propio paciente del doctor, lo mínimo para poder avisarle."""

    nombre: str
    telefono: str | None
    email: str | None


class CitaAgenda(BaseModel):
    id: UUID
    # Campo máquina: el instante real en UTC, para ordenar y saber si ya pasó.
    inicio_utc: datetime
    # Campo pintura: día y hora locales del doctor, calculados con su huso guardado.
    fecha: date
    hora: time
    fin: time
    duracion_minutos: int
    estado: str
    servicio: str | None
    color: str
    paciente: PacienteAgenda | None


class AgendaVentana(BaseModel):
    timezone: str
    desde: date
    hasta: date
    total: int
    confirmadas: int
    citas: list[CitaAgenda]


class EstadoIn(BaseModel):
    estado: Literal["confirmada", "completada", "no_vino", "cancelada"]
