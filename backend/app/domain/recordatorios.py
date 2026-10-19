"""Qué citas tocan recordatorio ahora mismo, y qué texto sale por el correo.

Dominio puro otra vez: ni base de datos ni SMTP. `plan_de_envios` recibe las citas candidatas con
sus banderas de despacho y la hora actual, y devuelve los mensajes que hay que mandar. Así se pueden
probar las dos reglas que de verdad molestan al paciente --cuándo se dispara y a qué hora local se le
escribe-- sin depender del reloj del entorno ni de un proveedor.

Reglas, y por qué son así:

- **Ventana, no instante.** Un cron que se salta un tick no debe perder el aviso, y uno que estuvo
  caído tres días no debe mandar un "mañana tienes cita" sobre una cita ya pasada. Cada recordatorio
  vale mientras el adelanto esté dentro de su ventana: 24 h -> [12 h, 24 h], 1 h -> [40 min, 60 min].
- **Horas de silencio 08:00-21:00 en el huso del doctor.** Un recordatorio a las 03:00 es una queja
  segura. Si la ventana entera cae de madrugada (cita a las 21:30 con el aviso de 1 h fuera de
  horario), ese aviso simplemente no sale: mejor uno menos que uno molesto.
- **Nada que no podamos cumplir.** El texto ofrece llamar al médico si hay teléfono guardado; sin
  teléfono dice "avísanos" y punto. No hay enlace de anular ni botón de confirmar: eso sería prometer
  un flujo que no existe.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.encaje import DIAS_SEMANA, MESES

TIPO_24H = "24h"
TIPO_1H = "1h"
TIPOS = (TIPO_24H, TIPO_1H)

# (adelanto máximo, adelanto mínimo) dentro del cual el aviso todavía es correcto.
VENTANAS: dict[str, tuple[timedelta, timedelta]] = {
    TIPO_24H: (timedelta(hours=24), timedelta(hours=12)),
    TIPO_1H: (timedelta(hours=1), timedelta(minutes=40)),
}

# Franja local del doctor en la que escribimos: desde las 08:00 (inclusive) hasta las 21:00 (exclusivo).
SILENCIO_DESDE = time(8, 0)
SILENCIO_HASTA = time(21, 0)


@dataclass(frozen=True)
class CitaParaRecordar:
    """Una cita confirmada con lo necesario para avisar: quién, cuándo y a dónde."""

    id: UUID
    doctor_nombre: str
    doctor_telefono: str | None
    zona: ZoneInfo
    inicio_utc: datetime
    servicio: str
    paciente_nombre: str
    email: str
    enviado_24h: bool
    enviado_1h: bool


@dataclass(frozen=True)
class Aviso:
    """Un correo a punto de salir: el despacho solo tiene que entregarlo y marcar la bandera."""

    cita_id: UUID
    tipo: str
    para: str
    asunto: str
    cuerpo: str


def adelantos_posibles() -> tuple[timedelta, timedelta]:
    """(mínimo, máximo) de adelanto que alguna ventana acepta.

    Es lo que hay que leer de la base de datos antes de llamar a `plan_de_envios`: si el despacho
    filtrara con una cifra inventada aquí, un cambio de ventana en este mismo archivo dejaría citas
    sin avisar sin que ningún test lo convirtiera en rojo.
    """
    limites = [limite for ventana in VENTANAS.values() for limite in ventana]
    return min(limites), max(limites)


def en_ventana(tipo: str, adelanto: timedelta) -> bool:
    maximo, minimo = VENTANAS[tipo]
    return minimo <= adelanto <= maximo


def dentro_de_horario(local: datetime) -> bool:
    return SILENCIO_DESDE <= local.time() < SILENCIO_HASTA


def fecha_absoluta(dia: date) -> str:
    """'miércoles 21 de octubre', dicho del todo y sin la coma del formato de `dia_largo`."""
    return f"{DIAS_SEMANA[dia.weekday()]} {dia.day} de {MESES[dia.month - 1]}"


def cuando_es(dia: date, hoy: date) -> str:
    """'hoy', 'mañana' o 'el miércoles 21 de octubre': el idioma del paciente, no un cálculo.

    Se escribe la fecha entera cuando queda más de un día, porque "el miércoles" suena a esta misma
    semana en un aviso que llega con dos semanas de antelación.
    """
    if dia == hoy:
        return "hoy"
    if dia == hoy + timedelta(days=1):
        return "mañana"
    return f"el {fecha_absoluta(dia)}"


def mensaje_de_recordatorio(cita: CitaParaRecordar, tipo: str, ahora: datetime) -> tuple[str, str]:
    """(asunto, cuerpo) en español, con la hora local del doctor y sin prometer nada raro.

    `tipo` no cambia el texto: lo que lee el paciente sale de la distancia real a la cita, así que el
    aviso que se retrasa por las horas de silencio sigue diciendo "hoy" y no "mañana".
    """
    inicio_local = cita.inicio_utc.astimezone(cita.zona)
    hoy_local = ahora.astimezone(cita.zona).date()
    cuando = cuando_es(inicio_local.date(), hoy_local)
    hora = inicio_local.strftime("%H:%M")
    # Una cita pudo guardarse sin servicio (el médico la escribió a mano): se recuerda igual.
    servicio = (cita.servicio or "").strip().lower()
    tiene_cita = f"tienes cita de {servicio}" if servicio else "tienes cita"

    asunto = f"Recordatorio: {cuando} a las {hora} con {cita.doctor_nombre}"
    saludo = f"Hola {cita.paciente_nombre.split(' ')[0]}:" if cita.paciente_nombre.strip() else "Hola:"
    detalle = f"{cuando.capitalize()} {tiene_cita} a las {hora}, en la consulta de {cita.doctor_nombre}."
    cambiar = (
        f"Si te viene mal, llámame al {cita.doctor_telefono} y la movemos."
        if cita.doctor_telefono
        else "Si te viene mal, avísanos y la movemos."
    )
    cuerpo = (
        f"{saludo}\n\n{detalle}\n\n{cambiar}\n\n"
        f"Este aviso lo envía automáticamente la agenda de {cita.doctor_nombre} para que no se te pase la hora."
    )
    return asunto, cuerpo


def plan_de_envios(citas: Sequence[CitaParaRecordar], ahora: datetime) -> list[Aviso]:
    """Los avisos que hay que mandar en este tick, ya redactados y en el orden de las citas.

    Una cita produce como mucho un aviso por pasada: si estuvieran pendientes los dos, se manda el de
    1 h, que es el que sirve a esas alturas, y el de 24 h se queda para el siguiente tick (donde ya
    estará fuera de ventana y se perderá, que es lo correcto).

    Se salta sin más las citas sin correo: el canal es email, y un destino vacío no es un fallo del
    proveedor, es una cita a la que no se puede escribir por aquí.
    """
    avisos: list[Aviso] = []
    for cita in citas:
        if not (cita.email or "").strip():
            continue
        for tipo in (TIPO_1H, TIPO_24H):
            ya_enviado = cita.enviado_1h if tipo == TIPO_1H else cita.enviado_24h
            if ya_enviado or not en_ventana(tipo, cita.inicio_utc - ahora):
                continue
            # De madrugada no escribimos, aunque la ventana siga abierta: el próximo tick decidirá.
            if not dentro_de_horario(ahora.astimezone(cita.zona)):
                break
            asunto, cuerpo = mensaje_de_recordatorio(cita, tipo, ahora)
            avisos.append(Aviso(cita_id=cita.id, tipo=tipo, para=cita.email, asunto=asunto, cuerpo=cuerpo))
            break
    return avisos
