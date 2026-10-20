"""Despacho interno de recordatorios: lo llama un cron externo con un secreto, no el navegador.

Por qué un endpoint en vez de un scheduler dentro del proceso: Railway reinita el contenedor cuando
quiere y un planificador en memoria pierde las pasadas y se duplica entre instancias. Un `curl`
cada pocos minutos desde fuera es idempotente porque las banderas están en la base de datos, así
que da igual que el cron se salte un tick, se lance dos veces o caiga el contenedor a mitad.

El endpoint está **apagado sin `CRON_SECRET`**: responde 503 en vez de quedar abierto. Es una ruta
que escribe en la agenda y envía correos a pacientes; dejarla sin secreto sería la puerta de un
spammer que además podría marcar avisos como enviados.

Tampoco sale en `/docs`: no es un contrato público, y la propia existencia de la ruta ya es
información útil para quien la quiera probar.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import comprobar_secreto_cron, get_ahora, get_proveedor_correo, get_session
from app.schemas import ResumenRecordatorios
from app.services.correo import Proveedor
from app.services.despacho import despachar

router = APIRouter(prefix="/internal", tags=["internal"])

APAGADO = "El despacho de recordatorios está apagado: define CRON_SECRET en el backend"


@router.post("/recordatorios", response_model=ResumenRecordatorios, include_in_schema=False)
async def ejecutar(
    session: AsyncSession = Depends(get_session),
    proveedor: Proveedor = Depends(get_proveedor_correo),
    ahora: datetime = Depends(get_ahora),
    x_cron_secret: str | None = Header(default=None, alias="x-cron-secret"),
) -> ResumenRecordatorios:
    """Una pasada: manda los avisos que tocan ahora y devuelve contadores, nunca datos del paciente.

    Se puede llamar a mano tantas veces como se quiera: lo que ya se envió está marcado y no se
    repite. Es lo que hace utilisable el cron como herramienta de soporte ("avísame otra vez, que
    no llegó") sin riesgo de hostigar a nadie.
    """
    comprobar_secreto_cron(x_cron_secret, apagado=APAGADO)
    return ResumenRecordatorios.model_validate(await despachar(session, proveedor, ahora=ahora))
