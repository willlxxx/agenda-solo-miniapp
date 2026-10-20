"""Supresión y export de la ficha del paciente (RGPD art. 15, 17 y 20).

Contra PostgreSQL real porque lo que se prueba es una clave foránea: `appointments.patient_id` no
tiene `ondelete`, así que el orden de las escrituras decide si el borrado sale o PG lo corta con un
`IntegrityError`. Y porque la propiedad que de verdad importa —el hueco de una cita ya atendida
sigue ocupado aunque ya no sepamos de quién era— depende de que la fila sobreviva.
"""

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from httpx import AsyncClient

from app.api import deps
from app.domain.borrado import confirmacion, explicacion_de_conserva, nombre_de_archivo
from tests.conftest import DOCTOR_LOPEZ, nueva_sesion, registrar, sql_filas

MADRID = ZoneInfo("Europe/Madrid")
ANA = {"nombre": "Ana Ibars", "telefono": "+34 611 22 33 44", "email": "ana.ibars@example.com"}


async def servicio_id(client: AsyncClient, nombre: str = "Revisión") -> str:
    filas = await client.get("/api/v1/services")
    return next(f["id"] for f in filas.json() if f["nombre"] == nombre)


async def primer_hueco(client: AsyncClient, slug: str, servicio: str) -> tuple[str, str]:
    """El primer hueco real del horizonte, como en el resto de la suite: sin franjas fijadas."""
    resp = await client.get(
        f"/api/v1/public/{slug}/disponibilidad", params={"servicio": servicio, "dias": 14}
    )
    assert resp.status_code == 200, resp.text
    for dia in resp.json()["dias"]:
        for hueco in dia["huecos"]:
            return dia["fecha"], hueco["inicio"][:5]
    raise AssertionError("El doctor no ofrece ningún hueco")


async def reservar(client: AsyncClient, slug: str, servicio: str, *, correo: str | None = ANA["email"]):
    """Reserva por el enlace público: ahí nace la ficha, con el consentimiento firmado."""
    fecha, hora = await primer_hueco(client, slug, servicio)
    cuerpo = {"servicio": servicio, "fecha": fecha, "hora": hora, **{**ANA, "email": correo}, "consentimiento": True}
    resp = await client.post(f"/api/v1/public/{slug}/citas", json=cuerpo)
    assert resp.status_code == 201, resp.text
    return fecha, hora


async def apuntar_a_lista(client: AsyncClient, slug: str, servicio: str) -> dict:
    resp = await client.post(
        f"/api/v1/public/{slug}/lista-espera",
        json={"servicio": servicio, **ANA, "preferencia": "manana", "consentimiento": True},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def agenda(client: AsyncClient, desde: str, dias: int = 7) -> list[dict]:
    resp = await client.get("/api/v1/appointments", params={"desde": desde, "dias": dias})
    assert resp.status_code == 200, resp.text
    return resp.json()["citas"]


async def cita_de(client: AsyncClient, fecha: str) -> dict:
    """La fila de la agenda de ese día."""
    citas = [c for c in await agenda(client, fecha, 1) if c["fecha"] == fecha]
    assert citas, f"no hay citas el {fecha}"
    return citas[0]


async def cita_con_paciente(client: AsyncClient, fecha: str) -> dict:
    """La primera fila de ese día que todavía tiene identidad.

    Tras un borrado hay dos citas en el mismo día: la anónima de antes y la nueva. Buscar la primera
    a secas devolvería la que ya no tiene ficha, que es justo lo que el test quiere descartar.
    """
    for cita in await agenda(client, fecha, 7):
        if cita["paciente"] is not None:
            return cita
    raise AssertionError("ninguna cita de la ventana tiene paciente")


async def ficha_por_la_cita(client: AsyncClient, cita_id: str) -> dict:
    resp = await client.get(f"/api/v1/pacientes/de-cita/{cita_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def id_de_la_ficha(client: AsyncClient, cita_id: str) -> str:
    return (await ficha_por_la_cita(client, cita_id))["id"]


async def id_por_nombre(nombre: str = ANA["nombre"]) -> str:
    """La ficha por su nombre, para cuando aún no hay ninguna cita que la señale."""
    filas = await sql_filas("select id from patients where nombre = :n", n=nombre)
    assert filas, "no existe esa ficha"
    return str(filas[0][0])


async def escribir(consulta: str, **params) -> None:
    """Escritura directa en BD. `sql_filas` no sirve para esto: recorre el resultado y no hace commit."""
    from sqlalchemy import text

    async with deps.SessionLocal() as session:
        await session.execute(text(consulta), params)
        await session.commit()


async def con_una_cita(client: AsyncClient, doctor: dict) -> tuple[str, dict]:
    """Deja una reserva hecha y devuelve (fecha, fila de la agenda)."""
    servicio = await servicio_id(client)
    fecha, _ = await reservar(client, doctor["slug_unico"], servicio)
    return fecha, await cita_de(client, fecha)


# --- Lo que decide el dominio, sin tocar la base de datos ---


def test_el_nombre_del_archivo_no_depende_del_teclado():
    assert nombre_de_archivo("Ana Pacíente Gómez") == "datos-de-ana-paciente-gomez.json"
    assert nombre_de_archivo("  Ñ  ") == "datos-de-paciente.json"


def test_borrar_a_quien_no_tuvo_citas_no_deja_nada_que_contar():
    texto = confirmacion("Ana Ibars", 0, 0, 0)
    assert "no queda ningún rastro" in texto
    assert "Quedan" not in texto


def test_la_frase_de_borrado_dice_cuantas_siguen_por_llegar():
    texto = confirmacion("Ana Ibars", 3, 1, 0)
    assert "3 citas sin nombre" in texto and "1 sigue por llegar" in texto


def test_solo_peticiones_no_habla_de_pruebas_de_consentimiento():
    texto = confirmacion("Ana Ibars", 0, 0, 2)
    assert "2 peticiones de la lista de espera sin nombre" in texto
    assert "consentimiento" not in texto


def test_el_texto_jamas_promete_borrar_las_citas():
    """El producto no puede decir 'se borra todo': la fila se queda y el texto lo dice."""
    texto = confirmacion("Ana Ibars", 2, 0, 1)
    assert "2 citas sin nombre" in texto and "se conserva su constancia" in texto
    assert "No se puede deshacer" in texto
    assert "cita" in explicacion_de_conserva()


# --- Leer la ficha desde la agenda ---


async def test_la_ficha_sale_por_la_cita_con_lo_que_hay_sobre_esa_persona(client: AsyncClient, doctor: dict):
    _, cita = await con_una_cita(client, doctor)

    ficha = await ficha_por_la_cita(client, cita["id"])
    assert ficha["nombre"] == ANA["nombre"]
    # La agenda solo pinta nombre y teléfono; el email sale aquí, que es donde hace falta.
    assert ficha["email"] == ANA["email"]
    assert len(ficha["citas"]) == 1 and ficha["citas"][0]["estado"] == "confirmada"
    assert ficha["peticiones"] == []
    assert ficha["aviso"].startswith(f"Se suprime la ficha de {ANA['nombre']}")


async def test_la_agenda_sigue_sin_repartir_el_id_del_paciente(client: AsyncClient, doctor: dict):
    """El invariante de privacidad que ya protegía `test_agenda`: la lista no lleva ids de ficha."""
    _, cita = await con_una_cita(client, doctor)
    assert set(cita["paciente"]) == {"nombre", "telefono", "email"}
    assert "paciente_id" not in cita


async def test_una_cita_sin_paciente_no_tiene_ficha_que_abrir(client: AsyncClient, doctor: dict):
    _, cita = await con_una_cita(client, doctor)
    await escribir("update appointments set patient_id = null where id = cast(:c as uuid)", c=cita["id"])

    resp = await client.get(f"/api/v1/pacientes/de-cita/{cita['id']}")
    assert resp.status_code == 404
    assert "no encontrado" in resp.json()["detail"].lower()


async def test_sin_sesion_no_se_ve_ninguna_ficha(client: AsyncClient, doctor: dict):
    _, cita = await con_una_cita(client, doctor)
    otra = nueva_sesion()

    assert (await otra.get(f"/api/v1/pacientes/de-cita/{cita['id']}")).status_code == 401
    assert (await otra.get(f"/api/v1/pacientes/{uuid.uuid4()}/datos")).status_code == 401
    assert (await otra.delete(f"/api/v1/pacientes/{uuid.uuid4()}")).status_code == 401


async def test_la_ficha_de_otro_doctor_no_existe_para_mi(client: AsyncClient, doctor: dict):
    _, cita = await con_una_cita(client, doctor)

    otra = nueva_sesion()
    await registrar(otra, DOCTOR_LOPEZ)
    assert (await otra.get(f"/api/v1/pacientes/de-cita/{cita['id']}")).status_code == 404


# --- La descarga de datos ---


async def test_la_descarga_sale_como_archivo_con_nombre_legible(client: AsyncClient, doctor: dict):
    _, cita = await con_una_cita(client, doctor)
    id_ = await id_de_la_ficha(client, cita["id"])

    resp = await client.get(f"/api/v1/pacientes/{id_}/datos")
    assert resp.status_code == 200, resp.text
    assert 'attachment; filename="datos-de-ana-ibars.json"' in resp.headers["content-disposition"]

    cuerpo = resp.json()
    assert cuerpo["de_quien_son_los_datos"] == ANA["nombre"]
    assert cuerpo["ficha"]["email"] == ANA["email"]
    assert len(cuerpo["citas"]) == 1
    assert "generado_el" in cuerpo and "consultorio" in cuerpo


async def test_la_descarga_dice_quien_escribio_cada_cita(client: AsyncClient, doctor: dict):
    _, cita = await con_una_cita(client, doctor)
    id_ = await id_de_la_ficha(client, cita["id"])

    cuerpo = (await client.get(f"/api/v1/pacientes/{id_}/datos")).json()
    assert cuerpo["citas"][0]["como_entró"] == "el paciente desde su enlace"


async def test_la_descarga_deja_claro_que_no_hay_datos_clinicos(client: AsyncClient, doctor: dict):
    """Honestidad hacia el paciente: el archivo dice lo que NO se guarda, no solo lo que hay."""
    _, cita = await con_una_cita(client, doctor)
    id_ = await id_de_la_ficha(client, cita["id"])

    cuerpo = (await client.get(f"/api/v1/pacientes/{id_}/datos")).json()
    assert "Diagnósticos" in cuerpo["no_se_guarda"]
    assert "al_suprimir" in cuerpo and "no se puede deshacer" in cuerpo["al_suprimir"].lower()


async def test_la_descarga_de_un_paciente_ajeno_es_404(client: AsyncClient, doctor: dict):
    _, cita = await con_una_cita(client, doctor)
    id_ = await id_de_la_ficha(client, cita["id"])

    otra = nueva_sesion()
    await registrar(otra, DOCTOR_LOPEZ)
    assert (await otra.get(f"/api/v1/pacientes/{id_}/datos")).status_code == 404


# --- Suprimir ---


async def test_suprimir_deja_la_cita_sin_identidad_y_su_prueba_intacta(client: AsyncClient, doctor: dict):
    _, cita = await con_una_cita(client, doctor)
    id_ = await id_de_la_ficha(client, cita["id"])

    resp = await client.delete(f"/api/v1/pacientes/{id_}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["citas_sin_identidad"] == 1

    paciente, consentimiento, estado, origen = (
        await sql_filas(
            "select patient_id, consentimiento_ok_en, estado, origen from appointments where id = cast(:c as uuid)",
            c=cita["id"],
        )
    )[0]
    assert paciente is None
    assert consentimiento is not None and estado == "confirmada" and origen == "publico"
    assert await sql_filas("select 1 from patients where id = cast(:p as uuid)", p=id_) == []


async def test_el_hueco_de_una_cita_borrada_sigue_ocupado(client: AsyncClient, doctor: dict):
    """La razón del diseño: si la fila se fuera con la ficha, alguien reservaría una hora atendida."""
    servicio = await servicio_id(client)
    slug = doctor["slug_unico"]
    fecha, hora = await reservar(client, slug, servicio)
    await client.delete(f"/api/v1/pacientes/{await id_de_la_ficha(client, (await cita_de(client, fecha))['id'])}")

    resp = await client.get(f"/api/v1/public/{slug}/disponibilidad", params={"servicio": servicio, "dias": 14})
    ofrecidas = {h["inicio"][:5] for d in resp.json()["dias"] if d["fecha"] == fecha for h in d["huecos"]}
    assert hora not in ofrecidas


async def test_la_agenda_pinta_la_cita_como_paciente_borrado(client: AsyncClient, doctor: dict):
    fecha, cita = await con_una_cita(client, doctor)
    await client.delete(f"/api/v1/pacientes/{await id_de_la_ficha(client, cita['id'])}")

    tras = await cita_de(client, fecha)
    assert tras["id"] == cita["id"] and tras["paciente"] is None


async def test_suprimir_no_deja_el_email_en_ningun_sitio(client: AsyncClient, doctor: dict):
    _, cita = await con_una_cita(client, doctor)
    await client.delete(f"/api/v1/pacientes/{await id_de_la_ficha(client, cita['id'])}")

    assert int((await sql_filas("select count(*) from patients where email = :e", e=ANA["email"]))[0][0]) == 0


async def test_la_lista_de_espera_sigue_visible_sin_nombre(client: AsyncClient, doctor: dict):
    """Soltar la identidad no tira la demanda: el día y el servicio pedidos siguen siendo útiles."""
    servicio = await servicio_id(client)
    slug = doctor["slug_unico"]
    await apuntar_a_lista(client, slug, servicio)
    _, cita = await con_una_cita(client, doctor)

    borrado = await client.delete(f"/api/v1/pacientes/{await id_de_la_ficha(client, cita['id'])}")
    assert borrado.status_code == 200 and borrado.json()["peticiones_sin_identidad"] == 1

    lista = (await client.get("/api/v1/waitlist", params={"filtro": "todas"})).json()
    assert lista["total"] == 1
    assert lista["entradas"][0]["nombre"] == "Petición sin nombre"
    assert lista["entradas"][0]["email"] is None and lista["entradas"][0]["servicio"] == "Revisión"


async def test_el_aviso_del_borrado_cuenta_lo_que_de_verdad_pasa(client: AsyncClient, doctor: dict):
    servicio = await servicio_id(client)
    slug = doctor["slug_unico"]
    primera, _ = await reservar(client, slug, servicio)
    await reservar(client, slug, servicio)  # mismo nombre y teléfono: es la misma ficha
    await apuntar_a_lista(client, slug, servicio)

    id_ = await id_de_la_ficha(client, (await cita_de(client, primera))["id"])
    cuerpo = (await client.delete(f"/api/v1/pacientes/{id_}")).json()
    assert cuerpo["citas_sin_identidad"] == 2
    # Las dos van a futuro: salieron del horizonte de disponibilidad.
    assert cuerpo["citas_por_llegar"] == 2
    assert "2 citas sin nombre" in cuerpo["aviso"] and "1 petición" in cuerpo["aviso"]


async def test_borrar_a_quien_no_tuvo_nada_tambien_vale(client: AsyncClient, doctor: dict):
    """Se apuntó a la lista, el médico quitó la petición y quedó la ficha huérfana: hay que poder
    borrarla igual, que es el caso más frecuente de un 'no me constes'."""
    servicio = await servicio_id(client)
    entrada = await apuntar_a_lista(client, doctor["slug_unico"], servicio)
    await client.delete(f"/api/v1/waitlist/{entrada['id']}")

    resp = await client.delete(f"/api/v1/pacientes/{await id_por_nombre()}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["citas_sin_identidad"] == 0 and resp.json()["peticiones_sin_identidad"] == 0
    assert "no queda ningún rastro" in resp.json()["aviso"]


async def test_borrar_dos_veces_no_inventa_una_segunda_ficha(client: AsyncClient, doctor: dict):
    _, cita = await con_una_cita(client, doctor)
    id_ = await id_de_la_ficha(client, cita["id"])

    assert (await client.delete(f"/api/v1/pacientes/{id_}")).status_code == 200
    segundo = await client.delete(f"/api/v1/pacientes/{id_}")
    assert segundo.status_code == 404 and "no encontrado" in segundo.json()["detail"].lower()


async def test_suprimir_al_de_uno_no_toca_lo_del_otro(client: AsyncClient, doctor: dict):
    _, ajena = await con_una_cita(client, doctor)

    otra = nueva_sesion()
    lopez = await registrar(otra, DOCTOR_LOPEZ)
    servicio_lopez = (await otra.get("/api/v1/services")).json()[0]["id"]
    fecha_l, _ = await reservar(otra, lopez["slug_unico"], servicio_lopez)
    id_lopez = await id_de_la_ficha(otra, (await cita_de(otra, fecha_l))["id"])

    assert (await otra.delete(f"/api/v1/pacientes/{id_lopez}")).status_code == 200
    assert (await client.get(f"/api/v1/pacientes/de-cita/{ajena['id']}")).status_code == 200
    assert (await cita_de(client, ajena["fecha"]))["paciente"]["nombre"] == ANA["nombre"]


async def test_si_vuelve_a_reservar_es_una_ficha_nueva(client: AsyncClient, doctor: dict):
    """Lo que afirma el texto del borrado: no hay forma de recuperar al paciente suprimido."""
    servicio = await servicio_id(client)
    slug = doctor["slug_unico"]
    fecha, _ = await reservar(client, slug, servicio)
    id_vieja = await id_de_la_ficha(client, (await cita_de(client, fecha))["id"])
    await client.delete(f"/api/v1/pacientes/{id_vieja}")

    segunda, _ = await reservar(client, slug, servicio)
    id_nueva = await id_de_la_ficha(client, (await cita_con_paciente(client, segunda))["id"])
    assert uuid.UUID(id_nueva) != uuid.UUID(id_vieja)


async def test_la_ultima_visita_se_va_con_la_ficha(client: AsyncClient, doctor: dict):
    _, cita = await con_una_cita(client, doctor)
    id_ = await id_de_la_ficha(client, cita["id"])
    ayer = (datetime.now(MADRID) - timedelta(days=2)).date()
    await escribir("update patients set ultima_visita = :d where id = cast(:p as uuid)", d=ayer, p=id_)

    assert (await client.delete(f"/api/v1/pacientes/{id_}")).status_code == 200
    assert await sql_filas("select ultima_visita from patients where id = cast(:p as uuid)", p=id_) == []


async def test_el_paciente_sin_correo_tambien_se_puede_suprimir(client: AsyncClient, doctor: dict):
    """El email es opcional en el enlace; el derecho a borrar no."""
    servicio = await servicio_id(client)
    fecha, _ = await reservar(client, doctor["slug_unico"], servicio, correo=None)
    cita = await cita_de(client, fecha)
    assert cita["paciente"]["email"] is None

    resp = await client.delete(f"/api/v1/pacientes/{await id_de_la_ficha(client, cita['id'])}")
    assert resp.status_code == 200 and resp.json()["citas_sin_identidad"] == 1
