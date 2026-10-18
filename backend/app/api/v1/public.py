"""Enlace público de reservas /d/{slug}: se lee la disponibilidad y se reserva, sin sesión.

El GET no bloquea nada; el POST es lo único que escribe y lo hace dentro de una
transacción con `pg_advisory_xact_lock` por doctor, revalidando el hueco con el MISMO
motor que usó el paciente para verlo. Así lectura y escritura no pueden divergir.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.domain.disponibilidad import CitaLocal, Franja, calcular_disponibilidad
from app.models import (
    UNIQUE_CONFIRMADA,
    UNIQUE_EN_ESPERA,
    Appointment,
    Availability,
    BlockedDate,
    Doctor,
    Patient,
    Service,
    Waitlist,
)
from app.schemas import (
    CitaIn,
    CitaPublica,
    DiaDisponiblePublic,
    DisponibilidadPublic,
    DoctorResumen,
    FichaPublica,
    HuecoPublic,
    ListaDeEsperaIn,
    ListaDeEsperaPublica,
    ServicePublic,
)

router = APIRouter(prefix="/public", tags=["public"])

MAX_DIAS = 60
HUECOS_POR_DIA = 60
PASO_MINUTOS = 15
# Margen para que el paciente pueda pulsar "hoy a las X" sin reservar en la hora en curso.
ANTICIPACION_MINUTOS = 30


def _zona(doctor: Doctor) -> ZoneInfo:
    try:
        return ZoneInfo(doctor.timezone)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"El huso horario guardado ({doctor.timezone}) no es válido",
        ) from exc


async def _doctor_por_slug(session: AsyncSession, slug: str) -> Doctor:
    # slug_unico es CITEXT: la comparación ya es insensible a mayúsculas.
    doctor = await session.scalar(select(Doctor).where(Doctor.slug_unico == slug))
    if doctor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Enlace no válido")
    return doctor


async def _servicio_del_doctor(session: AsyncSession, doctor: Doctor, servicio_id: uuid.UUID) -> Service:
    fila = await session.get(Service, servicio_id)
    if fila is None or fila.doctor_id != doctor.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Enlace no válido")
    return fila


async def _insumos(session: AsyncSession, doctor_id: uuid.UUID, zona: ZoneInfo, fechas: list[date]):
    """Franjas activas, citas confirmadas y días bloqueados del horizonte pedido."""
    franjas = [
        Franja(dia_semana=f.dia_semana, hora_inicio=f.hora_inicio, hora_fin=f.hora_fin)
        for f in await session.scalars(
            select(Availability).where(Availability.doctor_id == doctor_id, Availability.activo.is_(True))
        )
    ]
    # Colchón de un día a cada lado de la ventana UTC: cubre cualquier desfase horaria (±14 h).
    inicio_consulta = datetime.combine(fechas[0], time.min, tzinfo=UTC) - timedelta(days=1)
    fin_consulta = datetime.combine(fechas[-1] + timedelta(days=1), time.min, tzinfo=UTC) + timedelta(days=1)
    citas = [
        CitaLocal(inicio=c.fecha_hora.astimezone(zona).replace(tzinfo=None), duracion_minutos=c.duracion_minutos)
        for c in await session.scalars(
            select(Appointment).where(
                Appointment.doctor_id == doctor_id,
                Appointment.estado == UNIQUE_CONFIRMADA,
                Appointment.fecha_hora >= inicio_consulta,
                Appointment.fecha_hora < fin_consulta,
            )
        )
    ]
    bloqueados = set(
        await session.scalars(
            select(BlockedDate.fecha).where(
                BlockedDate.doctor_id == doctor_id,
                BlockedDate.fecha >= fechas[0],
                BlockedDate.fecha <= fechas[-1],
            )
        )
    )
    return franjas, citas, bloqueados


@router.get("/{slug}", response_model=FichaPublica)
async def ficha(slug: str, session: AsyncSession = Depends(get_session)) -> dict:
    """Lo que necesita la página para pintar cabecera y elegir servicio."""
    doctor = await _doctor_por_slug(session, slug)
    filas = await session.scalars(
        select(Service)
        .where(Service.doctor_id == doctor.id, Service.activo.is_(True))
        .order_by(Service.orden, Service.nombre)
    )
    return {"doctor": doctor, "servicios": list(filas)}


@router.get("/{slug}/disponibilidad", response_model=DisponibilidadPublic)
async def disponibilidad(
    slug: str,
    servicio: uuid.UUID = Query(description="Id de un servicio activo de este doctor"),
    dias: int = Query(default=14, ge=1, le=MAX_DIAS),
    paso: int = Query(default=PASO_MINUTOS, ge=5, le=60, description="Rejilla de horas ofrecidas, en minutos"),
    anticipacion: int = Query(
        default=ANTICIPACION_MINUTOS, ge=0, le=720, description="Margen mínimo para reservar hoy"
    ),
    desde: date | None = Query(default=None, description="Primer día del horizonte (default: hoy)"),
    session: AsyncSession = Depends(get_session),
):
    doctor = await _doctor_por_slug(session, slug)
    zona = _zona(doctor)
    ahora = datetime.now(zona).replace(tzinfo=None)
    fila = await _servicio_del_doctor(session, doctor, servicio)
    if not fila.activo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ese servicio no está disponible en este enlace")

    primer_dia = desde or ahora.date()
    if primer_dia < ahora.date():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="No se puede consultar el pasado")
    fechas = [primer_dia + timedelta(days=i) for i in range(dias)]

    franjas, citas, bloqueados = await _insumos(session, doctor.id, zona, fechas)
    dias_calculados = calcular_disponibilidad(
        franjas=franjas,
        citas=citas,
        bloqueados=bloqueados,
        duracion_minutos=fila.duracion_minutos,
        fechas=fechas,
        ahora=ahora,
        paso_minutos=paso,
        anticipacion_minutos=anticipacion,
        max_huecos_por_dia=HUECOS_POR_DIA,
    )

    return DisponibilidadPublic(
        doctor=DoctorResumen.model_validate(doctor),
        servicio=ServicePublic.model_validate(fila),
        paso_minutos=paso,
        anticipacion_minutos=anticipacion,
        desde=fechas[0],
        hasta=fechas[-1],
        dias=[
            DiaDisponiblePublic(
                fecha=d.fecha,
                huecos=[HuecoPublic(inicio=h.inicio, fin=h.fin) for h in d.huecos],
            )
            for d in dias_calculados
        ],
    )


@router.post("/{slug}/citas", response_model=CitaPublica, status_code=status.HTTP_201_CREATED)
async def reservar(slug: str, datos: CitaIn, session: AsyncSession = Depends(get_session)):
    """Reserva con bloqueo por doctor: dos pacientes sobre el mismo hueco, uno gana."""
    if not datos.consentimiento:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Necesitamos tu consentimiento expreso para guardar la cita",
        )

    doctor = await _doctor_por_slug(session, slug)
    zona = _zona(doctor)
    ahora = datetime.now(zona).replace(tzinfo=None)
    fila = await _servicio_del_doctor(session, doctor, datos.servicio)
    if not fila.activo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ese servicio no está disponible en este enlace")

    if datos.fecha < ahora.date():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Ese día ya pasó")
    if (datos.fecha - ahora.date()).days > MAX_DIAS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Solo se reserva con {MAX_DIAS} días de antelación"
        )

    # fold=0: en la única hora repetida del cambio de horario (domingo de octubre) nos quedamos
    # con la primera de las dos, que es la que ofreció el motor.
    inicio_local = datetime.combine(datos.fecha, datos.hora, tzinfo=zona)
    inicio_utc = inicio_local.astimezone(UTC)

    # El rollback expira los objetos ya cargados: nos quedamos con los escalares antes de bloquear.
    doctor_id = doctor.id
    nombre_doctor = doctor.nombre
    servicio_id = fila.id
    nombre_servicio = fila.nombre
    duracion_servicio = fila.duracion_minutos

    # Cerramos la tx implícita de las lecturas de arriba para abrir la nuestra con el advisory lock.
    await session.rollback()
    async with session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:clave, 0))"),
            {"clave": f"agenda-cita:{doctor_id}"},
        )
        # Revalidación DENTRO del lock: lo que el paciente vio puede haber cambiado mientras escribía.
        franjas, citas, bloqueados = await _insumos(session, doctor_id, zona, [datos.fecha])
        ofrecidos = calcular_disponibilidad(
            franjas=franjas,
            citas=citas,
            bloqueados=bloqueados,
            duracion_minutos=duracion_servicio,
            fechas=[datos.fecha],
            ahora=ahora,
            paso_minutos=PASO_MINUTOS,
            anticipacion_minutos=ANTICIPACION_MINUTOS,
        )
        if not ofrecidos or all(h.inicio != datos.hora for h in ofrecidos[0].huecos):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="Ese hueco ya no está libre. Elige otra hora.",
            )

        paciente = await session.scalar(
            select(Patient).where(
                Patient.doctor_id == doctor_id,
                Patient.nombre == datos.nombre,
                Patient.telefono == datos.telefono,
            )
        )
        if paciente is None:
            paciente = Patient(
                doctor_id=doctor_id,
                nombre=datos.nombre,
                telefono=datos.telefono,
                email=str(datos.email) if datos.email else None,
            )
            session.add(paciente)
            await session.flush()

        cita = Appointment(
            doctor_id=doctor_id,
            service_id=servicio_id,
            patient_id=paciente.id,
            fecha_hora=inicio_utc,
            # Congelamos la duración: si mañana el doctor cambia el servicio, esta cita no se reescribe.
            duracion_minutos=duracion_servicio,
            estado=UNIQUE_CONFIRMADA,
            # El consentimiento se prueba en el momento de la reserva, no en la hora de la cita.
            consentimiento_ok_en=datetime.now(UTC),
        )
        session.add(cita)

    fin = inicio_local + timedelta(minutes=duracion_servicio)
    return CitaPublica(
        id=cita.id,
        fecha=datos.fecha,
        hora=datos.hora,
        fin=fin.time(),
        duracion_minutos=duracion_servicio,
        servicio=nombre_servicio,
        doctor=nombre_doctor,
    )


@router.post("/{slug}/lista-espera", response_model=ListaDeEsperaPublica, status_code=status.HTTP_201_CREATED)
async def apuntar_lista_de_espera(slug: str, datos: ListaDeEsperaIn, session: AsyncSession = Depends(get_session)):
    """El hueco no existe: apuntamos la demanda y el doctor llama cuando lo libera.

    Es la única escritura pública además de la reserva, así que usa su propio advisory lock por
    doctor (`lista-espera:`), distinto del de citas: dos pacientes entrando en la lista no pelean
    por un hueco, solo pelean por no duplicarse.
    """
    if not datos.consentimiento:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Necesitamos tu consentimiento expreso para guardar tus datos",
        )
    contacto = (datos.telefono or "").strip() or (str(datos.email) if datos.email else None)
    if contacto is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Sin teléfono ni email no podemos avisarte cuando quede un hueco",
        )

    doctor = await _doctor_por_slug(session, slug)
    zona = _zona(doctor)
    ahora = datetime.now(zona).replace(tzinfo=None)
    fila = await _servicio_del_doctor(session, doctor, datos.servicio)
    if not fila.activo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ese servicio no está disponible en este enlace")

    if datos.fecha is not None:
        if datos.fecha < ahora.date():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Ese día ya pasó")
        if (datos.fecha - ahora.date()).days > MAX_DIAS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Solo apuntamos peticiones con {MAX_DIAS} días de antelación",
            )

    # Escalares antes del rollback: la tx implícita de las lecturas invalida los objetos cargados.
    doctor_id = doctor.id
    nombre_doctor = doctor.nombre
    servicio_id = fila.id
    nombre_servicio = fila.nombre

    await session.rollback()
    async with session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:clave, 0))"),
            {"clave": f"lista-espera:{doctor_id}"},
        )
        # Misma identidad que usa la reserva (nombre + teléfono dentro del doctor), así un paciente
        # que ya fue apuntado no crea una ficha paralela en la agenda del doctor.
        telefono = (datos.telefono or "").strip()
        correo = str(datos.email).lower() if datos.email else None
        paciente = await _paciente_de_lista(session, doctor_id, datos.nombre, telefono, correo)
        if paciente is None:
            paciente = Patient(
                doctor_id=doctor_id,
                nombre=datos.nombre,
                telefono=telefono or None,
                # Guardamos el email ya en minúsculas: es la forma en la que lo buscamos arriba.
                email=correo,
            )
            session.add(paciente)
            await session.flush()

        # Misma clave que el UNIQUE de la tabla, así un duplicado nunca llega a insertar y revienta
        # en un 500: si la fila ya existe (pendiente o ya avisada) devolvemos esa fila.
        condicion_fecha = Waitlist.fecha.is_(None) if datos.fecha is None else Waitlist.fecha == datos.fecha
        existente = await session.scalar(
            select(Waitlist).where(
                Waitlist.doctor_id == doctor_id,
                Waitlist.patient_id == paciente.id,
                condicion_fecha,
            )
        )
        if existente is not None:
            # Recargar la página no debe inflar la lista del doctor: devolvemos su fila original.
            return ListaDeEsperaPublica(
                id=existente.id,
                doctor=nombre_doctor,
                servicio=nombre_servicio,
                fecha=existente.fecha,
                preferencia=existente.preferencia_horaria or "cualquier_hora",
                estado=existente.estado,
                creado=False,
            )

        entrada = Waitlist(
            doctor_id=doctor_id,
            service_id=servicio_id,
            patient_id=paciente.id,
            fecha=datos.fecha,
            preferencia_horaria=datos.preferencia,
            estado=UNIQUE_EN_ESPERA,
        )
        session.add(entrada)

    return ListaDeEsperaPublica(
        id=entrada.id,
        doctor=nombre_doctor,
        servicio=nombre_servicio,
        fecha=datos.fecha,
        preferencia=datos.preferencia,
        estado=UNIQUE_EN_ESPERA,
        creado=True,
    )


async def _paciente_de_lista(
    session: AsyncSession,
    doctor_id: uuid.UUID,
    nombre: str,
    telefono: str,
    correo: str | None,
) -> Patient | None:
    """Busca la ficha del paciente con la identidad que traía la petición.

    La unicalidad de `patients` es (doctor, nombre, teléfono), y PG no deduplica NULL: si llega
    solo email buscamos por email, si no la lista crearía una ficha nueva en cada intento.
    """
    if telefono:
        return await session.scalar(
            select(Patient).where(
                Patient.doctor_id == doctor_id, Patient.nombre == nombre, Patient.telefono == telefono
            )
        )
    if correo:
        return await session.scalar(
            select(Patient).where(
                Patient.doctor_id == doctor_id,
                Patient.nombre == nombre,
                # lower() en los dos lados: una reserva antigua pudo guardar el email tal como se
                # escribió, y si no lo igualamos así crearíamos una ficha paralela por una mayúscula.
                func.lower(Patient.email) == correo,
                Patient.telefono.is_(None),
            )
        )
    return await session.scalar(
        select(Patient).where(Patient.doctor_id == doctor_id, Patient.nombre == nombre, Patient.telefono.is_(None))
    )
