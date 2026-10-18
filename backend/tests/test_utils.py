"""Utilidades puras y endpoints sin autenticación."""

from httpx import AsyncClient

from app.utils import normalizar_slug


def test_normalizar_slug_quita_acentos_y_puntuacion():
    assert normalizar_slug("Dr. García Peña") == "dr-garcia-pena"
    assert normalizar_slug("  O'Doherty & Hijos  ") == "o-doherty-hijos"
    assert normalizar_slug("123 !!!") == "123"


def test_normalizar_slug_vacio_cae_a_doctor():
    assert normalizar_slug("   ???   ") == "doctor"


def test_slug_disponible_no_muta_un_slug_libre():
    import asyncio

    from app.utils import slug_disponible

    async def ocupado(_: str) -> bool:
        return False

    assert asyncio.run(slug_disponible("dra-lopez", ocupado)) == "dra-lopez"


def test_slug_disponible_anade_sufijos_hasta_librarse():
    import asyncio

    from app.utils import slug_disponible

    ocupados = {"dra-lopez", "dra-lopez-2"}

    async def existe(slug: str) -> bool:
        return slug in ocupados

    assert asyncio.run(slug_disponible("dra-lopez", existe)) == "dra-lopez-3"


async def test_healthz_no_pide_sesion(client: AsyncClient):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"estado": "ok"}
