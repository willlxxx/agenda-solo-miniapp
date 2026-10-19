"""El emparejamiento lista-de-espera ↔ hueco libre, sin base de datos: pura aritmética de horas.

Aquí se fija lo que el paciente entiende cuando marca "por la mañana" o "el martes 21", y cómo
redactamos el aviso. Ninguna de esas dos cosas depende del locale ni del sistema: los días salen
en español escrito a propósito.
"""

import uuid
from datetime import date, time

from app.domain.disponibilidad import DiaDisponible, Hueco
from app.domain.encaje import (
    Peticion,
    dia_largo,
    enlace_correo,
    enlace_whatsapp,
    huecos_de,
    mejor_hueco,
    mensaje_de_aviso,
    telefono_internacional,
)

SERVICIO = uuid.uuid4()
ENTRADA = uuid.uuid4()
LUNES = date(2026, 10, 19)
MARTES = date(2026, 10, 20)
MIERCOLES = date(2026, 10, 21)


def peticion(**cambios) -> Peticion:
    base = dict(
        entrada_id=ENTRADA,
        paciente_id=uuid.uuid4(),
        nombre="Ana Paciente",
        telefono="655112233",
        email=None,
        servicio_id=SERVICIO,
        servicio="Revisión",
        fecha=None,
        preferencia="cualquier_hora",
    )
    return Peticion(**{**base, **cambios})


def dia(fecha: date, *horas: tuple[str, str]) -> DiaDisponible:
    return DiaDisponible(
        fecha=fecha,
        huecos=tuple(
            Hueco(time(*map(int, inicio.split(":"))), time(*map(int, fin.split(":"))))
            for inicio, fin in horas
        ),
    )


def dos_dias() -> list[DiaDisponible]:
    return [
        dia(LUNES, ("09:00", "09:30"), ("11:15", "11:45")),
        dia(MARTES, ("10:00", "10:30"), ("16:30", "17:00")),
    ]


# --- Qué hueco se ofrece ---


def test_sin_dia_fijo_se_ofrece_el_primer_hueco_del_horizonte():
    encontrado = mejor_hueco(dos_dias(), peticion())
    assert encontrado is not None
    assert (encontrado.fecha, encontrado.inicio, encontrado.fin) == (LUNES, time(9, 0), time(9, 30))


def test_dia_exacto_sin_hueco_deja_al_paciente_esperando():
    """Si pidió el martes y el martes no tiene hueco, sigue esperando: no le cambiamos el día."""
    encontrado = mejor_hueco(dos_dias(), peticion(fecha=MIERCOLES))
    assert encontrado is None


def test_dia_exacto_usa_su_primera_hora():
    """Lo mismo pero con un día que sí existe en el horizonte: coge su primer hueco libre."""
    encontrado = mejor_hueco(dos_dias(), peticion(fecha=MARTES))
    assert encontrado is not None and encontrado.inicio == time(10, 0)


def test_preferencia_de_manana_descarta_la_tarde():
    encontrado = mejor_hueco(dos_dias(), peticion(preferencia="manana"))
    assert encontrado is not None
    assert encontrado.inicio < time(14, 0)
    assert encontrado.fecha == LUNES


def test_preferencia_de_tarde_solo_coge_lo_que_empieza_desde_las_14():
    dias = [dia(LUNES, ("09:00", "09:30"), ("13:45", "14:15")), dia(MARTES, ("16:30", "17:00"))]
    encontrado = mejor_hueco(dias, peticion(preferencia="tarde"))
    assert encontrado is not None
    assert (encontrado.fecha, encontrado.inicio) == (MARTES, time(16, 30))


def test_el_corte_de_las_14_es_el_que_entiende_el_paciente():
    """13:45 cuenta como mañana y 14:00 como tarde, aunque el médico abra a las 9."""
    assert mejor_hueco([dia(LUNES, ("13:45", "14:15"))], peticion(preferencia="manana")) is not None
    assert mejor_hueco([dia(LUNES, ("13:45", "14:15"))], peticion(preferencia="tarde")) is None
    assert mejor_hueco([dia(LUNES, ("14:00", "14:30"))], peticion(preferencia="tarde")) is not None


def test_horizonte_vacio_no_da_nada():
    assert mejor_hueco([], peticion()) is None


# --- Varias peticiones a la vez ---


def test_cada_peticion_mira_el_hueco_de_su_servicio():
    limpieza = uuid.uuid4()
    peticiones = [peticion(), peticion(entrada_id=uuid.uuid4(), servicio_id=limpieza, servicio="Limpieza")]
    por_servicio = {SERVICIO: [dia(LUNES, ("09:00", "09:30"))], limpieza: [dia(MARTES, ("12:00", "12:30"))]}
    encajes = huecos_de(por_servicio, peticiones)
    assert [(e.entrada_id, e.fecha) for e in encajes] == [(ENTRADA, LUNES), (peticiones[1].entrada_id, MARTES)]


def test_las_que_no_encajan_no_aparecen_y_el_orden_es_el_de_llegada():
    sin_hueco = uuid.uuid4()
    peticiones = [
        peticion(),
        peticion(entrada_id=sin_hueco, servicio_id=sin_hueco),
        peticion(entrada_id=uuid.uuid4(), preferencia="tarde"),
    ]
    encajes = huecos_de({SERVICIO: dos_dias()}, peticiones)
    assert [e.entrada_id for e in encajes] == [ENTRADA, peticiones[2].entrada_id]
    # El lunes solo tiene mañanas, así que quien pidió tarde sale del martes por la tarde.
    assert encajes[1].fecha == MARTES and encajes[1].inicio == time(16, 30)


def test_dos_personas_no_reciben_el_mismo_hueco():
    """Un hueco se ofrece una vez: escribir a dos el mismo martes sería peor que no decir nada."""
    peticiones = [peticion(), peticion(entrada_id=uuid.uuid4())]
    encajes = huecos_de({SERVICIO: dos_dias()}, peticiones)
    assert [(e.fecha, e.inicio) for e in encajes] == [(LUNES, time(9, 0)), (LUNES, time(11, 15))]


def test_lo_que_pisaba_el_hueco_asignado_tampoco_se_ofrece():
    """Con cita a las 09:00-09:30 la rejilla real vuelve a las 09:30: 09:15 ya no sirve."""
    rejilla = [dia(LUNES, ("09:00", "09:30"), ("09:15", "09:45"), ("09:30", "10:00"))]
    peticiones = [peticion(), peticion(entrada_id=uuid.uuid4())]
    encajes = huecos_de({SERVICIO: rejilla}, peticiones)
    assert [e.inicio for e in encajes] == [time(9, 0), time(9, 30)]


def test_cuando_no_quedan_huecos_la_tercera_sigue_esperando():
    peticiones = [peticion(), peticion(entrada_id=uuid.uuid4()), peticion(entrada_id=uuid.uuid4())]
    encajes = huecos_de({SERVICIO: [dia(LUNES, ("09:00", "09:30"))]}, peticiones)
    assert [e.entrada_id for e in encajes] == [ENTRADA]


def test_el_hueco_del_dia_pedido_no_se_presta_a_otro():
    """Si la tarde del martes es el único hueco, se la lleva quien lo pidió primero."""
    peticiones = [
        peticion(fecha=MARTES, preferencia="tarde"),
        peticion(entrada_id=uuid.uuid4(), preferencia="tarde"),
    ]
    encajes = huecos_de({SERVICIO: dos_dias()}, peticiones)
    assert [(e.entrada_id, e.fecha, e.inicio) for e in encajes] == [(ENTRADA, MARTES, time(16, 30))]


# --- Teléfono: cuándo podemos prometer un WhatsApp ---


def test_el_telefono_nacional_espanol_sale_con_prefijo():
    assert telefono_internacional("655112233") == "34655112233"
    assert telefono_internacional("91 555 44 33") == "34915554433"
    assert telefono_internacional(" 655-11-22-33 ") == "34655112233"


def test_formas_ya_internacionales_se_respetan():
    assert telefono_internacional("+34 655 11 22 33") == "34655112233"
    assert telefono_internacional("0034655112233") == "34655112233"
    assert telefono_internacional("34655112233") == "34655112233"
    assert telefono_internacional("+1 415 555 2671") == "14155552671"


def test_lo_que_no_es_un_numero_no_inventa_un_wa_me():
    for raro in (None, "", "llamad al 900", "655", "abc", "+12", "900"):
        assert telefono_internacional(raro) is None


def test_los_separadores_y_letras_sueltas_no_impiden_el_link():
    """Limpiamos signos y espacios: la gente escribe '655 11 22 33' o '+34 (655) 112233'."""
    assert telefono_internacional("+34 (655) 11-22-33") == "34655112233"


def test_enlace_whatsapp_lleva_el_mensaje_codificado():
    enlace = enlace_whatsapp("655112233", "Ha quedado un hueco el martes")
    assert enlace is not None
    assert enlace.startswith("https://wa.me/34655112233?text=")
    assert "Ha%20quedado%20un%20hueco%20el%20martes" in enlace
    assert enlace_whatsapp("no-es-numero", "hola") is None


def test_enlace_de_correo_con_asunto_y_cuerpo():
    correo = enlace_correo("ana@example.com", "cuerpo del aviso", "Hueco libre")
    assert correo is not None and correo.startswith("mailto:ana@example.com?subject=")
    assert "cuerpo%20del%20aviso" in correo
    assert enlace_correo(None, "x", "y") is None


# --- Lo que lee el paciente ---


def test_dia_largo_en_espanol_fijo():
    assert dia_largo(MIERCOLES) == "miércoles, 21 de octubre"
    assert dia_largo(date(2026, 12, 25)) == "viernes, 25 de diciembre"


def test_el_mensaje_dice_quien_llama_y_no_promete_una_cita():
    encontrado = mejor_hueco(dos_dias(), peticion(fecha=MARTES))
    assert encontrado is not None
    texto = mensaje_de_aviso(peticion(), encontrado, doctor="Dra. García Peña")
    assert "Ana" in texto and "revisión" in texto.lower()
    assert "martes, 20 de octubre" in texto and "10:00" in texto
    assert "Dra. García Peña" in texto
    # Todavía no hay reserva: el mensaje no puede decir que la cita ya es suya.
    assert "tienes cita" not in texto and "reservada" not in texto


def test_un_nombre_sin_apellidos_no_rompe_el_saludo():
    encontrado = mejor_hueco(dos_dias(), peticion(nombre="Pili"))
    assert encontrado is not None
    assert mensaje_de_aviso(peticion(nombre="Pili"), encontrado, doctor="Dr Lopez").startswith("Hola Pili:")


def test_sin_nombre_y_sin_servicio_el_texto_sale_legible():
    """Un servicio borrado desde la reserva deja `servicio` vacío: el aviso no puede decir 'hueco de None'."""
    encontrado = mejor_hueco(dos_dias(), peticion(servicio=None))
    assert encontrado is not None
    texto = mensaje_de_aviso(peticion(servicio=None, nombre=""), encontrado, doctor="Dra. García Peña")
    assert "hueco de tu visita" in texto and "Hola" not in texto


def test_encaje_lleva_la_entrada_para_poder_reservar_despues():
    encontrado = mejor_hueco(dos_dias(), peticion())
    assert encontrado is not None and encontrado.entrada_id == ENTRADA
