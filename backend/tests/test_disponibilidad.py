"""Tests del motor de disponibilidad: lógica pura, sin base de datos ni reloj del entorno."""

from datetime import date, datetime, time, timedelta

from app.domain.disponibilidad import CitaLocal, Franja, calcular_disponibilidad, fusionar, restar

LUNES = date(2027, 3, 1)  # lunes real: el test no depende del día en que se corre
MARTES = date(2027, 3, 2)
MIERCOLES = date(2027, 3, 3)

MANANAN = Franja(dia_semana=LUNES.weekday(), hora_inicio=time(9, 0), hora_fin=time(14, 0))
TARDE = Franja(dia_semana=LUNES.weekday(), hora_inicio=time(16, 0), hora_fin=time(20, 0))


def horizonte(desde: date = LUNES, dias: int = 7) -> list[date]:
    return [desde + timedelta(days=i) for i in range(dias)]


def calcular(**cambios):
    base = dict(
        franjas=[MANANAN, TARDE],
        citas=[],
        bloqueados=[],
        duracion_minutos=30,
        fechas=horizonte(),
        ahora=datetime(2027, 2, 20, 8, 0),  # antes del horizonte: nada se recorta por pasado
        paso_minutos=15,
    )
    base.update(cambios)
    return calcular_disponibilidad(**base)


def horas(dia) -> list[tuple[time, time]]:
    return [(h.inicio, h.fin) for h in dia.huecos]


def inicios(dia) -> list[time]:
    return [h.inicio for h in dia.huecos]


# --- Bloques de bajo nivel ---


def test_fusionar_pega_contiguos_y_solapados():
    assert fusionar([(60, 90), (30, 60), (80, 120), (200, 210)]) == [(30, 120), (200, 210)]


def test_fusionar_ignora_intervalos_vacios():
    assert fusionar([(10, 10), (5, 8)]) == [(5, 8)]


def test_restar_devuelve_los_trozos_libres():
    assert restar((0, 100), [(20, 40), (60, 70)]) == [(0, 20), (40, 60), (70, 100)]


def test_restar_ignora_lo_que_cae_fuera_del_rango():
    assert restar((0, 100), [(150, 200)]) == [(0, 100)]
    assert restar((50, 100), [(0, 60)]) == [(60, 100)]


# --- Reglas del producto ---


def test_dia_libre_ofrece_toda_la_rejilla():
    lunes = calcular()[0]
    assert lunes.fecha == LUNES
    # Mañana 9:00-13:30 (19) + tarde 16:00-19:30 (15).
    assert len(lunes.huecos) == 34
    assert horas(lunes)[0] == (time(9, 0), time(9, 30))
    assert horas(lunes)[-1] == (time(19, 30), time(20, 0))


def test_la_cita_tapa_su_hora_pero_no_a_los_vecinos():
    dia = calcular(citas=[CitaLocal(datetime(2027, 3, 1, 10, 0), 30)], fechas=[LUNES]).pop(0)
    empiezan_en = inicios(dia)
    assert time(10, 0) not in empiezan_en
    assert time(9, 30) in empiezan_en  # 9:30-10:00 roza la cita y es válido
    assert time(10, 30) in empiezan_en


def test_la_duracion_debe_caber_entera():
    assert calcular(franjas=[Franja(0, time(9, 0), time(9, 45))], duracion_minutos=60) == []


def test_cabe_justo_antes_de_la_cita_siguiente():
    franja = Franja(LUNES.weekday(), time(9, 0), time(10, 30))
    dia = calcular(
        franjas=[franja],
        duracion_minutos=60,
        citas=[CitaLocal(datetime(2027, 3, 1, 10, 30), 30)],
        fechas=[LUNES],
    ).pop(0)
    assert inicios(dia) == [time(9, 0), time(9, 15), time(9, 30)]


def test_dos_citas_contiguas_no_dejan_hueco_de_quince():
    dia = calcular(
        duracion_minutos=30,
        citas=[CitaLocal(datetime(2027, 3, 1, 10, 0), 15), CitaLocal(datetime(2027, 3, 1, 10, 15), 15)],
        fechas=[LUNES],
    ).pop(0)
    empiezan_en = inicios(dia)
    assert time(10, 0) not in empiezan_en and time(10, 15) not in empiezan_en
    assert time(9, 30) in empiezan_en and time(10, 30) in empiezan_en


def test_dia_bloqueado_no_ofrece_nada():
    assert all(d.fecha != LUNES for d in calcular(bloqueados=[LUNES]))


def test_un_dia_sin_franja_de_ese_dia_semana_no_aparece():
    dias = calcular(franjas=[MANANAN], fechas=horizonte(dias=2))
    assert [d.fecha for d in dias] == [LUNES]


def test_la_cita_de_ayer_al_tarde_tapa_la_madrugada_de_manana():
    franja_martes = Franja(MARTES.weekday(), time(0, 0), time(1, 0))
    dia = calcular(
        franjas=[franja_martes],
        duracion_minutos=30,
        citas=[CitaLocal(datetime(2027, 3, 1, 23, 30), 60)],
        fechas=horizonte(dias=2),
    ).pop(0)
    assert dia.fecha == MARTES
    assert horas(dia) == [(time(0, 30), time(1, 0))]


# --- Recorte por reloj ---


def test_lo_que_ya_paso_no_se_ofrece():
    franja = Franja(MARTES.weekday(), time(9, 0), time(14, 0))
    dia = calcular(franjas=[franja], fechas=[MARTES], ahora=datetime(2027, 3, 2, 11, 7)).pop(0)
    # La rejilla arranca en la apertura (9:00), así que el primer hueco libre es 11:15.
    assert inicios(dia)[:2] == [time(11, 15), time(11, 30)]


def test_la_anticipacion_minima_empuja_la_primera_hora():
    franja = Franja(MARTES.weekday(), time(9, 0), time(14, 0))
    dia = calcular(
        franjas=[franja],
        fechas=[MARTES],
        ahora=datetime(2027, 3, 2, 11, 0),
        anticipacion_minutos=60,
    ).pop(0)
    assert horas(dia)[0] == (time(12, 0), time(12, 30))


def test_los_dias_pasados_desaparecen_y_el_de_hoy_se_recorta():
    martes = Franja(MARTES.weekday(), time(9, 0), time(14, 0))
    miercoles = Franja(MIERCOLES.weekday(), time(9, 0), time(14, 0))
    dias = calcular(
        franjas=[martes, miercoles],
        fechas=horizonte(dias=3),
        ahora=datetime(2027, 3, 3, 12, 0),
    )
    assert [d.fecha for d in dias] == [MIERCOLES]
    assert inicios(dias[0]) == [
        time(12, 0),
        time(12, 15),
        time(12, 30),
        time(12, 45),
        time(13, 0),
        time(13, 15),
        time(13, 30),
    ]


def test_el_tope_por_dia_limita_el_payload():
    dia = calcular(paso_minutos=5, max_huecos_por_dia=10, fechas=[LUNES]).pop(0)
    assert len(dia.huecos) == 10
