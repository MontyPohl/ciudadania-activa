# Progreso de Sprints — Ciudadanía Activa

Registro de avance del prototipo. Se actualiza **al cerrar cada sprint**, antes de iniciar el siguiente.

- **Estado general:** Sprint 1 en curso (implementación local completa y backend verificado contra la base real de Supabase; despliegue en Vercel/Railway pendiente de acción manual). Sin decisiones de arquitectura pendientes.
- **Plan de referencia:** [PLAN_DESARROLLO.md](PLAN_DESARROLLO.md)
- **Ventana de desarrollo:** 01/08/2026 → 15/11/2026 (16 sprints semanales, Anexo F revisado)
- **Estados posibles:** `Pendiente` · `En curso` · `Completado` · `Completado con observaciones` · `Bloqueado`

---

## Resumen general

| Sprint | Fechas | Objetivo | Estado |
|---|---|---|---|
| 1 | 01/08 – 07/08 | Entorno, repositorio y CI/CD | Completado con observaciones |
| 2 | 08/08 – 14/08 | Modelo de datos e incentivos | Pendiente |
| 3 | 15/08 – 21/08 | Autenticación, roles y permisos | Pendiente |
| 4 | 22/08 – 28/08 | Verificación de identidad y privacidad | Pendiente |
| 5 | 29/08 – 04/09 | Mapa público y geolocalización | Pendiente |
| 6 | 05/09 – 11/09 | Creación de reportes y duplicados | Pendiente |
| 7 | 12/09 – 18/09 | Panel ciudadano y confirmaciones | Pendiente |
| 8 | 19/09 – 25/09 | Clasificación asistida por IA | Pendiente |
| 9 | 26/09 – 02/10 | Panel institucional y validación | Pendiente |
| 10 | 03/10 – 09/10 | Estados, evidencias, auditoría y reversión | Pendiente |
| 11 | 10/10 – 16/10 | Créditos, ranking del mes en curso y privacidad | Pendiente |
| 12 | 17/10 – 23/10 | Catálogo de recompensas y canje simulado | Pendiente |
| 13 | 24/10 – 30/10 | Dashboards, integración e ingesta social | Pendiente |
| 14 | 31/10 – 06/11 | Pruebas unitarias, integración, seguridad y E2E | Pendiente |
| 15 | 07/11 – 13/11 | Carga, dataset definitivo y documentación | Pendiente |
| 16 | 14/11 – 15/11 | Cierre y preparación de la defensa | Pendiente |

**Progreso:** 0 / 16 sprints completados (Sprint 1 con la implementación local y CI terminadas; pendiente de completar el despliegue en Vercel/Railway/Supabase para cerrarlo formalmente).

### Decisiones que condicionan el avance

| ID | Decisión | Afecta a | Estado |
|---|---|---|---|
| D-01 | Reversión de una validación cuyos créditos ya fueron canjeados | Sprint 10 · Sprint 12 | **Resuelta** — la reversión siempre se ejecuta; el saldo es la diferencia entre créditos válidos y créditos consumidos y puede resultar negativa, sin penalización adicional; el vale emitido permanece emitido; no se permite otro canje hasta que la diferencia alcance de nuevo el saldo requerido |
| D-02 | ¿Puede el operador fijar un `credit_cost` distinto de 1.000? | Sprint 12 | **Resuelta** — no. El costo es fijo en 1.000 créditos en el MVP; `credit_cost` se conserva con `DEFAULT 1000` por extensibilidad futura, pero no es editable por el operador |
| D-03 | ¿Dónde reside la instancia de PostgreSQL/PostGIS? | Sprint 1 · Sprint 15 | **Resuelta** — en **Supabase**, junto con Storage. El backend Django y los workers Celery se despliegan en Railway y se conectan mediante `DATABASE_URL`. Fundamento: coherencia con la arquitectura de la tesis, soporte de PostGIS, simplificación operativa, centralización de base y almacenamiento, y facilidad de inspección y documentación durante el TCC. La latencia Railway ↔ Supabase se mide en el Sprint 15 y no altera la arquitectura. Si el plan contratado no incluye respaldos automáticos, son obligatorios los respaldos lógicos periódicos |

**No quedan decisiones pendientes.** La planificación está **aprobada** y habilitada para iniciar el Sprint 1. Detalle en la sección 33.2 de [PLAN_DESARROLLO.md](PLAN_DESARROLLO.md).

### Correcciones de diseño incorporadas al plan

| # | Corrección | Afecta a |
|---|---|---|
| C-01 | Nuevo estado `ANULADO` para transacciones que estuvieron pendientes y nunca se acreditaron por rechazo del reporte. `REVERTIDO` queda reservado para las que sí estuvieron acreditadas y luego fueron revertidas | S2 · S9 · S11 · S14 |
| C-02 | Se elimina el `CHECK` de autoconfirmación en base de datos. Se conserva `UNIQUE(report_id, citizen_id)`; la regla de autoconfirmación vive en el servicio de dominio | S2 · S7 |
| C-03 | El MVP publica solo el Top 10 del mes en curso y el impacto histórico. Se eliminan selector de mes, histórico de rankings anteriores y la conservación de períodos cerrados | S11 |
| C-04 | El canje se serializa con `select_for_update()` sobre `User` dentro de `transaction.atomic()`, recalculando el saldo institucional dentro del bloqueo. No existen “filas de saldo” que bloquear | S12 |
| C-05 | Escenarios de prueba S-11 (anulación por rechazo) y S-12 (reversión posterior a un canje con saldo negativo) incorporados a la suite de aceptación | S9 · S10 · S12 · S14 · S15 |
| C-06 | `AUTH_USER_MODEL = "auth_app.User"` y esqueleto mínimo del modelo definidos **antes** de la primera migración; el resto de campos se agrega de forma incremental. No se migra con el usuario estándar de Django | S1 · S2 |
| C-07 | Valores económicos del ledger inmutables: `REPORT_CREATED` nace con 20/20 y `CONFIRMATION_CREATED` con 10/10, ambos `PENDIENTE`. Mientras estén pendientes no participan de ningún saldo. Al validar solo cambian estado, institución y marcas temporales | S2 · S6 · S7 · S9 |
| C-08 | El saldo institucional se define como la **diferencia entre créditos válidos y créditos consumidos**, no como el importe canjeado. El criterio funcional de reversión distingue el caso que vuelve a cero del que queda negativo | S10 · S11 · S12 |
| C-09 | `voided_at` incorporado junto a `credited_at` y `reversed_at` en la migración inicial de `reward_transactions` y en el diccionario de datos | S2 |
| C-10 | PostgreSQL/PostGIS alojado en **Supabase** de forma consistente en arquitectura, despliegue, Sprint 1, riesgos y `ADR-0003`. Backend y workers en Railway, conectados por `DATABASE_URL` | S1 · S15 |
| C-11 | Política de **respaldos lógicos** (`pg_dump` semanal y antes de cada migración destructiva) obligatoria si el plan de Supabase no incluye respaldos automáticos; volcados fuera del repositorio | S1 en adelante |
| C-12 | Medición explícita de la latencia Railway ↔ Supabase incorporada a las pruebas de carga, aislando tiempo de red del tiempo de aplicación | S15 |

---

## Sprint 1 — Entorno, repositorio y CI/CD

**Fechas planificadas:** 01/08/2026 – 07/08/2026
**Fecha real de inicio:** 12/08/2026
**Estado:** Completado con observaciones (implementación local y CI completos; despliegue en Vercel/Railway/Supabase pendiente de acción manual del responsable del proyecto)
**Fecha real de cierre:** —  (queda abierto hasta completar el despliegue, ver "Observaciones y desvíos")

### Funcionalidades terminadas
- Monorepo creado con `backend/`, `frontend/`, `infra/`, `docs/adr/` y `.github/workflows/` según §7 del plan.
- Backend Django 5.2 con `config/settings/{base,dev,prod,test}.py`, DRF (paginación, `EXCEPTION_HANDLER` propio, sin autenticación aún), enrutado bajo `/api/v1/`, `config/celery.py` con app Celery + broker/result backend en Redis, `django.contrib.gis` habilitado.
- Las nueve apps de dominio (`auth_app`, `reports_app`, `rewards_app`, `map_app`, `analytics_app`, `notifications_app`, `admin_app`, `ai_client_app`, `social_ingestion_app`) más `core` creadas y registradas en `INSTALLED_APPS`, sin modelos de dominio todavía (fuera de alcance del sprint).
- `AUTH_USER_MODEL = "auth_app.User"` fijado desde el primer commit; esqueleto mínimo de `User` (`AbstractBaseUser` + `PermissionsMixin`, PK `UUID`, `email` único como `USERNAME_FIELD`, `full_name`, `is_active`, `is_staff`, `date_joined`) con su `UserManager`. Migración `auth_app.0001_initial` generada y aplicada antes que `admin`.
- Endpoint `GET /api/v1/health/` → `{status, version, db, redis}`, verificando conexión real a PostgreSQL y a Redis.
- Ruff y `pytest`/`pytest-django`/`pytest-cov` configurados en `pyproject.toml`.
- Frontend Next.js 16 (App Router, TypeScript, TailwindCSS v4) con estructura `app/(public|citizen|operator|admin)`, `features/`, `components/ui/`, `lib/api/`, `types/`; cliente HTTP tipado que lee `NEXT_PUBLIC_API_URL`; página de inicio que consulta `/api/v1/health/` y muestra el estado del backend. Paleta de marca y tipografía Poppins aplicadas vía Tailwind (`@theme` en `globals.css`).
- ESLint + Prettier configurados en el frontend (`npm run lint`, `npm run format`, `npm run typecheck`).
- `infra/docker-compose.yml` con los cuatro servicios previstos (`db` PostGIS `17-3.5`, `redis`, `backend`, `worker`), healthchecks y volumen persistente de datos. El major de PostgreSQL local se alineó al 17 de Supabase tras la verificación contra la instancia real (ver más abajo).
- `.github/workflows/ci-backend.yml` (Ruff + `makemigrations --check` + `migrate` + `pytest --cov` con servicios PostGIS y Redis) y `ci-frontend.yml` (Prettier + ESLint + `tsc --noEmit` + `next build`), ambos filtrados por ruta.
- `.env.example` documentando todas las variables previstas del proyecto (Django, base de datos, Redis, CORS, Google OAuth, HMAC de identidad, Supabase Storage, Gemini, email, Telegram, reglas de dominio parametrizables, frontend). `.env` excluido en `.gitignore`.
- `docs/BACKLOG.md`: backlog del Anexo E trasladado a un tablero en el repositorio, con las prioridades Alta/Media/Baja originales y los riesgos del Anexo E.2.
- ADR-0001, ADR-0002, ADR-0003 y ADR-0014 redactados en `docs/adr/`.
- `README.md` con requisitos, instalación local paso a paso, variables de entorno y comandos habituales.

### Pruebas realizadas
- `python manage.py migrate` sobre una base PostGIS vacía (contenedor `infra-db-1`): aplica las 19 migraciones de `contenttypes`/`auth`/`auth_app`/`admin`/`sessions` sin errores, con `auth_app.0001_initial` antes de `admin`.
- `python manage.py makemigrations --check --no-input`: sin cambios pendientes.
- `python manage.py createsuperuser` (no interactivo): crea la cuenta correctamente con el modelo `auth_app.User`.
- `SELECT postgis_version();` sobre la base del contenedor: PostGIS habilitado (3.5.2 tras la alineación al major 17; 3.4.3 en la verificación inicial sobre PostgreSQL 15).
- `pytest --cov=apps`: 5 pruebas verdes (smoke de `/health/`; `create_user`/`create_superuser`; `email` único vía `IntegrityError`; `get_user_model()` devuelve `auth_app.User`), cobertura 91 % sobre el código nuevo.
- `ruff check .`: sin hallazgos.
- `GET http://localhost:8000/api/v1/health/` contra el stack levantado con `docker compose up backend worker`: `{"status":"ok","version":"0.1.0","db":true,"redis":true}`.
- Worker de Celery: arranca y se conecta a `redis://redis:6379/0` sin errores (log `celery@… ready.`).
- Frontend: `next build`, `npm run lint`, `npm run typecheck` y `npm run format:check` en verde. Verificación visual con navegador headless (Playwright) contra `next dev`: la página muestra el título, la paleta de marca y las tres píldoras de estado; sin errores de consola.

#### Verificación del backend contra la instancia real de Supabase (16/08/2026)

Ejecutada antes de iniciar el Sprint 2, para confirmar que lo construido en local funciona sobre la base de datos definitiva. Se hizo con un contenedor efímero, sin desplegar Railway ni Vercel.

- **Conexión.** Session Pooler de Supabase, puerto 5432, con TLS (`sslmode=require`, confirmado con `PQsslInUse`). Se descartó la conexión directa (`db.<ref>.supabase.co`) porque solo resuelve por IPv6 sin el add-on IPv4, y el Transaction Pooler (6543) porque no soporta *prepared statements* y rompería `migrate`.
- **Versiones reales.** PostgreSQL **17.6** y PostGIS **3.3.7**, esta última instalada en el esquema **`gis`**. Registrado en `docs/CAMBIOS_TESIS_PENDIENTES.md` (CT-39, CT-40, CT-41) por ser divergente de la documentación académica, que declara PostgreSQL 15.
- **Hallazgo bloqueante y su corrección.** Con el `search_path` por defecto de Supabase (`"$user", public, extensions`), GeoDjango no resuelve el tipo `geometry` ni las funciones PostGIS (`connection.ops.spatial_version` falla con `ImproperlyConfigured`). Las migraciones del Sprint 1 no se vieron afectadas porque `auth_app.User` no tiene campos geométricos, pero habría bloqueado el Sprint 2. Se corrigió fijando un `search_path` explícito y configurable (`DB_SEARCH_PATH`, por defecto `public,gis,extensions`) en `config/settings/base.py`, verificado en ambos entornos.
- **Migraciones.** Las 19 migraciones (`contenttypes`, `auth`, `auth_app.0001_initial`, `admin`, `sessions`) aplicadas sobre Supabase sin errores. `makemigrations --check --no-input` sin cambios pendientes y `manage.py check` sin hallazgos, contra ambas bases.
- **Comprobaciones geoespaciales.** Sobre Supabase: `to_regtype('geometry')`, `spatial_version = (3, 3, 7)`, creación de una columna `geometry(POINT,4326)` con `ST_DistanceSphere` y `ST_DWithin` (base de la regla R-04 de duplicados), todo en transacciones revertidas para no dejar datos. Las 19 funciones espaciales previstas para el MVP se verificaron presentes en PostGIS 3.3.7.
- **Health check y modelo de usuario.** `GET /api/v1/health/` → `{"status":"ok","db":true,"redis":true}` con Django conectado a Supabase. `AUTH_USER_MODEL = "auth_app.User"` verificado contra la tabla `users` materializada en Supabase (alta, `check_password`, lectura por email, todo revertido). **No se creó ningún superusuario ni dato permanente en Supabase.**
- **Alineación de entornos.** El PostgreSQL local pasó de 15 a 17 (`postgis/postgis:17-3.5`) y el servicio `db` del CI de backend con él. Regresión local completa en verde tras el cambio: `migrate` desde base limpia, `makemigrations --check`, `manage.py check`, `pytest --cov=apps` (5 pruebas, 90 %), `ruff check`, comprobaciones GeoDjango y health check.
- **Aislamiento local/remoto.** Verificado que `docker compose up` sigue apuntando a la base local aunque el `.env` declare una `DATABASE_URL` remota (comprobado con un host ficticio). Es un mecanismo deliberado, documentado en ADR-0003 y en el README.

### Problemas encontrados
- Durante la verificación visual, Docker Desktop quedó con **dos procesos `com.docker.backend` simultáneos** tras un reinicio previo del daemon, lo que dejó la API de Docker respondiendo `500 Internal Server Error` (`docker ps`, `docker compose ps` colgados). Se resolvió terminando todos los procesos `Docker Desktop`/`com.docker.*` y relanzando la aplicación. Mientras el daemon estaba degradado, una captura de pantalla mostró `db`/`redis` en rojo con `status: "ok"` en el pill estático — no es un defecto de la aplicación, sino un efecto de la inestabilidad puntual del motor de Docker en esta máquina; **pendiente de última verificación tras el reinicio** (ver más abajo).
- `create-next-app`/`next dev` (Next.js 16) regeneran automáticamente `AGENTS.md` y `CLAUDE.md` en la raíz del frontend en cada arranque. Se desactivó con `agentRules: false` en `next.config.ts`, ya que esos archivos no forman parte de la estructura documentada en el plan.
- GDAL/GEOS no están disponibles de forma nativa en Windows; siguiendo la mitigación ya prevista en el plan, toda verificación de backend (migraciones, pruebas, lint) se ejecutó dentro del contenedor `Dockerfile.dev`, no en el host.

### Commits relevantes
_Pendiente — el responsable del proyecto realiza y aprueba los commits. Ningún cambio de este sprint fue commiteado automáticamente._

### Observaciones y desvíos respecto del plan
- Incorpora la corrección **C-06** (`AUTH_USER_MODEL` y el esqueleto mínimo de `auth_app.User` definidos antes de la primera migración — verificado con `migrate` sobre base vacía) y **C-10** (arquitectura de base de datos consistente: PostGIS en Supabase, backend/workers en Railway, conexión por `DATABASE_URL`; en local, el mismo `DATABASE_URL` apunta al contenedor de `infra/docker-compose.yml` sin ninguna rama de código distinta).
- **Pendiente de acción manual del responsable del proyecto** (no ejecutable por el asistente sin acceso a las cuentas):
  1. ~~Crear el proyecto en **Supabase**, habilitar PostGIS (`CREATE EXTENSION postgis;`) y obtener el `DATABASE_URL` de producción.~~ **Hecho (16/08/2026):** proyecto creado, PostGIS habilitada en el esquema `gis`, conexión verificada por Session Pooler con TLS y migraciones del Sprint 1 aplicadas. Detalle en "Verificación del backend contra la instancia real de Supabase".
  2. Verificar en el panel de Supabase si el plan contratado incluye **respaldos automáticos** (criterio C-11/D-03). Si no los incluye, iniciar la rutina de `pg_dump` semanal y antes de cada migración destructiva, y registrar aquí la primera ejecución. **Sigue pendiente:** requiere acceso al panel de facturación.
  3. Crear el proyecto en **Railway** para backend y worker, configurar variables de entorno de producción y conectar el repositorio (`Root Directory: backend`).
  4. Crear el proyecto en **Vercel**, conectar el repositorio (`Root Directory: frontend`) y configurar `NEXT_PUBLIC_API_URL` apuntando al backend de Railway.
  5. Con ambos desplegados, verificar el criterio de aceptación "el frontend desplegado consulta correctamente el backend desplegado" y abrir la PR de prueba para confirmar CI en verde antes de cerrar el sprint formalmente.
  6. Trasladar el backlog de `docs/BACKLOG.md` a la herramienta de tablero que el equipo decida usar (GitHub Projects u otra), si se prefiere una herramienta distinta al archivo versionado.

---

## Sprint 2 — Modelo de datos e incentivos

**Fechas planificadas:** 08/08/2026 – 14/08/2026
**Estado:** Pendiente
**Fecha real de cierre:** —

### Funcionalidades terminadas
_Pendiente._

### Pruebas realizadas
_Pendiente._

### Problemas encontrados
_Pendiente._

### Commits relevantes
_Pendiente._

### Observaciones y desvíos respecto del plan
_Pendiente. Incorpora las correcciones C-01 (estado `ANULADO`), C-02 (sin `CHECK` de autoconfirmación), C-06 (el `User` se amplía, no se redefine) y C-09 (`credited_at`, `voided_at` y `reversed_at` desde la migración inicial)._

---

## Sprint 3 — Autenticación, roles y permisos

**Fechas planificadas:** 15/08/2026 – 21/08/2026
**Estado:** Pendiente
**Fecha real de cierre:** —

### Funcionalidades terminadas
_Pendiente._

### Pruebas realizadas
_Pendiente._

### Problemas encontrados
_Pendiente._

### Commits relevantes
_Pendiente._

### Observaciones y desvíos respecto del plan
_Pendiente._

---

## Sprint 4 — Verificación de identidad y privacidad

**Fechas planificadas:** 22/08/2026 – 28/08/2026
**Estado:** Pendiente
**Fecha real de cierre:** —

### Funcionalidades terminadas
_Pendiente._

### Pruebas realizadas
_Pendiente._

### Problemas encontrados
_Pendiente._

### Commits relevantes
_Pendiente._

### Observaciones y desvíos respecto del plan
_Pendiente._

---

## Sprint 5 — Mapa público y geolocalización

**Fechas planificadas:** 29/08/2026 – 04/09/2026
**Estado:** Pendiente
**Fecha real de cierre:** —

### Funcionalidades terminadas
_Pendiente._

### Pruebas realizadas
_Pendiente._

### Problemas encontrados
_Pendiente._

### Commits relevantes
_Pendiente._

### Observaciones y desvíos respecto del plan
_Pendiente._

---

## Sprint 6 — Creación de reportes y duplicados

**Fechas planificadas:** 05/09/2026 – 11/09/2026
**Estado:** Pendiente
**Fecha real de cierre:** —

### Funcionalidades terminadas
_Pendiente._

### Pruebas realizadas
_Pendiente._

### Problemas encontrados
_Pendiente._

### Commits relevantes
_Pendiente._

### Observaciones y desvíos respecto del plan
_Pendiente._

---

## Sprint 7 — Panel ciudadano y confirmaciones

**Fechas planificadas:** 12/09/2026 – 18/09/2026
**Estado:** Pendiente
**Fecha real de cierre:** —

### Funcionalidades terminadas
_Pendiente._

### Pruebas realizadas
_Pendiente._

### Problemas encontrados
_Pendiente._

### Commits relevantes
_Pendiente._

### Observaciones y desvíos respecto del plan
_Pendiente._

---

## Sprint 8 — Clasificación asistida por IA

**Fechas planificadas:** 19/09/2026 – 25/09/2026
**Estado:** Pendiente
**Fecha real de cierre:** —

### Funcionalidades terminadas
_Pendiente._

### Pruebas realizadas
_Pendiente._

### Problemas encontrados
_Pendiente._

### Commits relevantes
_Pendiente._

### Observaciones y desvíos respecto del plan
_Pendiente._

---

## Sprint 9 — Panel institucional y validación

**Fechas planificadas:** 26/09/2026 – 02/10/2026
**Estado:** Pendiente
**Fecha real de cierre:** —

### Funcionalidades terminadas
_Pendiente._

### Pruebas realizadas
_Pendiente._

### Problemas encontrados
_Pendiente._

### Commits relevantes
_Pendiente._

### Observaciones y desvíos respecto del plan
_Pendiente._

---

## Sprint 10 — Estados, evidencias, auditoría y reversión

**Fechas planificadas:** 03/10/2026 – 09/10/2026
**Estado:** Pendiente
**Fecha real de cierre:** —

### Funcionalidades terminadas
_Pendiente._

### Pruebas realizadas
_Pendiente._

### Problemas encontrados
_Pendiente._

### Commits relevantes
_Pendiente._

### Observaciones y desvíos respecto del plan
_Pendiente. Incorpora la decisión D-01 (reversión siempre ejecutable; el saldo es la diferencia entre créditos válidos y consumidos y puede resultar negativa) y las correcciones C-01 (anulación de pendientes que siguieran sin acreditar) y C-08 (redacción del saldo y del criterio de reversión)._

---

## Sprint 11 — Créditos, ranking del mes en curso y privacidad

**Fechas planificadas:** 10/10/2026 – 16/10/2026
**Estado:** Pendiente
**Fecha real de cierre:** —

### Funcionalidades terminadas
_Pendiente._

### Pruebas realizadas
_Pendiente._

### Problemas encontrados
_Pendiente._

### Commits relevantes
_Pendiente._

### Observaciones y desvíos respecto del plan
_Pendiente._

---

## Sprint 12 — Catálogo de recompensas y canje simulado

**Fechas planificadas:** 17/10/2026 – 23/10/2026
**Estado:** Pendiente
**Fecha real de cierre:** —

### Funcionalidades terminadas
_Pendiente._

### Pruebas realizadas
_Pendiente._

### Problemas encontrados
_Pendiente._

### Commits relevantes
_Pendiente._

### Observaciones y desvíos respecto del plan
_Pendiente. Incorpora la decisión D-02 (costo fijo de 1.000 créditos, no editable) y las correcciones C-04 (canje serializado con `select_for_update()` sobre `User`) y C-08 (saldo definido como diferencia entre créditos válidos y consumidos)._

---

## Sprint 13 — Dashboards, integración e ingesta social

**Fechas planificadas:** 24/10/2026 – 30/10/2026
**Estado:** Pendiente
**Fecha real de cierre:** —

### Funcionalidades terminadas
_Pendiente._

### Pruebas realizadas
_Pendiente._

### Problemas encontrados
_Pendiente._

### Commits relevantes
_Pendiente._

### Observaciones y desvíos respecto del plan
_Pendiente. Telegram y X simulado solo se implementan si el núcleo obligatorio está estable; registrar aquí la decisión tomada y su justificación._

---

## Sprint 14 — Pruebas unitarias, integración, seguridad y E2E

**Fechas planificadas:** 31/10/2026 – 06/11/2026
**Estado:** Pendiente
**Fecha real de cierre:** —

### Funcionalidades terminadas
_Pendiente._

### Pruebas realizadas
_Pendiente._

### Problemas encontrados
_Pendiente._

### Commits relevantes
_Pendiente._

### Observaciones y desvíos respecto del plan
_Pendiente._

---

## Sprint 15 — Carga, dataset definitivo y documentación

**Fechas planificadas:** 07/11/2026 – 13/11/2026
**Estado:** Pendiente
**Fecha real de cierre:** —

### Funcionalidades terminadas
_Pendiente._

### Pruebas realizadas
_Pendiente._

### Problemas encontrados
_Pendiente._

### Commits relevantes
_Pendiente._

### Observaciones y desvíos respecto del plan
_Pendiente._

---

## Sprint 16 — Cierre y preparación de la defensa

**Fechas planificadas:** 14/11/2026 – 15/11/2026
**Estado:** Pendiente
**Fecha real de cierre:** —

### Funcionalidades terminadas
_Pendiente._

### Pruebas realizadas
_Pendiente._

### Problemas encontrados
_Pendiente._

### Commits relevantes
_Pendiente._

### Observaciones y desvíos respecto del plan
_Pendiente._

---

## Seguimiento de criterios técnicos

Se completa a medida que las mediciones estén disponibles (Sprints 14 y 15).

| Indicador | Umbral | Medición | Estado |
|---|---|---|---|
| Cobertura backend crítico | ≥ 80 % | — | Pendiente |
| Concordancia IA vs etiquetas de oro | ≥ 85 % | — | Pendiente |
| Latencia P50 endpoints críticos | ≤ 800 ms | — | Pendiente |
| Latencia de red Railway ↔ Supabase | Medida y documentada | — | Pendiente |
| Latencia P99 bajo carga | ≤ 2.000 ms | — | Pendiente |
| Throughput de creación de reportes | ≥ 50 rep/min | — | Pendiente |
| Flujos E2E críticos | 100 % | — | Pendiente |
| Reglas de incentivos (S-01…S-12) | 100 % | — | Pendiente |
| Pruebas de seguridad (Anexo H.5) | 100 % | — | Pendiente |
| Trazabilidad | 100 % | — | Pendiente |

## Seguimiento de ADR

| ADR | Título | Sprint previsto | Estado |
|---|---|---|---|
| ADR-0001 | Monorepo frontend + backend | S1 | Aceptada |
| ADR-0002 | Django modular por apps de dominio | S1 | Aceptada |
| ADR-0003 | PostgreSQL + PostGIS y alojamiento en Supabase (documenta D-03, versiones reales, `search_path`, aislamiento local/remoto, TLS y la política de respaldos) | S1 | Aceptada — actualizada el 16/08/2026 con la verificación contra la instancia real; pendiente confirmar en Supabase si el plan incluye respaldos automáticos |
| ADR-0004 | Ledger auditable con importes inmutables | S2 | Pendiente |
| ADR-0005 | Google OAuth 2.0 + JWT | S3 | Pendiente |
| ADR-0006 | Identidad HMAC y almacenamiento privado | S4 | Pendiente |
| ADR-0007 | Almacenamiento de fotografías | S6 | Pendiente |
| ADR-0008 | Integración desacoplada con Gemini | S8 | Pendiente |
| ADR-0009 | Tareas asíncronas y qué no es asíncrono | S8 | Pendiente |
| ADR-0010 | Créditos por institución y conversión 1:1 | S9 | Pendiente |
| ADR-0011 | Privacidad configurable del ranking | S4 | Pendiente |
| ADR-0012 | Detección de duplicados por proximidad | S6 | Pendiente |
| ADR-0013 | Política de reversión, anulación y saldo negativo | S10 | Pendiente |
| ADR-0014 | Modelo de usuario personalizado desde la primera migración | S1 | Aceptada |
