# Ciudadanía Activa

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

Completar los valores de terceros (Google OAuth, Gemini, Supabase, email) a medida que los
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
volume rm infra_postgres_data`). En local esto no destruye información: los datos se regeneran con
`migrate` y las cargas de prueba del sprint correspondiente.

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
cp ../.env.example .env.local   # o crear .env.local con NEXT_PUBLIC_API_URL
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
| `SUPABASE_URL` | **Sí** | No | No | No |
| `SUPABASE_SERVICE_ROLE_KEY` | **Sí** | No | No | No |
| `SUPABASE_STORAGE_BUCKET_PRIVATE` | **Sí** | No | No | No |
| `BREVO_API_KEY` · `BREVO_API_URL` · `DEFAULT_FROM_EMAIL` | No | **Sí** | No | No |
| `DATABASE_URL` · `REDIS_URL` · `DJANGO_SECRET_KEY` | **Sí** | **Sí** | **Sí** | No |
| `REPORT_AUTO_CLOSE_HOURS` | No | **Opcional** | **Opcional** | No |
| `PUBLIC_MAP_CACHE_TTL_SECONDS` | **Sí** | No | No | No |
| `NEXT_PUBLIC_API_URL` · `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | No | No | No | **Sí** (públicas) |

El backend web es el único que crea verificaciones y firma imágenes, así que es el único que
necesita la clave del HMAC y la credencial de Storage. El worker es el único que envía correo,
así que es el único que necesita la clave de Brevo. **Ninguna variable de Supabase llega al
frontend**: el navegador nunca habla con el almacenamiento.

Sin estas variables la aplicación **arranca igual**. `config/settings/prod.py` no las exige:
backend web y worker comparten ajustes, y obligar a que estén presentes forzaría a copiar una
credencial con privilegios totales sobre el almacenamiento a un servicio que no la usa. El
comportamiento es fallo cerrado **local**: el endpoint que las necesita responde `503` genérico
y el resto del sistema sigue funcionando.

El scheduler se despliega como un proceso único separado del worker, con el comando
`celery -A config.celery:app beat --loglevel=info`. Beat agenda `reports.auto_close`, pero es el
worker quien ejecuta la tarea y consulta `REPORT_AUTO_CLOSE_HOURS`; si se sobrescribe el valor por
defecto, debe configurarse de forma consistente al menos en todos los workers que consuman esa
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
en el backend web.

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
- **No hay reintentos automáticos.** Un fallo deja la notificación en `FALLIDO` y ahí se queda:
  reencolar la tarea sobre una notificación fallida **no la reenvía**. Los reintentos son
  alcance de los Sprints 12-13.

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

| Comando | Descripción |
|---|---|
| `cd backend && pytest --cov=apps` | Pruebas del backend con cobertura |
| `cd backend && ruff check .` | Lint del backend |
| `cd backend && python manage.py makemigrations --check` | Verifica que no falten migraciones |
| `cd backend && ruff format --check .` | Verifica el formato del backend |
| `cd backend && python manage.py seed_catalogs` | Recarga los catálogos semilla (idempotente) |
| `cd backend && python manage.py seed_reports` | Carga 14 reportes sintéticos del mapa (idempotente; requiere catálogos) |
| `cd backend && python manage.py promote_user <email> --role operator --institution <slug>` | Promueve un usuario existente a operador |
| `cd backend && python manage.py promote_user <email> --role admin` | Promueve un usuario existente a administrador |
| `cd backend && python manage.py resend_notification <uuid>` | Reencola una notificación que quedó en `FALLIDO` |
| `cd frontend && npm run lint` | Lint del frontend |
| `cd frontend && npm test` | Pruebas unitarias y de componentes del frontend (Vitest + jsdom) |
| `cd frontend && npm run typecheck` | Verificación de tipos (`next typegen && tsc --noEmit`) |
| `cd frontend && npm run format` | Formatea el frontend con Prettier |
| `cd frontend && npm run build` | Build de producción |

## CI/CD

Cada Pull Request ejecuta `ci-backend.yml` (Ruff + `pytest` con servicios PostGIS y Redis) y
`ci-frontend.yml` (Prettier + Vitest + ESLint + `tsc --noEmit` + `next build`) mediante GitHub Actions. La
rama `main` está protegida: solo se integra con ambos checks en verde.

El frontend se despliega automáticamente en **Vercel** desde `main`. El backend y los procesos de
Celery se publican por separado en **Heroku** mediante un push explícito a su remote Git; un merge
en GitHub no despliega Heroku. Los procesos backend acceden a PostgreSQL en **Supabase** mediante
`DATABASE_URL`; sólo el backend web accede a Storage con `SUPABASE_URL` y las credenciales privadas
correspondientes. La configuración de los proveedores se administra desde sus paneles y no se
versiona en el repositorio.

### Entornos desplegados

| Servicio | Proveedor | URL pública | Notas |
|---|---|---|---|
| Frontend | Vercel | `https://ciudadania-activa.vercel.app` | `Root Directory: frontend`; sólo necesita las variables públicas `NEXT_PUBLIC_*`. El proyecto conserva 28 variables de backend heredadas que no llegan al navegador, pero amplían el radio de exposición y deben retirarse en una operación de seguridad separada |
| Backend | Heroku | `https://ciudadania-activa-api-5b4f8e81977b.herokuapp.com` | App `ciudadania-activa-api`, stack `container`; proceso `web`: `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT`. **Sin fase `release`: ningún deploy migra la base** |
| Worker de Celery | Heroku | — | Proceso `worker` de la misma app: `celery -A config.celery:app worker --loglevel=info --concurrency=2` |
| Celery Beat | Heroku | — | Proceso `beat` activo con exactamente **1 dyno**; agenda `reports.auto_close` cada 15 minutos |
| Redis | Heroku Key-Value Store | — | Plan `mini`. Entrega `rediss://`, que exige `?ssl_cert_reqs=none` en `REDIS_URL` |
| Base de datos y storage | Supabase | — | PostgreSQL 17 + PostGIS en el esquema `gis` |

Estado del backend: `GET /api/v1/health/` → `{"status":"ok","db":true,"redis":true}`. El backend
restringe `CORS_ALLOWED_ORIGINS` al dominio del frontend.

Estado operativo tras el cierre de S10: Heroku `v14`, `web=1`, `worker=1`, `beat=1` y
`REPORT_AUTO_CLOSE_HOURS=72`. Pendientes conocidos que no deben confundirse con funcionalidad
operativa completa:

- `BREVO_API_KEY` está ausente; el correo transaccional no está operativo. Las entregas fallan de
  forma controlada y consultable, sin impedir el arranque del worker.
- HSTS permanece pendiente.
- Redis usa TLS con `CERT_NONE` (`ssl_cert_reqs=none`), deuda de seguridad conocida.
- La clasificación por IA está intencionalmente deshabilitada mediante
  `AI_CLASSIFICATION_ENABLED=false`.

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

- Rama `main` protegida; una rama por funcionalidad (`feat/s06-creacion-reportes`, `fix/…`,
  `docs/…`) con Pull Request y revisión antes de integrar.
- Commits con la convención `tipo(alcance): descripción` (p. ej. `feat(rewards): acreditar puntos
  pendientes al validar`).
- Las vistas no contienen reglas de negocio: toda escritura de dominio pasa por `services/` dentro
  de cada app Django.
