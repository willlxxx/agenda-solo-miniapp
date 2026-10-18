"""Endpoints públicos del enlace /d/{slug}: lectura sin sesión sobre PostgreSQL real."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from httpx import AsyncClient

from tests.conftest import DOCTOR_GARCIA, DOCTOR_LOPEZ, insertar_cita, nueva_sesion, registrar


def proximo_lunes() -> date:
    """Siempre un lunes estrictamente futuro: el motor no recorta el día por reloj."""
    dia = date.today() + timedelta(days=1)
    while dia.weekday() != 0:
        dia += timedelta(days=1)
    return dia


async def id_servicio(client: AsyncClient, nombre: str) -> str:
    filas = await client.get("/api/v1/services")
    return next(f["id"] for f in filas.json() if f["nombre"] == nombre)


async def huecos_de(client: AsyncClient, slug: str, servicio: str, **params) -> dict:
    consulta = {"servicio": servicio, "desde": proximo_lunes().isoformat(), "dias": 1, **params}
    resp = await client.get(f"/api/v1/public/{slug}/disponibilidad", params=consulta)
    assert resp.status_code == 200, resp.text
    return resp.json()


def inicios(dia: dict) -> list[str]:
    """La API devuelve hora ISO con segundos ("09:00:00"); en los tests leemos "09:00".

    La conversión vive aquí, no en el backend: cambiar el serializado ahora rompería años de ISO.
    """
    return [h["inicio"][:5] for h in dia["huecos"]]


def tramo(hueco: dict) -> tuple[str, str]:
    return (hueco["inicio"][:5], hueco["fin"][:5])


# --- Ficha pública ---


async def test_ficha_sin_sesion_con_los_servicios_activos(client: AsyncClient, doctor: dict):
    suelto = nueva_sesion()  # el navegador del paciente: sin cookie
    resp = await suelto.get(f"/api/v1/public/{doctor['slug_unico']}")
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["doctor"]["nombre"] == DOCTOR_GARCIA["nombre"]
    assert [s["nombre"] for s in cuerpo["servicios"]] == [
        "Revisión",
        "Limpieza",
        "Empaste",
        "Urgencia",
        "Endodoncia inicial",
    ]
    # Minimización: el enlace público no enseña el email del doctor.
    assert "email" not in cuerpo["doctor"]
    await suelto.aclose()


async def test_ficha_de_un_slug_inexistente_es_404(client: AsyncClient):
    resp = await client.get("/api/v1/public/no-existe-nadie")
    assert resp.status_code == 404


async def test_el_slug_no_distingue_mayusculas(client: AsyncClient, doctor: dict):
    resp = await client.get(f"/api/v1/public/{doctor['slug_unico'].upper()}")
    assert resp.status_code == 200


async def test_servicio_desactivado_sale_de_la_ficha(client: AsyncClient, doctor: dict):
    await client.delete(f"/api/v1/services/{await id_servicio(client, 'Urgencia')}")
    cuerpo = (await client.get(f"/api/v1/public/{doctor['slug_unico']}")).json()
    assert "Urgencia" not in [s["nombre"] for s in cuerpo["servicios"]]


# --- Motor a través de la API ---


async def test_lunes_completo_de_revision(client: AsyncClient, doctor: dict):
    cuerpo = await huecos_de(client, doctor["slug_unico"], await id_servicio(client, "Revisión"))
    dia = cuerpo["dias"][0]
    assert dia["fecha"] == proximo_lunes().isoformat()
    assert len(dia["huecos"]) == 34  # 9:00-13:30 + 16:00-19:30
    assert tramo(dia["huecos"][0]) == ("09:00", "09:30")
    assert inicios(dia)[-1] == "19:30"


async def test_un_servicio_mas_largo_estrecha_la_rejilla(client: AsyncClient, doctor: dict):
    cuerpo = await huecos_de(client, doctor["slug_unico"], await id_servicio(client, "Endodoncia inicial"))
    huecos = cuerpo["dias"][0]["huecos"]
    assert len(huecos) == 30  # 9:00-13:00 + 16:00-19:00
    assert "13:30" not in inicios(cuerpo["dias"][0])  # no caben los 60 min tras esa hora


async def test_el_paso_de_la_rejilla_es_configurable(client: AsyncClient, doctor: dict):
    cuerpo = await huecos_de(client, doctor["slug_unico"], await id_servicio(client, "Revisión"), paso=30)
    assert cuerpo["paso_minutos"] == 30
    assert inicios(cuerpo["dias"][0])[:3] == ["09:00", "09:30", "10:00"]


async def test_dia_bloqueado_no_ofrece_nada(client: AsyncClient, doctor: dict):
    lunes = proximo_lunes()
    resp = await client.post("/api/v1/blocked-dates", json={"fecha": lunes.isoformat(), "motivo": "puente"})
    assert resp.status_code == 201
    cuerpo = await huecos_de(client, doctor["slug_unico"], await id_servicio(client, "Revisión"))
    assert cuerpo["dias"] == []


async def test_sin_franjas_de_ese_dia_no_hay_dias(client: AsyncClient, doctor: dict):
    lunes = proximo_lunes().weekday()
    franjas = (await client.get("/api/v1/availability")).json()
    for franja in franjas:
        if franja["dia_semana"] == lunes:
            assert (await client.delete(f"/api/v1/availability/{franja['id']}")).status_code == 204
    cuerpo = await huecos_de(client, doctor["slug_unico"], await id_servicio(client, "Revisión"))
    assert cuerpo["dias"] == []


async def test_una_cita_confirmada_tapa_su_hora(client: AsyncClient, doctor: dict):
    lunes = proximo_lunes()
    diez_madrid = datetime.combine(lunes, time(10, 0), tzinfo=ZoneInfo("Europe/Madrid")).astimezone(UTC)
    await insertar_cita(doctor["id"], inicio_utc=diez_madrid, duracion=30)
    dia = (await huecos_de(client, doctor["slug_unico"], await id_servicio(client, "Revisión")))["dias"][0]
    assert "10:00" not in inicios(dia)
    assert "09:30" in inicios(dia) and "10:30" in inicios(dia)
    # La cita de 10:00-10:30 se lleva también el hueco de 9:45, que la pisaría.
    assert len(dia["huecos"]) == 31


async def test_una_cita_cancelada_libera_la_hora(client: AsyncClient, doctor: dict):
    lunes = proximo_lunes()
    diez_madrid = datetime.combine(lunes, time(10, 0), tzinfo=ZoneInfo("Europe/Madrid")).astimezone(UTC)
    await insertar_cita(doctor["id"], inicio_utc=diez_madrid, duracion=30, estado="cancelada")
    dia = (await huecos_de(client, doctor["slug_unico"], await id_servicio(client, "Revisión")))["dias"][0]
    assert "10:00" in inicios(dia)


async def test_las_citas_de_otro_doctor_no_tapan_este_enlace(client: AsyncClient, doctor: dict):
    lunes = proximo_lunes()
    diez_madrid = datetime.combine(lunes, time(10, 0), tzinfo=ZoneInfo("Europe/Madrid")).astimezone(UTC)
    revision = await id_servicio(client, "Revisión")  # aún con la cookie de García
    otro = await registrar(client, DOCTOR_LOPEZ)
    await insertar_cita(otro["id"], inicio_utc=diez_madrid, duracion=30)
    dia = (await huecos_de(client, doctor["slug_unico"], revision))["dias"][0]
    assert len(dia["huecos"]) == 34


async def test_el_huso_horario_del_doctor_manda(client: AsyncClient):
    """Cita a las 12:00 UTC = 09:00 en Buenos Aires: la rejilla local es la que se tapa."""
    datos = {**DOCTOR_GARCIA, "email": "ba@example.com", "timezone": "America/Argentina/Buenos_Aires"}
    doctor = await registrar(client, datos)
    lunes = proximo_lunes()
    once_utc = datetime.combine(lunes, time(12, 0), tzinfo=UTC)
    await insertar_cita(doctor["id"], inicio_utc=once_utc, duracion=30)
    servicios = (await client.get(f"/api/v1/public/{doctor['slug_unico']}")).json()["servicios"]
    revision = next(s["id"] for s in servicios if s["nombre"] == "Revisión")
    dia = (await huecos_de(client, doctor["slug_unico"], revision))["dias"][0]
    assert "09:00" not in inicios(dia)
    assert "09:30" in inicios(dia)
    assert tramo(dia["huecos"][0]) == ("09:30", "10:00")


# --- Errores y fronteras ---


async def test_servicio_de_otro_doctor_es_404(client: AsyncClient, doctor: dict):
    await registrar(client, DOCTOR_LOPEZ)
    ajeno = await id_servicio(client, "Revisión")
    suelto = nueva_sesion()
    resp = await suelto.get(
        f"/api/v1/public/{doctor['slug_unico']}/disponibilidad",
        params={"servicio": str(uuid.uuid4())},
    )
    assert resp.status_code == 404
    resp = await suelto.get(
        f"/api/v1/public/{doctor['slug_unico']}/disponibilidad",
        params={"servicio": ajeno},
    )
    assert resp.status_code == 404
    await suelto.aclose()


async def test_servicio_desactivado_no_se_puede_consultar(client: AsyncClient, doctor: dict):
    urgente = await id_servicio(client, "Urgencia")
    await client.delete(f"/api/v1/services/{urgente}")
    resp = await client.get(f"/api/v1/public/{doctor['slug_unico']}/disponibilidad", params={"servicio": urgente})
    assert resp.status_code == 404


async def test_el_cambio_de_horario_no_descoloca_las_horas(client: AsyncClient, doctor: dict):
    """26-oct-2026: Madrid ya está en invierno (+01). La cita sigue tapando sus 10:00 locales."""
    lunes_invierno = date(2026, 10, 26)
    diez_utc = datetime.combine(lunes_invierno, time(10, 0), tzinfo=ZoneInfo("Europe/Madrid")).astimezone(UTC)
    assert diez_utc.hour == 9  # una hora antes que en verano
    await insertar_cita(doctor["id"], inicio_utc=diez_utc, duracion=30)
    dia = (await huecos_de(client, doctor["slug_unico"], await id_servicio(client, "Revisión"), desde=lunes_invierno))[
        "dias"
    ][0]
    assert "10:00" not in inicios(dia) and "11:00" in inicios(dia)
    assert len(dia["huecos"]) == 31


async def test_consultar_el_pasado_es_422(client: AsyncClient, doctor: dict):
    resp = await client.get(
        f"/api/v1/public/{doctor['slug_unico']}/disponibilidad",
        params={"servicio": await id_servicio(client, "Revisión"), "desde": "2020-01-06", "dias": 1},
    )
    assert resp.status_code == 422


async def test_horizonte_demasiado_largo_es_422(client: AsyncClient, doctor: dict):
    resp = await client.get(
        f"/api/v1/public/{doctor['slug_unico']}/disponibilidad",
        params={"servicio": await id_servicio(client, "Revisión"), "dias": 365},
    )
    assert resp.status_code == 422


async def test_el_payload_no_fuga_citas_ni_pacientes(client: AsyncClient, doctor: dict):
    cuerpo = await huecos_de(client, doctor["slug_unico"], await id_servicio(client, "Revisión"))
    assert set(cuerpo) == {"doctor", "servicio", "paso_minutos", "anticipacion_minutos", "dias"}
    assert set(cuerpo["doctor"]) == {"nombre", "slug_unico", "timezone"}
