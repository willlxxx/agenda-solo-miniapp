# Dos etapas, una imagen final: Node solo existe para construir la cara y no entra en el despliegue.
#
# La imagen final es Python sirviendo API e interfaz en el mismo proceso (lo que hace `carpeta_interfaz`
# en backend/app/main.py), así que un host con puertos sueltos es suficiente: no hace falta un segundo
# servicio Node, ni CORS, ni otro dominio.

# --- Etapa 1: el `vite build` con adapter-static (fallback index.html) ---
# bookworm-slim en vez de alpine: coincide con la libc de la imagen final, y Node solo vive en esta
# capa de build. Si tu registro prefiere alpine, es cambiar esta línea y nada más.
FROM node:24-bookworm-slim AS cara
WORKDIR /src
# Primero solo el lock: si cambia el código pero no las dependencias, `npm ci` no se repite.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Etapa 2: backend + la cara dentro ---
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # Sin esto, el log del despliegue sale con caracteres cortados cuando el host no está en UTF-8.
    PYTHONIOENCODING=utf-8
WORKDIR /app

COPY backend/ ./backend/
RUN pip install ./backend

# `carpeta_interfaz()` busca por defecto ../../frontend/build desde app/main.py, que aquí es
# /app/frontend/build: copiando el build justo ahí, el contenedor no necesita FRONTEND_BUILD.
COPY --from=cara /src/build ./frontend/build
COPY scripts/ ./scripts/

# El puerto 8000 es el convenio local; los hosts suelen inyectar PORT, y arranque.sh lo respeta.
EXPOSE 8000

# Healthcheck sin curl: en la imagen slim no está instalado y bajarlo sería un segundo sistema.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request as u,sys;sys.exit(0 if u.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["sh", "/app/scripts/arranque.sh"]
