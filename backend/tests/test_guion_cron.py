"""El disparador del cron del host: que la llamada salga bien o se explique ella sola.

Pruebas puras, sin red: se carga el archivo desde `scripts/` por ruta (no es un paquete importable, es
un guion del despliegue) y se sustituye `urlopen` por un falso. Lo que importa aquí es el contrato con
el host: la ruta correcta, la cabecera del secreto, el `?simular=true` de la primera pasada y que un
fallo salga con un código de salida que el cron interpreta como fallo.
"""

import importlib.util
import json
import urllib.error
from pathlib import Path

import pytest


def _cargar_guion():
    ruta = Path(__file__).resolve().parents[2] / "scripts" / "cron.py"
    espec = importlib.util.spec_from_file_location("guion_cron_del_host", ruta)
    modulo = importlib.util.module_from_spec(espec)
    espec.loader.exec_module(modulo)
    return modulo


cron = _cargar_guion()


class RespuestaFalsa:
    """Lo que devuelve `urlopen` de verdad: estado, cuerpo y poder usarse con `with`."""

    def __init__(self, estado=200, cuerpo=b'{"enviados": 2}'):
        self.estado = estado
        self._cuerpo = cuerpo

    @property
    def status(self):
        return self.estado

    def read(self):
        return self._cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *_) -> bool:
        return False


@pytest.fixture
def red_falsa(monkeypatch):
    """Sustituye `urlopen` por un falso y da acceso a las peticiones que se construyeron."""

    class Llamadas(list):
        def __init__(self) -> None:
            super().__init__()
            self.caja = {"respuesta": None, "error": None}

    vistas = Llamadas()

    def falso_urlopen(peticion, timeout=None):
        vistas.append(peticion)
        if vistas.caja["error"] is not None:
            raise vistas.caja["error"]
        return vistas.caja["respuesta"] or RespuestaFalsa()

    monkeypatch.setattr(cron.urllib.request, "urlopen", falso_urlopen)
    return vistas


def _orden(trabajo="recordatorios", **kwargs):
    """`--url` y `--secreto` van siempre: así ninguna prueba depende del `.env` de quien la corre."""
    opciones = {"url": "https://agenda.up.railway.app", "secreto": "s3cr3to-del-cron", **kwargs}
    argumentos = [trabajo]
    if opciones.pop("simular", None):
        argumentos.append("--simular")
    for nombre, valor in opciones.items():
        if valor is not None:
            argumentos += [f"--{nombre}", str(valor)]
    return argumentos


def test_cada_trabajo_tiene_su_ruta_interna():
    assert cron.TRABAJOS["recordatorios"] == "/api/v1/internal/recordatorios"
    assert cron.TRABAJOS["limpieza"] == "/api/v1/internal/limpieza"


def test_la_barra_final_no_duplica_el_camino():
    assert cron.url_del_trabajo("https://a.b/", "/api/v1/internal/limpieza", False) == (
        "https://a.b/api/v1/internal/limpieza"
    )


def test_simular_es_lo_que_se_ve_antes_de_dejar_borrar():
    url = cron.url_del_trabajo("https://a.b", "/api/v1/internal/limpieza", True)
    assert url.endswith("/api/v1/internal/limpieza?simular=true")


def test_una_url_sin_esquema_no_se_llama_a_un_sitio_equivocado():
    with pytest.raises(ValueError) as error:
        cron.url_del_trabajo("agenda.up.railway.app", "/api/v1/internal/limpieza", False)
    assert "esquema" in str(error.value)


def test_recordatorios_llama_con_el_secreto_en_la_cabecera(red_falsa, capsys):
    assert cron.main(_orden()) == 0
    peticion = red_falsa[0]
    assert peticion.full_url == "https://agenda.up.railway.app/api/v1/internal/recordatorios"
    assert peticion.get_header("X-cron-secret") == "s3cr3to-del-cron"
    assert peticion.method == "POST"
    salida = capsys.readouterr().out
    assert "recordatorios 200" in salida
    assert '"enviados": 2' in salida


def test_el_resumen_se_imprime_en_una_linea_ordenada(red_falsa, capsys):
    red_falsa.caja["respuesta"] = RespuestaFalsa(cuerpo=b'{"z": 1, "a": 2}')
    cron.main(_orden())
    _, _, resto = capsys.readouterr().out.partition(": ")
    assert json.loads(resto) == {"a": 2, "z": 1}


def test_limpieza_con_simular_lleva_el_parametro_en_la_url(red_falsa):
    cron.main(_orden("limpieza", simular=True))
    assert red_falsa[0].full_url.endswith("/api/v1/internal/limpieza?simular=true")


def test_simular_con_recordatorios_es_un_error_de_comando(red_falsa, capsys):
    assert cron.main(_orden(simular=True)) == 2
    assert red_falsa == []
    assert "--simular solo existe para la limpieza" in capsys.readouterr().err


def test_sin_url_no_se_intenta_ni_siquiera(red_falsa, capsys):
    assert cron.main(["recordatorios", "--secreto", "s"]) == 2
    assert red_falsa == []
    assert "API_URL" in capsys.readouterr().err


def test_sin_secreto_avisa_de_que_las_rutas_estan_apagadas(red_falsa, capsys):
    assert cron.main(["recordatorios", "--url", "https://a.b"]) == 2
    assert red_falsa == []
    assert "503" in capsys.readouterr().err


def test_toma_los_valores_del_entorno_como_el_resto_del_despliegue(red_falsa, monkeypatch):
    monkeypatch.setenv("API_URL", "https://a.b")
    monkeypatch.setenv("CRON_SECRET", "del-env")
    assert cron.main(["recordatorios"]) == 0
    assert red_falsa[0].get_header("X-cron-secret") == "del-env"


def test_un_503_se_cuenta_como_fallo_y_su_cuerpo_se_lee(monkeypatch, capsys):
    """La ruta apagada responde con la razón en el cuerpo: eso es lo que hay que ver en el log del cron."""
    monkeypatch.setattr(cron, "llamar", lambda url, secreto, timeout: (503, '{"detail":"apagado"}'))
    assert cron.main(_orden()) == 3
    errores = capsys.readouterr().err
    assert "recordatorios 503" in errores
    assert "apagado" in errores


def test_un_502_de_un_proxy_no_se_cuela_como_exito(monkeypatch, capsys):
    monkeypatch.setattr(cron, "llamar", lambda url, secreto, timeout: (502, "<h1>502 bad gateway</h1>"))
    assert cron.main(_orden("limpieza")) == 3
    assert "502" in capsys.readouterr().err


def test_una_red_que_no_responde_reintenta_y_sale_con_codigo_propio(red_falsa, capsys):
    """El retry es lo que salva el tick de la limpieza: una pasada fallida sin repetir son 24 horas."""
    red_falsa.caja["error"] = urllib.error.URLError("tiempo de espera agotado")
    assert cron.main(_orden("limpieza", intentos=2)) == 4
    assert len(red_falsa) == 2
    errores = capsys.readouterr().err
    assert "se repite" in errores
    assert "no se pudo llamar" in errores


def test_un_intento_no_repite(red_falsa):
    red_falsa.caja["error"] = urllib.error.URLError("fuera de servicio")
    assert cron.main(_orden(intentos=1)) == 4
    assert len(red_falsa) == 1


def test_el_timeout_llega_hasta_la_llamada(red_falsa, monkeypatch):
    tiempos = []

    def apuntar(url, secreto, timeout):
        tiempos.append(timeout)
        return 200, "{}"

    monkeypatch.setattr(cron, "llamar", apuntar)
    cron.main(_orden(timeout=7))
    assert tiempos == [7]


def test_un_html_se_recorta_en_lugar_de_inundar_el_log():
    """El log del cron se cobra por líneas: una página de error entera no cabe."""
    recortado = cron.imprimir_resumen("<p>" + "x" * 900 + "</p>")
    assert recortado.count("x") < 400
    assert cron.imprimir_resumen("") == "(respuesta vacía)"
