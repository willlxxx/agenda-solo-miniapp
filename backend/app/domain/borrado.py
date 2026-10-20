"""Qué se va y qué se queda cuando el médico suprime a un paciente (art. 17 RGPD).

Decide y redacta, sin tocar la base de datos. La regla que sostiene todo el módulo es esta:
suprimir los datos personales de una persona NO puede borrar la actividad del consultorio. Si la
fila de la cita desaparece con la ficha, el médico pierde el histórico de lo que atendió y —lo que
es peor— el hueco vuelve a estar libre en el motor de disponibilidad, así que alguien podría
reservar una hora que ya fue atendida. Por eso la supresión suelta la identidad y conserva la fila.

También es lo que se puede defender ante una reclamación: constancia de la relación profesional
(sin datos), prueba del consentimiento con el que se reservó, y cero forma de reconstruir quién era.
"""

import unicodedata

# En humano, no en nombres de columna: este texto lo lee el médico antes de pulsar.
LO_QUE_SE_VA = ("el nombre", "el teléfono", "el email", "la última visita")

LO_QUE_SE_CONSERVA = (
    "la hora, la duración, el servicio y el estado de cada cita",
    "la prueba del consentimiento con la que se reservó",
    "el día y el servicio que pidió quien estaba en la lista de espera",
)

SIN_NOMBRE = "Paciente suprimido"


def _slug(texto: str) -> str:
    """Minúsculas, sin acentos y con guiones: para que el nombre de archivo no dependa del SO."""
    sin_acentos = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    partes = "".join(c if c.isalnum() else "-" for c in sin_acentos.lower()).split("-")
    return "-".join(p for p in partes if p)


def nombre_de_archivo(nombre: str) -> str:
    """`Ana Paciente` -> `datos-de-ana-paciente.json`.

    Si del nombre no queda nada útil (iniciales, ideogramas sin descomponer, espacios) se pone
    `datos-de-paciente.json`: un archivo llamado `datos-de-n.json` no le dice a nadie de quién es.
    """
    slug = _slug(nombre)
    return f"datos-de-{slug if len(slug) > 2 else 'paciente'}.json"


def _plural(cantidad: int, uno: str, varios: str) -> str:
    return uno if cantidad == 1 else varios


def _lista(termios: tuple[str, ...]) -> str:
    """('el nombre', 'el teléfono', 'el email') -> 'el nombre, el teléfono y el email'."""
    if len(termios) <= 1:
        return termios[0] if termios else ""
    return ", ".join(termios[:-1]) + " y " + termios[-1]


def confirmacion(nombre: str, citas: int, por_llegar: int, peticiones: int) -> str:
    """Lo que el panel muestra ANTES de borrar, con las cuentas reales de esa ficha.

    Un borrado irreversible no puede ser un clic sin más: el texto dice qué se pierde, qué se
    queda y cuántas citas van a quedar sin nombre a partir de ahora. Si no hay nada que soltar,
    también lo dice: borrar a alguien sin citas es otra situación y merecen frases distintas.
    """
    cabecera = f"Se suprime la ficha de {nombre}: {_lista(LO_QUE_SE_VA)}."
    if citas == 0 and peticiones == 0:
        cuerpo = "No tiene citas ni peticiones en la lista, así que no queda ningún rastro."
    else:
        trozos = []
        if citas:
            una = f"{citas} {_plural(citas, 'cita', 'citas')} sin nombre"
            if por_llegar:
                una += f" —{por_llegar} {_plural(por_llegar, 'sigue', 'siguen')} por llegar—"
            trozos.append(una)
        if peticiones:
            trozos.append(
                f"{peticiones} {_plural(peticiones, 'petición', 'peticiones')} de la lista de espera sin nombre"
            )
        cuerpo = "Quedan " + " y ".join(trozos)
        if citas:
            cuerpo += ", y se conserva su constancia con la prueba del consentimiento"
        cuerpo += "."
    cierre = "No se puede deshacer: si vuelve a reservar, será una ficha nueva."
    return " ".join([cabecera, cuerpo, cierre])


def explicacion_de_conserva() -> str:
    """Por qué la app no borra también las citas: va en el export, para que el médico lo pueda citar."""
    return "Se conserva sin identidad: " + "; ".join(LO_QUE_SE_CONSERVA) + "."
