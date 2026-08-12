# Ciudadanía Activa

Plataforma de reportes urbanos ciudadanos con verificación de identidad, validación institucional
e incentivos (puntos de impacto y créditos canjeables). Trabajo de Conclusión de Carrera —
Universidad Columbia del Paraguay.

Documentación completa del proyecto en [docs/PLAN_DESARROLLO.md](docs/PLAN_DESARROLLO.md),
avance por sprint en [docs/PROGRESO_SPRINTS.md](docs/PROGRESO_SPRINTS.md), backlog priorizado en
[docs/BACKLOG.md](docs/BACKLOG.md) y decisiones de arquitectura en [docs/adr/](docs/adr/).

## Stack

| Capa | Tecnología |
|---|---|
| Frontend | Next.js (App Router) + TypeScript + TailwindCSS |
| Backend | Python 3.11 + Django + Django REST Framework |
| Base de datos | PostgreSQL 15 + PostGIS, alojada en **Supabase** |
| Almacenamiento | Supabase Storage |
| Caché / broker | Redis 7 |
| Asincronía | Celery + Celery Beat |
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

### 2. Base de datos y Redis (Docker)

```bash
docker compose -f infra/docker-compose.yml up db redis
```

Esto expone PostGIS en `localhost:5432` y Redis en `localhost:6379`, según lo declarado en `.env`.

> Alternativa: `docker compose -f infra/docker-compose.yml up` levanta además los contenedores
> `backend` y `worker`, útil para reproducir el stack completo sin instalar Python localmente.

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

### 4. Frontend (Next.js)

```bash
cd frontend
cp ../.env.example .env.local   # o crear .env.local con NEXT_PUBLIC_API_URL
npm install
npm run dev
```

La aplicación queda disponible en `http://localhost:3000` y consulta el estado del backend en la
página de inicio.

## Comandos habituales

| Comando | Descripción |
|---|---|
| `cd backend && pytest --cov=apps` | Pruebas del backend con cobertura |
| `cd backend && ruff check .` | Lint del backend |
| `cd backend && python manage.py makemigrations --check` | Verifica que no falten migraciones |
| `cd frontend && npm run lint` | Lint del frontend |
| `cd frontend && npm run typecheck` | Verificación de tipos (`tsc --noEmit`) |
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

## Convenciones

- Rama `main` protegida; una rama por funcionalidad (`feat/s06-creacion-reportes`, `fix/…`,
  `docs/…`) con Pull Request y revisión antes de integrar.
- Commits con la convención `tipo(alcance): descripción` (p. ej. `feat(rewards): acreditar puntos
  pendientes al validar`).
- Las vistas no contienen reglas de negocio: toda escritura de dominio pasa por `services/` dentro
  de cada app Django.
