"""Disponibilidad semanal: rangos válidos y sin solapamientos."""

from httpx import AsyncClient

from tests.conftest import DOCTOR_LOPEZ, nueva_sesion, registrar

LUNES = 0
FRANJA_MANANA = {"dia_semana": LUNES, "hora_inicio": "09:00", "hora_fin": "14:00"}


async def test_el_onboarding_deja_10_franjas_activas(client: AsyncClient, doctor):
    franjas = (await client.get("/api/v1/availability")).json()
    assert len(franjas) == 10
    assert all(f["activo"] for f in franjas)
    assert {f["dia_semana"] for f in franjas} == {0, 1, 2, 3, 4}


async def test_franja_valida_se_crea(client: AsyncClient, doctor):
    resp = await client.post(
        "/api/v1/availability", json={"dia_semana": 5, "hora_inicio": "09:00", "hora_fin": "13:00"}
    )
    assert resp.status_code == 201
    assert resp.json()["dia_semana"] == 5


async def test_fin_anterior_a_inicio_es_rechazada(client: AsyncClient, doctor):
    resp = await client.post(
        "/api/v1/availability", json={"dia_semana": LUNES, "hora_inicio": "14:00", "hora_fin": "09:00"}
    )
    assert resp.status_code == 422


async def test_fin_igual_a_inicio_es_rechazada(client: AsyncClient, doctor):
    resp = await client.post(
        "/api/v1/availability", json={"dia_semana": LUNES, "hora_inicio": "10:00", "hora_fin": "10:00"}
    )
    assert resp.status_code == 422


async def test_dia_semana_fuera_de_rango_es_rechazado(client: AsyncClient, doctor):
    for dia in (-1, 7):
        resp = await client.post(
            "/api/v1/availability", json={"dia_semana": dia, "hora_inicio": "09:00", "hora_fin": "10:00"}
        )
        assert resp.status_code == 422, f"día {dia} debería rechazarse"


async def test_solapamiento_partial_es_rechazado(client: AsyncClient, doctor):
    """El horario por defecto ya tiene 09:00-14:00 del lunes: 13:00-15:00 choca."""
    resp = await client.post(
        "/api/v1/availability", json={"dia_semana": LUNES, "hora_inicio": "13:00", "hora_fin": "15:00"}
    )
    assert resp.status_code == 409


async def test_contencion_total_es_rechazada(client: AsyncClient, doctor):
    resp = await client.post(
        "/api/v1/availability", json={"dia_semana": LUNES, "hora_inicio": "10:00", "hora_fin": "11:00"}
    )
    assert resp.status_code == 409


async def test_yunta_a_la_par_no_es_solapamiento(client: AsyncClient, doctor):
    """14:00-16:00 junto a 09:00-14:00 comparte borde pero no hora: es válido."""
    resp = await client.post(
        "/api/v1/availability", json={"dia_semana": LUNES, "hora_inicio": "14:00", "hora_fin": "16:00"}
    )
    assert resp.status_code == 201


async def test_la_misma_hora_en_otro_dia_no_choca(client: AsyncClient, doctor):
    """Sábado no está en el horario por defecto: la misma franja es aceptable."""
    resp = await client.post(
        "/api/v1/availability", json={"dia_semana": 5, "hora_inicio": "09:00", "hora_fin": "14:00"}
    )
    assert resp.status_code == 201


async def test_franja_inactiva_no_bloquea_el_hueco(client: AsyncClient, doctor):
    franjas = (await client.get("/api/v1/availability")).json()
    lunes_manana = next(f for f in franjas if f["dia_semana"] == LUNES and f["hora_inicio"] == "09:00:00")
    await client.patch(f"/api/v1/availability/{lunes_manana['id']}", json={"activo": False})
    resp = await client.post(
        "/api/v1/availability", json={"dia_semana": LUNES, "hora_inicio": "09:30", "hora_fin": "13:30"}
    )
    assert resp.status_code == 201


async def test_reactivar_una_franja_desactivada_vuelve_a_chocar(client: AsyncClient, doctor):
    """Desactivadas las dos franjas del lunes, 13:00-17:00 es válida; al reactivar
    la de 16:00-20:00 reaparece el solape y debe rechazarse."""
    franjas = (await client.get("/api/v1/availability")).json()
    lunes = [f for f in franjas if f["dia_semana"] == LUNES]
    tarde = next(f for f in lunes if f["hora_inicio"] == "16:00:00")
    for franja in lunes:
        assert (await client.patch(f"/api/v1/availability/{franja['id']}", json={"activo": False})).status_code == 200

    creada = await client.post(
        "/api/v1/availability", json={"dia_semana": LUNES, "hora_inicio": "13:00", "hora_fin": "17:00"}
    )
    assert creada.status_code == 201, creada.text

    reactivada = await client.patch(f"/api/v1/availability/{tarde['id']}", json={"activo": True})
    assert reactivada.status_code == 409


async def test_dos_franjas_con_la_misma_hora_de_inicio_se_rechazan(client: AsyncClient, doctor):
    """Aunque la primera esté inactiva: (doctor, día, inicio) es único en BD."""
    franjas = (await client.get("/api/v1/availability")).json()
    lunes_manana = next(f for f in franjas if f["dia_semana"] == LUNES and f["hora_inicio"] == "09:00:00")
    await client.patch(f"/api/v1/availability/{lunes_manana['id']}", json={"activo": False})
    resp = await client.post("/api/v1/availability", json=FRANJA_MANANA)
    assert resp.status_code == 409


async def test_patch_que_produce_solapamiento_es_rechazado(client: AsyncClient, doctor):
    franjas = (await client.get("/api/v1/availability")).json()
    tarde_lunes = next(f for f in franjas if f["dia_semana"] == LUNES and f["hora_inicio"] == "16:00:00")
    resp = await client.patch(f"/api/v1/availability/{tarde_lunes['id']}", json={"hora_inicio": "13:00"})
    assert resp.status_code == 409


async def test_patch_valido_se_aplica(client: AsyncClient, doctor):
    franjas = (await client.get("/api/v1/availability")).json()
    tarde_lunes = next(f for f in franjas if f["dia_semana"] == LUNES and f["hora_inicio"] == "16:00:00")
    resp = await client.patch(f"/api/v1/availability/{tarde_lunes['id']}", json={"hora_fin": "19:00"})
    assert resp.status_code == 200
    assert resp.json()["hora_fin"].startswith("19:00")


async def test_eliminar_franja(client: AsyncClient, doctor):
    franjas = (await client.get("/api/v1/availability")).json()
    objetivo = franjas[0]["id"]
    assert (await client.delete(f"/api/v1/availability/{objetivo}")).status_code == 204
    restantes = (await client.get("/api/v1/availability")).json()
    assert objetivo not in {f["id"] for f in restantes}
    assert len(restantes) == 9


async def test_un_doctor_no_ve_las_franjas_de_otro(client: AsyncClient, doctor):
    propias = (await client.get("/api/v1/availability")).json()
    ajeno = nueva_sesion()
    await registrar(ajeno, DOCTOR_LOPEZ)
    assert (await ajeno.delete(f"/api/v1/availability/{propias[0]['id']}")).status_code == 404
    ajenas = (await ajeno.get("/api/v1/availability")).json()
    assert {f["id"] for f in ajenas}.isdisjoint({f["id"] for f in propias})
    await ajeno.aclose()
