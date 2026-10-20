"""El guardia de arranque: lo que evita que un despliegue público se levante a medias.

Pruebas puras, sin base de datos ni servidor. Se construye `Settings` a mano con `_env_file=None`
para que el `.env` de la máquina del que las corre no cambie el resultado.
"""

import logging
from types import SimpleNamespace

import pytest
from fastapi.responses import Response

from app.arranque import revisar_arranque
from app.config import SECRETO_DE_DESARROLLO, Settings
from app.security import set_session_cookie

SECRETO_BUENO = "x" * 43  # lo que sale de `secrets.token_urlsafe(32)`


def _produccion(**kwargs) -> Settings:
    return Settings(entorno="produccion", jwt_secret=SECRETO_BUENO, _env_file=None, **kwargs)


def test_desarrollo_levanta_con_los_secretos_de_desarrollo():
    """Ni un aviso: si no, el log del dev server sería ruido y nadie lo leería nunca."""
    revisar_arranque(Settings(_env_file=None))


def test_en_produccion_el_jwt_del_repositorio_no_arranca():
    s = Settings(entorno="produccion", jwt_secret=SECRETO_DE_DESARROLLO, _env_file=None)
    with pytest.raises(RuntimeError) as error:
        revisar_arranque(s)
    assert "JWT_SECRET" in str(error.value)


def test_un_jwt_corto_tampoco_arranca():
    s = Settings(entorno="produccion", jwt_secret="todavia-mas-corto", _env_file=None)
    with pytest.raises(RuntimeError) as error:
        revisar_arranque(s)
    assert "caracteres" in str(error.value)


def test_copiar_el_env_ejemplo_tal_cual_tampoco_arranca():
    """Es el fallo del despliegue de quince minutos: se cambia ENTORNO y se deja el ejemplo puesto."""
    ejemplo_del_env = "cambia-esto-en-produccion-por-al-menos-32-bytes-aleatorios"
    with pytest.raises(RuntimeError) as error:
        revisar_arranque(Settings(entorno="produccion", jwt_secret=ejemplo_del_env, _env_file=None))
    assert "JWT_SECRET" in str(error.value)


def test_un_sqlite_en_produccion_no_arranca():
    """En local SQLite va; en producción dos reservas simultáneas se pisan sin advisory lock."""
    with pytest.raises(RuntimeError) as error:
        revisar_arranque(_produccion(database_url="sqlite+aiosqlite:///agenda.db"))
    assert "PostgreSQL" in str(error.value)


def test_produccion_bien_configurada_arranca_sin_quejarse():
    revisar_arranque(
        _produccion(
            cron_secret="un-secreto-de-cron-largo",
            recordatorios_proveedor="smtp",
            smtp_host="smtp.example.com",
            smtp_de="clinica@example.com",
        )
    )


@pytest.mark.parametrize("entorno", ["produccion", "producción", "PRODUCCION", " prod ", "prod"])
def test_como_se_escriba_produccion_se_activan_las_comprobaciones(entorno):
    """El acento y las mayúsculas no pueden ser un motivo para quedarse sin comprobaciones."""
    s = Settings(entorno=entorno, jwt_secret=SECRETO_DE_DESARROLLO, _env_file=None)
    with pytest.raises(RuntimeError) as error:
        revisar_arranque(s)
    assert "JWT_SECRET" in str(error.value), entorno


@pytest.mark.parametrize("entorno", ["production", "producccion", "", "desarrollos"])
def test_un_typo_en_entorno_es_fallo_grave_no_un_silencio(entorno):
    """'production' vacío o mal escrito no puede significar 'esto no es producción, apaga todo'."""
    s = Settings(entorno=entorno, jwt_secret=SECRETO_BUENO, _env_file=None)
    with pytest.raises(RuntimeError) as error:
        revisar_arranque(s)
    assert "ENTORNO" in str(error.value), entorno


def test_sin_cron_secret_avisa_pero_levanta(caplog):
    """El día uno puede ser solo agenda; un 503 en las rutas internas no es motivo para no arrancar."""
    with caplog.at_level(logging.WARNING, logger="agenda.arranque"):
        revisar_arranque(_produccion())
    assert "CRON_SECRET" in caplog.text
    assert "503" in caplog.text


def test_smtp_a_medias_avisa_pero_levanta(caplog):
    with caplog.at_level(logging.WARNING, logger="agenda.arranque"):
        revisar_arranque(_produccion(cron_secret="x", recordatorios_proveedor="smtp"))
    assert "SMTP_HOST" in caplog.text


def test_un_proveedor_inventado_se_avisa(caplog):
    with caplog.at_level(logging.WARNING, logger="agenda.arranque"):
        revisar_arranque(_produccion(cron_secret="x", recordatorios_proveedor="mailgun"))
    assert "mailgun" in caplog.text


def test_en_desarrollo_no_se_avisa_de_nada(caplog):
    """Un log limpio al arrancar es lo que hace que se lea cuando sí hay algo."""
    with caplog.at_level(logging.WARNING, logger="agenda.arranque"):
        revisar_arranque(Settings(cron_secret=None, _env_file=None))
    assert caplog.text == ""


def test_la_cookie_lleva_secure_solo_en_produccion(monkeypatch):
    """Antes colgaba de FRONTEND_ORIGIN; desde que API e interfaz son un solo origen, eso ya no dice nada."""
    monkeypatch.setattr("app.security.get_settings", lambda: SimpleNamespace(jwt_ttl_dias=30, es_produccion=False))
    respuesta = Response()
    set_session_cookie(respuesta, "token")
    assert "secure" not in respuesta.headers["set-cookie"].lower()

    monkeypatch.setattr("app.security.get_settings", lambda: SimpleNamespace(jwt_ttl_dias=30, es_produccion=True))
    respuesta = Response()
    set_session_cookie(respuesta, "token")
    cabecera = respuesta.headers["set-cookie"].lower()
    assert "secure" in cabecera
    assert "httponly" in cabecera
    assert "samesite=lax" in cabecera
