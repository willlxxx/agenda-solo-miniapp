"""El sondeo del `HEALTHCHECK` de la imagen: que diga «la agenda contesta», no «algo contesta».

Pruebas puras, sin red, con el mismo truco que `test_guion_cron.py`: `scripts/salud.py` no es un paquete
importable, es un guion del despliegue, así que se carga por ruta y se sustituye `urlopen`. Lo que se
prueba aquí son las dos cosas que el sondeo tiene que acertar: el puerto (`PORT` lo pone el host; en el
despliegue real de Railway fue 8080 y el convenio local es 8000) y el cuerpo (la interfaz se sirve con
*fallback*, o sea que una ruta rara devuelve el HTML de la SPA con un 200 encima).
"""

import importlib.util
import urllib.error
from pathlib import Path

import pytest


def _cargar_guion():
    ruta = Path(__file__).resolve().parents[2] / "scripts" / "salud.py"
    espec = importlib.util.spec_from_file_location("sondeo_de_salud", ruta)
    modulo = importlib.util.module_from_spec(espec)
    espec.loader.exec_module(modulo)
    return modulo


salud = _cargar_guion()


class RespuestaFalsa:
    """Lo que devuelve `urlopen`: estado, cuerpo y poder usarse con `with`."""

    def __init__(self, estado=200, cuerpo=b'{"estado":"ok"}'):
        self.estado = estado
        self._cuerpo = cuerpo

    @property
    def status(self):
        return self.estado

    def read(self, *_):
        return self._cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *_) -> bool:
        return False


@pytest.fixture
def red_falsa(monkeypatch):
    """Sustituye `urlopen` por un falso y deja elegir qué contesta (o qué falla)."""

    class Vistas(list):
        def __init__(self) -> None:
            super().__init__()
            self.caja = {"respuesta": None, "error": None}

    vistas = Vistas()

    def falso_urlopen(url, timeout=None):
        vistas.append((url, timeout))
        if vistas.caja["error"] is not None:
            raise vistas.caja["error"]
        return vistas.caja["respuesta"] or RespuestaFalsa()

    monkeypatch.setattr(salud.urllib.request, "urlopen", falso_urlopen)
    return vistas


def test_sin_port_el_convenio_local_es_8000(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    assert salud.url_a_sondear() == "http://127.0.0.1:8000/healthz"


def test_el_puerto_del_host_manda_en_el_sondeo(monkeypatch):
    """El caso real: Railway inyectó `PORT=8080` y la app escuchó ahí, no en 8000."""
    monkeypatch.setenv("PORT", "8080")
    assert salud.url_a_sondear() == "http://127.0.0.1:8080/healthz"


def test_un_port_vacio_no_deja_el_sondeo_sin_puerto(monkeypatch):
    monkeypatch.setenv("PORT", "")
    assert salud.url_a_sondear().endswith(":8000/healthz")


def test_el_200_con_su_estado_ok_es_saludable(red_falsa):
    assert salud.saludable("http://127.0.0.1:8000/healthz") is True
    url, timeout = red_falsa[0]
    assert url == "http://127.0.0.1:8000/healthz"
    assert timeout == salud.TIMEOUT


def test_un_200_con_el_html_del_fallback_no_lo_es(red_falsa):
    """La SPA conteste con 200 a cualquier ruta: si el sondeo solo mirara el estado, aprobaría."""
    red_falsa.caja["respuesta"] = RespuestaFalsa(cuerpo=b"<!doctype html><html>x</html>")
    assert salud.saludable("http://127.0.0.1:8000/healthz") is False


def test_un_json_de_otra_cosa_no_lo_es(red_falsa):
    red_falsa.caja["respuesta"] = RespuestaFalsa(cuerpo=b'{"estado": "arrancando"}')
    assert salud.saludable() is False


def test_un_no_2xx_se_cuenta_como_fallo(red_falsa):
    red_falsa.caja["respuesta"] = RespuestaFalsa(estado=503)
    assert salud.saludable() is False


def test_un_error_http_no_se_cuela_como_exito(red_falsa):
    """`urllib` lanza en los 4xx/5xx: hay que tragárselo y responder falso, no dejar la traza en el log."""
    red_falsa.caja["error"] = urllib.error.HTTPError(
        "http://127.0.0.1:8000/healthz", 500, "internal server error", {}, None
    )
    assert salud.saludable() is False


def test_un_puerto_cerrado_es_falso(red_falsa):
    red_falsa.caja["error"] = urllib.error.URLError("conexión rechazada")
    assert salud.saludable() is False


def test_una_respuesta_que_no_es_json_no_revienta(red_falsa):
    red_falsa.caja["respuesta"] = RespuestaFalsa(cuerpo=b"\xff\xfe sin decodificar")
    assert salud.saludable() is False


def test_el_sondeo_no_puede_colgarse_mas_de_su_timeout(red_falsa):
    assert salud.saludable("http://127.0.0.1:8000/healthz", timeout=2) is True
    assert red_falsa[0][1] == 2
