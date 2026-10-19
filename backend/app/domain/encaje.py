"""Qué hueco libre encaja con lo que pidió cada persona de la lista de espera.

Lógica pura otra vez, como el motor de disponibilidad: aquí no se toca la base de datos ni se
piensa en husos. El router trae los días con sus huecos (ya en hora local del doctor) y la
petición tal y como la dejó el paciente: día concreto o "cualquier día", franja y duración.

Reglas que salen de lo que el paciente pidió al apuntarse:
- si pidió un día exacto, solo vale ESE día; "cualquier día" acepta el primero que encaje;
- "por la mañana" es lo que empieza antes de las 14:00 del doctor, "por la tarde" desde ahí;
  no usamos el horario del doctor para definirla porque él puede tener una sola franja larga;
- el hueco lo calcula el motor con la duración del servicio, así que aquí solo se filtra y se
  coge el más antiguo posible: quien llegó antes a la lista se atiende antes.

Los nombres de días y meses van escritos a mano, con `strftime` fuera: dependía del locale del
sistema y en Linux salían en inglés. Prefiero texto determinista.
"""

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time
from urllib.parse import quote

from app.domain.disponibilidad import DiaDisponible, Hueco

# Corte de "mañana": la frontera que el paciente tiene en la cabeza al marcar la casilla.
LIMITE_MANANA = time(14, 0)

DIAS_SEMANA = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MESES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

PREFIJO_ESPARA = "34"
NUMEROS_INTERNACIONALES = range(8, 16)  # Longitudes válidas de un E.164 sin el signo.
_caracteres_telefono = re.compile(r"[^+\d]")


@dataclass(frozen=True)
class Peticion:
    """Lo que pidió el paciente, en los términos que usamos para buscarle hueco."""

    entrada_id: uuid.UUID
    paciente_id: uuid.UUID
    nombre: str
    telefono: str | None
    email: str | None
    servicio_id: uuid.UUID
    servicio: str | None
    fecha: date | None
    preferencia: str


@dataclass(frozen=True)
class Encaje:
    """Un hueco que le sirve a una petición, listo para ofrecer."""

    entrada_id: uuid.UUID
    fecha: date
    inicio: time
    fin: time


def encaja(hueco: Hueco, preferencia: str) -> bool:
    if preferencia == "manana":
        return hueco.inicio < LIMITE_MANANA
    if preferencia == "tarde":
        return hueco.inicio >= LIMITE_MANANA
    return True


def _pisa(a: Hueco, b: Hueco) -> bool:
    """Dos huecos que se solapan (o se tocan por dentro): no caben los dos con la misma cita."""
    return a.inicio < b.fin and b.inicio < a.fin


def _primero_que_encaja(
    fechas: Sequence[date],
    disponibles: Mapping[date, Sequence[Hueco]],
    peticion: Peticion,
) -> Encaje | None:
    for fecha in fechas:
        # Con día pedido no miramos ni uno más: si ese día se llenó, la persona sigue esperando.
        if peticion.fecha is not None and fecha != peticion.fecha:
            continue
        for hueco in disponibles.get(fecha, ()):
            if encaja(hueco, peticion.preferencia or "cualquier_hora"):
                return Encaje(
                    entrada_id=peticion.entrada_id,
                    fecha=fecha,
                    inicio=hueco.inicio,
                    fin=hueco.fin,
                )
    return None


def mejor_hueco(dias: Sequence[DiaDisponible], peticion: Peticion) -> Encaje | None:
    """El primer hueco que cumple lo pedido, barriendo los días en orden (el más próximo primero).

    Con `fecha` fijada no miramos ni un día más: si ese día se llenó, la persona sigue en la
    lista esperando, y ofrecerle otro día sería cambiarle lo que pidió.
    """
    return _primero_que_encaja(
        [d.fecha for d in dias],
        {d.fecha: d.huecos for d in dias},
        peticion,
    )


def huecos_de(cada_servicio: dict[uuid.UUID, Sequence[DiaDisponible]], peticiones: Sequence[Peticion]) -> list[Encaje]:
    """Empareja cada petición con su hueco; las que no encajan, simplemente no salen.

    Un hueco se ofrece una sola vez: en cuanto se lo asignamos a una petición lo quitamos del
    horizonte, junto con todo lo que lo pisaría con esa cita ya puesta (es justo lo que el motor
    dejaría de ofrecer). Así dos personas de la lista no reciben el mismo "ha quedado un hueco"
    y quien apuntó antes se atiende antes: las peticiones llegan ordenadas por orden de llegada.
    """
    orden: dict[uuid.UUID, list[date]] = {}
    restantes: dict[uuid.UUID, dict[date, list[Hueco]]] = {}
    for servicio, dias in cada_servicio.items():
        orden[servicio] = [d.fecha for d in dias]
        restantes[servicio] = {d.fecha: list(d.huecos) for d in dias}

    encajes: list[Encaje] = []
    for peticion in peticiones:
        quedan = restantes.get(peticion.servicio_id, {})
        encontrado = _primero_que_encaja(orden.get(peticion.servicio_id, ()), quedan, peticion)
        if encontrado is None:
            continue
        ofrecido = Hueco(encontrado.inicio, encontrado.fin)
        quedan[encontrado.fecha] = [h for h in quedan.get(encontrado.fecha, []) if not _pisa(h, ofrecido)]
        encajes.append(encontrado)
    return encajes


def numero_local(numero: str) -> str:
    """Deja solo el signo y las cifras: '+34 655 11 22 33' → '+34655112233'."""
    return _caracteres_telefono.sub("", numero)


def telefono_internacional(numero: str | None, prefijo: str = PREFIJO_ESPARA) -> str | None:
    """Número en formato wa.me (cifras, sin '+'), o None si no lo podemos garantizar.

    Asumimos España cuando llega un número nacional de 9 cifras (6xx/7xx de móvil, 8xx/9xx fijo),
    que es lo que escribe casi todo el mundo en el enlace. Con cualquier otra forma devolvemos
    None: el panel seguirá ofreciendo llamar y copiar el mensaje, pero no inventamos un wa.me.
    """
    if not numero:
        return None
    limpio = numero_local(numero)
    if limpio.startswith("+"):
        cifras = limpio[1:]
    elif limpio.startswith("00"):
        cifras = limpio[2:]
    elif limpio.isdigit() and len(limpio) == 9 and limpio[0] in "5679":
        cifras = prefijo + limpio  # nacional español sin prefijo
    elif limpio.isdigit() and limpio.startswith(prefijo) and len(limpio) == len(prefijo) + 9:
        cifras = limpio  # ya marcado desde España, p. ej. 34655112233
    else:
        return None
    if not cifras.isdigit() or len(cifras) not in NUMEROS_INTERNACIONALES:
        return None
    return cifras


def dia_largo(fecha: date) -> str:
    """'martes, 21 de octubre' en español fijo, sin depender del locale del servidor."""
    return f"{DIAS_SEMANA[fecha.weekday()]}, {fecha.day} de {MESES[fecha.month - 1]}"


def mensaje_de_aviso(peticion: Peticion, encaje: Encaje, *, doctor: str) -> str:
    """El texto que el médico manda por su propio WhatsApp o correo.

    No dice "tienes cita": el hueco sigue libre hasta que él lo reserva, y así el paciente no
    cree que ya está confirmado por haber leído el mensaje.
    """
    servicio = (peticion.servicio or "tu visita").lower()
    nombre = peticion.nombre.split(" ")[0] if peticion.nombre else ""
    saludo = f"Hola {nombre}: " if nombre else ""
    return (
        f"{saludo}ha quedado un hueco de {servicio} el {dia_largo(encaje.fecha)} a las "
        f"{encaje.inicio:%H:%M} en la agenda de {doctor}. Si te viene bien, respóndeme y lo dejo "
        "para ti; si ya no lo necesitas, dime y te quito de la lista."
    )


def enlace_whatsapp(numero: str | None, mensaje: str, *, prefijo: str = PREFIJO_ESPARA) -> str | None:
    """wa.me con el mensaje preparado, o None si el teléfono no es internacionalizable."""
    cifras = telefono_internacional(numero, prefijo)
    if cifras is None:
        return None
    return f"https://wa.me/{cifras}?text={quote(mensaje)}"


def enlace_correo(correo: str | None, mensaje: str, asunto: str) -> str | None:
    if not correo:
        return None
    return f"mailto:{correo}?subject={quote(asunto)}&body={quote(mensaje)}"
