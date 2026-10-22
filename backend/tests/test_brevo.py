"""El canal Brevo: la API HTTPS que sustituye al SMTP cuando el host no deja salir el correo.

Nace de un fallo de producción concreto --`SMTP no pudo entregar … [Errno 101] Network is unreachable`
desde Railway, donde el egress de SMTP está apagado fuera del plan Pro-- y de la lección que dejó: la
pantalla prometía un enlace de recuperación que nunca salía, porque `listo` miraba otras variables que
las que el envío necesita. De ahí las tres capas de este archivo:

- **la llamada**: que sea HTTPS contra la API de Brevo, con la clave en la cabecera y el cuerpo en su
  formato, y que cualquier rechazo --400, 401, red cortada-- se convierta en `ErrorEnvio` con la
  explicación del proveedor dentro. Ninguna prueba toca la red ni la cuenta de nadie: el transporte es
  `httpx.MockTransport`.
- **la decisión compartida**: `canal_elegido` es la única pregunta que responden `/recuperar/disponible`,
  el constructor del proveedor y el guardia de arranque, así que no pueden volver a contradecirse.
- **el contrato con la recuperación**: un Brevo que rechaza deja el motivo en `password_resets.error_envio`
  y la respuesta pública sigue siendo un 202.

El andamio (`transporte_devolviendo`, `con_ajustes`) está en `tests/conftest.py` porque `Resend` es el mismo
contrato con otro formato: ver `tests/test_resend.py`.
"""

import json
import logging

import httpx
import pytest
from httpx import AsyncClient

from app.api.deps import get_canal_correo, get_proveedor_correo
from app.config import Settings
from app.main import app
from app.services.correo import (
    URL_DE_BREVO,
    Brevo,
    Canal,
    Correo,
    ErrorEnvio,
    Resend,
    Smtp,
    canal_desde_configuracion,
    canal_elegido,
    proveedor_desde_configuracion,
)
from tests.conftest import (
    DOCTOR_GARCIA,
    con_ajustes,
    sql_filas,
    transporte_devolviendo,
    transporte_reventando,
)

CLAVE = "xkeysib-una-clave-de-prueba-que-no-es-de-nadie"
UN_CORREO = Correo(para="paciente@example.com", asunto="Su cita mañana", cuerpo="A las 09:00.")
ORIGEN = "https://clinica.example"


def brevo(transporte: httpx.AsyncBaseTransport) -> Brevo:
    return Brevo(api_key=CLAVE, de="doctor@clinic.example", nombre_remitente="Dra. García", transporte=transporte)


# --- la llamada: es la API de Brevo, por HTTPS y con la clave en la cabecera ---


async def test_la_salida_va_a_la_api_https_de_brevo_con_la_clave_en_la_cabecera():
    transporte, visto = transporte_devolviendo(status_code=201, cuerpo='{"messageId": "abc"}')
    await brevo(transporte).enviar(UN_CORREO)

    pedido = visto["pedido"]
    assert str(pedido.url) == URL_DE_BREVO
    assert pedido.url.scheme == "https", "con la clave en una cabecera, un http la mandaría en claro"
    assert pedido.method == "POST"
    assert pedido.headers["x-api-key"] == CLAVE
    assert pedido.headers["accept"] == "application/json"


async def test_el_cuerpo_es_el_formato_de_brevo_texto_llano_y_un_destinatario():
    transporte, visto = transporte_devolviendo(status_code=201)
    await brevo(transporte).enviar(UN_CORREO)

    assert json.loads(visto["pedido"].content) == {
        "sender": {"name": "Dra. García", "email": "doctor@clinic.example"},
        "to": [{"email": "paciente@example.com"}],
        "subject": "Su cita mañana",
        "textContent": "A las 09:00.",
    }


async def test_sin_nombre_de_remitente_se_presta_el_correo():
    """`sender.name` no admite vacío, así que `BREVO_NOMBRE` es opcional de verdad solo si queda nombre."""
    transporte, visto = transporte_devolviendo(status_code=201)
    await Brevo(api_key=CLAVE, de="doctor@clinic.example", transporte=transporte).enviar(UN_CORREO)
    assert json.loads(visto["pedido"].content)["sender"]["name"] == "doctor@clinic.example"


async def test_un_202_tambien_es_entregado():
    """El código exacto no es contrato; lo que se promete es que un 2xx no se cuente como fallo."""
    transporte, _ = transporte_devolviendo(status_code=202)
    await brevo(transporte).enviar(UN_CORREO)


async def test_el_nombre_del_proveedor_es_brevo():
    """Lo lee la cara pública para decir quién manda el aviso, y lo usa el log de la recuperación."""
    assert Brevo(api_key=CLAVE, de="doctor@clinic.example").nombre == "brevo"


# --- los rechazos: siempre `ErrorEnvio`, nunca un `httpx` suelto que rompa la pasada del cron ---


async def test_brevo_rechaza_y_su_explicacion_llega_hasta_el_error():
    """El 400 por remitente sin verificar tiene que leerse, no quedarse en un número."""
    transporte, _ = transporte_devolviendo(
        status_code=400,
        cuerpo='{"message": "The sender email is not valid.", "code": "invalid_parameter"}',
    )
    proveedor = Brevo(api_key=CLAVE, de="no-verificado@clinic.example", transporte=transporte)
    with pytest.raises(ErrorEnvio) as error:
        await proveedor.enviar(UN_CORREO)
    assert "400" in str(error.value)
    assert "sender email is not valid" in str(error.value)
    assert "paciente@example.com" in str(error.value)


async def test_la_clave_incorrecta_se_dice_como_rechazo_no_como_red():
    transporte, _ = transporte_devolviendo(status_code=401, cuerpo='{"message": "Invalid API key"}')
    with pytest.raises(ErrorEnvio) as error:
        await Brevo(api_key="clave-rot", de="doctor@clinic.example", transporte=transporte).enviar(UN_CORREO)
    assert "Invalid API key" in str(error.value)
    assert "clave-rot" not in str(error.value), "el secreto no se escribe en el log ni en `error_envio`"


async def test_un_cuerpo_que_no_es_json_no_deja_el_mensaje_en_nada():
    """Un 500 de un proxy trae HTML: el texto crudo sirve más que 'sin respuesta del proveedor'."""
    transporte, _ = transporte_devolviendo(
        status_code=500,
        cuerpo="<html>gateway subiendo</html>",
        contenido="text/html",
    )
    with pytest.raises(ErrorEnvio) as error:
        await brevo(transporte).enviar(UN_CORREO)
    assert "gateway subiendo" in str(error.value)


async def test_una_red_cortada_es_error_envio_no_un_excepcion_de_httpx():
    """El despacho y la recuperación cuentan sus reintentos sobre `ErrorEnvio`; otra cosa mataría la pasada."""
    proveedor = brevo(transporte_reventando(httpx.ConnectError("[Errno 101] Network is unreachable")))
    with pytest.raises(ErrorEnvio) as error:
        await proveedor.enviar(UN_CORREO)
    assert "Network is unreachable" in str(error.value)
    assert CLAVE not in str(error.value)


async def test_una_respuesta_lenta_tampoco_se_cuela_como_exito():
    proveedor = brevo(transporte_reventando(httpx.TimeoutException("tardó demasiado")))
    with pytest.raises(ErrorEnvio):
        await proveedor.enviar(UN_CORREO)


# --- qué canal dice `.env` y qué le falta: la única pregunta que se hace ---


def test_sin_canal_el_elegido_es_simulado_y_no_le_falta_nada():
    assert canal_elegido(Settings(_env_file=None)) == ("simulado", [])


@pytest.mark.parametrize("valor", ["", "none", " Simulado ", "SIMULADO"])
def test_el_simulado_se_puede_escribir_de_varias_formas(valor):
    assert canal_elegido(Settings(_env_file=None, recordatorios_proveedor=valor)) == ("simulado", [])


def test_smtp_a_medias_dice_las_dos_variables_que_faltan():
    _, faltan = canal_elegido(Settings(_env_file=None, recordatorios_proveedor="smtp"))
    assert faltan == ["SMTP_HOST", "SMTP_DE"]


@pytest.mark.parametrize(
    "ajustes_esperados",
    [
        {"brevo_api_key": CLAVE, "brevo_de": "d@e.com"},
        {"brevo_api_key": CLAVE},
        {"brevo_de": "d@e.com"},
        {"brevo_api_key": " ", "brevo_de": " "},
    ],
)
def test_brevo_dice_exactamente_lo_que_le_falta(ajustes_esperados):
    elegido, faltan = canal_elegido(
        Settings(_env_file=None, recordatorios_proveedor="brevo", **ajustes_esperados)
    )
    assert elegido == "brevo"
    assert faltan == [
        nombre
        for nombre, clave in (("BREVO_API_KEY", "brevo_api_key"), ("BREVO_DE", "brevo_de"))
        if not (ajustes_esperados.get(clave) or "").strip()
    ], "los espacios en blanco no cuentan como valor"


def test_un_proveedor_inventado_es_un_error_no_un_simulado_silencioso():
    with pytest.raises(ValueError) as error:
        canal_elegido(Settings(_env_file=None, recordatorios_proveedor="mandarin"))
    assert "mandarin" in str(error.value)


@pytest.mark.parametrize(
    "escrito,esperado",
    [("Brevo", Brevo), ("  resend  ", Resend), ("  smtp  ", Smtp), ("SIMULADO", None)],
)
def test_como_se_escriba_el_canal_en_el_panel_no_cambia_lo_que_se_construye(escrito, esperado, monkeypatch):
    """`Brevo` con la B mayúscula o ` resend ` con espacios es el mismo canal: no se apaga por formato."""
    con_ajustes(
        monkeypatch,
        recordatorios_proveedor=escrito,
        brevo_api_key=CLAVE,
        brevo_de="d@e.com",
        resend_api_key=CLAVE,
        resend_de="d@e.com",
        smtp_host="smtp.example.com",
        smtp_de="d@e.com",
    )
    if esperado is None:
        assert not canal_desde_configuracion().listo
    else:
        assert isinstance(proveedor_desde_configuracion(), esperado)


# --- el proveedor y la promesa pública salen de la misma comprobación ---


def test_el_proveedor_brevo_lleva_la_clave_y_el_remitente_recortados(monkeypatch):
    con_ajustes(
        monkeypatch,
        recordatorios_proveedor="brevo",
        brevo_api_key=f"  {CLAVE}  ",
        brevo_de="  doctor@clinic.example  ",
        brevo_nombre="Dra. García",
    )
    proveedor = proveedor_desde_configuracion()
    assert isinstance(proveedor, Brevo)
    assert proveedor.api_key == CLAVE
    assert proveedor.de == "doctor@clinic.example"
    assert proveedor.nombre_remitente == "Dra. García"
    assert proveedor.transporte is None, "en producción no se finge el transporte"


def test_el_canal_smtp_sigue_construyendola_igual(monkeypatch):
    con_ajustes(
        monkeypatch,
        recordatorios_proveedor="smtp",
        smtp_host="smtp.gmail.com",
        smtp_port=465,
        smtp_de="doctor@gmail.com",
        smtp_ssl=True,
    )
    proveedor = proveedor_desde_configuracion()
    assert isinstance(proveedor, Smtp)
    assert (proveedor.host, proveedor.puerto, proveedor.ssl_directo) == ("smtp.gmail.com", 465, True)


@pytest.mark.parametrize(
    "proveedor,extras,listo,fallo_al_construir",
    [
        ("smtp", {"smtp_host": "smtp.example.com", "smtp_de": "d@e.com"}, True, False),
        ("smtp", {"smtp_de": "d@e.com"}, False, True),
        ("brevo", {"brevo_api_key": CLAVE, "brevo_de": "d@e.com"}, True, False),
        ("brevo", {"brevo_api_key": CLAVE}, False, True),
        ("brevo", {"brevo_de": "d@e.com"}, False, True),
        ("resend", {"resend_api_key": CLAVE, "resend_de": "d@e.com"}, True, False),
        ("resend", {"resend_api_key": CLAVE}, False, True),
        ("resend", {"resend_de": "  "}, False, True),
        ("simulado", {}, False, False),
    ],
)
def test_lo_que_promete_disponible_es_lo_que_el_envio_puede_cumplir(
    monkeypatch, proveedor, extras, listo, fallo_al_construir
):
    """La promesa que se prueba aquí: `listo=True` y fallar al mandar no puede volver a pasar.

    Era el fallo exacto que dejó el buzón vacío: `listo` comprobaba `SMTP_HOST` y `SMTP_DE`, y con eso la
    pantalla afirmaba que el enlace saldría mientras el envío moría al abrir el socket. Ahora las dos
    cosas preguntan a `canal_elegido`.
    """
    con_ajustes(monkeypatch, recordatorios_proveedor=proveedor, **extras)
    assert canal_desde_configuracion().listo is listo
    if fallo_al_construir:
        with pytest.raises(ValueError) as error:
            proveedor_desde_configuracion()
        assert "faltan" in str(error.value)
    else:
        proveedor_desde_configuracion()


def test_el_canal_inventado_no_rompe_la_pantalla_de_acceso(monkeypatch):
    """`/recuperar/disponible` se llama desde el navegador del médico: un typo no puede ser un 500."""
    con_ajustes(monkeypatch, recordatorios_proveedor="telegram")
    assert canal_desde_configuracion() == Canal(
        nombre="simulado", listo=False, origen_enlace="http://localhost:5173"
    )


# --- un Brevo que rechaza, dentro del flujo de recuperación real ---


@pytest.fixture
def canal_brevo():
    """El canal de salida puesto como lo estaría en el despliegue, y retirado al terminar."""
    def poner(transporte: httpx.AsyncBaseTransport) -> None:
        app.dependency_overrides[get_proveedor_correo] = lambda: brevo(transporte)
        app.dependency_overrides[get_canal_correo] = lambda: Canal(
            nombre="brevo", listo=True, origen_enlace=ORIGEN
        )

    yield poner
    for dep in (get_proveedor_correo, get_canal_correo):
        app.dependency_overrides.pop(dep, None)


async def pedir_enlace(client: AsyncClient, email: str = DOCTOR_GARCIA["email"]):
    return await client.post("/api/v1/auth/recuperar/solicitar", json={"email": email})


async def test_brevo_rechazado_deja_el_motivo_en_la_base_y_sigue_diciendo_202(client, doctor, canal_brevo):
    """Ni se le promete el enlace a quien pregunta, ni se filtra el motivo del proveedor al público.

    Sobre PostgreSQL real y con el transporte de mentira: `error_envio` es lo que el fundador va a leer en
    la tabla cuando el aviso no llegue, y tiene que explicar el rechazo en vez de ser un `HTTP 400` pelado.
    """
    transporte, _ = transporte_devolviendo(
        status_code=400, cuerpo='{"message": "The sender email is not valid."}'
    )
    canal_brevo(transporte)

    respuesta = await pedir_enlace(client)
    assert respuesta.status_code == 202
    assert respuesta.json() == {"aceptada": True}, "la respuesta pública no cambia según el resultado"

    motivo = (await sql_filas("select error_envio from password_resets limit 1"))[0][0]
    assert motivo is not None
    assert "400" in motivo
    assert "sender email is not valid" in motivo
    assert CLAVE not in motivo, "la API key no puede acabar en una tabla"


async def test_brevo_que_si_entrega_deja_la_fila_sin_motivo(client, doctor, canal_brevo):
    transporte, _ = transporte_devolviendo(status_code=201, cuerpo='{"messageId": "abc"}')
    canal_brevo(transporte)

    assert (await pedir_enlace(client)).status_code == 202
    fila = (await sql_filas("select error_envio, usado_en from password_resets limit 1"))[0]
    assert fila == (None, None), "un enlace entregado no deja motivo ni marca de uso"


# --- el guardia de arranque con el canal nuevo ---


def _produccion(**kwargs) -> Settings:
    return Settings(entorno="produccion", jwt_secret="x" * 43, cron_secret="y", _env_file=None, **kwargs)


def test_brevo_sin_clave_avisa_pero_levanta(caplog):
    from app.arranque import revisar_arranque

    with caplog.at_level(logging.WARNING, logger="agenda.arranque"):
        revisar_arranque(
            _produccion(
                recordatorios_proveedor="brevo",
                brevo_de="doctor@clinic.example",
                frontend_origin="https://agenda.example",
            )
        )
    assert "BREVO_API_KEY" in caplog.text


def test_brevo_listo_con_dominio_real_arranca_mudo(caplog):
    from app.arranque import revisar_arranque

    with caplog.at_level(logging.WARNING, logger="agenda.arranque"):
        revisar_arranque(
            _produccion(
                recordatorios_proveedor="brevo",
                brevo_api_key=CLAVE,
                brevo_de="doctor@clinic.example",
                frontend_origin="https://agenda.example",
            )
        )
    assert caplog.text == "", "un despliegue bien montado tiene que salir mudo"


def test_brevo_listo_pero_con_enlaces_a_localhost_avisa(caplog):
    """Los correos salen y nadie puede abrirlos: el enlace apunta a la máquina del despliegue."""
    from app.arranque import revisar_arranque

    with caplog.at_level(logging.WARNING, logger="agenda.arranque"):
        revisar_arranque(
            _produccion(
                recordatorios_proveedor="brevo",
                brevo_api_key=CLAVE,
                brevo_de="doctor@clinic.example",
                frontend_origin="http://localhost:5173",
            )
        )
    assert "FRONTEND_ORIGIN" in caplog.text


def test_un_canal_inventado_se_avisa_y_se_usa_el_simulado(caplog):
    from app.arranque import revisar_arranque

    with caplog.at_level(logging.WARNING, logger="agenda.arranque"):
        revisar_arranque(
            _produccion(recordatorios_proveedor="whatsapp", frontend_origin="https://agenda.example")
        )
    assert "whatsapp" in caplog.text
    assert "ningún correo sale" in caplog.text
