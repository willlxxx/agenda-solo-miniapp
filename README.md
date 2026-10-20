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
cd backend && pytest -q          # 276 pruebas sobre PostgreSQL real (base agenda_test autogestionada)
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
  meses antes de borrar citas pasadas (la banda la fija `doctors.retencion_meses` y el barrido lo
  hace `POST /api/v1/internal/limpieza`; ver la sección de retención). Nunca almacenar
  diagnósticos, notas clínicas, radiografías ni historial médico.
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

## Lista de espera (Semana 2)

Cuando el horizonte del enlace no da huecos, el paciente puede dejar su contacto. Es **demanda, no
reserva**: un apuntado no tapa ninguna hora (hay test de regresión contra `disponibilidad`).

| Endpoint | Qué hace |
| --- | --- |
| `POST /api/v1/public/{slug}/lista-espera` | Apunta la petición: servicio, día opcional, franja (`manana`/`tarde`/`cualquier_hora`), nombre y teléfono o email, con consentimiento explícito. |
| `GET /api/v1/waitlist?filtro=pendientes\|contactadas\|todas` | La lista del doctor, ordenada: primero lo pendiente y dentro de cada bloque por orden de llegada (máx. 200). |
| `PATCH /api/v1/waitlist/{id}` | Pasa a `contactada` (ya le llamó) o de vuelta a `en_espera`. |
| `DELETE /api/v1/waitlist/{id}` | Borra la petición. Es la vía de borrado GDPR que puede pedir un paciente. |

Reglas:

- Mismo advisory lock por doctor que las citas, pero con clave aparte (`lista-espera:{doctor}`):
  apuntarse no bloquea reservas ni se bloquea por ellas.
- Identidad igual que la reserva: (doctor, nombre, teléfono) → una sola ficha en `patients`. Con
  solo email se busca por email normalizado a minúsculas, porque PG no deduplica NULL y si no la
  lista crearía una ficha paralela en cada intento.
- Idempotente con la clave del `UNIQUE (doctor_id, patient_id, fecha)`: recargar la página devuelve
  la fila existente con `creado: false` en vez de duplicar. Si esa fila ya estaba `contactada`, la
  respuesta lo dice y la página lo cuenta, en lugar de prometer un aviso que ya ocurrió.
- `blocked_dates` y franjas apagadas también alimentan la lista: un `blocked_date` de mañana hace
  que mañana no ofrezca huecos y el paciente acabe aquí.
- Sin teléfono ni email no se guarda nada: no habría forma de avisar.

La vista del paciente está en `frontend/src/routes/d/[slug]/+page.svelte` (se abre sola cuando el
horizonte viene vacío, y con un "¿Ninguna hora te viene bien?" cuando sí las hay pero no le encajan).
La del doctor es `frontend/src/lib/panel/ListaDeEspera.svelte`, justo debajo de la agenda, con
contadores por pestaña y un borrado que pide confirmar con un segundo clic.

## Avisos de hueco libre (Semana 2)

Cierra el círculo: quien estaba en la lista deja de estarlo porque el médico le ofrece una hora real
y, si el paciente acepta, se la guarda él desde su propio panel.

| Endpoint | Qué hace |
| --- | --- |
| `GET /api/v1/waitlist/encajes?dias=21` | Cruza lo pendiente con la disponibilidad **real de este momento** y devuelve, por petición, el hueco que le sirve, el aviso ya redactado y sus enlaces. Es lectura: no reserva nada. |
| `POST /api/v1/waitlist/{id}/reservar` | Escribe la cita con los datos que el paciente dejó al apuntarse, y cierra la fila como `contactada` con `avisado_en` y `hueco_ofrecido`. |

**Cómo llega el aviso al paciente**: el panel abre `https://wa.me/…?text=…` o `mailto:…` con el texto
escrito, o lo copia. El envío lo hace la cuenta del médico. No hay proveedor de mensajería, ni
credenciales que guardar, ni terceros que declarar en la página pública: por eso el texto habla de
"ha quedado un hueco", nunca de cita confirmada.

Reglas:

- **Un hueco, un aviso.** El encaje va por orden de llegada y va descontando la hora que ofrece
  (`app/domain/encaje.py`), así que dos personas que piden lo mismo no reciben la misma mañana.
- **Lo que se ofrece es lo que se puede reservar.** `/encajes` usa el mismo motor
  (`calcular_disponibilidad`) y el mismo paso de rejilla que el enlace público; `/reservar` revalida
  dentro del candado. Si alguien ocupó la hora mientras se escribía el WhatsApp, devuelve 409 y la
  petición sigue pendiente.
- **Un solo núcleo de escritura** (`app/services/reserva.py`): el enlace público y el panel llaman a
  `reservar_hueco`, con el mismo `pg_advisory_xact_lock('agenda-cita:{doctor}')`. La columna
  `appointments.origen` (`publico` | `panel`, con CHECK) dice de dónde vino cada cita: la del panel
  la escribió el médico attestando una petición telefónica, no un clic del paciente.
- **Reabrir una fila borra su aviso** (`avisado_en`/`hueco_ofrecido` a NULL): si el paciente dijo que
  ese día no, vuelve a la cola limpia y el panel no insiste con lo mismo.
- El teléfono tiene que ser internacionalizable para prometer un `wa.me`; si no lo es, el panel sigue
  ofreciendo llamar, correo y copiar, pero no inventa un enlace.

En el panel es la sección "Huecos para quien espera" (`frontend/src/lib/panel/Avisos.svelte`), entre
la agenda y la lista. Al reservar por el paciente, `estado-panel.svelte.ts` avisa a las otras dos
secciones para que vuelvan a leer: si no, la cita aparecería arriba con retraso y la fila seguiría
pendiente abajo.

## Recordatorios automáticos (Semana 2)

El único mensaje que sale solo de la agenda. Lo que se avisaba a mano desde el panel (huecos libres)
sigue siendo manual; aquí lo que se automatiza es el "mañana tienes cita", porque es idéntico para
todos y no necesita a nadie delante.

| Endpoint | Qué hace |
| --- | --- |
| `POST /api/v1/internal/recordatorios` | Una pasada del cron: busca las citas confirmadas cuyo adelanto está en ventana, redacta los avisos y los entrega. Devuelve contadores (`enviados_24h`, `enviados_1h`, `omitidos`, `errores`, `agotados`…), nunca datos del paciente. Hay que mandar la cabecera `x-cron-secret`. |

No sale en `/docs` y está **apagado sin `CRON_SECRET`** (503): es una ruta que escribe en la agenda y
envía correos a pacientes.

**Por qué un endpoint y un cron de fuera, y no un planificador dentro del proceso**: Railway reinita
el contenedor cuando quiere, un scheduler en memoria pierde las pasadas y se duplica entre
instancias, y no hay forma de forzar una pasada a mano. Con `curl` desde fuera, la idempotencia la
dan las banderas de la base de datos: se puede lanzar dos veces el mismo minuto sin escribirle dos
veces a nadie.

```bash
curl -sS -X POST "$AGENDA_URL/api/v1/internal/recordatorios" \
  -H "x-cron-secret: $CRON_SECRET"
```

Cada 15 minutos basta con las ventanas de 24 h y 1 h. En Railway: un servicio *cron* con ese comando
(su variable de entorno con el secreto); en cualquier otro sitio, un crontab o una GitHub Action
programada. Con `RECORDATORIOS_PROVEEDOR=simulado` (el defecto) no se envía nada: la pasada decide,
redacta y marca, pero el correo se queda en el proveedor simulado. Es lo que corre en desarrollo y en
los tests.

Reglas:

- **Ventana, no instante.** El aviso de 24 h vale mientras el adelanto esté entre 12 y 24 h, y el de
  1 h entre 40 y 60 minutos. Un cron que se salta un tick no pierde el aviso; uno que estuvo caído
  tres días no manda un "mañana tienes cita" sobre una cita ya pasada.
- **Horas de silencio 08:00–21:00 en el huso del doctor.** Un recordatorio a las 03:00 es una queja
  segura. Si la ventana entera cae de madrugada, ese aviso no sale y la bandera queda sin marcar:
  la pasada siguiente podrá mandarlo mientras esté en ventana.
- **Candado por doctor** (`pg_advisory_xact_lock('recordatorios:{doctor}')`), clave distinta de la de
  las citas. Dentro del candado se vuelven a leer las banderas: si otra pasada ya mandó el aviso,
  cuenta como `omitidos` y no se repite.
- **El fallo no marca nada.** Si el proveedor no entrega, la bandera sigue en `false` y
  `recordatorio_intentos` suma uno; a los 3 intentos la cita se da por perdida (`agotados`) en vez
  de reintentar cada cuarto de hora contra un SMTP caído.
- **Nada que no podamos cumplir.** El texto ofrece el teléfono del médico si lo hay; si no, dice
  "avísanos". No hay enlace de anular ni botón de confirmar: eso sería prometer un flujo que no
  existe.
- **Sin email no hay recordatorio.** El campo es opcional en la página pública, y el canal es correo:
  una cita sin dirección aparece como `sin_correo` y no se intenta escribir a ningún sitio.

Declaración de terceros (GDPR): el envío sale del **SMTP que configure cada doctor**
(`SMTP_HOST`/`SMTP_DE` en `backend/.env`), no de un servicio de mensajería de terceros. La página
pública declara la finalidad en la casilla de consentimiento ("que use el email que escriba solo para
recordármela") y bajo el formulario dice exactamente qué se va a mandar y a qué dirección; borrar la
ficha del paciente borra el email, y por tanto el recordatorio. Retención: 6–12 meses como el resto
de la agenda.

Está en `app/domain/recordatorios.py` (decidir y redactar, sin BD ni SMTP), `app/services/despacho.py`
(leer, candar, entregar, marcar) y `app/services/correo.py` (`Simulado` y `Smtp` con la librería
estándar dentro de un hilo). Se prueba en `tests/test_recordatorios.py`, con fechas y reloj fijos:
corra el CI a la hora que corra, la pasada de las 04:00 de la madrugada se sigue comprobando a las
04:00.

## Suprimir y exportar los datos de un paciente (Semana 2)

La casilla de consentimiento promete que se puede borrar la ficha. Esta es esa ruta, y es la que
cierra el círculo con el recordatorio: sin email guardado no hay aviso que mandar.

| Endpoint | Qué hace |
| --- | --- |
| `GET /api/v1/pacientes/de-cita/{cita_id}` | La ficha de quien está en esa fila de la agenda: nombre, teléfono, email, alta, última visita, sus citas y sus peticiones de la lista, y el texto de consecuencias con las cuentas reales. |
| `GET /api/v1/pacientes/{paciente_id}/datos` | Artículo 15 y 20: un JSON que se descarga como adjunto con nombre legible (`datos-de-ana-ibars.json`), fechado con `generado_el`. |
| `DELETE /api/v1/pacientes/{paciente_id}` | Artículo 17: borra la ficha y suelta la identidad de sus citas y de sus peticiones. Devuelve cuántas cosas quedaron sin nombre. |

Los tres requieren sesión del médico y solo alcanzan a sus propios pacientes (404 si la cita o la
ficha es de otro consultorio). La entrada es la cita, no un censo: la agenda no lleva el `patient_id`
en su payload y sigue sin llevarlo.

**Conservar la cita, soltar la identidad.** `DELETE` pone a `NULL` el `patient_id` de las citas y de
las entradas de la lista de espera y entonces borra la fila de `patients`. Se va el nombre, el
teléfono, el email y la última visita. Se queda la hora, la duración, el servicio, el estado, el
`origen` y —lo importante— la constancia del consentimiento con su instante, que es la prueba de que
esa reserva fue legítima. Borrar la cita entera destruiría el propio registro del consultorio,
liberaría un hueco de un día que ya pasó y dejaría al médico sin forma de contestar a "yo sí avisé de
la anulación". Un borrado de datos no es una cancelación.

Detalles que hacen esto seguro:

- **Segundo clic con cuentas.** El botón grande dice "Borrar su ficha" y abre el texto de
  `app/domain/borrado.py` con los números de ESA ficha ("Quedan 3 citas sin nombre —1 sigue por
  llegar—…"). El texto lo escribe el servidor, no el panel, para que no puedan divergir.
- **El mismo candado que reserva.** La transacción toma `pg_advisory_xact_lock('agenda-cita:{doctor}')`,
  la clave de las reservas: nadie puede apuntarse a un hueco con la identidad que se está suprimiendo
  mientras dura el borrado.
- **Primero `UPDATE`, después `DELETE`.** Las FK de `appointments` y `waitlist` hacia `patients` no
  tienen `ondelete`, así que al revés PostgreSQL cortaría la operación en cuanto la ficha tuviera una
  sola cita.
- **La fila sigue ocupada.** En la agenda la cita pasa a leerse "paciente borrado" y su hueco no se
  vuelve a ofrecer; la lista de espera muestra "Petición sin nombre". Si esa persona reserva otro día,
  es una ficha nueva con su `id`.
- **El export dice lo que no se guarda**: diagnósticos, notas clínicas ni historial nunca estuvieron
  en la base, y el archivo lo declara en `no_se_guarda`.

Lo que todavía no existe, para no prometerlo: no hay portal donde el paciente se descargue o se borre
solo —lo hace el médico desde su panel y le pasa el archivo—. La retención de las citas pasadas ya está
resuelta aparte, en la siguiente sección.

Está en `app/domain/borrado.py` (qué se va, qué se queda y cómo se redacta, sin BD),
`app/services/pacientes.py` (leer, exportar y suprimir en transacción) y
`app/api/v1/pacientes.py`, con la mini-ficha en `frontend/src/lib/panel/Agenda.svelte`. Se prueba en
`tests/test_pacientes.py`.

## Retención: cuándo se borra una cita cerrada (Semana 2)

El borrado por petición del paciente (arriba) es un caso. Este es el otro: el histórico que nadie pide
borrar y que no conviene guardar para siempre. Es el art. 5.1.e (minimización) y, para el aviso de la
página pública, el compromiso del plazo.

| Endpoint | Qué hace |
| --- | --- |
| `GET /api/v1/retencion` | El plazo del consultorio (6–12 meses) y la frase que explica qué hace. La frase la escribe el servidor: si la escribiera el panel, quedaría mintiendo en cuanto cambiemos la regla. |
| `PUT /api/v1/retencion` | `{"meses": 8}`. Cambiar el número **no borra nada en el momento**; solo fija el umbral que usará la próxima pasada. |
| `POST /api/v1/internal/limpieza` | La pasada del cron sobre todos los consultorios. Con `?simular=true` ejecuta los mismos `DELETE` y deshace la transacción: enseña las cuentas exactas sin perder una fila. Cabecera `x-cron-secret`, fuera de `/docs`, apagado (503) sin secreto. |

Qué se va y qué se queda, dicho sin eufemismos:

- **Solo citas cerradas**: `completada`, `no_vino` y `cancelada` con más del plazo. Una `confirmada`
  cuyo día ya pasó no se toca: es un cabo suelto del registro, no dato viejo, y borrarla sería tapar
  un hueco en lugar de limpiarlo. La pasada lo cuantifica en `confirmadas_sin_cerrar` para que el
  médico vea cuántas lleva sin cerrar.
- **La constancia del consentimiento se va con la fila.** Mientras la cita existe,
  `consentimiento_ok_en` prueba que el procesamiento fue legítimo; cuando ya no hay cita que
  justificar, conservar el recibo sería mantener un dato personal indefinidamente.
- **Fichas huérfanas**: una ficha de `patients` solo se borra si, **después** de limpiar citas y
  peticiones, no queda nada señalándola. El orden es citas → peticiones → fichas, dentro de la misma
  transacción; al revés, una ficha cuyo último histórico se acaba de eliminar seguiría apareciendo
  como "usada".
- **Peticiones de la lista de espera** con el plazo cumplido, da igual su estado: una `en_espera` de
  hace dos años no es demanda, es un teléfono guardado sin finalidad.
- **Un plazo por consultorio**, dentro de 6–12 (`ck_doctor_retencion` en la BD y `normalizar_meses()`
  en el dominio). El barrido respeta el de cada uno: la retención de García no puede llevarse el
  histórico de Lopez.

Detalles que hacen esto seguro:

- **La regla decide en el dominio, el SQL solo recorta.** `app/domain/retencion.py` tiene los
  predicados (`cita_eliminable`, `peticion_eliminable`, `ficha_eliminable`); el servicio trae
  candidatos con un criterio ancho y borra **por lista de ids**. Si el `WHERE` volviera a escribir la
  regla, un cambio en el dominio cambiaría los contadores pero no el borrado.
- **Calendario, no 30 días.** `restar_meses` recorta al último día del mes destino: 31 de marzo menos
  un mes es 28 de febrero, y el 29 de febrero de 2024 menos 12 meses es 28 de febrero de 2023. Sin ese
  recorte, la pasada del día 31 se caería en los meses cortos.
- **Candado por consultorio** (`pg_advisory_xact_lock('limpieza:{doctor}')`), clave propia: no
  reserva huecos ni marca avisos, así que no tiene por qué esperar a nadie.
- **El candado de la base manda.** Si entre la lectura y el `DELETE` alguien reserva sobre una ficha
  que estábamos soltando, la FK sin `ondelete` corta con `IntegrityError`, la tanda entera se
  deshace y cuenta como `conflictos`. Nunca se borra a medias la reserva de alguien que acaba de
  llegar.
- **Idempotente y sin datos personales.** La segunda pasada del mismo minuto ya no encuentra nada; el
  payload son solo contadores (`citas_eliminadas`, `peticiones_eliminadas`, `fichas_eliminadas`,
  `fichas_en_uso`, `confirmadas_sin_cerrar`, `conflictos`, `simulada`).

```bash
# Primero enseñar, luego borrar
curl -sS -X POST "$AGENDA_URL/api/v1/internal/limpieza?simular=true" -H "x-cron-secret: $CRON_SECRET"
curl -sS -X POST "$AGENDA_URL/api/v1/internal/limpieza"          -H "x-cron-secret: $CRON_SECRET"
```

Con una pasada al día basta: el plazo se mide en meses. Es la misma tarea programada que los
recordatorios (otro `curl` en el cron de Railway o una GitHub Action), y **tampoco corre sola en
producción hasta que ese cron esté montado** —en desarrollo se lanza a mano con las dos líneas de
arriba—.

Está en `app/domain/retencion.py` (plazo, banda y predicados, sin BD ni reloj de entorno),
`app/services/limpieza.py` (leer, candar, borrar por ids y deshacer si es ensayo),
`app/api/v1/retencion.py` (lo que ve el médico) y `app/api/v1/limpieza.py` (la ruta del cron), con el
deslizador en `frontend/src/lib/panel/Retencion.svelte`. Se prueba en `tests/test_retencion.py`, con
`ahora` fijado por dependencia: una retención de 12 meses se comprueba con fechas de 2025, no
esperando a que el CI corra el día que toca.

## La cara: estética de consulta y botones que responden (Semana 2)

Hasta aquí el panel funcionaba pero no se veía como una herramienta de odontología, y había tres
botones que hacían su trabajo en silencio: **"Copiar"** en el enlace de reserva, **"Hoy"** en la
agenda y **"+ Franja"** en el horario. Los tres tienen ahora reacción visible, y tanto el panel como
la página del paciente (`/d/<slug>`) llevan una identidad propia.

Qué se eligió y por qué:

- **Paleta de esmalte y menta clínica**, no la típica azul-sanitaria: fondo `#eef4f1` con un halo
  radial verde en la cabecera, superficie blanca, primario `#0d6b60` y un brillo `#7cd0c4` que es la
  única nota decorativa (la línea superior de cada tarjeta). En modo oscuro el fondo es
  `#061210` y las sombras se apagan a `none`, porque una tarjeta con sombra sobre negro se ve
  pegada, no flotando. Todo vive en variables de `frontend/src/styles.css`; ningún componente pone un
  color literal.
- **Cero dependencias nuevas.** Los iconos son SVG dibujados a mano en `frontend/src/lib/icons.ts` y
  se pintan con `Icon.svelte` (24×24, `stroke="currentColor"`, `aria-hidden`); no hay librería de
  iconos ni tipografías descargadas —el display es la pila del sistema (`ui-serif`/Georgia para los
  títulos, el resto sans)—, y el único archivo nuevo es `frontend/static/favicon.svg`, propio. La
  declaración de terceros de la página pública sigue siendo cierta sin tocarla.
- **El diente es el hilo**, no un adorno suelto: la cabecera de la página pública lo dibuja con un
  trazo animado (`stroke-dashoffset`, solo si el navegador no pide `prefers-reduced-motion`), cada
  servicio lleva su diente con el color que el médico le asignó, y "Servicios" del panel repite el
  gesto. La secuencia de reserva se lee como un raíl de tres pasos (`Servicio → Día y hora → Tus
  datos`) que marca lo hecho en verde y lo presente en menta, para que el paciente sepa cuántos
  pasos le quedan sin contar con que entienda el inglés de una barra de progreso.
- **Ningún estado depende solo del color o del movimiento.** "Copiado" cambia de texto, de icono y de
  color a la vez, y lo mismo dice una franja abierta/cerrada (además de la opacidad, la palabra). Los
  botones que se quedaron solo icono conservan su `aria-label`, y el aviso de copiar va en un
  `role="status" aria-live="polite"` para que un lector de pantalla lo lea.

Cómo se siente cada uno de los tres botones que no respondían:

- **Copiar** pasa a verde con el icono de check y "Copiado", y el texto de abajo lo confirma; si el
  navegador bloquea el portapapeles (pasa sin HTTPS o sin el foco de la pestaña) el botón **no** finge
  éxito: se queda como está y el aviso explica que se copie a mano con Ctrl+C. El verde se apaga solo
  a los 2,6 s; el error no, porque un aviso de "no copió" que desaparece deja al médico con la duda.
- **Hoy** destella (`pulso--primario`, un anillo que se expande y muere en 620 ms) y **vuelve a pedir
  la semana aunque ya estés en ella**. Era el fallo de verdad: el botón solo reasignaba el lunes, y si
  la semana no cambiaba, el efecto que carga las citas no llegaba a correr —pulsar "Hoy" un martes por
  la tarde no refrescaba nada—.
- **+ Franja** se queda "Añadiendo…" y bloqueado mientras se guarda, la fila nueva entra deslizándose
  y luego destella; "Quitar" la apaga a media opacidad mientras la borra. Además el hueco que propone
  ya no es una hora fija a las 10:00: se coloca **después de la última franja del día**, porque con la
  mañana abierta (09:00–14:00) la propuesta fija empalmaba con ella y el botón devolvía un 409 de
  solape que además **no se veía en ningún sitio**: el mensaje de error se dibujaba dentro de la
  tarjeta de "Vacaciones y festivos", abajo del todo. Ahora hay dos avisos, uno por tarjeta.

El resto del feedback es una línea de CSS: transición de 150 ms en los controles, `translateY(1px)
scale(0.975)` al pulsar, y dos animaciones reutilizables (`nace` para lo que entra, `cambia` para lo
que acaba de guardarse) que van **claveteadas por id**, así que una fila solo se anima cuando es nueva
de verdad y no en cada refresco de la lista. Con `prefers-reduced-motion: reduce` todo ese movimiento
se apaga dejando una duración casi nula: las palabras y los iconos siguen contando lo mismo.

Está en `frontend/src/styles.css` (tokens, estados, animaciones), `frontend/src/lib/icons.ts` +
`Icon.svelte` (el dibujo), y ya en cada sección: `panel/+page.svelte`, `Agenda.svelte`,
`Horario.svelte`, `Avisos.svelte`, `ListaDeEspera.svelte`, `Servicios.svelte`, `Retencion.svelte` y
`routes/d/[slug]/+page.svelte`.

## Siguiente paso

El paciente reserva, el doctor gestiona su semana, recoge a quien se quedó sin hueco, le ofrece una
hora real cuando la hay, y el paciente recibe el aviso de su cita sin que nadie lo escriba. Lo que
falta:

- **Antes de publicar**: generar un `JWT_SECRET` nuevo y un `CRON_SECRET` (los de `backend/.env` son
  de desarrollo y ese archivo nunca se sube), montar el cron con los dos `curl` (recordatorios cada
  15 min, limpieza una vez al día, `?simular=true` la primera vez para ver cuentas antes de borrar),
  y poner el SMTP real del doctor (`RECORDATORIOS_PROVEEDOR=smtp` + `SMTP_HOST`/`SMTP_DE`). Sin esas
  cosas la agenda funciona, no envía avisos y no borra su histórico viejo.
- Panel: la mini-ficha de cada cita ya dice cuándo se recordó (y el export lo lleva por escrito). Lo
  que falta es la vista de conjunto: una columna con el estado de los avisos de toda la semana, para
  responder a "no me avisasteis" sin ir cita por cita.
- El aviso de retención de la página pública todavía dice que se puede pedir el archivo o el borrado,
  pero no cuantifica el plazo. En cuanto el cron de limpieza esté montado se puede añadir "y se borra
  solo a los X meses"; hasta entonces sería prometer un automático que no corre.
- Fase 2 del documento, todavía fuera: pagos, Google Calendar, integración con el software dental.

