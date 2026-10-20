"""Retención: cuánto tarda en borrarse sola una cita cerrada, y qué entra dentro de ese plazo.

Es la parte que se puede equivocar de forma silenciosa, así que vive aquí, sin BD ni reloj de
entorno: `ahora` y los meses del consultorio entran por parámetro y las decisiones son funciones
totalmente deterministas. Un mes no son 30 días —esto es calendario, no aritmética de segundos—, y
la diferencia entre borrar de más y borrar de menos se decide justo en esa resta.

Las tres reglas que sostienen todo lo demás:

- **Solo citas cerradas.** `completada`, `no_vino` y `cancelada`. Una `confirmada` cuyo día ya pasó
  es un cabo suelto del médico (no la marcó), no dato viejo: borrarla sería tapar un hueco del
  registro en lugar de limpiarlo, y el cron no decide por él.
- **El consentimiento se va con la fila.** Mientras la cita existe, `consentimiento_ok_en` es la
  prueba de que el procesamiento fue legítimo. Cuando ya no hay cita que justificar, conservar el
  recibo sería mantener un dato personal indefinidamente, que es exactamente lo que la retención
  quiere evitar.
- **La identidad acompañada no se borra por antigüedad.** Una ficha sigue viva mientras tenga citas
  o peticiones: su antigüedad no importa si alguien la está usando.

El plazo es por consultorio (`doctors.retencion_meses`) dentro de la banda 6-12 meses que admite el
principio de minimización: nadie necesita una cita de hace tres años para gestionar su agenda.
"""

from calendar import monthrange
from datetime import datetime

# La banda que declara el README: menos de 6 meses empieza a romper la contabilidad del consultorio
# (un seguro o una revisión a los 4 meses), más de 12 guarda datos que ya no sirven para nada.
MESES_MIN = 6
MESES_MAX = 12
MESES_DEFECTO = 12

# Estados que han terminado: a partir de aquí la cita ya no espera nada de nadie.
ESTADOS_CERRADOS = ("completada", "no_vino", "cancelada")


def normalizar_meses(meses: int) -> int:
    """El número de meses dentro de la banda, o un error claro.

    Se valida aquí y no solo en el esquema HTTP porque el valor puede venir de una migración, de un
    `PATCH` o de un `INSERT` a mano en la base de datos: la limpieza es irreversible y no tiene
    sentido fiarse de la capa de al lado.
    """
    if not isinstance(meses, int) or isinstance(meses, bool):
        raise TypeError(f"retencion_meses tiene que ser un entero, no {type(meses).__name__}")
    return max(MESES_MIN, min(MESES_MAX, meses))


def restar_meses(momento: datetime, meses: int) -> datetime:
    """El mismo instante de hace `meses` meses, en el mismo calendario.

    El día se recorta al último día del mes destino: 31 de marzo menos un mes es 28 de febrero, no
    3 de marzo ni un `ValueError`. Ese recorte es lo que hace que la pasada de día 31 de un mes largo
    no se quede sin ejecutar por un mes de 28 días.
    """
    if meses < 0:
        raise ValueError("los meses a restar no pueden ser negativos")
    total = momento.year * 12 + (momento.month - 1) - meses
    año, mes_cero = divmod(total, 12)
    mes = mes_cero + 1
    dia = min(momento.day, monthrange(año, mes)[1])
    return momento.replace(year=año, month=mes, day=dia)


def umbral(ahora: datetime, meses: int) -> datetime:
    """La línea: todo lo cerrado y anterior a este instante se puede borrar.

    Compara contra `ahora`, que el cron pasa en UTC y los tests fijan; si dependiera del reloj de la
    máquina, la misma pasada borraría cosas distintas según el día que corre.
    """
    return restar_meses(ahora, normalizar_meses(meses))


def cita_eliminable(estado: str, fecha_hora: datetime, limite: datetime) -> bool:
    """Si esta cita ya puede desaparecer de la base.

    Solo estado y fecha: una `confirmada` viejísima sigue devolviendo False aunque su día haya pasado
    (ver el docstring del módulo), porque cerrarla es decisión del médico, no del calendario. Y una
    `cancelada` del mes pasado sí se va: el paciente la anuló y ya no queda nada que recordar de ella.
    """
    return estado in ESTADOS_CERRADOS and fecha_hora <= limite


def peticion_eliminable(creado_en: datetime, limite: datetime) -> bool:
    """Una petición de la lista de espera apuntada hace más del plazo.

    No se mira el estado: una `en_espera` de hace dos años no es una demanda viva, es un teléfono y
    un email que llevan dos años guardados sin finalidad.
    """
    return creado_en <= limite


def ficha_eliminable(
    creado_en: datetime,
    limite: datetime,
    *,
    citas_restantes: int,
    peticiones_restantes: int,
) -> bool:
    """La ficha de una persona que ya no tiene nada vivo detrás.

    `citas_restantes` son las que quedan DESPUÉS de la limpieza de citas —incluidas las confirmadas
    pendientes, que son las que de verdad importan: borrarle el nombre a quien tiene cita el mes que
    viene sería romper la agenda del médico—, y `peticiones_restantes` lo mismo con la lista.
    """
    if citas_restantes or peticiones_restantes:
        return False
    return creado_en <= limite


def explicacion(meses: int) -> str:
    """La frase que lee el médico en el panel antes de tocar el número.

    Vale también para el README: describe solo lo que el código hace, y dice lo que no hace (las
    citas pendientes) para que nadie espere una limpieza que borra su semana en curso.
    """
    limite = normalizar_meses(meses)
    return (
        f"Las citas cerradas de hace más de {limite} meses se borran solas, con su nombre, su "
        "teléfono y la prueba de consentimiento. Las citas pendientes y las que aún no marcaste "
        "como cerradas no se tocan."
    )
