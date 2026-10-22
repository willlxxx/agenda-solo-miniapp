"""Salida de correo de los recordatorios: dos canales de API HTTPS, uno de SMTP y uno de simulación.

`simulado` no toca la red: guarda lo que habría mandado. Es el valor por defecto en desarrollo y en
los tests, y es lo que permite probar el despacho entero sin credenciales de nadie ni mensajes
reales a pacientes. `smtp` usa la librería estándar dentro de un hilo: SMTP no es asíncrono y no
hacía falta añadir una dependencia para algo que corre una vez cada pocos minutos. `brevo` y `resend`
llaman a la API HTTPS de un servicio de envíos con `httpx`, y existen por un motivo concreto: el
egress de SMTP de Railway está apagado fuera del plan Pro, así que un despliegue ahí con `smtp` no
llega a conectar. De los dos, `resend` es el que se puede montar con esta cuenta (Brevo no deja
verificar el buzón desde Venezuela) y el que pide dominio verificado a cambio.

El proveedor se elige por configuración, así que cambiar de canal (WhatsApp, SMS) después es añadir
una clase aquí y una variable en `.env`, no reescribir el despacho.
"""

import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

import anyio
import httpx

from app.config import Settings, get_settings


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


URL_DE_BREVO = "https://api.brevo.com/v3/smtp/email"
URL_DE_RESEND = "https://api.resend.com/emails"
# El aviso corre en el cron o en un request de recuperación: ninguno de los dos puede quedarse colgado
# medio minuto mirando una API que no contesta.
TIEMPO_LIMITE_DE_API = 15.0
# Los dos servicios contestan 201 con su identificador; se aceptan los 2xx porque el código exacto no es contrato.
CODICOS_DE_ENTREGA = frozenset({200, 201, 202})


def _razon_del_proveedor(respuesta: httpx.Response) -> str:
    """Lo que el servicio dice en el cuerpo, o el texto crudo si no es el JSON de siempre.

    Vale más que un `HTTP 400` pelado porque este texto es el que acaba en `password_resets.error_envio`
    y en el contador del cron: cuando el remitente no está verificado, el `400` no lo dice y el que lee
    el log se pone a mirar la red en vez del panel del proveedor. Brevo escribe `message` y `code`, Resend
    escribe `message` y `name`; se leen los cuatro y gana el primero que traiga texto.
    """
    try:
        datos = respuesta.json()
    except ValueError:
        datos = None
    if isinstance(datos, dict):
        for clave in ("message", "Code", "code", "name"):
            valor = datos.get(clave)
            if isinstance(valor, str) and valor.strip():
                return valor.strip()[:300]
    return (respuesta.text or "").strip()[:300] or "sin respuesta del proveedor"


@dataclass(frozen=True)
class Brevo:
    """El envío por la API HTTPS de Brevo, que es lo que funciona en cualquier plan de Railway.

    Nace de un fallo visto en producción: con `smtp` el contenedor no llega a abrir el socket contra
    `smtp.gmail.com:587` --`[Errno 101] Network is unreachable`--, porque el egress de SMTP está
    apagado fuera del plan Pro. El 443 de una API de envíos sí sale. Se queda en el repo aunque este
    despliegue use `Resend`: la cuenta no pudo verificar el buzón desde Venezuela, pero el canal es
    válido para otro consultorio y sus pruebas siguen siendo las mismas que atan a `resend`.

    Mismo contrato que `Smtp`: `enviar` solo lanza `ErrorEnvio`, nunca un `httpx.HTTPStatusError` suelto,
    porque el despacho y la recuperación cuentan sus reintentos sobre esa excepción.
    """

    nombre: str = "brevo"
    api_key: str = ""
    de: str = ""
    nombre_remitente: str = ""
    # Solo para los tests: un transporte de mentira, para probar el contrato sin tocar la red ni una
    # cuenta de nadie. En producción llega a `None` desde la configuración.
    transporte: httpx.AsyncBaseTransport | None = None

    def _cuerpo(self, correo: Correo) -> dict:
        return {
            "sender": {"name": self.nombre_remitente or self.de, "email": self.de},
            "to": [{"email": correo.para}],
            "subject": correo.asunto,
            # `textContent`, no `htmlContent`: los textos de `app/domain` ya son llanos, y meterlos en
            # HTML sería cambiar cómo se leen sin haberlo decidido en el dominio.
            "textContent": correo.cuerpo,
        }

    async def enviar(self, correo: Correo) -> None:
        try:
            async with httpx.AsyncClient(
                timeout=TIEMPO_LIMITE_DE_API, transport=self.transporte
            ) as cliente:
                respuesta = await cliente.post(
                    URL_DE_BREVO,
                    json=self._cuerpo(correo),
                    headers={"x-api-key": self.api_key, "accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise ErrorEnvio(f"Brevo no pudo entregar a {correo.para}: {exc}") from exc
        if respuesta.status_code not in CODICOS_DE_ENTREGA:
            raise ErrorEnvio(
                f"Brevo rechazó el envío a {correo.para} ({respuesta.status_code}): "
                f"{_razon_del_proveedor(respuesta)}"
            )


@dataclass(frozen=True)
class Resend:
    """El envío por la API HTTPS de Resend, el canal que toca en este despliegue.

    Entra en lugar de `Brevo` por un motivo de cuenta, no de código: Brevo no dejó verificar el buzón
    desde Venezuela. Resend abre por el mismo 443 y con el mismo contrato que `Smtp` --`enviar` solo
    lanza `ErrorEnvio`--, pero cobra un requisito que Brevo no cobraba: el dominio del remitente tiene
    que estar verificado en su panel, así que un `doctor@gmail.com` en `RESEND_DE` se rechaza. La
    respuesta de ese rechazo (`message` del JSON) es la que acaba en `error_envio`.

    El "De:" se manda como una sola cadena, `Nombre <correo>`, a diferencia de Brevo que pide el nombre
    y la dirección en campos separados.
    """

    nombre: str = "resend"
    api_key: str = ""
    de: str = ""
    nombre_remitente: str = ""
    # Solo para los tests: un transporte de mentira, para probar el contrato sin tocar la red ni una
    # cuenta de nadie. En producción llega a `None` desde la configuración.
    transporte: httpx.AsyncBaseTransport | None = None

    def _remitente(self) -> str:
        if "<" in self.de:
            # Alguien ya escribió `Consulta <avisos@dominio.com>` en la variable: no se dobla.
            return self.de
        if not self.nombre_remitente:
            return self.de
        return f"{self.nombre_remitente} <{self.de}>"

    def _cuerpo(self, correo: Correo) -> dict:
        return {
            "from": self._remitente(),
            "to": [correo.para],
            "subject": correo.asunto,
            # `text`, no `html`: los textos de `app/domain` ya son llanos, y meterlos en HTML sería cambiar
            # cómo se leen sin haberlo decidido en el dominio.
            "text": correo.cuerpo,
        }

    async def enviar(self, correo: Correo) -> None:
        try:
            async with httpx.AsyncClient(
                timeout=TIEMPO_LIMITE_DE_API, transport=self.transporte
            ) as cliente:
                respuesta = await cliente.post(
                    URL_DE_RESEND,
                    json=self._cuerpo(correo),
                    headers={"authorization": f"Bearer {self.api_key}", "accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise ErrorEnvio(f"Resend no pudo entregar a {correo.para}: {exc}") from exc
        if respuesta.status_code not in CODICOS_DE_ENTREGA:
            raise ErrorEnvio(
                f"Resend rechazó el envío a {correo.para} ({respuesta.status_code}): "
                f"{_razon_del_proveedor(respuesta)}"
            )


def canal_elegido(ajustes: Settings) -> tuple[str, list[str]]:
    """Qué canal dice `.env` y qué variables le faltan para dar la cara.

    La necesita `app.arranque` para avisar de lo mismo que el proveedor va a rechazar, y la comparten el
    constructor del proveedor y el del canal a propósito: si `disponible` dice que el canal está listo y al
    mandar falta una variable, la pantalla promete un correo que no sale.
    """
    elegido = (ajustes.recordatorios_proveedor or "simulado").strip().lower()
    if elegido in ("", "none", "simulado"):
        return "simulado", []
    if elegido == "smtp":
        faltan = [
            nombre
            for nombre, valor in (("SMTP_HOST", ajustes.smtp_host), ("SMTP_DE", ajustes.smtp_de))
            if not (valor or "").strip()
        ]
        return "smtp", faltan
    if elegido == "brevo":
        faltan = [
            nombre
            for nombre, valor in (("BREVO_API_KEY", ajustes.brevo_api_key), ("BREVO_DE", ajustes.brevo_de))
            if not (valor or "").strip()
        ]
        return "brevo", faltan
    if elegido == "resend":
        faltan = [
            nombre
            for nombre, valor in (("RESEND_API_KEY", ajustes.resend_api_key), ("RESEND_DE", ajustes.resend_de))
            if not (valor or "").strip()
        ]
        return "resend", faltan
    raise ValueError(f"Proveedor de recordatorios desconocido: {ajustes.recordatorios_proveedor!r}")


def proveedor_desde_configuracion() -> Proveedor:
    """El proveedor que dice `.env`, con la comprobación de credenciales hecha aquí y no a medias.

    Configurar un canal real sin sus variables es un error de despliegue que queremos ver en la respuesta
    del cron, no una conexión contra un host vacío ni un POST contra la API sin clave.
    """
    ajustes = get_settings()
    elegido, faltan = canal_elegido(ajustes)
    if faltan:
        raise ValueError(f"{elegido} configurado a medias: faltan {', '.join(faltan)}")
    if elegido == "simulado":
        return Simulado()
    if elegido == "brevo":
        return Brevo(
            api_key=(ajustes.brevo_api_key or "").strip(),
            de=(ajustes.brevo_de or "").strip(),
            nombre_remitente=(ajustes.brevo_nombre or "").strip(),
        )
    if elegido == "resend":
        return Resend(
            api_key=(ajustes.resend_api_key or "").strip(),
            de=(ajustes.resend_de or "").strip(),
            nombre_remitente=(ajustes.resend_nombre or "").strip(),
        )
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
    """El canal dicho en tres datos: cuál es, si daría la cara y de qué dominio cuelgan sus enlaces.

    Un valor ilegible en `RECORDATORIOS_PROVEEDOR` se queda en simulado y no lanza: esta función la llama
    la pantalla de acceso, y un typo del despliegue no puede traducirse en un 500 para quien solo quería
    entrar. El aviso por log lo da `app.arranque`.
    """
    ajustes = get_settings()
    try:
        elegido, faltan = canal_elegido(ajustes)
    except ValueError:
        return Canal(nombre="simulado", listo=False, origen_enlace=ajustes.frontend_origin)
    return Canal(
        nombre=elegido,
        listo=elegido != "simulado" and not faltan,
        origen_enlace=ajustes.frontend_origin,
    )
