"""Hashing argon2id, JWT y cookies de sesión. Sin estado en memoria (Railway reinicia containers)."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Response

from app.config import get_settings

COOKIE_NAME = "agenda_sesion"

_ph = PasswordHasher()


def hash_password(plaintext: str) -> str:
    return _ph.hash(plaintext)


def verify_password(stored_hash: str, plaintext: str) -> bool:
    try:
        return _ph.verify(stored_hash, plaintext)
    except (VerifyMismatchError, InvalidHashError):
        return False


def crear_token(doctor_id: uuid.UUID) -> str:
    s = get_settings()
    ahora = datetime.now(UTC)
    payload = {"sub": str(doctor_id), "iat": ahora, "exp": ahora + timedelta(days=s.jwt_ttl_dias)}
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decodificar_token(token: str) -> uuid.UUID | None:
    """Devuelve el doctor_id, o None si el token es inválido o expiró."""
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=["HS256"])
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def set_session_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        # La manda el entorno, no FRONTEND_ORIGIN: desde que la interfaz y la API son el mismo origen,
        # el string del dev server (5173) no dice nada de si el sitio va por https. Un despliegue
        # público lleva siempre `Secure`, y en local el navegador no la aceptaría por http.
        secure=s.es_produccion,
        max_age=s.jwt_ttl_dias * 86400,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")
