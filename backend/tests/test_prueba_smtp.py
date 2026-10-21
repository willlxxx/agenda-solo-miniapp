"""El sondeo de SMTP que se usa antes de escribir las variables en el panel de Railway.

Como `scripts/salud.py` y `scripts/cron.py`, el guion no es un paquete importable: se carga por ruta. Y
aquí lo que se prueba no es la red --eso solo se ve contra el proveedor de verdad--, sino las decisiones
que el fundador no puede repasar cada vez que falla un envío: cómo se lee `SMTP_SSL`, si el script mira
las capacidades correctas del EHLO (es `esmtp_features`; `extensions` no existe y dar con eso en
producción sería el peor sitio), si cada error devuelve su arreglo en vez de un rastro inútil, y si el
resumen final dice lo que se comprobó de verdad y no lo que habría gustado comprobar.
"""

import argparse
import importlib.util
import os
import smtplib
import socket
import ssl
import sys
from pathlib import Path

import pytest


def _cargar_guion():
    ruta = Path(__file__).resolve().parents[2] / "scripts" / "prueba-smtp.py"
    espec = importlib.util.spec_from_file_location("sondeo_de_smtp", ruta)
    modulo = importlib.util.module_from_spec(espec)
    espec.loader.exec_module(modulo)
    return modulo


smtp_guion = _cargar_guion()


def opciones(**cambios) -> argparse.Namespace:
    base = {
        "host": "smtp.example.test",
        "puerto": 587,
        "usuario": "",
        "de": "consulta@example.test",
        "ssl": False,
        "para": "",
        "desde_variable": False,
        "sin_login": True,
    }
    base.update(cambios)
    return argparse.Namespace(**base)


class ServidorFalso:
    """Un `smtplib.SMTP` de juguete: apunta a lo que el guion lee de verdad y cuenta lo que hizo."""

    def __init__(self, extensiones: dict[str, str]):
        self.esmtp_features = extensiones
        self.ehlo_resp = b"250 hola"
        self.pedidos: list[str] = []
        self.mensaje = None

    def ehlo(self):
        self.pedidos.append("ehlo")

    def starttls(self):
        self.pedidos.append("starttls")

    def login(self, usuario, contrasena):
        self.pedidos.append(f"login:{usuario}:{contrasena}")

    def send_message(self, mensaje):
        self.pedidos.append("send")
        self.mensaje = mensaje

    def quit(self):
        self.pedidos.append("quit")


def test_es_verdadero_acepta_los_modos_habituales_y_nada_mas():
    for valor in ("true", "True", "  TRUE ", "1", "si", "sí", "yes"):
        assert smtp_guion.es_verdadero(valor) is True, valor
    for valor in (None, "", "  ", "false", "0", "no", "smtp"):
        assert smtp_guion.es_verdadero(valor) is False, repr(valor)


def test_los_defectos_se_leen_del_entorno_como_en_el_env():
    # Es el detalle que hace útil al guion: sin argumentos, comprueba lo mismo que comprobará el
    # contenedor, leyendo las mismas variables.
    entorno = {
        "SMTP_HOST": "smtp.zoho.com",
        "SMTP_PORT": "465",
        "SMTP_USUARIO": "consulta@midominio",
        "SMTP_DE": "consulta@midominio",
        "SMTP_SSL": "true",
    }
    anterior = {clave: os.environ.get(clave) for clave in entorno}
    os.environ.update(entorno)
    try:
        leidas = smtp_guion.argumentos([])
    finally:
        for clave, valor in anterior.items():
            if valor is None:
                os.environ.pop(clave, None)
            else:
                os.environ[clave] = valor
    assert (leidas.host, leidas.puerto, leidas.ssl) == ("smtp.zoho.com", 465, True)
    assert leidas.usuario == leidas.de == "consulta@midominio"


def test_sin_host_no_se_intenta_nada():
    assert smtp_guion.revisar(opciones(host="")) == 1


def test_sin_remitente_y_con_destinatario_no_se_puede_construir_el_sobre():
    assert smtp_guion.revisar(opciones(de="", usuario="", para="paciente@example.test")) == 1


def test_sondear_host_y_tls_no_pide_remitente(monkeypatch, capsys):
    # Es el primer paso, el que se hace sin ninguna credencial a la vista: exigir un `--de` aquí sería
    # un motivo más para no comprobar el host antes de escribirlo en el panel.
    servido = ServidorFalso({"starttls": "yes"})
    monkeypatch.setattr(smtplib, "SMTP", lambda *_, **__: servido)

    assert smtp_guion.revisar(opciones(de="", usuario="")) == 0
    dicho = capsys.readouterr().out
    assert "sin remitente" in dicho
    assert servido.pedidos[-1] == "quit"


def test_conectar_mira_las_capacidades_del_ehlo_y_sube_tls(monkeypatch, capsys):
    servido = ServidorFalso({"starttls": "yes", "size": "36700160"})
    monkeypatch.setattr(smtplib, "SMTP", lambda *_, **__: servido)

    devuelto = smtp_guion.conectar("smtp.example.test", 587, ssl_directo=False)

    assert devuelto is servido
    assert servido.pedidos == ["ehlo", "starttls", "ehlo"]
    dicho = capsys.readouterr().out
    assert "size, starttls" in dicho


def test_conectar_no_promete_starttls_que_el_servidor_no_ofrece(monkeypatch):
    servido = ServidorFalso({"size": "36700160"})
    monkeypatch.setattr(smtplib, "SMTP", lambda *_, **__: servido)

    with pytest.raises(smtplib.SMTPNotSupportedError):
        smtp_guion.conectar("smtp.example.test", 25, ssl_directo=False)
    # Sin TLS no hay credenciales: el guion se cae antes de llegar a pedirlas.
    assert servido.pedidos == ["ehlo"]


def test_ssl_directo_no_negocia_starttls(monkeypatch):
    servido = ServidorFalso({})
    abierta = {}

    def falso_ssl(host, puerto, timeout=None):
        abierta.update(host=host, puerto=puerto, timeout=timeout)
        return servido

    monkeypatch.setattr(smtplib, "SMTP_SSL", falso_ssl)
    assert smtp_guion.conectar("smtp.gmail.com", 465, ssl_directo=True) is servido
    assert abierta == {"host": "smtp.gmail.com", "puerto": 465, "timeout": smtp_guion.TIEMPO_LIMITE}
    assert servido.pedidos == []


def test_revisar_cierra_la_conexion_aunque_el_login_falla(monkeypatch):
    servido = ServidorFalso({"starttls": "yes"})
    monkeypatch.setattr(smtplib, "SMTP", lambda *_, **__: servido)
    monkeypatch.setattr(smtp_guion, "pedir_contrasena", lambda _: "se-leyo-una-vez")

    def login_que_rechaza(usuario, contrasena):
        raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")

    servido.login = login_que_rechaza

    with pytest.raises(smtplib.SMTPAuthenticationError):
        smtp_guion.revisar(opciones(usuario="consulta@example.test", sin_login=False))
    # El `finally` existe para esto: un fallo a media conversación no deja un socket abierto.
    assert "quit" in servido.pedidos


def test_el_remitente_distinto_del_autenticado_se_avisa_pero_no_bloquea(monkeypatch, capsys):
    servido = ServidorFalso({"starttls": "yes"})
    monkeypatch.setattr(smtplib, "SMTP", lambda *_, **__: servido)

    smtp_guion.revisar(opciones(usuario="consulta@example.test", de="otro@example.test", para="p@p.test"))
    dicho = capsys.readouterr().out
    assert "no es la cuenta autenticada" in dicho
    assert servido.mensaje["From"] == "otro@example.test"


def test_cuando_coinciden_no_hay_aviso(monkeypatch, capsys):
    servido = ServidorFalso({"starttls": "yes"})
    monkeypatch.setattr(smtplib, "SMTP", lambda *_, **__: servido)

    assert smtp_guion.revisar(opciones(usuario="consulta@example.test", para="p@p.test")) == 0
    assert "no es la cuenta autenticada" not in capsys.readouterr().out


def test_sin_para_no_manda_ningun_correo(monkeypatch):
    servido = ServidorFalso({"starttls": "yes"})
    monkeypatch.setattr(smtplib, "SMTP", lambda *_, **__: servido)

    assert smtp_guion.revisar(opciones()) == 0
    assert "send" not in servido.pedidos
    assert servido.pedidos[-1] == "quit"


def test_sin_login_no_promete_las_credenciales_verificadas(monkeypatch, capsys):
    servido = ServidorFalso({"starttls": "yes"})
    monkeypatch.setattr(smtplib, "SMTP", lambda *_, **__: servido)

    assert smtp_guion.revisar(opciones()) == 0
    dicho = capsys.readouterr().out
    assert "las credenciales no se han probado" in dicho
    assert "Conexión y credenciales verificadas" not in dicho


def test_con_login_aceptado_si_se_pueden_dar_por_verificadas(monkeypatch, capsys):
    servido = ServidorFalso({"starttls": "yes"})
    monkeypatch.setattr(smtplib, "SMTP", lambda *_, **__: servido)
    monkeypatch.setattr(smtp_guion, "pedir_contrasena", lambda _: "la-de-16")

    assert smtp_guion.revisar(opciones(usuario="consulta@example.test", sin_login=False)) == 0
    assert "Conexión y credenciales verificadas" in capsys.readouterr().out


def test_preparar_salida_no_revienta_con_un_terminal_raro(monkeypatch):
    # Es la guarda que evita que el diagnóstico muera al imprimirse: si la sustitución no es posible,
    # se sigue igual y no se lanza nada.
    monkeypatch.setattr(sys, "stdout", object())
    monkeypatch.setattr(sys, "stderr", object())
    smtp_guion.preparar_salida()


@pytest.mark.parametrize(
    ("excepcion", "texto_esperado"),
    [
        (smtplib.SMTPAuthenticationError(535, b"no"), "contraseña de aplicación"),
        (smtplib.SMTPNotSupportedError("sin starttls"), "--puerto 465 --ssl"),
        (smtplib.SMTPSenderRefused(550, b"denied", "x@y.test"), "la cuenta autenticada"),
        (smtplib.SMTPRecipientsRefused({"a@b.test": (550, b"no")}), "destinatario"),
        (ssl.SSLCertVerificationError("certificate verify failed"), "certificado"),
        (socket.gaierror("no existe"), "no resuelve"),
        (TimeoutError("sin respuesta"), "bloquear"),
        (ConnectionRefusedError("cerrado"), "bloquear"),
        (smtplib.SMTPServerDisconnected("cortado"), "--sin-login"),
        (OSError("otro"), "otra conexión"),
        (ValueError("raro"), "la salida de arriba"),
    ],
)
def test_cada_fallo_trae_su_arreglo(capsys, excepcion, texto_esperado):
    with pytest.raises(SystemExit) as salida:
        smtp_guion.explicar(excepcion)
    assert salida.value.code == 1
    err = capsys.readouterr().err
    assert texto_esperado in err
    assert "No pasó:" in err


def test_un_corte_del_servidor_no_se_confunde_con_la_red(capsys):
    """`SMTPException` hereda de `OSError`: si el orden está mal, un corte del proveedor se explica
    como si fuera la conexión, y el fundador se pone a mirar el router en vez del proveedor."""
    with pytest.raises(SystemExit):
        smtp_guion.explicar(smtplib.SMTPServerDisconnected("Connection unexpectedly closed: timed out"))
    dicho = capsys.readouterr().err
    assert "El proveedor respondió algo inesperado" in dicho
    assert "Fallo de red al hablar con el proveedor" not in dicho
