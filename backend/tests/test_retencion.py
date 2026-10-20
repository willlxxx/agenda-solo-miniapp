"""Retención: cuántos meses guarda el consultorio sus citas cerradas y qué se lleva el barrido.

Dos capas, como en recordatorios, porque el riesgo está en dos sitios distintos:

- el **dominio puro** (`app/domain/retencion.py`) con fechas fijas: restar meses es calendario, no
  aritmética de segundos, y ahí se decide si se borra de más o de menos;
- la **pasada contra PostgreSQL real** con el reloj sustituido: aquí importa la escritura, el orden
  (citas y peticiones antes que fichas), que `simular` no deje ni una fila menos y que un consultorio
  no borre el histórico de otro.

Nada de esto puede depender del día en que corre el CI: la pasada entera se prueba con `ahora`
fijo, y las fechas de las filas se escriben a mano en el test.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api.deps import get_ahora
from app.config import get_settings
from app.domain.retencion import (
    ESTADOS_CERRADOS,
    MESES_MAX,
    MESES_MIN,
    cita_eliminable,
    explicacion,
    ficha_eliminable,
    normalizar_meses,
    peticion_eliminable,
    restar_meses,
    umbral,
)
from app.main import app
from app.models import UNIQUE_CONFIRMADA
from tests.conftest import DOCTOR_LOPEZ, registrar, sql_filas

# Lunes 19 de octubre de 2026 a las 12:00 UTC. Con 12 meses de retención el umbral cae en el 19 de
# octubre de 2025: todo lo cerrado de antes se va, todo lo de después se queda.
AHORA = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
VIEJA = datetime(2025, 6, 1, 9, 0, tzinfo=UTC)  # fuera del plazo de 12 meses
DENTRO = datetime(2026, 2, 19, 9, 0, tzinfo=UTC)  # hace 8 meses: dentro de 12, fuera de 6
FUTURA = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
ALTA_VIEJA = datetime(2024, 1, 15, 9, 0, tzinfo=UTC)
SECRETO = "secreto-de-cron-de-prueba"


# --- dominio puro ---


def test_la_banda_de_meses_es_la_que_declara_el_proyecto():
    assert (MESES_MIN, MESES_MAX) == (6, 12)
    assert normalizar_meses(3) == MESES_MIN
    assert normalizar_meses(99) == MESES_MAX
    assert normalizar_meses(9) == 9


def test_un_valor_que_no_es_entero_no_se_normaliza_sino_que_se_rechaza():
    """Un `True` valdría como 1 en Python; aquí eso significaría borrar con el plazo mínimo por error."""
    for malo in (True, False, "12", 12.0, None):
        with pytest.raises(TypeError):
            normalizar_meses(malo)


def test_restar_meses_es_calendario_y_no_treinta_dias():
    assert restar_meses(datetime(2026, 3, 31, 12, tzinfo=UTC), 1) == datetime(2026, 2, 28, 12, tzinfo=UTC)
    # El 29 de febrero no existe al año siguiente: se recorta, no revienta.
    assert restar_meses(datetime(2024, 2, 29, 12, tzinfo=UTC), 12) == datetime(2023, 2, 28, 12, tzinfo=UTC)
    assert restar_meses(datetime(2026, 10, 19, 12, tzinfo=UTC), 12) == datetime(2025, 10, 19, 12, tzinfo=UTC)
    assert restar_meses(AHORA, 0) == AHORA


def test_el_umbral_mantiene_el_huso_y_aplica_la_banda():
    limite = umbral(AHORA, 24)  # fuera de banda: se normaliza a 12 antes de restar
    assert limite == datetime(2025, 10, 19, 12, tzinfo=limite.tzinfo)
    assert str(limite.tzinfo) == "UTC"


def test_solo_los_estados_cerrados_salen_del_borrado():
    """Los tres estados cerrados, ni uno más: `confirmada` es la que no ha terminado aún."""
    assert len(ESTADOS_CERRADOS) == 3
    limite = umbral(AHORA, 12)
    assert [cita_eliminable(estado, VIEJA, limite) for estado in ESTADOS_CERRADOS] == [True] * 3
    assert cita_eliminable("confirmada", VIEJA, limite) is False
    assert cita_eliminable("completada", FUTURA, limite) is False


def test_la_lista_de_estados_del_dominio_es_la_que_guarda_la_base_de_datos():
    """Si alguien añade un estado de cita en el modelo, la retención tiene que declararlo.

    Sin esta comprobación el estado nuevo se quedaría fuera de `ESTADOS_CERRADOS` por defecto y sus
    citas no se borrarían nunca: un dato que se conserva indefinidamente sin que nadie lo decida.
    """
    from app.models import ESTADOS_CITA

    assert set(ESTADOS_CITA) - set(ESTADOS_CERRADOS) == {UNIQUE_CONFIRMADA}


def test_una_cancelacion_reciente_todavia_no_se_borra():
    """La antigüedad manda: el hecho de que esté cerrada no la suelta antes de su plazo."""
    assert cita_eliminable("cancelada", DENTRO, umbral(AHORA, 12)) is False


def test_una_ficha_solo_se_va_cuando_ya_no_tiene_nada_detras():
    limite = umbral(AHORA, 12)
    assert ficha_eliminable(ALTA_VIEJA, limite, citas_restantes=0, peticiones_restantes=0) is True
    assert ficha_eliminable(ALTA_VIEJA, limite, citas_restantes=1, peticiones_restantes=0) is False
    assert ficha_eliminable(ALTA_VIEJA, limite, citas_restantes=0, peticiones_restantes=1) is False
    # Recién creada y sin nada detrás: sigue dentro de plazo, no la toca el barrido.
    assert ficha_eliminable(DENTRO, limite, citas_restantes=0, peticiones_restantes=0) is False


def test_una_peticion_de_lista_vieja_se_va_aunque_siga_en_espera():
    limite = umbral(AHORA, 12)
    assert peticion_eliminable(ALTA_VIEJA, limite) is True
    assert peticion_eliminable(DENTRO, limite) is False


def test_la_explicacion_dice_el_plazo_y_lo_que_no_toca():
    texto = explicacion(9)
    assert "9 meses" in texto
    assert "no se tocan" in texto
    assert explicacion(99).startswith("Las citas cerradas de hace más de 12 meses")


# --- la pasada, sobre la base de datos real ---


@pytest.fixture
def cron_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("CRON_SECRET", SECRETO)
    get_settings.cache_clear()
    yield SECRETO
    get_settings.cache_clear()


@pytest.fixture
def reloj():
    """Sustituye el reloj del endpoint: la pasada decide con esta hora, no con la del CI."""
    estado = {"ahora": AHORA}
    app.dependency_overrides[get_ahora] = lambda: estado["ahora"]

    def fijar(momento: datetime) -> datetime:
        estado["ahora"] = momento
        return momento

    yield SimpleNamespace(fijar=fijar)
    app.dependency_overrides.pop(get_ahora, None)


async def escribir(consulta: str, **params) -> None:
    from sqlalchemy import text

    from app.api import deps

    async with deps.SessionLocal() as session:
        await session.execute(text(consulta), params)
        await session.commit()


async def ficha(doctor_id: str, *, nombre: str, creado_en: datetime, telefono: str | None = None) -> str:
    identificador = str(uuid.uuid4())
    await escribir(
        "insert into patients (id, doctor_id, nombre, telefono, email, creado_en)"
        " values (cast(:id as uuid), cast(:d as uuid), :nombre, :telefono, :email, :creado)",
        id=identificador,
        d=doctor_id,
        nombre=nombre,
        telefono=telefono or f"+34 600 {identificador[:3]}",
        email=None,
        creado=creado_en,
    )
    return identificador


async def cita(
    doctor_id: str,
    *,
    estado: str,
    cuando: datetime,
    paciente_id: str | None = None,
) -> str:
    identificador = str(uuid.uuid4())
    await escribir(
        "insert into appointments (id, doctor_id, patient_id, fecha_hora, duracion_minutos, estado,"
        " consentimiento_ok_en)"
        " values (cast(:id as uuid), cast(:d as uuid), cast(:p as uuid), :cuando, 30, :estado, :cuando)",
        id=identificador,
        d=doctor_id,
        p=paciente_id,
        cuando=cuando,
        estado=estado,
    )
    return identificador


async def peticion(doctor_id: str, *, creado_en: datetime, paciente_id: str | None = None) -> str:
    identificador = str(uuid.uuid4())
    await escribir(
        "insert into waitlist (id, doctor_id, patient_id, fecha, preferencia_horaria, estado, creado_en)"
        " values (cast(:id as uuid), cast(:d as uuid), cast(:p as uuid),"
        " cast(:fecha as date), 'manana', 'en_espera', :creado)",
        id=identificador,
        d=doctor_id,
        p=paciente_id,
        fecha=creado_en.date(),
        creado=creado_en,
    )
    return identificador


async def barrer(client, *, simular: bool = False, secreto: str = SECRETO):
    url = "/api/v1/internal/limpieza" + ("?simular=true" if simular else "")
    return await client.post(url, headers={"x-cron-secret": secreto})


async def contar(tabla: str, **condicion) -> int:
    if condicion:
        claves = ", ".join(f"{campo} = cast(:{campo} as uuid)" for campo in condicion)
        filas = await sql_filas(f"select count(*) from {tabla} where {claves}", **condicion)
    else:
        filas = await sql_filas(f"select count(*) from {tabla}")
    return int(filas[0][0])


async def test_una_cita_cerrada_fuera_de_plazo_desaparece(client, doctor, cron_secret, reloj):
    await cita(doctor["id"], estado="completada", cuando=VIEJA)
    resumen = (await barrer(client)).json()
    assert (resumen["citas_eliminadas"], resumen["doctores"]) == (1, 1)
    assert await contar("appointments") == 0


async def test_lo_que_no_esta_cerrado_o_dentro_de_plazo_se_queda(client, doctor, cron_secret, reloj):
    vieja_pendiente = await cita(doctor["id"], estado="confirmada", cuando=VIEJA)
    reciente_cerrada = await cita(doctor["id"], estado="completada", cuando=DENTRO)
    futura = await cita(doctor["id"], estado="confirmada", cuando=FUTURA)

    resumen = (await barrer(client)).json()
    assert resumen["citas_eliminadas"] == 0
    # Las tres siguen donde estaban: la pendiente no se cierra sola y la cerrada aún está en plazo.
    assert [await contar("appointments", id=cita_id) for cita_id in (vieja_pendiente, reciente_cerrada, futura)] == [
        1,
        1,
        1,
    ]


async def test_la_pasada_avisa_de_las_confirmadas_cuyo_dia_ya_paso(client, doctor, cron_secret, reloj):
    """El cron no cierra citas por el médico, pero le dice cuántas lleva sueltas.

    Sin este contador, la retención parecería culpable de un vacío: el médico marcaría "me borró la
    agenda" algo que en realidad es una cita que nadie cerró nunca.
    """
    await cita(doctor["id"], estado="confirmada", cuando=VIEJA)
    await cita(doctor["id"], estado="confirmada", cuando=FUTURA)
    resumen = (await barrer(client)).json()
    assert (resumen["confirmadas_sin_cerrar"], resumen["citas_eliminadas"]) == (1, 0)


async def test_la_ficha_de_un_historico_ya_borrado_se_va_con_el(client, doctor, cron_secret, reloj):
    """Aquí es donde la retención cumple el principio de minimización: no queda nombre sin cita."""
    paciente = await ficha(doctor["id"], nombre="Ana Vieja", creado_en=ALTA_VIEJA)
    await cita(doctor["id"], estado="completada", cuando=VIEJA, paciente_id=paciente)

    resumen = (await barrer(client)).json()
    assert (resumen["citas_eliminadas"], resumen["fichas_eliminadas"], resumen["fichas_en_uso"]) == (1, 1, 0)
    assert await contar("patients") == 0 and await contar("appointments") == 0


async def test_una_ficha_con_cita_pendiente_no_se_toca(client, doctor, cron_secret, reloj):
    paciente = await ficha(doctor["id"], nombre="Berta Viva", creado_en=ALTA_VIEJA)
    await cita(doctor["id"], estado="confirmada", cuando=FUTURA, paciente_id=paciente)
    await cita(doctor["id"], estado="completada", cuando=VIEJA, paciente_id=paciente)

    resumen = (await barrer(client)).json()
    assert (resumen["citas_eliminadas"], resumen["fichas_eliminadas"], resumen["fichas_en_uso"]) == (1, 0, 1)
    assert await contar("patients", id=paciente) == 1


async def test_la_lista_de_espera_vieja_tambien_se_barra(client, doctor, cron_secret, reloj):
    """Una petición de hace dos años no es demanda: es un teléfono guardado sin finalidad."""
    await peticion(doctor["id"], creado_en=ALTA_VIEJA)
    await peticion(doctor["id"], creado_en=DENTRO)

    resumen = (await barrer(client)).json()
    assert resumen["peticiones_eliminadas"] == 1
    assert await contar("waitlist") == 1


async def test_simular_da_las_mismas_cuentas_y_no_borra_nada(client, doctor, cron_secret, reloj):
    paciente = await ficha(doctor["id"], nombre="Carmen Ensayo", creado_en=ALTA_VIEJA)
    await cita(doctor["id"], estado="completada", cuando=VIEJA, paciente_id=paciente)
    await peticion(doctor["id"], creado_en=ALTA_VIEJA)

    simulada = (await barrer(client, simular=True)).json()
    assert (simulada["simulada"], simulada["citas_eliminadas"], simulada["fichas_eliminadas"]) == (True, 1, 1)
    assert await contar("appointments") == 1 and await contar("patients") == 1 and await contar("waitlist") == 1

    # Y la pasada real, justo después, borra exactamente lo que anunció el ensayo.
    real = (await barrer(client)).json()
    assert (real["simulada"], real["citas_eliminadas"], real["fichas_eliminadas"]) == (False, 1, 1)
    assert await contar("appointments") == 0 and await contar("patients") == 0


async def test_cada_consultorio_manda_con_su_propio_plazo(client, doctor, cron_secret, reloj):
    """El plazo es del consultorio, y la pasada lo respeta fila a fila.

    Ocho meses: dentro de la retención de 12 de García, fuera de los 6 de Lopez. Si la limpieza
    usara un umbral global, las dos agendas se borrarían entre sí.
    """
    Lopez = await registrar(client, DOCTOR_LOPEZ)
    await escribir(
        "update doctors set retencion_meses = 6 where id = cast(:d as uuid)", d=Lopez["id"]
    )
    await cita(doctor["id"], estado="completada", cuando=DENTRO)
    await cita(Lopez["id"], estado="completada", cuando=DENTRO)

    resumen = (await barrer(client)).json()
    assert (resumen["doctores"], resumen["citas_eliminadas"]) == (2, 1)


async def test_un_doctor_nuevo_no_pierde_nada(client, cron_secret, reloj):
    await registrar(client)
    assert (await barrer(client)).json()["citas_eliminadas"] == 0


async def test_dos_pasadas_simultaneas_no_cuentan_dos_veces_el_mismo_borrado(client, doctor, cron_secret, reloj):
    await cita(doctor["id"], estado="no_vino", cuando=VIEJA)
    respuestas = await asyncio.gather(barrer(client), barrer(client))
    assert [r.status_code for r in respuestas] == [200, 200]
    assert sum(r.json()["citas_eliminadas"] for r in respuestas) == 1


async def test_el_resumen_no_devuelve_ni_un_nombre(client, doctor, cron_secret, reloj):
    paciente = await ficha(doctor["id"], nombre="Dolores Secreta", creado_en=ALTA_VIEJA, telefono="+34 611 99 88 77")
    await cita(doctor["id"], estado="completada", cuando=VIEJA, paciente_id=paciente)
    respuesta = await barrer(client)
    assert "Dolores Secreta" not in respuesta.text and "+34 611 99 88 77" not in respuesta.text


async def test_sin_secreto_configurado_la_limpieza_esta_apagada(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CRON_SECRET", "")
    get_settings.cache_clear()
    await cita((await registrar(client))["id"], estado="completada", cuando=VIEJA)
    resp = await barrer(client, secreto=SECRETO)
    assert resp.status_code == 503
    assert "CRON_SECRET" in resp.json()["detail"]
    assert await contar("appointments") == 1


async def test_sin_cabecera_o_con_secreto_falso_no_se_borra_nada(client, doctor, cron_secret, reloj):
    await cita(doctor["id"], estado="completada", cuando=VIEJA)
    assert (await client.post("/api/v1/internal/limpieza")).status_code == 401
    assert (await barrer(client, secreto="secreto-equivocado")).status_code == 401
    assert await contar("appointments") == 1


async def test_la_ruta_de_borrado_no_sale_en_la_documentacion_publica(client):
    openapi = (await client.get("/openapi.json")).json()
    assert "/api/v1/internal/limpieza" not in openapi["paths"]


# --- el plazo lo elige el médico desde su panel ---


async def test_el_panel_lee_su_plazo_con_la_explicacion(client, doctor):
    respuesta = (await client.get("/api/v1/retencion")).json()
    assert respuesta["meses"] == 12
    assert "12 meses" in respuesta["explicacion"]


async def test_el_panel_puede_bajar_el_plazo_y_la_limpieza_lo_usa(client, doctor, cron_secret, reloj):
    await cita(doctor["id"], estado="completada", cuando=DENTRO)
    assert (await barrer(client)).json()["citas_eliminadas"] == 0  # con 12 meses, de momento se queda

    respuesta = await client.put("/api/v1/retencion", json={"meses": 6})
    assert respuesta.json()["meses"] == 6
    assert (await barrer(client)).json()["citas_eliminadas"] == 1


async def test_un_plazo_fuera_de_la_banda_se_rechaza(client, doctor):
    for meses in (1, 5, 13, 36):
        assert (await client.put("/api/v1/retencion", json={"meses": meses})).status_code == 422
    assert (await client.get("/api/v1/retencion")).json()["meses"] == 12


async def test_sin_sesion_no_se_lee_ni_se_cambia_el_plazo(client):
    assert (await client.get("/api/v1/retencion")).status_code == 401
    assert (await client.put("/api/v1/retencion", json={"meses": 6})).status_code == 401
