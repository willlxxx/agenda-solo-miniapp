"""Salida de correo de los recordatorios: un proveedor real y uno de simulación.

`simulado` no toca la red: guarda lo que habría mandado. Es el valor por defecto en desarrollo y en
los tests, y es lo que permite probar el despacho entero sin credenciales de nadie ni mensajes
reales a pacientes. `smtp` usa la librería estándar dentro de un hilo: SMTP no es asíncrono y no
hacía falta añadir una dependencia para algo que corre una vez cada pocos minutos.

El proveedor se elige por configuración, así que cambiar de canal (WhatsApp, SMS) después es añadir
una clase aquí y una variable en `.env`, no reescribir el despacho.
"""

import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

import anyio

from app.config import get_settings


class ErrorEnvio(Exception):
    """El proveedor no pudo entregar: el despacho suma un intento y deja la bandera sin marcar."""


@dataclass(frozen=True)
class Correo:
    para: str
    asunto: str
    cuerpo: str


class Proveedor(Protocol):
    nombre: str

    async def enviar(self, correo: Correo) -> None: ...


@dataclass
class Simulado:
    """Guarda lo que se habría mandado. `fallar=True` sirve para probar el reintento."""

    nombre: str = "simulado"
    enviados: list[Correo] = field(default_factory=list)
    fallar: bool = False

    async def enviar(self, correo: Correo) -> None:
        if self.fallar:
            raise ErrorEnvio("Proveedor simulado en modo fallo")
        self.enviados.append(correo)


@dataclass(frozen=True)
class Smtp:
    nombre: str = "smtp"
    host: str = ""
    puerto: int = 587
    usuario: str | None = None
    contrasena: str | None = None
    de: str = ""
    ssl_directo: bool = False

    def _conectar(self) -> smtplib.SMTP:
        if self.ssl_directo:
            servidor = smtplib.SMTP_SSL(self.host, self.puerto, timeout=20)
        else:
            servidor = smtplib.SMTP(self.host, self.puerto, timeout=20)
            # STARTTLS antes de las credenciales: sin TLS la contraseña viajaría en claro.
            servidor.ehlo()
            servidor.starttls()
            servidor.ehlo()
        if self.usuario and self.contrasena:
            servidor.login(self.usuario, self.contrasena)
        return servidor

    def _enviar_sin_hilo(self, correo: Correo) -> None:
        mensaje = EmailMessage()
        mensaje["From"] = self.de
        mensaje["To"] = correo.para
        mensaje["Subject"] = correo.asunto
        mensaje.set_content(correo.cuerpo)
        servidor = self._conectar()
        try:
            servidor.send_message(mensaje)
        finally:
            servidor.quit()

    async def enviar(self, correo: Correo) -> None:
        try:
            await anyio.to_thread.run_sync(self._enviar_sin_hilo, correo)
        except (OSError, smtplib.SMTPException) as exc:
            raise ErrorEnvio(f"SMTP no pudo entregar a {correo.para}: {exc}") from exc


def proveedor_desde_configuracion() -> Proveedor:
    """El proveedor que dice `.env`, con la comprobación de credenciales hecha aquí y no a medias.

    Configurar `smtp` sin host o sin remitente es un error de despliegue que queremos ver en la
    respuesta del cron, no una conexión contra un host vacío.
    """
    ajustes = get_settings()
    elegido = (ajustes.recordatorios_proveedor or "simulado").strip().lower()
    if elegido in ("simulado", "none", ""):
        return Simulado()
    if elegido != "smtp":
        raise ValueError(f"Proveedor de recordatorios desconocido: {ajustes.recordatorios_proveedor!r}")

    faltan = [
        nombre
        for nombre, valor in (("SMTP_HOST", ajustes.smtp_host), ("SMTP_DE", ajustes.smtp_de))
        if not (valor or "").strip()
    ]
    if faltan:
        raise ValueError(f"SMTP configurado a medias: faltan {', '.join(faltan)}")
    return Smtp(
        host=ajustes.smtp_host or "",
        puerto=ajustes.smtp_port,
        usuario=ajustes.smtp_usuario,
        contrasena=ajustes.smtp_contrasena,
        de=ajustes.smtp_de or "",
        ssl_directo=ajustes.smtp_ssl,
    )


@dataclass(frozen=True)
class Canal:
    """Lo que se puede saber del canal sin abrir una conexión ni pedir credenciales.

    Lo necesita la cara pública para responder a "¿puedo recuperar mi contraseña por email?" sin
    intentar mandar nada: construir el proveedor real es conectar o fallar, y aquí lo que se quiere es
    solo leer la configuración.
    """

    nombre: str
    listo: bool
    origen_enlace: str


def canal_desde_configuracion() -> Canal:
    """El canal dicho en tres datos: cuál es, si daría la cara y de qué dominio cuelgan sus enlaces."""
    ajustes = get_settings()
    elegido = (ajustes.recordatorios_proveedor or "simulado").strip().lower()
    es_smtp = elegido == "smtp"
    listo = es_smtp and bool((ajustes.smtp_host or "").strip()) and bool((ajustes.smtp_de or "").strip())
    return Canal(nombre="smtp" if es_smtp else "simulado", listo=listo, origen_enlace=ajustes.frontend_origin)
