"""Pydantic v2: entrada/salida de la API."""

from datetime import date, datetime, time
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.utils import normalizar_slug


class DoctorPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nombre: str
    email: EmailStr
    telefono: str | None
    timezone: str
    slug_unico: str


def huso_valido(valor: str) -> str:
    """La regla de la zona horaria, escrita una sola vez: la comparten el registro y la ficha editada.

    Es la misma comprobación que hace `app.api.deps.zona_del` al leer el valor guardado. Si aquí se
    colara un huso fuera de la base IANA, el panel se caería con un 500 en la primera agenda del mes.
    """
    try:
        ZoneInfo(valor)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Huso horario desconocido: {valor}") from exc
    return valor


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
        return huso_valido(valor)


class AccesoIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class DoctorPatch(BaseModel):
    """Lo que el consultorio puede cambiar de su ficha. Todo opcional: se guarda solo lo que llega.

    `email` y `slug_unico` son únicos en la base, así que el endpoint comprueba si los tiene otra
    cuenta y devuelve 409. Aquí no se resuelve con un sufijo como en el registro: un enlace que
    nadie pidió, con un `-2` pegado, es un enlace que el médico no va a reconocer.
    """

    nombre: str | None = Field(default=None, min_length=2, max_length=120)
    email: EmailStr | None = None
    telefono: str | None = Field(default=None, max_length=40)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    slug_unico: str | None = Field(default=None, min_length=1, max_length=60)

    @field_validator("timezone")
    @classmethod
    def _huso_real(cls, valor: str | None) -> str | None:
        return None if valor is None else huso_valido(valor)

    @field_validator("slug_unico")
    @classmethod
    def _slug_normalizado(cls, valor: str | None) -> str | None:
        """'Mi Consulta' -> 'mi-consulta': el enlace se guarda ya escrito como lo verá el paciente."""
        if valor is None:
            return None
        normalizado = normalizar_slug(valor)
        if normalizado == "doctor":
            raise ValueError("El enlace necesita letras o números; con esos signos no se forma.")
        return normalizado

    @field_validator("nombre", "email", "timezone", "slug_unico")
    @classmethod
    def _unicos_y_esenciales_no_se_vacian(cls, valor: object) -> object:
        """Mandarlos como `null` no es vaciarlos: esas columnas son NOT NULL y un 500 aquí sería culpa nuestra."""
        if valor is None:
            raise ValueError("Este campo no puede quedar vacío")
        return valor


class CambioContrasenaIn(BaseModel):
    """Cambio de clave con la sesión ya abierta. La actual se sigue pidiendo: ver nota en el endpoint."""

    actual: str = Field(min_length=1, max_length=128)
    nueva: str = Field(min_length=8, max_length=128)


class RecuperarSolicitudIn(BaseModel):
    """Pedir el enlace. Solo hace falta el email, y la respuesta no va a decir si esa cuenta existe."""

    email: EmailStr


class SolicitudRecuperacionOut(BaseModel):
    """La única respuesta que da el pedido, igual para las dos mitades del caso.

    No hay campo `existe` ni un 404 para el email desconocido: cualquier diferencia contaría qué correos
    tienen una agenda montada, que es justo el listado que un atacante querría para apuntar sus intentos.
    """

    aceptada: bool = True


class DisponibilidadRecuperacion(BaseModel):
    """Lo que la cara pública lee antes de ofrecer el botón de recuperar.

    `razon` es el identificador del caso, no un texto: el frontend decide qué dice en cada uno, y así
    el mensaje en español no sale del lado de la interfaz.
    """

    disponible: bool
    razon: str | None = None


class RecuperarConfirmacionIn(BaseModel):
    """Consumir el enlace: el token tal cual viajó en el fragmento, y la clave nueva."""

    token: str = Field(min_length=20, max_length=200)
    nueva: str = Field(min_length=8, max_length=128)


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
    # El horizonte que se ha consultado, para que la página pueda ofrecer la lista de espera
    # diciendo «no queda nada entre el X y el Y» aunque los días vacíos no salgan en `dias`.
    desde: date
    hasta: date
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


# --- Lista de espera ---
# El paciente pide un hueco que no existe; esto no reserva nada, es una demanda apuntada.


class ListaDeEsperaIn(BaseModel):
    servicio: UUID
    fecha: date | None = Field(default=None, description="Día que quería (null = cualquier día)")
    preferencia: Literal["manana", "tarde", "cualquier_hora"] = "cualquier_hora"
    nombre: str = Field(min_length=2, max_length=120)
    telefono: str | None = Field(default=None, max_length=40)
    email: EmailStr | None = None
    consentimiento: bool = Field(description="Casilla obligatoria: guardamos sus datos para avisarle")


class ListaDeEsperaPublica(BaseModel):
    id: UUID
    doctor: str
    servicio: str
    fecha: date | None
    preferencia: str
    estado: str
    # False si acaba de crearse; true si ya estaba apuntado y no hemos duplicado la fila.
    creado: bool


class EntradaLista(BaseModel):
    """Lo que ve el doctor: contacto para llamar y qué pedía el paciente. Nada clínico."""

    id: UUID
    nombre: str
    telefono: str | None
    email: str | None
    servicio: str | None
    fecha: date | None
    preferencia: str | None
    estado: str
    # El instante real de la petición: la lista se atiende por orden de llegada.
    pedido_utc: datetime
    # Cuándo le avisamos (y con qué hueco), para no insistir dos veces sobre la misma fila.
    avisado_utc: datetime | None
    hueco_ofrecido_utc: datetime | None


class ListaEspera(BaseModel):
    total: int
    en_espera: int
    contactadas: int
    filtro: Literal["pendientes", "contactadas", "todas"]
    entradas: list[EntradaLista]


class EstadoListaIn(BaseModel):
    estado: Literal["en_espera", "contactada"]


# --- Avisar de un hueco libre a quien espera ---


class AvisoHueco(BaseModel):
    """Petición pendiente + el hueco que ya le sirve, con el aviso escrito y sus enlaces.

    El backend no manda nada: devolvemos `wa.me` y `mailto:` para que el médico lo envíe él
    desde su propia cuenta. Por eso el mensaje habla de "hueco", no de cita confirmada.
    """

    id: UUID
    nombre: str
    telefono: str | None
    email: str | None
    servicio: str | None
    # Lo que pidió, para que el médico sepa por qué este hueco es el suyo.
    pidio_fecha: date | None
    preferencia: str | None
    # El hueco ofrecido, en la hora local del doctor: es lo que hay que escribirle.
    fecha: date
    inicio: time
    fin: time
    mensaje: str
    # None cuando no podemos garantizar el formato (teléfono raro, sin email): el panel
    # seguirá ofreciendo llamar y copiar el mensaje.
    whatsapp: str | None
    correo: str | None


class AvisosHueco(BaseModel):
    timezone: str
    desde: date
    hasta: date
    # Cuántas pendientes hay y a cuántas se les puede ofrecer algo ahora mismo.
    pendientes: int
    con_hueco: int
    avisos: list[AvisoHueco]


class ReservaAsistidaIn(BaseModel):
    """El médico escribe la cita por el paciente: solo falta el hueco, lo demás ya está guardado."""

    fecha: date
    hora: time


class ReservaAsistida(BaseModel):
    id: UUID
    fecha: date
    hora: time
    fin: time
    duracion_minutos: int
    servicio: str
    doctor: str
    paciente: str
    # La fila de la lista, ya cerrada como `contactada`, para pintar el cambio sin recargar.
    entrada: EntradaLista


# --- Recordatorios automáticos (despacho interno para el cron) ---


class ResumenRecordatorios(BaseModel):
    """Lo que hizo una pasada del cron: contadores, nunca datos del paciente.

    Quien monta el cron necesita saber si funcionó, no leer la agenda de otro doctor, así que aquí
    no salen nombres ni correos. Los contadores separan los tres silencios distintos que hay:
    `sin_correo` (no se puede), `omitidos` (otro proceso ya lo mandó) y `agotados` (dejamos de
    reintentar).
    """

    model_config = ConfigDict(from_attributes=True)

    proveedor: str
    candidatas: int
    sin_correo: int
    planificados: int
    enviados: int
    enviados_24h: int
    enviados_1h: int
    omitidos: int
    errores: int
    agotados: int
    husos_invalidos: int


class ResumenLimpieza(BaseModel):
    """Contadores de una pasada de retención. Tampoco salen datos del paciente.

    `simulada` importa en el log: una pasada de ensayo muestra exactamente lo que borraría la real,
    y sin este campo las dos leerían igual en el cron. `conflictos` es la señal de que hubo que
    deshacer la tanda de un consultorio porque entró una reserva a mitad.
    """

    model_config = ConfigDict(from_attributes=True)

    doctores: int
    citas_eliminadas: int
    peticiones_eliminadas: int
    fichas_eliminadas: int
    fichas_en_uso: int
    confirmadas_sin_cerrar: int
    conflictos: int
    simulada: bool


class RetencionIn(BaseModel):
    """Cuánto tarda en borrarse una cita cerrada. La banda la fija el dominio, no este esquema."""

    meses: int = Field(ge=6, le=12, description="Meses de retención del consultorio (6 a 12)")


class RetencionPublic(BaseModel):
    """El plazo del consultorio y la frase que explica qué hace. El panel no reinventa la frase."""

    meses: int
    explicacion: str


# --- Ficha del paciente: export y supresión (RGPD art. 15, 17 y 20) ---


class CitaDeFicha(BaseModel):
    """La cita como la ve su dueño: en su huso, y con la prueba del consentimiento anexa."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    inicio_utc: datetime
    fecha: date
    hora: time
    fin: time
    duracion_minutos: int
    estado: str
    # 'publico' = la clicó el paciente; 'panel' = la escribió el médico attestando su petición.
    origen: str
    servicio: str | None
    consentimiento_en: datetime
    recordatorio_24h_en: datetime | None
    recordatorio_1h_en: datetime | None
    recordatorio_intentos: int


class PeticionDeFicha(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    fecha: date | None
    preferencia: str | None
    estado: str
    servicio: str | None
    pedido_utc: datetime
    avisado_utc: datetime | None


class FichaPaciente(BaseModel):
    """La ficha que abre el panel desde una cita, con lo que hay guardado de esa persona.

    Es el único sitio donde salen el email y la última visita juntos, y siempre tras la cookie de
    sesión: la lista de la agenda sigue pintando nombre y teléfono, nada más.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nombre: str
    telefono: str | None
    email: str | None
    ultima_visita: date | None
    creado_en: datetime
    timezone: str
    citas: list[CitaDeFicha]
    peticiones: list[PeticionDeFicha]
    # Lo que el panel muestra como consecuencia del borrado, antes de que nadie pulse.
    aviso: str


class Supresion(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    paciente: str
    # Cuántas filas siguen en pie sin identidad: el médico necesita el número, no el nombre.
    citas_sin_identidad: int
    citas_por_llegar: int
    peticiones_sin_identidad: int
    aviso: str
