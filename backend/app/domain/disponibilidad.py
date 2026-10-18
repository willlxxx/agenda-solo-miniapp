"""Motor de disponibilidad: dónde cabe realmente una cita.

Lógica pura y determinista: el router aporta las franjas, las citas, los días bloqueados,
la duración del servicio y la hora actual del doctor. Así se puede testear sin base de
datos y sin depender del reloj del entorno.

Reglas que salen del documento de producto y no se pueden relajar:
- la duración del servicio debe CABER ENTERA dentro del hueco libre;
- un día bloqueado no ofrece nada;
- solo cuentan las franjas activas y las citas confirmadas;
- no se ofrece una hora que ya pasó ni una que queda dentro de la anticipación mínima.

Trabajamos en "minutos absolutos" desde la medianoche del primer día del horizonte. Eso
resuelve de golpe dos problemas: las citas que cruzan la medianoche (una urgencia de
lunes 23:30-00:30 tapa el martes de madrugada) y el horario de verano, porque el router
convierte cada cita a la hora local ANTES de llamar aquí y nosotros solo sumamos minutos.
Las franjas nunca cruzan la medianoche (hora_fin > hora_inicio dentro del mismo día).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time

MINUTOS_DIA = 24 * 60


@dataclass(frozen=True)
class Franja:
    """Franja semanal activa, en hora local del doctor."""

    dia_semana: int  # 0 = lunes
    hora_inicio: time
    hora_fin: time


@dataclass(frozen=True)
class CitaLocal:
    """Cita confirmada, ya convertida a la hora local del doctor (sin zona)."""

    inicio: datetime
    duracion_minutos: int


@dataclass(frozen=True)
class Hueco:
    inicio: time
    fin: time


@dataclass(frozen=True)
class DiaDisponible:
    fecha: date
    huecos: tuple[Hueco, ...]


def _min_del_dia(momento: time) -> int:
    return momento.hour * 60 + momento.minute


def _hora_desde_minutos(minutos: int) -> time:
    return time(hour=(minutos % MINUTOS_DIA) // 60, minute=minutos % 60)


def _absoluto(dia: date, minutos: int, referencia: date) -> int:
    """Minutos desde la medianoche de `referencia`; admite negativos y valores > 24 h."""
    return (dia - referencia).days * MINUTOS_DIA + minutos


def fusionar(intervalos: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Une intervalos solapados o pegados, ordenados. Citas contiguas no dejan micro-huecos."""
    resultado: list[tuple[int, int]] = []
    for inicio, fin in sorted(intervalos):
        if fin <= inicio:
            continue
        if resultado and inicio <= resultado[-1][1]:
            antes_ini, antes_fin = resultado[-1]
            resultado[-1] = (antes_ini, max(antes_fin, fin))
        else:
            resultado.append((inicio, fin))
    return resultado


def restar(rango: tuple[int, int], ocupados: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Trozos de `rango` que quedan libres tras quitar `ocupados` (ya fusionados y ordenados)."""
    ini, fin = rango
    libres: list[tuple[int, int]] = []
    cursor = ini
    for o_ini, o_fin in ocupados:
        if o_fin <= cursor:
            continue
        if o_ini >= fin:
            break
        if o_ini > cursor:
            libres.append((cursor, min(o_ini, fin)))
        cursor = o_fin
        if cursor >= fin:
            break
    if cursor < fin:
        libres.append((cursor, fin))
    return libres


def calcular_disponibilidad(
    *,
    franjas: Iterable[Franja],
    citas: Iterable[CitaLocal],
    bloqueados: Iterable[date],
    duracion_minutos: int,
    fechas: Sequence[date],
    ahora: datetime,
    paso_minutos: int = 15,
    anticipacion_minutos: int = 0,
    max_huecos_por_dia: int = 60,
) -> list[DiaDisponible]:
    """Huecos ofrecibles por día, en hora local del doctor.

    `ahora` es la hora actual del doctor SIN zona (la convierte el router con su huso).
    `fechas` debe venir ordenada y ascendente; su primer elemento es la referencia temporal.
    Los días sin huecos no aparecen: la página pública solo pinta lo reservable.
    """
    if not fechas or duracion_minutos <= 0:
        return []

    referencia = fechas[0]
    minutos_ocupados = []
    for cita in citas:
        inicio = _absoluto(cita.inicio.date(), cita.inicio.hour * 60 + cita.inicio.minute, referencia)
        minutos_ocupados.append((inicio, inicio + cita.duracion_minutos))
    ocupados = fusionar(minutos_ocupados)
    bloqueados_set = set(bloqueados)

    por_dia_semana: dict[int, list[tuple[int, int]]] = {}
    for franja in franjas:
        inicio = _min_del_dia(franja.hora_inicio)
        fin = _min_del_dia(franja.hora_fin)
        if fin > inicio:
            por_dia_semana.setdefault(franja.dia_semana, []).append((inicio, fin))

    hoy = ahora.date()
    minuto_actual = ahora.hour * 60 + ahora.minute
    resultado: list[DiaDisponible] = []

    for fecha in fechas:
        if fecha < hoy or fecha in bloqueados_set:
            continue
        franjas_del_dia = sorted(por_dia_semana.get(fecha.weekday(), []))
        if not franjas_del_dia:
            continue

        offset = (fecha - referencia).days * MINUTOS_DIA
        # Solo es reservable un hueco que empieza al menos `anticipacion_minutos` después de ahora.
        corte = offset + minuto_actual + anticipacion_minutos if fecha == hoy else -1
        huecos: list[Hueco] = []
        lleno = False

        for ini, fin in franjas_del_dia:
            for libre_ini, libre_fin in restar((offset + ini, offset + fin), ocupados):
                # La rejilla parte de la apertura de la franja, no de medianoche:
                # si el doctor abre a las 9:05, los huecos empiezan a las 9:05.
                t = libre_ini
                desfase = (t - (offset + ini)) % paso_minutos
                if desfase:
                    t += paso_minutos - desfase
                while t + duracion_minutos <= libre_fin:
                    if t >= corte:
                        huecos.append(Hueco(_hora_desde_minutos(t), _hora_desde_minutos(t + duracion_minutos)))
                        if len(huecos) >= max_huecos_por_dia:
                            lleno = True
                            break
                    t += paso_minutos
                if lleno:
                    break
            if lleno:
                break

        if huecos:
            resultado.append(DiaDisponible(fecha=fecha, huecos=tuple(huecos)))

    return resultado
