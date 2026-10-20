"""Limpieza de retención: lo que ejecuta el cron cuando le toca barrer el dato viejo.

Igual que el despacho de recordatorios, **decidir** está en `app/domain/retencion.py` y aquí solo se
lee, se candadea y se escribe: las consultas traen candidatos con un criterio ancho (`creado_en` o
`fecha_hora` anteriores a `ahora`) y es el dominio, función a función, quien dice cuáles se van. Si
el SQL volviera a escribir la regla por su cuenta, un cambio en el dominio cambiaría los contadores
pero no el borrado, y aquí un desajuste no es un correo de más: son datos de salud perdidos.

Las reglas de escritura:

- **Un advisory lock por doctor** (`limpieza:`), clave propia: la limpieza no reserva huecos ni marca
  avisos, así que no tiene por qué esperar a nadie.
- **Primero citas y peticiones, después fichas.** "Ficha huérfana" se evalúa sobre el estado que
  queda DESPUÉS de los dos borrados, dentro de la misma transacción. Al revés, una ficha cuyo último
  histórico se acaba de eliminar seguiría apareciendo como "usada" y el médico leería que se queda
  algo que en realidad se va.
- **El candado de la base, no el nuestro.** Si entre nuestra lectura y el `DELETE` alguien reserva
  una cita a nombre de esa ficha, la FK sin `ondelete` corta la operación con `IntegrityError`: la
  transacción se deshace entera y contamos el conflicto en vez de borrarle su reserva a quien acaba
  de llegar.
- **Simular borra de verdad y deshace.** Una pasada de ensayo no puede conformarse con contar lo que
  *parecía* elegible: hace los mismos `DELETE` bajo el mismo candado y hace `ROLLBACK` al final, así
  que las cuentas que muestra son exactamente las que saldrían.

`ahora` entra de fuera y en UTC, otra vez por los tests: una retención de 12 meses se prueba con
fechas fijas, no esperando a que el CI corra el día que toca.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.retencion import (
    ESTADOS_CERRADOS,
    cita_eliminable,
    ficha_eliminable,
    normalizar_meses,
    peticion_eliminable,
    umbral,
)
from app.models import UNIQUE_CONFIRMADA, Appointment, Doctor, Patient, Waitlist


class _Simulada(Exception):
    """Señala el final de una pasada de ensayo: sale de la transacción y la hace rollback."""


@dataclass
class Resumen:
    """Lo que hizo la pasada, sin un solo dato personal en el payload."""

    doctores: int = 0
    citas_eliminadas: int = 0
    peticiones_eliminadas: int = 0
    fichas_eliminadas: int = 0
    # Fichas antiguas que, aun después del barrido, siguen teniendo citas o peticiones detrás.
    fichas_en_uso: int = 0
    # Citas confirmadas cuyo día ya pasó: el cron no las toca, pero el médico debe saber cuántas lleva.
    confirmadas_sin_cerrar: int = 0
    # Doctores cuyo borrado cortó la FK porque entró una reserva a mitad: nada a medias.
    conflictos: int = 0
    simulada: bool = False


async def _doctores(session: AsyncSession) -> list[tuple[uuid.UUID, int]]:
    """Scalares, no ORM: la primera transacción de la pasada expira cualquier objeto cargado."""
    filas = await session.execute(select(Doctor.id, Doctor.retencion_meses).order_by(Doctor.id))
    return [(fila.id, fila.retencion_meses) for fila in filas]


async def _citas_a_borrar(
    session: AsyncSession, doctor_id: uuid.UUID, ahora: datetime, limite: datetime
) -> list[uuid.UUID]:
    """Las cerradas que el dominio declara fuera de plazo, elegidas fila a fila.

    El `where` no decide la retención: solo recorta el lectura a lo que ya pasó. Una cerrada de
    mañana seguiría estando fuera de `limite` de todos modos, así que no se escapa nada.
    """
    filas = await session.execute(
        select(Appointment.id, Appointment.estado, Appointment.fecha_hora).where(
            Appointment.doctor_id == doctor_id,
            Appointment.estado.in_(ESTADOS_CERRADOS),
            Appointment.fecha_hora <= ahora,
        )
    )
    return [fila.id for fila in filas if cita_eliminable(fila.estado, fila.fecha_hora, limite)]


async def _peticiones_a_borrar(
    session: AsyncSession, doctor_id: uuid.UUID, ahora: datetime, limite: datetime
) -> list[uuid.UUID]:
    filas = await session.execute(
        select(Waitlist.id, Waitlist.creado_en).where(
            Waitlist.doctor_id == doctor_id,
            Waitlist.creado_en <= ahora,
        )
    )
    return [fila.id for fila in filas if peticion_eliminable(fila.creado_en, limite)]


async def _referencias(session: AsyncSession, ids: Sequence[uuid.UUID]) -> tuple[dict, dict]:
    """Citas y peticiones que quedan por ficha, en dos consultas agrupadas.

    Dos `count(*) group by` en vez de un `exists` por ficha porque esto corre para todos los
    consultorios del despliegue en una sola pasada, y también porque la cuenta tiene que ser la foto
    de DESPUÉS de los borrados: se llama ya eliminadas las filas.
    """
    if not ids:
        return {}, {}
    citas = await session.execute(
        select(Appointment.patient_id, func.count())
        .where(Appointment.patient_id.in_(ids))
        .group_by(Appointment.patient_id)
    )
    pedidas = await session.execute(
        select(Waitlist.patient_id, func.count())
        .where(Waitlist.patient_id.in_(ids))
        .group_by(Waitlist.patient_id)
    )
    return dict(citas.all()), dict(pedidas.all())


async def _fichas_a_borrar(
    session: AsyncSession, doctor_id: uuid.UUID, ahora: datetime, limite: datetime
) -> tuple[list[uuid.UUID], int]:
    """Las fichas antiguas sin nada detrás, y cuántas antiguas sí siguen en uso.

    La cuenta de referencias se hace cuando los `DELETE` de citas y peticiones ya están ejecutados
    dentro de esta transacción, así que solo ve las filas que quedan: "huérfana" significa huérfana
    al final de la pasada, no al principio.
    """
    candidatas = await session.execute(
        select(Patient.id, Patient.creado_en).where(
            Patient.doctor_id == doctor_id,
            Patient.creado_en <= ahora,
        )
    )
    antiguas = [(fila.id, fila.creado_en) for fila in candidatas if fila.creado_en <= limite]
    if not antiguas:
        return [], 0

    ids = [identificador for identificador, _ in antiguas]
    citas, pedidas = await _referencias(session, ids)
    se_van: list[uuid.UUID] = []
    en_uso = 0
    for identificador, creado_en in antiguas:
        if ficha_eliminable(
            creado_en,
            limite,
            citas_restantes=int(citas.get(identificador, 0)),
            peticiones_restantes=int(pedidas.get(identificador, 0)),
        ):
            se_van.append(identificador)
        else:
            en_uso += 1
    return se_van, en_uso


async def _borrar(session: AsyncSession, modelo, ids: Sequence[uuid.UUID]) -> int:
    """`DELETE ... where id in (...)` y cuántas filas se fueron de verdad.

    Se borra por id, no por la condición: lo decidió el dominio, y el `rowcount` es lo que PostgreSQL
    confirma. Si alguien cambió una fila entre la lectura y el borrado, el número lo dice.
    """
    if not ids:
        return 0
    resultado = await session.execute(
        delete(modelo).where(modelo.id.in_(ids)).execution_options(synchronize_session=False)
    )
    return int(resultado.rowcount or 0)


async def _tanda(
    session: AsyncSession,
    doctor_id: uuid.UUID,
    meses: int,
    ahora: datetime,
    resumen: Resumen,
    *,
    simular: bool,
) -> None:
    """El barrido de un consultorio, entero dentro de una transacción con su candado."""
    limite = umbral(ahora, meses)

    await session.rollback()
    async with session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:clave, 0))"),
            {"clave": f"limpieza:{doctor_id}"},
        )

        # Inventario que el médico va a leer en el log: citas pendientes cuyo día ya pasó. Se cuentan
        # antes porque el borrado no las toca; las otras dos cuentas se hacen sobre lo que queda.
        resumen.confirmadas_sin_cerrar += int(
            await session.scalar(
                select(func.count())
                .select_from(Appointment)
                .where(
                    Appointment.doctor_id == doctor_id,
                    Appointment.estado == UNIQUE_CONFIRMADA,
                    Appointment.fecha_hora < ahora,
                )
            )
            or 0
        )

        citas = await _citas_a_borrar(session, doctor_id, ahora, limite)
        pedidas = await _peticiones_a_borrar(session, doctor_id, ahora, limite)
        resumen.citas_eliminadas += await _borrar(session, Appointment, citas)
        resumen.peticiones_eliminadas += await _borrar(session, Waitlist, pedidas)

        fichas, en_uso = await _fichas_a_borrar(session, doctor_id, ahora, limite)
        resumen.fichas_eliminadas += await _borrar(session, Patient, fichas)
        resumen.fichas_en_uso += en_uso

        if simular:
            raise _Simulada


async def limpiar(
    session: AsyncSession,
    *,
    ahora: datetime | None = None,
    simular: bool = False,
) -> Resumen:
    """Una pasada del cron de retención, consultorio a consultorio."""
    momento = ahora or datetime.now(UTC)
    if momento.tzinfo is None:
        raise ValueError("La limpieza trabaja en UTC: `ahora` tiene que venir con huso")

    resumen = Resumen(simulada=simular)
    for doctor_id, meses in await _doctores(session):
        resumen.doctores += 1
        try:
            await _tanda(session, doctor_id, normalizar_meses(meses), momento, resumen, simular=simular)
        except _Simulada:
            continue  # el `async with` ya deshizo la transacción: las cuentas quedan, los datos también
        except IntegrityError:
            # Alguien reservó sobre una ficha que estábamos soltando. Se deshace su tanda entera y
            # la próxima pasada la volverá a intentar con la foto nueva.
            resumen.conflictos += 1

    return resumen
