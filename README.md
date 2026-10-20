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
frontend/  SvelteKit: landing, /auth/registro, /auth/acceso, /panel; el build estático lo sirve la API
scripts/   arranque.sh (migración + uvicorn, el mismo dentro del contenedor), cron.py (el disparador que
           lanza el cron del host), salud.py (el sondeo del HEALTHCHECK, respeta PORT), postgres.ps1 (PG local)
Dockerfile docker-compose.yml   el despliegue: una imagen con API + interfaz, y su PostgreSQL
railway.toml railway.cron.toml  referencia de la configuración de los dos servicios de Railway; el host
           no los lee en este proyecto (Config as Code obsoleto): se manda lo que dice el panel
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

Sin el dev server, la interfaz construida (`npm run build`) la sirve la propia API en el 8000: con
el venv en el PATH, `sh scripts/arranque.sh` migra y levanta las dos cosas en un proceso. Es también
el comando del despliegue (abajo, «Publicarlo»).

## Pruebas

```bash
cd backend && pytest -q          # 351 pruebas: 278 sobre PostgreSQL real (base agenda_test autogestionada)
                                 # y 73 sin base: guardia de arranque, URL del host, estáticos, cron y sondeo
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
  trazo animado (`stroke-dashoffset`, solo si el navegador no pide `prefers-reduced-motion`). El
  contorno del diente mide 65 unidades y el trazo usa `pathLength="100"`, así que el `dasharray` se
  cuenta sobre 100 y el dibujo ocupa **toda** la animación: con la longitud física y un dash de 460
  —lo que había antes— el diente aparecía entero en el último 14 % y el resto del tiempo no pasaba
  nada visible. Cada servicio lleva su diente con el color que el médico le asignó, y "Servicios" del
  panel repite el gesto. La secuencia de reserva se lee como un raíl de tres pasos (`Servicio → Día y
  hora → Tus datos`) que marca lo hecho en verde y lo presente en menta, para que el paciente sepa
  cuántos pasos le quedan sin contar con que entienda el inglés de una barra de progreso.
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

## Publicar: la interfaz la sirve el propio FastAPI (Semana 2)

`frontend/vite.config.ts` usa **`@sveltejs/adapter-static` con `fallback: 'index.html'`**, y el
adaptador va **dentro de `sveltekit({ adapter: … })`**: en SvelteKit 3 un plugin de adaptador suelto
en `plugins` se ignora en silencio, el build termina con el aviso `adapter_missing` y no hay carpeta
`build/`. Con `npm run build` ya sale `frontend/build/` (241 KB: `index.html`, `_app/`, favicon y
`robots.txt`).

Por qué estático y no un servidor Node: las dos pantallas con estado llevan `export const ssr = false`
y no existe ningún `+page.server.ts` ni `+server.ts`, o sea que el navegador hace todo el trabajo.
`adapter-node` sería levantar un segundo proceso en otro puerto, con CORS y con la cookie del médico
viajando entre dos orígenes, para mover el mismo HTML que cabe en un `FileResponse`. Así hay **un solo
proceso** (`uvicorn`) y la cookie `HttpOnly` `SameSite=Lax` es del mismo origen sin abrir nada.

`backend/app/main.py` monta la carpeta (`carpeta_interfaz()` + `montar_interfaz(app, …)`) **después**
de las rutas de API:

- `/_app/**` va por `StaticFiles` envuelto en `_Inmutable`, que le pone
  `cache-control: public, max-age=31536000, immutable`: el nombre del archivo es un hash del contenido,
  así que caducarlo un año no puede servir nunca algo viejo, y un paciente que vuelve al enlace desde
  el móvil no baja otra vez los 240 KB. Starlette no admite `max_age`, de ahí el envoltorio ASGI.
- El resto se resuelve con una ruta comodín de `GET` **y `HEAD`** (un monitor, un CDN o un `curl -I`
  contra un servidor que responde 405 a HEAD se lee como servidor caído): si el archivo existe se
  manda, con `resolve()` + `is_relative_to()` para que ninguna forma de `..` saque la lectura de la
  carpeta; si no, se manda `index.html` con `Cache-Control: no-cache`. Eso es lo que hace que un enlace
  directo a `/panel` o a `/d/<slug>` funcione sin configuración de servidor, y que un despliegue nuevo
  no deje a un paciente con el HTML que apunta a los archivos del build anterior.
- Una ruta `/api/…` inexistente sigue respondiendo **404 en JSON**, no un `index.html` disfrazado de
  error.
- Si no hay `frontend/build` —la API sola, o CI, donde `backend` y `frontend` son jobs separados y el
  build nunca llega al job de las pruebas— `carpeta_interfaz()` devuelve `None` y el montaje se salta
  entero. La carpeta es `frontend/build` del monorepo o lo que marque `FRONTEND_BUILD`.

`backend/tests/test_estaticos.py` (6 pruebas, sin base de datos) monta un build de mentira con un
`secreto.env` al lado y comprueba los enlaces profundos, el 404 JSON de la API, cuatro formas de
salirse de la carpeta, el `HEAD /panel` y que sin build no se monta nada.

En local, ese mismo arranque es `sh scripts/arranque.sh` con el venv en el PATH (hace la migración y
levanta la API con la interfaz dentro en el 8000).

## Publicarlo: la imagen, los secretos y el cron

Código no hace falta más: `Dockerfile`, `docker-compose.yml` y `scripts/arranque.sh` son el
despliegue. Lo que queda es un host, dos secretos y las dos pegas del cron.

**La imagen** tiene dos etapas y un solo proceso al final. Node existe solo en la primera
(`npm ci` + `npm run build`); la segunda instala el backend con `pip install ./backend` y copia el
build a `/app/frontend/build`, que es donde `carpeta_interfaz()` lo encuentra sin poner
`FRONTEND_BUILD`. El `CMD` es `sh /app/scripts/arranque.sh`, y el `HEALTHCHECK` llama a `/healthz`
con `urllib` porque en la imagen slim no hay `curl` y bajarlo sería un segundo sistema que mantener.
`.dockerignore` deja fuera `backend/.env`, `node_modules`, `.svelte-kit`, `frontend/build` y el venv:
la clave con la que se firman las sesiones no viaja dentro de una imagen que se sube a un registro.

**Lo que no se pudo verificar en esta máquina**, dicho para que no se cuelen como probado: el daemon
de aquí es Boot2Docker 19.03 (kernel 4.19, 1 CPU, 1 GB) y no admite crear hilos dentro de un
contenedor. `npm ci` aborta en `uv_thread_create` y `pip install` en `RuntimeError: can't start new
thread`. Es el virtualizador, no el Dockerfile, pero significa que **la construcción completa de la
imagen hay que verla en el host real**. Lo que sí está comprobado aquí: el reparto de rutas dentro de
la imagen (`/app/frontend/build` con su `index.html`, su `_app`, su favicon y su `robots.txt`, y
`/app/scripts/arranque.sh` dentro), que `docker-compose config` acepta el archivo con los dos
servicios, y que `scripts/arranque.sh` arranca API + interfaz tal cual fuera de un contenedor.

**La base de datos que entrega el host vale tal cual** (`normalizar_url_database` en
`backend/app/config.py`, 19 pruebas). Railway, Neon, Supabase o Render inyectan en el servicio
`DATABASE_URL` con la forma `postgresql://usuario:clave@host:6543/base?sslmode=require`, y con la pila
de este proyecto eso no conecta dos veces: sin `+asyncpg` SQLAlchemy busca psycopg (no está instalado),
y con `+asyncpg` añadido a mano asyncpg rechaza el argumento —`TypeError: connect() got an unexpected
keyword argument 'sslmode'`, probado con SQLAlchemy 2.1.5 y asyncpg 0.32— antes de abrir el socket. La
app traduce el esquema a `postgresql+asyncpg://` y `sslmode` a `ssl`, que es como asyncpg llama a los
mismos modos de libpq (`require`, `verify-ca`, `verify-full`, `disable`), y deja el resto de la query
intacto. Se hace aquí y no en el panel porque el valor del proveedor no se puede editar, y porque
`alembic/env.py` lee el mismo ajuste: la migración, que es lo primero que corre en el arranque, también
lo necesita. SQLite y un `postgres://` mal formado **no se reescriben**: el guardia de arranque tiene
que poder enseñar el valor original en su mensaje.

**El comando de arranque** es el mismo en el contenedor, en un Fly, en Railway o en un systemd:
`alembic upgrade head` y `uvicorn --host 0.0.0.0 --port $PORT --proxy-headers`. La migración va
dentro y reintentada hasta dos minutos porque app y base de datos arrancan a la vez, y un contenedor
que muere en el intento 1 es un despliegue que parece roto sin estarlo. `--proxy-headers` es lo que
hace que detrás del proxy los enlaces y el log salgan con el esquema y el host verdaderos. La lista de
confianza se pasa por variable de entorno (`FORWARDED_ALLOW_IPS`, por defecto `*`) y **no** por
bandera: con `--forwarded-allow-ips '*'` el `*` lo expande el shell y uvicorn recibe el listado de la
carpeta como argumentos sueltos; pasó aquí, se arregló así.

**`ENTORNO=produccion` activa el guardia** (`backend/app/arranque.py`, 20 pruebas). Se ejecuta al
importar la app, antes de abrir el puerto:

```
El despliegue no arranca:
- JWT_SECRET sigue siendo el de desarrollo: con el repositorio delante, cualquiera puede firmarse
  una cookie válida y entrar en el panel de cualquier consultorio.
```

No arranca con: el `JWT_SECRET` del repo **o el de `.env.example`** (copiar el ejemplo y cambiar solo
`ENTORNO` es el fallo del despliegue de quince minutos), uno de menos de 32 caracteres, un
`DATABASE_URL` que no sea PostgreSQL (con SQLite la agenda va en local y dos reservas simultáneas se
pisan, porque no hay advisory lock), o un `ENTORNO` mal escrito —`production`, vacío—, porque un typo
no puede significar «me quedé sin comprobaciones». Son **avisos** por el log, no fallos: `CRON_SECRET`
vacío (las dos rutas internas responden 503) y `RECORDATORIOS_PROVEEDOR=smtp` a medias. Con ese
entorno la cookie de sesión sale con `Secure`, y eso ya no depende del string de `FRONTEND_ORIGIN`.

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"   # JWT_SECRET
python -c "import secrets; print(secrets.token_urlsafe(24))"   # CRON_SECRET
```

**Las dos pegas** (`scripts/cron.py`, 17 pruebas: una llamada y el proceso se acaba):
`POST /api/v1/internal/recordatorios` cada 15 minutos con la cabecera `x-cron-secret`, y
`POST /api/v1/internal/limpieza` una vez al día. El guion es solo stdlib porque la imagen final no lleva
`curl` y porque el servicio del cron no necesita leer la base: habla con la API como cualquier cliente.
Acepta `recordatorios` y `limpieza`, `--simular` para la primera pasada, `--url` y `--secreto` (o
`API_URL` y `CRON_SECRET` del entorno), y sale con 2 si falta configuración, 3 si la API respondió error
y 4 si no se llegó. Reintentar es seguro: lo enviado está marcado en la base y la limpieza va en
transacción con candado. La primera pasada de la limpieza de cada despliegue, con `--simular`: son los
mismos `DELETE` bajo el mismo candado y deshace la transacción, así que se ven las cuentas exactas antes
de perder una fila.

**Ruta corta con Docker Compose en un VPS**: `docker compose up -d` levanta `postgres` y `app`; en el
`docker-compose.yml` están `ENTORNO=desarrollo` y el `JWT_SECRET` del ejemplo a propósito, para que
nadie suba el archivo tal cual a producción y se lleve el chasco de que el contenedor no arranca.
Cámbialos por secretos reales y pon `ENTORNO=produccion`.

**Ruta corta con otro host de imágenes** (Fly, un PaaS que construya el `Dockerfile`): en variables
`DATABASE_URL` (la del host tal cual, se traduce arriba), `ENTORNO=produccion`, `JWT_SECRET`,
`CRON_SECRET`, y `FRONTEND_ORIGIN=https://tu-dominio` (hoy no interviene para nada en la sesión, pero
deja el CORS apuntando al sitio real por si las dos mitades se separan algún día). Comprobación del
primer arranque, en este orden: `/healthz` da 200, `/panel` devuelve HTML, `/api/v1/services` da **401**
(no 200 con el HTML disfrazado), y en el log no hay ningún `aviso de despliegue`. Después: registrar la
consulta en `/auth/registro`, pegar el enlace en el móvil, pasar una cita de prueba y borrarla desde el
panel. En Railway, que es el host elegido, esos pasos están concretos abajo.

**El correo** es lo último, porque es lo único que escribe fuera: `RECORDATORIOS_PROVEEDOR=smtp`,
`SMTP_HOST`, `SMTP_DE` (muchos proveedores rechazan un `From` que no es la cuenta autenticada),
`SMTP_USUARIO`/`SMTP_CONTRASENA` y `SMTP_SSL=true` solo con el 465. Hasta entonces el simulado guarda
en la base lo que habría mandado, y la página pública no promete nada que no esté pasando.

## Railway: los clics, los dos servicios y las variables

Host elegido: **Railway**, con la cuenta del fundador. Son **dos servicios del mismo repositorio y la
misma imagen**: `agenda` (la web, la que ve el paciente) y `cron-avisos` (el programado, una pasada cada
quince minutos). El orden es este:

**0. El repo publicado.** Railway construye desde GitHub, no desde esta máquina: `railway up` con un
`Dockerfile` propio sigue sin estar soportado (así lo llama el issue abierto
[`railwayapp/cli#819`](https://github.com/railwayapp/cli/issues/819)) y el Docker de aquí no admite crear
hilos dentro de un contenedor. Antes de nada, por tanto, `main` tiene que llevar el `Dockerfile` y todo
lo de estas dos semanas.

**1. El proyecto.** New Project → **Deploy from GitHub repo** → `willlxxx/agenda-solo-miniapp`. La primera
vez pide instalar la app de Railway en el repo, y eso se hace con su cuenta. Railway encontró el
`Dockerfile` solo (builder «Dockerfile Automatically Detected») y construyó con él, así que la imagen está
servida sin configurar nada.

**Los dos `*.toml` del repo no se están usando, y no hay que contar con ellos.** En el servicio de este
proyecto la página de Settings no tiene ningún archivo de configuración enlazado (el botón dice «Add File
Path») y el propio panel avisa de que *Config as Code* está obsoleto: los archivos que ya lo usaban siguen
valiendo hasta el 2026-12-01, pero un servicio que nunca lo usó no puede activarlo desde el 2026-08-28.
Se cae por tanto la regla que se había anotado aquí («la configuración definida en código gana siempre»):
**en este despliegue la fuente de verdad es el panel**, y `railway.toml` y `railway.cron.toml` quedan como
referencia escrita de lo que debe ir en cada pantalla, no como lo que el host lee.

**2. La base de datos.** New → PostgreSQL. **No hay que tocar la variable**: Railway inyecta en el
servicio un `DATABASE_URL` de su forma, y `normalizar_url_database` lo traduce (arriba, «La base de datos
que entrega el host vale tal cual»). La primera vez que se despliegue conviene mirar el log de
`arranque.sh`: si la migración llega hasta `Running upgrade` es que la URL se abrió bien.

**3. Variables del servicio web** (Settings → Variables):

```
ENTORNO=produccion
JWT_SECRET            el token_urlsafe(32), como secreto
CRON_SECRET           el token_urlsafe(24), como secreto
RECORDATORIOS_PROVEEDOR=simulado        hasta tener el SMTP del doctor
FRONTEND_ORIGIN=https://<dominio>
DATABASE_URL          (la pone Railway sola)
```

Con `ENTORNO=produccion` el guardia se niega a arrancar si `JWT_SECRET` sigue siendo el del repo o el
del ejemplo, y la cookie de sesión sale con `Secure`.

**4. El dominio.** Settings → Networking → **Generate domain**. Con esa dirección se rellena
`FRONTEND_ORIGIN` (no afecta a la cookie, pero deja el CORS apuntando al sitio real) y se hace la
comprobación: `/healthz` da 200, `/panel` devuelve HTML, `/api/v1/services` da **401** —no 200 con el
HTML disfrazado— y en el log no aparece ningún `aviso de despliegue`. Después, registrar la consulta en
`/auth/registro`, pegar el enlace en el móvil, pasar una cita de prueba y borrarla desde el panel. En
producción no hay datos sembrados: la cuenta de prueba de esta máquina no viaja.

**5. El servicio del cron.** Another service → Deploy from GitHub repo → el mismo repo, otra vez. Como
*Config as Code* no entra en juego (paso 1), el orden de arranque y el horario se escriben en su Settings,
no en `railway.cron.toml`. Sus variables:

```
API_URL=https://<dominio del servicio web>
CRON_SECRET           el mismo valor, otra vez
```

El horario se escribe en Settings → Cron Schedule: `*/15 * * * *`. El límite del host es que dos
ejecuciones estén separadas **al menos 5 minutos en UTC**, así que un cuarto de hora cabe holgado. No lleva
`healthcheckPath`: el trabajo termina y el proceso se va, que es justo lo que pide el cron de Railway.

Así quedó montado en el host: el servicio nuevo venía con Railpack y se le eligió **Dockerfile**, para que
construya con este `Dockerfile` de dos etapas; en Settings lleva `python scripts/cron.py recordatorios`
como orden de arranque y el preset *Every 15 minutes*. `API_URL` es la URL que el propio host le generó al
servicio web, y no necesita dominio propio: habla con la API como lo haría cualquier cliente. Para
`CRON_SECRET` no hubo que copiar el valor: en Variables se declaró como **Reference**
`${{agenda-solo-miniapp.CRON_SECRET}}`, y Railway lo resuelve leyendo la variable del otro servicio —con
eso el secreto no sale de la cuenta ni pasa por un portapapeles—.

**6. Comprobar el primer tick** (log del servicio `cron-avisos`). Cada línea dice el estado y los
contadores que devuelve la ruta interna, nunca datos de un paciente:

- `recordatorios 200: {"enviados": 0, ...}` → funcionando; con `RECORDATORIOS_PROVEEDOR=simulado` los
  avisos se registran en la base y no salen correos.
- `recordatorios 401` → `CRON_SECRET` distinto en los dos servicios.
- `recordatorios 503` → el `CRON_SECRET` del servicio web está vacío y las rutas internas están apagadas.
- `fallo: no se pudo llamar a https://…` → `API_URL` mal escrito, o la agenda aún no había levantado.

Sobre la **Restart Policy** del servicio del cron: con un horario puesto, el propio panel de Settings lo
dice —*Services with a cron schedule do not have a restart policy*—, así que no hay que cambiarla a
`None` a mano; el proceso que termina bien no vuelve a arrancar.

**7. Lo que queda apagado a propósito: la limpieza.** La decisión del primer despliegue es lanzar solo
los avisos; la ruta que borra el histórico viejo se deja fuera hasta ver las cuentas. Cuando toque, un
tercer servicio igual que `cron-avisos` (mismo repo, mismo Dockerfile) pero con este orden de arranque en
su Settings y un horario diario:

```
python scripts/cron.py limpieza --simular
```

La primera semana se lee el `--simular` (deshace la transacción: enseña cuántas filas cerradas tiene cada
consultorio fuera de su plazo); cuando cuadre, se quita el flag.

**Cómo salió el primer despliegue real** (2026-10-20, cuenta del fundador, proyecto con los dos servicios
y su PostgreSQL en EU West 1). Los dos contenedores se construyeron con este `Dockerfile` en los
servidores del host, así que eso deja de ser una incógnita: en el log del arranque se leyó
`Context impl PostgresqlImpl` y `Will assume transactional DDL` —la migración contra la base que inyecta
Railway, o sea que `normalizar_url_database` funcionó con la URL real—, después `Application startup
complete` y `Uvicorn running on http://0.0.0.0:8080`. El **healthcheck** también: con
`healthcheckPath = /healthz`, la propia sonda del host aparece en el log como
`100.64.0.1… "GET /healthz" 200 OK` en cuanto levanta, y desde fuera `/healthz` devuelve
`{"estado":"ok"}`, `/` y `/reservas` dan 200. Las otras dos comprobaciones del primer arranque también:
`/panel` devuelve HTML y `/api/v1/services` da **401** en JSON, no 200 con el HTML disfrazado. La ruta
interna sigue cerrada desde fuera: un `POST /api/v1/internal/recordatorios` sin cabecera de secreto
responde **401** (503 sería el caso de `CRON_SECRET` vacío en el servicio web, y aquí no lo está).

Un detalle del healthcheck que el despliegue dejó en evidencia: el `HEALTHCHECK` del `Dockerfile` llevaba
el 8000 fijado a mano, y el host inyectó `PORT=8080`. No afectó a Railway, que sondea su
`healthcheckPath`, pero en `docker compose` habría dado por roto un contenedor sano, y al revés: como la
interfaz se sirve con *fallback*, cualquier ruta desconocida contesta 200 con el HTML de la SPA, así que
mirar solo el estado no demuestra que la API esté viva. Por eso el sondeo de la imagen es
`scripts/salud.py` (11 pruebas en `test_sondeo_salud.py`): respeta `PORT` igual que `arranque.sh` y pide
el cuerpo que `/healthz` promete.

El **guardia de arranque saltó de verdad**, que es la prueba que le faltaba: cinco despliegues entre las
01:26 y las 01:55 murieron en el intento, y en el log de uno de ellos se leía el motivo exacto —
`RuntimeError: El despliegue no arranca: - JWT_SECRET tiene 16 caracteres y hacen falta 32 o más`—, o sea
que el proceso prefirió no abrir el puerto antes que servir un sitio público con sesiones falsificables.
Con el `token_urlsafe(32)` puesto, el servicio está activo desde entonces y en el log no aparece ningún
`aviso de despliegue`.

El **cron** dio once pasadas desde que se montó (de 02:29 a 04:47, hora del panel en GMT-4) y cada una arranca
el contenedor, imprime una línea y termina, sin reiniciarse: cuatro sobre el despliegue viejo —02:29 lanzada a
mano con *Run now*, y 02:32, 02:45 y 03:01 automáticas—, seis sobre la imagen que ya lleva `scripts/salud.py`
(03:20 a mano; 03:32, 03:46, 04:00, 04:17 y 04:31 automáticas) y una sobre el último despliegue, la de 04:47.
Entre automáticas hay saltos de 12 a 17 minutos, porque el horario del host no es un metrónomo. Todas con 200 y
sus contadores, y los contadores cuentan una historia seguida:

```
02:29  recordatorios 200: {"candidatas": 1, "planificados": 0, "enviados": 0, "enviados_1h": 0,
                           "enviados_24h": 0, "omitidos": 0, "agotados": 0, "errores": 0, ...}
03:01  recordatorios 200: {"candidatas": 1, "planificados": 1, "enviados": 1, "enviados_1h": 1,
                           "enviados_24h": 0, "omitidos": 0, "agotados": 0, "errores": 0, ...}
03:20  recordatorios 200: {"candidatas": 1, "planificados": 0, "enviados": 0, ...}
03:32  recordatorios 200: {"candidatas": 0, "planificados": 0, "enviados": 0, ...}
```

(03:46, 04:00, 04:17, 04:31 y 04:47 repiten la línea de 03:32 tal cual; `husos_invalidos`, `sin_correo` y el
`proveedor: "simulado"` van en todas y aquí están cortados por legibilidad).
Hasta las 03:01 la pasada barría la única cita confirmada de la base real y el dominio decidía callar:
`candidatas: 1` con `planificados: 0` es una cita dentro del rango de lectura pero fuera de las dos ventanas de
adelanto (24 h pide entre 12 y 24 h de margen, 1 h entre 40 y 60 minutos), o dentro de la franja de silencio del
huso del doctor. A las 03:01 entró en la ventana de 1 h y esa pasada hizo el recorrido completo:
`planificados: 1, enviados: 1, enviados_1h: 1` —leyó la agenda, decidió, entregó al proveedor y escribió la
bandera con su marca de tiempo en el PostgreSQL del host. Con `RECORDATORIOS_PROVEEDOR=simulado` la entrega es
el registro interno, no un correo que alguien recibió: lo probado es el viaje hasta la bandera, y lo que sigue
sin probar es que el SMTP del doctor escriba de verdad. La línea de 03:20 vuelve a ver la cita y ya no planea
nada porque la bandera de 1 h está puesta: el dominio la mira antes de decidir, y dentro del lock se revalida
por si otra pasada acaba de ponerla. A las 03:32 la lectura ya no la alcanza (`candidatas: 0`) porque caía por
debajo del adelanto mínimo de las ventanas, o porque ya había pasado. Lo que esa bandera pinta en la
mini-ficha del panel y en el export hace falta verlo con la sesión del doctor.

Un 200 con contadores es además la prueba de los enchufes: la Reference del secreto se resolvió (con 401 o 503
no habría contadores que leer), `API_URL` apunta al servicio web, y la pasada leyó la base real. Un aviso para
quien lo repita: el visor del cron devuelve «No logs in this time range» con la ventana de 1 s que trae el
enlace de la ejecución; pidiendo en la URL una franja más ancha, las líneas salen.

Tras empujar esas correcciones, el host volvió a construir **los dos servicios** solos
(`agenda-solo-miniapp` ACTIVE y `cron-avisos` completado a las 03:15 GMT-4), así que la agenda pública y el
disparador ya corren sobre la imagen con `scripts/salud.py`; las siete pasadas que van de 03:20 a 04:47 son de
esas imágenes, y un segundo rebuild posterior al último commit documentó la de 04:47 con `recordatorios 200`.
El lado de la API lo confirma por su cuenta: en su log se lee `152.55.185.210… "POST
/api/v1/internal/recordatorios" 200 OK` a las 03:20, con otra IP distinta de la de los `GET` de esta máquina.

**Lo que sigue sin probar**, para que tampoco cuente: que un correo llegue a un paciente de verdad —con
`RECORDATORIOS_PROVEEDOR=simulado`, que es como está a propósito, el aviso se registra en la base y no sale—,
el SMTP real del doctor, un dominio propio en lugar del
`*.up.railway.app`, la ruta de limpieza —que sigue apagada a propósito— y el coste del plan con los dos
servicios. Faltan dos comprobaciones que solo se ven con la agenda por delante: en el log del cron, una pasada
con `enviados_24h: 1` —la ventana de 1 h ya dio su `enviados_1h: 1`, así que se sabe exactamente qué buscar—, y
en el panel del doctor, con su sesión, la mini-ficha de esa cita mostrando el aviso registrado. Ninguna de las
dos exige escribir código: basta con tener una cita a menos de un día y esperar al tick.

## Siguiente paso

El paciente reserva, el doctor gestiona su semana, recoge a quien se quedó sin hueco, le ofrece una
hora real cuando la hay, y el aviso de la cita se decide, se entrega y se marca solo en cada pasada del cron:
en la base real ya pasó una vez (`enviados_1h: 1` a las 03:01), con el proveedor simulado, o sea sin que un
correo saliera. Lo que falta:

- **Publicado está**: el host (Railway), la imagen construida por el host, la base del host migrada, el
  healthcheck y el cron de avisos cada quince minutos, todo ello visto en los logs de arriba. En esa base
  ya hay al menos una consulta con una cita confirmada, que es la que el cron cuenta como `candidatas: 1`.
  Lo que sigue en mano del fundador es el **SMTP real del doctor**, que es lo único que hace que salga un
  correo de verdad: con `RECORDATORIOS_PROVEEDOR=smtp`, `SMTP_HOST`, `SMTP_DE`, `SMTP_USUARIO`,
  `SMTP_CONTRASENA` y `SMTP_SSL` si procede, el despacho deja de ser simulado. La limpieza por cron sigue
  apagada a propósito hasta leer la primera pasada en `--simular`.
- Panel: la mini-ficha de cada cita ya dice cuándo se recordó (y el export lo lleva por escrito). Lo
  que falta es la vista de conjunto: una columna con el estado de los avisos de toda la semana, para
  responder a "no me avisasteis" sin ir cita por cita.
- El aviso de retención de la página pública todavía dice que se puede pedir el archivo o el borrado,
  pero no cuantifica el plazo. En cuanto el cron de limpieza esté montado se puede añadir "y se borra
  solo a los X meses"; hasta entonces sería prometer un automático que no corre.
- Fase 2 del documento, todavía fuera: pagos, Google Calendar, integración con el software dental.

