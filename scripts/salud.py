#!/usr/bin/env python3
"""El sondeo de `HEALTHCHECK` de la imagen, sin `curl` y sin depender del número de puerto.

Existe por dos motivos concretos:

- La imagen final es `python:3.12-slim` y no lleva `curl`, así que el sondeo tiene que ser stdlib.
- El puerto lo decide el host: en local `scripts/arranque.sh` escucha en 8000 y en el despliegue real
  de Railway el host inyectó `PORT=8080` (se leyó en su log: `Uvicorn running on http://0.0.0.0:8080`).
  Un sondeo con el 8000 fijado a mano declararía *no saludable* a un contenedor sano, que es el peor
  fallo posible en un healthcheck.

Se lee `PORT` tal cual lo lee `arranque.sh`, con el mismo defecto, para que las dos mitades no puedan
separarse. Y no basta con un 2xx: la interfaz se sirve con *fallback* (cualquier ruta desconocida
devuelve el HTML de la SPA con 200), así que un sondeo que solo mira el estado podría dar por bueno un
200 que no es la API. Se pide el cuerpo que `/healthz` promete, `{"estado":"ok"}`, y con eso el healthcheck
significa «la agenda contesta», no «algo contesta».

No imprime nada: `docker` se come la salida del sondeo y un log vacío es más fácil de leer en
`docker ps` que una traza de `urllib` cada treinta segundos.

Uso (el que hace el `Dockerfile`):

    python scripts/salud.py
"""

import json
import os
import sys
import urllib.error
import urllib.request

RUTA = "/healthz"
TIMEOUT = 3.0


def url_a_sondear() -> str:
    """El mismo `${PORT:-8000}` de `scripts/arranque.sh`, traducido a URL de sondeo."""
    puerto = os.environ.get("PORT") or "8000"
    return f"http://127.0.0.1:{puerto}{RUTA}"


def saludable(url: str = "", timeout: float = TIMEOUT) -> bool:
    """Verdadero si `/healthz` contesta 2xx **y** dice `ok`.

    Todo lo demás es falso: conexión rechazada, tiempo de espera, un 404/500 (que `urllib` trae como
    `HTTPError`), un 200 con el HTML del fallback de la SPA encima, o un JSON con otra cosa dentro.
    """
    try:
        with urllib.request.urlopen(url or url_a_sondear(), timeout=timeout) as respuesta:
            if not 200 <= respuesta.status < 300:
                return False
            cuerpo = respuesta.read(4096)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False
    try:
        return json.loads(cuerpo).get("estado") == "ok"
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        return False


if __name__ == "__main__":
    sys.exit(0 if saludable() else 1)
