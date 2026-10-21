"""Editar la ficha del consultorio y cambiar la clave con la sesión ya abierta.

Lo que se prueba aquí es la parte que no se puede deshacer en silencio: el email con el que se entra,
el enlace que tienen los pacientes apuntado y el huso que decide a qué hora se lee cada cita. Un
`PATCH` que aceptase cualquiera de esas tres cosas mal no devuelve un error, deja una agenda que
nadie entiende.
"""

from httpx import AsyncClient

from tests.conftest import DOCTOR_GARCIA, DOCTOR_LOPEZ, registrar


async def _volver_con_garcia(client: AsyncClient) -> None:
    """La segunda inscripción deja su propia cookie: para probar colisiones hay que volver a la primera.

    Sin esto, el `PATCH` posterior editaría la cuenta de Lopez y las pruebas darían por bueno un 200
    que llegó a otro consultorio.
    """
    acceso = await client.post(
        "/api/v1/auth/acceso",
        json={"email": DOCTOR_GARCIA["email"], "password": DOCTOR_GARCIA["password"]},
    )
    assert acceso.status_code == 200


async def _dos_cuentas(client: AsyncClient) -> None:
    await registrar(client, DOCTOR_GARCIA)
    await registrar(client, DOCTOR_LOPEZ)
    await _volver_con_garcia(client)


# --- Editar la ficha ---


async def test_sin_sesion_no_se_edita_nada(client: AsyncClient):
    assert (await client.patch("/api/v1/auth/yo", json={"nombre": "Otro"})).status_code == 401


async def test_lo_editado_vuelve_en_yo_y_se_queda(client: AsyncClient, doctor):
    resp = await client.patch(
        "/api/v1/auth/yo",
        json={"nombre": "Dra. Elena Ruiz Hoya", "telefono": "+52 55 1111 2222"},
    )
    assert resp.status_code == 200
    assert resp.json()["nombre"] == "Dra. Elena Ruiz Hoya"
    # La cabecera del panel y la página pública leen de `/auth/yo`: si no se hubiera escrito, aquí se ve.
    assert (await client.get("/api/v1/auth/yo")).json()["telefono"] == "+52 55 1111 2222"


async def test_sin_campos_no_hay_nada_que_guardar(client: AsyncClient, doctor):
    resp = await client.patch("/api/v1/auth/yo", json={})
    assert resp.status_code == 400


async def test_el_telefono_se_puede_vaciar(client: AsyncClient, doctor):
    await client.patch("/api/v1/auth/yo", json={"telefono": "+34 600 000 000"})
    vacio = await client.patch("/api/v1/auth/yo", json={"telefono": None})
    assert vacio.json()["telefono"] is None


async def test_el_email_no_se_puede_dejar_vacio(client: AsyncClient, doctor):
    """La columna es NOT NULL: colar un null sería un 500 en la cara del médico."""
    assert (await client.patch("/api/v1/auth/yo", json={"email": None})).status_code == 422


async def test_el_huso_manda_en_la_hora_que_lee_el_panel(client: AsyncClient, doctor):
    await client.patch("/api/v1/auth/yo", json={"timezone": "America/Bogota"})
    ventana = (await client.get("/api/v1/appointments?dias=1")).json()
    assert ventana["timezone"] == "America/Bogota"


async def test_un_huso_inventado_no_llega_a_la_base(client: AsyncClient, doctor):
    rechazado = await client.patch("/api/v1/auth/yo", json={"timezone": "Marte/Olimpo"})
    assert rechazado.status_code == 422
    assert "Marte/Olimpo" in str(rechazado.json()["detail"])
    assert (await client.get("/api/v1/auth/yo")).json()["timezone"] == "Europe/Madrid"


# --- El enlace público ---


async def test_el_enlace_se_guarda_como_lo_va_a_ver_el_paciente(client: AsyncClient, doctor):
    resp = await client.patch("/api/v1/auth/yo", json={"slug_unico": "Dra. García Peña"})
    assert resp.status_code == 200
    # Es el slug con el que nació la cuenta: ponerlo igual no es una colisión, es un no-op.
    assert resp.json()["slug_unico"] == "dra-garcia-pena"


async def test_el_enlace_nuevo_abre_la_ficha_y_el_viejo_ya_no(client: AsyncClient, doctor):
    await client.patch("/api/v1/auth/yo", json={"slug_unico": "Consulta de Elena"})
    assert (await client.get("/api/v1/public/consulta-de-elena")).status_code == 200
    assert (await client.get(f"/api/v1/public/{doctor['slug_unico']}")).status_code == 404


async def test_un_enlace_solo_con_signos_no_forma_nada(client: AsyncClient, doctor):
    """'doctor' es lo que sale de normalizar '___': preferimos decirlo antes que guardar un slug suelto."""
    assert (await client.patch("/api/v1/auth/yo", json={"slug_unico": "___"})).status_code == 422


async def test_el_enlace_de_otra_consulta_no_se_roba_ni_se_sufija(client: AsyncClient):
    await _dos_cuentas(client)
    resp = await client.patch("/api/v1/auth/yo", json={"slug_unico": "Dr Lopez"})
    assert resp.status_code == 409
    assert "enlace" in resp.json()["detail"]
    # En el registro el sufijo '-2' es cortesía; aquí sería un enlace que nadie pidió.
    assert (await client.get("/api/v1/auth/yo")).json()["slug_unico"] == "dra-garcia-pena"


# --- El email de acceso ---


async def test_el_email_de_otra_cuenta_es_409_y_no_toca_nada(client: AsyncClient):
    await _dos_cuentas(client)
    resp = await client.patch("/api/v1/auth/yo", json={"email": DOCTOR_LOPEZ["email"]})
    assert resp.status_code == 409
    assert (await client.get("/api/v1/auth/yo")).json()["email"] == DOCTOR_GARCIA["email"]


async def test_reescribir_tu_propio_email_no_es_colision(client: AsyncClient):
    """CITEXT no distingue mayúsculas: guardarse el mismo email con otra mano sigue siendo el suyo."""
    await _dos_cuentas(client)
    resp = await client.patch("/api/v1/auth/yo", json={"email": "GARCIA@EXAMPLE.com"})
    assert resp.status_code == 200


async def test_el_email_nuevo_es_el_que_abre_la_puerta(client: AsyncClient, doctor):
    await client.patch("/api/v1/auth/yo", json={"email": "elena@consultrio.example"})
    await client.post("/api/v1/auth/salida")
    assert (await client.get("/api/v1/auth/yo")).status_code == 401
    viejo = await client.post(
        "/api/v1/auth/acceso",
        json={"email": DOCTOR_GARCIA["email"], "password": DOCTOR_GARCIA["password"]},
    )
    assert viejo.status_code == 401
    nuevo = await client.post(
        "/api/v1/auth/acceso",
        json={"email": "elena@consultrio.example", "password": DOCTOR_GARCIA["password"]},
    )
    assert nuevo.status_code == 200


# --- La contraseña ---


async def test_cambiar_la_clave_exige_sesion(client: AsyncClient):
    resp = await client.post("/api/v1/auth/contrasena", json={"actual": "x", "nueva": "una-clave-larga"})
    assert resp.status_code == 401


async def test_la_clave_actual_mal_es_403_no_401(client: AsyncClient, doctor):
    """401 diría que la sesión se cayó; aquí la sesión es buena y lo que falla es la confirmación."""
    resp = await client.post(
        "/api/v1/auth/contrasena",
        json={"actual": "no-es-mi-clave", "nueva": "una-clave-nueva-1"},
    )
    assert resp.status_code == 403
    assert (await client.get("/api/v1/auth/yo")).status_code == 200


async def test_clave_nueva_corta_se_rechaza_antes_de_escribir(client: AsyncClient, doctor):
    resp = await client.post(
        "/api/v1/auth/contrasena",
        json={"actual": DOCTOR_GARCIA["password"], "nueva": "abc"},
    )
    assert resp.status_code == 422
    sigue = await client.post(
        "/api/v1/auth/acceso",
        json={"email": DOCTOR_GARCIA["email"], "password": DOCTOR_GARCIA["password"]},
    )
    assert sigue.status_code == 200


async def test_la_clave_nueva_sirve_y_la_vieja_queda_fuera(client: AsyncClient, doctor):
    cambio = await client.post(
        "/api/v1/auth/contrasena",
        json={"actual": DOCTOR_GARCIA["password"], "nueva": "la-clave-nueva-2"},
    )
    assert cambio.status_code == 204

    # La sesión que hizo el cambio sigue dentro: no hay lista de revocación, caduca con el JWT.
    assert (await client.get("/api/v1/auth/yo")).status_code == 200

    vieja = await client.post(
        "/api/v1/auth/acceso",
        json={"email": DOCTOR_GARCIA["email"], "password": DOCTOR_GARCIA["password"]},
    )
    assert vieja.status_code == 401
    nueva = await client.post(
        "/api/v1/auth/acceso",
        json={"email": DOCTOR_GARCIA["email"], "password": "la-clave-nueva-2"},
    )
    assert nueva.status_code == 200


async def test_cambiar_la_clave_no_deja_la_hash_en_texto_plano(client: AsyncClient, doctor):
    import uuid

    from app.api import deps
    from app.models import Doctor

    await client.post(
        "/api/v1/auth/contrasena",
        json={"actual": DOCTOR_GARCIA["password"], "nueva": "la-clave-nueva-3"},
    )
    async with deps.SessionLocal() as session:
        almacen = await session.get(Doctor, uuid.UUID(doctor["id"]))
    assert almacen.password_hash.startswith("$argon2id$")
    assert "la-clave-nueva-3" not in almacen.password_hash
