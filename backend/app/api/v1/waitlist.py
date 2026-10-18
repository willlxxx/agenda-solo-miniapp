"""Lista de espera del doctor: la demanda que aún no tiene hueco.

Lee y gestiona solo las filas de su propio doctor. El estado no reserva nada: `contactada`
significa «ya le he llamado», y borrar la fila es la vía de borrado que le podemos ofrecer al
paciente si pide que no le guardemos la petición.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_doctor_id, get_session
from app.models import UNIQUE_EN_ESPERA, Patient, Service, Waitlist
from app.schemas import EntradaLista, EstadoListaIn, ListaEspera

router = APIRouter(prefix="/waitlist", tags=["waitlist"])

# Un enlace público compartido en una mala tarde puede llenar la lista; el panel no necesita más.
MAX_ENTRADAS = 200
ESTADO_CONTACTADA = "contactada"


def _leer(
    entrada: Waitlist,
    nombre: str | None,
    telefono: str | None,
    email: str | None,
    servicio: str | None,
) -> EntradaLista:
    """Fila + contacto -> lo que pinta el panel.

    Si el paciente ya no está (le borraron la ficha) la petición se sigue mostrando sin identidad:
    saber qué servicio y qué día pidió sigue siendo útil.
    """
    return EntradaLista(
        id=entrada.id,
        nombre=nombre or "Petición sin nombre",
        telefono=telefono,
        email=email,
        servicio=servicio,
        fecha=entrada.fecha,
        preferencia=entrada.preferencia_horaria,
        estado=entrada.estado,
        # El instante real manda para el orden; el panel lo traduce al huso del doctor.
        pedido_utc=entrada.creado_en,
    )


async def _fila(session: AsyncSession, doctor_id: uuid.UUID, entrada_id: uuid.UUID) -> Waitlist:
    fila = await session.get(Waitlist, entrada_id)
    if fila is None or fila.doctor_id != doctor_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Petición no encontrada")
    return fila


@router.get("", response_model=ListaEspera)
async def listar(
    filtro: str = Query(default="pendientes", pattern="^(pendientes|contactadas|todas)$"),
    doctor_id: uuid.UUID = Depends(get_current_doctor_id),
    session: AsyncSession = Depends(get_session),
):
    consulta = (
        select(Waitlist, Patient.nombre, Patient.telefono, Patient.email, Service.nombre)
        .outerjoin(Patient, Patient.id == Waitlist.patient_id)
        .outerjoin(Service, Service.id == Waitlist.service_id)
        .where(Waitlist.doctor_id == doctor_id)
    )
    if filtro == "pendientes":
        consulta = consulta.where(Waitlist.estado == UNIQUE_EN_ESPERA)
    elif filtro == "contactadas":
        consulta = consulta.where(Waitlist.estado == ESTADO_CONTACTADA)
    # Primero lo pendiente y, dentro de cada bloque, por orden de llegada: es una lista de espera.
    consulta = consulta.order_by(Waitlist.estado != UNIQUE_EN_ESPERA, Waitlist.creado_en).limit(MAX_ENTRADAS)
    filas = (await session.execute(consulta)).all()

    entradas = [_leer(*fila) for fila in filas]
    return ListaEspera(
        total=len(entradas),
        en_espera=sum(1 for e in entradas if e.estado == UNIQUE_EN_ESPERA),
        contactadas=sum(1 for e in entradas if e.estado == ESTADO_CONTACTADA),
        filtro=filtro,
        entradas=entradas,
    )


@router.patch("/{entrada_id}", response_model=EntradaLista)
async def cambiar_estado(
    entrada_id: uuid.UUID,
    datos: EstadoListaIn,
    doctor_id: uuid.UUID = Depends(get_current_doctor_id),
    session: AsyncSession = Depends(get_session),
):
    fila = await _fila(session, doctor_id, entrada_id)
    fila.estado = datos.estado
    await session.commit()
    await session.refresh(fila)
    paciente = await session.get(Patient, fila.patient_id) if fila.patient_id else None
    servicio = await session.get(Service, fila.service_id) if fila.service_id else None
    return _leer(
        fila,
        paciente.nombre if paciente else None,
        paciente.telefono if paciente else None,
        paciente.email if paciente else None,
        servicio.nombre if servicio else None,
    )


@router.delete("/{entrada_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar(
    entrada_id: uuid.UUID,
    doctor_id: uuid.UUID = Depends(get_current_doctor_id),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Borra la petición. La ficha del paciente se queda: puede tener citas vivas que la necesiten."""
    fila = await _fila(session, doctor_id, entrada_id)
    await session.delete(fila)
    await session.commit()
