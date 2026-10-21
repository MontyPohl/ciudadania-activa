# Ciudadanía Activa

**Estado al 20/10/2026:** S14 completado — implementado, desplegado y verificado.
El [PR #54](https://github.com/MontyPohl/ciudadania-activa/pull/54) se integró en `main`
como `4bd05fb`, con CI del candidato `f9a9da0` 6/6 verde; Heroku v26 desplegó la reconciliación `a7f775d`.
IA OpenAI activa con prompt v2 y correo Brevo configurado; el primer envío real con esta
configuración sigue pendiente de observar. El repositorio es público desde el 19/10.
Ver [progreso por sprint](docs/PROGRESO_SPRINTS.md) y [registro del despliegue, §15](docs/runbooks/HEROKU_MIGRATION.md#15-operación-vigente--corte-20102026-0125-utc).

Plataforma de reportes urbanos ciudadanos con verificación de identidad, validación institucional
e incentivos (puntos de impacto y créditos canjeables). Trabajo de Conclusión de Carrera —
Universidad Columbia del Paraguay.

Documentación completa del proyecto en [docs/PLAN_DESARROLLO.md](docs/PLAN_DESARROLLO.md),
**contrato de la API en [docs/API.md](docs/API.md)**, modelo de datos en
[docs/DICCIONARIO_DATOS.md](docs/DICCIONARIO_DATOS.md), avance por sprint en
[docs/PROGRESO_SPRINTS.md](docs/PROGRESO_SPRINTS.md), backlog priorizado en
[docs/BACKLOG.md](docs/BACKLOG.md) y decisiones de arquitectura en [docs/adr/](docs/adr/).
Las diferencias entre el prototipo real y lo declarado en la tesis se registran en
[docs/CAMBIOS_TESIS_PENDIENTES.md](docs/CAMBIOS_TESIS_PENDIENTES.md).
La infraestructura aislada para futuras ejecuciones experimentales S11 se documenta en
[docs/runbooks/S11_EXPERIMENTAL_INSTRUMENTATION.md](docs/runbooks/S11_EXPERIMENTAL_INSTRUMENTATION.md);
su presencia no autoriza experimentos ni contiene resultados de tesis.

## Stack

| Capa | Tecnología |
|---|---|
| Frontend | Next.js (App Router) + TypeScript + TailwindCSS |
| Backend | Python 3.11 + Django + Django REST Framework |
| Base de datos | PostgreSQL 17 + PostGIS, alojada en **Supabase** |
| Almacenamiento | Supabase Storage |
| Caché / broker | Redis 7 |
| Asincronía | Celery (`worker=1` y un único `beat=1` activos desde el cierre productivo de S10) |
| Hosting | Vercel (frontend) · Heroku (backend + workers) · Supabase (DB + storage) |
| Calidad | Ruff (Python) · ESLint + Prettier (TypeScript) · GitHub Actions |

## Estructura del repositorio

```text
ciudadania-activa/
├── backend/     # Proyecto Django (config/ + apps/)
├── frontend/    # Proyecto Next.js
├── infra/       # docker-compose.yml (PostGIS + Redis + backend + worker)
├── docs/        # Plan de desarrollo, progreso, ADRs, backlog
└── .github/workflows/  # CI de backend y frontend
```

## Requisitos

- Python 3.11+
- Node.js 22+
- Docker Desktop (para PostGIS y Redis locales)
- Cuenta de Supabase (base de datos + storage) y de Heroku/Vercel para despliegue

## Puesta en marcha local

### 1. Variables de entorno

```bash
cp .env.example .env
```

Completar los valores de terceros (Google OAuth, OpenAI, Supabase, email) a medida que los
sprints correspondientes los requieran. `.env` nunca se versiona.

`.env` describe **siempre el entorno local**. Las credenciales de Supabase no van aquí: viven en
`.env.supabase.local` y solo se usan para verificaciones puntuales (ver
[Verificación contra Supabase](#verificación-contra-supabase)).

### 2. Base de datos y Redis (Docker)

```bash
docker compose -f infra/docker-compose.yml up db redis
```

Esto expone PostGIS en `localhost:5432` y Redis en `localhost:6379`, según lo declarado en `.env`.

> Alternativa: `docker compose -f infra/docker-compose.yml up` levanta además los contenedores
> `backend`, `worker` y `beat`, útil para reproducir el stack completo sin instalar Python
> localmente. `beat` programa cada 15 minutos el autocierre de reportes resueltos.

El contenedor `db` usa `postgis/postgis:17-3.5`, alineado con el major de PostgreSQL que entrega
Supabase. **Cambiar el major de PostgreSQL invalida el volumen `infra_postgres_data`**: hay que
eliminarlo y dejar que se reinicialice (`docker compose -f infra/docker-compose.yml stop db && docker
volume rm infra_postgres_data`). Eliminar el volumen **borra sus datos locales**. `migrate` y los
seeds pueden reconstruir el esquema y las cargas sintéticas, pero no recuperan reportes ni cuentas creados a mano;
respaldalos antes si necesitás conservarlos.

> **Aislamiento deliberado:** `infra/docker-compose.yml` fija `DATABASE_URL` al contenedor local en
> el bloque `environment` de `backend`, `worker` y `beat`, que tiene precedencia sobre `env_file`.
> Por eso
> `docker compose up` **nunca** escribe en Supabase, aunque el `.env` contenga una URL remota. Es
> intencional y no debe "corregirse" (ADR-0003).

### 3. Backend (Django)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements/dev.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

La API queda disponible en `http://localhost:8000/api/v1/`. Endpoint de verificación:
`GET /api/v1/health/`.

#### Acceso con Google (Sprint 3)

La autenticación usa **Google Identity Services**: el navegador obtiene un ID token y el
backend lo verifica antes de emitir su propio JWT. Hace falta un **OAuth Client ID** de
tipo «aplicación web» creado en Google Cloud, con estos orígenes JavaScript autorizados:

- `http://localhost:3000` para desarrollo
- `https://ciudadania-activa.vercel.app` para producción

**No se configuran redirect URIs ni client secret**: el flujo aprobado es de ID token, no
de Authorization Code (ver [ADR-0005](docs/adr/ADR-0005-autenticacion-oauth-jwt.md)).

El mismo valor va en dos variables: `GOOGLE_OAUTH_CLIENT_ID` en el backend y
`NEXT_PUBLIC_GOOGLE_CLIENT_ID` en el frontend. El Client ID **no es un secreto** —viaja al
navegador por diseño—, pero tampoco se versiona: `.env.example` solo declara los nombres.

Sin esa variable el proyecto **sigue arrancando y compilando**: la pantalla de acceso
muestra un aviso de que Google no está configurado y el backend rechaza cualquier intento
de login de forma segura.

Los roles no se asignan desde la interfaz: se otorgan por consola con `promote_user` (ver
[Comandos habituales](#comandos-habituales)).

#### Catálogos semilla

`migrate` deja cargadas las 3 instituciones y las 10 categorías del MVP mediante la
migración de datos `admin_app.0002_seed_catalogs`, de modo que **no hace falta ningún paso
extra** en una instalación desde cero.

Para reponer o reparar el catálogo sobre una base ya migrada:

```bash
python manage.py seed_catalogs      # equivalente: python scripts/seed_catalogs.py
```

El comando es **idempotente**: identifica cada fila por su `slug`, así que ejecutarlo varias
veces no duplica ni recrea registros. No escribe `contact_email` ni `sla_hours`, valores que
la documentación del proyecto todavía no define. El detalle del modelo de datos está en
[docs/DICCIONARIO_DATOS.md](docs/DICCIONARIO_DATOS.md).

#### Reportes sintéticos del mapa

Después de disponer de los catálogos, la demostración local del mapa se carga expresamente con:

```bash
python manage.py seed_reports
```

El comando no corre con `migrate`. Crea o repara 14 reportes inequívocamente sintéticos, dos por
estado, con UUID v5 y ubicaciones deterministas del Gran Asunción. Es idempotente, no crea
usuarios, no borra reportes ajenos y deja `citizen = NULL`. Los estados avanzados son snapshots
de demostración: no crean historiales ni anticipan los workflows de los Sprints 9 y 10.

### 4. Frontend (Next.js)

```bash
cd frontend
# Crear .env.local solo con las dos variables públicas:
# NEXT_PUBLIC_API_URL y NEXT_PUBLIC_GOOGLE_CLIENT_ID (ver .env.example)
npm install
npm run dev
```

La aplicación queda disponible en `http://localhost:3000` y consulta el estado del backend en la
página de inicio. El mapa público en `/mapa` usa Leaflet y Supercluster con los tiles HTTPS
oficiales de OpenStreetMap; conserva siempre visible la atribución y solicita únicamente los tiles
del viewport que el navegador necesita mostrar.

## Verificación contra Supabase

El desarrollo normal usa la base local. Para comprobar puntualmente que el backend funciona contra
la instancia real de Supabase (migraciones, PostGIS, health check) se usa un **contenedor efímero**
que no pasa por `docker compose`, precisamente para no romper el aislamiento descrito arriba.

1. Crear `.env.supabase.local` en la raíz del repositorio. Está ignorado por Git y **nunca se
   versiona**. Contiene solo lo necesario para la prueba:

   ```ini
   DJANGO_SETTINGS_MODULE=config.settings.dev
   DJANGO_SECRET_KEY=<cualquier valor de desarrollo>
   DJANGO_DEBUG=True
   DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
   API_VERSION=0.1.0
   # Session Pooler de Supabase (puerto 5432) + TLS obligatorio.
   DATABASE_URL=postgis://<usuario>:<password>@<host>.pooler.supabase.com:5432/postgres?sslmode=require
   DB_SEARCH_PATH=public,gis,extensions
   REDIS_URL=redis://redis:6379/0
   ```

   La cadena se copia desde **Supabase → Project Settings → Database → Connection string → Session
   pooler**. Dos condiciones que no son opcionales:

   - **Session Pooler (puerto 5432)**, no el Transaction Pooler (6543): `psycopg` usa *prepared
     statements* por defecto y el modo transacción no los soporta, por lo que `migrate` fallaría.
     La conexión directa (`db.<ref>.supabase.co`) solo resuelve por IPv6 salvo con el add-on IPv4.
   - **`sslmode=require`**: sin él se usa el `prefer` por defecto, que acepta texto plano de forma
     silenciosa si el servidor no ofrece TLS.
   - Si la contraseña tiene caracteres especiales, hay que codificarlos en la URL
     (`@` → `%40`, `#` → `%23`, `/` → `%2F`, …).

2. Levantar Redis y ejecutar los comandos en un contenedor efímero:

   ```bash
   docker compose -f infra/docker-compose.yml up -d redis
   docker compose -f infra/docker-compose.yml build backend

   docker run --rm --network infra_default --env-file .env.supabase.local \
     -v "$PWD/backend:/app" -w /app infra-backend:latest \
     python manage.py showmigrations
   ```

   Sustituyendo el comando final por `migrate --no-input`, `makemigrations --check --no-input` o
   `check` según lo que se quiera verificar.

> Aplicar migraciones contra Supabase modifica la base real. Antes de una migración destructiva
> corresponde el respaldo lógico obligatorio: ver [Respaldos de la base de datos](#respaldos-de-la-base-de-datos).

## Secretos y configuración por servicio

Ningún valor real vive en el repositorio: `.env.example` declara **solo los nombres** y
`.env` está en `.gitignore`. Lo importante es que **no todos los servicios necesitan todo**,
y repartir credenciales de más es un riesgo gratuito.

| Variable | Backend web (Heroku) | Worker Celery (Heroku) | Celery Beat (Heroku) | Frontend (Vercel) |
|---|:--:|:--:|:--:|:--:|
| `DOCUMENT_HMAC_SECRET` | **Sí** | No | No | No |
| `SUPABASE_URL` | **Sí** | **Sí** (IA) | No | No |
| `SUPABASE_SERVICE_ROLE_KEY` | **Sí** | **Sí** (IA) | No | No |
| `SUPABASE_STORAGE_BUCKET_PRIVATE` | **Sí** | No | No | No |
| `SUPABASE_STORAGE_BUCKET_REPORTS` | **Sí** | **Sí** (IA) | No | No |
| `AI_CLASSIFICATION_ENABLED` · `AI_PROVIDER` · `AI_PROMPT_VERSION` | No | **Sí** | No | No |
| `OPENAI_API_KEY` · `OPENAI_MODEL` · `OPENAI_REASONING_EFFORT` | No | **Sí** | No | No |
| `REDIS_SSL_CERT_REQS` | Opcional | Opcional | Opcional | No |
| `BREVO_API_KEY` · `BREVO_API_URL` · `DEFAULT_FROM_EMAIL` | No | **Sí** | No | No |
| `DATABASE_URL` · `REDIS_URL` · `DJANGO_SECRET_KEY` | **Sí** | **Sí** | **Sí** | No |
| `REPORT_AUTO_CLOSE_HOURS` | No | **Opcional** | **Opcional** | No |
| `PUBLIC_MAP_CACHE_TTL_SECONDS` | **Sí** | No | No | No |
| `DASHBOARD_METRICS_CACHE_TTL_SECONDS` (default `120`) · `PUBLIC_METRICS_CACHE_TTL_SECONDS` (default `300`) | Opcional | No | No | No |
| `NEXT_PUBLIC_API_URL` · `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | No | No | No | **Sí** (públicas) |

El backend web crea verificaciones y firma imágenes; el worker de IA descarga fotografías
privadas de reportes y también necesita la credencial de Storage. Solo web usa la clave HMAC.
El worker es el único que envía correo, así que es el único que necesita la clave de Brevo. **Ninguna variable de Supabase llega al
frontend**: el navegador nunca habla con el almacenamiento.

Sin estas variables la aplicación **arranca igual**. `config/settings/prod.py` no las exige:
backend web y worker comparten ajustes. La configuración se exige en la operación que la
necesita; un worker sin IA no necesita Storage. En Heroku los procesos de una misma app
comparten las config vars: la tabla indica consumo, no aislamiento efectivo por proceso. El
comportamiento es fallo cerrado **local**: el endpoint que las necesita responde `503` genérico
y el resto del sistema sigue funcionando.

El scheduler se despliega como un proceso único separado del worker, con el comando
`celery -A config.celery:app beat --loglevel=info`. Beat agenda tres tareas: `reports.auto_close`
(cada 15 min), `notifications.dispatch_due` (cada 5 min) y `rewards.notify_available_rewards`
(09:00 locales). Es el worker quien las ejecuta, y para `reports.auto_close` consulta
`REPORT_AUTO_CLOSE_HOURS`; si se sobrescribe el valor por defecto, debe configurarse de forma consistente al menos en todos los workers que consuman esa
cola. La variable es opcional, debe ser un entero positivo y vale `72` por defecto; un valor
inválido impide el arranque del proceso que lo reciba. Ejecutar más de una instancia de Beat
duplicaría entregas innecesarias, aunque el servicio conserva idempotencia bajo lock del reporte.

### `DOCUMENT_HMAC_SECRET`

Clave del HMAC-SHA-256 con el que se guarda la cédula. Tres reglas, detalladas en
[ADR-0006](docs/adr/ADR-0006-identidad-hmac-y-almacenamiento-privado.md):

- **Una por entorno.** Desarrollo la suya, la suite de pruebas una ficticia versionada,
  producción la real. La de producción **no se comparte** con desarrollo ni con CI.
- **Estable en producción** desde que exista la primera identidad verificada real. Cambiarla
  invalida todos los `document_hmac` ya calculados y **no hay forma de recalcularlos**, porque
  el número de cédula no se guarda en claro en ninguna parte.
- **Nunca versionada.** Alta entropía, solo en variables de entorno del proveedor.

### Supabase Storage

Bucket **privado** (`SUPABASE_STORAGE_BUCKET_PRIVATE`, valor previsto `verificaciones`), sin
acceso anónimo. En PostgreSQL se guarda la **clave del objeto**
—`verifications/{user_id}/{uuid4}.{ext}`—, nunca una URL. Para ver una selfie, el backend emite
una **URL firmada de 300 segundos** en el detalle administrativo; no se persiste, no se cachea y
no se registra en logs.

`SUPABASE_SERVICE_ROLE_KEY` tiene privilegios totales sobre el almacenamiento: solo se configura
en backend web y worker de IA; nunca en Vercel.

### Correo transaccional

**No se usa SMTP.** El plan Free de Railway no ofrecía SMTP saliente: la conexión no se
rechazaba, se quedaba colgada hasta agotar el timeout. La decisión sigue vigente tras migrar a
Heroku, que tampoco garantiza SMTP saliente en los planes en uso. Eso es exactamente lo que ocurrió en la
validación E2E productiva del 23/08/2026 —`TimeoutError` tras 135 s, notificación en `FALLIDO`
y correo que nunca llegó—. El envío va contra la **API HTTPS de correo transaccional de Brevo**,
`POST https://api.brevo.com/v3/smtp/email`, que sale por el puerto 443 como cualquier otra
llamada del proyecto. La decisión y sus alternativas están en
[ADR-0015](docs/adr/ADR-0015-correo-transaccional-por-api-https.md).

`BREVO_API_KEY` es configuración del **worker**, que es quien envía. El backend web solo escribe
la fila en `notifications`, así que no la necesita. Sin ella el envío **falla cerrado** —no se
intenta ninguna llamada— y la notificación queda en `FALLIDO`, que es un estado previsto y
consultable en el admin, no una caída del servicio.

Tres precisiones que ahorran un rato de depuración:

- **La clave de API no es la clave SMTP.** Brevo emite las dos por separado, en la misma
  pantalla («SMTP & API»). La SMTP no autentica contra la API v3: devuelve `401`.
- **El remitente debe estar verificado en Brevo.** `DEFAULT_FROM_EMAIL` admite la forma
  `Nombre <direccion>`; el cliente la separa y la envía como `{"name": ..., "email": ...}`. Si
  la dirección no figura como remitente verificado, Brevo rechaza el envío con `400`.
- **Reintentos automáticos acotados (S13).** Solo los fallos transitorios (red, timeout, 5xx o
  429) se reintentan: 3 intentos en total, con esperas de 5 y 30 minutos, mediante el barrido
  `notifications.dispatch_due`, que nunca envía notificaciones de más de 24 h sin intentos. Un
  fallo permanente deja la notificación en `FALLIDO` y ahí se queda: reencolar la tarea sobre
  una notificación fallida **no la reenvía**.

Para recuperar una notificación fallida hay una puerta explícita y solo una:

```bash
python manage.py resend_notification <notification_uuid>
```

Devuelve **esa** notificación de `FALLIDO` a `PENDIENTE`, lo registra en `audit_logs` como
`notification.resent` y encola el envío. El correo lo entrega el worker por el camino normal;
el comando no habla con Brevo. Exige que la notificación esté en `FALLIDO`: sobre una
`PENDIENTE` o `ENVIADO` falla sin tocar nada, porque reactivarla duplicaría el correo. Si el
broker no responde, la notificación vuelve a `FALLIDO` y basta con repetir el comando.

**Cómo obtener el UUID.** El comando exige que se lo pasen explícitamente: no busca la
notificación por usuario ni por fecha, a propósito, para que reenviar sea siempre un acto
dirigido a una fila concreta. El identificador debe averiguarse con una **consulta
administrativa de solo lectura o una herramienta operativa autorizada**, y pasarse después
al comando.

No se documenta aquí el admin de Django como vía estándar: `Notification` está registrada
en él, pero entrar exige `is_staff`, y **el rol de dominio `admin` no lo concede**
—`promote_user` no toca `is_staff` ni `is_superuser` (ADR-0005 §11)—. Que exista una cuenta
con acceso al admin en un entorno concreto es una circunstancia de ese entorno, no una
garantía del sistema.

## Comandos habituales

`promote_user` exige una cuenta existente; operador requiere `--institution`, administrador
no lo admite. No concede `is_staff` ni `is_superuser`. Los comandos `ai_*` son de gestión,
no endpoints; sus opciones y efectos están en [API §9](docs/API.md#9-comandos-de-gestión-de-ia-post-s12).

| Comando | Descripción |
|---|---|
| `cd backend && pytest --cov=apps --cov-config=coverage-gate.toml --cov-report=term-missing --ignore=apps/experiments_app` | Pruebas del dominio con compuerta de cobertura de producto ≥ 80 %; mismo alcance que `ci-backend.yml` |
| `cd backend && coverage report --rcfile=coverage-ht5.toml` | Reporte informativo HT5-01 sobre los mismos datos del dominio: manifiesto congelado de 33 archivos de S11A |
| `cd backend && COVERAGE_FILE=.coverage-experiments pytest apps/experiments_app --cov=apps --cov-report=term-missing --ignore=apps/experiments_app/tests/test_ex10_official_launcher.py --ignore=apps/experiments_app/tests/test_ex10_launcher.py --ignore=apps/experiments_app/tests/test_ex10_preflight_launcher.py` | Aparato experimental separado, como `ci-experiments.yml`; no hereda la compuerta del dominio y conserva sus datos en otro archivo |
| `cd backend && ruff check .` | Lint del backend |
| `cd backend && python manage.py makemigrations --check` | Verifica que no falten migraciones |
| `cd backend && ruff format --check .` | Verifica el formato del backend |
| `cd backend && python manage.py seed_catalogs` | Recarga los catálogos semilla (idempotente) |
| `cd backend && python manage.py seed_reports` | Carga 14 reportes sintéticos del mapa (idempotente; requiere catálogos) |
| `cd backend && python manage.py promote_user <email> --role operator --institution <slug>` | Promueve un usuario existente a operador |
| `cd backend && python manage.py promote_user <email> --role admin` | Promueve un usuario existente a administrador |
| `cd backend && python manage.py resend_notification <uuid>` | Reencola una notificación que quedó en `FALLIDO` y reinicia sus intentos; convive con los reintentos automáticos de fallos transitorios |
| `cd backend && python manage.py load_x_mock_dataset apps/social_ingestion_app/data/x_mock_dataset_v1.json [--dry-run]` | Carga el dataset **ficticio** de X simulado (S13): sin red, Celery ni IA, atómico e idempotente. Cargarlo en producción es una decisión aparte |
| `cd backend && python manage.py ai_usage_summary --input-usd-per-1m <precio> --output-usd-per-1m <precio>` | Resume tokens persistidos y estima costo; solo lectura |
| `cd backend && python manage.py ai_try_classification --description <texto> --category <slug> --prompt both` | Prueba manual v1/v2; llama a OpenAI sin persistir clasificaciones |
| `cd backend && python manage.py ai_compare_prompts --cases <casos.csv> --photos <carpeta> --out <resultados.csv>` | Compara prompts con casos y fotos locales; consume cuota |
| `cd frontend && npm run lint` | Lint del frontend |
| `cd frontend && npm test` | Pruebas unitarias y de componentes del frontend (Vitest + jsdom) |
| `cd frontend && npm run typecheck` | Verificación de tipos (`next typegen && tsc --noEmit`) |
| `cd frontend && npm run format` | Formatea el frontend con Prettier |
| `cd frontend && npm run format:check` | Verifica el formato sin modificar archivos |
| `cd frontend && npm run build` | Build de producción |
| `cd frontend && PLAYWRIGHT_PRODUCTION=1 npm run test:e2e` | E2E del frontend con API interceptada; requiere el build con las variables públicas de la fixture de `ci-frontend.yml` |
| `cd frontend && npm run test:e2e:fullstack` | E2E local contra backend real con Storage falso; preparación, variables y límites en [S14_E2E_FULLSTACK.md](docs/s14/S14_E2E_FULLSTACK.md) |

## CI/CD

Los Pull Requests y los pushes a `main` ejecutan GitHub Actions según los filtros
de cada workflow (por evento o por job). Desde el S14 hay **dos workflows de backend**, ambos con PostGIS y Redis:

- `ci-backend.yml`: Ruff (lint y formato), control de migraciones y pruebas del dominio,
  excluyendo `experiments_app`. La compuerta de `backend/coverage-gate.toml` exige **≥ 80 %**
  sobre el código de producto de `apps/`, sin el aparato experimental, el arnés de evaluación
  de IA, pruebas ni migraciones. Si no alcanza el umbral, el job falla. Después informa
  **HT5-01** sobre el manifiesto congelado de **33 archivos** de S11A mediante
  `backend/coverage-ht5.toml`, con los mismos datos; los dos alcances no son equivalentes.
- `ci-experiments.yml`: corre `experiments_app` por separado, sin la compuerta de producto;
  excluye los tres launchers EX-10 que exceden las 6 h de Actions. El job `changes`
  siempre reporta; en PR compara base común → head con `git diff` (incluye borrados y
  ambas rutas de renombres, sin límite de archivos). Si ninguna ruta aplica,
  `experiments` queda **skipped**, sin omitir el workflow y dejar el check pendiente.
  En **todo push a `main`** y en ejecución **manual** corre siempre como red de seguridad.
  No hay schedule; si se agrega, también correrá siempre. `ci-backend.yml` excluye esta
  suite con `--ignore=apps/experiments_app` y no depende de este job.

El filtro de PR de experiments es deliberadamente conservador; ante nuevas dependencias,
ampliarlo antes de reducir su alcance. Las rutas son:

| Rutas | Dependencia o criterio |
|---|---|
| `backend/**` | `exporter`, `provenance` y `ex10_state` → `admin_app`, `auth_app`, `reports_app`, `rewards_app`, `notifications_app`, `ai_client_app`; `local_storage` → `core.services.storage`; `ex10_executor` → `map_app.cache`. Los servicios importados enlazan entre sí; settings/URLs cargan también `analytics_app` y `social_ingestion_app`. Se incluyen todas las apps y sus migraciones porque pytest-django prepara la base completa, además de fixtures y datos. |
| Incluidos en `backend/**`: `config/**`, `requirements/**`, `conftest.py`, `pyproject.toml`, `coverage*.toml`, Dockerfiles, `manage.py`, `scripts/**` | Settings de test → base, URLs y Celery; `requirements/test.txt` → `base.txt` (no hay lockfile Python actual); pytest y cobertura; scripts S11 ejercitados por la suite. El job instala directamente Python/GDAL y requirements, sin construir Docker, pero se conserva la cobertura del entorno congelado S11. |
| `infra/**`, `.env.example`, `.gitignore`, `docs/tesis/S11A_POLITICA_CUSTODIA_Y_ACCESO.md` | Overlays, aislamiento, procedencia y política de custodia de evidencia S11/GOV-T054, conservados del filtro previo. |
| `.github/workflows/ci-experiments.yml` | Cambios del propio comando, servicios o filtro de CI. |
| En raíz: `conftest.py`, `pytest.ini`, `pyproject.toml`, `setup.cfg`, `tox.ini`, `.coveragerc`, `coverage*.toml`, `requirements*.txt`, `requirements/**`, `uv.lock`, `poetry.lock`, `Pipfile*` | Configuración compartida de Python, pytest, cobertura y dependencias, también si se incorpora en el futuro. |

Los PR que solo cambian frontend o documentación ajena a esa política omiten la suite larga;
no se alteran las pruebas, sus exclusiones ni la validación de evidencia S11.

`ci-frontend.yml` conserva Prettier, Vitest, ESLint, tipos y build; su job `frontend-e2e`
ejecuta todas las specs de `e2e/` contra el build de producción con una API interceptada.
La corrida local full-stack del S14 usa backend real y Storage falso, y se documenta aparte
en [S14_E2E_FULLSTACK.md](docs/s14/S14_E2E_FULLSTACK.md). El informe y los resultados están en
[S14_INFORME_PRUEBAS.md](docs/s14/S14_INFORME_PRUEBAS.md).
El candidato `f9a9da0` del PR #54 cerró con **6/6 checks verdes**: `test` (dominio y compuerta)
en unos 3 min, `experiments` en unos 47 min, y `build` y `frontend-e2e` también verdes.

**`main` no tiene protección de rama configurada** (verificado el 20/10/2026, S14-D20).
Esperar los checks aplicables en verde y revisar el PR antes de integrar es el procedimiento
del proyecto; GitHub no lo impone mediante una regla de protección.

El frontend se despliega automáticamente en **Vercel** desde `main`. El backend y los procesos de
Celery se publican por separado en **Heroku** mediante un push explícito a su remote Git; un merge
en GitHub no despliega Heroku. Los procesos backend acceden a PostgreSQL en **Supabase** mediante
`DATABASE_URL`; web y worker de IA acceden a Storage con `SUPABASE_URL` y las credenciales privadas
correspondientes. La configuración de los proveedores se administra desde sus paneles y no se
versiona en el repositorio.

### Entornos desplegados

| Servicio | Proveedor | URL pública | Notas |
|---|---|---|---|
| Frontend | Vercel | `https://ciudadania-activa.vercel.app` | `Root Directory: frontend`; solo las dos variables públicas que el código lee, `NEXT_PUBLIC_API_URL` y `NEXT_PUBLIC_GOOGLE_CLIENT_ID`, ambas de tipo *Config*. **Ningún secreto de backend se carga en Vercel** (verificado en la remediación S9.5, ver [runbook](docs/runbooks/HEROKU_MIGRATION.md)) |
| Backend | Heroku | `https://ciudadania-activa-api-5b4f8e81977b.herokuapp.com` | App `ciudadania-activa-api`, stack `container`; proceso `web`: `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT`. **Sin fase `release`: ningún deploy migra la base** |
| Worker de Celery | Heroku | — | Proceso `worker` de la misma app: `celery -A config.celery:app worker --loglevel=info --concurrency=2` |
| Celery Beat | Heroku | — | Proceso `beat` activo con exactamente **1 dyno**; agenda `reports.auto_close` (cada 15 min), `notifications.dispatch_due` (cada 5 min) y `rewards.notify_available_rewards` (09:00 locales) |
| Redis | Heroku Key-Value Store | — | Plan `mini`. Para `rediss://` sin `ssl_cert_reqs`, settings añade `REDIS_SSL_CERT_REQS` (default `none`, PR #46) |
| Base de datos y storage | Supabase | — | PostgreSQL 17 + PostGIS en el esquema `gis` |

Estado del backend: `GET /api/v1/health/` → `{"status":"ok","db":true,"redis":true}`. El backend
restringe `CORS_ALLOWED_ORIGINS` al dominio del frontend.

Estado operativo verificado el **20/10/2026**, tras el despliegue de **Heroku v26**
(`Deploy a7f775d9`, 14:58:43 −03): `web=1`, `worker=1` y `beat=1`; health OK con
base de datos y Redis, `migrate --plan` vacío y `check` limpio. La reconciliación
`a7f775d` despliega el árbol `51a9352`, igual a `origin/main:backend`. S14 no cambió
el esquema: no requirió migraciones, mantenimiento ni `pg_dump`.
El smoke confirmó 200 en categorías públicas, ranking mensual y métricas públicas,
con `Cache-Control: public, max-age=60`, y en `/transparencia`, `/mapa` y `/ranking`.
Los SHAs y la verificación están en el [registro del despliegue, §15](docs/runbooks/HEROKU_MIGRATION.md#15-operación-vigente--corte-20102026-0125-utc).

- IA activa: `AI_CLASSIFICATION_ENABLED=true`, `AI_PROVIDER=openai`,
  `OPENAI_MODEL=gpt-6-luna`, `OPENAI_REASONING_EFFORT=low`, `AI_PROMPT_VERSION=v2`.
  El default de código sigue en `v1`; una versión desconocida con OpenAI falla cerrado.
- Correo configurado para identidad y canje; S13 agregó las notificaciones de reportes,
  recompensa disponible y reintentos automáticos, desplegados en v25 y conservados en v26
  (ver [API §10.5](docs/API.md#105-notificaciones-del-sprint-13)).
  El remitente usa Gmail verificado en Brevo: falta dominio propio autenticado por
  el riesgo de spam (advertencias de DKIM/DMARC).
- Corte productivo previo (20/10/2026, 01:25 UTC): 16 reportes, 0 clasificaciones IA, 0 recompensas, 0 canjes y dos
  `VERIFICACION_APROBADA` en `ENVIADO` (del 23/08 y 29/08, previas a Heroku). Habilitar IA no demuestra su calidad con reportes reales.
- HSTS sigue como deuda. Redis con `CERT_NONE` es un **riesgo aceptado** en S14-D18:
  Heroku Key-Value Store usa un certificado autofirmado sin CA verificable en esta
  configuración, por lo que el tráfico va cifrado pero no se verifica la identidad del
  servidor. #46 conserva el parámetro tras rotación, sin resolver ese riesgo.
- S14 fija `REST_FRAMEWORK["NUM_PROXIES"] = 1` para los throttles por IP del cliente,
  detrás de un proxy: navegador → router de Heroku → dyno. Sin `X-Forwarded-For`, local
  y CI usan `REMOTE_ADDR`. Agregar otro proxy exige revisar el ajuste. El cambio se
  integró con PR #54 y está desplegado y verificado en Heroku v26.
- Si cae Redis, mapa, ranking y métricas mantienen sus siete lecturas mediante el
  throttle fail-open del S14: se pierde temporalmente el contador y se registra solo el
  scope. Las escrituras siguen fallando cerrado; el detalle está en [API §10](docs/API.md#10-métricas-y-transparencia-sprint-13).

### Migraciones estructurales: orden obligatorio

Railway ejecutaba `python manage.py migrate --noinput` en su pre-deploy, así que un push a `main`
aplicaba migraciones a producción de inmediato. **Heroku no reproduce ese comportamiento**:
`backend/heroku.yml` no define fase `release`, y esa ausencia es deliberada. Migrar es ahora un
acto explícito:

1. trabajar en rama o *pull request* y esperar **CI en verde**;
2. generar el **`pg_dump` productivo previo** (ver [Respaldos de la base de datos](#respaldos-de-la-base-de-datos));
3. hacer *merge*/push a `main`; esto despliega Vercel, pero **no** Heroku;
4. activar mantenimiento y detener todos los writers (`web=0`, `worker=0`, `beat=0`);
5. desplegar el backend con el artefacto autorizado mediante push al remote de Heroku. En esta
   infraestructura, ese push inicia inmediatamente el build y el deployment;
   usar el commit de reconciliación del subtree descrito en el runbook, sin forzar el remote;
6. comprobar que el deploy **no** alteró el esquema (`showmigrations`);
7. aplicar la migración a mano, y solo entonces:
   `heroku run --app ciudadania-activa-api python manage.py migrate --noinput`;
8. **verificar producción** con consultas de solo lectura (`showmigrations`, esquema, catálogos);
9. restaurar primero `web=1`, después `worker=1` y finalmente un único `beat=1`, verificando cada
   proceso antes de retirar el mantenimiento;
10. generar un respaldo posterior si el cambio es estructuralmente relevante.

El respaldo deja de ser una carrera contra el pre-deploy, pero sigue siendo obligatorio: el paso 7
es irreversible. El procedimiento completo está en
[docs/runbooks/HEROKU_MIGRATION.md](docs/runbooks/HEROKU_MIGRATION.md).

Dos detalles de configuración que no son opcionales y conviene no "simplificar":

- `SECURE_REDIRECT_EXEMPT = [r"^api/v1/health/$"]` en `config/settings/prod.py`. Se introdujo para
  el healthcheck de Railway, que llegaba por HTTP plano y sin `X-Forwarded-Proto`, y sigue siendo
  necesario para sondear `/api/v1/health/` sin recibir un `301`. HTTPS sigue forzado para el resto.
- `--concurrency=2` en el worker. Sin el parámetro, Celery detecta todas las CPU del host y levanta
  decenas de procesos innecesarios.

## Validación S15

La implementación en revisión de S15 incorpora un dataset sintético restaurable,
herramientas de evaluación IA offline, carga k6 conciliada con SQL y recorrido de
navegador con backend real local. `ci-s15-tools.yml` ejecuta los contratos offline
cuando cambian herramientas o backend; no consume API de pago ni requiere datos reales.

El [informe vigente](docs/s15/S15_INFORME_VALIDACION.md) y el
[índice de evidencia](docs/s15/S15_INDICE_VALIDACION.md) distinguen pruebas locales,
sustitutos y diagnósticos remotos. Las fotos inéditas/oro humano y las cuentas reales
siguen pendientes; S15 permanece abierto. El [README de herramientas](tools/s15/README.md)
detalla comandos locales. Los dumps, sesiones, imágenes y trazas se conservan fuera de Git.

## Respaldos de la base de datos

El plan Free de Supabase **no ofrece respaldos programados** para este proyecto, así que el respaldo
es **manual y obligatorio**: `pg_dump` periódico y siempre antes de una migración destructiva o
importante. Los volcados van **fuera del repositorio** y **nunca se versionan** (`.gitignore` ya
excluye `*.dump`, `*.sql` y `*.pgdump`).

```bash
# Cliente del major 17. Si la máquina no lo tiene, se usa un contenedor en lugar de instalarlo.
pg_dump --format=custom --no-owner --no-acl \
  --file ~/backups/ciudadania-activa/ciudadania_activa_$(date +%Y%m%d_%H%M%S).dump "$DATABASE_URL"

pg_restore --list <archivo.dump>   # validación: debe leerse sin errores
```

Para evitar validar un archivo todavía abierto por `pg_dump`, los respaldos operativos se escriben
primero con sufijo `.partial`. Se espera al proceso real, se exige `exit=0` y `size > 0`, se ejecuta
`pg_restore --list` y sólo entonces se renombra el archivo final. Durante S10 un archivo fue
observado con 0 bytes mientras `pg_dump` seguía activo; no fue corrupción de PostgreSQL, sino una
validación prematura que este procedimiento corrige.

Al restaurar en una instancia PostgreSQL estándar, la extensión `supabase_vault` no existe y sus
objetos fallan: es esperado y no afecta a los datos de la aplicación (`--exclude-schema=vault` lo
evita). Para una restauración fiel a producción, el destino debe usar una versión de PostGIS
compatible con **3.3.7**. El procedimiento completo y sus límites están en
[ADR-0003](docs/adr/ADR-0003-postgis-y-alojamiento-en-supabase.md).

## Convenciones

- Una rama por funcionalidad (`feat/s06-creacion-reportes`, `fix/…`, `docs/…`) con Pull Request,
  revisión y checks aplicables en verde antes de integrar. `main` no tiene protección de rama
  configurada (S14-D20); esta convención es un procedimiento del proyecto.
- Commits con la convención `tipo(alcance): descripción` (p. ej. `feat(rewards): acreditar puntos
  pendientes al validar`).
- Las vistas no contienen reglas de negocio: toda escritura de dominio pasa por `services/` dentro
  de cada app Django.
