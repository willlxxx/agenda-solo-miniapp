"""Barrido interno de retención: lo lanza el cron, no el navegador.

Misma forma que `/internal/recordatorios` y por las mismas razones: el proceso se reinicia cuando
quiere Railway, así que la única pasada fiable es la que llama alguien de fuera con un secreto. Y es
además la ruta más peligrosa de la API —borra datos de salud con nombre y teléfono—, de modo que
aquí el apagado por defecto no es una cortesía: sin `CRON_SECRET` la ruta devuelve 503 y no borra.

Idempotencia dos veces: la segunda pasada del mismo minuto ya no encuentra nada elegible, y el
parámetro `simular` corre los mismos `DELETE` bajo el mismo candado y deshace la transacción. Eso es
lo que permite mirar antes de saltar: el cron puede lanzar una pasada de ensayo y el médico leer las
cuentas exactas sin haber perdido una sola fila.

No sale en `/docs`, y el payload no lleva ni un nombre: solo contadores.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import comprobar_secreto_cron, get_ahora, get_session
from app.schemas import ResumenLimpieza
from app.services.limpieza import limpiar

router = APIRouter(prefix="/internal", tags=["internal"])

APAGADO = "La limpieza de retención está apagada: define CRON_SECRET en el backend"


@router.post("/limpieza", response_model=ResumenLimpieza, include_in_schema=False)
async def ejecutar(
    session: AsyncSession = Depends(get_session),
    ahora: datetime = Depends(get_ahora),
    simular: bool = Query(default=False, description="Haz la pasada y deshazla: muestra las cuentas reales"),
    x_cron_secret: str | None = Header(default=None, alias="x-cron-secret"),
) -> ResumenLimpieza:
    """Una pasada de retención sobre todos los consultorios, con contadores como única salida.

    El cron la llama con `?simular=true` la primera vez de cada despliegue para que el médico vea
    cuántas filas cerradas tiene fuera de su plazo antes de dejarla borrar.
    """
    comprobar_secreto_cron(x_cron_secret, apagado=APAGADO)
    return ResumenLimpieza.model_validate(await limpiar(session, ahora=ahora, simular=simular))
