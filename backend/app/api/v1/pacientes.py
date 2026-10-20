"""La ficha del paciente: verla, llevársela y suprimirla.

Solo lectura y una escritura irreversible, y las dos cosas para el médico autenticado sobre sus
propios datos. La entrada natural no es un censo de pacientes —la agenda no lleva el id del
paciente en su payload, y así sigue— sino la cita: `/de-cita/{id}` devuelve la ficha de quien está
en esa fila.

`/datos` es el artículo 15 y 20 en forma de archivo, y `DELETE` es el 17. El borrado no toca las
citas de fondo: suelta la identidad, así que el consultorio conserva la constancia de lo atendido y
el hueco deja de estar ocupado por un nombre.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_doctor, get_session, zona_del
from app.domain.borrado import confirmacion, nombre_de_archivo
from app.models import Doctor
from app.schemas import FichaPaciente, Supresion
from app.services import pacientes

router = APIRouter(prefix="/pacientes", tags=["pacientes"])


def _pintar(ficha: pacientes.Ficha, zona: str) -> FichaPaciente:
    """Ficha del servicio + el texto de consecuencia, para que el panel no reinvente la frase."""
    return FichaPaciente(
        id=ficha.id,
        nombre=ficha.nombre,
        telefono=ficha.telefono,
        email=ficha.email,
        ultima_visita=ficha.ultima_visita,
        creado_en=ficha.creado_en,
        timezone=zona,
        citas=ficha.citas,
        peticiones=ficha.peticiones,
        aviso=confirmacion(ficha.nombre, len(ficha.citas), ficha.por_llegar, len(ficha.peticiones)),
    )


@router.get("/de-cita/{cita_id}", response_model=FichaPaciente)
async def ficha_de_una_cita(
    cita_id: uuid.UUID,
    doctor: Doctor = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_session),
):
    """Quién está detrás de esta fila de la agenda, con todas sus citas y peticiones."""
    try:
        ficha = await pacientes.desde_cita(session, doctor.id, cita_id, zona_del(doctor))
    except pacientes.ErrorFicha as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from exc
    return _pintar(ficha, doctor.timezone)


@router.get("/{paciente_id}/datos")
async def descargar_datos(
    paciente_id: uuid.UUID,
    doctor: Doctor = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_session),
):
    """Los datos de esa persona, en un JSON que se descarga con nombre legible.

    Sale como `attachment` a propósito: el médico se lo puede pasar al paciente tal cual, y el
    archivo queda fechado (`generado_el`) por si alguien discute qué se entregó y cuándo.
    """
    try:
        ficha = await pacientes.leer(session, doctor.id, paciente_id, zona_del(doctor))
    except pacientes.ErrorFicha as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from exc
    return JSONResponse(
        content=pacientes.export(ficha, doctor, doctor.timezone),
        headers={"Content-Disposition": f'attachment; filename="{nombre_de_archivo(ficha.nombre)}"'},
    )


@router.delete("/{paciente_id}", response_model=Supresion)
async def suprimir_ficha(
    paciente_id: uuid.UUID,
    doctor: Doctor = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_session),
):
    """Suprime la ficha y suelta la identidad de sus citas y peticiones. Devuelve las cuentas.

    No hay vuelta atrás y no hay segundo borrado que hacer: si el paciente vuelve a reservar, se
    crea una ficha nueva, y las citas que ya tenía siguen ocupando su hueco sin decir quién era.
    """
    try:
        resultado = await pacientes.suprimir(session, doctor.id, paciente_id)
    except pacientes.ErrorFicha as exc:
        code = status.HTTP_404_NOT_FOUND if isinstance(exc, pacientes.FichaInexistente) else status.HTTP_409_CONFLICT
        raise HTTPException(code, detail=exc.detail) from exc
    return Supresion.model_validate(resultado)
