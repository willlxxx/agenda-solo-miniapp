"""Avisar de un hueco libre a quien está en la lista de espera, y reservárselo el médico.

Dos cosas se prueban aquí, y ninguna se puede simular:

- que el hueco que el panel ofrece es un hueco REAL de la disponibilidad de ese momento, con el
  mismo motor y la misma rejilla con los que después se escribe la cita;
- que la reserva asistada entra por el mismo advisory lock que la pública: si alguien ocupó el
  hueco mientras el médico escribía el WhatsApp, gana uno solo.

El canal de aviso son enlaces (wa.me y mailto:) que el médico abre desde su propia cuenta: no hay
proveedor externo que mande mensajes, así que tampoco hay nada que comprobar fuera de esta base.
"""

import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from httpx import AsyncClient

from tests.conftest import DOCTOR_LOPEZ, lunes_que_viene, nueva_sesion, registrar, sql_filas

LUNES = lunes_que_viene(date.today())
ANA = {"nombre": "Ana Ibars", "telefono": "+34 611 22 33 44", "email": "ana@example.com"}


async def servicio_id(client: AsyncClient, nombre: str = "Revisión") -> str:
    filas = await client.get("/api/v1/services")
    return next(f["id"] for f in filas.json() if f["nombre"] == nombre)


async def apuntar(sesion: AsyncClient, slug: str, servicio: str, **extras) -> dict:
    cuerpo = {"servicio": servicio, **ANA, "consentimiento": True, **extras}
    resp = await sesion.post(f"/api/v1/public/{slug}/lista-espera", json=cuerpo)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def avisos(client: AsyncClient, **params) -> dict:
    resp = await client.get("/api/v1/waitlist/encajes", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def reservar_por_el_doctor(client: AsyncClient, entrada: str, *, fecha: date = LUNES, hora: str = "09:00"):
    return await client.post(
        f"/api/v1/waitlist/{entrada}/reservar", json={"fecha": fecha.isoformat(), "hora": hora}
    )


async def contar(tabla: str, doctor_id: str) -> int:
    filas = await sql_filas(f"select count(*) from {tabla} where doctor_id = cast(:d as uuid)", d=doctor_id)
    return int(filas[0][0])


async def huecos_publicos(client: AsyncClient, slug: str, servicio: str, dias: int = 14) -> dict:
    resp = await client.get(
        f"/api/v1/public/{slug}/disponibilidad", params={"servicio": servicio, "dias": dias}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- Lo que el panel ve ---


async def test_una_peticion_pendiente_sale_con_su_hueco_y_el_aviso_escrito(client, doctor):
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    entrada = await apuntar(client, slug, servicio, fecha=LUNES.isoformat(), preferencia="manana")

    cuerpo = await avisos(client)
    assert (cuerpo["pendientes"], cuerpo["con_hueco"]) == (1, 1)
    aviso = cuerpo["avisos"][0]
    assert aviso["id"] == entrada["id"]
    assert aviso["nombre"] == "Ana Ibars" and aviso["servicio"] == "Revisión"
    # El primer hueco de la mañana del lunes: la rejilla arranca en la apertura de la franja.
    assert (aviso["fecha"], aviso["inicio"], aviso["fin"]) == (LUNES.isoformat(), "09:00:00", "09:30:00")
    assert "Ana" in aviso["mensaje"] and "revisión" in aviso["mensaje"].lower()
    # Todavía no hay cita: el texto no puede decir que ya es suya.
    assert "reservada" not in aviso["mensaje"] and "tienes cita" not in aviso["mensaje"]
    assert aviso["whatsapp"].startswith("https://wa.me/34611223344?text=")
    assert aviso["correo"].startswith("mailto:")


async def test_el_encaje_es_una_lectura_no_toca_nada(client, doctor):
    servicio = await servicio_id(client)
    await apuntar(client, doctor["slug_unico"], servicio, fecha=LUNES.isoformat())
    antes = (await huecos_publicos(client, doctor["slug_unico"], servicio))["dias"]

    await avisos(client)

    assert (await huecos_publicos(client, doctor["slug_unico"], servicio))["dias"] == antes
    assert await contar("appointments", doctor["id"]) == 0


async def test_dos_esperas_del_mismo_hueco_no_reciben_el_mismo_aviso(client, doctor):
    """Un hueco se ofrece una sola vez: escribir a dos el mismo lunes sería peor que callar."""
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    primero = await apuntar(client, slug, servicio, nombre="Uno Primero", telefono="611000001", fecha=LUNES.isoformat())
    segundo = await apuntar(
        client, slug, servicio, nombre="Dos Despues", telefono="611000002", fecha=LUNES.isoformat()
    )

    cuerpo = await avisos(client)
    assert [a["id"] for a in cuerpo["avisos"]] == [primero["id"], segundo["id"]]
    # Con cita de 09:00 a 09:30 puesta, la rejilla real vuelve a las 09:30: 09:15 ya no vale.
    assert [a["inicio"] for a in cuerpo["avisos"]] == ["09:00:00", "09:30:00"]


async def test_sin_franjas_no_hay_nada_que_ofrecer(client, doctor):
    servicio = await servicio_id(client)
    await apuntar(client, doctor["slug_unico"], servicio, fecha=LUNES.isoformat())
    for franja in (await client.get("/api/v1/availability")).json():
        assert (await client.delete(f"/api/v1/availability/{franja['id']}")).status_code == 204

    cuerpo = await avisos(client)
    assert (cuerpo["pendientes"], cuerpo["con_hueco"], cuerpo["avisos"]) == (1, 0, [])


async def test_un_telefono_raro_no_inventa_un_wa_me(client, doctor):
    """Seguimos ofreciendo el mensaje y el correo; el enlace de WhatsApp solo existe si es válido."""
    servicio = await servicio_id(client)
    await apuntar(
        client,
        doctor["slug_unico"],
        servicio,
        nombre="Telefono Raro",
        telefono="llamad al 900",
        email="raro@example.com",
        fecha=LUNES.isoformat(),
    )

    aviso = (await avisos(client))["avisos"][0]
    assert aviso["whatsapp"] is None and aviso["correo"].startswith("mailto:raro@example.com")


async def test_lo_que_ya_esta_cerrado_no_vuelve_a_salir(client, doctor):
    servicio = await servicio_id(client)
    entrada = await apuntar(client, doctor["slug_unico"], servicio, fecha=LUNES.isoformat())
    assert (await client.patch(f"/api/v1/waitlist/{entrada['id']}", json={"estado": "contactada"})).status_code == 200

    cuerpo = await avisos(client)
    assert (cuerpo["pendientes"], cuerpo["avisos"]) == (0, [])
    # Marcarlo a mano también deja constancia del aviso: el panel no insiste.
    assert (await client.get("/api/v1/waitlist?filtro=contactadas")).json()["entradas"][0]["avisado_utc"]


async def test_el_encaje_pide_sesion(client, doctor):
    servicio = await servicio_id(client)
    await apuntar(client, doctor["slug_unico"], servicio, fecha=LUNES.isoformat())
    suelto = nueva_sesion()
    assert (await suelto.get("/api/v1/waitlist/encajes")).status_code == 401
    await suelto.aclose()


# --- Reservar por él ---


async def test_reservar_por_el_doctor_escribe_la_cita_y_cierra_la_fila(client, doctor):
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    entrada = await apuntar(client, slug, servicio, fecha=LUNES.isoformat())

    resp = await reservar_por_el_doctor(client, entrada["id"])
    assert resp.status_code == 201, resp.text
    cita = resp.json()
    assert (cita["fecha"], cita["hora"], cita["fin"]) == (LUNES.isoformat(), "09:00:00", "09:30:00")
    assert cita["servicio"] == "Revisión" and cita["paciente"] == "Ana Ibars"
    assert cita["entrada"]["estado"] == "contactada" and cita["entrada"]["avisado_utc"]

    # 'panel', no 'publico': el médico attestó la petición telefónica, el paciente no clicó nada.
    filas = await sql_filas(
        "select origen, estado, duracion_minutos from appointments where doctor_id = cast(:d as uuid)",
        d=doctor["id"],
    )
    assert filas == [("panel", "confirmada", 30)]
    lista = await sql_filas(
        "select estado, avisado_en is not null, hueco_ofrecido is not null "
        "from waitlist where doctor_id = cast(:d as uuid)",
        d=doctor["id"],
    )
    assert lista == [("contactada", True, True)]
    # Reutiliza la ficha del paciente que creó el apuntarse: no hay duplicados.
    assert await contar("patients", doctor["id"]) == 1
    assert await contar("waitlist", doctor["id"]) == 1


async def test_la_cita_asistada_desaparece_de_la_disponibilidad_publica(client, doctor):
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    entrada = await apuntar(client, slug, servicio, fecha=LUNES.isoformat())
    assert (await reservar_por_el_doctor(client, entrada["id"])).status_code == 201

    dia = next(d for d in (await huecos_publicos(client, slug, servicio))["dias"] if d["fecha"] == LUNES.isoformat())
    assert "09:00:00" not in [h["inicio"] for h in dia["huecos"]]
    assert "09:30:00" in [h["inicio"] for h in dia["huecos"]]


async def test_reservar_sobre_un_hueco_que_ya_no_esta_libre_es_409(client, doctor):
    """El paciente ocupó el hueco mientras el médico escribía el WhatsApp: no hay dos citas."""
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    entrada = await apuntar(client, slug, servicio, fecha=LUNES.isoformat())
    ocupado = await client.post(
        f"/api/v1/public/{slug}/citas",
        json={
            "servicio": servicio,
            "fecha": LUNES.isoformat(),
            "hora": "09:00",
            "nombre": "Otro Paciente",
            "telefono": "600999888",
            "consentimiento": True,
        },
    )
    assert ocupado.status_code == 201, ocupado.text

    resp = await reservar_por_el_doctor(client, entrada["id"])
    assert resp.status_code == 409
    assert "hueco" in resp.json()["detail"]
    assert await contar("appointments", doctor["id"]) == 1
    # La petición sigue pendiente: el aviso no cuajó y el médico puede ofrecer otro hueco.
    estado = await sql_filas(
        "select estado, avisado_en, hueco_ofrecido from waitlist where doctor_id = cast(:d as uuid)", d=doctor["id"]
    )
    assert estado == [("en_espera", None, None)]
    assert (await avisos(client))["avisos"][0]["inicio"] == "09:30:00"


async def test_dos_reservas_asistadas_sobre_el_mismo_hueco_solo_una_cuaja(client, doctor):
    """La misma prueba de concurrencia que la reserva pública, pero por el camino del panel."""
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    primero = await apuntar(client, slug, servicio, nombre="Uno Primero", telefono="611000001", fecha=LUNES.isoformat())
    segundo = await apuntar(
        client, slug, servicio, nombre="Dos Despues", telefono="611000002", fecha=LUNES.isoformat()
    )

    respuestas = await asyncio.gather(
        client.post(
            f"/api/v1/waitlist/{primero['id']}/reservar", json={"fecha": LUNES.isoformat(), "hora": "10:00"}
        ),
        client.post(
            f"/api/v1/waitlist/{segundo['id']}/reservar", json={"fecha": LUNES.isoformat(), "hora": "10:00"}
        ),
    )
    assert sorted(r.status_code for r in respuestas) == [201, 409]
    assert await contar("appointments", doctor["id"]) == 1


async def test_reservar_un_hueco_que_no_ofrecemos_es_409(client, doctor):
    """El médico no puede inventarse una hora fuera de la rejilla: revalida con el mismo motor."""
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    entrada = await apuntar(client, slug, servicio, fecha=LUNES.isoformat())

    assert (await reservar_por_el_doctor(client, entrada["id"], hora="09:07")).status_code == 409
    assert await contar("appointments", doctor["id"]) == 0


async def test_reservar_un_dia_pasado_es_422(client, doctor):
    servicio = await servicio_id(client)
    entrada = await apuntar(client, doctor["slug_unico"], servicio, fecha=LUNES.isoformat())

    resp = await reservar_por_el_doctor(client, entrada["id"], fecha=date.today() - timedelta(days=2))
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Ese día ya pasó"


async def test_la_fila_de_otro_doctor_no_se_reserva_ni_se_avis(client, doctor):
    servicio = await servicio_id(client)
    entrada = await apuntar(client, doctor["slug_unico"], servicio, fecha=LUNES.isoformat())
    ajeno = uuid.UUID(entrada["id"])

    await registrar(client, DOCTOR_LOPEZ)  # la cookie pasa a ser de otro médico
    resp = await reservar_por_el_doctor(client, str(ajeno))
    assert resp.status_code == 404
    assert await contar("appointments", doctor["id"]) == 0


async def test_reabrir_la_fila_la_devuelve_a_la_cola_sin_el_aviso(client, doctor):
    """El paciente dijo que ese día no: la petición vuelve a estar pendiente y el aviso se borra."""
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    entrada = await apuntar(client, slug, servicio, fecha=LUNES.isoformat())
    assert (await reservar_por_el_doctor(client, entrada["id"])).status_code == 201

    reabierta = await client.patch(f"/api/v1/waitlist/{entrada['id']}", json={"estado": "en_espera"})
    assert reabierta.status_code == 200
    assert reabierta.json()["avisado_utc"] is None and reabierta.json()["hueco_ofrecido_utc"] is None

    # Y el panel la vuelve a listar, con el hueco que queda libre (el de las 09:00 ya es suyo).
    aviso = (await avisos(client))["avisos"][0]
    assert aviso["id"] == entrada["id"] and aviso["inicio"] == "09:30:00"


# --- La columna de origen también en el camino público ---


async def test_la_reserva_autoclictada_nace_con_origen_publico(client, doctor):
    slug = doctor["slug_unico"]
    servicio = await servicio_id(client)
    resp = await client.post(
        f"/api/v1/public/{slug}/citas",
        json={
            "servicio": servicio,
            "fecha": LUNES.isoformat(),
            "hora": "09:00",
            **ANA,
            "consentimiento": True,
        },
    )
    assert resp.status_code == 201, resp.text
    assert (
        await sql_filas("select origen from appointments where doctor_id = cast(:d as uuid)", d=doctor["id"])
    ) == [("publico",)]


async def test_el_origen_no_acepta_un_valor_inventado(client, doctor):
    """La CHECK de la tabla protege también lo que se escribe a mano: origen solo tiene dos valores."""
    from sqlalchemy.exc import IntegrityError

    from app.api import deps
    from app.models import Appointment

    inicio = datetime.combine(LUNES, time(9, 0), tzinfo=UTC)
    async with deps.SessionLocal() as session:
        session.add(
            Appointment(
                doctor_id=uuid.UUID(doctor["id"]),
                fecha_hora=inicio,
                duracion_minutos=30,
                estado="confirmada",
                consentimiento_ok_en=inicio,
                origen="whatsapp",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
