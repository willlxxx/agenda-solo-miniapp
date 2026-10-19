"""Recordatorios automáticos: qué citas tocan aviso y qué sale por el correo.

Se prueba en dos capas, porque son dos riesgos distintos:

- el **dominio puro** (ventanas, horas de silencio, texto) con fechas fijas: es lo que no puede
  fallar según la hora a la que corre el CI, y es lo que decide si un paciente recibe un correo a
  las 03:00 o no recibe ninguno;
- el **endpoint del cron** contra PostgreSQL real, con el proveedor simulado y el reloj sustituido:
  aquí lo que importa es la escritura (bandera + fecha + intentos) y que dos pasadas solapadas no
  dupliquen un aviso.

Nada de esto abre un SMTP: `recordatorios_proveedor` por defecto es `simulado`, así que el archivo
entero puede correr sin credenciales y sin mandarle un correo a nadie.
"""

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient

from app.api.deps import get_ahora, get_proveedor_correo
from app.config import get_settings
from app.domain.recordatorios import (
    TIPO_1H,
    TIPO_24H,
    VENTANAS,
    CitaParaRecordar,
    adelantos_posibles,
    cuando_es,
    dentro_de_horario,
    en_ventana,
    mensaje_de_recordatorio,
    plan_de_envios,
)
from app.main import app
from app.services.correo import Simulado
from app.services.despacho import MAX_INTENTOS
from tests.conftest import DOCTOR_GARCIA, MADRID, registrar, sql_filas

# Fechas fijas y leídas del revés: el 21 de octubre de 2026 Madrid todavía está en horario de
# verano (cambia el último domingo de mes), así que las 09:00 locales son las 07:00 UTC.
INICIO = datetime(2026, 10, 21, 7, 0, tzinfo=UTC)
INICIO_MADRUGADA = datetime(2026, 10, 21, 2, 0, tzinfo=UTC)  # 04:00 locales
MIERCOLES = date(2026, 10, 21)
SECRETO = "secreto-de-cron-de-prueba"


# --- dominio puro ---


def cita(**cambios) -> CitaParaRecordar:
    base = {
        "id": uuid.uuid4(),
        "doctor_nombre": "Dra. García Peña",
        "doctor_telefono": "+34 600 11 22 33",
        "zona": MADRID,
        "inicio_utc": INICIO,
        "servicio": "Revisión",
        "paciente_nombre": "Ana Ibars",
        "email": "ana@example.com",
        "enviado_24h": False,
        "enviado_1h": False,
    }
    base.update(cambios)
    return CitaParaRecordar(**base)


def test_la_ventana_de_24h_acepta_de_12_a_24_horas():
    assert en_ventana(TIPO_24H, timedelta(hours=24)) is True
    assert en_ventana(TIPO_24H, timedelta(hours=12)) is True
    assert en_ventana(TIPO_24H, timedelta(hours=11, minutes=59)) is False
    assert en_ventana(TIPO_24H, timedelta(hours=24, minutes=1)) is False


def test_la_ventana_de_1h_acepta_de_40_a_60_minutos():
    assert en_ventana(TIPO_1H, timedelta(minutes=60)) is True
    assert en_ventana(TIPO_1H, timedelta(minutes=40)) is True
    assert en_ventana(TIPO_1H, timedelta(minutes=39)) is False
    assert en_ventana(TIPO_1H, timedelta(minutes=61)) is False


def test_el_rango_de_lectura_cubre_las_dos_ventanas():
    """Lo que el despacho lee de la BD tiene que contener todo lo que el dominio puede decidir.

    Si alguien estrecha una ventana y el barrido se queda corto, el aviso no se manda y ningún otro
    test se entera: esta es la comprobación que lo convierte en rojo.
    """
    minimo, maximo = adelantos_posibles()
    limites = [limite for pareja in VENTANAS.values() for limite in pareja]
    assert minimo == min(limites) and maximo == max(limites)


def test_el_silencio_empieza_a_las_ocho_y_termina_a_las_nueve_de_la_noche():
    dentro = ZoneInfo("Europe/Madrid")
    assert dentro_de_horario(datetime(2026, 10, 20, 7, 59, tzinfo=dentro)) is False
    assert dentro_de_horario(datetime(2026, 10, 20, 8, 0, tzinfo=dentro)) is True
    assert dentro_de_horario(datetime(2026, 10, 20, 20, 59, tzinfo=dentro)) is True
    assert dentro_de_horario(datetime(2026, 10, 20, 21, 0, tzinfo=dentro)) is False


def test_el_dia_se_dice_como_lo_diria_una_persona():
    assert cuando_es(MIERCOLES, MIERCOLES) == "hoy"
    assert cuando_es(MIERCOLES + timedelta(days=1), MIERCOLES) == "mañana"
    assert cuando_es(date(2026, 10, 26), MIERCOLES) == "el lunes 26 de octubre"


def test_el_aviso_ofrece_el_telefono_del_medico():
    asunto, cuerpo = mensaje_de_recordatorio(cita(), TIPO_24H, INICIO - timedelta(hours=23))
    assert asunto == "Recordatorio: mañana a las 09:00 con Dra. García Peña"
    assert "Ana" in cuerpo
    assert "revisión" in cuerpo
    assert "+34 600 11 22 33" in cuerpo


def test_sin_telefono_no_se_promete_una_llamada():
    _, cuerpo = mensaje_de_recordatorio(cita(doctor_telefono=None), TIPO_24H, INICIO - timedelta(hours=20))
    assert "avísanos" in cuerpo
    assert "llámame" not in cuerpo


def test_la_hora_que_lee_el_paciente_es_la_del_despacho_no_la_utc():
    asunto, cuerpo = mensaje_de_recordatorio(cita(), TIPO_1H, INICIO - timedelta(minutes=50))
    assert "09:00" in asunto and "09:00" in cuerpo
    assert "07:00" not in asunto and "07:00" not in cuerpo


def test_una_cita_sin_servicio_tambien_se_recuerda():
    _, cuerpo = mensaje_de_recordatorio(cita(servicio=""), TIPO_24H, INICIO - timedelta(hours=22))
    assert "tienes cita a las 09:00" in cuerpo


def test_el_cuerpo_declara_que_el_aviso_es_automático():
    _, cuerpo = mensaje_de_recordatorio(cita(), TIPO_24H, INICIO - timedelta(hours=22))
    assert "automáticamente" in cuerpo


def test_sin_email_no_hay_a_donde_escribir():
    assert plan_de_envios([cita(email="   ")], INICIO - timedelta(hours=20)) == []


def test_lo_que_ya_esta_marcado_no_se_vuelve_a_mandar():
    assert plan_de_envios([cita(enviado_24h=True, enviado_1h=True)], INICIO - timedelta(minutes=50)) == []
    assert plan_de_envios([cita(enviado_24h=True, enviado_1h=True)], INICIO - timedelta(hours=20)) == []


def test_el_aviso_de_24h_no_tapa_el_de_una_hora():
    """Son dos banderas distintas a propósito: al día siguiente la misma cita vuelve a necesitar aviso."""
    avisos = plan_de_envios([cita(enviado_24h=True)], INICIO - timedelta(minutes=50))
    assert [aviso.tipo for aviso in avisos] == [TIPO_1H]


def test_si_los_dos_estan_pendientes_manda_el_de_ultima_hora():
    avisos = plan_de_envios([cita()], INICIO - timedelta(minutes=50))
    assert [aviso.tipo for aviso in avisos] == [TIPO_1H]


def test_una_cita_fuera_de_ventana_no_sale_en_la_pasada():
    for adelanto in (timedelta(hours=6), timedelta(days=3), timedelta(minutes=10)):
        assert plan_de_envios([cita()], INICIO - adelanto) == []


def test_de_madrugada_no_se_escribe_a_nadie_esa_hora_no_le_llega_el_avis():
    # El mismo adelanto de 24 h, pero cayendo a las 04:00 locales del doctor.
    assert plan_de_envios([cita(inicio_utc=INICIO_MADRUGADA)], INICIO_MADRUGADA - timedelta(hours=24)) == []
    # Y dentro de horario, ese mismo adelanto sí produce el aviso: la diferencia es la hora.
    avisos = plan_de_envios([cita(inicio_utc=INICIO_MADRUGADA)], INICIO_MADRUGADA - timedelta(hours=20))
    assert [aviso.tipo for aviso in avisos] == [TIPO_24H]


# --- el endpoint del cron, sobre la base de datos real ---


@pytest.fixture
def cron_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    """`CRON_SECRET` como lo tendría el despliegue: la app lo lee por entorno, no por parámetro."""
    monkeypatch.setenv("CRON_SECRET", SECRETO)
    get_settings.cache_clear()
    yield SECRETO
    get_settings.cache_clear()


@pytest.fixture
def pasadas():
    """Sustituye el canal de salida y el reloj del endpoint, y los retira al terminar."""
    estado = {"correo": Simulado(), "ahora": INICIO - timedelta(hours=23)}

    def correo(*, fallar: bool = False) -> Simulado:
        estado["correo"] = Simulado(fallar=fallar)
        return estado["correo"]

    def reloj(momento: datetime) -> datetime:
        estado["ahora"] = momento
        return momento

    app.dependency_overrides[get_proveedor_correo] = lambda: estado["correo"]
    app.dependency_overrides[get_ahora] = lambda: estado["ahora"]
    yield SimpleNamespace(correo=correo, reloj=reloj)
    app.dependency_overrides.pop(get_proveedor_correo, None)
    app.dependency_overrides.pop(get_ahora, None)


async def escribir(consulta: str, **params) -> None:
    """Escribir directo en agenda_test.

    No sirve `sql_filas`: está pensada para leer, recorre el resultset (un INSERT no devuelve filas)
    y no hace commit, así que lo que se insertara por ahí desaparecería al cerrar la sesión.
    """
    from sqlalchemy import text

    from app.api import deps

    async with deps.SessionLocal() as session:
        await session.execute(text(consulta), params)
        await session.commit()


async def paciente(
    doctor_id: str, *, nombre: str = "Ana Ibars", telefono: str | None = None, email: str | None = None
) -> str:
    pid = str(uuid.uuid4())
    await escribir(
        "insert into patients (id, doctor_id, nombre, telefono, email) "
        "values (cast(:id as uuid), cast(:d as uuid), :nombre, :telefono, :email)",
        id=pid,
        d=doctor_id,
        nombre=nombre,
        telefono=telefono,
        email=email,
    )
    return pid


async def con_cita(
    doctor_id: str,
    *,
    inicio_utc: datetime = INICIO,
    email: str | None = "ana@example.com",
    servicio_id: str | None = None,
    intentos: int = 0,
    estado: str = "confirmada",
) -> str:
    """Cita en la BD con su paciente, sin pasar por la reserva: el despacho no reserva nada."""
    pid = await paciente(doctor_id, email=email)
    cita_id = str(uuid.uuid4())
    await escribir(
        "insert into appointments (id, doctor_id, patient_id, service_id, fecha_hora, duracion_minutos,"
        " estado, consentimiento_ok_en, recordatorio_intentos)"
        " values (cast(:id as uuid), cast(:d as uuid), cast(:p as uuid), cast(:s as uuid),"
        " :inicio, 30, :estado, :inicio, :intentos)",
        id=cita_id,
        d=doctor_id,
        p=pid,
        s=servicio_id,
        inicio=inicio_utc,
        estado=estado,
        intentos=intentos,
    )
    return cita_id


async def ejecutar(client: AsyncClient, secreto: str = SECRETO):
    return await client.post("/api/v1/internal/recordatorios", headers={"x-cron-secret": secreto})


async def estado_de(cita_id: str) -> tuple[bool, bool, bool, bool, int]:
    filas = await sql_filas(
        "select recordatorio_24h_enviado, recordatorio_24h_enviado_en is not null,"
        " recordatorio_1h_enviado, recordatorio_1h_enviado_en is not null, recordatorio_intentos"
        " from appointments where id = cast(:c as uuid)",
        c=cita_id,
    )
    return filas[0]


async def test_sin_secreto_configurado_el_endpoint_esta_apagado(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CRON_SECRET", "")
    get_settings.cache_clear()
    resp = await ejecutar(client, SECRETO)
    assert resp.status_code == 503
    assert "CRON_SECRET" in resp.json()["detail"]


async def test_sin_cabecera_o_con_secreto_falso_no_pasa_nada(client, doctor, cron_secret, pasadas):
    await con_cita(doctor["id"])
    correo = pasadas.correo()
    assert (await client.post("/api/v1/internal/recordatorios")).status_code == 401
    assert (await ejecutar(client, "secreto-equivocado")).status_code == 401
    assert correo.enviados == []


async def test_la_pasada_manda_el_aviso_de_24h_y_deja_constancia(client, doctor, cron_secret, pasadas):
    correo = pasadas.correo()
    cita_id = await con_cita(doctor["id"])

    resumen = (await ejecutar(client)).json()
    assert resumen["proveedor"] == "simulado"
    assert (resumen["candidatas"], resumen["enviados"], resumen["enviados_24h"]) == (1, 1, 1)
    assert correo.enviados[0].para == "ana@example.com"
    assert "09:00" in correo.enviados[0].asunto

    # La bandera y su fecha van juntas: sin fecha no se puede responder a "¿le avisé de esto?".
    enviado_24h, con_fecha, enviado_1h, _, intentos = await estado_de(cita_id)
    assert (enviado_24h, con_fecha, enviado_1h, intentos) == (True, True, False, 0)


async def test_la_segunda_pasada_no_vuelve_a_escribirle(client, doctor, cron_secret, pasadas):
    correo = pasadas.correo()
    await con_cita(doctor["id"])
    await ejecutar(client)
    resumen = (await ejecutar(client)).json()
    assert (resumen["enviados"], resumen["planificados"]) == (0, 0)
    assert len(correo.enviados) == 1


async def test_el_aviso_de_una_hora_sale_el_ultimo_dia(client, doctor, cron_secret, pasadas):
    correo = pasadas.correo()
    pasadas.reloj(INICIO - timedelta(minutes=50))
    cita_id = await con_cita(doctor["id"])

    resumen = (await ejecutar(client)).json()
    assert (resumen["enviados"], resumen["enviados_1h"]) == (1, 1)
    assert "09:00" in correo.enviados[0].asunto
    _, _, enviado_1h, con_fecha, _ = await estado_de(cita_id)
    assert (enviado_1h, con_fecha) == (True, True)


async def test_el_texto_usa_el_telefono_guardado_del_doctor(client, doctor, cron_secret, pasadas):
    correo = pasadas.correo()
    await escribir(
        "update doctors set telefono = :t where id = cast(:d as uuid)", t="+34 611 00 00 00", d=doctor["id"]
    )
    await con_cita(doctor["id"])
    await ejecutar(client)
    assert "+34 611 00 00 00" in correo.enviados[0].cuerpo


async def test_sin_email_en_la_ficha_no_hay_destino(client, doctor, cron_secret, pasadas):
    correo = pasadas.correo()
    cita_id = await con_cita(doctor["id"], email=None)

    resumen = (await ejecutar(client)).json()
    assert (resumen["candidatas"], resumen["sin_correo"], resumen["enviados"]) == (1, 1, 0)
    assert correo.enviados == []
    assert await estado_de(cita_id) == (False, False, False, False, 0)


async def test_a_las_cuatro_de_la_madrugada_no_escribe_a_nadie(client, doctor, cron_secret, pasadas):
    correo = pasadas.correo()
    cita_id = await con_cita(doctor["id"], inicio_utc=INICIO_MADRUGADA)
    pasadas.reloj(INICIO_MADRUGADA - timedelta(hours=24))  # adelanto válido, hora local 04:00

    resumen = (await ejecutar(client)).json()
    assert resumen["enviados"] == 0 and correo.enviados == []
    # Queda pendiente, no descartada: la pasada de las 08:00 sí podrá avisar.
    assert await estado_de(cita_id) == (False, False, False, False, 0)


async def test_un_fallo_del_proveedor_deja_la_bandera_sin_marcar(client, doctor, cron_secret, pasadas):
    pasadas.correo(fallar=True)
    cita_id = await con_cita(doctor["id"])

    resumen = (await ejecutar(client)).json()
    assert (resumen["planificados"], resumen["enviados"], resumen["errores"]) == (1, 0, 1)
    assert await estado_de(cita_id) == (False, False, False, False, 1)


async def test_tras_tres_fallos_se_deja_de_intentar(client, doctor, cron_secret, pasadas):
    correo = pasadas.correo()
    await con_cita(doctor["id"], intentos=MAX_INTENTOS)

    resumen = (await ejecutar(client)).json()
    assert (resumen["agotados"], resumen["enviados"], resumen["errores"]) == (1, 0, 0)
    assert correo.enviados == []


async def test_dos_pasadas_simultaneas_mandan_un_solo_correo(client, doctor, cron_secret, pasadas):
    """El advisory lock por doctor: el cron duplicado no le escribe dos veces al paciente."""
    correo = pasadas.correo()
    cita_id = await con_cita(doctor["id"])

    respuestas = await asyncio.gather(ejecutar(client), ejecutar(client))
    assert [r.status_code for r in respuestas] == [200, 200]
    assert len(correo.enviados) == 1
    assert sum(r.json()["enviados"] for r in respuestas) == 1
    assert await estado_de(cita_id) == (True, True, False, False, 0)


async def test_un_huso_guardado_invalido_no_escribe(client, doctor, cron_secret, pasadas):
    await escribir(
        "update doctors set timezone = :z where id = cast(:d as uuid)", z="Marte/Olympus", d=doctor["id"]
    )
    await con_cita(doctor["id"])
    resumen = (await ejecutar(client)).json()
    assert (resumen["husos_invalidos"], resumen["enviados"]) == (1, 0)


async def test_una_cita_cancelada_no_se_avisa(client, doctor, cron_secret, pasadas):
    correo = pasadas.correo()
    await con_cita(doctor["id"], estado="cancelada")
    resumen = (await ejecutar(client)).json()
    assert (resumen["candidatas"], resumen["enviados"]) == (0, 0)
    assert correo.enviados == []


async def test_el_resumen_no_devuelve_datos_del_paciente(client, doctor, cron_secret, pasadas):
    await con_cita(doctor["id"])
    resp = await ejecutar(client)
    assert "ana@example.com" not in resp.text
    assert "Ana Ibars" not in resp.text


async def test_la_ruta_interna_no_sale_en_la_documentacion_publica(client):
    openapi = (await client.get("/openapi.json")).json()
    assert "/api/v1/internal/recordatorios" not in openapi["paths"]


async def test_un_doctor_nuevo_no_tiene_nada_que_avisar(client, cron_secret, pasadas):
    await registrar(client, DOCTOR_GARCIA)
    resumen = (await ejecutar(client)).json()
    assert (resumen["candidatas"], resumen["enviados"]) == (0, 0)
