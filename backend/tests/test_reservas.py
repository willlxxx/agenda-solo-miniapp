"""Reserva del paciente sobre el enlace público: escritura real con advisory lock en PostgreSQL."""

import asyncio
import uuid
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from httpx import AsyncClient

from tests.conftest import DOCTOR_LOPEZ, nueva_sesion, registrar, sql_filas

MADRID = ZoneInfo("Europe/Madrid")
LUNES_VERANO = date(2026, 10, 19)  # Madrid en +02
LUNES_INVIERNO = date(2026, 10, 26)  # tras el cambio de horario: +01
PACIENTE = {"nombre": "Ana Paciente", "telefono": "600111222", "email": "ana.paciente@example.com"}


async def reserva(
    sesion: AsyncClient, slug: str, servicio: str, *, fecha: date = LUNES_VERANO, hora: str = "09:00", **extra
):
    cuerpo = {"servicio": servicio, "fecha": fecha.isoformat(), "hora": hora, **PACIENTE, "consentimiento": True}
    return await sesion.post(f"/api/v1/public/{slug}/citas", json={**cuerpo, **extra})


async def revision(client: AsyncClient) -> str:
    filas = await client.get("/api/v1/services")
    return next(f["id"] for f in filas.json() if f["nombre"] == "Revisión")


async def inicios_de(client: AsyncClient, slug: str, servicio: str) -> list[str]:
    resp = await client.get(
        f"/api/v1/public/{slug}/disponibilidad", params={"servicio": servicio, "desde": LUNES_VERANO, "dias": 1}
    )
    assert resp.status_code == 200, resp.text
    return [h["inicio"][:5] for h in resp.json()["dias"][0]["huecos"]]


async def contar(tabla: str, doctor_id: str) -> int:
    filas = await sql_filas(f"select count(*) from {tabla} where doctor_id = cast(:d as uuid)", d=doctor_id)
    return int(filas[0][0])


# --- Camino feliz ---


async def test_reservar_un_hueco_lo_quita_de_la_lista(client: AsyncClient, doctor: dict):
    servicio = await revision(client)
    resp = await reserva(client, doctor["slug_unico"], servicio)
    assert resp.status_code == 201, resp.text
    cita = resp.json()
    assert (cita["hora"], cita["fin"], cita["duracion_minutos"], cita["servicio"]) == (
        "09:00:00",
        "09:30:00",
        30,
        "Revisión",
    )

    tras = await inicios_de(client, doctor["slug_unico"], servicio)
    assert "09:00" not in tras  # el hueco elegido
    assert "09:15" not in tras  # y el que lo pisaba
    assert "09:30" in tras


async def test_dos_pacientes_mismo_hueco_el_segundo_recibe_409(client: AsyncClient, doctor: dict):
    servicio = await revision(client)
    assert (await reserva(client, doctor["slug_unico"], servicio)).status_code == 201
    segundo = await reserva(client, doctor["slug_unico"], servicio, nombre="Otro Paciente", telefono="600999888")
    assert segundo.status_code == 409
    assert await contar("appointments", doctor["id"]) == 1


async def test_concurrencia_real_25_peticiones_una_sola_cita(client: AsyncClient, doctor: dict):
    """El test que justifica PostgreSQL: todas caen sobre el mismo hueco a la vez, sin cookie."""
    servicio = await revision(client)
    paciente = nueva_sesion()
    respuestas = await asyncio.gather(
        *[
            paciente.post(
                f"/api/v1/public/{doctor['slug_unico']}/citas",
                json={
                    "servicio": servicio,
                    "fecha": LUNES_VERANO.isoformat(),
                    "hora": "11:00",
                    "nombre": f"Paciente {i}",
                    "telefono": f"600{i:05d}",
                    "consentimiento": True,
                },
            )
            for i in range(25)
        ]
    )
    codigos = sorted(r.status_code for r in respuestas)
    assert codigos.count(201) == 1, codigos
    assert set(codigos) == {201, 409}
    assert await contar("appointments", doctor["id"]) == 1
    await paciente.aclose()


async def test_el_lock_no_sobre_bloquea_huecos_distintos(client: AsyncClient, doctor: dict):
    """Cuatro huecos que no se tocan, reservados a la vez, deben cuajar los cuatro."""
    servicio = await revision(client)
    paciente = nueva_sesion()
    respuestas = await asyncio.gather(
        *[
            paciente.post(
                f"/api/v1/public/{doctor['slug_unico']}/citas",
                json={
                    "servicio": servicio,
                    "fecha": LUNES_VERANO.isoformat(),
                    "hora": hora,
                    "nombre": f"Paciente {i}",
                    "telefono": f"610{i:05d}",
                    "consentimiento": True,
                },
            )
            for i, hora in enumerate(["09:00", "09:30", "10:00", "10:30"])
        ]
    )
    assert sorted(r.status_code for r in respuestas) == [201, 201, 201, 201]
    assert await contar("appointments", doctor["id"]) == 4
    await paciente.aclose()


async def test_el_paciente_se_reutiliza_no_se_duplica(client: AsyncClient, doctor: dict):
    servicio = await revision(client)
    assert (await reserva(client, doctor["slug_unico"], servicio, hora="09:00")).status_code == 201
    assert (await reserva(client, doctor["slug_unico"], servicio, hora="16:00")).status_code == 201
    assert await contar("patients", doctor["id"]) == 1


# --- Fronteras y validaciones ---


async def test_sin_consentimiento_no_hay_cita(client: AsyncClient, doctor: dict):
    resp = await reserva(client, doctor["slug_unico"], await revision(client), consentimiento=False)
    assert resp.status_code == 422
    assert await contar("appointments", doctor["id"]) == 0


async def test_una_hora_que_no_esta_en_la_rejilla_es_409(client: AsyncClient, doctor: dict):
    assert (await reserva(client, doctor["slug_unico"], await revision(client), hora="10:07")).status_code == 409


async def test_un_dia_bloqueado_no_se_reserva(client: AsyncClient, doctor: dict):
    await client.post("/api/v1/blocked-dates", json={"fecha": LUNES_VERANO.isoformat()})
    assert (await reserva(client, doctor["slug_unico"], await revision(client))).status_code == 409


async def test_el_pasado_y_el_horizonte_lejos_es_422(client: AsyncClient, doctor: dict):
    servicio = await revision(client)
    assert (await reserva(client, doctor["slug_unico"], servicio, fecha=date(2026, 10, 1))).status_code == 422
    lejos = date.today() + timedelta(days=90)
    while lejos.weekday() != 0:
        lejos += timedelta(days=1)
    assert (await reserva(client, doctor["slug_unico"], servicio, fecha=lejos)).status_code == 422


async def test_servicio_ajeno_es_404_y_propio_no_necesita_cookie(client: AsyncClient, doctor: dict):
    servicio = await revision(client)
    assert (await reserva(client, doctor["slug_unico"], str(uuid.uuid4()))).status_code == 404
    await registrar(client, DOCTOR_LOPEZ)  # la cookie del client pasa a ser de López
    suelto = nueva_sesion()  # navegador del paciente: sin sesión ninguna
    resp = await reserva(suelto, doctor["slug_unico"], servicio)
    assert resp.status_code == 201
    await suelto.aclose()


async def test_servicio_desactivado_es_404(client: AsyncClient, doctor: dict):
    servicio = await revision(client)
    await client.delete(f"/api/v1/services/{servicio}")
    assert (await reserva(client, doctor["slug_unico"], servicio)).status_code == 404


# --- Lo que queda guardado en BD ---


async def test_la_duracion_se_congela_y_el_utc_respeta_el_huso(client: AsyncClient, doctor: dict):
    servicio = await revision(client)
    verano = await reserva(client, doctor["slug_unico"], servicio, fecha=LUNES_VERANO, hora="09:00")
    invierno = await reserva(client, doctor["slug_unico"], servicio, fecha=LUNES_INVIERNO, hora="09:00")
    assert (verano.status_code, invierno.status_code) == (201, 201)

    filas = await sql_filas(
        "select fecha_hora at time zone 'UTC', duracion_minutos, consentimiento_ok_en is not null "
        "from appointments where doctor_id = cast(:d as uuid) order by fecha_hora",
        d=doctor["id"],
    )
    # 09:00 locales son 07:00 UTC en verano y 08:00 UTC en invierno: el huso del doctor manda.
    assert [f[0].strftime("%m-%d %H:%M") for f in filas] == ["10-19 07:00", "10-26 08:00"]
    assert [int(f[1]) for f in filas] == [30, 30]
    assert all(f[2] for f in filas)  # evidencia de consentimiento en cada reserva

    # Cambiar el servicio después no reescribe la cita existente.
    await client.patch(f"/api/v1/services/{servicio}", json={"duracion_minutos": 60})
    despues = await sql_filas(
        "select duracion_minutos from appointments where doctor_id = cast(:d as uuid) order by fecha_hora",
        d=doctor["id"],
    )
    assert [int(f[0]) for f in despues] == [30, 30]


async def test_la_cita_no_guarda_nada_clinico(client: AsyncClient, doctor: dict):
    """El paciente puede escribir donde quiere: solo viajan los campos del esquema."""
    servicio = await revision(client)
    resp = await reserva(
        client, doctor["slug_unico"], servicio, hora="12:00", nombre="Ana Paciente", telefono="600111222"
    )
    assert resp.status_code == 201
    columnas = await sql_filas("select column_name from information_schema.columns where table_name='appointments'")
    textos = {c[0] for c in columnas}
    assert not {"nota", "notas", "diagnostico", "motivo_clinico", "historial"} & textos
    pacientes = await sql_filas(
        "select nombre, telefono, email from patients where doctor_id = cast(:d as uuid)", d=doctor["id"]
    )
    assert pacientes == [("Ana Paciente", "600111222", "ana.paciente@example.com")]
