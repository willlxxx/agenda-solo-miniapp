#!/usr/bin/env python3
"""El disparador que lanza el cron del host: una pasada por ejecución y el proceso se acaba.

Railway (y cualquier cron de sistema) se ejecuta llamando a un comando, no a un endpoint, así que este
archivo es el puente. Existe por dos motivos: la imagen final es `python:3.12-slim` y no lleva `curl`,
y el cron de Railway termina el servicio cuando el comando acaba, así que no puede ser un bucle.

Es **solo stdlib**, a propósito: no importa nada de `app` porque si la app está rota es justo cuando
uno quiere llamar al despacho, y porque el servicio del cron no necesita leer la base de datos — habla
con la API como lo haría cualquier otro cliente, con el secreto en la cabecera.

Uso:

    python scripts/cron.py recordatorios
    python scripts/cron.py limpieza --simular

Variables:

    API_URL      la base del servicio, con esquema y sin barra final (https://algo.up.railway.app)
    CRON_SECRET  el mismo valor que `CRON_SECRET` en el servicio web; vacío, las rutas responden 503

Códigos de salida: 0 si la API respondió 2xx; 2 si falta configuración; 3 si respondió error; 4 si no
se llegó (red, TLS, tiempo de espera). El resumen que devuelve la API se imprime tal cual, que es lo
que se lee en el log del cron; las rutas internas devuelven contadores, nunca datos de un paciente.

Reintentar es seguro: el despacho marca en la base lo que ya envió y la limpieza va en transacción con
candado, así que dos pasadas del mismo minuto no duplican un aviso ni borran dos veces.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

TRABAJOS = {
    "recordatorios": "/api/v1/internal/recordatorios",
    "limpieza": "/api/v1/internal/limpieza",
}

# La limpieza recorre todos los consultorios de una pasada; con 30 s se queda corta un martes con la
# agenda llena y el cron lo marca como fallo cuando el trabajo seguía haciendo falta.
TIMEOUT_DEFECTO = 120.0


def url_del_trabajo(base: str, ruta: str, simular: bool) -> str:
    """La URL final, sin doblar la barra ni perder el `?simular=true` de la primera pasada."""
    limpia = base.rstrip("/")
    if not limpia.startswith(("http://", "https://")):
        raise ValueError(f"API_URL={base!r} no tiene esquema: pon algo como https://tu-dominio")
    return f"{limpia}{ruta}" + ("?simular=true" if simular else "")


def llamar(url: str, secreto: str, timeout: float) -> tuple[int, str]:
    """Una llamada POST. Devuelve (estado, cuerpo) y deja que el de arriba decida qué significa."""
    peticion = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={"x-cron-secret": secreto, "content-type": "application/json", "user-agent": "agenda-solo-cron"},
    )
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            return respuesta.status, respuesta.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        # Un 401 o un 503 también es información: el cuerpo dice si el secreto está mal o si la ruta
        # está apagada, y eso es exactamente lo que hay que ver en el log del cron.
        return error.code, error.read().decode("utf-8", "replace")


def imprimir_resumen(cuerpo: str) -> str:
    """El resumen en una línea legible; si la API devolvió HTML, se saca igual sin reventar."""
    try:
        return json.dumps(json.loads(cuerpo), ensure_ascii=False, sort_keys=True)
    except (json.JSONDecodeError, TypeError):
        return cuerpo.strip()[:400] or "(respuesta vacía)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lanza una pasada del cron interno y termina.")
    parser.add_argument("trabajo", choices=sorted(TRABAJOS), help="qué ruta interna se llama")
    parser.add_argument(
        "--simular",
        action="store_true",
        help="solo en limpieza: haz la pasada y deshazla, para ver las cuentas antes de borrar",
    )
    parser.add_argument("--url", default=os.environ.get("API_URL", ""), help="base de la API (API_URL)")
    parser.add_argument("--secreto", default=os.environ.get("CRON_SECRET", ""), help="x-cron-secret (CRON_SECRET)")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_DEFECTO, help="segundos de espera")
    parser.add_argument("--intentos", type=int, default=2, help="cuántas veces se llama si no hay respuesta")
    argumentos = parser.parse_args(argv)

    if argumentos.simular and argumentos.trabajo != "limpieza":
        print("error: --simular solo existe para la limpieza", file=sys.stderr)
        return 2
    if not argumentos.url:
        print("error: falta API_URL (la base del servicio, con https://)", file=sys.stderr)
        return 2
    if not argumentos.secreto:
        print(
            "error: falta CRON_SECRET; sin ese valor las dos rutas internas responden 503 a propósito",
            file=sys.stderr,
        )
        return 2
    try:
        url = url_del_trabajo(argumentos.url, TRABAJOS[argumentos.trabajo], argumentos.simular)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    intentos = max(1, argumentos.intentos)
    for numero in range(1, intentos + 1):
        try:
            estado, cuerpo = llamar(url, argumentos.secreto, argumentos.timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            detalle = getattr(error, "reason", error)
            if numero < intentos:
                print(f"aviso: intento {numero} sin respuesta ({detalle}), se repite", file=sys.stderr)
                continue
            print(f"fallo: no se pudo llamar a {url} ({detalle})", file=sys.stderr)
            return 4
        resumen = imprimir_resumen(cuerpo)
        if 200 <= estado < 300:
            print(f"{argumentos.trabajo} {estado}: {resumen}")
            return 0
        print(f"{argumentos.trabajo} {estado}: {resumen}", file=sys.stderr)
        return 3
    return 4  # inalcanzable con --intentos >= 1; aquí por no dejar la salida sin definir


if __name__ == "__main__":
    sys.exit(main())
