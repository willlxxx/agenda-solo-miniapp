"""Agenda del doctor: lo que entra por el enlace público, pintado en su huso y con estado."""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from httpx import AsyncClient

from app.api import deps
from app.models import Appointment
from tests.conftest import DOCTOR_LOPEZ, nueva_sesion, registrar, sql_filas

MADRID = ZoneInfo("Europe/Madrid")
PACIENTE = {"nombre": "Ana Paciente", "telefono": "600111222", "email": "ana.paciente@example.com"}


async def servicio_id(client: AsyncClient, nombre: str = "Revisión") -> str:
    filas = await client.get("/api/v1/services")
    return next(f["id"] for f in filas.json() if f["nombre"] == nombre)


async def primer_hueco(client: AsyncClient, slug: str, servicio: str) -> tuple[str, str]:
    """El primer hueco real del horizonte: no hardcodeamos las franjas sembradas."""
    resp = await client.get(
        f"/api/v1/public/{slug}/disponibilidad", params={"servicio": servicio, "dias": 14}
    )
    assert resp.status_code == 200, resp.text
    for dia in resp.json()["dias"]:
        for hueco in dia["huecos"]:
            return dia["fecha"], hueco["inicio"][:5]
    raise AssertionError("El doctor no ofrece ningún hueco: han cambiado las franjas sembradas")


async def reservar(client: AsyncClient, slug: str, servicio: str, *, fecha: str | None = None, hora: str | None = None):
    if fecha is None or hora is None:
        fecha, hora = await primer_hueco(client, slug, servicio)
    cuerpo = {"servicio": servicio, "fecha": fecha, "hora": hora, **PACIENTE, "consentimiento": True}
    resp = await client.post(f"/api/v1/public/{slug}/citas", json=cuerpo)
    assert resp.status_code == 201, resp.text
    return fecha, hora


async def ver_agenda(client: AsyncClient, **params) -> dict:
    resp = await client.get("/api/v1/appointments", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def fin_de(hora: str, minutos: int = 30) -> str:
    """Hora local + duración congelada, para afirmar sobre el tramo pintado."""
    total = int(hora[:2]) * 60 + int(hora[3:5]) + minutos
    return f"{total // 60 % 24:02d}:{total % 60:02d}"


async def sembrar(
    doctor_id: str,
    *,
    inicio_utc: datetime,
    duracion: int = 30,
    estado: str = "confirmada",
    servicio: str | None = None,
    paciente: str | None = None,
) -> str:
    """Cita directa en BD, para fechas que la reserva pública ya no acepta (pasado, otro doctor)."""
    async with deps.SessionLocal() as session:
        cita = Appointment(
            doctor_id=uuid.UUID(str(doctor_id)),
            service_id=uuid.UUID(str(servicio)) if servicio else None,
            patient_id=uuid.UUID(str(paciente)) if paciente else None,
            fecha_hora=inicio_utc,
            duracion_minutos=duracion,
            estado=estado,
            consentimiento_ok_en=inicio_utc,
        )
        session.add(cita)
        await session.flush()
        await session.refresh(cita)
        await session.commit()
        return str(cita.id)


# --- Camino feliz: el paciente reserva, el doctor lo ve ---


async def test_la_cita_del_paciente_aparece_en_la_agenda(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    fecha, hora = await reservar(client, doctor["slug_unico"], servicio)

    cuerpo = await ver_agenda(client, desde=fecha, dias=1)
    assert cuerpo["total"] == 1 and cuerpo["confirmadas"] == 1
    cita = cuerpo["citas"][0]
    assert (cita["fecha"], cita["hora"][:5], cita["fin"][:5]) == (fecha, hora, fin_de(hora))
    assert cita["servicio"] == "Revisión"
    assert cita["paciente"] == {"nombre": "Ana Paciente", "telefono": "600111222", "email": "ana.paciente@example.com"}
    assert cita["estado"] == "confirmada"
    assert cita["color"] == "#2563eb"


async def test_una_ventana_de_un_dia_solo_muestra_ese_dia(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    fecha, _ = await reservar(client, doctor["slug_unico"], servicio)
    cuerpo = await ver_agenda(client, desde=fecha, dias=1)
    assert [c["fecha"] for c in cuerpo["citas"]] == [fecha]
    assert cuerpo["desde"] == fecha and cuerpo["hasta"] == fecha


async def test_sin_sesion_no_se_ve_nada(client: AsyncClient, doctor: dict):
    otra = nueva_sesion()
    resp = await otra.get("/api/v1/appointments")
    assert resp.status_code == 401


async def test_el_doctor_no_ve_las_citas_de_otro_doctor(client: AsyncClient, doctor: dict):
    otra = nueva_sesion()
    lopez = await registrar(otra, DOCTOR_LOPEZ)
    servicio = (await otra.get("/api/v1/services")).json()[0]["id"]
    fecha, _ = await reservar(otra, lopez["slug_unico"], servicio)

    assert (await ver_agenda(otra, desde=fecha, dias=1))["total"] == 1
    assert (await ver_agenda(client, desde=fecha, dias=1))["total"] == 0


# --- UTC dentro, hora local fuera ---


async def test_se_guarda_en_utc_y_se_pinta_en_el_huso_del_doctor(client: AsyncClient, doctor: dict):
    # Madrid está en +01 en noviembre: 08:00 UTC son las 09:00 de la consulta.
    id_ = await sembrar(doctor["id"], inicio_utc=datetime(2026, 11, 20, 8, 0, tzinfo=UTC))
    cita = (await ver_agenda(client, desde="2026-11-20", dias=1))["citas"][0]
    assert cita["id"] == id_
    assert cita["hora"][:5] == "09:00" and cita["fin"][:5] == "09:30"
    assert cita["inicio_utc"].startswith("2026-11-20T08:00")


async def test_una_cita_cerca_de_la_medianoche_sigue_en_su_dia_local(client: AsyncClient, doctor: dict):
    # 23:30 local del día 20: en UTC sería el día 21, y la agenda lo pinta el 20.
    inicio = datetime(2026, 11, 20, 23, 30, tzinfo=MADRID).astimezone(UTC)
    await sembrar(doctor["id"], inicio_utc=inicio)
    citas = (await ver_agenda(client, desde="2026-11-20", dias=1))["citas"]
    assert [c["fecha"] for c in citas] == ["2026-11-20"]
    assert citas[0]["fin"][:5] == "00:00"


async def test_el_orden_es_por_instante_real_no_por_la_hora_pintada(client: AsyncClient, doctor: dict):
    await sembrar(doctor["id"], inicio_utc=datetime(2026, 11, 23, 10, 0, tzinfo=UTC))
    await sembrar(doctor["id"], inicio_utc=datetime(2026, 11, 23, 8, 0, tzinfo=UTC))
    horas = [c["hora"][:5] for c in (await ver_agenda(client, desde="2026-11-23", dias=1))["citas"]]
    assert horas == ["09:00", "11:00"]


async def test_la_ventana_deja_fuera_lo_de_otro_dia(client: AsyncClient, doctor: dict):
    await sembrar(doctor["id"], inicio_utc=datetime(2026, 11, 20, 8, 0, tzinfo=UTC))
    await sembrar(doctor["id"], inicio_utc=datetime(2026, 11, 27, 8, 0, tzinfo=UTC))
    assert (await ver_agenda(client, desde="2026-11-20", dias=7))["total"] == 1
    assert (await ver_agenda(client, desde="2026-11-20", dias=8))["total"] == 2


# --- La ventana pedida ---


async def test_por_defecto_se_pide_la_semana_empezando_por_hoy(client: AsyncClient, doctor: dict):
    cuerpo = await ver_agenda(client)
    hoy = datetime.now(MADRID).date()
    assert cuerpo["desde"] == hoy.isoformat()
    assert cuerpo["timezone"] == "Europe/Madrid"
    assert datetime.fromisoformat(cuerpo["hasta"]).date() - hoy == timedelta(days=6)


async def test_dias_fuera_de_rango(client: AsyncClient, doctor: dict):
    for dias in (0, 31):
        resp = await client.get("/api/v1/appointments", params={"dias": dias})
        assert resp.status_code == 422, resp.text


async def test_no_se_pide_medio_ano_de_historico(client: AsyncClient, doctor: dict):
    hoy = datetime.now(MADRID).date()
    demasiado_atras = (hoy - timedelta(days=200)).isoformat()
    demasiado_adelante = (hoy + timedelta(days=400)).isoformat()
    assert (await client.get("/api/v1/appointments", params={"desde": demasiado_atras})).status_code == 422
    assert (await client.get("/api/v1/appointments", params={"desde": demasiado_adelante})).status_code == 422


# --- Estados ---


async def test_marcar_completada(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    fecha, _ = await reservar(client, doctor["slug_unico"], servicio)
    cita = (await ver_agenda(client, desde=fecha, dias=1))["citas"][0]

    resp = await client.patch(f"/api/v1/appointments/{cita['id']}", json={"estado": "completada"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "completada"
    # Sigue pintada igual: solo cambió el estado.
    assert resp.json()["hora"][:5] == cita["hora"][:5]
    filas = await sql_filas("select estado from appointments where id = cast(:i as uuid)", i=cita["id"])
    assert filas == [("completada",)]


async def test_no_vino_y_cancelada(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    fecha, _ = await reservar(client, doctor["slug_unico"], servicio)
    cita = (await ver_agenda(client, desde=fecha, dias=1))["citas"][0]
    for estado in ("no_vino", "cancelada"):
        resp = await client.patch(f"/api/v1/appointments/{cita['id']}", json={"estado": estado})
        assert resp.status_code == 200, resp.text
        assert resp.json()["estado"] == estado


async def test_completada_deja_constancia_de_la_ultima_visita(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    await reservar(client, doctor["slug_unico"], servicio)
    paciente = (await sql_filas("select id from patients where doctor_id = cast(:d as uuid)", d=doctor["id"]))[0][0]
    pasado = datetime.now(MADRID) - timedelta(minutes=90)
    id_ = await sembrar(doctor["id"], inicio_utc=pasado.astimezone(UTC), servicio=servicio, paciente=paciente)

    assert (await client.patch(f"/api/v1/appointments/{id_}", json={"estado": "completada"})).status_code == 200
    filas = await sql_filas(
        "select ultima_visita from patients where id = cast(:p as uuid)", p=paciente
    )
    assert filas == [(pasado.date(),)]


async def test_completada_futura_no_inventa_una_ultima_visita(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    fecha, _ = await reservar(client, doctor["slug_unico"], servicio)
    cita = (await ver_agenda(client, desde=fecha, dias=1))["citas"][0]
    assert (await client.patch(f"/api/v1/appointments/{cita['id']}", json={"estado": "completada"})).status_code == 200
    filas = await sql_filas("select ultima_visita from patients where doctor_id = cast(:d as uuid)", d=doctor["id"])
    assert filas[0][0] is None


async def test_cancelar_devuelve_el_hueco_a_la_disponibilidad(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    slug = doctor["slug_unico"]

    async def ofrece_hora() -> bool:
        resp = await client.get(
            f"/api/v1/public/{slug}/disponibilidad", params={"servicio": servicio, "dias": 14}
        )
        assert resp.status_code == 200, resp.text
        dias = [d for d in resp.json()["dias"] if d["fecha"] == fecha]
        return any(h["inicio"][:5] == hora for d in dias for h in d["huecos"])

    fecha, hora = await reservar(client, slug, servicio)
    assert not await ofrece_hora()  # reservada: ya no se ofrece

    cita = (await ver_agenda(client, desde=fecha, dias=1))["citas"][0]
    assert (await client.patch(f"/api/v1/appointments/{cita['id']}", json={"estado": "cancelada"})).status_code == 200
    assert await ofrece_hora()  # cancelada: el hueco vuelve a estar libre


async def test_reconfirmar_sobre_un_hueco_ya_ocupado_da_409(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    slug = doctor["slug_unico"]
    fecha, hora = await reservar(client, slug, servicio)
    original = (await ver_agenda(client, desde=fecha, dias=1))["citas"][0]

    # El doctor cancela, otro paciente ocupa el hueco, y la primera ya no puede volver.
    cancelar = await client.patch(f"/api/v1/appointments/{original['id']}", json={"estado": "cancelada"})
    assert cancelar.status_code == 200
    await reservar(client, slug, servicio, fecha=fecha, hora=hora)

    resp = await client.patch(f"/api/v1/appointments/{original['id']}", json={"estado": "confirmada"})
    assert resp.status_code == 409
    assert "otra cita" in resp.json()["detail"]


async def test_reconfirmar_un_hueco_libre_si_vale(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    fecha, _ = await reservar(client, doctor["slug_unico"], servicio)
    cita = (await ver_agenda(client, desde=fecha, dias=1))["citas"][0]
    assert (await client.patch(f"/api/v1/appointments/{cita['id']}", json={"estado": "cancelada"})).status_code == 200
    resp = await client.patch(f"/api/v1/appointments/{cita['id']}", json={"estado": "confirmada"})
    assert resp.status_code == 200 and resp.json()["estado"] == "confirmada"


async def test_un_estado_inventado_no_entrada(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    fecha, _ = await reservar(client, doctor["slug_unico"], servicio)
    cita = (await ver_agenda(client, desde=fecha, dias=1))["citas"][0]
    assert (await client.patch(f"/api/v1/appointments/{cita['id']}", json={"estado": "perpleja"})).status_code == 422


async def test_la_cita_de_otro_doctor_no_existe_para_mi(client: AsyncClient, doctor: dict):
    otra = nueva_sesion()
    lopez = await registrar(otra, DOCTOR_LOPEZ)
    servicio = (await otra.get("/api/v1/services")).json()[0]["id"]
    fecha, _ = await reservar(otra, lopez["slug_unico"], servicio)
    ajena = (await ver_agenda(otra, desde=fecha, dias=1))["citas"][0]["id"]

    resp = await client.patch(f"/api/v1/appointments/{ajena}", json={"estado": "cancelada"})
    assert resp.status_code == 404
    filas = await sql_filas("select estado from appointments where id = cast(:i as uuid)", i=ajena)
    assert filas == [("confirmada",)]


async def test_cita_inexistente_da_404(client: AsyncClient, doctor: dict):
    resp = await client.patch(f"/api/v1/appointments/{uuid.uuid4()}", json={"estado": "completada"})
    assert resp.status_code == 404


# --- Lo que NO sale por el hilo ---


async def test_la_agenda_no_tiene_ningun_campo_clinico(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    fecha, _ = await reservar(client, doctor["slug_unico"], servicio)
    cita = (await ver_agenda(client, desde=fecha, dias=1))["citas"][0]
    assert set(cita) == {
        "id",
        "inicio_utc",
        "fecha",
        "hora",
        "fin",
        "duracion_minutos",
        "estado",
        "servicio",
        "color",
        "paciente",
    }
    assert set(cita["paciente"]) == {"nombre", "telefono", "email"}


async def test_la_agenda_respeta_la_duracion_congelada(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    fecha, hora = await reservar(client, doctor["slug_unico"], servicio)
    await client.patch(f"/api/v1/services/{servicio}", json={"duracion_minutos": 60})

    cita = (await ver_agenda(client, desde=fecha, dias=1))["citas"][0]
    assert (cita["hora"][:5], cita["fin"][:5], cita["duracion_minutos"]) == (hora, fin_de(hora), 30)


async def test_sin_servicio_ni_paciente_la_agenda_no_revienta(client: AsyncClient, doctor: dict):
    await sembrar(doctor["id"], inicio_utc=datetime(2026, 11, 20, 8, 0, tzinfo=UTC))
    cita = (await ver_agenda(client, desde="2026-11-20", dias=1))["citas"][0]
    assert cita["servicio"] is None and cita["paciente"] is None
    assert cita["color"] == "#94a3b8"


async def test_cita_anulada_de_la_semana_sigue_visible_para_el_doctor(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    fecha, _ = await reservar(client, doctor["slug_unico"], servicio)
    cita = (await ver_agenda(client, desde=fecha, dias=1))["citas"][0]
    await client.patch(f"/api/v1/appointments/{cita['id']}", json={"estado": "cancelada"})

    cuerpo = await ver_agenda(client, desde=fecha, dias=1)
    assert cuerpo["total"] == 1 and cuerpo["confirmadas"] == 0
    assert cuerpo["citas"][0]["estado"] == "cancelada"
