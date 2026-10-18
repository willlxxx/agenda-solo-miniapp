"""Bloqueo de días: vacaciones y festivos."""

from datetime import date, timedelta

from httpx import AsyncClient

from tests.conftest import DOCTOR_LOPEZ, nueva_sesion, registrar

FUTURO = (date.today() + timedelta(days=30)).isoformat()


async def test_bloquear_un_dia(client: AsyncClient, doctor):
    resp = await client.post("/api/v1/blocked-dates", json={"fecha": FUTURO, "motivo": "Vacaciones"})
    assert resp.status_code == 201
    assert resp.json()["motivo"] == "Vacaciones"


async def test_el_mismo_dia_no_se_bloquea_dos_veces(client: AsyncClient, doctor):
    await client.post("/api/v1/blocked-dates", json={"fecha": FUTURO})
    resp = await client.post("/api/v1/blocked-dates", json={"fecha": FUTURO})
    assert resp.status_code == 409


async def test_bloquear_el_pasado_es_rechazado(client: AsyncClient, doctor):
    pasado = (date.today() - timedelta(days=1)).isoformat()
    resp = await client.post("/api/v1/blocked-dates", json={"fecha": pasado})
    assert resp.status_code == 422


async def test_fecha_con_formato_invalido_es_rechazada(client: AsyncClient, doctor):
    resp = await client.post("/api/v1/blocked-dates", json={"fecha": "31-12-2026"})
    assert resp.status_code == 422


async def test_la_lista_viene_ordenada_por_fecha(client: AsyncClient, doctor):
    hoy = date.today()
    for delta in (45, 12, 30):
        await client.post("/api/v1/blocked-dates", json={"fecha": (hoy + timedelta(days=delta)).isoformat()})
    fechas = [b["fecha"] for b in (await client.get("/api/v1/blocked-dates")).json()]
    assert fechas == sorted(fechas)


async def test_desbloquear_un_dia(client: AsyncClient, doctor):
    creado = await client.post("/api/v1/blocked-dates", json={"fecha": FUTURO, "motivo": "Festivo"})
    bloqueado = creado.json()["id"]
    assert (await client.delete(f"/api/v1/blocked-dates/{bloqueado}")).status_code == 204
    assert (await client.get("/api/v1/blocked-dates")).json() == []


async def test_un_doctor_no_desbloquea_el_dia_de_otro(client: AsyncClient, doctor):
    creado = await client.post("/api/v1/blocked-dates", json={"fecha": FUTURO})
    ajeno = nueva_sesion()
    await registrar(ajeno, DOCTOR_LOPEZ)
    assert (await ajeno.delete(f"/api/v1/blocked-dates/{creado.json()['id']}")).status_code == 404
    assert len((await client.get("/api/v1/blocked-dates")).json()) == 1
    await ajeno.aclose()


async def test_sin_sesion_no_se_ven_los_dias_bloqueados(client: AsyncClient):
    assert (await client.get("/api/v1/blocked-dates")).status_code == 401
