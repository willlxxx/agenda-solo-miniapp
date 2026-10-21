"""Recuperación del acceso: pedir el enlace, consumir la clave nueva y no contar cuentas.

Dos capas, como en los recordatorios:

- el **dominio puro** (`app/domain/recuperacion.py`) con valores metidos a mano: la forma del enlace,
  su caducidad y el texto del correo, que es lo que no puede depender del reloj de la máquina;
- los **endpoints contra PostgreSQL real**, con el canal y el reloj sustituidos: aquí lo que se prueba
  es la escritura (fila con el hash, bandera de uso, limpieza de las demás) y las dos promesas que le
  importan al médico: que un pedido no revele si su correo tiene agenda, y que un enlace gastado no
  vuelva a abrir la puerta.

Nada de esto abre un SMTP: el proveedor que se inyecta es `Simulado`, que guarda lo que habría mandado
y deja leer el enlace. Las credenciales reales no entran en ninguna prueba.
"""

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from app.api.deps import get_ahora, get_canal_correo, get_proveedor_correo
from app.config import get_settings
from app.domain.recuperacion import (
    CADUCIDAD,
    ESPERA_ENTRE_PEDIDOS,
    MINUTOS_DE_VIGENCIA,
    demasiado_pronto,
    disponible_recuperacion,
    enlace_de_recuperacion,
    es_origen_local,
    hash_del_token,
    mensaje_recuperacion,
    nuevo_token,
    sigue_vigente,
)
from app.main import app
from app.services.correo import Canal, Simulado
from tests.conftest import DOCTOR_GARCIA, sql_filas

# Una mañana de martes, dentro de la franja en la que la agenda escribe. El valor concreto no
# importa para la recuperación, pero sí que sea fijo: los minutos de vigencia se afirman aquí.
AHORA = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
ORIGEN = "https://clinica.example"
CLAVE_NUEVA = "otra-clave-de-prueba-9"

# --- dominio puro ---


def test_dos_tokens_nunca_iguales_y_suficientemente_largos():
    a, b = nuevo_token(), nuevo_token()
    assert a != b
    assert len(a) >= 40
    # 32 bytes url-safe no traen caracteres que una URL no soporte sin escapar.
    assert re.fullmatch(r"[A-Za-z0-9_-]+", a)


def test_el_hash_es_estable_y_no_deja_salir_el_token():
    token = "ABC123_" * 6
    assert hash_del_token(token) == hash_del_token(token)
    assert token not in hash_del_token(token)
    assert len(hash_del_token(token)) == 64


def test_el_token_va_en_el_fragmento_no_en_la_query():
    enlace = enlace_de_recuperacion(f"{ORIGEN}/", "TOKENZITO")
    assert enlace == f"{ORIGEN}/recuperar#token=TOKENZITO"
    # Lo que se registra en un log de accesos es lo que hay antes de la almohadilla.
    assert "TOKENZITO" not in enlace.split("#")[0]


def test_origenes_locales_se_reconocen():
    assert es_origen_local("http://localhost:5173")
    assert es_origen_local("http://127.0.0.1:8000")
    assert es_origen_local("")
    assert not es_origen_local(ORIGEN)


def test_un_enlace_caduca_y_se_gasta():
    expira = AHORA + CADUCIDAD
    assert sigue_vigente(expira, None, AHORA)
    assert sigue_vigente(expira, None, expira - timedelta(seconds=1))
    assert not sigue_vigente(expira, None, expira)
    assert not sigue_vigente(expira, AHORA, AHORA)


def test_el_cooldown_es_el_que_dice_la_constante():
    assert demasiado_pronto(AHORA, AHORA + ESPERA_ENTRE_PEDIDOS - timedelta(milliseconds=1))
    assert not demasiado_pronto(AHORA, AHORA + ESPERA_ENTRE_PEDIDOS)


def test_el_mensaje_dice_el_enlace_los_minutos_y_quien_lo_manda():
    enlace = enlace_de_recuperacion(ORIGEN, "TOKENZITO")
    asunto, cuerpo = mensaje_recuperacion("Dra. García Peña", enlace)
    assert "recuperar" in asunto.lower()
    assert enlace in cuerpo
    assert str(MINUTOS_DE_VIGENCIA) in cuerpo
    assert "solo una vez" in cuerpo
    # Declarado, no escondido: el enlace lo manda la propia agenda por su canal de avisos.
    assert "Te lo envía la propia agenda" in cuerpo


def test_la_disponibilidad_tiene_tres_casos_y_solo_uno_es_no():
    assert disponible_recuperacion(canal_listo=True, es_desarrollo=False) == (True, None)
    # En local hace falta poder recorrer el flujo entero sin credenciales de nadie.
    assert disponible_recuperacion(canal_listo=False, es_desarrollo=True) == (True, "simulado_en_desarrollo")
    # El estado del despliegue de hoy: sin canal, la respuesta honesta es no ofrecer el botón.
    assert disponible_recuperacion(canal_listo=False, es_desarrollo=False) == (False, "sin_canal_de_correo")


# --- endpoints contra la base real ---


@pytest.fixture
def canal_y_reloj():
    """Canal, proveedor y reloj del endpoint, con el proveedor simulado capturando los correos."""
    estado = {
        "proveedor": Simulado(),
        "canal": Canal(nombre="smtp", listo=True, origen_enlace=ORIGEN),
        "ahora": AHORA,
    }

    def proveedor(*, fallar: bool = False) -> Simulado:
        estado["proveedor"] = Simulado(fallar=fallar)
        return estado["proveedor"]

    def canal(**cambios) -> Canal:
        estado["canal"] = replace(estado["canal"], **cambios)
        return estado["canal"]

    def reloj(momento: datetime) -> datetime:
        estado["ahora"] = momento
        return momento

    app.dependency_overrides[get_proveedor_correo] = lambda: estado["proveedor"]
    app.dependency_overrides[get_canal_correo] = lambda: estado["canal"]
    app.dependency_overrides[get_ahora] = lambda: estado["ahora"]
    yield SimpleNamespace(proveedor=proveedor, canal=canal, reloj=reloj, estado=estado)
    for dep in (get_proveedor_correo, get_canal_correo, get_ahora):
        app.dependency_overrides.pop(dep, None)


@pytest.fixture
def produccion(monkeypatch):
    """El entorno de producción, solo para el caso en que la respuesta honesta es un 503."""
    monkeypatch.setenv("ENTORNO", "produccion")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def pedir(client: AsyncClient, email: str):
    return await client.post("/api/v1/auth/recuperar/solicitar", json={"email": email})


def token_del(correo) -> str:
    """El token tal como lo vería el médico: el que va después de la almohadilla en el cuerpo."""
    return re.search(r"#token=([A-Za-z0-9_-]+)", correo.cuerpo).group(1)


async def filas_tokens() -> list[tuple]:
    return await sql_filas("select token_hash, usado_en, error_envio from password_resets")


async def entrar(client: AsyncClient, clave: str) -> int:
    """Intento de acceso normal, para afirmar que la clave cambió de verdad."""
    resp = await client.post(
        "/api/v1/auth/acceso", json={"email": DOCTOR_GARCIA["email"], "password": clave}
    )
    return resp.status_code


async def confirmar(client: AsyncClient, *, token: str, nueva: str):
    """Consumir un enlace. Se repite en casi todas las pruebas y la URL ya no cabe en la línea."""
    return await client.post("/api/v1/auth/recuperar/confirmar", json={"token": token, "nueva": nueva})


async def test_solicitar_manda_un_correo_con_enlace_y_guarda_el_hash(client, doctor, canal_y_reloj):
    capturado = canal_y_reloj.proveedor()
    resp = await pedir(client, DOCTOR_GARCIA["email"])
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"aceptada": True}

    assert len(capturado.enviados) == 1
    correo = capturado.enviados[0]
    assert correo.para == DOCTOR_GARCIA["email"]
    assert f"{ORIGEN}/recuperar#token=" in correo.cuerpo

    filas = await filas_tokens()
    assert len(filas) == 1
    assert filas[0][0] == hash_del_token(token_del(correo))
    assert filas[0][1] is None and filas[0][2] is None
    # El token en claro no está en ninguna columna de la tabla.
    demas = await sql_filas("select token_hash from password_resets")
    assert all(token_del(correo) not in fila[0] for fila in demas)


async def test_un_email_ajeno_no_deja_fila_ni_correo(client, doctor, canal_y_reloj):
    capturado = canal_y_reloj.proveedor()
    resp = await pedir(client, "nadie@example.com")
    assert resp.status_code == 202
    assert await filas_tokens() == []
    assert capturado.enviados == []


async def test_la_respuesta_no_distingue_entre_cuenta_y_sin_cuenta(client, doctor, canal_y_reloj):
    capturado = canal_y_reloj.proveedor()
    con_cuenta = await pedir(client, DOCTOR_GARCIA["email"])
    enviados_existente = len(capturado.enviados)
    sin_cuenta = await pedir(client, "nadie@example.com")
    assert con_cuenta.status_code == sin_cuenta.status_code == 202
    assert con_cuenta.json() == sin_cuenta.json()
    # Y el lado visible tampoco: el número de correos cambia por el cooldown, no por la cuenta.
    assert len(capturado.enviados) == enviados_existente


async def test_dos_pedidos_seguidos_mandan_un_solo_correo(client, doctor, canal_y_reloj):
    capturado = canal_y_reloj.proveedor()
    await pedir(client, DOCTOR_GARCIA["email"])
    canal_y_reloj.reloj(AHORA + ESPERA_ENTRE_PEDIDOS - timedelta(seconds=1))
    await pedir(client, DOCTOR_GARCIA["email"])
    assert len(capturado.enviados) == 1
    assert len(await filas_tokens()) == 1

    # Pasada la espera, el médico que sigue sin recibir el enlace sí puede volver a pedirlo.
    canal_y_reloj.reloj(AHORA + ESPERA_ENTRE_PEDIDOS + timedelta(seconds=1))
    await pedir(client, DOCTOR_GARCIA["email"])
    assert len(capturado.enviados) == 2
    assert len(await filas_tokens()) == 2


async def test_confirmar_cambia_la_clave_y_gasta_el_enlace(client, doctor, canal_y_reloj):
    capturado = canal_y_reloj.proveedor()
    await pedir(client, DOCTOR_GARCIA["email"])
    token = token_del(capturado.enviados[0])

    resp = await confirmar(client, token=token, nueva=CLAVE_NUEVA)
    assert resp.status_code == 204, resp.text
    assert resp.content == b""

    vieja = await entrar(client, DOCTOR_GARCIA["password"])
    assert vieja == 401
    assert await entrar(client, CLAVE_NUEVA) == 200

    # Gastado: el mismo enlace ya no sirve, ni para poner otra clave distinta.
    repetido = await confirmar(client, token=token, nueva="una-clave-más-larga")
    assert repetido.status_code == 400
    assert "Ese enlace no sirve" in repetido.json()["detail"]

    filas = await filas_tokens()
    assert len(filas) == 1 and filas[0][1] is not None
    # El hash sigue siendo argon2id y no contiene la clave.
    [guardado] = await sql_filas("select password_hash from doctors")
    assert guardado[0].startswith("$argon2id$")
    assert CLAVE_NUEVA not in guardado[0]


async def test_un_enlace_caducado_no_cambia_nada(client, doctor, canal_y_reloj):
    capturado = canal_y_reloj.proveedor()
    await pedir(client, DOCTOR_GARCIA["email"])
    token = token_del(capturado.enviados[0])

    canal_y_reloj.reloj(AHORA + CADUCIDAD)
    resp = await confirmar(client, token=token, nueva=CLAVE_NUEVA)
    assert resp.status_code == 400
    assert await entrar(client, DOCTOR_GARCIA["password"]) == 200


async def test_un_token_inventado_da_el_mismo_mensaje_que_uno_caduco(client, doctor, canal_y_reloj):
    falso = await confirmar(client, token=nuevo_token(), nueva=CLAVE_NUEVA)
    assert falso.status_code == 400
    assert "Ese enlace no sirve" in falso.json()["detail"]


async def test_al_consumir_un_enlace_se_borran_los_demas_del_mismo_consultorio(
    client, doctor, canal_y_reloj
):
    capturado = canal_y_reloj.proveedor()
    await pedir(client, DOCTOR_GARCIA["email"])
    canal_y_reloj.reloj(AHORA + ESPERA_ENTRE_PEDIDOS + timedelta(seconds=1))
    await pedir(client, DOCTOR_GARCIA["email"])
    assert len(await filas_tokens()) == 2

    # Se consume el primero: el segundo, que sigue sin usar, se va con él.
    token = token_del(capturado.enviados[0])
    await confirmar(client, token=token, nueva=CLAVE_NUEVA)
    filas = await filas_tokens()
    assert len(filas) == 1

    segundo = token_del(capturado.enviados[1])
    quemado = await confirmar(client, token=segundo, nueva="otra-clave-mas-larga-2")
    assert quemado.status_code == 400


async def test_una_pasada_de_correo_falla_pero_no_lo_cuenta_como_exito(client, doctor, canal_y_reloj):
    fallido = canal_y_reloj.proveedor(fallar=True)
    resp = await pedir(client, DOCTOR_GARCIA["email"])
    assert resp.status_code == 202
    assert fallido.enviados == []
    [fila] = await filas_tokens()
    assert fila[2] is not None and "Proveedor simulado en modo fallo" in fila[2]

    # El token queda registrado, no tirado: con un canal que ya funciona se puede volver a pedir.
    canal_y_reloj.reloj(AHORA + ESPERA_ENTRE_PEDIDOS + timedelta(seconds=1))
    bueno = canal_y_reloj.proveedor()
    await pedir(client, DOCTOR_GARCIA["email"])
    assert len(bueno.enviados) == 1
    assert len(await filas_tokens()) == 2


async def test_sin_canal_de_correo_en_produccion_la_respuesta_es_honesta(client, doctor, canal_y_reloj, produccion):
    canal_y_reloj.canal(listo=False)
    disponible = await client.get("/api/v1/auth/recuperar/disponible")
    assert disponible.status_code == 200
    assert disponible.json() == {"disponible": False, "razon": "sin_canal_de_correo"}

    resp = await pedir(client, DOCTOR_GARCIA["email"])
    assert resp.status_code == 503
    assert "todavía no puede mandar correos" in resp.json()["detail"]
    # Y el intento no escribe nada: sin canal no hay enlace que pueda caducar.
    assert await filas_tokens() == []


async def test_con_canal_listo_la_pantalla_sabe_que_puede_ofrecer(client, doctor, canal_y_reloj):
    disponible = await client.get("/api/v1/auth/recuperar/disponible")
    assert disponible.json() == {"disponible": True, "razon": None}


async def test_en_desarrollo_sin_canal_se_ofrece_pero_se_dice_que_es_simulado(
    client, doctor, canal_y_reloj
):
    """El caso que permite probar el flujo en local sin credenciales: se ofrece, y se dice por qué."""
    canal_y_reloj.canal(nombre="simulado", listo=False)
    disponible = await client.get("/api/v1/auth/recuperar/disponible")
    assert disponible.json() == {"disponible": True, "razon": "simulado_en_desarrollo"}
    assert (await pedir(client, DOCTOR_GARCIA["email"])).status_code == 202


async def test_confirmar_exige_una_clave_de_ocho_caracteres(client, doctor, canal_y_reloj):
    cort = await client.post("/api/v1/auth/recuperar/confirmar", json={"token": nuevo_token(), "nueva": "corta"})
    assert cort.status_code == 422


async def test_solicitar_exige_un_email_con_forma(client, doctor, canal_y_reloj):
    resp = await pedir(client, "no-es-un-email")
    assert resp.status_code == 422


async def test_solicitar_puede_repetirse_despues_de_un_cambio_de_clave(client, doctor, canal_y_reloj):
    """Recuperar dos veces en la vida de la cuenta: el pedido nuevo no lo corta el token gastado."""
    capturado = canal_y_reloj.proveedor()
    await pedir(client, DOCTOR_GARCIA["email"])
    await client.post(
        "/api/v1/auth/recuperar/confirmar",
        json={"token": token_del(capturado.enviados[0]), "nueva": CLAVE_NUEVA},
    )
    canal_y_reloj.reloj(AHORA + ESPERA_ENTRE_PEDIDOS + timedelta(seconds=1))
    resp = await pedir(client, DOCTOR_GARCIA["email"])
    assert resp.status_code == 202
    assert len(capturado.enviados) == 2


async def test_el_token_gastado_no_se_convierte_en_historial(client, doctor, canal_y_reloj):
    """Un enlace ya usado no se queda pudriendo en la tabla a la espera del siguiente pedido."""
    capturado = canal_y_reloj.proveedor()
    await pedir(client, DOCTOR_GARCIA["email"])
    await client.post(
        "/api/v1/auth/recuperar/confirmar",
        json={"token": token_del(capturado.enviados[0]), "nueva": CLAVE_NUEVA},
    )
    canal_y_reloj.reloj(AHORA + ESPERA_ENTRE_PEDIDOS + timedelta(seconds=1))
    await pedir(client, DOCTOR_GARCIA["email"])
    # La fila que queda es la nueva: la gastada se borró al crearla.
    [fila] = await filas_tokens()
    assert fila[1] is None
