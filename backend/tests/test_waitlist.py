"""Lista de espera: el paciente pide un hueco que no existe y el doctor la gestiona.

Contra PostgreSQL real, porque la deduplicación depende del advisory lock y del
`UNIQUE (doctor_id, patient_id, fecha)`, y porque `citext` y los NULL deciden si alguien que
recarga la página aparece dos veces en la lista de su médico.
"""

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from httpx import AsyncClient

from app.api import deps
from app.models import Patient, Waitlist
from tests.conftest import DOCTOR_LOPEZ, nueva_sesion, registrar, sql_filas

MADRID = ZoneInfo("Europe/Madrid")
ANA = {"nombre": "Ana Ibars", "telefono": "+34 611 22 33 44"}


async def servicio_id(client: AsyncClient, nombre: str = "Revisión") -> str:
    filas = await client.get("/api/v1/services")
    return next(f["id"] for f in filas.json() if f["nombre"] == nombre)


async def sin_horario(client: AsyncClient) -> None:
    """Deja al doctor sin ninguna franja activa: así el horizonte real no ofrece huecos."""
    for franja in (await client.get("/api/v1/availability")).json():
        borrada = await client.delete(f"/api/v1/availability/{franja['id']}")
        assert borrada.status_code == 204, borrada.text


async def huecos(client: AsyncClient, slug: str, servicio: str, dias: int = 14) -> dict:
    resp = await client.get(f"/api/v1/public/{slug}/disponibilidad", params={"servicio": servicio, "dias": dias})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def apuntar(
    client: AsyncClient,
    slug: str,
    servicio: str,
    *,
    datos: dict | None = None,
    **extras,
):
    cuerpo = {"servicio": servicio, **ANA, "consentimiento": True, **(datos or {}), **extras}
    return await client.post(f"/api/v1/public/{slug}/lista-espera", json=cuerpo)


async def apuntada(client: AsyncClient, slug: str, servicio: str, **extras) -> dict:
    resp = await apuntar(client, slug, servicio, **extras)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def otra_sesion(doctor: dict) -> AsyncClient:
    """El navegador de otro médico, con su propia cookie de sesión."""
    c = nueva_sesion()
    await c.post("/api/v1/auth/acceso", json={"email": doctor["email"], "password": doctor["password"]})
    return c


async def ver_lista(client: AsyncClient, **params) -> dict:
    resp = await client.get("/api/v1/waitlist", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def hoy() -> datetime:
    """Hoy en el huso del doctor: el backend corta el horizonte por su reloj, no por UTC."""
    return datetime.now(MADRID)


# --- Alta desde el enlace público ---


async def test_apuntarse_funciona_aunque_no_haya_ningun_hueco(client: AsyncClient, doctor: dict):
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    await sin_horario(client)
    assert (await huecos(client, slug, servicio))["dias"] == []

    creada = await apuntada(client, slug, servicio)
    assert creada["creado"] is True and creada["estado"] == "en_espera"

    cuerpo = await ver_lista(client, filtro="todas")
    assert (cuerpo["total"], cuerpo["en_espera"], cuerpo["contactadas"]) == (1, 1, 0)
    entrada = cuerpo["entradas"][0]
    assert entrada["nombre"] == "Ana Ibars"
    assert entrada["telefono"] == "+34 611 22 33 44"
    assert entrada["servicio"] == "Revisión"
    assert entrada["preferencia"] == "cualquier_hora"
    assert entrada["fecha"] is None


async def test_disponibilidad_devuelve_el_horizonte_aunque_este_vacio(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    await sin_horario(client)
    cuerpo = await huecos(client, doctor["slug_unico"], servicio, dias=14)
    primer_dia = hoy().date()
    assert cuerpo["desde"] == primer_dia.isoformat()
    # 14 días pedidos = del hoy al hoy+13, ofrezca lo que ofrezca cada día.
    assert cuerpo["hasta"] == (primer_dia + timedelta(days=13)).isoformat()


async def test_sin_consentimiento_no_se_guarda_nada(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    resp = await apuntar(client, doctor["slug_unico"], servicio, datos={"consentimiento": False})
    assert resp.status_code == 422
    assert "consentimiento" in resp.json()["detail"].lower()
    assert await sql_filas("select count(*) from waitlist") == [(0,)]


async def test_sin_contacto_no_hay_lista(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    resp = await apuntar(client, doctor["slug_unico"], servicio, datos={"telefono": None})
    assert resp.status_code == 422
    assert "teléfono" in resp.json()["detail"]
    assert await sql_filas("select count(*) from patients") == [(0,)]


async def test_telefono_en_blanco_cuenta_como_sin_contacto(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    resp = await apuntar(client, doctor["slug_unico"], servicio, telefono="   ")
    assert resp.status_code == 422


async def test_recargar_la_pagina_no_duplica_la_peticion(client: AsyncClient, doctor: dict):
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    primera = await apuntada(client, slug, servicio)
    segunda = await apuntada(client, slug, servicio)
    assert segunda["creado"] is False and segunda["id"] == primera["id"]
    assert await sql_filas("select count(*) from waitlist") == [(1,)]
    # Y tampoco dos fichas de paciente: la identidad es (doctor, nombre, teléfono).
    assert await sql_filas("select count(*) from patients") == [(1,)]


async def test_dias_distintos_son_dos_peticiones(client: AsyncClient, doctor: dict):
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    manana = (hoy().date() + timedelta(days=1)).isoformat()
    pasado_manana = (hoy().date() + timedelta(days=2)).isoformat()
    await apuntada(client, slug, servicio, fecha=manana)
    await apuntada(client, slug, servicio, fecha=pasado_manana)
    assert await sql_filas("select count(*) from waitlist") == [(2,)]


async def test_quien_fue_avisado_no_duplica_su_fila(client: AsyncClient, doctor: dict):
    """El UNIQUE es (doctor, paciente, día), así que repetir tiene que devolver la fila existente."""
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    creada = await apuntada(client, slug, servicio)
    avisada = await client.patch(f"/api/v1/waitlist/{creada['id']}", json={"estado": "contactada"})
    assert avisada.status_code == 200

    repetida = await apuntar(client, slug, servicio)
    assert repetida.status_code == 201
    assert repetida.json()["creado"] is False
    assert repetida.json()["id"] == creada["id"]
    # El estado real viaja en la respuesta: la página tiene que poder decir "ya te avisó".
    assert repetida.json()["estado"] == "contactada"
    assert await sql_filas("select count(*) from waitlist") == [(1,)]


async def test_repetir_el_mismo_dia_avisado_no_rompe(client: AsyncClient, doctor: dict):
    """Regresión: filtrar solo por 'en_espera' dejaba insertar una segunda fila del mismo día y el
    UNIQUE de la tabla la convertía en un 500."""
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    dia = (hoy().date() + timedelta(days=4)).isoformat()
    creada = await apuntada(client, slug, servicio, fecha=dia)
    await client.patch(f"/api/v1/waitlist/{creada['id']}", json={"estado": "contactada"})

    resp = await apuntar(client, slug, servicio, fecha=dia)
    assert resp.status_code == 201, resp.text
    cuerpo = resp.json()
    assert cuerpo["creado"] is False and cuerpo["estado"] == "contactada" and cuerpo["fecha"] == dia
    assert await sql_filas("select count(*) from waitlist") == [(1,)]


async def test_el_pasado_se_rechaza(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    resp = await apuntar(client, doctor["slug_unico"], servicio, fecha=(hoy().date() - timedelta(days=1)).isoformat())
    assert resp.status_code == 422
    assert "pasó" in resp.json()["detail"]


async def test_mas_alla_del_horizonte_se_rechaza(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    resp = await apuntar(client, doctor["slug_unico"], servicio, fecha=(hoy().date() + timedelta(days=90)).isoformat())
    assert resp.status_code == 422
    assert "60 días" in resp.json()["detail"]


async def test_el_dia_y_la_franja_se_guardan_como_se_pidieron(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    dia = (hoy().date() + timedelta(days=3)).isoformat()
    creada = await apuntada(client, doctor["slug_unico"], servicio, fecha=dia, preferencia="manana")
    assert creada["fecha"] == dia and creada["preferencia"] == "manana"
    filas = await sql_filas(
        "select fecha, preferencia_horaria, estado from waitlist where id = CAST(:id AS uuid)",
        id=creada["id"],
    )
    assert filas[0] == (datetime.fromisoformat(dia).date(), "manana", "en_espera")


async def test_servicio_de_otro_doctor_no_vale(client: AsyncClient, doctor: dict):
    async with nueva_sesion() as otra:
        # Registramos al segundo doctor en SU cliente: la cookie no se comparte.
        await registrar(otra, DOCTOR_LOPEZ)
        ajeno = await servicio_id(otra, "Urgencia")
        resp = await apuntar(client, doctor["slug_unico"], ajeno)
    assert resp.status_code == 404
    assert await sql_filas("select count(*) from waitlist") == [(0,)]


async def test_slug_inventado_devuelve_404(client: AsyncClient):
    resp = await client.post(
        "/api/v1/public/no-existe-ningun-slug/lista-espera",
        json={"servicio": str(uuid.uuid4()), **ANA, "consentimiento": True},
    )
    assert resp.status_code == 404


async def test_servicio_inactivo_no_acepta_listas(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    apagado = await client.patch(f"/api/v1/services/{servicio}", json={"activo": False})
    assert apagado.status_code == 200
    resp = await apuntar(client, doctor["slug_unico"], servicio)
    assert resp.status_code == 404


# --- Gestión del doctor ---


async def test_sin_sesion_no_se_lee_ni_se_gestiona(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    creada = await apuntada(client, doctor["slug_unico"], servicio)
    async with nueva_sesion() as anonima:
        leida = await anonima.get("/api/v1/waitlist")
        parcheada = await anonima.patch(f"/api/v1/waitlist/{creada['id']}", json={"estado": "contactada"})
        borrada = await anonima.delete(f"/api/v1/waitlist/{creada['id']}")
    assert (leida.status_code, parcheada.status_code, borrada.status_code) == (401, 401, 401)


async def test_otro_doctor_no_ve_tu_lista(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    await apuntada(client, doctor["slug_unico"], servicio)
    async with nueva_sesion() as otra:
        await registrar(otra, DOCTOR_LOPEZ)
        assert (await otra.get("/api/v1/waitlist?filtro=todas")).json()["total"] == 0


async def test_marcar_aviso_y_reabrir(client: AsyncClient, doctor: dict):
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    creada = await apuntada(client, slug, servicio)

    avisada = await client.patch(f"/api/v1/waitlist/{creada['id']}", json={"estado": "contactada"})
    assert avisada.status_code == 200
    assert avisada.json()["estado"] == "contactada"
    assert avisada.json()["nombre"] == "Ana Ibars"
    assert (await ver_lista(client))["total"] == 0
    assert (await ver_lista(client, filtro="contactadas"))["contactadas"] == 1

    reabierta = await client.patch(f"/api/v1/waitlist/{creada['id']}", json={"estado": "en_espera"})
    assert reabierta.json()["estado"] == "en_espera"
    estado = await sql_filas(
        "select estado from waitlist where id = CAST(:id AS uuid)", id=creada["id"]
    )
    assert estado[0][0] == "en_espera"


async def test_estado_inventado_es_422(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    creada = await apuntada(client, doctor["slug_unico"], servicio)
    resp = await client.patch(f"/api/v1/waitlist/{creada['id']}", json={"estado": "resuelta"})
    assert resp.status_code == 422


async def test_filtro_inventado_es_422(client: AsyncClient, doctor: dict):
    resp = await client.get("/api/v1/waitlist", params={"filtro": "loquesea"})
    assert resp.status_code == 422


async def test_borrar_la_peticion_deja_la_ficha_del_paciente(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    creada = await apuntada(client, doctor["slug_unico"], servicio)
    borrada = await client.delete(f"/api/v1/waitlist/{creada['id']}")
    assert borrada.status_code == 204
    assert await sql_filas("select count(*) from waitlist") == [(0,)]
    # La petición se va; el paciente se queda porque puede tener citas vivas que le apunten.
    assert await sql_filas("select count(*) from patients") == [(1,)]


async def test_lo_ajeno_no_se_toca(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    creada = await apuntada(client, doctor["slug_unico"], servicio)
    async with nueva_sesion() as otra:
        await registrar(otra, DOCTOR_LOPEZ)
        ajeno = await otra.patch(f"/api/v1/waitlist/{creada['id']}", json={"estado": "contactada"})
        borrado = await otra.delete(f"/api/v1/waitlist/{creada['id']}")
        inventado = await otra.patch(f"/api/v1/waitlist/{uuid.uuid4()}", json={"estado": "contactada"})
    assert (ajeno.status_code, borrado.status_code, inventado.status_code) == (404, 404, 404)
    assert await sql_filas("select count(*) from waitlist") == [(1,)]


async def test_apuntarse_no_tapa_ningun_hueco(client: AsyncClient, doctor: dict):
    """Regresión de diseño: la lista es demanda, no reserva. Un apuntado no puede quitar horas."""
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    antes = await huecos(client, slug, servicio, dias=3)
    assert antes["dias"], "el doctor recién registrado siempre ofrece huecos"
    await apuntada(client, slug, servicio, preferencia="tarde")
    assert (await huecos(client, slug, servicio, dias=3))["dias"] == antes["dias"]
    assert await sql_filas("select count(*) from appointments") == [(0,)]


async def test_lo_pendiente_va_delante_aunque_llegara_despues(client: AsyncClient, doctor: dict):
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    primero = await apuntada(client, slug, servicio)
    segundo = await apuntada(client, slug, servicio, datos={"nombre": "Beto Sanz", "telefono": "+34 622 33 44 55"})

    await client.patch(f"/api/v1/waitlist/{primero['id']}", json={"estado": "contactada"})
    orden = [e["id"] for e in (await ver_lista(client, filtro="todas"))["entradas"]]
    assert orden == [segundo["id"], primero["id"]]
    # Y en la vista de pendientes solo queda el que no ha sido avisado.
    assert [e["id"] for e in (await ver_lista(client))["entradas"]] == [segundo["id"]]


async def test_el_solo_email_basta_y_reutiliza_la_ficha(client: AsyncClient, doctor: dict):
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    cuerpo = {"servicio": servicio, "nombre": "Cova Ríos", "email": "cova@example.com", "consentimiento": True}
    una = await client.post(f"/api/v1/public/{slug}/lista-espera", json=cuerpo)
    dos = await client.post(
        f"/api/v1/public/{slug}/lista-espera",
        json={**cuerpo, "email": "COVA@example.com", "fecha": (hoy().date() + timedelta(days=1)).isoformat()},
    )
    assert (una.status_code, dos.status_code) == (201, 201)
    assert una.json()["creado"] is True and dos.json()["creado"] is True
    # Mismo nombre y mismo email (da igual cómo se escriba) = una sola ficha de paciente.
    assert await sql_filas("select count(*) from patients") == [(1,)]
    assert await sql_filas("select count(*) from waitlist") == [(2,)]


async def test_la_fila_apunta_al_paciente_y_al_servicio(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    creada = await apuntada(client, doctor["slug_unico"], servicio)
    async with deps.SessionLocal() as session:
        fila = await session.get(Waitlist, uuid.UUID(creada["id"]))
        paciente = await session.get(Patient, fila.patient_id)
    assert str(fila.service_id) == servicio
    assert fila.estado == "en_espera" and fila.fecha is None
    assert paciente.nombre == "Ana Ibars"
    assert paciente.telefono == "+34 611 22 33 44"
