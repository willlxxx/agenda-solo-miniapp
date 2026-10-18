# Plan técnico — Semana 1

**Objetivo (criterio de éxito del documento):** *El doctor puede crear su cuenta y configurar sus servicios.*
Si al final de la semana un doctor piloto puede registrarse, ajustar duraciones y guardar su horario sin tocar código, la semana se considera completada.

---

## 1. Decisiones previas (bloqueantes, se toman el Día 1)

| Decisión | Propuesta | Justificación |
|---|---|---|
| Alcance exacto de v1 | Reserva + duración variable + confirmación + recordatorios 24h/1h + panel del día + bloqueo de festivos | El documento marca con ✅ "Fase 2" la lista de espera, pagos, Google Calendar e integración dental. Recomendación: **salen del MVP** y se re-planifican post-piloto (afecta a las Semanas 5 y 7 del roadmap). |
| Frontend | **SvelteKit** (Svelte + Vite, con SSR) | Cumple el stack del documento y el SSR es crítico para la página pública de reserva (`/d/{slug}`): SEO local para Google Business, carga <1s. |
| Base de datos local | **PostgreSQL 16 vía Docker Compose** | Riesgo entorno: el CLI de Docker existe pero el daemon no arranca; hay que reparar Docker Desktop el Día 1. Plan B: PostgreSQL administrado de Railway desde el día 1 (conexión remota) y abandonar Docker local. |
| Autenticación doctor | Email + contraseña, **JWT (HS256) en cookie HttpOnly** | Sin sesión en memoria (Railway reinicia containers); la cookie permite que el fetch del panel y una futura protección de rutas en SvelteKit (server hooks) compartan la sesión. |
| ORM / acceso a datos | SQLAlchemy 2.x **async** + Alembic | El motor de disponibilidad de Semana 2 necesita transacciones explícitas con `pg_advisory_xact_lock`; con ORM síncrono eso es incómodo. |
| Migraciones | Alembic desde la primera tabla | El esquema cambia en Semana 2; migrar con `create_all` genera deuda el primer día. |

---

## 2. Estructura del repositorio (monorepo simple)

```
miniapp/
├── docker-compose.yml        # postgres:16 + (Redis se añade Semana 5, no antes)
├── .env.example              # DATABASE_URL, JWT_SECRET, FRONTEND_ORIGIN
├── backend/
│   ├── pyproject.toml        # Python ≥3.12, fastapi, sqlalchemy[asyncio], asyncpg, alembic, argon2, pydantic-settings
│   ├── app/
│   │   ├── main.py           # factory FastAPI + CORS + router registration
│   │   ├── config.py         # Settings vía pydantic-settings (lee .env)
│   │   ├── db.py             # engine async, sessionmaker, Base
│   │   ├── models/           # 1 archivo por tabla, importados en models/__init__.py
│   │   ├── schemas/          # Pydantic v2 por recurso
│   │   ├── api/
│   │   │   ├── deps.py       # get_db, get_current_doctor
│   │   │   └── v1/
│   │   │       ├── auth.py         # /api/v1/auth/*
│   │   │       ├── services.py     # /api/v1/services/*
│   │   │       ├── availability.py # /api/v1/availability/*
│   │   │       └── blocked.py      # /api/v1/blocked-dates/*
│   │   └── security.py       # hash argon2id, crear/validar JWT
│   ├── alembic/              # env.py async + versiones/
│   └── tests/                # pytest + httpx AsyncClient sobre app de prueba
└── frontend/
    └── (SvelteKit: npm create svelte@latest)
        src/routes/
        ├── +page.svelte            # landing mínima con CTA
        ├── auth/registro/          # pantalla 1
        ├── auth/acceso/            # pantalla 2
        └── panel/                  # pantalla 3 (protegida)
            ├── +layout.svelte      # guarda: sin cookie válida → /auth/acceso
            └── +page.svelte        # onboarding: servicios + horario
```

---

## 3. Modelo de datos — migración inicial (las 7 tablas del documento)

Se crean **todas** desde el día 2 (los modelos de `appointments`, `patients` y `waitlist` quedan sin endpoints esta semana; cuesta cero y evita re-migrar en Semana 2-3).

```sql
-- Identidad del doctor; slug_unico alimenta el enlace tudominio.com/d/{slug}
doctors (
  id            uuid pk default gen_random_uuid(),
  nombre        text not null,
  email         citext not null unique,
  password_hash text not null,               -- argon2id
  telefono      text,
  timezone      text not null default 'Europe/Madrid',
  slug_unico    citext not null unique,      -- generado en registro: nombre-apellido normalizado
  creado_en     timestamptz not null default now()
)

services (
  id               uuid pk,
  doctor_id        uuid fk doctors on delete cascade,
  nombre           text not null,
  duracion_minutos int  not null check (duracion_minutos between 5 and 240),
  color            text not null default '#2563eb',
  activo           boolean not null default true,
  orden            int not null default 0
)

availability (
  id          uuid pk,
  doctor_id   uuid fk doctors on delete cascade,
  dia_semana  smallint not null check (dia_semana between 0 and 6),  -- 0=lunes
  hora_inicio time not null,
  hora_fin    time not null,
  activo      boolean not null default true,
  unique (doctor_id, dia_semana, hora_inicio)
)

blocked_dates (
  id        uuid pk,
  doctor_id uuid fk doctors on delete cascade,
  fecha     date not null,
  motivo    text,
  unique (doctor_id, fecha)
)

appointments (
  id                      uuid pk,
  doctor_id               uuid fk doctors on delete cascade,
  service_id              uuid fk services,
  patient_id              uuid fk patients,
  fecha_hora              timestamptz not null,
  duracion_minutos        int not null,           -- congelado al reservar: si el doctor edita el servicio después, la cita histórica no cambia
  estado                  text not null default 'confirmada'
                          check (estado in ('confirmada','completada','no_vino','cancelada')),
  consentimiento_ok_en    timestamptz not null,   -- GDPR: evidencia de consentimiento explícito
  recordatorio_24h_enviado boolean not null default false,  -- columnas de despacho para APScheduler (Semana 5)
  recordatorio_1h_enviado  boolean not null default false,
  creado_en               timestamptz not null default now()
)
-- ÍNDICE CLAVE anti-dobles-reservas y para el motor de Semana 2:
create index on appointments (doctor_id, fecha_hora)
  where estado = 'confirmada';

patients (
  id             uuid pk,
  doctor_id      uuid fk doctors on delete cascade,
  nombre         text not null,
  telefono       text,
  email          text,
  ultima_visita  date,
  creado_en      timestamptz not null default now()
)

waitlist (
  id                 uuid pk,
  doctor_id          uuid fk doctors on delete cascade,
  service_id         uuid fk services,
  patient_id         uuid fk patients,
  preferencia_horaria text,
  creado_en          timestamptz not null default now()
)
```

Notas:
- Fechas/horas de citas en **UTC (`timestamptz`)**; el timezone del doctor solo se usa al pintar y al calcular huecos. Esto se decide ahora porque retrofitearlo en Semana 2 es una fuente clásica de bugs.
- `citext` para emails y slugs (extensión `citext` activada en la migración 0001).

---

## 4. Backend — endpoints de la Semana 1

Solo `services`, `availability` y `blocked-dates`, además del registro con **onboarding mágico**:

| Método | Ruta | Comportamiento |
|---|---|---|
| POST | `/api/v1/auth/registro` | Crea doctor + **5 servicios predefinidos** (Revisión 30', Limpieza 30', Empaste 45', Urgencia 30', Endodoncia inicial 60') + horario por defecto L-V 09:00-14:00 / 16:00-20:00. Genera `slug_unico` y lo devuelve. Devuelve cookie JWT. |
| POST | `/api/v1/auth/acceso` | Email + contraseña → cookie JWT. |
| POST | `/api/v1/auth/salida` | Borra cookie. |
| GET | `/api/v1/yo` | Perfil del doctor autenticado (lo usa el guard del panel). |
| CRUD | `/api/v1/services` | Listar, crear, renombrar, cambiar duración/color, desactivar. |
| CRUD | `/api/v1/availability` | Franjas por día de la semana. |
| CRUD | `/api/v1/blocked-dates` | Días bloqueados con motivo. |

Reglas de negocio mínimas (con test unitario cada una):
- `hora_fin > hora_inicio` en availability; no se solapan franjas del mismo día.
- No borrar un servicio con `activo=false` si tiene citas futuras (esta semana: simplemente no aplica, se deja el check preparado).
- Slug único, normalizado (sin acentos, minúsculas, guiones).

---

## 5. Frontend — 3 pantallas (mobile-first)

1. **Registro** (`/auth/registro`): nombre, email, teléfono, contraseña, timezone (preseleccionado). Al enviar → panel.
2. **Acceso** (`/auth/acceso`).
3. **Panel / onboarding** (`/panel`): una sola pantalla con dos bloques:
   - *Servicios*: lista de los 5 predefinidos, editar duración con +/- 5 min, renombrar, activar/desactivar. Cero configuración obligatoria: funciona tal cual sale del registro.
   - *Horario*: matriz día × franjas + bloque "Vacaciones y festivos" para `blocked_dates`.
   - Guardado automático con debounce (sin botón "Guardar" gigante).

Convenciones visuales de la Fase 2 del documento: modo claro/oscuro automático (`prefers-color-scheme`), tipografía legible con luz de gabinete, targets táctiles ≥44px.

---

## 6. Calendario día a día

| Día | Trabajo | Termina el día con… |
|---|---|---|
| 1 | Reparar/validar Docker (o activar plan B Railway PG). Monorepo + `docker-compose.yml` + FastAPI hello-world + `/healthz` + SvelteKit creado y corriendo en paralelo (proxy `/api` → :8000) | `docker compose up` levanta PG; `curl /healthz` = 200; dos dev servers funcionando |
| 2 | Migración 0001 (las 7 tablas), modelos SQLAlchemy, `config.py`, `security.py`, registro/acceso/salida + cookie JWT | Usuario registrado en PG con contraseña hasheada; `/yo` devuelve perfil |
| 3 | CRUD services + availability + blocked-dates con validaciones y tests (≥15 tests) | Colección de endpoints verde en pytest |
| 4 | Pantallas registro + acceso + esqueleto del panel con guard | Flujo completo manual: registrarse → panel sin recargar |
| 5 | Panel: edición de servicios y horario con autoguardado; pulido mobile-first; README con setup; CI básico (ruff + pytest en GitHub Actions) | **Demo del criterio de éxito**: cuenta nueva → servicios ajustados → horario guardado, en móvil real |

---

## 7. Riesgos y dependencias

1. **Docker daemon caído** (verificado hoy: CLI 19.03.1 existe pero no arranca). Es la única tarea de infraestructura previa; si no se resuelve en medio día, Plan B sin dudar: Railway PostgreSQL gestionado desde el Día 1.
2. **Python 3.14**: muy nuevo; alguna dependencia (pydantic-core, asyncpg) puede no tener wheels aún. Mitigación: instalar el venv el Día 1 y validar imports; si algo falla, fallback sin coste a Python 3.12 vía `pyenv`/uven (no afecta al código, se apunta `requires-python ≥3.12`).
3. **No construir en Semana 1**: motor de disponibilidad, reservas, recordatorios, Google Calendar, lista de espera, pagos. Nada de Redis todavía.
4. **GDPR desde el día 1** (barato ahora, caro después): la política de privacidad y el aviso de tratamiento van en la pantalla de registro del paciente (Semana 3), pero el campo de evidencia `consentimiento_ok_en` ya está en el esquema, y en `README` se documenta la regla de retención 6–12 meses.

---

## 8. Definition of Done — Semana 1

- [ ] `docker compose up -d` (o DATABASE_URL de Railway) + `uvicorn` + `npm run dev` = entorno completo con 3 comandos documentados en README.
- [ ] Migración 0001 aplicada; `alembic upgrade head` re-ejecutable desde cero.
- [ ] Un doctor puede: registrarse → ver sus 5 servicios → ajustar duraciones → configurar horario → bloquear un día → cerrar sesión → volver a entrar.
- [ ] Cookies JWT HttpOnly + SameSite=Lax; contraseñas argon2id; sin credenciales en el repo.
- [ ] ≥15 tests en verde (validaciones de negocio + auth) en CI.
- [ ] Panel usable en móvil real a 390px de ancho, modo oscuro automático.
