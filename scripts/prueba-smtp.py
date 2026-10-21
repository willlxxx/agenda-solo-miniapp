#!/usr/bin/env python3
"""Comprobar el SMTP del consultorio antes de escribirlo en el panel de Railway.

Existe porque la contraseña de aplicación solo la ve el host: si está mal, lo primero que se entera el
fundador es que los avisos suman `errores` en el log del cron, cuarto de hora tras cuarto de hora. Este
sondeo dice en veinte segundos si el host responde, si arranca TLS y si las credenciales valen, y no
manda un correo a nadie salvo que se lo pidas con `--para`.

Reproduce la secuencia de `app/services/correo.py::Smtp._conectar` --STARTTLS antes del login, y con
`--ssl` conexión directa al 465--, así que lo que aquí falla es lo que fallará en el contenedor. La
contraseña no entra por argumento ni por este chat: `getpass` la pide en el terminal, de modo que no
queda escrita en el historial de comandos ni en el repositorio.

Uso (con las variables del `backend/.env`, sin mandar nada: solo conexión y login):

    python scripts/prueba-smtp.py

Señalando otro proveedor y comprobando la entrega de verdad en tu propio buzón:

    python scripts/prueba-smtp.py --host smtp.zoho.com --puerto 465 --ssl \
        --usuario consulta@midominio --de consulta@midominio --para consulta@midominio

Y sin meter ninguna contraseña, para ver solo si el host responde y sube TLS:

    python scripts/prueba-smtp.py --host smtp.gmail.com --sin-login

Sale 0 si todo fue bien y 1 en cualquier fallo, con el motivo y su arreglo en la última línea.
"""

import argparse
import getpass
import os
import smtplib
import socket
import ssl
import sys
from email.message import EmailMessage
from typing import Never

ASUNTO_DE_PRUEBA = "Prueba de SMTP desde scripts/prueba-smtp.py"
CUERPO_DE_PRUEBA = (
    "Este correo lo ha mandado el sondeo de SMTP del repositorio, no la agenda.\n\n"
    "Si lo ves aquí, el host responde, el TLS se negoció y las credenciales valen: si en el despliegue "
    "algo falla, ya no es el proveedor de correo."
)
TIEMPO_LIMITE = 20.0


def es_verdadero(valor: str | None) -> bool:
    """El mismo defecto que `SMTP_SSL` en `.env`: sin valor, STARTTLS en el puerto de siempre."""
    return (valor or "").strip().lower() in {"1", "true", "si", "sí", "yes"}


def argumentos(opciones: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sondear el SMTP del consultorio sin tocar la base de datos.",
        epilog="La contraseña se pide por el terminal; no se guarda ni se pasa por parámetros.",
    )
    parser.add_argument("--host", default=os.environ.get("SMTP_HOST", ""), help="smtp.gmail.com, por ejemplo")
    parser.add_argument(
        "--puerto",
        type=int,
        default=int(os.environ.get("SMTP_PORT", "587")),
        help="587 con STARTTLS, 465 con --ssl",
    )
    parser.add_argument("--usuario", default=os.environ.get("SMTP_USUARIO", ""), help="la cuenta autenticada")
    parser.add_argument("--de", default=os.environ.get("SMTP_DE", ""), help="el remitente del sobre")
    parser.add_argument(
        "--ssl",
        action="store_true",
        default=es_verdadero(os.environ.get("SMTP_SSL")),
        help="SSL directo, como SMTP_SSL=true",
    )
    parser.add_argument(
        "--para",
        default="",
        help="si se pone, manda un correo de prueba a esta dirección; sin valor solo conecta y hace login",
    )
    parser.add_argument(
        "--desde-variable",
        action="store_true",
        help="leer SMTP_CONTRASENA del entorno en vez de pedirla por el terminal",
    )
    parser.add_argument(
        "--sin-login",
        action="store_true",
        help="comprobar solo host y TLS: útil cuando el proveedor autentica por IP y no por usuario",
    )
    return parser.parse_args(opciones)


def pedir_contrasena(desde_variable: bool) -> str:
    if desde_variable:
        return os.environ.get("SMTP_CONTRASENA", "")
    return getpass.getpass("Contraseña de aplicación (se lee una vez y no se guarda): ")


def conectar(host: str, puerto: int, ssl_directo: bool) -> smtplib.SMTP:
    """La misma secuencia de `Smtp._conectar`, con el `ehlo()` que revela si hay STARTTLS."""
    if ssl_directo:
        return smtplib.SMTP_SSL(host, puerto, timeout=TIEMPO_LIMITE)
    servidor = smtplib.SMTP(host, puerto, timeout=TIEMPO_LIMITE)
    servidor.ehlo()
    # `esmtp_features` es lo que deja el último EHLO: `extensions` no existe en smtplib, y descubrirlo
    # aquí en vez de en el despliegue es la razón de ser de este script.
    ofrecido = ", ".join(sorted(servidor.esmtp_features)) or "nada"
    print(f"· {host}:{puerto} responde. Ofrece: {ofrecido}")
    if "starttls" not in servidor.esmtp_features:
        raise smtplib.SMTPNotSupportedError(f"{host}:{puerto} no ofrece STARTTLS")
    servidor.starttls()
    servidor.ehlo()
    return servidor


def enviar(servidor: smtplib.SMTP, de: str, usuario: str, para: str) -> None:
    if usuario and de.lower() != usuario.lower():
        print(f"· Aviso: el remitente ({de}) no es la cuenta autenticada ({usuario}).")
        print("  Gmail y otros rechazan ese envío, o lo aceptan la primera vez y luego no. Conviene igualarlos.")
    mensaje = EmailMessage()
    mensaje["From"] = de
    mensaje["To"] = para
    mensaje["Subject"] = ASUNTO_DE_PRUEBA
    mensaje.set_content(CUERPO_DE_PRUEBA)
    servidor.send_message(mensaje)


def revisar(opciones: argparse.Namespace) -> int:
    if not opciones.host.strip():
        print("Falta el host: pon --host smtp.gmail.com o deja SMTP_HOST en el entorno.", file=sys.stderr)
        return 1
    de = opciones.de.strip() or opciones.usuario.strip()
    # El remitente solo hace falta cuando se construye un sobre: un sondeo de host y TLS con `--sin-login`
    # no necesita ninguna dirección, y exigírsela sería un motivo de más para no hacer la comprobación.
    if not de and opciones.para.strip():
        print("Falta el remitente: sin `--de` ni `--usuario` no se puede construir el sobre.", file=sys.stderr)
        return 1

    modo = "SSL directo" if opciones.ssl else "STARTTLS"
    remitente = f"como remitente {de}" if de else "sin remitente, no hay sobre que construir"
    print(f"Comprobando {modo} contra {opciones.host}:{opciones.puerto}, {remitente}.")

    servidor = conectar(opciones.host, opciones.puerto, opciones.ssl)
    login_aceptado = False
    try:
        print("· TLS negociado.")
        if opciones.sin_login:
            print("· Sin login, como pediste: host y TLS verificados.")
        elif opciones.usuario.strip():
            contrasena = pedir_contrasena(opciones.desde_variable)
            if not contrasena:
                print("No se escribió ninguna contraseña: el login no se intenta.", file=sys.stderr)
                return 1
            servidor.login(opciones.usuario.strip(), contrasena)
            login_aceptado = True
            print(f"· Login aceptado como {opciones.usuario.strip()}.")
        else:
            print("· Sin SMTP_USUARIO: no se intenta el login, igual que hace `Smtp._conectar`.")

        if not opciones.para.strip():
            # La frase cambia según lo que se haya comprobado de verdad: decir «credenciales verificadas»
            # tras un `--sin-login` mandaría a dormir al fundador con una puerta abierta.
            alcance = (
                "Conexión y credenciales verificadas."
                if login_aceptado
                else "Conexión verificada; las credenciales no se han probado."
            )
            print(f"· Sin `--para`: no se ha mandado ningún correo. {alcance}")
            return 0
        enviar(servidor, de, opciones.usuario.strip(), opciones.para.strip())
        print(f"· Entregado el mensaje de prueba a {opciones.para.strip()}: ábrelo y mira el remitente real.")
        return 0
    finally:
        servidor.quit()


def explicar(exc: Exception) -> Never:
    """El motivo con su arreglo, porque un `SMTPAuthenticationError` pelado no le dice nada a nadie."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        arreglo = (
            "Con Gmail no vale la contraseña de la cuenta: hace falta la *contraseña de aplicación* de 16 "
            "caracteres, y para generarla hay que tener activada la verificación en dos pasos. Si la cuenta "
            "es de Google Workspace de un dominio propio, puede que el administrador tenga apagadas las "
            "contraseñas de aplicación."
        )
    elif isinstance(exc, smtplib.SMTPNotSupportedError):
        arreglo = "Este puerto no habla STARTTLS. En Gmail y Zoho el 465 va con SSL directo: --puerto 465 --ssl."
    elif isinstance(exc, smtplib.SMTPSenderRefused):
        # El nombre del atributo cambió entre versiones de Python (`smtp_sender` en 3.12, que es la
        # imagen del contenedor; `sender` en 3.14, que es la del terminal). Se lee el que haya, porque
        # un `AttributeError` dentro del mensaje de error sería lo peor de este sondeo.
        rechazada = getattr(exc, "smtp_sender", None) or getattr(exc, "sender", "la dirección del `From`")
        arreglo = f"Rechazó el remitente {rechazada!r}: en Gmail y Outlook el `From` es la cuenta autenticada."
    elif isinstance(exc, smtplib.SMTPRecipientsRefused):
        arreglo = f"Rechazó al destinatario: {exc.recipients}. Si es tu cuenta, revisa que el buzón exista."
    elif isinstance(exc, ssl.SSLCertVerificationError):
        arreglo = (
            "No se pudo verificar el certificado del proveedor desde esta máquina. Suele ser el almacén de "
            "certificados de Windows o un proxy en medio, no la agenda."
        )
    elif isinstance(exc, socket.gaierror):
        arreglo = "El host no resuelve: mira el nombre (smtp.gmail.com, no gmail.com) y la conexión."
    elif isinstance(exc, (ConnectionRefusedError, TimeoutError)):
        # `socket.timeout` es `TimeoutError` desde 3.10, así que nombrarlo otra vez solo ensucia.
        arreglo = "Puerto cerrado o sin respuesta: tu red puede bloquear 587/465. Prueba desde otra conexión."
    elif isinstance(exc, smtplib.SMTPException):
        # Va antes del `OSError` general porque `SMTPException` hereda de `OSError`: si no, un servidor que
        # corta la conexión quedaría explicado como si fuera la red, y no es lo mismo.
        arreglo = "El proveedor respondió algo inesperado. Repite con --sin-login para separar red de credenciales."
    elif isinstance(exc, OSError):
        arreglo = "Fallo de red al hablar con el proveedor. Repite el sondeo desde otra conexión."
    else:
        arreglo = "Revisa el comando y el entorno; la salida de arriba dice hasta dónde llegó."
    # Sin este `flush` el orden se rompe cuando la salida va a un fichero: stdout va con búfer y stderr no,
    # y el "Qué hacer" aparece antes de la línea que explicó qué se estaba comprobando.
    sys.stdout.flush()
    print(f"\nNo pasó: {exc}\nQué hacer: {arreglo}", file=sys.stderr)
    raise SystemExit(1)


def preparar_salida() -> None:
    """Para que un terminal con página de códigos antigua no revierta un diagnóstico que salió bien.

    Sin `errors="replace"`, imprimir la viñeta contra la codificación de `cmd.exe` lanza
    `UnicodeEncodeError` y el sondeo se cae justo cuando había verificado el envío.
    """
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    preparar_salida()
    try:
        raise SystemExit(revisar(argumentos()))
    except SystemExit:
        raise
    except Exception as exc:  # un rastro de smtplib no le sirve a nadie; el motivo y su arreglo, sí
        explicar(exc)
