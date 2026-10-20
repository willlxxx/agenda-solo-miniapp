"""La retención del consultorio: cuántos meses guarda el médico sus citas cerradas.

Es la única pantalla donde el médico decide un plazo de borrado, así que la respuesta lleva también
la frase que explica qué hace ese número (`app/domain/retencion.py`): si el panel escribiera su
propio texto, en cuanto cambiamos una regla de la limpieza la promesa del panel quedaría mintiendo.

La banda 6-12 la valida el dominio, no solo este fichero: el valor puede llegar de una migración o
de un `UPDATE` a mano, y la pasada que lo usa es irreversible.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_doctor, get_session
from app.domain.retencion import explicacion, normalizar_meses
from app.models import Doctor
from app.schemas import RetencionIn, RetencionPublic

router = APIRouter(prefix="/retencion", tags=["retencion"])


def _leer(doctor: Doctor) -> RetencionPublic:
    meses = normalizar_meses(doctor.retencion_meses)
    return RetencionPublic(meses=meses, explicacion=explicacion(meses))


@router.get("", response_model=RetencionPublic)
async def leer(doctor: Doctor = Depends(get_current_doctor)) -> RetencionPublic:
    """El plazo guardado y su explicación, para mostrarlo antes de tocarlo."""
    return _leer(doctor)


@router.put("", response_model=RetencionPublic)
async def guardar(
    datos: RetencionIn,
    doctor: Doctor = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_session),
) -> RetencionPublic:
    """Cambia el plazo. No borra nada en el momento: lo decide la próxima pasada del cron.

    Bajarlo de 12 a 6 no elimina las citas de golpe, y eso conviene decirlo: el médico puede probar
    un número y volver atrás sin consecuencia hasta que el cron corra.
    """
    doctor.retencion_meses = normalizar_meses(datos.meses)
    await session.commit()
    return _leer(doctor)
