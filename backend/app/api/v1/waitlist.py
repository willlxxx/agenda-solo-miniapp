"""Lista de espera del doctor: la demanda que aún no tiene hueco, y a quién ya se le puede avisar.

Lee y gestiona solo las filas de su propio doctor. El estado no reserva nada: `contactada`
significa «ya le he llamado», y borrar la fila es la vía de borrado que le podemos ofrecer al
paciente si pide que no le guardemos la petición.

`/encajes` cruza lo pendiente con la disponibilidad real de hoy y redacta el aviso; el enlace de
WhatsApp o correo lo abre el médico desde su propia cuenta, así que no hay ningún tercero que
mande mensajes por él. Cuando el paciente acepta, `/reservar` escribe la cita con el MISMO núcleo
que usa el enlace público: mismo advisory lock por doctor y misma revalidación del hueco.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_doctor, get_current_doctor_id, get_session, zona_del
from app.domain.disponibilidad import DiaDisponible, calcular_disponibilidad
from app.domain.encaje import Peticion, enlace_correo, enlace_whatsapp, huecos_de, mensaje_de_aviso
from app.models import ORIGEN_PANEL, UNIQUE_EN_ESPERA, Doctor, Patient, Service, Waitlist
from app.schemas import (
    AvisoHueco,
    AvisosHueco,
    EntradaLista,
    EstadoListaIn,
    ListaEspera,
    ReservaAsistida,
    ReservaAsistidaIn,
)
from app.services.reserva import (
    ANTICIPACION_MINUTOS,
    HUECOS_POR_DIA,
    MAX_DIAS,
    PASO_MINUTOS,
    Destino,
    DiaFueraDeHorizonte,
    ErrorReserva,
    PacienteFueraDeSitio,
    insumos,
    reservar_hueco,
)

router = APIRouter(prefix="/waitlist", tags=["waitlist"])

# Un enlace público compartido en una mala tarde puede llenar la lista; el panel no necesita más.
MAX_ENTRADAS = 200
ESTADO_CONTACTADA = "contactada"
# Horizonte con el que buscamos hueco para quien espera: tres semanas es lo que un paciente
# acepta de espera en una revisioncita, y mantiene el cálculo barato.
DIAS_DE_AVISO = 21


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
        avisado_utc=entrada.avisado_en,
        hueco_ofrecido_utc=entrada.hueco_ofrecido,
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
    if datos.estado == ESTADO_CONTACTADA:
        # El aviso manual también es un aviso: queda constancia de cuándo, y la fila deja de
        # salir en `/encajes`. Si ya tenía fecha, respetamos la primera (no queremos reescribir
        # el histórico del médico que corrige el estado dos veces).
        fila.avisado_en = fila.avisado_en or datetime.now(UTC)
    else:
        # Reabrirla (el paciente dijo que ese día no, o quiere otro hueco) la devuelve a la cola
        # sin rastro del aviso anterior: si no, el panel la seguiría contando como avisada.
        fila.avisado_en = None
        fila.hueco_ofrecido = None
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


@router.get("/encajes", response_model=AvisosHueco)
async def huecos_para_avisar(
    dias: int = Query(default=DIAS_DE_AVISO, ge=1, le=MAX_DIAS),
    doctor: Doctor = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_session),
):
    """A quién de la lista se le puede ofrecer ya un hueco, con el aviso escrito y sus enlaces.

    Es lectura: no reserva nada y no toca la disponibilidad. El hueco se busca con el mismo motor
    y las mismas reglas con las que después se reserva, así que lo que el médico ve aquí es
    exactamente lo que puede escribir. Si dos personas esperan lo mismo, la primera se lleva el
    hueco y la segunda recibe el siguiente: un hueco no se ofrece dos veces.
    """
    zona = zona_del(doctor)
    ahora = datetime.now(zona).replace(tzinfo=None)
    fechas = [ahora.date() + timedelta(days=i) for i in range(dias)]
    franjas, citas, bloqueados = await insumos(session, doctor.id, zona, fechas)

    # Solo peticiones con paciente y servicio vivos: sin uno de los dos no hay a quién escribir
    # ni duración con la que calcular el hueco.
    filas = (
        await session.execute(
            select(
                Waitlist,
                Patient.nombre,
                Patient.telefono,
                Patient.email,
                Service.id,
                Service.nombre,
                Service.duracion_minutos,
            )
            .join(Patient, Patient.id == Waitlist.patient_id)
            .join(Service, Service.id == Waitlist.service_id)
            .where(
                Waitlist.doctor_id == doctor.id,
                Waitlist.estado == UNIQUE_EN_ESPERA,
                Service.activo.is_(True),
            )
            .order_by(Waitlist.creado_en)
            .limit(MAX_ENTRADAS)
        )
    ).all()

    # La disponibilidad depende de la duración del servicio, así que se calcula una vez por servicio
    # y se reutiliza entre las peticiones que lo piden.
    cada_servicio: dict[uuid.UUID, Sequence[DiaDisponible]] = {}
    peticiones: list[Peticion] = []
    for entrada, nombre, telefono, email, servicio_id, nombre_servicio, duracion in filas:
        if servicio_id not in cada_servicio:
            cada_servicio[servicio_id] = calcular_disponibilidad(
                franjas=franjas,
                citas=citas,
                bloqueados=bloqueados,
                duracion_minutos=duracion,
                fechas=fechas,
                ahora=ahora,
                paso_minutos=PASO_MINUTOS,
                anticipacion_minutos=ANTICIPACION_MINUTOS,
                max_huecos_por_dia=HUECOS_POR_DIA,
            )
        peticiones.append(
            Peticion(
                entrada_id=entrada.id,
                paciente_id=entrada.patient_id,
                nombre=nombre,
                telefono=telefono,
                email=email,
                servicio_id=servicio_id,
                servicio=nombre_servicio,
                fecha=entrada.fecha,
                preferencia=entrada.preferencia_horaria or "cualquier_hora",
            )
        )

    encajes = {e.entrada_id: e for e in huecos_de(cada_servicio, peticiones)}
    avisos: list[AvisoHueco] = []
    for peticion in peticiones:
        encaje = encajes.get(peticion.entrada_id)
        if encaje is None:
            continue
        mensaje = mensaje_de_aviso(peticion, encaje, doctor=doctor.nombre)
        avisos.append(
            AvisoHueco(
                id=peticion.entrada_id,
                nombre=peticion.nombre,
                telefono=peticion.telefono,
                email=peticion.email,
                servicio=peticion.servicio,
                pidio_fecha=peticion.fecha,
                preferencia=peticion.preferencia,
                fecha=encaje.fecha,
                inicio=encaje.inicio,
                fin=encaje.fin,
                mensaje=mensaje,
                whatsapp=enlace_whatsapp(peticion.telefono, mensaje),
                correo=enlace_correo(peticion.email, mensaje, f"Hueco libre en la agenda de {doctor.nombre}"),
            )
        )

    return AvisosHueco(
        timezone=doctor.timezone,
        desde=fechas[0],
        hasta=fechas[-1],
        pendientes=len(peticiones),
        con_hueco=len(avisos),
        avisos=avisos,
    )


@router.post("/{entrada_id}/reservar", response_model=ReservaAsistida, status_code=status.HTTP_201_CREATED)
async def reservar_por_el_doctor(
    entrada_id: uuid.UUID,
    datos: ReservaAsistidaIn,
    doctor: Doctor = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_session),
):
    """El médico guarda el hueco que acaba de ofrecer, con los datos que el paciente ya dejó.

    Escribe por el núcleo de `app/services/reserva.py`: el mismo `pg_advisory_xact_lock` por doctor
    y la misma revalidación que la reserva autoclictada, así que si otro paciente ocupó el hueco
    mientras se escribía el mensaje, esto devuelve 409 en lugar de dejar dos citas encima.
    """
    fila = await _fila(session, doctor.id, entrada_id)
    if fila.patient_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Esta petición no tiene paciente: reserva desde la agenda",
        )
    servicio = await session.get(Service, fila.service_id) if fila.service_id else None
    if servicio is None or servicio.doctor_id != doctor.id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Esta petición no tiene servicio: reserva desde la agenda",
        )

    # Escalares antes de llamar: el núcleo cierra esta transacción y expira los objetos cargados,
    # y a partir de aquí no podemos tocar ni `doctor` ni `fila` sin volver a leerlos.
    doctor_id = doctor.id
    paciente_id = fila.patient_id
    zona = zona_del(doctor)
    destino = Destino(
        doctor_id=doctor.id,
        nombre_doctor=doctor.nombre,
        servicio_id=servicio.id,
        nombre_servicio=servicio.nombre,
        duracion_minutos=servicio.duracion_minutos,
        zona=zona,
    )
    try:
        hecha = await reservar_hueco(
            session,
            destino=destino,
            fecha=datos.fecha,
            hora=datos.hora,
            paciente=paciente_id,
            # No la clicó el paciente: la escribió el médico attestando que se lo pidió.
            origen=ORIGEN_PANEL,
        )
    except DiaFueraDeHorizonte as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.detail) from exc
    except PacienteFueraDeSitio as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from exc
    except ErrorReserva as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=exc.detail) from exc

    # El núcleo hizo rollback y commit: hay que volver a por la fila para cerrarla.
    fila = await _fila(session, doctor_id, entrada_id)
    fila.estado = ESTADO_CONTACTADA
    # `avisado_en` es cuándo se escribió el aviso; si el médico avisó antes de reservar, ese
    # instante es más honesto que ahora, así que no lo pisamos.
    fila.avisado_en = fila.avisado_en or datetime.now(UTC)
    fila.hueco_ofrecido = hecha.inicio_utc
    await session.commit()
    await session.refresh(fila)

    paciente = await session.get(Patient, paciente_id)
    return ReservaAsistida(
        id=hecha.id,
        fecha=hecha.fecha,
        hora=hecha.hora,
        fin=hecha.fin,
        duracion_minutos=hecha.duracion_minutos,
        servicio=hecha.servicio,
        doctor=hecha.doctor,
        paciente=paciente.nombre if paciente else "Petición sin nombre",
        entrada=_leer(
            fila,
            paciente.nombre if paciente else None,
            paciente.telefono if paciente else None,
            paciente.email if paciente else None,
            destino.nombre_servicio,
        ),
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
