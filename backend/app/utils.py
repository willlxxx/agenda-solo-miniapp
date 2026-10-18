"""Utilidades compartidas."""

import re
import unicodedata


def normalizar_slug(texto: str) -> str:
    """'Dr. García' -> 'dr-garcia'. Sin acentos, minúsculas, guiones."""
    sin_acentos = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", sin_acentos.lower()).strip("-")
    return slug or "doctor"


async def slug_disponible(base: str, existe) -> str:
    """Añade sufijo numérico hasta encontrar un slug libre. `existe` es un awaitable(str)->bool."""
    slug = base
    n = 2
    while await existe(slug):
        slug = f"{base}-{n}"
        n += 1
    return slug
