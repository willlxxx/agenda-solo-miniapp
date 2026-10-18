"""Servicios: edición, validaciones y aislamiento entre doctores."""

from httpx import AsyncClient

from tests.conftest import DOCTOR_LOPEZ, nueva_sesion, registrar


async def test_ajustar_duracion_de_un_servicio(client: AsyncClient, doctor):
    servicios = (await client.get("/api/v1/services")).json()
    empaste = next(s for s in servicios if s["nombre"] == "Empaste")
    actualizado = await client.patch(f"/api/v1/services/{empaste['id']}", json={"duracion_minutos": 60})
    assert actualizado.status_code == 200
    assert actualizado.json()["duracion_minutos"] == 60


async def test_duracion_fuera_de_rango_es_rechazada(client: AsyncClient, doctor):
    servicios = (await client.get("/api/v1/services")).json()
    for minutos in (0, 4, 241, 999):
        resp = await client.patch(f"/api/v1/services/{servicios[0]['id']}", json={"duracion_minutos": minutos})
        assert resp.status_code == 422, f"duración {minutos} debería rechazarse"


async def test_color_invalido_es_rechazado(client: AsyncClient, doctor):
    servicios = (await client.get("/api/v1/services")).json()
    resp = await client.patch(f"/api/v1/services/{servicios[0]['id']}", json={"color": "azul"})
    assert resp.status_code == 422


async def test_patch_vacio_devuelve_400(client: AsyncClient, doctor):
    servicios = (await client.get("/api/v1/services")).json()
    assert (await client.patch(f"/api/v1/services/{servicios[0]['id']}", json={})).status_code == 400


async def test_desactivar_es_borrado_suave(client: AsyncClient, doctor):
    servicios = (await client.get("/api/v1/services")).json()
    objetivo = servicios[0]["id"]
    assert (await client.delete(f"/api/v1/services/{objetivo}")).status_code == 204
    restantes = (await client.get("/api/v1/services")).json()
    desactivado = next(s for s in restantes if s["id"] == objetivo)
    assert desactivado["activo"] is False


async def test_servicio_nuevo_ordenado_por_orden(client: AsyncClient, doctor):
    creado = await client.post("/api/v1/services", json={"nombre": "Ortodoncia", "duracion_minutos": 45, "orden": 99})
    assert creado.status_code == 201
    lista = (await client.get("/api/v1/services")).json()
    assert lista[-1]["nombre"] == "Ortodoncia"


async def test_un_doctor_no_ve_los_servicios_de_otro(client: AsyncClient, doctor):
    propios = (await client.get("/api/v1/services")).json()
    ajeno = nueva_sesion()
    await registrar(ajeno, DOCTOR_LOPEZ)
    ajenos = (await ajeno.get("/api/v1/services")).json()
    assert {s["id"] for s in ajenos}.isdisjoint({s["id"] for s in propios})
    # Y ni siquiera puede tocarlo por ID directo.
    assert (await ajeno.patch(f"/api/v1/services/{propios[0]['id']}", json={"nombre": "X"})).status_code == 404
    await ajeno.aclose()


async def test_sin_sesion_sin_acceso_a_servicios(client: AsyncClient):
    assert (await client.get("/api/v1/services")).status_code == 401
