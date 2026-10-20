"""La API sirviendo su propio frontend: el despliegue de un solo proceso.

No toca la base de datos ni el `frontend/build` de verdad: se monta una app de mentira sobre una
carpeta temporal. Lo que tiene que quedar garantizado es el contrato de rutas, no que alguien haya
corrido `npm run build` antes de las pruebas:

- un enlace directo (`/panel`, `/d/mi-consulta`) devuelve el HTML de la app, no un 404;
- los archivos reales del build se sirven con su contenido;
- una ruta de API inexistente **sigue** respondiendo 404 en JSON: si el comodín se la traga, el
  médico ve una pantalla rara en vez de un error, y un monitor de API deja de detectar el fallo;
- un `..` en la URL no saca a nadie de la carpeta servida;
- HEAD responde, porque los monitores y los CDN lo preguntan;
- los archivos con hash llevan caducidad larga e `index.html` ninguna: son las dos mitades de que un
  despliegue nuevo se vea nuevo sin obligar a nadie a bajar el build dos veces.
"""

from types import SimpleNamespace

from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

from app.main import carpeta_interfaz, montar_interfaz

INDEX = "<!doctype html><title>Agenda Solo</title>"
ROBOTS = "User-agent: *\nDisallow: /panel\n"
SECRETO = "DATABASE_URL=postgresql+asyncpg://postgres:se-na-oculto@127.0.0.1:5433/agenda_dev"


def _frente(tmp_path):
    """Carpeta de build de mentira, con su secreto JUSTO al lado para probar el escape."""
    carpeta = tmp_path / "build"
    (carpeta / "_app" / "immutable").mkdir(parents=True)
    (carpeta / "index.html").write_text(INDEX, encoding="utf-8")
    (carpeta / "robots.txt").write_text(ROBOTS, encoding="utf-8")
    (carpeta / "_app" / "immutable" / "app.ASSETHASH.js").write_text("export const x = 1;", encoding="utf-8")
    (tmp_path / "secreto.env").write_text(SECRETO, encoding="utf-8")
    return carpeta


def _cliente(carpeta):
    # Se monta UNA app nueva con una ruta de API de verdad, porque el punto delicado es justo el
    # orden: el comodín del frontend tiene que quedarse detrás de la API.
    app = FastAPI()
    router = APIRouter()

    @router.get("/services")
    async def services() -> dict[str, str]:
        return {"ok": "si"}

    app.include_router(router, prefix="/api/v1")
    montar_interfaz(app, carpeta)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_enlace_directo_devuelve_la_app_sin_perderla(tmp_path):
    async with _cliente(_frente(tmp_path)) as cliente:
        for ruta in ("/", "/panel", "/auth/acceso", "/d/mi-consulta"):
            respuesta = await cliente.get(ruta)
            assert respuesta.status_code == 200, ruta
            assert respuesta.text == INDEX, ruta
            # Sin esto, un despliegue nuevo dejaría a pacientes abiertos con el HTML del build anterior.
            assert respuesta.headers["cache-control"] == "no-cache", ruta


async def test_los_archivos_del_build_se_sirven_como_estan(tmp_path):
    carpeta = _frente(tmp_path)
    async with _cliente(carpeta) as cliente:
        robots = await cliente.get("/robots.txt")
        assert robots.status_code == 200
        # En Windows `write_text` convierte el salto de línea al escribir; el archivo llegó bien,
        # lo que no puede hacer el test es depender del EOL de la máquina que lo corre.
        assert robots.text.replace("\r\n", "\n") == ROBOTS

        # El monte de `/_app` es el que pone los hashed; si no está montado, la app no arranca.
        asset = await cliente.get("/_app/immutable/app.ASSETHASH.js")
        assert asset.status_code == 200
        assert asset.text == "export const x = 1;"
        # Nombre con hash = contenido congelado: sin esta cabecera cada visita desde el móvil vuelve
        # a bajar el build entero, y con ella solo baja cuando cambia de verdad.
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"


async def test_la_api_que_no_existe_sigue_siendo_json(tmp_path):
    async with _cliente(_frente(tmp_path)) as cliente:
        respuesta = await cliente.get("/api/v1/no-existe")
        assert respuesta.status_code == 404
        assert respuesta.headers["content-type"].startswith("application/json")
        assert "index" not in respuesta.text.lower()

        # Y la que sí existe no la toca el comodín.
        viva = await cliente.get("/api/v1/services")
        assert viva.status_code == 200
        assert viva.json() == {"ok": "si"}


async def test_subir_de_carpeta_no_saca_el_secreto(tmp_path):
    async with _cliente(_frente(tmp_path)) as cliente:
        for ruta in ("/%2e%2e/secreto.env", "/..%2Fsecreto.env", "/%2e%2e%2f%2e%2e%2fsecreto.env"):
            respuesta = await cliente.get(ruta)
            assert SECRETO not in respuesta.text, ruta
            assert respuesta.text == INDEX, ruta


async def test_head_responde_como_un_sitio_estatico(tmp_path):
    async with _cliente(_frente(tmp_path)) as cliente:
        respuesta = await cliente.head("/panel")
        assert respuesta.status_code == 200
        assert respuesta.headers["content-type"].startswith("text/html")
        assert int(respuesta.headers["content-length"]) == len(INDEX)


def test_sin_build_la_api_vive_sola(tmp_path, monkeypatch):
    """Si no hay `index.html`, no se monta nada: CI y backend puro siguen respondiendo como antes."""
    monkeypatch.setattr("app.main.get_settings", lambda: SimpleNamespace(frontend_build=str(tmp_path)))
    assert carpeta_interfaz() is None

    vacia = tmp_path / "build"
    vacia.mkdir()
    monkeypatch.setattr("app.main.get_settings", lambda: SimpleNamespace(frontend_build=str(vacia)))
    assert carpeta_interfaz() is None
