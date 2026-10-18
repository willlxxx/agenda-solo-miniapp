"""Auth del doctor y onboarding mágico."""

from httpx import AsyncClient

from tests.conftest import DOCTOR_GARCIA, DOCTOR_LOPEZ, registrar


async def test_registro_crea_cuenta_con_slug_normalizado(client: AsyncClient):
    cuerpo = await registrar(client)
    assert cuerpo["slug_unico"] == "dra-garcia-pena"
    assert cuerpo["email"] == DOCTOR_GARCIA["email"]


async def test_registro_dispara_el_onboarding_magico(client: AsyncClient):
    await registrar(client)
    servicios = (await client.get("/api/v1/services")).json()
    horarios = (await client.get("/api/v1/availability")).json()
    # 5 servicios predefinidos y 10 franjas (L-V, mañana y tarde): el panel ya funciona solo.
    assert len(servicios) == 5
    assert {s["nombre"] for s in servicios} == {
        "Revisión",
        "Limpieza",
        "Empaste",
        "Urgencia",
        "Endodoncia inicial",
    }
    assert len(horarios) == 10


async def test_cookie_de_sesion_es_httponly_y_nunca_contiene_la_password(client: AsyncClient):
    resp = await client.post("/api/v1/auth/registro", json=DOCTOR_GARCIA)
    set_cookie = resp.headers["set-cookie"]
    assert "agenda_sesion=" in set_cookie
    assert "HttpOnly" in set_cookie and "SameSite" in set_cookie
    assert "password" not in resp.text


async def test_registro_duplicado_devuelve_409(client: AsyncClient):
    await registrar(client)
    resp = await client.post("/api/v1/auth/registro", json=DOCTOR_GARCIA)
    assert resp.status_code == 409


async def test_slug_repetido_recibe_sufijo(client: AsyncClient):
    await registrar(client)
    segundo = await registrar(client, {**DOCTOR_LOPEZ, "nombre": "Dra. García Peña"})
    assert segundo["slug_unico"] == "dra-garcia-pena-2"


async def test_password_demasiado_corta_es_rechazada(client: AsyncClient):
    resp = await client.post("/api/v1/auth/registro", json={**DOCTOR_GARCIA, "password": "abc"})
    assert resp.status_code == 422


async def test_yo_exige_sesion(client: AsyncClient):
    assert (await client.get("/api/v1/auth/yo")).status_code == 401


async def test_yo_devuelve_el_perfil_autenticado(client: AsyncClient, doctor):
    cuerpo = (await client.get("/api/v1/auth/yo")).json()
    assert cuerpo["id"] == doctor["id"]


async def test_token_falsificado_no_autentica(client: AsyncClient):
    client.cookies.set("agenda_sesion", "eyFalsificado.token.aqui")
    resp = await client.get("/api/v1/auth/yo")
    assert resp.status_code == 401


async def test_acceso_con_password_incorrecta_es_401(client: AsyncClient, doctor):
    resp = await client.post(
        "/api/v1/auth/acceso",
        json={"email": DOCTOR_GARCIA["email"], "password": "no-es-la-password"},
    )
    assert resp.status_code == 401


async def test_email_inexistente_y_password_incorrecta_dan_el_mismo_error(client: AsyncClient, doctor):
    inexistente = await client.post("/api/v1/auth/acceso", json={"email": "nadie@example.com", "password": "x"})
    incorrecta = await client.post(
        "/api/v1/auth/acceso",
        json={"email": DOCTOR_GARCIA["email"], "password": "equivocada"},
    )
    assert inexistente.status_code == incorrecta.status_code == 401
    assert inexistente.json()["detail"] == incorrecta.json()["detail"]


async def test_ciclo_completo_salida_y_reentrada(client: AsyncClient, doctor):
    await client.post("/api/v1/auth/salida")
    assert (await client.get("/api/v1/auth/yo")).status_code == 401
    acceso = await client.post(
        "/api/v1/auth/acceso",
        json={"email": DOCTOR_GARCIA["email"], "password": DOCTOR_GARCIA["password"]},
    )
    assert acceso.status_code == 200
    assert (await client.get("/api/v1/auth/yo")).status_code == 200


async def test_la_password_se_guarda_hasheada(client: AsyncClient, doctor):
    import uuid

    from app.api import deps
    from app.models import Doctor

    async with deps.SessionLocal() as session:
        almacen = await session.get(Doctor, uuid.UUID(doctor["id"]))
    assert almacen.password_hash.startswith("$argon2id$")
    assert DOCTOR_GARCIA["password"] not in almacen.password_hash
