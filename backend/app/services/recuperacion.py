"""Solicitar y consumir un enlace de recuperación: lo que escribe en la base y manda el correo.

La forma del enlace, su caducidad y el texto viven en `app/domain/recuperacion.py`; aquí solo hay base
de datos y proveedor, que es lo que no se puede probar sin levantar Postgres.

Las reglas que importan, y por qué están así:

- **Una cuenta que no existe no deja rastro.** Si el email no es de ningún consultorio no se crea
  fila ni se manda nada, y el endpoint responde lo mismo que si sí existiera. Contar la diferencia
  sería publicar qué correos tienen una agenda montada.
- **Un pedido cada minuto por consultorio.** Dos clicks seguidos son casi siempre "no me ha llegado",
  y cada click escribe una fila y manda un correo.
- **Las filas gastadas se limpian solas.** Antes de crear un token se borran los usados y caducados de
  ese mismo consultorio, así que la tabla no crece dependiente de un cron de limpieza que todavía no
  corre.
- **Un fallo del proveedor no se convierte en promesa.** Si la entrega falla, la fila queda con el
  motivo en `error_envio` para el log de operaciones, y la respuesta pública sigue siendo la misma:
  no se le dice a quien pregunta "ese email no pudo recibir el enlace", porque ya sería una respuesta
  distinta según si la cuenta existe.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.recuperacion import (
    CADUCIDAD,
    demasiado_pronto,
    enlace_de_recuperacion,
    hash_del_token,
    mensaje_recuperacion,
    nuevo_token,
    sigue_vigente,
)
from app.models import Doctor, PasswordReset
from app.security import hash_password
from app.services.correo import Correo, ErrorEnvio, Proveedor

log = logging.getLogger("agenda.recuperacion")

# El motivo del envío se guarda corto a propósito: un proveedor caído puede devolver un texto largo.
MAX_ERROR_ENVIO = 500


@dataclass(frozen=True)
class Solicitud:
    """Lo que pasó de verdad. Es para el log: la respuesta HTTP no devuelve nada de esto."""

    cuenta_existe: bool
    correo_mandado: bool
    motivo: str


async def _ultimo_pedido(session: AsyncSession, doctor_id: uuid.UUID) -> datetime | None:
    return await session.scalar(select(func.max(PasswordReset.creado_en)).where(PasswordReset.doctor_id == doctor_id))


async def solicitar(
    session: AsyncSession,
    proveedor: Proveedor,
    *,
    origen: str,
    email: str,
    ahora: datetime,
) -> Solicitud:
    """Generar el enlace y entregarlo por el canal que esté configurado.

    `origen` entra por parámetro y no se lee de `get_settings()` dentro: el enlace va a un correo que
    el médico abre desde otro aparato, así que su base tiene que ser el dominio público del despliegue
    y eso es una decisión del endpoint, no de este fichero.
    """
    doctor = await session.scalar(select(Doctor).where(Doctor.email == email))
    if doctor is None:
        return Solicitud(cuenta_existe=False, correo_mandado=False, motivo="sin_cuenta")

    ultimo = await _ultimo_pedido(session, doctor.id)
    if ultimo is not None and demasiado_pronto(ultimo, ahora):
        return Solicitud(cuenta_existe=True, correo_mandado=False, motivo="pedido_hace_un_momento")

    await session.execute(
        delete(PasswordReset).where(
            PasswordReset.doctor_id == doctor.id,
            or_(PasswordReset.usado_en.is_not(None), PasswordReset.expira_en <= ahora),
        )
    )

    token = nuevo_token()
    # `creado_en` se escribe con el reloj del request y no se deja en el `now()` del servidor: el
    # cooldown se calcula comparando esta columna con `ahora`, y si cada una mirase un reloj distinto
    # el intervalo de un minuto dependería del desfase entre las dos máquinas.
    fila = PasswordReset(
        doctor_id=doctor.id,
        token_hash=hash_del_token(token),
        creado_en=ahora,
        expira_en=ahora + CADUCIDAD,
    )
    session.add(fila)
    await session.flush()

    enlace = enlace_de_recuperacion(origen, token)
    asunto, cuerpo = mensaje_recuperacion(doctor.nombre, enlace)
    try:
        await proveedor.enviar(Correo(para=doctor.email, asunto=asunto, cuerpo=cuerpo))
    except ErrorEnvio as exc:
        fila.error_envio = str(exc)[:MAX_ERROR_ENVIO]
        await session.commit()
        log.warning("recuperación: el proveedor %s no pudo entregar el enlace", proveedor.nombre)
        return Solicitud(cuenta_existe=True, correo_mandado=False, motivo="fallo_envio")

    await session.commit()
    # El canal simulado solo llega hasta aquí en desarrollo: el endpoint lo corta antes en producción.
    # Sin esta línea no se podría probar el flujo entero sin credenciales de nadie.
    if proveedor.nombre != "smtp":
        log.warning("recuperación: canal %s, el enlace NO salió por correo: %s", proveedor.nombre, enlace)
    else:
        log.info("recuperación: enlace mandado por smtp a la cuenta que lo pidió")
    return Solicitud(cuenta_existe=True, correo_mandado=True, motivo="mandado")


async def confirmar(session: AsyncSession, *, token: str, nueva: str, ahora: datetime) -> bool:
    """Gastar el enlace: escribir la clave nueva y dejar inservibles los demás tokens del consultorio.

    Devuelve `False` en los tres casos que no se distinguen entre sí (no existe, caducó, ya se usó)
    porque la diferencia le diría a quien prueba tokens al azar cómo va su intento. Con 32 bytes
    aleatorios y 30 minutos de vida no es un ataque real, pero tampoco hay por qué ayudar.
    """
    fila = await session.scalar(select(PasswordReset).where(PasswordReset.token_hash == hash_del_token(token)))
    if fila is None or not sigue_vigente(fila.expira_en, fila.usado_en, ahora):
        return False

    doctor = await session.get(Doctor, fila.doctor_id)
    if doctor is None:
        return False

    doctor.password_hash = hash_password(nueva)
    fila.usado_en = ahora
    await session.execute(
        delete(PasswordReset).where(PasswordReset.doctor_id == doctor.id, PasswordReset.id != fila.id)
    )
    await session.commit()
    return True
