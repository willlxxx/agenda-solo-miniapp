"""Lo que se comprueba al arrancar, para que un despliegue público no se levante a medias.

El criterio es uno solo: si el fallo hace que las sesiones se puedan falsificar, el proceso **no
arranca**. Si lo que falta es una función opcional (el cron de avisos, el SMTP del doctor), se avisa
por el log y se levanta igual, porque el día uno un consultorio puede querer solo la agenda y ya
tendrá los recordatorios el día dos.

De ahí que el guardia dependa de `ENTORNO` y no de que alguien recuerde una lista: con
`ENTORNO=produccion` y el `JWT_SECRET` del repositorio, arrancar es el bug.
"""

import logging

from app.config import SECRETO_DE_DESARROLLO, Settings, get_settings
from app.domain.recuperacion import es_origen_local

log = logging.getLogger("agenda.arranque")


def _logger_vivo() -> logging.Logger:
    """El logger del módulo, reactivado antes de escribir.

    `fileConfig` de Alembic acaba llamando a `_install_loggers` con `disable_existing_loggers`, y con
    eso apaga por dentro todo logger que ya existiera cuando alguien migra en el mismo proceso (lo hace
    `tests/conftest.py`, y lo haría un arranque que migre desde Python). Un aviso de despliegue que no
    sale por el log es peor que no tener aviso: quien lo lea cree que todo estaba bien.
    """
    log.disabled = False
    return log

# Los únicos valores que se entienden. Un typo ('produccio', 'production') se queda en desarrollo sin
# avisar, así que se trata como fallo grave y no como 'no es producción'.
VALORES_DE_ENTORNO = {"desarrollo", "produccion", "prod"}

# Menos de esto y un HS256 se puede romper por fuerza bruta con una GPU de casa.
LONGITUD_MINIMA_DE_SECRETO = 32

# El de `.env.example` está aquí también prohibido: copiar el ejemplo y cambiar solo `ENTORNO` es
# exactamente el fallo que se cuela en un despliegue de quince minutos.
SECRETOS_PROHIBIDOS = {SECRETO_DE_DESARROLLO, "cambia-esto-en-produccion-por-al-menos-32-bytes-aleatorios"}


def _problemas_graves(s: Settings) -> list[str]:
    """Lo que impide arrancar, en español y dicho entero: el fundador lo lee en el log de arranque."""
    if s.entorno_normalizado not in VALORES_DE_ENTORNO:
        return [
            f"ENTORNO={s.entorno!r} no se entiende. Usa 'desarrollo' o 'produccion'. "
            "Dejarlo con un typo sería arrancar un sitio público creyendo que está protegido."
        ]
    if not s.es_produccion:
        return []
    graves = []
    if s.jwt_secret in SECRETOS_PROHIBIDOS:
        graves.append(
            "JWT_SECRET sigue siendo el de desarrollo: con el repositorio delante, cualquiera puede "
            "firmarse una cookie válida y entrar en el panel de cualquier consultorio."
        )
    elif len(s.jwt_secret) < LONGITUD_MINIMA_DE_SECRETO:
        graves.append(
            f"JWT_SECRET tiene {len(s.jwt_secret)} caracteres y hacen falta {LONGITUD_MINIMA_DE_SECRETO} "
            "o más (por ejemplo con `python -c \"import secrets; print(secrets.token_urlsafe(32))\"`)."
        )
    if not s.database_url.startswith("postgresql"):
        graves.append(
            f"DATABASE_URL={s.database_url!r} no apunta a PostgreSQL: con SQLite la agenda funciona en "
            "local y se cae en cuanto dos pacientes reservan a la vez, porque no hay advisory lock."
        )
    return graves


def _avisos(s: Settings) -> list[str]:
    """Lo que funciona a medias: se dice, no se bloquea."""
    if not s.es_produccion:
        return []
    avisos = []
    if not s.cron_secret:
        avisos.append(
            "CRON_SECRET vacío: /internal/recordatorios y /internal/limpieza responden 503. La agenda "
            "funciona, pero no sale ningún recordatorio y no se borra el histórico viejo."
        )
    if s.recordatorios_proveedor == "smtp" and not (s.smtp_host and s.smtp_de):
        avisos.append(
            "RECORDATORIOS_PROVEEDOR=smtp sin SMTP_HOST o SMTP_DE: cada envío va a fallar y el aviso "
            "queda solo registrado en la base."
        )
    if s.recordatorios_proveedor not in {"smtp", "simulado"}:
        avisos.append(
            f"RECORDATORIOS_PROVEEDOR={s.recordatorios_proveedor!r} no es 'smtp' ni 'simulado': se "
            "usa el simulado, o sea que ningún correo sale realmente."
        )
    if s.recordatorios_proveedor == "smtp" and es_origen_local(s.frontend_origin):
        avisos.append(
            f"RECORDATORIOS_PROVEEDOR=smtp con FRONTEND_ORIGIN={s.frontend_origin!r}: los correos salen, "
            "pero los enlaces de recuperación apuntan a esta máquina y nadie puede abrirlos desde su "
            "teléfono. En un despliegue público va el dominio real."
        )
    return avisos


def revisar_arranque(ajustes: Settings | None = None) -> None:
    """Se llama al importar la app: si algo es grave, no llega a abrir el puerto."""
    s = ajustes if ajustes is not None else get_settings()
    graves = _problemas_graves(s)
    if graves:
        raise RuntimeError("El despliegue no arranca:\n- " + "\n- ".join(graves))
    for aviso in _avisos(s):
        _logger_vivo().warning("aviso de despliegue: %s", aviso)
