"""Punto de entrada FastAPI."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.v1 import (
    agenda,
    auth,
    availability,
    blocked,
    limpieza,
    pacientes,
    public,
    recordatorios,
    retencion,
    services,
    waitlist,
)
from app.arranque import revisar_arranque
from app.config import get_settings

# Antes de abrir el puerto: un despliegue público con el JWT_SECRET del repositorio no debe arrancar
# ni servir un minuto. En 'desarrollo' esto no dice nada, así que pytest y la máquina local siguen.
revisar_arranque()

app = FastAPI(title="Agenda Solo API", version="0.1.0", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (
    auth.router,
    services.router,
    availability.router,
    blocked.router,
    agenda.router,
    waitlist.router,
    pacientes.router,
    retencion.router,
    public.router,
    recordatorios.router,
    limpieza.router,
):
    app.include_router(router, prefix="/api/v1")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"estado": "ok"}


class _Inmutable:
    """Le pone cabecera de caducidad larga a lo que sirve `StaticFiles`.

    Los nombres que genera Vite llevan el hash del contenido, así que un archivo servido hoy bajo ese
    nombre no puede cambiar nunca: preguntar al año siguiente sigue siendo correcto, y un paciente que
    vuelve al enlace desde el móvil no baja otra vez los 240 KB. Starlette no admite `max_age` en
    `StaticFiles`, por eso se envuelve el ASGI en vez de tocar la respuesta. El 304 (el navegador ya lo
    tiene) se manda tal cual: caducidad larga no significa responder dos veces la misma cosa.
    """

    def __init__(self, interior: ASGIApp) -> None:
        self.interior = interior

    async def __call__(self, alcance: Scope, recibir: Receive, enviar: Send) -> None:
        async def enviar_con_cabecera(partes: Message) -> None:
            if partes["type"] == "http.response.start" and partes["status"] == 200:
                cabeceras = [*partes["headers"], (b"cache-control", b"public, max-age=31536000, immutable")]
                partes = {**partes, "headers": cabeceras}
            await enviar(partes)

        await self.interior(alcance, recibir, enviar_con_cabecera)


def carpeta_interfaz() -> Path | None:
    """La carpeta del `vite build`, o None si no está: la API puede vivir sola sin interfaz."""
    ajustada = get_settings().frontend_build
    raiz = Path(ajustada) if ajustada else Path(__file__).resolve().parents[2] / "frontend" / "build"
    return raiz if (raiz / "index.html").is_file() else None


def montar_interfaz(aplicacion: FastAPI, carpeta: Path) -> None:
    """Servir el frontend desde esta misma API: sin Node, sin CORS y con los enlaces directos vivos.

    La función va separada del arranque para poder probarla con una carpeta de mentira, sin depender
    de que nadie haya corrido `npm run build` en la máquina que ejecuta las pruebas.
    """
    if (carpeta / "_app").is_dir():
        # Los hashed de SvelteKit son inmutables y van por su monte; el resto (favicon, robots) lo
        # resuelve la ruta comodín de abajo, que también hace de fallback para los enlaces directos.
        aplicacion.mount("/_app", _Inmutable(StaticFiles(directory=carpeta / "_app")), name="estaticos_front")

    # GET y HEAD: un monitor, un CDN o un `curl -I` contra un despliegue que responde 405 a HEAD se
    # lee como servidor caído, y aquí el HTML es justo lo que quieren ver.
    @aplicacion.api_route("/{ruta:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def interfaz(ruta: str) -> FileResponse:
        if ruta.startswith("api/"):
            # Una ruta de API inexistente sigue siendo un 404 en JSON, no un index.html disfrazado.
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ruta de API inexistente")
        try:
            candidato = (carpeta / ruta).resolve()
            es_archivo = candidato.is_file() and candidato.is_relative_to(carpeta.resolve())
        except OSError:
            es_archivo = False
        if es_archivo:
            return FileResponse(candidato)
        # `no-cache` en el index: un despliegue nuevo no puede dejar a un paciente con el HTML viejo,
        # que es el que apunta a los nombres de archivo del build anterior.
        return FileResponse(carpeta / "index.html", headers={"Cache-Control": "no-cache"})


_front = carpeta_interfaz()

if _front:
    montar_interfaz(app, _front)
