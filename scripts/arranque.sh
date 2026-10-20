#!/bin/sh
# El único comando de arranque de este proyecto: migra y levanta la API con la interfaz dentro.
#
# Por qué la migración va aquí y no en un paso aparte del host: un primer despliegue sin tablas no es
# un 500 bonito, es un consultorio que a las ocho de la mañana no puede abrir su panel. Y por qué
# `--proxy-headers`: detrás de Railway, Fly o un nginx propio la app vería llegar todo desde el proxy,
# y los enlaces y el log saldrían con el esquema y el host equivocados.
#
# Se usa tal cual en el CMD de la imagen y en un systemd/VPS. Requiere `python` con el proyecto
# instalado (en la imagen) o el venv activado (en una máquina).
set -eu

cd "$(dirname "$0")/../backend"

# La migración se reintenta hasta dos minutos. Cuando app y base de datos arrancan a la vez (compose,
# Railway tras un deploy) la primera conexión suele caer, y un contenedor que muere en el intento 1 es
# un despliegue que parece roto sin estarlo. Si falla de verdad —una migración escrita a mano, una
# DATABASE_URL equivocada— el mensaje lo dice al final en vez de callarse.
intentos=0
until python -m alembic upgrade head; do
    intentos=$((intentos + 1))
    if [ "$intentos" -ge 24 ]; then
        echo "La base de datos no aceptó la migración en 2 minutos: revisa DATABASE_URL y que el PostgreSQL esté arriba." >&2
        exit 1
    fi
    echo "base de datos aún lista? intento $intentos, reintento en 5 s" >&2
    sleep 5
done

# La lista de IPs de confianza va por variable de entorno y NO por `--forwarded-allow-ips '*'`: el
# asterisco literal en la línea de órdenes lo expande el shell (en Windows, además, el arranque de la
# propia ejecución) y uvicorn recibe el listado de la carpeta como argumentos sueltos. Probado aquí,
# en Git Bash: `Error: Got unexpected extra arguments (alembic alembic.ini app pyproject.toml tests)`.
# Por eso el valor por defecto es '*' (cualquier proxy: el contenedor de Railway, el bridge de Docker),
# y se puede estrechar con FORWARDED_ALLOW_IPS si el servidor es público sin proxy delante.
export FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-*}"

exec python -m uvicorn app.main:app \
    --host "${HOST:-0.0.0.0}" \
    --port "${PORT:-8000}" \
    --proxy-headers
