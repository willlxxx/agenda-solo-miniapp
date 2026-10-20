"""La URL de base de datos que entrega un host, traducida a la que esta app puede abrir.

Pruebas puras: no abren socket ni base de datos. Se comprueba a la vez la función y el validador de
`Settings`, porque lo que se juega en un despliegue es que el valor que llega del entorno sobreviva al
viaje hasta `asyncpg.connect()`.

El caso real que motivó todo esto: Railway inyecta en el servicio `DATABASE_URL` con la forma
`postgresql://usuario:clave@host:6543/base?sslmode=require`. Sin traducir, SQLAlchemy busca psycopg (no
está instalado) y, si alguien pone `+asyncpg` a mano, asyncpg rechaza el argumento:
`TypeError: connect() got an unexpected keyword argument 'sslmode'`.
"""

import pytest

from app.arranque import revisar_arranque
from app.config import SECRETO_DE_DESARROLLO, Settings, normalizar_url_database

SECRETO_BUENO = "x" * 43
URL_DE_HOST = "postgresql://podrcoduser:Vj2n9dXqP4mL@roundhouse.proxy.rlwy.net:6543/railway?sslmode=require"
URL_DE_HOST_CORTA = "postgres://u:p@host.internal:5432/db?sslmode=require"


def test_la_url_que_inyecta_el_host_sale_en_forma_asyncpg():
    """El caso completo: esquema, argumento de TLS y todo lo demás intacto."""
    assert normalizar_url_database(URL_DE_HOST) == (
        "postgresql+asyncpg://podrcoduser:Vj2n9dXqP4mL@roundhouse.proxy.rlwy.net:6543/railway?ssl=require"
    )


def test_el_esquema_postgres_abreviado_tambien_se_reconoce():
    assert normalizar_url_database(URL_DE_HOST_CORTA) == (
        "postgresql+asyncpg://u:p@host.internal:5432/db?ssl=require"
    )


@pytest.mark.parametrize(
    ("modo", "esperado"),
    [
        ("require", "require"),
        ("verify-ca", "verify-ca"),
        ("verify-full", "verify-full"),
        ("disable", "disable"),
        ("prefer", "prefer"),
    ],
)
def test_sslmode_se_traduce_a_ssl_sin_cambiar_el_semantico(modo, esperado):
    """asyncpg parsea los modos de libpq en `ssl`; en `sslmode` ni siquiera existe el argumento."""
    salida = normalizar_url_database(f"postgresql://u:p@h/db?sslmode={modo}")
    assert salida.endswith(f"?ssl={esperado}")
    assert "sslmode" not in salida


def test_lo_que_ya_vale_no_se_toca():
    """La URL de desarrollo de este repo y cualquier URL ya normalizada, incluidas dos pasadas."""
    for intacta in [
        "postgresql+asyncpg://postgres@127.0.0.1:5433/agenda_dev",
        "postgresql+asyncpg://u:p@h:5432/db?ssl=require",
    ]:
        assert normalizar_url_database(intacta) == intacta
        assert normalizar_url_database(normalizar_url_database(intacta)) == intacta


def test_los_demas_parametros_de_la_query_se_conservan():
    url = "postgresql://u:p@h/db?connect_timeout=5&sslmode=require&application_name=agenda"
    salida = normalizar_url_database(url)
    assert salida.startswith("postgresql+asyncpg://")
    assert "connect_timeout=5" in salida
    assert "application_name=agenda" in salida
    assert "ssl=require" in salida


def test_una_contrasena_escalada_sigue_llegando_igual():
    """`%` en la clave: si se reescapa dos veces, el host responde clave incorrecta y nadie entiende por qué."""
    salida = normalizar_url_database("postgresql://u:p%40ssw0rd@h/db?sslmode=require")
    assert ":p%40ssw0rd@" in salida


def test_un_ssl_puesto_a_mano_gana_al_sslmode_del_host():
    salida = normalizar_url_database("postgresql://u:p@h/db?sslmode=disable&ssl=require")
    assert salida == "postgresql+asyncpg://u:p@h/db?ssl=require"


def test_postgresql_con_otro_driver_se_pasa_a_asyncpg():
    """Es el único driver instalado: dejar `+psycopg` sería un despliegue que se cae en la migración."""
    assert normalizar_url_database("postgresql+psycopg://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"


def test_sqlite_no_se_reescribe_nunca():
    """Reescribirlo sería arreglar por casualidad algo que está mal: el guardia lo tiene que ver tal cual."""
    for ajena in ["sqlite:///agenda.db", "sqlite+aiosqlite:///agenda.db", "mysql+pymysql://u@h/db"]:
        assert normalizar_url_database(ajena) == ajena


def test_una_ilegible_no_revienta_al_importar_la_app():
    """Aquí no se explica el error: lo hace `app.arranque`, con el valor original en el mensaje."""
    for basura in ["", "   ", "esto no es una url", "://"]:
        assert normalizar_url_database(basura) == basura


def test_settings_normaliza_lo_que_llega_por_variable_de_entorno(monkeypatch):
    """El viaje real: el host pone DATABASE_URL y la app ni se entera de que venía en otra forma."""
    monkeypatch.setenv("DATABASE_URL", URL_DE_HOST)
    assert Settings(_env_file=None).database_url == (
        "postgresql+asyncpg://podrcoduser:Vj2n9dXqP4mL@roundhouse.proxy.rlwy.net:6543/railway?ssl=require"
    )


def test_settings_normaliza_tambien_el_valor_pasado_por_codigo():
    s = Settings(_env_file=None, database_url=URL_DE_HOST_CORTA)
    assert s.database_url.startswith("postgresql+asyncpg://")


def test_una_base_de_host_en_produccion_pasa_el_guardia():
    """El guardia comprueba la URL ya normalizada: si no, rechazaría el Postgres de Railway por el esquema."""
    revisar_arranque(
        Settings(
            _env_file=None,
            entorno="produccion",
            jwt_secret=SECRETO_BUENO,
            database_url=URL_DE_HOST,
        )
    )


def test_el_sqlite_normalizado_sigue_sin_arrancar_en_produccion():
    """La normalización no abre la puerta a SQLite: el advisory lock sigue siendo lo que importa."""
    with pytest.raises(RuntimeError) as error:
        revisar_arranque(
            Settings(
                _env_file=None,
                entorno="produccion",
                jwt_secret=SECRETO_BUENO,
                database_url="sqlite+aiosqlite:///agenda.db",
            )
        )
    assert "PostgreSQL" in str(error.value)


def test_sin_jwt_real_no_arranca_aunque_la_base_venga_bien(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL_DE_HOST)
    with pytest.raises(RuntimeError) as error:
        revisar_arranque(Settings(_env_file=None, entorno="produccion", jwt_secret=SECRETO_DE_DESARROLLO))
    assert "JWT_SECRET" in str(error.value)
