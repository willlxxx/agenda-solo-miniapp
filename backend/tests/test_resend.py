"""El canal Resend: la API HTTPS de envíos que se puede montar desde esta cuenta.

Sustituye a Brevo por un motivo de cuenta y no de código: el alta de Brevo no dejó verificar el buzón
desde Venezuela, y sin remitente verificado el envío muere en un 400. Resend abre por el mismo 443 --el
egress de SMTP de Railway sigue cortado fuera del plan Pro, así que el `smtp` del doctor no vale-- y cobra
otra cosa: el dominio del "De:" tiene que estar verificado en su panel, con lo que un Gmail de consulta
queda fuera. Este archivo prueba lo que ese cambio significa:

- **la llamada**: HTTPS contra `api.resend.com/emails`, la clave como `Authorization: Bearer`, el cuerpo con
  `from`/`to`/`subject`/`text` y el nombre del remitente dentro de la misma cadena.
- **los rechazos**: 400, 401, red cortada y tiempo límite se convierten siempre en `ErrorEnvio` con el
  motivo del proveedor dentro, y ni una vez la clave escrita en el mensaje.
- **la promesa**: `disponible`, el constructor del proveedor y el guardia de arranque preguntan las tres a
  `canal_elegido`, así que `listo=True` sin clave puesta no puede volver a pasar.

Nada de esto toca la red ni la cuenta de nadie: el transporte es `httpx.MockTransport` (en `tests/conftest.py`).
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
    URL_DE_RESEND,
    Canal,
    Correo,
    ErrorEnvio,
    Resend,
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

# Con el prefijo `re_` va una cadena de mentira: ninguna clave real pasa por un test.
CLAVE = "re_clave-de-prueba-que-no-es-de-nadie"
REMITENTE = "avisos@clinica.example"
UN_CORREO = Correo(para="paciente@example.com", asunto="Su cita mañana", cuerpo="A las 09:00.")
ORIGEN = "https://clinica.example"


def resend(transporte: httpx.AsyncBaseTransport, **kwargs) -> Resend:
    return Resend(api_key=CLAVE, de=REMITENTE, nombre_remitente="Dra. García", transporte=transporte, **kwargs)


def _cuerpo_de(pedido: httpx.Request) -> dict:
    return json.loads(pedido.content)


# --- la llamada: es la API de Resend, por HTTPS y con la clave en la cabecera ---


async def test_la_salida_va_a_la_api_https_de_resend_con_la_clave_como_portador():
    transporte, visto = transporte_devolviendo(status_code=201, cuerpo='{"id": "9c5b1"}')
    await resend(transporte).enviar(UN_CORREO)

    pedido = visto["pedido"]
    assert str(pedido.url) == URL_DE_RESEND
    assert pedido.url.scheme == "https", "la clave viaja en una cabecera: con http iría en claro"
    assert pedido.method == "POST"
    assert pedido.headers["authorization"] == f"Bearer {CLAVE}"
    assert pedido.headers["accept"] == "application/json"


async def test_el_cuerpo_es_el_formato_de_resend_texto_llano_y_un_destinatario():
    """Resend pide el destinatario como lista de cadenas y el "De:" como una sola cadena, no en dos campos."""
    transporte, visto = transporte_devolviendo(status_code=201)
    await resend(transporte).enviar(UN_CORREO)

    assert _cuerpo_de(visto["pedido"]) == {
        "from": "Dra. García <avisos@clinica.example>",
        "to": ["paciente@example.com"],
        "subject": "Su cita mañana",
        "text": "A las 09:00.",
    }


@pytest.mark.parametrize(
    "nombre,de,esperado",
    [
        ("", REMITENTE, "avisos@clinica.example"),
        ("Consulta X", REMITENTE, "Consulta X <avisos@clinica.example>"),
        ("Consulta X", "Consulta Ya Puesta <avisos@clinica.example>", "Consulta Ya Puesta <avisos@clinica.example>"),
    ],
)
async def test_el_remitente_se_forma_una_vez_y_no_se_doble(nombre, de, esperado):
    """`RESEND_DE` con su nombre escrito a mano no puede acabar en `A <B> <C>`: Resend lo rechazaría."""
    transporte, visto = transporte_devolviendo(status_code=201)
    await Resend(api_key=CLAVE, de=de, nombre_remitente=nombre, transporte=transporte).enviar(UN_CORREO)
    assert _cuerpo_de(visto["pedido"])["from"] == esperado


async def test_un_200_tambien_es_entregado():
    """No se fija el código exacto: lo que se promete es que un 2xx no cuente como fallo."""
    transporte, _ = transporte_devolviendo(status_code=200, cuerpo='{"id": "9c5b1"}')
    await resend(transporte).enviar(UN_CORREO)


async def test_el_nombre_del_proveedor_es_resend():
    """Lo lee la cara pública para decir quién manda el aviso, y lo usa el log de la recuperación."""
    assert Resend(api_key=CLAVE, de=REMITENTE).nombre == "resend"


# --- los rechazos: siempre `ErrorEnvio`, nunca un `httpx` suelto que rompa la pasada del cron ---


async def test_el_dominio_sin_verificar_se_explica_en_el_error():
    """El 400 que más va a ver este despliegue: el "De:" es de un dominio no verificado en Resend."""
    transporte, _ = transporte_devolviendo(
        status_code=403,
        cuerpo='{"name": "validation_error", "message": "domain not verified: clinica.example"}',
    )
    with pytest.raises(ErrorEnvio) as error:
        await resend(transporte).enviar(UN_CORREO)
    texto = str(error.value)
    assert "403" in texto
    assert "domain not verified" in texto
    assert "paciente@example.com" in texto


async def test_un_429_de_resend_no_se_disfraza_de_exito():
    """El plan libre limita a 10 solicitudes por segundo: un rechazo seguido sigue siendo rechazo."""
    transporte, _ = transporte_devolviendo(
        status_code=429, cuerpo='{"name": "rate_limit_exceeded", "message": "Too many requests"}'
    )
    with pytest.raises(ErrorEnvio) as error:
        await resend(transporte).enviar(UN_CORREO)
    assert "Too many requests" in str(error.value), "gana el `message`, que es lo que se puede actuar"


async def test_si_resend_solo_trae_el_nombre_del_fallo_se_lee_ese():
    """No todos sus rechazos traen `message`; quedarse en `HTTP 403` pelado no sirve para arreglarlo."""
    transporte, _ = transporte_devolviendo(status_code=403, cuerpo='{"name": "invalid_api_key"}')
    with pytest.raises(ErrorEnvio) as error:
        await resend(transporte).enviar(UN_CORREO)
    assert "invalid_api_key" in str(error.value)


async def test_la_clave_incorrecta_se_dice_como_rechazo_no_como_red():
    transporte, _ = transporte_devolviendo(status_code=401, cuerpo='{"message": "Unauthorized"}')
    with pytest.raises(ErrorEnvio) as error:
        await Resend(api_key="re_rota", de=REMITENTE, transporte=transporte).enviar(UN_CORREO)
    assert "Unauthorized" in str(error.value)
    assert "re_rota" not in str(error.value), "el secreto no se escribe en el log ni en `error_envio`"


async def test_un_cuerpo_que_no_es_json_no_deja_el_mensaje_en_nada():
    transporte, _ = transporte_devolviendo(
        status_code=500, cuerpo="<html>gateway subiendo</html>", contenido="text/html"
    )
    with pytest.raises(ErrorEnvio) as error:
        await resend(transporte).enviar(UN_CORREO)
    assert "gateway subiendo" in str(error.value)


async def test_una_red_cortada_es_error_envio_no_una_excepcion_de_httpx():
    """El despacho y la recuperación cuentan sus reintentos sobre `ErrorEnvio`; otra cosa mataría la pasada."""
    proveedor = resend(transporte_reventando(httpx.ConnectError("[Errno 101] Network is unreachable")))
    with pytest.raises(ErrorEnvio) as error:
        await proveedor.enviar(UN_CORREO)
    assert "Network is unreachable" in str(error.value)
    assert CLAVE not in str(error.value)


async def test_una_respuesta_lenta_tampoco_se_cuela_como_exito():
    with pytest.raises(ErrorEnvio):
        await resend(transporte_reventando(httpx.TimeoutException("tardó demasiado"))).enviar(UN_CORREO)


# --- qué canal dice `.env` y qué le falta ---


def test_resend_solo_dice_que_le_falta_lo_que_de_verdad_le_falta():
    assert canal_elegido(
        Settings(_env_file=None, recordatorios_proveedor="resend", resend_api_key=CLAVE, resend_de=REMITENTE)
    ) == ("resend", [])
    assert canal_elegido(Settings(_env_file=None, recordatorios_proveedor="resend"))[1] == [
        "RESEND_API_KEY",
        "RESEND_DE",
    ]


def test_el_proveedor_resend_lleva_la_clave_y_el_remitente_recortados(monkeypatch):
    con_ajustes(
        monkeypatch,
        recordatorios_proveedor="resend",
        resend_api_key=f"  {CLAVE}  ",
        resend_de=f"  {REMITENTE}  ",
        resend_nombre="Dra. García",
    )
    proveedor = proveedor_desde_configuracion()
    assert isinstance(proveedor, Resend)
    assert proveedor.api_key == CLAVE
    assert proveedor.de == REMITENTE
    assert proveedor.nombre_remitente == "Dra. García"
    assert proveedor.transporte is None, "en producción no se finge el transporte"


def test_un_resend_a_medias_se_niega_en_el_constructor_y_no_en_la_pantalla(monkeypatch):
    """`listo=False` para la cara pública y `ValueError` para el cron: la misma comprobación, dos lecturas."""
    con_ajustes(monkeypatch, recordatorios_proveedor="resend", resend_api_key=CLAVE)
    assert not canal_desde_configuracion().listo
    with pytest.raises(ValueError) as error:
        proveedor_desde_configuracion()
    assert "RESEND_DE" in str(error.value)


# --- un Resend que rechaza, dentro del flujo de recuperación real ---


@pytest.fixture
def canal_resend():
    """El canal de salida puesto como lo estaría en el despliegue, y retirado al terminar."""

    def poner(transporte: httpx.AsyncBaseTransport) -> None:
        app.dependency_overrides[get_proveedor_correo] = lambda: resend(transporte)
        app.dependency_overrides[get_canal_correo] = lambda: Canal(
            nombre="resend", listo=True, origen_enlace=ORIGEN
        )

    yield poner
    for dep in (get_proveedor_correo, get_canal_correo):
        app.dependency_overrides.pop(dep, None)


async def pedir_enlace(client: AsyncClient, email: str = DOCTOR_GARCIA["email"]):
    return await client.post("/api/v1/auth/recuperar/solicitar", json={"email": email})


async def test_resend_rechazado_deja_el_motivo_en_la_base_y_sigue_diciendo_202(client, doctor, canal_resend):
    """Ni se le promete el enlace a quien pregunta, ni se filtra el motivo del proveedor al público.

    Sobre PostgreSQL real: `error_envio` es lo que el fundador va a leer cuando el aviso no llegue, y tiene
    que decir que el dominio no está verificado en vez de quedarse en un número.
    """
    transporte, _ = transporte_devolviendo(
        status_code=403, cuerpo='{"message": "domain not verified: clinica.example"}'
    )
    canal_resend(transporte)

    respuesta = await pedir_enlace(client)
    assert respuesta.status_code == 202
    assert respuesta.json() == {"aceptada": True}, "la respuesta pública no cambia según el resultado"

    motivo = (await sql_filas("select error_envio from password_resets limit 1"))[0][0]
    assert motivo is not None
    assert "403" in motivo
    assert "domain not verified" in motivo
    assert CLAVE not in motivo, "la API key no puede acabar en una tabla"


async def test_resend_que_si_entrega_deja_la_fila_sin_motivo(client, doctor, canal_resend):
    transporte, _ = transporte_devolviendo(status_code=201, cuerpo='{"id": "9c5b1"}')
    canal_resend(transporte)

    assert (await pedir_enlace(client)).status_code == 202
    fila = (await sql_filas("select error_envio, usado_en from password_resets limit 1"))[0]
    assert fila == (None, None), "un enlace entregado no deja motivo ni marca de uso"


# --- el guardia de arranque con el canal nuevo ---


def _produccion(**kwargs) -> Settings:
    return Settings(entorno="produccion", jwt_secret="x" * 43, cron_secret="y", _env_file=None, **kwargs)


def test_resend_sin_clave_avisa_pero_levanta(caplog):
    from app.arranque import revisar_arranque

    with caplog.at_level(logging.WARNING, logger="agenda.arranque"):
        revisar_arranque(
            _produccion(
                recordatorios_proveedor="resend",
                resend_de=REMITENTE,
                frontend_origin="https://agenda.example",
            )
        )
    assert "RESEND_API_KEY" in caplog.text


def test_resend_listo_con_dominio_real_arranca_mudo(caplog):
    from app.arranque import revisar_arranque

    with caplog.at_level(logging.WARNING, logger="agenda.arranque"):
        revisar_arranque(
            _produccion(
                recordatorios_proveedor="resend",
                resend_api_key=CLAVE,
                resend_de=REMITENTE,
                frontend_origin="https://agenda.example",
            )
        )
    assert caplog.text == "", "un despliegue bien montado tiene que salir mudo"


def test_resend_listo_pero_con_enlaces_a_localhost_avisa(caplog):
    """Los correos salen y nadie puede abrirlos: el enlace apunta a la máquina del despliegue."""
    from app.arranque import revisar_arranque

    with caplog.at_level(logging.WARNING, logger="agenda.arranque"):
        revisar_arranque(
            _produccion(
                recordatorios_proveedor="resend",
                resend_api_key=CLAVE,
                resend_de=REMITENTE,
                frontend_origin="http://localhost:5173",
            )
        )
    assert "FRONTEND_ORIGIN" in caplog.text
