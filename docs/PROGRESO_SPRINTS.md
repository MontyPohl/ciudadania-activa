# Progreso de Sprints — Ciudadanía Activa

Registro de avance del prototipo. Se actualiza **al cerrar cada sprint**, antes de iniciar el siguiente.

- **Estado general:** Sprint 1 **completado** (cierre técnico y documental el 20/08/2026: implementación local, CI/CD y despliegue en Vercel/Railway/Supabase verificados en producción, y procedimiento de respaldo manual comprobado). Sprint 2 **completado** (cierre el 20/08/2026: modelo de datos de las 18 entidades implementado, desplegado y verificado en producción, con respaldos previo y posterior a la migración). Sprint 3 sin iniciar. Sin decisiones de arquitectura pendientes.
- **Plan de referencia:** [PLAN_DESARROLLO.md](PLAN_DESARROLLO.md)
- **Ventana de desarrollo:** 01/08/2026 → 15/11/2026 (16 sprints semanales, Anexo F revisado)
- **Estados posibles:** `Pendiente` · `En curso` · `Completado` · `Completado con observaciones` · `Bloqueado`

---

## Resumen general

| Sprint | Fechas | Objetivo | Estado |
|---|---|---|---|
| 1 | 01/08 – 07/08 | Entorno, repositorio y CI/CD | Completado |
| 2 | 08/08 – 14/08 | Modelo de datos e incentivos | Completado |
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

**Progreso:** 2 / 16 sprints completados (Sprint 1 y Sprint 2, ambos cerrados el 20/08/2026). Sprint 3 **no iniciado**: su ventana planificada comienza el 15/08, pero la ejecución real arrastra el desvío de calendario registrado en CT-29 y arranca cuando se autorice.

### Decisiones que condicionan el avance

| ID | Decisión | Afecta a | Estado |
|---|---|---|---|
| D-01 | Reversión de una validación cuyos créditos ya fueron canjeados | Sprint 10 · Sprint 12 | **Resuelta** — la reversión siempre se ejecuta; el saldo es la diferencia entre créditos válidos y créditos consumidos y puede resultar negativa, sin penalización adicional; el vale emitido permanece emitido; no se permite otro canje hasta que la diferencia alcance de nuevo el saldo requerido |
| D-02 | ¿Puede el operador fijar un `credit_cost` distinto de 1.000? | Sprint 12 | **Resuelta** — no. El costo es fijo en 1.000 créditos en el MVP; `credit_cost` se conserva con `DEFAULT 1000` por extensibilidad futura, pero no es editable por el operador |
| D-03 | ¿Dónde reside la instancia de PostgreSQL/PostGIS? | Sprint 1 · Sprint 15 | **Resuelta** — en **Supabase**, junto con Storage. El backend Django y los workers Celery se despliegan en Railway y se conectan mediante `DATABASE_URL`. Fundamento: coherencia con la arquitectura de la tesis, soporte de PostGIS, simplificación operativa, centralización de base y almacenamiento, y facilidad de inspección y documentación durante el TCC. La latencia Railway ↔ Supabase se mide en el Sprint 15 y no altera la arquitectura. Si el plan contratado no incluye respaldos automáticos, son obligatorios los respaldos lógicos periódicos. **Confirmado el 20/08/2026:** el plan Free no ofrece respaldos programados accesibles para este proyecto, por lo que la rutina manual de `pg_dump` queda en vigor y su procedimiento fue comprobado de extremo a extremo |
| D-04 | ¿Qué alcance tiene la integración social del MVP? | Sprint 13 · Sprint 15 | **Resuelta el 20/08/2026** — **exclusivamente una simulación interna de publicaciones de X/Twitter**, cargada desde un dataset del propio proyecto y persistida en `x_mock_messages`. **Telegram queda fuera del alcance** (sin Bot API, sin webhook, sin *polling*, sin token) y **no habrá integración real con la API de X** (sin OAuth, sin *scraping*, sin credenciales sociales, sin Celery para la ingesta). Se conservan `XMockMessage`, `source='x_mock'` y la regla RF-45. La eliminación técnica de `TelegramIngestedMessage` se ejecutará al inicio del Sprint 13 con migración controlada y respaldo previo. Ver **CT-43** |

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
| C-11 | Política de **respaldos lógicos** (`pg_dump` semanal y antes de cada migración destructiva) obligatoria si el plan de Supabase no incluye respaldos automáticos; volcados fuera del repositorio. **Condición confirmada el 20/08/2026** (plan Free sin respaldos programados): la política queda activa y el procedimiento verificado | S1 en adelante |
| C-12 | Medición explícita de la latencia Railway ↔ Supabase incorporada a las pruebas de carga, aislando tiempo de red del tiempo de aplicación | S15 |

---

## Sprint 1 — Entorno, repositorio y CI/CD

**Fechas planificadas:** 01/08/2026 – 07/08/2026
**Fecha real de inicio:** 12/08/2026
**Estado:** Completado
**Fecha real de cierre:** 20/08/2026 (cierre técnico y documental: despliegue verificado en producción y procedimiento de respaldo manual comprobado)

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

#### Despliegue en producción y verificación de extremo a extremo (20/08/2026)

Completa los puntos que quedaban como acción manual del responsable del proyecto. Con esto el Sprint 1 no conserva observaciones técnicas bloqueantes.

- **Backend en Railway.** Django desplegado en `https://ciudadania-activa-production.up.railway.app`, con `Root Directory: /backend`, Gunicorn escuchando en `0.0.0.0:8000` y `python manage.py migrate --noinput` como comando de pre-deploy. `GET /api/v1/health/` responde **HTTP 200** con `status=ok`, `db=true` y `redis=true`.
- **Hotfix del healthcheck de Railway.** `SECURE_SSL_REDIRECT=True` hacía que el healthcheck HTTP interno de Railway —que consulta el contenedor por HTTP plano y sin `X-Forwarded-Proto`— recibiera un **301** y el deploy fallara con «Healthcheck failure». Se corrigió con `SECURE_REDIRECT_EXEMPT = [r"^api/v1/health/$"]` en `config/settings/prod.py`, que exime **solo** esa ruta; HTTPS sigue siendo obligatorio para el resto del tráfico. En el mismo cambio, la comprobación de Redis del health check pasó a usar timeouts explícitos de 2 s (`socket_connect_timeout`, `socket_timeout`) y se añadió una prueba de regresión. Commit `0673ef6` (18/08/2026).
- **Redis en Railway.** Instancia gestionada operativa; el backend reporta `redis=true`.
- **Worker de Celery en Railway.** Servicio independiente, sin dominio público, sin healthcheck HTTP y sin pre-deploy de migraciones. Start command: `celery -A config.celery:app worker --loglevel=info --concurrency=2`. Conectado correctamente al Redis privado de Railway; log confirmado (`mingle: all alone`, `celery@… ready.`). `--concurrency=2` se fija de forma explícita porque, sin el parámetro, Celery detecta las 48 CPU del host y levanta 48 procesos innecesarios. **Celery Beat no se desplegó:** todavía no existen tareas periódicas reales que lo requieran (la primera está prevista en el Sprint 10).
- **Frontend en Vercel.** Next.js desplegado en `https://ciudadania-activa.vercel.app`, con `Root Directory: frontend` y la variable pública `NEXT_PUBLIC_API_URL` apuntando al backend de Railway. No se cargan secretos de backend en Vercel.
- **CORS.** El backend declara `CORS_ALLOWED_ORIGINS=https://ciudadania-activa.vercel.app`. La integración frontend → backend se probó correctamente, de modo que queda cumplido el criterio de aceptación «el frontend desplegado consulta correctamente el backend desplegado».
- **GitHub Actions.** Verificado en el panel: CI Backend en verde sobre `0673ef6` y sobre `32215e1`; CI Frontend en verde sobre `32215e1`; Dependency Graph en verde. El commit `0673ef6` solo afecta al backend, por lo que no dispara —ni necesita— un run nuevo de frontend.

#### Respaldos: verificación del plan y comprobación del procedimiento manual (20/08/2026)

Cierra el punto 2 de las acciones manuales y el criterio C-11 / D-03.

- **El plan Free de Supabase no ofrece respaldos programados** accesibles para este proyecto. Queda por tanto activa la política de ADR-0003: `pg_dump` manual periódico, respaldo previo a toda migración destructiva o importante, volcados **fuera del repositorio** y **nunca versionados**.
- **Procedimiento comprobado de extremo a extremo.** Cliente PostgreSQL **17.11** ejecutado mediante contenedor `postgres:17` (la máquina de trabajo no tiene cliente nativo y no se instalaron paquetes en el sistema). Volcado en formato *custom* con `--no-owner --no-acl`, guardado fuera del repositorio y con permisos restrictivos. `pg_dump` terminó con **código 0 y sin advertencias**, y `pg_restore --list` leyó el archivo sin errores: **≈437 entradas de objeto**, las 10 tablas Django del esquema `public` —incluidas las de `auth_app`— y las referencias de PostGIS (esquema `gis`, extensión `postgis`, `spatial_ref_sys`).
- **Restauración real probada** sobre una instancia PostgreSQL/PostGIS 17 temporal y desechable, eliminada al terminar: las tablas Django se restauraron completas, las 19 migraciones esperadas están presentes (`admin`, `auth`, `auth_app`, `contenttypes`, `sessions`) y PostGIS quedó funcional (GEOS y PROJ operativos, *round-trip* de geometría y `ST_Transform`/`ST_Distance` correctos). **Única incompatibilidad:** la extensión `supabase_vault`, específica del entorno Supabase, no existe en PostgreSQL vanilla, de modo que ni ella ni su tabla de secretos se restauran en ese destino. No es un defecto del volcado.
- Para una restauración fiel a producción, el destino debe usar una versión de **PostGIS compatible con 3.3.7**: el volcado emite `CREATE EXTENSION` sin versión, por lo que adopta la que el destino tenga instalada.
- **La base de producción no fue modificada.** Solo se ejecutaron operaciones de lectura (`pg_dump` toma únicamente locks `ACCESS SHARE`) y consultas de catálogo. No se ejecutó ninguna restauración contra Supabase.

### Problemas encontrados
- Durante la verificación visual, Docker Desktop quedó con **dos procesos `com.docker.backend` simultáneos** tras un reinicio previo del daemon, lo que dejó la API de Docker respondiendo `500 Internal Server Error` (`docker ps`, `docker compose ps` colgados). Se resolvió terminando todos los procesos `Docker Desktop`/`com.docker.*` y relanzando la aplicación. Mientras el daemon estaba degradado, una captura de pantalla mostró `db`/`redis` en rojo con `status: "ok"` en el pill estático — no es un defecto de la aplicación, sino un efecto de la inestabilidad puntual del motor de Docker en esta máquina. **Cerrado:** el health check quedó verificado en producción sobre Railway (`db=true`, `redis=true`).
- `create-next-app`/`next dev` (Next.js 16) regeneran automáticamente `AGENTS.md` y `CLAUDE.md` en la raíz del frontend en cada arranque. Se desactivó con `agentRules: false` en `next.config.ts`, ya que esos archivos no forman parte de la estructura documentada en el plan.
- GDAL/GEOS no están disponibles de forma nativa en Windows; siguiendo la mitigación ya prevista en el plan, toda verificación de backend (migraciones, pruebas, lint) se ejecutó dentro del contenedor `Dockerfile.dev`, no en el host.
- Durante la validación temporal de la restauración del respaldo (20/08/2026) se ejecutó por error `docker volume prune -f` en lugar de una limpieza dirigida al contenedor de prueba. Eliminó **5 volúmenes anónimos huérfanos** del entorno de desarrollo; los volúmenes nombrados de los proyectos permanecieron intactos y no hubo pérdida conocida de información de este proyecto. **Aprendizaje:** para limpiar pruebas se usa `docker rm -f -v` sobre el contenedor concreto y se evita el prune global.

### Commits relevantes
- `1698932` chore(init): configurar base técnica de Ciudadanía Activa
- `dfb2f47` docs: agregar matriz de cambios pendientes de la tesis
- `d0056c0` fix(infra): alinear PostgreSQL y conexión con Supabase
- `de0dd87` fix(frontend): generar tipos antes del typecheck
- `32215e1` docs: actualizar arquitectura Supabase y trazabilidad de tesis
- `0673ef6` fix(deploy): corregir healthcheck de Railway

El responsable del proyecto realiza y aprueba los commits; ningún cambio se commitea automáticamente. El cierre documental recogido en esta entrada se registra en el commit `docs: cerrar Sprint 1 con despliegue verificado`.

### Observaciones y desvíos respecto del plan
- Incorpora la corrección **C-06** (`AUTH_USER_MODEL` y el esqueleto mínimo de `auth_app.User` definidos antes de la primera migración — verificado con `migrate` sobre base vacía) y **C-10** (arquitectura de base de datos consistente: PostGIS en Supabase, backend/workers en Railway, conexión por `DATABASE_URL`; en local, el mismo `DATABASE_URL` apunta al contenedor de `infra/docker-compose.yml` sin ninguna rama de código distinta).
- **Acciones manuales del responsable del proyecto** (no ejecutables por el asistente sin acceso a las cuentas), **todas completadas**:
  1. ~~Crear el proyecto en **Supabase**, habilitar PostGIS (`CREATE EXTENSION postgis;`) y obtener el `DATABASE_URL` de producción.~~ **Hecho (16/08/2026):** proyecto creado, PostGIS habilitada en el esquema `gis`, conexión verificada por Session Pooler con TLS y migraciones del Sprint 1 aplicadas. Detalle en "Verificación del backend contra la instancia real de Supabase".
  2. ~~Verificar en el panel de Supabase si el plan contratado incluye **respaldos automáticos** (criterio C-11/D-03). Si no los incluye, iniciar la rutina de `pg_dump`.~~ **Hecho (20/08/2026):** el plan Free no ofrece respaldos programados accesibles para este proyecto. La rutina manual queda activa y su procedimiento se comprobó de extremo a extremo, incluida una restauración real. Detalle en "Respaldos: verificación del plan y comprobación del procedimiento manual".
  3. ~~Crear el proyecto en **Railway** para backend y worker, configurar variables de entorno de producción y conectar el repositorio.~~ **Hecho (verificado el 20/08/2026):** backend y `celery-worker` desplegados como servicios separados, con `Root Directory: /backend` y Redis gestionado. Detalle en "Despliegue en producción y verificación de extremo a extremo".
  4. ~~Crear el proyecto en **Vercel**, conectar el repositorio (`Root Directory: frontend`) y configurar `NEXT_PUBLIC_API_URL` apuntando al backend de Railway.~~ **Hecho (verificado el 20/08/2026).**
  5. ~~Con ambos desplegados, verificar el criterio de aceptación "el frontend desplegado consulta correctamente el backend desplegado" y confirmar CI en verde antes de cerrar el sprint formalmente.~~ **Hecho (20/08/2026):** integración frontend → backend probada con `CORS_ALLOWED_ORIGINS` restringido al dominio de Vercel, y CI de backend y frontend en verde. El fallo de healthcheck detectado en el camino se corrigió en `0673ef6`.
  6. Trasladar el backlog de `docs/BACKLOG.md` a la herramienta de tablero que el equipo decida usar (GitHub Projects u otra), si se prefiere una herramienta distinta al archivo versionado. **Opcional y sin efecto sobre el cierre del sprint:** el backlog ya está trasladado y versionado en el repositorio, que satisface el criterio de aceptación.

---

## Sprint 2 — Modelo de datos e incentivos

**Fechas planificadas:** 08/08/2026 – 14/08/2026
**Fecha real de inicio:** 20/08/2026
**Estado:** Completado
**Fecha real de cierre:** 20/08/2026 (implementación, CI, despliegue productivo verificado y respaldos previo y posterior a la migración)

### Funcionalidades terminadas
- **Diseño aprobado en dos fases previas** (inspección y addendum) con 16 decisiones cerradas (D2-01 … D2-16) antes de escribir código.
- **Modelo base transversal** en `core`: `BaseModel` abstracto (PK `UUID`, `created_at`, `updated_at`), enumeraciones compartidas en `core/enums.py` y servicio de auditoría `core.services.audit.log(actor, action, payload)`.
- **Las 18 entidades del Anexo A materializadas**: 17 tablas nuevas más la ampliación incremental de `users` con `google_oauth_id`, `role` y `verification_status`. `AUTH_USER_MODEL` y `auth_app.0001_initial` no se tocaron (ADR-0014).
- Distribución por app según §8 del plan: `auth_app` (`citizen_profiles`), `admin_app` (`institutions`, `categories`, `institution_operators`, `audit_logs`), `reports_app` (`reports`, `report_media`, `report_status_history`, `resolution_evidences`, `report_confirmations`), `ai_client_app` (`ai_classifications`), `rewards_app` (`reward_transactions`, `rewards`, `reward_redemptions`), `notifications_app` (`notifications`), `social_ingestion_app` (`telegram_ingested_messages`, `x_mock_messages`). `map_app` y `analytics_app` siguen sin modelos propios.
- **Enumeraciones como `TextChoices`** sobre `varchar`, sin tipos `ENUM` nativos. El ledger incluye los **cuatro** estados (`PENDIENTE`, `ACREDITADO`, `ANULADO`, `REVERTIDO`) y los seis `event_type`. No existe ningún estado `VALIDADO` del reporte.
- **`reports.location` como `geometry(Point,4326)`** con índice GIST, más los B-tree de `status`, `priority` y `created_at`; el de `institution_id` lo aporta la clave foránea.
- **Ledger con sus tres columnas de soporte desde la migración inicial** (`credited_at`, `voided_at`, `reversed_at`), `CheckConstraint` de coherencia estado ↔ marca temporal, e índices únicos parciales que garantizan un solo `REPORT_CREATED` por reporte y un solo `CONFIRMATION_CREATED` por confirmación.
- **Catálogos semilla** cargados por la migración de datos `admin_app.0002_seed_catalogs` (idempotente y reversible, con copia congelada), más el management command `seed_catalogs` y el envoltorio `scripts/seed_catalogs.py`: 3 instituciones y 10 categorías con el mapeo del Anexo A.3 y `default_priority = MEDIA`.
- **Esqueleto declarativo de `rewards_app.services.ledger`** con las cinco funciones del contrato del §8, todas levantando `NotImplementedError`: ninguna regla económica está implementada y `reports_app` no las invoca.
- **Django Admin de inspección**: los 18 modelos registrados; `reward_transactions`, `audit_logs` y `reward_redemptions` en **solo lectura estricta** (sin alta, cambio ni borrado); `credit_cost` de `rewards` como `readonly_fields` sin usar `editable=False` en el modelo; `document_hmac` y `verification_selfie_url` fuera de `list_display` y `search_fields`.
- **Tipos TypeScript espejo** en `frontend/src/types/domain.ts` (entidades y enumeraciones). Sin pantallas, hooks ni fetching.
- Documentación: `docs/DICCIONARIO_DATOS.md` nuevo, `ADR-0004-ledger-auditable.md` nuevo, fila **CT-42** en `CAMBIOS_TESIS_PENDIENTES.md` y sección de catálogos semilla en el `README.md`.

### Pruebas realizadas
Todas dentro del contenedor `Dockerfile.dev` contra PostgreSQL 17 + PostGIS 3.5.2 de `infra/docker-compose.yml`. **Ninguna se ejecutó contra Supabase.**

- **Baseline previo a tocar código:** `pytest --collect-only -q` → **7 pruebas**; `pytest --cov=apps` → **91 %** (218 sentencias, 20 sin cubrir).
- **Resultado final:** **147 pruebas verdes**, cobertura **98 %** (1.501 sentencias, 27 sin cubrir). Por encima del baseline, del objetivo práctico del 90 % y del mínimo contractual RNF-04 (80 %).
- `python manage.py migrate` sobre base vacía creada desde `template_postgis`: aplica las 28 migraciones y crea **las 18 tablas**, con `auth_app.0001_initial` antes de `admin`.
- **Rollback completo** en orden inverso de dependencias hasta `auth_app 0001`: no queda ninguna tabla de dominio, `users` sobrevive intacta. **Reaplicación** posterior correcta, con el catálogo semilla de nuevo cargado.
- Seed: la migración de datos deja 3 instituciones y 10 categorías; `seed_catalogs` ejecutado dos veces consecutivas no duplica ni recrea filas (0 nuevas en ambas), y repone el catálogo si se vacía.
- Verificación geoespacial directa en PostgreSQL: `geometry_columns` devuelve `POINT / 4326 / 2` para `reports.location`; existe un único índice `USING gist` sobre esa columna; `ST_DWithin` con cast a `geography` encuentra el punto a ~35 m y descarta el lejano con radio de 100 m; `ST_DistanceSphere` mide en metros. Todas las funciones usadas existen en PostGIS 3.3.7, el piso de compatibilidad productivo.
- Constraints ejercitados con pruebas negativas: unicidades (`institutions.name`/`slug`, `categories.name`/`slug`, `citizen_profiles.document_hmac`, `report_confirmations(report_id, citizen_id)`, `reward_redemptions.code`, `users.google_oauth_id` admitiendo varios `NULL`, `UNIQUE(user, institution)` de operadores, `UNIQUE(institution, name)` de recompensas), `CHECK` de `sla_hours`, `credit_cost`, `credits_spent` y `confidence`, y las **11 combinaciones incoherentes** de estado ↔ marca temporal del ledger, junto con los **cinco casos válidos** —incluidos los dos de `REVERTIDO`—.
- `on_delete` verificado en base de datos: `SET_NULL` en autor, validador y actores de auditoría; `PROTECT` en categorías, instituciones, sujeto del ledger, reportes con historia económica, confirmadores y recompensas canjeadas; `CASCADE` en material asociado al reporte y en el perfil del ciudadano.
- Prueba explícita de que la base de datos **no** impide confirmar el propio reporte (C-02/CT-16: la regla es del servicio de dominio del Sprint 7) y de que ninguna función del ledger está implementada.
- `python manage.py check`, `python manage.py makemigrations --check --no-input`, `ruff check .` y `ruff format --check .`: sin hallazgos.
- Frontend: `npm run typecheck`, `npm run lint` y `prettier --check .` en verde.

#### Despliegue productivo y verificación (20/08/2026)

- **Commit desplegado:** `c134ca9` (`feat(data): implementar modelo de dominio del Sprint 2`), 80 archivos, +4.333 líneas.
- **CI sobre ese commit:** CI Backend **success** (job `test`: Ruff, `makemigrations --check`, `migrate` sobre base vacía y `pytest --cov`) y CI Frontend **success** (job `build`: Prettier, ESLint, `tsc`, `next build`). Sin checks pendientes ni rojos.
- **Migraciones en Supabase:** las **nueve** del Sprint 2 aplicadas, `django_migrations` con **28 filas** (19 del Sprint 1 + 9 nuevas). Ninguna quedó pendiente y no hubo aplicación parcial.
- **Esquema productivo verificado** con consultas de solo lectura: las **18 entidades** presentes (17 nuevas más `users`, que ya existía); `reports.location` como `geometry(Point,4326)` con **un único índice GIST**; los B-tree de `status`, `priority` y `created_at`; los cuatro índices nominales del ledger, incluidos los dos únicos parciales de `REPORT_CREATED` y `CONFIRMATION_CREATED`; el `CheckConstraint` de coherencia estado ↔ marca temporal idéntico al validado en local; `document_hmac` NOT NULL y único; `reports.category_id` NOT NULL, `institution_id` nullable, `priority` y `source` NOT NULL; `report_confirmations` **sin** columna `confirmed_at`.
- **Entorno real:** PostgreSQL **17.6**, PostGIS **3.3.7** en el esquema **`gis`**, `search_path = public,gis,extensions`.
- **Seed productivo:** 3 instituciones y 10 categorías con el mapeo completo del Anexo A.3, todas con `default_priority = MEDIA`, sin `contact_email` ni `sla_hours`. Las 15 tablas funcionales restantes y `users` quedaron en 0 filas: la migración creó estructura y catálogos, sin datos inesperados.
- La verificación se ejecutó con la sesión de base de datos fijada en `READ ONLY`, comprobado con un intento de escritura deliberado que PostgreSQL rechazó.

#### Respaldos del Sprint 2

Dos volcados en formato *custom* con `--no-owner --no-acl`, ambos fuera del repositorio, con permisos restrictivos y **ambos conservados**:

| Clasificación | Archivo | Estado que representa |
|---|---|---|
| **BACKUP PRE-SPRINT-2** | `ciudadania_activa_20260820_022332.dump` | producción **antes** de las migraciones estructurales del Sprint 2 (solo las 10 tablas del Sprint 1) |
| **BACKUP POST-SPRINT-2** | `ciudadania_activa_20260820_042805_post_s2.dump` | producción **después** de las migraciones (las 17 tablas nuevas presentes) |

La diferencia entre ambos TOC es exactamente las 17 tablas nuevas: ausentes en el PRE, presentes en el POST. El POST-S2 se validó de extremo a extremo: `pg_restore --list` legible y **restauración real** en una instancia PostgreSQL/PostGIS 17 temporal y desechable. La restauración completa falla en tres objetos de **`supabase_vault`** —extensión propia de Supabase que no existe en PostgreSQL vanilla, límite ya documentado en ADR-0003— y **ninguno** de los errores es atribuible a otra causa; excluyendo únicamente los objetos de `vault` de la lista de restauración, el `pg_restore` termina en **exit 0** sin errores. Sobre la base restaurada se comprobaron las 18 entidades, las 28 migraciones, el GIST, los índices y el `CHECK` del ledger, y los catálogos semilla completos.

### Problemas encontrados
- **La base de datos de pruebas no heredaba PostGIS.** La imagen `postgis/postgis` habilita la extensión en la base del proyecto y en `template_postgis`, pero no en `template1`, que es la plantilla que PostgreSQL usa al crear la base de test. Con `reports.location` la creación de la base de pruebas fallaba porque el tipo `geometry` no existía. Se resolvió declarando `DATABASES["default"]["TEST"] = {"TEMPLATE": "template_postgis"}` **solo en `config/settings/test.py`**: la extensión sigue siendo infraestructura provisionada (ADR-0003) y ninguna migración la crea, de modo que el comportamiento en Supabase no cambia. Local y CI usan la misma imagen, así que la plantilla existe en ambos.
- **`makemigrations` agrupó la ampliación de `users` y `citizen_profiles` en una sola migración.** Se dividió a mano en `0002_user_domain_fields` (tres `AddField`) y `0003_citizen_profile` (`CreateModel`) antes de aplicarlas, para poder revertir el perfil sin deshacer las columnas del usuario. Ninguna migración ya aplicada fue editada.
- **Archivos generados dentro del contenedor quedaron con propietario `root`** por el volumen montado. Se corrigió la propiedad tras cada generación; no afecta al contenido.

### Commits relevantes
- `c134ca9` feat(data): implementar modelo de dominio del Sprint 2

El responsable del proyecto revisó el diff y aprobó el commit; ningún cambio se commitea automáticamente. El cierre documental recogido en esta entrada se registra en un commit posterior.

### Observaciones y desvíos respecto del plan
- Incorpora las correcciones **C-01** (estado `ANULADO` presente en el enumerado y probado), **C-02** (sin `CHECK` de autoconfirmación, con prueba que lo documenta), **C-06** (el `User` se amplía por migración incremental, no se redefine) y **C-09** (`credited_at`, `voided_at` y `reversed_at` en la migración inicial de `reward_transactions`).
- **Seis divergencias estructurales respecto del Anexo A**, aprobadas en el diseño y consolidadas en la fila **CT-42** de `CAMBIOS_TESIS_PENDIENTES.md`: `is_active` en instituciones y categorías, `reports.rejection_reason`, `citizen_profiles.verified_by`, uso de `created_at` en lugar de una columna `confirmed_at`, y `reports.institution_id` nullable.
- **`sla_hours` y `contact_email` quedan vacíos**: la tesis no documenta ningún valor y no se inventó ninguno. Se completarán cuando exista el dato real (métricas del Sprint 13).
- **Nada de la lógica de negocio se adelantó**: no hay endpoints nuevos, ni servicios de incentivos, confirmación, validación, ranking, canje o IA, ni tareas de Celery, ni signals. El único servicio con cuerpo real es el de auditoría, que el propio Sprint 2 exige tener disponible.
- El desvío de calendario del Sprint 1 (CT-29) se arrastra: el sprint planificado para el 08/08–14/08 comenzó el 20/08/2026. Es desvío de fechas, no de alcance.
- **Railway aplica las migraciones automáticamente en el pre-deploy.** El servicio de backend tiene configurado `python manage.py migrate --noinput` como comando de pre-deploy (fijado en el Sprint 1). El push de `c134ca9` disparó ese despliegue y las nueve migraciones quedaron aplicadas en Supabase entre las **07:11:23 y las 07:11:29 UTC** del 20/08/2026, mientras **CI Backend todavía estaba corriendo** —terminó a las **07:13:04 UTC**—. No es un fallo de Django ni de Railway: es una propiedad del pipeline tal como está configurado, y conviene tenerla presente porque significa que **un push a `main` con migraciones nuevas puede modificar producción antes de que CI Backend termine**. El resultado fue correcto y verificado, y el hallazgo no bloqueó el sprint.
- **Aprendizaje operativo y procedimiento para los sprints siguientes.** Mientras `migrate` permanezca en el pre-deploy de Railway, el respaldo previo a una migración estructural debe generarse **antes** del push a `main`, no después de que CI termine. El flujo preferido queda documentado así: rama o *pull request* → **CI verde** → **`pg_dump` productivo previo** → *merge*/push a `main` → Railway ejecuta `migrate` en el pre-deploy → **verificación productiva** → respaldo posterior si el cambio es estructuralmente relevante. Si en algún momento se quisiera un gate manual absoluto, la alternativa es retirar `migrate` del pre-deploy y ejecutarlo como operación autorizada aparte. **No se modificó la configuración de Railway en este sprint**, ni el esquema de ramas, ni los workflows de CI: por ahora es procedimiento documentado. Ver `ADR-0003` y el `README.md`.
- En este sprint la secuencia real fue la inversa —respaldo previo tomado a las 05:23 UTC, push a las 07:10, migración automática a las 07:11— así que el punto de retorno anterior a las migraciones existe y está conservado, pero se obtuvo **antes** en el tiempo y no como paso inmediatamente previo al despliegue.

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

> **Alcance revisado el 20/08/2026 (decisión D-04 · CT-43).** La integración social del MVP se
> limita a una **simulación interna de publicaciones de X/Twitter**, cargada desde un dataset del
> propio proyecto y persistida en `x_mock_messages`. **Telegram ya no forma parte del MVP** y **no
> habrá integración real con la API de X**: sin Bot API, sin webhook, sin *polling*, sin tokens, sin
> OAuth de X, sin *scraping*, sin credenciales sociales y sin Celery para la ingesta. `XMockMessage`
> y `source='x_mock'` se conservan, igual que la regla RF-45.
>
> **Trabajo estructural previsto para el inicio de este sprint:** eliminar `TelegramIngestedMessage`
> (modelo, admin, pruebas y tipo TypeScript espejo), retirar el valor `telegram` del enumerado
> `ReportSource`, generar las migraciones correspondientes en `social_ingestion_app` y `reports_app`,
> y actualizar entonces `DICCIONARIO_DATOS.md` de 18 a 17 entidades. Al ser una migración
> **destructiva** (`DROP TABLE telegram_ingested_messages`), exige **respaldo productivo previo al
> push a `main`** según ADR-0003, y verificación productiva y respaldo posterior tras el
> auto-migrate de Railway.
>
> **A día de hoy nada de eso se ha ejecutado:** `TelegramIngestedMessage` sigue en el modelo, la
> tabla sigue en producción (sin filas) y el esquema conserva **18 entidades**.

### Funcionalidades terminadas
_Pendiente._

### Pruebas realizadas
_Pendiente._

### Problemas encontrados
_Pendiente._

### Commits relevantes
_Pendiente._

### Observaciones y desvíos respecto del plan
_Pendiente. La parte social se reduce a la simulación de X/Twitter (D-04 · CT-43) y solo se implementa si el núcleo obligatorio está estable; registrar aquí la decisión tomada y su justificación, junto con el resultado de la eliminación de `TelegramIngestedMessage`._

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
| ADR-0003 | PostgreSQL + PostGIS y alojamiento en Supabase (documenta D-03, versiones reales, `search_path`, aislamiento local/remoto, TLS y la política de respaldos) | S1 | Aceptada — actualizada el 20/08/2026: plan Free sin respaldos programados, política de respaldo manual verificada con restauración real y despliegue en Railway/Vercel operativo |
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
