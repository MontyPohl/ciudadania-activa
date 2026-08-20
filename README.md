# Ciudadanía Activa

Plataforma de reportes urbanos ciudadanos con verificación de identidad, validación institucional
e incentivos (puntos de impacto y créditos canjeables). Trabajo de Conclusión de Carrera —
Universidad Columbia del Paraguay.

Documentación completa del proyecto en [docs/PLAN_DESARROLLO.md](docs/PLAN_DESARROLLO.md),
avance por sprint en [docs/PROGRESO_SPRINTS.md](docs/PROGRESO_SPRINTS.md), backlog priorizado en
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
| Asincronía | Celery (worker desplegado; Beat previsto para las tareas periódicas del Sprint 10) |
| Hosting | Vercel (frontend) · Railway (backend + workers) · Supabase (DB + storage) |
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
- Cuenta de Supabase (base de datos + storage) y de Railway/Vercel para despliegue

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
> `backend` y `worker`, útil para reproducir el stack completo sin instalar Python localmente.

El contenedor `db` usa `postgis/postgis:17-3.5`, alineado con el major de PostgreSQL que entrega
Supabase. **Cambiar el major de PostgreSQL invalida el volumen `infra_postgres_data`**: hay que
eliminarlo y dejar que se reinicialice (`docker compose -f infra/docker-compose.yml stop db && docker
volume rm infra_postgres_data`). En local esto no destruye información: los datos se regeneran con
`migrate` y las cargas de prueba del sprint correspondiente.

> **Aislamiento deliberado:** `infra/docker-compose.yml` fija `DATABASE_URL` al contenedor local en
> el bloque `environment` de `backend` y `worker`, que tiene precedencia sobre `env_file`. Por eso
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

### 4. Frontend (Next.js)

```bash
cd frontend
cp ../.env.example .env.local   # o crear .env.local con NEXT_PUBLIC_API_URL
npm install
npm run dev
```

La aplicación queda disponible en `http://localhost:3000` y consulta el estado del backend en la
página de inicio.

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

## Comandos habituales

| Comando | Descripción |
|---|---|
| `cd backend && pytest --cov=apps` | Pruebas del backend con cobertura |
| `cd backend && ruff check .` | Lint del backend |
| `cd backend && python manage.py makemigrations --check` | Verifica que no falten migraciones |
| `cd backend && ruff format --check .` | Verifica el formato del backend |
| `cd backend && python manage.py seed_catalogs` | Recarga los catálogos semilla (idempotente) |
| `cd backend && python manage.py promote_user <email> --role operator --institution <slug>` | Promueve un usuario existente a operador |
| `cd backend && python manage.py promote_user <email> --role admin` | Promueve un usuario existente a administrador |
| `cd frontend && npm run lint` | Lint del frontend |
| `cd frontend && npm run typecheck` | Verificación de tipos (`next typegen && tsc --noEmit`) |
| `cd frontend && npm run format` | Formatea el frontend con Prettier |
| `cd frontend && npm run build` | Build de producción |

## CI/CD

Cada Pull Request ejecuta `ci-backend.yml` (Ruff + `pytest` con servicios PostGIS y Redis) y
`ci-frontend.yml` (Prettier + ESLint + `tsc --noEmit` + `next build`) mediante GitHub Actions. La
rama `main` está protegida: solo se integra con ambos checks en verde.

El despliegue continuo parte de `main`: el frontend se despliega en **Vercel** y el backend junto
con los workers de Celery en **Railway**, conectados a la base de datos y el storage de
**Supabase** mediante `DATABASE_URL`. La configuración de estos proveedores (creación de
proyectos, variables de entorno de producción y conexión del repositorio) se realiza desde sus
paneles y no se automatiza desde este repositorio.

### Entornos desplegados

| Servicio | Proveedor | URL pública | Notas |
|---|---|---|---|
| Frontend | Vercel | `https://ciudadania-activa.vercel.app` | `Root Directory: frontend`; solo la variable pública `NEXT_PUBLIC_API_URL`. **Ningún secreto de backend se carga en Vercel** |
| Backend | Railway | `https://ciudadania-activa-production.up.railway.app` | `Root Directory: /backend`; Gunicorn en `0.0.0.0:8000`; pre-deploy `python manage.py migrate --noinput` |
| Worker de Celery | Railway | — | Servicio separado, sin dominio público ni healthcheck HTTP: `celery -A config.celery:app worker --loglevel=info --concurrency=2` |
| Base de datos y storage | Supabase | — | PostgreSQL 17 + PostGIS en el esquema `gis` |

Estado del backend: `GET /api/v1/health/` → `{"status":"ok","db":true,"redis":true}`. El backend
restringe `CORS_ALLOWED_ORIGINS` al dominio del frontend.

### Migraciones estructurales: orden obligatorio

El pre-deploy del backend ejecuta `python manage.py migrate --noinput`, de modo que **un push a
`main` con migraciones nuevas las aplica a producción de inmediato**, antes incluso de que CI
termine. Por eso el respaldo previo que exige `ADR-0003` debe generarse **antes** del push, no
después de ver CI en verde:

1. trabajar en rama o *pull request* y esperar **CI en verde**;
2. generar el **`pg_dump` productivo previo** (ver [Respaldos de la base de datos](#respaldos-de-la-base-de-datos));
3. hacer *merge*/push a `main`;
4. Railway aplica las migraciones en su pre-deploy;
5. **verificar producción** con consultas de solo lectura (`showmigrations`, esquema, catálogos);
6. generar un respaldo posterior si el cambio es estructuralmente relevante.

Nunca se ejecuta `migrate` a mano contra Supabase para adelantarse al pre-deploy.

Dos detalles de configuración que no son opcionales y conviene no "simplificar":

- `SECURE_REDIRECT_EXEMPT = [r"^api/v1/health/$"]` en `config/settings/prod.py`. Railway consulta el
  healthcheck por HTTP plano y sin `X-Forwarded-Proto`; sin esta exención, `SECURE_SSL_REDIRECT`
  devuelve `301` y el deploy falla. HTTPS sigue forzado para el resto de las rutas.
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
