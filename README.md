# Agenda Solo — microapp de reservas para odontólogos unipersonales

El paciente reserva solo desde un enlace (`/d/{slug}`), recibe confirmación y recordatorios,
y el doctor ve su día en una sola pantalla. Es una herramienta de agenda, **no** un software de
gestión dental: sin odontograma, sin historia clínica, sin facturación.

Estado: **Semana 1 completada** (cuenta del doctor + configuración de servicios y horario).
Plan completo: [`PLAN-SEMANA-1.md`](PLAN-SEMANA-1.md).

## Stack

| Capa | Elección |
|---|---|
| Frontend | SvelteKit 3 + Svelte 5 (runes) + Vite |
| Backend | FastAPI + SQLAlchemy 2 async + Alembic |
| Base de datos | PostgreSQL 16+ con `citext` e índice parcial anti-dobles-reservas |
| Autenticación | argon2id + JWT HS256 en cookie `HttpOnly` `SameSite=Lax` (solo el doctor) |

## Estructura

```
backend/   FastAPI: app/{models,schemas,api/v1}, alembic/, tests/
frontend/  SvelteKit: landing, /auth/registro, /auth/acceso, /panel
scripts/   postgres.ps1 (PostgreSQL local sin Docker)
```

## Ponerse en marcha

### 1. Base de datos

**Opción A — PostgreSQL local (el que funciona en esta máquina, sin Docker):**

```powershell
# Una sola vez: binarios portables en C:\tools\pgsql y C:\tools\pgsql\data
.\scripts\postgres.ps1 start        # escucha 127.0.0.1:5433
```

**Opción B — Docker (`docker compose up -d`) o Railway:** exporta `DATABASE_URL` en `backend/.env`.

### 2. Backend

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate    # Windows; source .venv/bin/activate en macOS/Linux
pip install -e ".[dev]"
copy .env.example .env                              # y ajusta DATABASE_URL / JWT_SECRET
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, con proxy /api -> :8000
```

## Pruebas

```bash
cd backend && pytest -q          # 123 tests sobre PostgreSQL real (base agenda_test autogestionada)
cd frontend && npm run check     # svelte-check, 0 errores
```

CI (`.github/workflows/ci.yml`): lint + migración + `alembic check` + pytest con Postgres 16 de
servicio, y check + build del frontend.

## Reglas que no se pueden romper

- **Anti-dobles-reservas.** Las citas se leen por el índice parcial `estado = 'confirmada'`; la
  reserva debe ir en transacción con `pg_advisory_xact_lock` (Semana 2). SQLite o bases simuladas
  no son válidos ni en pruebas: el comportamiento que importa es el de PostgreSQL.
- **Las fechas de cita se almacenan en UTC** (`timestamptz`). El `timezone` del doctor solo se usa
  al pintar y al calcular huecos.
- **`duracion_minutos` se congela en la cita.** Editar un servicio no reescribe citas históricas.
- **Datos de salud (GDPR).** El servicio reservado revela información de salud: consentimiento
  explícito en la reserva (`consentimiento_ok_en`), minimización de datos, y retención de 6–12
  meses antes de borrar citas pasadas. Nunca almacenar diagnósticos, notas clínicas, radiografías
  ni historial médico.
- **Los servicios no se borran físicamente**: se desactivan (`activo = false`), porque pueden tener
  citas futuras.

## Notas del entorno actual

- No hay Docker Desktop (el CLI existe pero el daemon no arranca), ni WSL con distro, ni `winget`.
  `scripts/postgres.ps1` usa los binarios portables de PostgreSQL descargados de Maven Central.
  `docker-compose.yml` queda como referencia canónica para otras máquinas y para el despliegue.
- Python 3.14 funciona sin problemas con `asyncpg`, `pydantic-core` y `argon2-cffi`; no hizo falta
  bajar a 3.12. `requires-python` sigue en `>=3.12`.
- En Windows `ZoneInfo` no encuentra ni `UTC` sin el paquete `tzdata`: el motor de disponibilidad lo
  necesita para leer el huso del doctor. En Linux (Railway) ya existe `/usr/share/zoneinfo`.
- El alias `#lib/*` se resuelve por el campo `imports` de `package.json` (SvelteKit 3 eliminó
  `$lib`). `tsconfig.json` añade `paths` explícitos porque SvelteCheck no resuelve `imports` sin
  ellos.

## Motor de disponibilidad (Semana 2, en curso)

Lectura pública para el enlace del doctor, sin sesión y sin escribir nada:

| Endpoint | Qué devuelve |
| --- | --- |
| `GET /api/v1/public/{slug}` | Nombre y huso del doctor + servicios **activos** (sin email ni teléfono). |
| `GET /api/v1/public/{slug}/disponibilidad?servicio=&dias=14&paso=15&anticipacion=30&desde=` | Huecos por día, en hora local del doctor. |
| `POST /api/v1/public/{slug}/citas` | Reserva real: `pg_advisory_xact_lock` por doctor + revalidación del hueco dentro de la tx. |

La página del paciente está en `frontend/src/routes/d/[slug]/+page.svelte` (cuatro pasos: servicio →
día y hora → datos → confirmación). Es el enlace que el panel muestra en "Tu enlace de reservas".

Reglas del cálculo, en `app/domain/disponibilidad.py` (lógica pura, testeable sin BD):

- La **duración del servicio cabe entera** en el hueco; un sobrante de 20 min no se ofrece.
- Se descuentan `blocked_dates`, las citas **confirmadas** (cancelada/no_vino no tapan) y las
  franjas inactivas. Las citas contiguas se fusionan: no dejan micro-huecos inutilizables.
- La rejilla (`paso`, 5–60 min) arranca en la **hora de apertura de la franja**, no en medianoche.
- Horas que ya pasaron o dentro de `anticipacion` no se ofrecen; horizonte máximo 60 días.
- Todo se calcula en **minutos absolutos desde el primer día del horizonte** y las citas se
  convierten al huso del doctor **antes**: así una urgencia de lunes 23:30-00:30 tapa la madrugada
  del martes y el cambio de horario de octubre no descoloca ninguna hora (hay test de ambos).
- Un hueco de `disponibilidad` **no es una reserva**: no bloquea nada. El bloqueo real llega con
  `pg_advisory_xact_lock` en el POST de citas.

## Agenda del doctor (Semana 2)

Con la cookie de sesión (`GET`/`PATCH` sobre `/api/v1/appointments`), en `app/api/v1/agenda.py`:

| Endpoint | Qué devuelve |
| --- | --- |
| `GET /api/v1/appointments?desde=&dias=7` | Ventana de citas (máx. 30 días, histórico limitado a 92), pintadas en el huso del doctor, con servicio, color y contacto del paciente. |
| `PATCH /api/v1/appointments/{id}` | Cambia `estado` a `confirmada` / `completada` / `no_vino` / `cancelada`. |

Reglas de la agenda:

- En BD el instante sigue en **UTC**; la respuesta trae los dos: `inicio_utc` (campo máquina) y
  `fecha`/`hora`/`fin` pintados con el huso del doctor. Una cita a las 23:30 local sigue apareciendo
  en su día, aunque en UTC sea mañana.
- La **duración congelada** manda: si mañana el servicio pasa a 60 min, `fin` de las citas viejas
  no se reescribe.
- `completada` apunta `patients.ultima_visita` **solo si la fecha ya pasó**; `cancelada` no deja
  rastro de visita.
- **Solo `confirmada` tapa un hueco.** Al pasar a cualquiera de los otros tres estados el hueco
  vuelve a la disponibilidad pública (el motor cuenta `estado = 'confirmada'`).
- Reactivar una cita a `confirmada` comprueba solape contra las demás confirmadas: si otro paciente
  reservó mientras estaba cancelada, devuelve **409** en vez de montar dos citas encima.
- Minimización GDPR: la agenda del doctor devuelve contacto del paciente y nombre del servicio,
  nada clínico. No hay campo de notas en ninguna tabla.

La vista está en `frontend/src/lib/panel/Agenda.svelte`, montada arriba del panel: semana a semana
(con "Hoy"), día por día, con botones Completada / No vino / Cancelar y un "Ver las N cerradas" que
además deja Reconfirmar. La cita recién cerrada se queda visible para que el cambio se note.

## Siguiente paso

El paciente reserva y el doctor ya ve y gestiona su semana. Lo que falta para el resto de la Semana 2:

- **Lista de espera** cuando un día no ofrece huecos (`waitlist` ya tiene tabla).
- **Recordatorios** 24 h / 1 h antes (APScheduler o Redis; `timestamptz` ya está bien para calcularlos).
- Antes de publicar: generar un `JWT_SECRET` nuevo (el de `backend/.env` es de desarrollo y ese
  archivo nunca se sube), y decidir el proveedor de SMS/email para los recordatorios.
