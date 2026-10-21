"""La recuperación de acceso que se puede probar sin base de datos ni reloj de entorno.

Está separado del servicio por el mismo criterio que los recordatorios: **decidir** (qué forma tiene un
enlace, cuánto vive, qué le decimos al médico por escrito) vive aquí y se prueba con valores metidos a
mano; **escribir y entregar** vive en `app/services/recuperacion.py` y necesita la base real.

Tres decisiones que vienen de aquí y no del endpoint:

- El token se genera con `secrets` y se guarda hasheado. Lo que viaja por el enlace nunca se lee en la
  base, así que un volcado de la tabla no devuelve accesos.
- La caducidad son 30 minutos. Sobra para cambiar de dispositivo y escribir la clave nueva, y es lo
  bastante corta como para que un enlace que alguien dejó caer en un correo reenviado no siga vivo al
  día siguiente.
- El enlace va en el **fragmento** de la URL (`/recuperar#token=…`), no en la query. Lo que va después
  de la almohadilla no viaja en la petición HTTP: no acaba en el log de accesos del host ni en un
  `Referer`, que es justo donde un token de recuperación no debería aparecer nunca.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

# Cuánto tarda en caducar un enlace y cuánto hay que esperar entre dos pedidos seguidos.
CADUCIDAD = timedelta(minutes=30)
MINUTOS_DE_VIGENCIA = int(CADUCIDAD.total_seconds() // 60)
# Dos pedidos casi seguidos son casi siempre un "no me ha llegado" antes de que el correo aterrice.
# Sin esta espera, martillar el botón llenaría la tabla de tokens muertos y de correos de más.
ESPERA_ENTRE_PEDIDOS = timedelta(seconds=60)

# 32 bytes url-safe: 43 caracteres de alfabeto base64. No hace falta más para que la fuerza bruta
# sobre un enlace de 30 minutos no sea un ataque posible.
BYTES_DE_TOKEN = 32

# Orígenes que no sirven para un enlace que va a abrir otra máquina.
ORIGENES_LOCALES = ("http://localhost", "http://127.0.0.1", "http://[::1]", "http://0.0.0.0")


def nuevo_token() -> str:
    """El token en claro: este es el único sitio donde existe, y sale por el enlace una vez."""
    return secrets.token_urlsafe(BYTES_DE_TOKEN)


def hash_del_token(token: str) -> str:
    """Lo que se guarda en la base. Irreversible, así que la tabla no es una lista de accesos."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def enlace_de_recuperacion(origen: str, token: str) -> str:
    """La URL completa del enlace, con el token en el fragmento."""
    return f"{origen.rstrip('/')}/recuperar#token={token}"


def es_origen_local(origen: str) -> bool:
    """Si la URL es de esta máquina. Un enlace con `localhost` no lo abre nadie desde su móvil."""
    limpio = (origen or "").strip().lower()
    return not limpio or any(limpio.startswith(local) for local in ORIGENES_LOCALES)


def sigue_vigente(expira_en: datetime, usado_en: datetime | None, ahora: datetime) -> bool:
    """Un enlace vale hasta su caducidad y una sola vez."""
    if usado_en is not None:
        return False
    return expira_en > ahora


def demasiado_pronto(creado_en: datetime, ahora: datetime) -> bool:
    """Si el último pedido de este consultorio es tan reciente que no hay que mandar otro correo."""
    return ahora - creado_en < ESPERA_ENTRE_PEDIDOS


# Motivos por los que la recuperación se ofrece o no. El frontend los lee y decide qué cara poner.
SIN_CANAL = "sin_canal_de_correo"
SIMULADO_EN_DESARROLLO = "simulado_en_desarrollo"


def disponible_recuperacion(*, canal_listo: bool, es_desarrollo: bool) -> tuple[bool, str | None]:
    """Si este despliegue puede devolverle el acceso a un médico por correo, dicho sin rodeos.

    Con el canal real listo, sí. Con el simulado y `ENTORNO=desarrollo` también, porque en local hace
    falta poder recorrer el flujo entero, y ahí el enlace sale por el log de la consola. En cualquier
    otro caso la respuesta es NO: ofrecer un botón que mete el token en un proveedor simulado sería
    prometer un correo que no va a llegar nunca.
    """
    if canal_listo:
        return True, None
    if es_desarrollo:
        return True, SIMULADO_EN_DESARROLLO
    return False, SIN_CANAL


def mensaje_recuperacion(nombre: str, enlace: str, minutos: int = MINUTOS_DE_VIGENCIA) -> tuple[str, str]:
    """(asunto, cuerpo) para el médico que pidió recuperar su acceso.

    Dice quién manda el correo y qué hacer si no fue él, que es lo que convierte un enlace perdido en
    un problema pequeño. No promete que el enlace sea el único modo de entrar: las sesiones que ya
    estaban abiertas siguen valiendo hasta su caducidad, y eso está dicho en la pantalla y en el README.
    """
    presentacion = nombre.strip().split(" ")[0] if nombre.strip() else ""
    saludo = f"Hola {presentacion}:" if presentacion else "Hola:"
    asunto = "Enlace para recuperar tu acceso a Agenda Solo"
    cuerpo = (
        f"{saludo}\n\n"
        f"Has pedido recuperar el acceso a la agenda de {nombre}. Entra en este enlace para poner una "
        "contraseña nueva:\n\n"
        f"{enlace}\n\n"
        f"Funciona durante {minutos} minutos y solo una vez. Si no has sido tú, ignora el mensaje y "
        "no pulses nada: nadie puede ponerse en tu lugar con este enlace sin leerlo antes.\n\n"
        "Te lo envía la propia agenda porque la recuperación sale por el mismo canal que los avisos a "
        "tus pacientes."
    )
    return asunto, cuerpo
