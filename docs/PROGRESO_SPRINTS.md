# Progreso de Sprints — Ciudadanía Activa

Registro de avance del prototipo. Se actualiza **al cerrar cada sprint**, antes de iniciar el siguiente.

- **Estado general:** Sprint 1 **completado** (cierre técnico y documental el 20/08/2026: implementación local, CI/CD y despliegue en Vercel/Railway/Supabase verificados en producción, y procedimiento de respaldo manual comprobado). Sprint 2 **completado** (cierre el 20/08/2026: modelo de datos de las 18 entidades implementado, desplegado y verificado en producción, con respaldos previo y posterior a la migración). Sprint 3 **completado** (cierre el 20/08/2026: autenticación con Google, JWT propio, roles y permisos desplegados en Railway y Vercel, con el login real verificado de extremo a extremo). Sprint 4 **completado** (inicio el 22/08/2026, cierre el 23/08/2026: verificación de identidad con HMAC y almacenamiento privado, revisión administrativa manual con cotejo de la cédula, notificaciones asíncronas entregadas por la API HTTPS de Brevo y privacidad configurable del ranking, todo verificado en producción con una identidad real). Sprint 5 **completado** el 27/08/2026: S5-A, S5-B y S5-C están cerradas, integradas y validadas. Sprint 6 **completado** el 28/08/2026: S6-A y S6-B están cerrados. Sprint 7 **completado** el 29/08/2026: S7-A, S7-B y S7-C están integrados en `main`, y S7-D quedó probado en producción y cerrado con evidencia funcional y de base de datos en modo solo lectura. Sprint 8 continúa **en curso**. Sprint 9 quedó **completado** el 09/09/2026: S9-A, S9-B1, S9-B2 y el cierre documental S9-B3 están integrados en `main`; PR #38 fue mergeado mediante `7e95856c1b494d562ce1c267c06d5bebaec28cc8`. Sprint 10 quedó **implementado, desplegado, verificado y reconciliado** el 21/09/2026: el candidato `171f8c8b8f448a4092e73aeb5f67406e6b81ef09` se desplegó en Heroku, PR #39 se integró en `main` mediante `7ae82b865d90c4cade02dd3378b44107f088b7dd`, y backend, frontend, migraciones, backfill, workers e invariantes productivas quedaron verificados. Sprint 11 está **en curso**: S11-A quedó oficialmente congelada en `main@35f850eb24df552805fe8694f50fcbb3588a4141`; S11-B dejó preparada la instrumentación aislada y reproducible, sin ejecutar experimentos ni generar resultados; S11-C–E no se iniciaron.
- **Plan de referencia:** [PLAN_DESARROLLO.md](PLAN_DESARROLLO.md)
- **Ventana de desarrollo:** 01/08/2026 → 15/11/2026 (16 sprints semanales, Anexo F revisado)
- **Estados posibles:** `Pendiente` · `En curso` · `Completado` · `Completado con observaciones` · `Bloqueado`

---

## Resumen general

| Sprint | Fechas | Objetivo | Estado |
|---|---|---|---|
| 1 | 01/08 – 07/08 | Entorno, repositorio y CI/CD | Completado |
| 2 | 08/08 – 14/08 | Modelo de datos e incentivos | Completado |
| 3 | 15/08 – 21/08 | Autenticación, roles y permisos | Completado |
| 4 | 22/08 – 28/08 | Verificación de identidad y privacidad | Completado |
| 5 | 29/08 – 04/09 | Mapa público y geolocalización | Completado |
| 6 | 05/09 – 11/09 | Creación de reportes y duplicados | Completado |
| 7 | 12/09 – 18/09 | Panel ciudadano y confirmaciones | Completado |
| 8 | 19/09 – 25/09 | Clasificación asistida por IA | En curso |
| 9 | 26/09 – 02/10 | Panel institucional y validación | Completado |
| 10 | 03/10 – 09/10 | Estados, evidencias, auditoría y reversión | Completado — implementado, desplegado, verificado y reconciliado |
| 11 | 10/10 – 16/10 | Metodología, instrumentación, experimentos, análisis y evidencia de tesis | En curso — S11-A congelada; S11-B preparada; S11-C–E pendientes |
| 12 | 17/10 – 23/10 | Catálogo de recompensas y canje simulado | Pendiente |
| 13 | 24/10 – 30/10 | Dashboards, integración e ingesta social | Pendiente |
| 14 | 31/10 – 06/11 | Pruebas unitarias, integración, seguridad y E2E | Pendiente |
| 15 | 07/11 – 13/11 | Carga, dataset definitivo y documentación | Pendiente |
| 16 | 14/11 – 15/11 | Cierre y preparación de la defensa | Pendiente |

**Progreso:** 8 / 16 sprints completados (Sprints 1, 2 y 3 cerrados el 20/08/2026; Sprint 4 el 23/08/2026; Sprint 5 el 27/08/2026; Sprint 6 el 28/08/2026; Sprint 7 el 29/08/2026; Sprint 9 el 09/09/2026). La ejecución real arrastra el desvío de calendario registrado en CT-29. Sprint 8 continúa **en curso**: R2 permanece prerregistrado/no ejecutado y CA-4 no demostrado. Sprint 11 completó la congelación S11-A y la preparación técnica S11-B; no inició experimentos S11-C, análisis S11-D, cierre S11-E ni S12–S13.

### Decisiones que condicionan el avance

| ID | Decisión | Afecta a | Estado |
|---|---|---|---|
| D-01 | Reversión de una validación cuyos créditos ya fueron canjeados | Sprint 10 · Sprint 12 | **Resuelta** — la reversión siempre se ejecuta; el saldo es la diferencia entre créditos válidos y créditos consumidos y puede resultar negativa, sin penalización adicional; el vale emitido permanece emitido; no se permite otro canje hasta que la diferencia alcance de nuevo el saldo requerido |
| D-02 | ¿Puede el operador fijar un `credit_cost` distinto de 1.000? | Sprint 12 | **Resuelta** — no. El costo es fijo en 1.000 créditos en el MVP; `credit_cost` se conserva con `DEFAULT 1000` por extensibilidad futura, pero no es editable por el operador |
| D-03 | ¿Dónde reside la instancia de PostgreSQL/PostGIS? | Sprint 1 · Sprint 15 | **Resuelta** — en **Supabase**, junto con Storage. El backend Django y los workers Celery se despliegan actualmente en Heroku y se conectan mediante `DATABASE_URL`; Railway fue el proveedor anterior. Fundamento: coherencia con la arquitectura de la tesis, soporte de PostGIS, simplificación operativa, centralización de base y almacenamiento, y facilidad de inspección y documentación durante el TCC. La latencia Heroku ↔ Supabase se mide en el Sprint 15 y no altera la arquitectura. Si el plan contratado no incluye respaldos automáticos, son obligatorios los respaldos lógicos periódicos. **Confirmado el 20/08/2026:** el plan Free no ofrece respaldos programados accesibles para este proyecto, por lo que la rutina manual de `pg_dump` queda en vigor y su procedimiento fue comprobado de extremo a extremo |
| D-04 | ¿Qué alcance tiene la integración social del MVP? | Sprint 13 · Sprint 15 | **Resuelta el 20/08/2026** — **exclusivamente una simulación interna de publicaciones de X/Twitter**, cargada desde un dataset del propio proyecto y persistida en `x_mock_messages`. **Telegram queda fuera del alcance** (sin Bot API, sin webhook, sin *polling*, sin token) y **no habrá integración real con la API de X** (sin OAuth, sin *scraping*, sin credenciales sociales, sin Celery para la ingesta). Se conservan `XMockMessage`, `source='x_mock'` y la regla RF-45. La eliminación técnica de `TelegramIngestedMessage` se ejecutará al inicio del Sprint 13 con migración controlada y respaldo previo. Ver **CT-43** |

Las decisiones base D-01–D-04 están resueltas. Antes de ampliar las notificaciones más allá del correo de identidad deberán decidirse destinatarios, contenido, privacidad, idempotencia, asociación con reportes, tratamiento de fallos del broker, momento de emisión y uso de `transaction.on_commit`. Detalle de la planificación base en la sección 33.2 de [PLAN_DESARROLLO.md](PLAN_DESARROLLO.md).

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
| C-10 | PostgreSQL/PostGIS alojado en **Supabase** de forma consistente en arquitectura, despliegue, Sprint 1, riesgos y `ADR-0003`. Backend y workers actualmente en Heroku, conectados por `DATABASE_URL` | S1 · S15 |
| C-11 | Política de **respaldos lógicos** (`pg_dump` semanal y antes de cada migración destructiva) obligatoria si el plan de Supabase no incluye respaldos automáticos; volcados fuera del repositorio. **Condición confirmada el 20/08/2026** (plan Free sin respaldos programados): la política queda activa y el procedimiento verificado | S1 en adelante |
| C-12 | Medición explícita de la latencia Heroku ↔ Supabase incorporada a las pruebas de carga, aislando tiempo de red del tiempo de aplicación | S15 |

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
**Fecha real de inicio:** 20/08/2026
**Estado:** Completado
**Fecha real de cierre:** 20/08/2026 (implementación, CI, despliegue en Railway y Vercel, y login real con Google verificado de extremo a extremo)

### Funcionalidades terminadas
- **Diseño aprobado en dos fases** (Fase A y addendum A.1) con nueve decisiones cerradas
  antes de escribir código, registradas en `ADR-0005`.
- **Flujo de Google Identity Services**: el navegador obtiene un ID token y el backend lo
  verifica con `google-auth` (firma, emisor, `audience`, expiración) y exige `sub`,
  `email` y `email_verified`. La identidad de confianza es el claim `sub`, que se guarda
  en `users.google_oauth_id`. Ni el token ni el `sub` se registran en logs.
- **JWT propio** con `djangorestframework-simplejwt`: access de 15 minutos, refresh de 7
  días, sin rotación y **sin `token_blacklist`**, de modo que el sprint no crea ninguna
  tabla. Vista de renovación propia, porque la de SimpleJWT no comprueba `is_active`.
- **Tres endpoints**, los del plan: `POST /api/v1/auth/google/`,
  `POST /api/v1/auth/refresh/` y `GET /api/v1/auth/me/`. **No existe `/auth/logout/`** en
  Django: sin revocación server-side no habría nada que revocar.
- **Vinculación de cuentas** con los seis casos resueltos (A-F), 409 genérico en los
  conflictos, reintento controlado ante `IntegrityError` para que dos peticiones
  simultáneas con el mismo `sub` produzcan un solo usuario, y búsqueda por
  `email__iexact` para no generar duplicados por capitalización.
- **Denegación por defecto**: `DEFAULT_PERMISSION_CLASSES = ["apps.core.permissions.DenyAll"]`.
  Los cinco permisos del plan viven en `core.permissions` y leen el rol de la base de
  datos en cada petición, nunca de un claim del token.
- **BFF mínimo de sesión en Next.js**: tres Route Handlers same-origin que guardan el
  refresh en una cookie `ca_refresh` **first-party** `HttpOnly` y devuelven al navegador
  solo el access y el usuario. El access vive en memoria; el resto de la API se consume
  directamente contra Django con `Authorization: Bearer`.
- **Frontend**: `SessionProvider` con tres estados, botón de Google con el script oficial
  de GIS y sin dependencia npm nueva, cabecera con estado de sesión y de verificación,
  pantalla de acceso, guardas por rol para los grupos `(citizen)`, `(operator)` y
  `(admin)`, y renovación única compartida ante un `401`.
- **Comando `promote_user`**, única vía de asignación de roles: promueve usuarios
  existentes, exige institución para `operator` y la rechaza para `admin`, crea o reactiva
  el vínculo conservando los demás, es idempotente, no toca `verification_status` ni
  `is_staff`/`is_superuser`, y audita la promoción como acción de sistema.
- Se retiran de `.env.example` `GOOGLE_OAUTH_CLIENT_SECRET` y `GOOGLE_OAUTH_REDIRECT_URI`:
  pertenecían al Authorization Code flow descartado y ninguna parte del código las leía.

### Pruebas realizadas
Todas dentro del contenedor `Dockerfile.dev` contra PostgreSQL 17 + PostGIS local, con el
verificador de Google **sustituido por un doble**: ninguna prueba sale a la red y ninguna
necesita credenciales reales.

- **Baseline previo a tocar código:** 147 pruebas, cobertura **98 %**.
- **Resultado:** **249 pruebas verdes**, cobertura **99 %**. Los módulos nuevos
  (`services/google.py`, `services/accounts.py`, `serializers.py`, `views.py`,
  `core/permissions.py`, `promote_user`) quedan al **100 %**.
- Verificación del ID token: válido, firma inválida, expirado, `audience` incorrecta,
  emisor incorrecto, `email_verified` falso, `sub` ausente o vacío, `email` ausente, JWKS
  inalcanzable (503) y client id sin configurar (503 sin verificar nada).
- Vinculación: los seis casos A-F, más el reintento tras `IntegrityError`, dos
  resoluciones del mismo `sub` que producen un único usuario, y el duplicado preexistente
  por capitalización que devuelve 409 en lugar de elegir una fila.
- Usuario nuevo: `citizen`, `PENDIENTE`, contraseña inusable y **sin `CitizenProfile`**.
- Cuenta inactiva: login 401 genérico, refresh 401, `Bearer` rechazado y sin reactivación.
- `last_login` cambia en el login y **no** cambia al renovar.
- JWT: `user_id` con la PK UUID, refresh que no rota, refresh que no sirve como access,
  `user_id` manipulado a un valor no-UUID y claim `role` inyectado que no eleva privilegios.
- Permisos: `DenyAll` no concede a nadie —tampoco a un superusuario—, una vista sin
  permiso declarado no responde, el health público sigue accesible, y los cinco permisos
  con sus combinaciones de rol, vínculo activo e institución del objeto.
- `promote_user`: operador con vínculo nuevo y reactivado, idempotencia, conservación de
  otros vínculos, admin sin flags de Django, combinaciones inválidas, usuario inexistente,
  auditoría como `system` sin datos sensibles y atomicidad entre rol, vínculo y auditoría.
- `python manage.py check`, `makemigrations --check --no-input`, `ruff check .` y
  `ruff format --check .` sin hallazgos.
- Frontend: `npm run typecheck`, `npm run lint`, `prettier --check .` y `npm run build` en
  verde. El build genera las tres rutas del BFF y la pantalla de acceso.

#### Despliegue productivo y prueba real (20/08/2026)

- **Commit desplegado:** `d375c76` (`feat(auth): implementar autenticación Google y roles`), integrado a `main` mediante *squash and merge* del PR #2, con 43 archivos y +3.245 líneas.
- **CI sobre el commit de la rama:** CI Backend **success** (Ruff, `makemigrations --check`, `migrate` sobre base vacía y `pytest --cov`) y CI Frontend **success** (Prettier, ESLint, `tsc`, `next build`). En el log de CI: **262 pruebas verdes y 99 % de cobertura**, y `makemigrations --check` con **`No changes detected`**.
- **Backend en Railway:** desplegado y operativo. `GET /api/v1/health/` responde **HTTP 200** con `status=ok`, `db=true` y `redis=true`. El `migrate --noinput` del pre-deploy fue una operación **sin efecto**: el sprint no introduce ninguna migración.
- **Endpoints de autenticación verificados en producción**, sin usar ningún token real: `POST /auth/google/` con cuerpo vacío responde **400**; `POST /auth/refresh/` sin refresh, **400**; `GET /auth/me/` sin `Bearer`, **401**; y `POST /auth/logout/` sigue devolviendo **404**, porque Django expone exactamente tres endpoints de autenticación. Ningún 500.
- **Frontend en Vercel Production:** desplegado sobre el mismo commit. `GET /login` responde **HTTP 200** y el bundle de producción incorpora el Client ID y el script oficial de Google Identity Services.
- **BFF de sesión verificado en producción:** `POST /api/auth/refresh` sin cookie responde **401**; `POST /api/auth/logout`, **204**, emitiendo la cookie de borrado con `HttpOnly`, `Secure`, `SameSite=Lax` y `Path=/api/auth`; `POST /api/auth/google` con cuerpo vacío, **400**.
- **Configuración de Google confirmada sin exponer valores.** El Client ID se verificó por comportamiento: una sonda con una cadena que no es un token —jamás un token real— devolvía **503** cuando el backend no tenía el `audience` configurado y devuelve **401** una vez aplicado, lo que prueba que la verificación se ejecuta. En el frontend se comprobó por presencia en el bundle de producción. En ningún momento se leyó, imprimió ni versionó el valor.
- **Prueba real de extremo a extremo, ejecutada manualmente por el responsable del proyecto:** login con una cuenta de Google habilitada como usuario de prueba, con el botón oficial de Google Identity Services cargado correctamente; el usuario quedó autenticado y **creado con rol `citizen`**; la interfaz mostró el estado de verificación como **pendiente**, es decir identidad sin verificar; **recargar la página mantuvo la sesión**; `POST /api/auth/refresh` respondió **200**; `GET /api/v1/auth/me/` respondió **200**; `POST /api/auth/logout` respondió **204**; y tras el cierre de sesión una nueva recarga dejó al usuario **deslogueado**. El código de estado exacto de la petición inicial a `/api/auth/google` **no quedó registrado**, de modo que no se documenta ninguno: lo verificado es el éxito funcional del flujo completo.

### Problemas encontrados
- **La vista de renovación de SimpleJWT no comprueba `is_active`.** Su serializador valida
  el refresh sin cargar al usuario, de modo que una cuenta desactivada podría renovar su
  sesión indefinidamente. Se resolvió con una vista propia que carga el usuario y devuelve
  401 genérico. Hay una prueba dedicada.
- **La clave de pruebas era demasiado corta para firmar HS256.** `SECRET_KEY` en
  `config/settings/test.py` medía 15 bytes y PyJWT emitía 27 advertencias por petición
  firmada. Se alargó a 43 bytes; solo afecta al entorno de test.
- **El linter del frontend rechaza mutar un valor devuelto por `useState`.** El access
  token se guardaba en un objeto creado con `useState`; se cambió a `useRef`, que es el
  contenedor mutable idiomático y no dispara `react-hooks/immutability`.
- **Las variables de entorno no llegaron con el primer despliegue.** Tras el merge, el
  backend devolvía `503` en el login y el bundle de producción no incorporaba el Client ID:
  en Railway el cambio quedaba en *staged* y en Vercel la build anterior a configurar la
  variable no la había incorporado. Se resolvió con un Deploy del backend y un Redeploy de
  Production, ambos ejecutados por el responsable del proyecto, y se reverificó por sonda.
  Aprendizaje: una variable de entorno guardada no está activa hasta que hay un despliegue
  posterior, y en Vercel las `NEXT_PUBLIC_*` se congelan en el momento de la build.
- **Railway dejó de registrar sus despliegues como *deployments* de GitHub** a partir del
  commit `5e66f63`. El despliegue sí ocurre —se comprobó por el comportamiento de los
  endpoints— pero deja de haber señal externa consultable. Queda anotado porque los gates de
  migraciones de sprints futuros se apoyaban en esa señal; conviene revisar la integración
  en el panel.

### Commits relevantes
- `d375c76` feat(auth): implementar autenticación Google y roles — integración a `main` por *squash and merge* del PR #2, cuyo único commit en la rama fue `539eaa3`.

El responsable del proyecto revisó el diff y aprobó el merge; ningún cambio se integra automáticamente. Desde este sprint la implementación no se hace sobre `main`: va en rama propia y llega por Pull Request con CI en verde.

### Observaciones y desvíos respecto del plan
- **Cero migraciones**, como estaba previsto: no se tocó ningún modelo, `choices`,
  `default`, `Meta` ni relación, y `token_blacklist` no se instala precisamente para no
  crear tablas. `makemigrations --check` confirma `No changes detected`.
- **El flujo de Google es de ID token, no de Authorization Code.** Es lo que describe el
  Anexo B (entrada `token_google`) y el propio plan («intercambio del token de Google por
  JWT propio»). Como consecuencia, las variables `GOOGLE_OAUTH_CLIENT_SECRET` y
  `GOOGLE_OAUTH_REDIRECT_URI`, escritas en el Sprint 1 anticipando el otro flujo, quedaron
  sin uso y se retiraron de `.env.example`.
- **Se añade un BFF mínimo de sesión en Next.js**, no previsto explícitamente en el plan.
  El motivo es concreto: Vercel y Railway son sitios distintos, así que una cookie de
  refresh emitida por el backend sería *third-party*. Con el BFF la cookie es first-party
  y no depende de la política de cookies de terceros del navegador. El BFF es solo de
  sesión: tres Route Handlers, sin proxy general.
- **`DenyAll` en lugar de `IsAuthenticated` como permiso por defecto.** Es la lectura
  literal de «toda vista nueva debe declarar explícitamente su permiso»: con
  `IsAuthenticated`, una vista olvidada seguiría accesible a cualquier autenticado.
- **Sin revocación server-side del refresh**, y se documenta como limitación en
  `ADR-0005`: instalar `token_blacklist` habría creado dos tablas. El logout borra la
  cookie y olvida el access; el corte de emergencia es `is_active = False`.
- **`role = admin` no implica `is_staff` ni `is_superuser`.** Ninguna fuente los equipara y
  mezclarlos confundiría dos sistemas de autorización distintos.
- **Limitación estructural detectada y no corregida en este sprint:** el índice único de
  `users.email` es sensible a mayúsculas, así que `Persona@x.com` y `persona@x.com` pueden
  coexistir. Se mitiga en el servicio con `email__iexact` y guardando en minúsculas; un
  `UniqueConstraint(Lower("email"))` queda como candidato para un sprint futuro, con su
  respaldo previo. Registrado en `ADR-0005`.
- **El login no se audita**: el §30 del plan no lo incluye entre las acciones de auditoría
  obligatoria. Sí se audita la promoción de rol, que cae dentro de «administración de
  catálogos, operadores y recompensas».
- **Nada del Sprint 4 se adelantó**: no hay cédula, ni HMAC, ni selfie, ni revisión
  administrativa, y el login **no** crea `CitizenProfile`.

---

## Sprint 4 — Verificación de identidad y privacidad

**Fechas planificadas:** 22/08/2026 – 28/08/2026
**Fecha real de inicio:** 22/08/2026
**Estado:** Completado
**Fecha real de cierre:** 23/08/2026 (implementación, tres Pull Requests con CI en verde, migración productiva, prueba real de extremo a extremo con una identidad verdadera, migración del transporte de correo a API HTTPS y limpieza de las credenciales SMTP legacy)

### Avance al 22/08/2026 (implementación local completa, sin desplegar)

El diseño se cerró en dos fases —A y A.1— con **doce decisiones aprobadas** antes de
escribir código, registradas en `ADR-0006` y `ADR-0011`. La implementación se ejecutó en
cinco fases sobre la rama `feat/s04-verificacion-identidad`, **sin ningún commit todavía**.

**Terminado y verificado en local:**

- **Fundaciones backend** (Fase B): `citizen_profiles.rejection_reason` y su migración,
  permiso `IsCitizen`, ajustes de identidad y correo, normalización de la cédula,
  HMAC-SHA-256, validación de la selfie con Pillow y cliente de Supabase Storage.
- **Flujo ciudadano** (Fase C): envío, consulta del estado propio, reenvío tras rechazo
  con bloqueo explícito de filas y relectura bajo el lock, compensación de Storage y
  *throttle* del envío.
- **Privacidad del ranking** (Fases C y E.1): las cuatro modalidades se **guardan y se
  recuperan**; `GET` y `PATCH` sobre la misma ruta.
- **Revisión administrativa** (Fase D): cola, detalle con URL firmada de 300 s, aprobación
  y rechazo con motivo, concurrencia entre dos administradores, auditoría y notificación
  en la misma transacción.
- **Notificaciones** (Fase D): fila en `notifications`, tarea de Celery idempotente y sin
  reintentos, dos plantillas de texto plano, y tratamiento de los fallos de broker y del
  proveedor de correo sin tumbar la revisión. **La entrega se hace por la API HTTPS de
  Brevo, no por SMTP** (ADR-0015): el plan Free de Railway no ofrece SMTP saliente, y la
  implementación inicial con `send_mail` se quedaba colgada hasta agotar el timeout.
- **Frontend** (Fase E): pantallas de verificación, perfil, cola e detalle administrativo,
  con navegación mínima y sin dependencias npm nuevas.
- **Documentación**: `docs/API.md` nuevo, `ADR-0006`, `ADR-0011` y `ADR-0015`, diccionario
  de datos actualizado y siete filas nuevas en `CAMBIOS_TESIS_PENDIENTES.md` (CT-44 a
  CT-50).

**Los cinco puntos que quedaban para cerrar el sprint se completaron el 23/08/2026:**
infraestructura productiva aprovisionada —bucket privado `verificaciones`, variables del
backend web, y `BREVO_API_KEY` / `BREVO_API_URL` / `DEFAULT_FROM_EMAIL` del worker—; tres
Pull Requests con CI en verde; respaldo previo y migración productiva de `auth_app.0004`
bajo el gate de ADR-0003; prueba funcional real de extremo a extremo con una identidad
verdadera; y este cierre documental.

### Funcionalidades terminadas
- **Verificación de identidad del ciudadano.** El número de cédula se recibe como texto, se
  normaliza —dígitos ASCII y los separadores `.`, `-` y espacio, con los ceros iniciales
  preservados— y se persiste **únicamente** como HMAC-SHA-256 con clave del servidor. El
  número textual no queda en la base, ni en logs, ni en la auditoría, ni en ninguna
  respuesta. La **selfie sí muestra la cédula**, así que vive en un bucket privado y solo
  un administrador la ve mediante URL firmada efímera (RF-02, ADR-0006).
- **Almacenamiento privado validado en producción.** Bucket `verificaciones` privado en
  Supabase Storage; JPEG o PNG hasta 5 MB con el formato determinado por el **contenido
  real** y no por el nombre del archivo; URL firmada de **300 s** emitida solo al abrir el
  detalle administrativo. `SUPABASE_URL` se corrigió a la URL base durante el
  aprovisionamiento. Se conserva la `service_role` legacy por compatibilidad del cliente;
  la migración a `sb_secret_*` queda diferida.
- **Revisión administrativa manual, con cotejo de la cédula.** Cola filtrable, detalle con
  la selfie firmada, y aprobación o rechazo con motivo en texto libre. Para **aprobar**, el
  administrador transcribe el número que ve en la selfie: el backend lo normaliza con la
  **misma** función del envío ciudadano, calcula su HMAC con el mismo secreto y lo compara
  con `hmac.compare_digest` contra `citizen_profiles.document_hmac`, dentro del bloqueo de
  la revisión. Sin coincidencia no hay aprobación. El número transcrito **no se persiste,
  no se audita y no vuelve en la respuesta**. El rechazo no lo pide, porque el motivo más
  frecuente de un rechazo es que la cédula no se distinga en la imagen.
- **Reenvío tras rechazo**, con bloqueo explícito de las filas de `citizen_profiles` y
  `users`, relectura del estado bajo el lock y compensación en Storage.
- **Notificaciones asíncronas.** Fila en `notifications` dentro de la transacción de la
  decisión y envío encolado en `on_commit`. Ni un broker caído ni un fallo del proveedor
  tumban la revisión: la notificación queda en `FALLIDO`, que es un estado consultable.
- **Correo transaccional por API HTTPS de Brevo**, no por SMTP (ADR-0015). Cliente propio
  con `requests`, sin SDK, con timeout explícito y fallo cerrado sin clave. Se conservan el
  modelo, la tarea de Celery, las plantillas de texto plano y la semántica
  `PENDIENTE`/`ENVIADO`/`FALLIDO`. Sin reintentos automáticos: siguen siendo alcance de
  S12-S13.
- **Reenvío manual auditable** de una notificación fallida:
  `python manage.py resend_notification <uuid>`. Exige `FALLIDO`, devuelve la fila a
  `PENDIENTE` con la fila bloqueada, lo registra como `notification.resent` con
  `actor_type = system` y encola el envío en `on_commit`. Sin endpoint y sin acción en el
  admin, a propósito (CT-51).
- **Privacidad configurable del ranking**: las cuatro modalidades se guardan y se
  recuperan mediante `GET` y `PATCH` sobre la misma ruta; el alias no es único a nivel
  global (ADR-0011).
- **`IsCitizen`** como sexto permiso de dominio, sin alterar la matriz rol × capacidad.
- **Frontend**: pantallas de verificación, perfil, cola y detalle administrativo, sin
  dependencias npm nuevas y sin ninguna variable de Supabase en el navegador.

### Pruebas realizadas
- **Resultado final: 712 pruebas verdes y 99 % de cobertura** (5.509 sentencias, 18 sin
  cubrir), con `notifications_app` al 100 %. Progresión a lo largo del sprint: 582 al
  cerrar la implementación local, 627 tras el cotejo de la cédula, 690 tras la migración a
  Brevo HTTPS y 712 con el reenvío manual.
- `ruff check`, `ruff format --check` y `makemigrations --check --no-input` en verde en
  cada uno de los tres Pull Requests. Frontend con `format:check`, `lint`, `typecheck` y
  `build` en verde.
- **CI en verde en los tres PR** (#5, #6 y #7), reproduciendo las mismas cifras que en
  local. `CI Frontend` no se dispara en los PR que no tocan el frontend: el workflow filtra
  por rutas.
- **Ninguna prueba sale a la red.** Storage está doblado, la verificación de Google
  también, y el cliente de Brevo se sustituye con una fixture `autouse` acotada al módulo
  `brevo` —no al `requests` compartido—, con dos pruebas que fijan ese límite.
- **Prueba real de extremo a extremo en producción (23/08/2026)** con una identidad
  verdadera: envío ciudadano, revisión administrativa con cotejo del número, aprobación,
  y correo de aprobación efectivamente recibido en Gmail. La cuenta administrativa se
  promovió por consola con `promote_user`, auditada como acción de sistema.
- **Respaldos lógicos** previos y posteriores validados con `pg_restore --list` y
  verificados por checksum: tres PRE —uno antes de cada integración— y un POST final tras
  la recuperación de la notificación.

### Problemas encontrados
Los tres se detectaron en la validación E2E productiva del **23/08/2026** y los tres
quedaron resueltos antes del cierre:

1. **El administrador no podía cotejar la cédula.** Podía leer el número en la selfie pero
   no tenía forma de comprobar que fuera el declarado, porque el número textual no se
   persiste. Resuelto exigiendo la transcripción del número en la aprobación y cotejándolo
   por HMAC (enmienda de ADR-0006, fila CT-49).
2. **El correo de aprobación no llegó, pese a que la aprobación sí se aplicó.** El
   `celery-worker` registró `Falló el envío de la notificación … (TimeoutError)` tras
   **135,7 s**, y la notificación quedó en `FALLIDO`. Causa: **Railway no permite SMTP
   saliente en los planes Free, Trial ni Hobby**; la conexión no se rechaza, se queda
   colgada. No era un defecto de la implementación ni de las credenciales: el transporte
   elegido no existe en el plan contratado. Resuelto sustituyendo **solo el transporte**
   por la API HTTPS de Brevo, conservando `Notification`, Celery, la tarea, las plantillas
   y la semántica de estados (ADR-0015, fila CT-50). **Sin migración y sin cambio de
   esquema.**

3. **La notificación fallida no tenía forma de recuperarse.** `FALLIDO` es terminal por
   diseño —`dispatch.send()` se retira sin enviar si el estado no es `PENDIENTE`— y eso
   protege de convertir un fallo en un bucle de correos, pero dejaba sin salida al mensaje
   que ya había fallado: reencolar la tarea no reenvía nada. Resuelto añadiendo
   `resend_notification`, una puerta explícita, auditada y de una sola notificación por
   ejecución (ADR-0015 §7, fila CT-51).

   La notificación real de la aprobación se recuperó con ese comando, ejecutado **una sola
   vez**: pasó a `ENVIADO`, el correo llegó y quedó una única entrada
   `notification.resent` con `actor_type = system`.

### Commits relevantes
- `9234e47` feat(identity): implementar verificación de identidad del Sprint 4 — integración
  a `main` por *squash and merge* del PR #4, desde `feat/s04-verificacion-identidad`.
- `66d30f7` fix(identity): verificar cedula al aprobar identidad — PR #5, desde
  `fix/s04-verificacion-cedula-admin`. Único commit en la rama: `7ab8975`.
- `92e0d53` fix(notifications): usar Brevo API HTTPS para correo — PR #6, desde
  `fix/s04-brevo-https-email`. Único commit en la rama: `987b444`.
- `666cc8c` fix(notifications): agregar reenvio manual auditable — PR #7, desde
  `fix/s04-resend-notification`. Único commit en la rama: `27cef32`.

Los cuatro llegaron por Pull Request con CI en verde y *squash and merge*, con el SHA
aprobado fijado explícitamente en el merge. El responsable del proyecto revisó cada diff;
ningún cambio se integró automáticamente. Ninguna rama se borró tras el merge.

Los tres últimos son **correcciones nacidas de la validación en producción**, no alcance
nuevo: el sprint se desplegó, se probó de verdad, y lo que la prueba destapó se arregló
antes de cerrarlo.

### Observaciones y desvíos respecto del plan
- **`IsCitizen` como sexto permiso** sobre los cinco que enumera el plan —necesario porque
  quien envía su verificación todavía no está verificado—, sin alterar la matriz rol ×
  capacidad del §29.
- **Adiciones al contrato del Anexo B**: `GET /api/v1/admin/identity/{id}/` y
  `GET /api/v1/profile/ranking-visibility/` (CT-47 y CT-48), más el campo
  `document_number` en la aprobación (CT-49). La ruta `(citizen)/verificacion` tampoco
  estaba enumerada en el §7 del plan.
- **Una sola migración, aditiva**: `auth_app.0004_citizen_profile_rejection_reason`. Los
  tres hotfixes posteriores no tocaron el esquema: cero migraciones y cero columnas nuevas.
- **El plan daba SMTP y servicio transaccional por intercambiables; en el alojamiento
  contratado no lo son.** Railway no permite SMTP saliente en los planes Free, Trial ni
  Hobby. Es una restricción del proveedor gestionado que el diseño no podía anticipar, de
  la misma clase que las de CT-39 a CT-41, y quedó registrada en CT-50.
- **La validación en producción con datos reales fue la que encontró los tres defectos.**
  Ninguno era visible en local con 582 pruebas en verde: el cotejo ausente de la cédula, el
  SMTP inalcanzable y la falta de recuperación de una notificación fallida solo aparecen
  cuando el flujo se ejecuta de punta a punta contra la infraestructura real. Conviene
  tenerlo presente al planificar los sprints siguientes: el criterio de «terminado» del
  proyecto incluye la prueba real, no solo la suite.
- **Limpieza operativa completada**: las cinco variables SMTP legacy se retiraron de
  Railway y la credencial SMTP antigua se revocó en Brevo, ya con el transporte HTTPS
  validado en producción. La clave de API HTTPS sigue activa y no se creó ninguna
  credencial de reemplazo. Queda pendiente, sin urgencia, la migración de la
  `service_role` legacy de Supabase a `sb_secret_*`.

---

## Sprint 5 — Mapa público y geolocalización

**Fechas planificadas:** 29/08/2026 – 04/09/2026
**Estado:** Completado
**Fecha real de cierre:** 27/08/2026

### Funcionalidades terminadas

- **S5-A cerrada:** tres rutas anónimas de solo lectura:
  `GET /api/v1/public/reports/`, `GET /api/v1/public/reports/{id}/` y
  `GET /api/v1/public/categories/`.
- Lista positiva de seis estados publicables; filtros `active`, `resolved` y `all`, con
  activos priorizados, orden estable y `RECHAZADO` opaco como `404` en detalle.
- Contrato público mediante lista blanca, coordenadas numéricas redondeadas a tres
  decimales y ninguna información del autor.
- Bbox obligatorio `west,south,east,north`, máximo `0.5°` por eje y expansión de
  privacidad a la cuadrícula desplazada medio paso (`n × 0.001° + 0.0005°`).
- Consulta PostGIS con `location__bboverlaps`/`&&` como prefiltro GIST y
  `ST_Intersects` como recheck exacto de fronteras, sin transformar ni extraer
  coordenadas de la columna; prueba separada de elección real con cardinalidad
  representativa.
- RedisCache nativo, TTL de 60 segundos, listados solamente, claves generacionales e
  invalidación O(1) con degradación a PostgreSQL.
- Throttle compartido `public_map = 120/min` y LocMemCache obligatorio en tests.
- Management command idempotente `seed_reports`: 14 snapshots sintéticos, UUID v5, dos
  por estado, sin usuarios, historiales ni migración de datos.
- **S5-B cerrada:** rutas públicas `/mapa` y `/reporte/[id]`, con acceso
  visible desde la cabecera y sin exigir autenticación.
- Leaflet 1.9.4 y React Leaflet 5 cargados solo en cliente mediante `next/dynamic` con
  `ssr: false`; tiles HTTPS oficiales de OpenStreetMap, atribución permanente y sin
  precarga, descarga offline ni proxy.
- Cliente HTTP público separado, sin JWT, refresh, cookies ni almacenamiento local; bbox
  limitado a `0.5°`, debounce de 350 ms, cancelación y secuencia contra respuestas tardías.
- Paginación completa y secuencial con validación same-origin y detección de ciclos;
  Supercluster directo para marcadores y clusters pointer, acompañado por una lista textual
  completa como vía principal de teclado.
- Filtros compartibles `estado`, `categoria` y `reporte`; panel lateral en escritorio,
  ficha inferior no modal en móvil, restauración de foco, cierre con Escape y lista
  textual visible de reportes. Un deep-link a un reporte público fuera del bbox inicial
  consulta su detalle, centra el mapa con sus coordenadas públicas y recarga el listado normal.
- Detalle público sin mapa ni coordenadas numéricas, con estados diferenciados para
  carga, `404`, `429` y error general. S5-B no incorpora acciones de confirmación,
  edición ni voto.
- **S5-C cerrada:** LocationPicker reutilizable y controlado, sin
  acoplamiento a Report ni a rutas de creación. Permite seleccionar ubicación exacta
  mediante click en el mapa, marcador arrastrable o campos de latitud/longitud operables
  por teclado.
- La geolocalización se solicita exclusivamente tras pulsar «Usar mi ubicación» mediante
  getCurrentPosition, con estados diferenciados y recuperación manual. Una selección
  manual o un cambio externo posterior invalida cualquier respuesta GPS tardía.
- El valor canónico conserva su precisión; el formato visible no reemplaza coordenadas
  no editadas. No se usan URL, storage, logs, telemetría ni llamadas al backend.
- OSM y el observador de resize se extrajeron a módulos neutrales compartidos; el mapa
  público conserva su semántica y S5-C no incorpora geocoder.

### Pruebas realizadas

- Suite focalizada `map_app` + `seed_reports`: **114 pruebas verdes** sobre privacidad,
  bbox, estados, categorías, HTTP, paginación, caché, throttle, seed y planes espaciales.
- Regresión completa local: **826 pruebas verdes** y **99 % de cobertura** sobre `apps`.
- `ruff check .`, `ruff format --check .`, `makemigrations --check --no-input` y
  `git diff --check`: sin hallazgos al cerrar la implementación local.
- Frontend S5-B: **77 pruebas verdes** antes de iniciar S5-C, cubriendo bbox,
  query params, cliente anónimo, paginación, cancelación, clustering, filtros, estados UX,
  ficha, lista accesible y detalle. `npm run format:check`, `npm run lint`,
  `npm run typecheck` en verde; el build de producción con `npm run build -- --webpack`
  quedó verde en la validación local previa al gate adversarial.
- Frontend tras S5-C: **172 pruebas verdes** en 13 archivos, incluidos precisión canónica,
  borradores, selección por click y drag, GPS y carreras asíncronas, control externo,
  accesibilidad, resize y regresión del mapa público.
- Gate adversarial de S5-C: **95/95 pruebas focalizadas verdes**. CI Frontend ejecutó
  `next build` con Turbopack y terminó en `SUCCESS`; Vercel Production quedó en `SUCCESS`.
- Cierre productivo de S5-B: **5/5 reproducciones correctas**, lista recuperada y foco
  nunca terminado en `body`. El smoke final de `/mapa` no detectó regresiones tras S5-C.
- Los **31/31 criterios técnicos de S5-C** quedaron aprobados después del merge.

### Problemas encontrados

- Redondear solo la respuesta dejaba un oráculo por consultas bbox adaptativas. Se cerró
  canonizando la consulta a las fronteras half-step de la misma celda pública.
- El gate adversarial pre-commit detectó que `&&`, al trabajar con cajas aproximadas,
  incluía algunos empates ubicados sobre un borde lógicamente abierto. Se conservó como
  prefiltro GIST y se añadió el recheck exacto `ST_Intersects`. El mismo gate corrigió
  los enlaces de página cuyo bbox canónico podía medir `0.501°` y ser rechazado al
  reutilizarse.
- El plan pedía filtro por categoría sin ofrecer un catálogo público completo. Se agregó
  el endpoint mínimo de categorías activas y la divergencia quedó en CT-53.
- `CACHES` no existía y Redis comparte base lógica con Celery. Producción nunca usa
  `cache.clear()`; la suite quedó aislada con LocMemCache.
- El primer ajuste de bounds de Leaflet puede producir un viewport distinto según la
  relación de aspecto del contenedor. La inicialización comprueba `map.getBounds()` y
  acerca progresivamente hasta respetar el máximo consultable, sin asumir un zoom fijo.
- Leaflet no puede ejecutarse durante SSR. El Server Component de `/mapa` permanece
  delgado y un componente cliente concentra la importación dinámica, con skeleton propio
  mientras carga el chunk.
- `Marker.keyboard` de Leaflet 1.9.4 agrega foco y semántica de botón a un `DivIcon`, pero
  no implementa activación robusta por Enter y Space. Para no publicar controles de teclado
  inoperables ni añadir hacks DOM, los iconos quedan pointer-only y la lista textual visible
  ofrece selección y detalle completos mediante teclado.
- El runner restringido de la auditoría bloqueó subprocesses internos de Turbopack y, en
  otra ejecución, de TypeScript. No eran defectos del código: el build Webpack quedó verde
  en el host normal y GitHub Actions confirmó `next build` con Turbopack en `SUCCESS`.

### Commits relevantes

- S5-A integrada a `main` en `d8bfd6f84c62a6427aba67b91f332f6940d5b540` y validada
  posteriormente en producción; S5-A quedó cerrada.
- S5-B cerrada mediante PR #12, integrada y validada en producción en el squash commit
  `27a9e1cd460bb12a81bf71654e4d1f9d8bcab62e`.
- S5-C cerrada mediante PR #13, integrado el 27/08/2026 en el squash commit
  `0e33ca4bf330b0557f35ee45d302d70b5932ea8b`.

### Observaciones y desvíos respecto del plan

- Al cerrar Sprint 5, S5-A, S5-B y S5-C estaban cerradas y S6 todavía no se había
  iniciado. Sprint 6 comenzó y quedó cerrado el 28/08/2026 tras completar S6-A y S6-B.
- `LocationPicker` permanece integrado desde S5-C. En el cierre de S5-C su consumo por el
  formulario real y el E2E browser se habían diferido a S6-B; ambos quedaron posteriormente
  integrados y validados sin exigir una ruta demo retroactiva.
- El detalle público y el catálogo de categorías no figuran ambos en el Anexo B; se
  registraron CT-52 y CT-53. La reducción de precisión y la canonización bbox quedaron en
  CT-54.
- S5-C no toca backend, modelos, tablas, migraciones, datos productivos ni páginas de S6.

---

## Sprint 6 — Creación de reportes y duplicados

**Fechas planificadas:** 05/09/2026 – 11/09/2026
**Fecha real de inicio:** 28/08/2026
**Estado:** Completado — S6-A y S6-B cerrados, integrados y validados en producción
**Fecha real de cierre:** 28/08/2026

### Funcionalidades terminadas

- **S6-A — backend de creación de reportes: CERRADO.** `POST /api/v1/reports/` exige
  identidad ciudadana aprobada y un multipart estricto con categoría, descripción, foto y
  coordenadas. La creación persiste atómicamente Report, una fotografía, el historial
  inicial, `REPORT_CREATED` con +20/+20 `PENDIENTE` y el AuditLog correspondiente.
- La ubicación se conserva como `Point` SRID 4326 con precisión completa; la API pública
  mantiene su política independiente de redondeo a tres decimales y no expone ciudadano,
  media, hashes ni coordenadas precisas.
- Las fotos JPEG/PNG de hasta 5 MiB se validan por contenido, se normalizan sin metadata y
  se almacenan en el bucket privado `reportes`. El backend tiene configurado
  `SUPABASE_STORAGE_BUCKET_REPORTS=reportes`; el worker no necesita esta variable.
- La unicidad del SHA-256 de los bytes originales se protege con el índice único parcial
  `report_media_foto_hash_unico`; el upload previo a la transacción tiene compensación
  acotada ante un fallo posterior.
- Tras el commit se invalida la generación de caché pública y se encola
  `ai.classify_report`. La tarea fue recibida y completada en producción, pero en S6 es
  deliberadamente **no-op**: no llama Gemini, no clasifica y no crea `AIClassification`.
- **S6-B1 — contrato frontend de creación: INTEGRADO EN MAIN.** Incluye tipos separados del
  mapa público, validaciones puras, construcción exacta del multipart, preflight de sesión y
  un único POST sin replay tras 401, errores controlados —incluido resultado incierto—, lock
  `singleFlight`, reducer de cinco pasos y cliente GET cancelable de posibles duplicados.
- **S6-B2 — wizard frontend de creación: INTEGRADO EN MAIN.** Añade la ruta ciudadana
  `/reportes/nuevo`, guards de sesión/rol/verificación, catálogo con retry GET, cinco pasos
  accesibles, foto JPEG/PNG con preview local y revocación de object URLs, `LocationPicker`
  sin pérdida de precisión, confirmación con consulta cancelable de duplicados, submit
  single-flight, éxito y UX controlada para errores HTTP y resultado incierto.
- **S6-B3 — browser E2E, integración y documentación: INTEGRADO EN MAIN.** Incorpora
  Playwright 1.62.1 y Chromium con Next/Webpack local, API sintética interceptada, retries en cero
  y artefactos solo ante fallo. Cubre guards, multipart exacto, foto, GPS permitido/denegado,
  coordenadas por teclado, click y drag Leaflet, viewport 390 px, duplicados advisory, doble click,
  Enter repetido, 401/409/429/503, resultado incierto y accesibilidad/foco. El workflow frontend
  queda preparado con un job `frontend-e2e` separado y sin secretos o servicios productivos.

### Pruebas realizadas

- Frontend tras S6-B3: **246 pruebas unitarias/componentes/integración verdes en 20 archivos**;
  B2 agregó **29 pruebas** de
  guards, transiciones, catálogo, descripción, lifecycle de preview, límite de 5 MiB,
  integración precisa de ubicación, duplicados cancelables/no bloqueantes, doble click, Enter,
  éxito y errores HTTP/resultado incierto. S6-B3 añade **22 browser E2E**; dos ejecuciones
  completas consecutivas terminaron `22/22` en aproximadamente 1,5 minutos cada una, con cero
  retries y cero flakes. Prettier, ESLint y TypeScript quedaron verdes. Aunque el build Webpack no
  pudo revalidarse en el runner local restringido, la integración de B3 quedó confirmada por CI:
  `build` y `frontend-e2e` terminaron en `SUCCESS`, al igual que Vercel.
- Implementación backend: **994 pruebas verdes**, **99 % de cobertura**, `manage.py check`,
  `makemigrations --check`, Ruff y CI Backend en `SUCCESS`.
- La migración `reports_app.0002_reportmedia_report_media_foto_hash_unico` quedó aplicada
  en producción. El índice parcial se verificó único, válido y listo, sobre `file_hash`
  cuando `media_type = 'FOTO'`.
- Backend y worker quedaron `Online` en Railway. El pre-deploy ejecutó
  `migrate --noinput` sin migraciones pendientes después de activar la configuración de
  Storage.
- En el gate de S6-A se ejecutó **exactamente una** creación productiva controlada: HTTP `201`, estado
  `CREADO` y reward +20 puntos/+20 créditos `PENDIENTE`. Los deltas fueron exactamente
  +1 Report, +1 ReportMedia, +1 History, +1 RewardTransaction, +1 AuditLog y +1 objeto PNG
  privado; Notifications y AIClassifications permanecieron sin cambios.
- PostgreSQL/PostGIS, media, historial, reward, auditoría, rotación de caché y ejecución
  Celery se verificaron de extremo a extremo. El reporte apareció de inmediato en la API
  pública con la whitelist y reducción de precisión previstas, sin datos privados.
- El gate final de S6-B recorrió el wizard real en `/reportes/nuevo`, incluido el warning advisory
  de duplicado y la decisión de continuar. Produjo exactamente un submit y un único
  `POST /api/v1/reports/`, HTTP `201`, sin retry. Se verificaron los mismos deltas unitarios,
  precisión PostGIS exacta, fotografía en Storage privado, History, Reward, Audit, caché, Celery y
  API pública privacy-safe; Notifications y AIClassifications permanecieron sin cambios.

### Problemas encontrados

- El build Turbopack local llegó a compilación pero no pudo descargar Poppins desde Google Fonts
  por la restricción de red del runner. En B3 el único intento Turbopack reprodujo exclusivamente
  ese fallo de descarga. Webpack quedó bloqueado localmente al interpretar el subprocess de
  TypeScript y no se modificaron fuentes para maquillar ninguna de estas condiciones ambientales.
- El browser real detectó tres defectos frontend que las pruebas de componente no exponían: un
  `401` posterior al POST desmontaba el wizard y perdía el borrador; aplicar coordenadas manuales
  lejanas podía dejar el marcador fuera del viewport del mapa; y la fila de sesión autenticada
  desbordaba a 390 px. Se corrigieron e integraron preservando el error dentro del formulario, con
  recentrado explícito tras Apply y wrapping responsive de la cabecera, respectivamente.
- El despliegue inicial no tenía creado el bucket `reportes` ni configurada
  `SUPABASE_STORAGE_BUCKET_REPORTS`. Se aprovisionó un bucket privado limitado a JPEG/PNG y
  5 MiB, se configuró únicamente el backend y se validó su uso real sin exponer secretos.
- Producción no dispone de una identidad QA aprobada. Para los gates controlados S6-A y S6-B se
  autorizó explícitamente usar la misma identidad real ya verificada, sin modificarla ni revelar
  sus credenciales; no se creó ningún usuario o perfil adicional.

### Commits relevantes

- PR #15 integrado mediante *squash and merge* en
  `17983b28b0d2e60d71d4bb096402869446d6e28b`
  (`feat(reports): implementar creación de reportes`).
- PR #16 integrado mediante *squash and merge* en
  `6971e4d19fd55e1ca02442bdd7a4ecb86e18259f`
  (`docs(sprint): cerrar S6-A`).
- PR #17 integrado mediante *squash and merge* en
  `e585c2861c84a972da7adeef8676cd942fec018b`
  (`feat(reports): preparar creación frontend segura`).
- PR #18 integrado mediante *squash and merge* en
  `5b71036f262c21ce512abc46b8f5aaa945cf58f0`
  (`feat(reports): implementar wizard de creación`).
- PR #19 integrado mediante *squash and merge* en
  `34290576bb00af2a88bc9c4d26ed1f011f97a263`
  (`test(reports): completar E2E de creación`).

### Observaciones y desvíos respecto del plan

- Evidencia productiva conservada intencionalmente: correlación
  `S6A-E2E-20260828-021516`, Report
  `31cb4fe3-ff0f-48dc-ae08-07ae227fd619`. Es un reporte sintético marcado expresamente
  como «NO ES UNA INCIDENCIA REAL» y corresponde a la única creación del gate S6-A.
- Evidencia productiva de S6-B conservada intencionalmente: correlación
  `S6B-E2E-20260828-001`, Report `92210415-68d7-44fd-b094-cffbd1d8e181`. Es un reporte
  sintético que no representa una incidencia real; fue creado desde la UI productiva mediante
  exactamente un submit y sin retry. La persistencia completa, el objeto privado, la precisión
  PostGIS, la recompensa `PENDIENTE` 20/20, Audit, History, caché, Celery y privacidad pública
  quedaron verificados.
- Ninguno de los dos gates produjo Notification, Brevo, email, push o AIClassification.
  `ai.classify_report` se publicó, recibió y completó, pero continúa inequívocamente como
  **no-op**: no invoca Gemini. La clasificación real corresponde al Sprint 8.
- La migración de la credencial legacy `service_role` de Supabase a `sb_secret_*` no se
  inició y permanece fuera de este cierre.
- Los E2E de S6-B3 usan frontend y Chromium reales, pero interceptan únicamente las fronteras API
  con identidad, tokens, imágenes, coordenadas y respuestas sintéticas. No conectan a Railway,
  Supabase o Storage, y no realizan ningún POST remoto.
- La advertencia de GitHub Actions sobre la transición de Node 20 a 24 permanece como deuda CI no
  bloqueante. S6-B3 no actualiza `actions/checkout@v4` ni `actions/setup-node@v4` para silenciarla.
- La consulta backend de posibles duplicados exige `lat`/`lng` precisos en query string. El
  cliente B1 los usa exclusivamente en un `fetch` GET privado y no los coloca en navegación,
  historial, storage ni logs; la exposición potencial en logs de infraestructura pertenece al
  contrato backend ya cerrado y queda como riesgo de privacidad para revisión posterior.
- S6-B1, S6-B2 y S6-B3 están integrados; el gate productivo UI fue exitoso. **S6-B y Sprint 6
  quedan cerrados.** Al cierre de Sprint 6, Sprint 7 permanecía pendiente y aún no había sido
  iniciado.

---

## Sprint 7 — Panel ciudadano y confirmaciones

**Fechas planificadas:** 12/09/2026 – 18/09/2026
**Estado:** Completado — S7-A, S7-B y S7-C integrados en `main`; S7-D probado y cerrado en producción
**Fecha real de cierre:** 29/08/2026

### Funcionalidades terminadas
- **S7-A integrado en `main` mediante el PR #21 (squash `4ed5a89894432b2d047ee33fadd376066562aa3f`):** contrato backend de confirmaciones, reward pendiente,
  contador público en detalle, detalle contextual autenticado, history owner-only, resumen
  ciudadano y admin read-only.
- **S7-B integrado en `main` mediante el PR #22 (squash `9c6a2c9f19809b93cacecb774e1ff2f416c06dca`):** ruta `/panel` para ciudadano aprobado con resumen de reportes
  y puntos de impacto, distribución de todos los estados, confirmaciones realizadas, listado
  paginado de reportes propios y seguimiento contextual con timeline owner-only. Incluye parsing
  estricto de los cuatro contratos GET, estados de auth/loading/empty/error, retry manual,
  cancelación y protección ante respuestas tardías, navegación accesible y layout responsive.
  No incluye la acción social de confirmación ni ninguna llamada `POST`.
- **S7-C integrado en `main` mediante el PR #23 (squash `832087de937e2e3d75a7353a58eb6bb4de6d99f2`):** el detalle público muestra siempre el contador y mantiene su
  acceso anónimo. Ciudadanos aprobados consultan el contexto privado de elegibilidad y pueden usar
  el CTA “Confirmo que el problema sigue ahí”. El POST vacío aplica preflight, single-flight y cero
  replay; el 201 actualiza contador y muestra +10 puntos pendientes. Errores, conflictos y resultado
  incierto permanecen controlados, con reconciliación exclusivamente mediante GET seguro. Se
  añadieron Browser E2E con dos identidades sintéticas, sin cuentas ni servicios productivos.
- **S7-D completado en producción:** se incorporó y verificó una segunda identidad ciudadana real e
  independiente mediante el flujo oficial de alta, solicitud y aprobación; ambos actores quedaron
  listos y distintos. El preflight corregido S7-D-R0b seleccionó un reporte sintético activo del
  ciudadano propietario y congeló los baselines sin mutaciones de dominio. S7-D-W1 ejecutó una sola
  confirmación del ciudadano confirmador, con HTTP 201 y cero retry. S7-D-R1 verificó el estado
  funcional posterior y los tres agregados internos mediante una transacción productiva
  explícitamente `READ ONLY`, finalizada con `ROLLBACK` y sin escrituras.

### Pruebas realizadas
- Suite local S7-A sobre PostgreSQL/PostGIS: reglas y prioridad, atomicidad, constraint e
  `IntegrityError`, concurrencia con dos conexiones, auth, throttle, privacidad, query budgets,
  detalle público/privado, history, summary y admin.
- Suite frontend S7-B: parsing válido e inválido, errores HTTP/red, guards, resumen, todos los
  estados, listado y empty state, paginación, timeline, reintentos, doble acción, presupuesto de
  requests y carrera de selección. Vitest quedó en **280/280** y la regresión browser S6 en
  **22/22**; Prettier, ESLint, TypeScript y el build Webpack canónico terminaron en verde.
- Frontend tras S7-C: **326/326 Vitest en 27 archivos**. Playwright incorpora **24 escenarios
  S7-C** y conserva los **22 de S6**; dos corridas completas consecutivas terminaron **46/46**, con
  `retries=0` y sin flakes. Prettier, ESLint, Next typegen, TypeScript, Webpack y `npm ls --depth=0`
  quedaron verdes.
- Gate productivo S7-D: S7-D-R0b quedó **GO** con ambos actores listos e independientes, ownership
  demostrado sobre el reporte sintético `31cb4fe3-ff0f-48dc-ae08-07ae227fd619`, estado baseline
  `CREADO`, `confirmation_count=0`, `confirmed_by_me=false`,
  `can_confirm=true`, `eligibility.code=null`, `confirmations_performed=0`,
  `points.pending=0` y `points.accredited=0`.
- La única confirmación productiva terminó con HTTP 201: `confirmation_count` cambió de 0 a 1,
  `confirmed_by_me` de `false` a `true`, `can_confirm` de `true` a `false` y el código a
  `ALREADY_CONFIRMED`; `confirmations_performed` pasó de 0 a 1, `points.pending` de 0 a 10 y
  `points.accredited` permaneció en 0. El reporte continuó `CREADO`.
- S7-D-R1 quedó **PASS** y `READY_FOR_S7_D_CLOSURE=true`. La evidencia SQL productiva confirmó, en
  modo `READ ONLY`, exactamente un `ReportConfirmation`, un `RewardTransaction` y un
  `AuditLog` `report.confirmed`, con referencias cruzadas consistentes y cero escrituras. El reward
  es `CONFIRMATION_CREATED`, 10/10 y `PENDIENTE`; su `report_id` es `NULL` por diseño y los 10 de
  `credit_delta` son ledger técnico, no créditos disponibles ni acreditados.

### Problemas encontrados
- La primera corrida focal S7-C terminó 19/23 porque un test intentaba enviar Enter sobre un botón
  ya reemplazado tras el primer evento y tres selectores globales confundían el alert de la acción
  con el route announcer de Next. Se acotaron los selectores a la región semántica y se usó teclado
  global; los cuatro casos corregidos y las dos suites completas posteriores quedaron verdes.
- El primer S7-D-R0 terminó **NO-GO** con `POST_COUNT_NONZERO_AUTH_REFRESH`: el frontend renovó la
  sesión mediante el POST técnico `/api/auth/refresh`, sin mutación de dominio. El protocolo se
  corrigió separando `AUTH_REFRESH` de `DOMAIN_MUTATION`; el resultado inicial no se reinterpretó
  retroactivamente y S7-D-R0b repitió el preflight con baselines frescos antes de quedar GO.

### Commits relevantes
- PR #21 integrado mediante *squash and merge* en
  `4ed5a89894432b2d047ee33fadd376066562aa3f`.
- PR #22 integrado mediante *squash and merge* en
  `9c6a2c9f19809b93cacecb774e1ff2f416c06dca`.
- PR #23 integrado mediante *squash and merge* en
  `832087de937e2e3d75a7353a58eb6bb4de6d99f2`.

### Observaciones y desvíos respecto del plan
- S7-D-I realizó tres escrituras productivas de identidad estrictamente acotadas: alta mediante
  Google, solicitud de verificación y aprobación administrativa individual. La documentación no
  conserva PII, credenciales, identificadores de actores ni documentos.
- S7-D-W1 realizó exactamente una confirmación productiva y cero retry. R1 no creó una segunda
  confirmación ni ejecutó POST de dominio, PUT, PATCH o DELETE; tampoco realizó acreditaciones,
  rewards manuales ni cambios de identidad posteriores.
- La evidencia de base de datos usó únicamente `SELECT` dentro de una transacción `READ ONLY` y
  terminó con `ROLLBACK`: cero `INSERT`, `UPDATE`, `DELETE` o DDL. Confirmó un agregado de cada tipo
  y que el payload de auditoría contiene exclusivamente `report_id` y `confirmation_id`, sin
  exponer este último.
- S7-A, S7-B y S7-C están integrados en `main`; S7-D está completado y
  `READY_FOR_S7_D_CLOSURE=true`. **Sprint 7 queda cerrado.**

---

## Sprint 8 — Clasificación asistida por IA

**Fechas planificadas:** 19/09/2026 – 25/09/2026
**Estado:** En curso — S8-A a S8-E1 integrados en `main`, más el hotfix de mapeo de errores, el prerregistro del modelo y la instrumentación de recuperación. La corrida canónica S8-E2 quedó `NOT_DEMONSTRATED` por los límites del nivel gratuito (RPM 5, RPD 20); la recuperación S8-E2-R1 quedó `ABORTED` tras ejecutar solo el lote 1 —10 de 14 casos agotaron el timeout de 20 s y su evidencia JSON se perdió—; y S8-E2-R2 está prerregistrada en local con timeout de 60 s y evidencia persistente, sin ejecutar. **CA-4 sigue sin medición válida.**
**Fecha real de cierre:** —

### Funcionalidades terminadas
- **S8-A — integrado en `main` mediante el PR #25 (squash `abd7c671de3329b0d00730d35b7dcbe54b4d1792`).** Gate exclusivamente
  documental: cierra por escrito el contrato de clasificación IA antes de escribir código de
  integración. Entrega [AI_CLASSIFICATION_CONTRACT.md](AI_CLASSIFICATION_CONTRACT.md) v1 con
  las veinte decisiones cerradas, el prompt versionado como `s8-classification-v1` en
  `backend/apps/ai_client_app/prompts/classification_v1.txt`,
  [ADR-0008](adr/ADR-0008-integracion-gemini-desacoplada.md) y
  [ADR-0009](adr/ADR-0009-clasificacion-asincrona-celery.md), y la especificación de la
  migración futura en el diccionario de datos.

  Puntos vinculantes: salida por slugs de catálogo cerrado con rechazo de campos
  desconocidos · `AI_CONFIDENCE_THRESHOLD = 0.75` · `CLASIFICADO` como «resultado de IA
  persistido y auditable» · fallo del proveedor deja el reporte en `CREADO` · la IA nunca
  produce `RECHAZADO` · auditoría por `prompt_version` en vez de prompt renderizado ·
  `raw_response` en el payload de `audit_logs` y no como columna · migración futura limitada
  a `justification` y `model_name` · UI de operador diferida al Sprint 9 · proveedor real
  fuera de la CI.

- **S8-B — integrado en `main` mediante el PR #26 (squash `31a4429ca511d5fa1f7fe92222b1edad624673fb`).**
  Implementa el contrato de S8-A sin tocar ningún proveedor: `contracts.py` con
  `ClassificationRequest` —lista blanca cerrada, sin PII ni coordenadas— y
  `ParsedClassification`; `parser.py` con validación estricta de las seis claves exactas;
  `catalog_mapping.py` con resolución exacta por slug contra categorías e instituciones
  activas; `policy.py` con el umbral, `ResolvedClassification` y `derive_has_changes`; y
  `adapters/` con el `Protocol` del proveedor y un adaptador falso determinista.

  El parser rechaza lo que el JSON estándar aceptaría de más: `NaN`, `Infinity`,
  `-Infinity`, claves duplicadas, cualquier top-level que no sea objeto, vallas Markdown,
  texto suelto alrededor del objeto, campos faltantes o desconocidos y toda coerción de
  tipos. `AI_CONFIDENCE_THRESHOLD = Decimal("0.75")` vive como constante del módulo: S8-B
  no lee `settings` ni variables de entorno, y el cableado a configuración queda para S8-C.

  Un slug que no existe, que está dado de baja o que llega en `null` produce
  `effective_inconsistency` y revisión humana, aunque el modelo se declare coherente. No
  hay normalización de mayúsculas, ni de acentos, ni coincidencia aproximada, ni respaldo
  silencioso en `Category.default_institution`. `hubo_cambio` lo deriva el backend y no se
  acepta del modelo.

  Nada de esto persiste: el núcleo no crea `AiClassification`, no escribe `AuditLog` ni
  historial y no modifica `Report`, `Category` ni `Institution`. `ai.classify_report` sigue
  siendo un no-op.

- **S8-C — integrado en `main` mediante el PR #27 (squash `7ad54dcc96d066c5cc89642e4855e8f43ba0e251`).** Cierra el ciclo completo de clasificación sobre el núcleo
  de S8-B: `orchestration.py` une parser, mapeo y política con la persistencia, la
  transición de estado y la auditoría; `tasks.py` implementa `ai.classify_report` de
  verdad; y la migración `0002` añade `justification` y `model_name` a
  `ai_classifications`, los dos únicos campos que el contrato aprobó (D17).

  **El proveedor sigue deshabilitado.** `adapters/runtime.get_runtime_adapter()` devuelve
  `None`, de modo que la tarea sale antes de tocar la base de datos y ninguna instalación
  clasifica nada. Es deliberado: la creación de reportes encola `ai.classify_report` en
  producción desde el Sprint 6, y dejar el adaptador falso activo por defecto habría
  bastado para clasificar incidencias ciudadanas reales con datos inventados. Habilitar un
  proveedor y decidir de dónde sale su configuración es trabajo de S8-D.

  El flujo llama al proveedor **fuera** de la transacción —sostener un `select_for_update`
  durante una petición de red convertiría una API lenta en un bloqueo de fila prolongado—
  y comprueba el estado del reporte dos veces: antes de llamar, para no gastar cuota en
  una reentrega, y otra vez ya dentro del lock, que es la única lectura con autoridad.

  Todo resultado que valide se persiste y transiciona `CREADO → CLASIFICADO`, se aplique o
  no la sugerencia: baja confianza, inconsistencia y slug no resoluble son desenlaces
  legítimos con revisión humana pendiente, no fallos. La sugerencia solo se escribe sobre
  `category`, `institution` y `priority`, y solo cuando la política lo permite. Una
  respuesta que no valida no persiste nada y deja el reporte en `CREADO`. Ninguna ruta
  produce `RECHAZADO`.

  Fuera de S8-C por decisión explícita: descarga de la imagen desde Storage —`image_bytes`
  viaja vacío—, política de reintentos del proveedor, endpoints y frontend.

- **S8-D — integrado en `main` mediante el PR #28 (squash
  `b37a450e996e1e1f04a5c4cca3900b287540e3bf`), sin haberse probado contra el proveedor
  real.** Integra el SDK oficial
  `google-genai` 2.20.0 mediante la **Interactions API** (`client.interactions.create`), no
  la API legada `generate_content` ni el paquete `google-generativeai`. Pide salida
  estructurada con el esquema JSON del contrato —seis campos exactos,
  `additionalProperties: false`, enum de prioridad y confianza acotada— y envía
  `store=False` para no pedir al proveedor que conserve la interacción.

  **La clasificación sigue apagada.** `AI_CLASSIFICATION_ENABLED` vale `false` por
  defecto y hacen falta las tres condiciones —interruptor, clave y modelo— para que
  `get_runtime_adapter()` devuelva algo. El interruptor está separado de la credencial a
  propósito: sin él, una `GEMINI_API_KEY` olvidada en las variables de un entorno
  bastaría para que un despliegue empezara a clasificar incidencias reales sin que nadie
  lo hubiese decidido.

  **Los reintentos viven en una sola capa.** El cliente del SDK se construye con
  `HttpRetryOptions(attempts=1)` —cero reintentos internos, verificado contra la
  documentación del propio tipo instalado— de modo que Celery es quien reintenta:
  `AI_MAX_RETRIES = 3` posteriores al intento original, con un máximo estricto de cuatro
  llamadas al proveedor por ejecución, retroceso exponencial y *jitter*. Solo se
  reintenta lo transitorio: timeout, corte de transporte, 408, 429 y 5xx. Credencial,
  permisos, modelo inexistente, petición malformada y respuesta que no valida no se
  reintentan. El timeout contractual son 20 segundos, expresados como 20.000 en
  `HttpOptions.timeout`, que en esta versión del SDK está en milisegundos.

  **La imagen viaja inline.** `storage.download_private()` la descarga desde el bucket
  privado con la credencial del servidor y los bytes van en base64 dentro de la petición.
  No se genera ninguna URL firmada ni pública, no se usa la File API del proveedor y ni
  la clave del objeto ni el bucket salen del backend. Los bytes ya están normalizados por
  la pipeline de S6 —solo JPEG y PNG, formato verificado con Pillow, recodificados sin
  metadata—, así que la imagen enviada no arrastra EXIF ni geolocalización de cámara.

  El prompt `s8-classification-v1` queda **byte-idéntico**: ya contenía las instrucciones
  de contenido no confiable y de no devolver datos personales que este gate exigía. Un
  reporte con evidencia registrada que no se puede leer **no** se clasifica en silencio
  sin ella.

- **S8-E1 — integrado en `main` mediante el PR #29 (squash
  `00a42e284a8f8eec1ca23b1dfa59fa0c93a3ff04`).** Construye la infraestructura de medición de
  CA-4 / RNF-05: `evaluation/dataset_s8_v1.json` con **28 casos** congelados,
  `evaluation/dataset.py` con validación estricta, `evaluation/scoring.py` con la métrica,
  `evaluation/runner.py` adapter-agnostic y el comando
  `manage.py evaluate_ai_classification`, que **solo admite el adaptador falso**.

  **Este gate no demuestra CA-4.** Con el adaptador falso la concordancia CA-4 es del
  **7,14 %** —y la estricta también, porque los dos casos que acierta reconocen además la
  inconsistencia—, y es el resultado correcto: ese adaptador repite la categoría que eligió el
  ciudadano y propone siempre `MEDIA`, de modo que falla en casi todos los casos que
  exigen corregir. El dataset no se diseñó para favorecerlo. Lo que queda demostrado es
  que la maquinaria de medición funciona y es reproducible, no la capacidad de ningún
  modelo.

  **Métrica oficial alineada con el Anexo H.** CA-4 se mide como exactitud sobre
  `category_slug`, `institution_slug` y `priority` —«Exactitud esperada >= 85% sobre
  categoria/institucion/prioridad»—, que es el criterio normativo de la tesis y el único al
  que se aplica el umbral del 85 %. Junto a ella se conserva una **concordancia estricta de
  diagnóstico** que añade `inconsistency`: no es CA-4, no tiene umbral propio y sirve para
  ver si el modelo, además de clasificar bien, reconoce cuándo la descripción contradice lo
  que eligió el ciudadano. La estricta nunca puede superar a CA-4, porque es CA-4 más una
  condición. `inconsistency` conserva su etiqueta de oro y su exactitud propia: queda fuera
  del criterio oficial, no de la medición.

  `confidence` no puntúa: es una estimación del modelo sobre sí mismo, no una etiqueta
  humana. `justification` se exige y se valida con el parser, pero no se puntúa
  semánticamente, porque dos redacciones distintas pueden ser igual de correctas.

  El dataset está congelado por SHA-256
  (`3bc54d591dc104aeabeecb2f5cc7c7a38e70ba042e7f045321ab33ae9b5efad4`): modificarlo rompe
  una prueba a propósito y obliga a crear una versión nueva en vez de mover el listón en
  silencio.

- **Hotfix de mapeo de errores — integrado en `main` mediante el PR #30 (squash
  `bd24ffc1f66344ab1cf42452113c9aaff7d6976f`).** La primera corrida real destapó que las
  excepciones de la Interactions API no heredan de `google.genai.errors.APIError`: el
  `except APIError` del adaptador las dejaba pasar enteras. El defecto iba más allá del
  benchmark, porque un 429 o un 503 reales no se habrían clasificado como transitorios ni
  habrían disparado el reintento. Ahora la clasificación se hace por la forma del error
  —`code`, `status_code` y la cadena de causas— y no por su clase.

- **S8-E2 — `NOT_DEMONSTRATED`. Corrida canónica con el modelo prerregistrado aún sin
  ejecutar.**

  La primera corrida usó `gemini-2.5-flash` y **no produjo ninguna clasificación**: los 28
  casos devolvieron HTTP 404 y el informe cerró con `errors_count = 28`. Eso no es un 0 %
  de capacidad ni un FAIL del modelo, sino la ausencia de medición.

  **Diagnóstico del 404: resuelto.** Una llamada mínima reprodujo el error y el proveedor
  indicó la causa —`gemini-2.5-flash` ya no está disponible para usuarios nuevos— y señaló
  `gemini-3.6-flash` como reemplazo. La inspección offline del SDK había confirmado antes
  que el enrutamiento era correcto (`POST /v1beta/interactions` sobre
  `generativelanguage.googleapis.com`), de modo que ni el dataset, ni el prompt, ni el
  esquema de clasificación intervinieron en el fallo.

  **Conectividad con `gemini-3.6-flash`: PASS**, verificada con una única llamada sintética
  —entrada mínima, sin dataset, sin prompt de clasificación y sin esquema—.

  **Modelo prerregistrado para la corrida:** `gemini-3.6-flash`. La sustitución queda
  fijada **antes** de cualquier medición válida y por indisponibilidad técnica del modelo
  original, no por rendimiento observado: `gemini-3.6-flash` todavía no ha visto ningún
  caso del benchmark. El protocolo completo está en
  [evaluation/README.md](../backend/apps/ai_client_app/evaluation/README.md).

  Siguen congelados el dataset `s8-evaluation-v1` (28 casos, sha256 `3bc54d59…`), el ground
  truth, el prompt `s8-classification-v1`, la definición de CA-4 y el umbral del 85 %, que
  con 28 casos exige **24 aciertos**.

  **La corrida canónica con `gemini-3.6-flash` se ejecutó una sola vez y volvió a quedar
  `NOT_DEMONSTRATED`.** 28 casos intentados, **6** clasificaciones válidas y **22**
  `TransientAiProviderError`. El `ca4_concordance_pct` resultante, 21.43 %, **no es un
  FAIL**: es un porcentaje calculado sobre una corrida inválida, porque 22 casos no
  produjeron clasificación alguna y el denominador siguen siendo 28. La regla de validez
  —escrita antes de ver cualquier número— invalida la corrida con un solo error.

  **Corrección: los artefactos originales de esa corrida se perdieron.** Una versión
  anterior de esta entrada afirmaba que «la evidencia se conserva íntegra». Ya no es
  cierta. Los ficheros vivían en `/tmp`, que en la máquina de desarrollo es **tmpfs**, y un
  reinicio del sistema borró `s8-e2-gemini-3.6-flash.json`, su `stderr` y el JSON de la
  corrida del 404. Sobreviven los resultados aquí documentados y los SHA-256 registrados en
  los informes de cada gate, pero **no los artefactos**, que no se pueden volver a
  inspeccionar. No se reconstruyen: fabricar un JSON desde lo narrado produciría un fichero
  con apariencia de evidencia primaria sin serlo. Esto **no** convierte `NOT_DEMONSTRATED`
  en PASS ni en FAIL —el veredicto sigue siendo el mismo—; lo que se reduce es la
  **reproducibilidad** de aquella ejecución, y debe constar.

  **Causa técnica: `RATE_LIMIT_CONFIRMED`.** El diagnóstico offline estableció que los 22
  fallos salieron de `interactions.create` con exactamente un intento HTTP por caso, en 28
  llamadas secuenciales sin pausa, y que la corrida duró 320.6 s. La consulta a AI Studio lo
  cerró: **nivel gratuito, RPM 5 y RPD 20**; el TPM (6.74 K de 250 K) **no** fue el límite
  alcanzado. El protocolo original era por tanto **inejecutable por construcción**: 28
  solicitudes no caben en una cuota diaria de 20, y lanzarlas sin pausa excede 5 por minuto.
  Lo que **no** es recuperable es la naturaleza exacta de cada uno de los 22 fallos: el
  adaptador distinguía «respondió 429» de «no respondió a tiempo», pero el runner guardaba
  solo el nombre de la clase y esa distinción se perdió al serializar. Afirmar que los 22
  fueron HTTP 429 iría más allá de la evidencia.

- **S8-E2-R1 — `ABORTED` / `NOT_DEMONSTRATED`. Lote 1 ejecutado una vez, evidencia JSON perdida.**

  Enmienda **solo el transporte** del experimento: **2 lotes fijos de 14 + 14**, cada uno en
  una **ventana RPD distinta**, con **20 s de pacing** entre solicitudes (3 por minuto, por
  debajo del RPM 5). El pacing no es el timeout: el timeout acota cuánto se espera *una*
  respuesta y sigue en 20 s; el pacing acota cada cuánto se pide *la siguiente*. Antes de
  cada lote hay que verificar visualmente en AI Studio, en solo lectura, cuota diaria
  disponible **≥ 14**; no se gastan llamadas en comprobarla.

  Los dos lotes son **una sola corrida**: tras el lote 1 el estado obligatorio es
  `R1_IN_PROGRESS` y no se calcula PASS ni FAIL. Eso no depende de la disciplina de quien
  ejecute — `BatchEvaluation` no guarda ningún porcentaje, así que el dato no existe hasta
  combinar los 28. Los lotes tampoco son datasets: se recortan por índice sobre el archivo
  congelado, y `combine_batches` aborta si los lotes no describen el mismo experimento o no
  cubren el dataset exactamente una vez.

  Sin cambios en modelo, dataset, ground truth, prompt, scoring, umbral, timeout ni
  reintentos (SDK `attempts=1`, script 0, Celery fuera del gate, reruns selectivos 0). La
  solución al RPM es el pacing y la solución al RPD es el batching; **no** se introdujo
  reintento para 429 ni se activó facturación: la recuperación debe ser válida bajo los
  límites del nivel gratuito observado.

  **Instrumentación añadida**, que es la lección concreta de la corrida perdida: por caso se
  registran ahora `error_message` (sanitizado por patrones explícitos, conservando la
  distinción diagnóstica) y `duration_ms` (reloj monotónico). Viajan **fuera** del informe
  canónico, en `EvaluationRun.diagnostics`, para que el informe conserve su determinismo
  byte a byte.

  **Ejecución y cierre.** Se ejecutó **solo el lote 1**, una vez: 14 intentos, 14 llamadas al
  proveedor, 4 respuestas válidas (casos 001, 004, 005, 010) y **10**
  `TransientAiProviderError`, los 10 con el mensaje sanitizado «El proveedor de IA no
  respondió a tiempo.». Duraciones entre 6076 ms y 20 270 ms contra un timeout de 20 000 ms.
  El lote 2 **no se ejecutó** y R1 queda **`ABORTED`**.

  El motivo primario del aborto es que la **evidencia JSON obligatoria del lote 1 es
  irrecuperable**: el script la escribió en el `/tmp` interno de un contenedor lanzado con
  `--rm` y sin montar un directorio de salida del host, y el contenedor se eliminó al
  terminar. Sin ese artefacto R1 no puede combinarse ni auditarse por caso según su propio
  protocolo. El `stderr`, redirigido en el host, sí sobrevivió y está preservado fuera de
  `/tmp` con un manifiesto y sus SHA-256. Los campos que vivían solo en el JSON
  —predicciones, coincidencias por campo, `duration_ms` individuales— **no se reconstruyen**.

  Diagnóstico técnico adicional: **`TRANSPORT_OR_TIMEOUT_FAILURE_CONFIRMED_FOR_10_CASES`**.
  Los 10 fallos entraron por la ruta transporte/timeout del adaptador; **cero** quedaron
  registrados como «respondió 429», es decir, en este lote no se observaron rechazos por
  cuota bajo el pacing prerregistrado; y la duración máxima observada fue de 20 270 ms contra
  un deadline configurado de 20 000 ms, de modo que el patrón es **compatible con presión del
  timeout**. Por tanto **20 s resultó operacionalmente inadecuado para completar R1**.

  Lo que **no** es demostrable es la clase original de cada uno de los 10 errores. Una
  versión anterior de esta entrada decía `CLIENT_TIMEOUT_CONFIRMED_FOR_10_CASES`, y eso iba
  más allá del dato: `_es_fallo_de_transporte` acepta `httpx.TimeoutException` **o**
  `httpx.TransportError` —la primera es subclase de la segunda— y ambas rutas terminan en el
  mismo mensaje, así que un corte de conexión produce «no respondió a tiempo» igual que un
  deadline agotado. El mensaje distingue del 429, que es la distinción que hacía falta, pero
  no separa timeout de otros fallos de transporte. Tampoco se afirma que el pacing haya
  resuelto definitivamente el límite de tasa: lo observado es la ausencia de 429 en **este**
  lote con **este** pacing.

  Subir el timeout a 60 s en R2 sigue siendo una corrección técnica justificada: el deadline
  es el único parámetro que el patrón señala, y ampliarlo no puede empeorar un fallo de
  transporte que no fuera de tiempo.

  `RUN_VALID_FOR_CA4 = NO`, `CA4_RESULT = NOT_DEMONSTRATED`. Los 4 aciertos no se convierten
  en porcentaje: son los casos que obtuvieron respuesta, una muestra seleccionada por el
  propio fallo.

- **S8-E2-R2 — recuperación prerregistrada, NO EJECUTADA (local, sin publicar).**

  Corrida **nueva y completa** de los 28 casos. No es continuación de R1, no reutiliza
  ninguno de sus resultados y no es un rerun selectivo: los 4 casos que R1 respondió se
  vuelven a pedir.

  **Único cambio técnico: el timeout pasa de 20 s a 60 s.** Se fija antes de la corrida y no
  podrá ajustarse después de ver resultados. La insuficiencia de los 20 s es un hecho
  observado sobre el transporte, no sobre la capacidad del modelo: un caso que agota el
  tiempo no produce clasificación, así que no aporta ni acierto ni fallo a CA-4. Sin cambios
  en modelo, dataset, ground truth, prompt, scoring, umbral, pacing (20 s), `store=False`,
  reintentos (`attempts=1`, script 0) ni configuración de *thinking*.

  `RecoveryProtocol` mantiene R1 y R2 como instancias congeladas e independientes, con los
  valores absolutos que cada prerregistro fijó y que se pasan explícitamente al validar. Un
  lote de R1 no puede combinarse bajo R2 ni al revés, porque su estado no coincide. R1 queda
  como registro histórico, sin reinterpretar.

  **Lo declarado es lo realmente usado.** Declarar no es verificar: la evidencia
  archivaba `dataset_sha256` sin demostrar que el dataset ejecutado viniera de esos
  bytes, y el prompt solo estaba congelado por una etiqueta de versión que no impide
  que el archivo cambie debajo. La ruta canónica liga ahora cada valor declarado al
  efectivo — `run_recovery_batch` ya no recibe un `EvaluationDataset`: lee los bytes
  una sola vez (sin ventana TOCTOU), verifica su SHA-256 contra el prerregistro y
  construye el dataset desde esos mismos bytes; y hashea el template que
  `render_classification_prompt` usará de verdad contra el nuevo `prompt_sha256`.
  Cualquier divergencia aborta antes de construir el cliente, de modo que unos
  inputs equivocados cuestan cero llamadas y cero cuota. `prompt_sha256` se añade al
  prerregistro y a la evidencia **antes** de ejecutar R2; no es una propiedad
  retroactiva de los JSON perdidos de R1, que no se reconstruyen.

  **El timeout de la evidencia es el de la petición.** `RecoveryProtocol` declara
  `request_timeout_seconds`, pero el timeout que gobierna la llamada se fija al construir el
  cliente del SDK. Con dos fuentes independientes, una corrida podía archivar 60 s mientras
  el cliente esperaba 20: la evidencia describiría un experimento que no ocurrió y nada en
  ella lo delataría. `evaluation/execution.py` cierra eso — `run_recovery_batch` deriva el
  modelo y el timeout del **mismo** `protocol` que después valida el lote, rechaza recibirlos
  a mano, y exige `protocol` sin valor por omisión para que ejecutar R2 no pueda degradar en
  silencio a R1.

  **La evidencia deja de vivir en `/tmp`.** `/tmp` no es almacenamiento autoritativo en este
  proyecto: es tmpfs, y ya destruyó los artefactos de dos corridas. R2 escribirá en un
  directorio persistente del host elegido por quien ejecuta mediante `$EVIDENCE_DIR`;
  ninguna ruta de evidencia está fijada en el código, y hay una prueba que lo comprueba. La
  redirección va del contenedor al host directamente, nunca al `/tmp` interno del
  contenedor: ese paso intermedio fue exactamente lo que perdió el lote 1 de R1.

  **CA-4 no está demostrado** y el Sprint 8 no puede cerrarse.

### Pruebas realizadas
- **S8-A:** ninguna. El gate no modifica código funcional. El único archivo bajo `backend/`
  es el prompt versionado, que es texto y no se importa desde ningún módulo.
- **S8-B:** 225 pruebas nuevas en `apps/ai_client_app/tests/` —parser, adaptador falso,
  mapeo de catálogos y política—, todas offline. Cubren el camino feliz, los tres desenlaces
  con resultado, la frontera exacta del umbral en 0.75, cada campo obligatorio ausente por
  separado, cada tipo incorrecto por campo, las constantes no representables, las claves
  duplicadas, la valla Markdown, el texto antes y después del objeto, y que los mensajes de
  error no filtren la respuesta cruda ni la descripción del ciudadano.

  Invariantes verificadas sobre el AST y no sobre el texto fuente: ningún módulo del núcleo
  importa `ReportStatus` ni escribe `RECHAZADO`, el adaptador falso no importa azar, reloj,
  UUID, entorno ni cliente HTTP alguno, y `policy` no consulta `settings`.

  Suite backend completa en verde: **1265 pruebas, 0 fallos**, cobertura 99 %.
  `ruff check .` y `ruff format --check .` limpios. `makemigrations --check`: *No changes
  detected*. Ejecutado con PostGIS y Redis locales de `infra/docker-compose.yml`; sin red
  externa y sin contacto con ningún proveedor de IA.
- **S8-C:** 61 pruebas nuevas —la suite pasa de 1265 a 1323, tras sustituir las tres del
  no-op anterior—. Cubren la aplicación automática y su frontera exacta en
  0.75; los seis casos de revisión humana —confianza baja, inconsistencia, slug
  desconocido y slug nulo por cada campo—; las ocho formas de respuesta inválida; el fallo
  del proveedor; el reporte inexistente; el identificador que no es un UUID; los seis
  estados distintos de `CREADO`; y la ausencia de proveedor configurado.

  Idempotencia probada de tres maneras: reentrega secuencial —que además comprueba que el
  proveedor no se llama por segunda vez—, la ventana exacta entre la comprobación previa y
  el lock forzada de forma determinista, y **dos hilos compitiendo de verdad contra
  PostgreSQL**, siguiendo el patrón ya establecido en `test_identity_concurrency.py`. En
  los tres casos el resultado es una clasificación, un asiento de historial y una entrada
  de auditoría.

  Invariantes verificadas: la auditoría no filtra descripción, ciudadano ni coordenadas;
  la respuesta cruda se trunca a 8.000 caracteres; ninguna ruta produce `RECHAZADO`; la
  clasificación no crea ni altera transacciones del ledger; y un slug inventado no da de
  alta la categoría.

  Suite backend completa en verde: **1323 pruebas, 0 fallos**, cobertura 99 %. `ruff
  check .` y `ruff format --check .` limpios. `makemigrations --check`: *No changes
  detected*. `manage.py check`: sin incidencias. Sin red externa y sin contacto con ningún
  proveedor de IA.
- **S8-D:** 110 pruebas nuevas en cinco archivos, más las de la descarga privada y el
  umbral configurable; la suite pasa de 1323 a 1451. **Todas contra dobles.** El cliente del SDK y el
  almacenamiento están sustituidos por completo: se captura lo que el adaptador *habría*
  enviado y se afirma sobre ello.

  Cubren el uso de la Interactions API y la ausencia de `generate_content`; `store=False`;
  el esquema estructurado completo; el timeout de 20 s y los reintentos internos del SDK
  desactivados; la traducción de 408, 429 y 5xx a transitorio y de 400, 401, 403, 404 y
  422 a permanente; timeout y corte de transporte; salida vacía o no textual; interacción
  no completada; imagen inline en base64 frente a petición solo de texto; descarga privada
  con sus fallos transitorio y permanente; y las tres condiciones del runtime por
  separado.

  Invariantes de privacidad: ni el identificador del reporte, ni el del ciudadano, ni las
  coordenadas, ni la clave del objeto, ni el hash llegan al proveedor, verificado además
  sobre el conjunto exacto de claves de la petición. Un intento de inyección en la
  descripción viaja delimitado como contenido no confiable y no altera esquema, catálogos
  ni parámetros. El parser sigue rechazando una respuesta con campo de más aunque el
  esquema estructurado la hubiese pedido bien.

  Suite backend completa en verde: **1451 pruebas, 0 fallos**, cobertura 99 %. `ruff
  check .` y `ruff format --check .` limpios. `makemigrations --check`: *No changes
  detected* —S8-D no crea ninguna migración—. `manage.py check`: sin incidencias.

  **Cero llamadas reales**: ninguna petición a Gemini, ninguna descarga real de Supabase,
  ninguna clave de API real y ningún consumo de cuota.
- **S8-E1:** 72 pruebas nuevas en tres archivos; la suite pasa de 1451 a 1523. Cubren la
  validación estricta del dataset —campos desconocidos, prohibidos, identificadores
  duplicados, slugs fuera del catálogo, prioridad inválida, `inconsistency` no booleana—,
  su congelación por hash y su cobertura completa de categorías, instituciones y
  prioridades.

  Del runner: que no conoce al proveedor ni al runtime, que no toca base de datos ni
  Celery, que usa el parser real, que no introduce relojes ni azar, y que **las etiquetas
  de oro nunca viajan en la petición**. De la aritmética: 100 %, 0 %, una fracción exacta
  del 75 % y la frontera exacta de `85.00` calculada con `Decimal`, referida siempre a
  CA-4. De la separación de métricas: fallar **solo** `inconsistency` deja CA-4 en 100 % y
  la estricta en 0 %, mientras que fallar cualquiera de los tres campos oficiales rompe
  ambas. De los fallos: una respuesta inválida o una excepción del adaptador cuentan como
  no concordantes, quedan en `errors_count` y **no detienen la corrida**.

  Determinismo verificado ejecutando el comando dos veces: salida JSON **idéntica byte a
  byte** (14.849 bytes, sha256 `6972c1e7…`). Suite backend completa en verde: **1523
  pruebas, 0 fallos**, cobertura 99 %. `ruff check .` y `ruff format --check .` limpios.
  `makemigrations --check`: *No changes detected*. Sin red, sin proveedor y sin secretos.

### Problemas encontrados
- La auditoría previa al gate reportó como incorrecta la referencia «Anexo H.3» del prompt
  base en `PLAN_DESARROLLO.md`. **La verificación documental la desmintió.** El
  `.docx` revisado —fuente normativa del proyecto— numera H.2 como escenarios del módulo de
  incentivos y H.3 como prompt base para IA, de modo que la referencia del plan es correcta.
  El PDF versionado es la versión anterior, sin ese H.2, y por eso desplaza toda la
  numeración un lugar. **No se corrigió ninguna referencia**; la tabla de equivalencia entre
  ambas numeraciones quedó documentada en el contrato.
- `PLAN_DESARROLLO.md` citaba el ADR-0009 como `ADR-0009-tareas-asincronas-celery.md`. El
  archivo se creó como `ADR-0009-clasificacion-asincrona-celery.md` y la referencia se
  ajustó al nombre real.

### Commits relevantes
- S8-A: commit local `docs(ai): cerrar contrato S8-A` sobre
  `810bb14a49e52d377818e4fde2cc78d4c76731eb`. **No publicado**: sin push, sin PR y sin merge.

### Observaciones y desvíos respecto del plan
- **Sprint 8 queda iniciado documentalmente, no técnicamente.** `ai.classify_report` sigue
  siendo un no-op, no hay SDK de Gemini instalado, no hay secretos configurados, no se creó
  ninguna migración, ningún endpoint y ningún componente de frontend.
- Ninguna llamada al proveedor de IA, ni real ni de prueba. Cero contacto con Railway,
  Vercel, Supabase, Redis o producción.
- La UI del bloque «Clasificación asistida por IA» se difiere al Sprint 9 por decisión
  explícita del contrato (D18): vive dentro del detalle de reporte para operadores, que es
  entregable de ese sprint. **No debe contabilizarse como deuda de S8.**
- CA-4 sigue siendo requisito real del Sprint 8, pero su medición contra el proveedor real
  queda fuera de la CI y exige un gate propio antes del cierre. Alcanzar el 85 % con el
  cliente falso validaría el medidor, no el modelo.
- La migración de la credencial legacy `service_role` de Supabase a `sb_secret_*` sigue sin
  iniciarse y permanece fuera del alcance del Sprint 8.
- La advertencia de GitHub Actions sobre la transición de Node 20 a 24 continúa como deuda
  de CI no bloqueante y fuera de este sprint.

---

## Sprint 9 — Panel institucional y validación

**Fechas planificadas:** 26/09/2026 – 02/10/2026
**Estado:** Completado — S9-A, S9-B1, S9-B2 y S9-B3 integrados
**Fecha real de cierre:** 09/09/2026

### Funcionalidades terminadas

- **S9-A (integrado):** API de listado y detalle institucional con scope efectivo coherente,
  selección multiinstitución y proyección histórica fail-closed de revisión humana.
- **S9-B1 (integrado):** servicios transaccionales de validación/rechazo y ledger, con bloqueos,
  historial, auditoría e idempotencia frente a concurrencia.
- **S9-B2 (integrado mediante PR #37):** endpoints HTTP autorizados de validación y rechazo, códigos
  estables y ocultación cross-tenant; invalidación pública post-commit best effort; bandeja,
  detalle, selector desde `/auth/me/`, confirmaciones de efectos económicos y actualización tras
  éxito o conflicto. El hardening de revisión añade payloads JSON fail-closed, autorización
  linealizable frente a revocación, bandeja limitada server-side a estados operables y paginación
  frontend aislada por institución. Incluye pruebas backend, frontend y dos recorridos E2E
  sintéticos.
- **S9-B3 (integrado mediante PR #38):** concilia el estado integrado de
  S9-A, S9-B1 y S9-B2, la trazabilidad entre contrato, API, ledger y pruebas, y delimita
  explícitamente las capacidades posteriores. No añade endpoints, modelos, migraciones ni
  comportamiento funcional.

### Pruebas realizadas

- Matriz HTTP S9-B2: éxito, entradas estrictas, autenticación/autorización, membresía, institución
  inactiva, fallback S9-A, sugerencia IA no autoritativa, conflictos, rollback, caché y concurrencia:
  **105 pruebas institucionales** y **1.807 pruebas backend completas** superadas.
- Contratos frontend de lectura/mutación, selector multiinstitución, confirmaciones obligatorias,
  motivo cerrado, ausencia de replay y refresh posterior a `409`: **341 pruebas Vitest**, ESLint,
  TypeScript, Prettier y build de producción superados.
- E2E sintético: operador → institución → detalle → validar; operador → institución → detalle →
  motivo → rechazar. Suite completa: **48 pruebas Playwright** superadas contra servidor local
  precalentado; el build local de producción también finalizó correctamente.
- Verificación focal previa al cierre S9-B3: **153 pruebas backend**, **14 frontend** y **2 E2E de
  operador** superadas desde el merge exacto de S9-B2.

### Problemas encontrados

- El pre-merge review detectó cuatro hallazgos, corregidos en rama: JSON no-objeto que llegaba a
  `500`, ventana concurrente entre revocación y decisión, estados no operables en la bandeja y
  ausencia de navegación paginada. La caché pública continúa deliberadamente como best effort
  después del commit para que una indisponibilidad de Redis no revierta una decisión institucional
  válida. Los cuatro hallazgos fueron corregidos; la segunda revisión dio PASS y el PR #37 fue
  mergeado. No se detectaron defectos funcionales nuevos durante el cierre S9-B3.

### Commits relevantes

- S9-A: integrado mediante PR #34.
- S9-B1: merge `a89b9efe676abfae0401e48145831383f6beaf3f` mediante PR #35.
- S9-B2: merge `4ecf502361156281cbec51764310c67f50ace449` mediante PR #37.
- S9-B3: `da2eb2984e2882a744c119ac9404f20d3a18cfb9` contiene la implementación inicial y el
  cierre documental; `2088587cd32509e866fc2d1e171d1d6e1ccc556d` contiene las correcciones
  posteriores al primer pre-merge review sobre estado de integración y secuencias
  transaccionales; `41babc681181baf4c9ac805e3eb04edf9f8ab188` contiene el fix final de
  trazabilidad y correspondía al `HEAD` al iniciar la revisión post-review;
  `4472e459f8054f77fd0bff810264700f5206d284` contiene la corrección final documental de los
  hallazgos de revisión; `f87ae07bab5675bb07b99b590ee7df8b955f2485` contiene la corrección final
  de trazabilidad histórica, sin cambios funcionales; y
  `c99e769b6a72833301d7dbd142fa8a5bd47f5ada` resuelve D-07 en `docs/DICCIONARIO_DATOS.md`,
  documentando `justification`, `model_name` y la migración `0002` como implementados. La cadena
  histórica de la rama fue
  `main@4ecf5023 → da2eb298 → 2088587 → 41babc6 → 4472e45 → f87ae07 → c99e769b → a089d3b`.
  El PR #38 fue mergeado en `main` mediante
  `7e95856c1b494d562ce1c267c06d5bebaec28cc8`; ninguno de sus commits intermedios representa por
  sí solo el estado completo de la rama. Después del merge, `main` avanzó mediante los commits
  administrativos autorizados `f0e8ce1` y `730d69e`, ajenos al alcance de S9.

### Observaciones y desvíos respecto del plan

- Se difieren expresamente a S11 `GET /api/v1/rewards/summary/`,
  `GET /api/v1/rewards/transactions/`, la ruta frontend `/recompensas` y ranking/privacidad.
- Catálogo y canje corresponden a S12; dashboards e ingesta social a S13. Las notificaciones
  completas no forman parte de S9 y requieren resolver previamente las decisiones enumeradas en
  el estado general.
- S8 continúa en curso; R2 permanece `PREREGISTERED / NOT_EXECUTED` y CA-4
  `NOT_DEMONSTRATED`. S11, S12 y S13 no fueron iniciados.
- No hubo acciones manuales de producción, Vercel o Railway durante S9-B3.

---

## Sprint 10 — Estados, evidencias, auditoría y reversión

**Fechas planificadas:** 03/10/2026 – 09/10/2026
**Estado:** IMPLEMENTADO · DESPLEGADO · VERIFICADO · RECONCILIADO
**Fecha real de cierre:** 21/09/2026

### Funcionalidades terminadas

- Ciclos de resolución monotónicos, migraciones aditivas, backfill determinista y restricciones de
  integridad.
- Flujo institucional `ASIGNADO → EN_PROCESO → RESUELTO → CERRADO`, con ciclo esperado, locks,
  historial y auditoría.
- Evidencia privada JPEG/PNG normalizada, limitada por ciclo, con URLs efímeras e idempotencia.
- Bonus de resolución, reversión administrativa compensatoria y revalidación sin reactivar valor
  económico histórico.
- Autocierre periódico a las 72 horas por defecto, con tarea Celery idempotente y Beat único.
- Lectura administrativa paginada de auditoría y UI de operador, ciudadano y administrador.

El candidato desplegado fue `171f8c8b8f448a4092e73aeb5f67406e6b81ef09`. Su árbol de
`backend/` coincide exactamente con el árbol del commit Heroku
`9676e94b6b5daf4943f17a796285344a0d77af4d`: el deploy creó el release `v13` y la configuración
posterior de `REPORT_AUTO_CLOSE_HOURS=72` creó el release vigente `v14`. PR #39 se integró en
`main` mediante el merge commit `7ae82b865d90c4cade02dd3378b44107f088b7dd`, preservando tanto
la corrección `report.confirmed → CYCLE_0` como `backend/heroku.yml`.

El respaldo previo validado fue `pre_s10_corrected_retry_20260921_030350.dump`, de 328954 bytes,
SHA-256 `370392f09cafbe8971a181d729fb278ac4346e495d9b3210a304986f60f4940e`, formato custom legible
por `pg_restore --list` y 624 entradas TOC.

### Pruebas realizadas

El candidato incluye pruebas unitarias, de API, esquema/migraciones, concurrencia e idempotencia para
S10, además de las suites completas backend, frontend y Playwright. La auditoría productiva previa
a `0004` terminó con `READONLY_AUDIT_COMPLETE` y el universo aprobado: 16 reportes,
`SYNTHETIC=14`, `CYCLE_0=2`, `CYCLE_1=0`, `UNKNOWN=0`, `INCONSISTENT=0`. Reconoció los eventos
`report.created` y `report.confirmed` del caso histórico de ciclo cero.
El release gate final reprodujo 1.984 pruebas backend con 99% de cobertura, 360 Vitest, 52 Playwright
Chromium sobre build de producción y las 8 carreras PostgreSQL; Ruff, Django check,
`makemigrations --check`, ESLint, TypeScript, Prettier y build pasaron. Las 62 pruebas focalizadas
incluyeron clasificación negativa, abort transaccional con cero escrituras y violaciones directas
de constraints.

En producción se aplicaron, en orden, `reports_app.0004`, `0005`, `0006`, `0007` y
`rewards_app.0003`, `0004`; `manage.py check` terminó sin incidencias. Se verificaron siete
constraints, tres índices únicos parciales y el trigger monotónico de ciclos. El checkpoint y el
estado final conservaron 16 reportes, 2 historiales, 3 transacciones de recompensa, 9 entradas de
auditoría y 16 geometrías; los 16 reportes y los 2 historiales quedaron en ciclo cero. Las tres
transacciones históricas permanecen legítimamente con `resolution_cycle=NULL`. Las huellas de IDs
y geometrías no cambiaron: `production_data_universe_changed=NO`.

El smoke productivo confirmó health HTTP 200 con DB y Redis, categorías, reportes, autenticación,
auditoría y endpoints S10. `web.1`, `worker.1` y un único `beat.1` quedaron `UP`; el worker se
conectó a Redis y registró las tareas S9 y `reports.auto_close`, y Beat inició su scheduler `*/15`.
El frontend de Vercel respondió 200 en `/`, `/login` y `/mapa`; sus bundles apuntan al backend
Heroku esperado y no contienen referencias a Railway.

### Problemas encontrados

El primer release gate encontró tres blockers técnicos, todos remediados y verificados en el gate
final:

1. `S10-RG-B01 CLOSED`: migración y preflight usan un clasificador compartido, explícito y
   fail-closed; no existe `COALESCE`/fallback de ownership y las contradicciones abortan antes de
   cualquier escritura;
2. `S10-RG-B02 CLOSED`: PostgreSQL exige report/ciclo positivo para bonus, report/ciclo/original
   para reversals y rechaza `related_transaction` en otros eventos; las unicidades parciales se
   comprobaron directamente en el esquema local;
3. `S10-RG-B03 CLOSED`: el primer preflight productivo reveló que `report.confirmed` es una acción
   histórica válida de ciclo 0. La corrección `7e31953f20943b2d2a79e9df41f74502a5267f78`
   la incorpora a la allowlist sin admitir acciones de ciclos avanzados; las regresiones cubren
   explícitamente `report.confirmed → CYCLE_0` y
   `report.resolution_evidence_created → UNKNOWN`.

El primer archivo de respaldo corregido fue observado mientras la sesión real de `pg_dump` seguía
activa y por eso aparentó temporalmente 0 bytes. No fue corrupción de PostgreSQL. El procedimiento
quedó corregido a: escribir en `.partial`, esperar al proceso real, exigir `exit=0`, comprobar
`size > 0`, validar con `pg_restore --list` y sólo entonces renombrar al nombre final.

El primer deployment Vercel posterior al merge se registró para el SHA
`7ae82b865d90c4cade02dd3378b44107f088b7dd` con `environment=Production` y `status=SUCCESS`.
La integración GitHub/Vercel informó a la vez `production_environment=false`; se conserva como
discrepancia de metadato y no se intentó corregir. La evidencia operativa aceptada fue el entorno
`Production`, el SHA coincidente, el estado exitoso y el dominio productivo verificado.

Pendientes conocidos, expresamente fuera de este cierre:

- `BREVO_API_KEY=ABSENT`: el correo no está plenamente operativo; una entrega sin configuración
  queda en fallo controlado y consultable, sin impedir el arranque del worker.
- HSTS permanece pendiente.
- Redis usa TLS con `CERT_NONE`; es deuda de seguridad conocida.
- La clasificación por IA permanece intencionalmente deshabilitada.

### Commits relevantes

La implementación y remediación abarcan `8b660bc`–`0fff30c`; B01/B02 quedaron materialmente
corregidos mediante `754c123`, `64ff407`, `88480ca`, `20a55db`, `f47b4e1`, `1ec39cb`, `0469f55`
y `0fff30c`, sobre el baseline S9 `730d69ede7033c3487b68f79b1828fe6f2f43180`. La corrección
posterior del clasificador es `7e31953f20943b2d2a79e9df41f74502a5267f78`; el candidato final
es `171f8c8b8f448a4092e73aeb5f67406e6b81ef09` y su integración en `main` es
`7ae82b865d90c4cade02dd3378b44107f088b7dd`.

### Observaciones y desvíos respecto del plan

ADR-0013 define que una reversión administrativa desde `ASIGNADO`, `EN_PROCESO`, `RESUELTO` o
`CERRADO` devuelve el reporte a `CLASIFICADO`. La implementación conserva evidencia e historial,
limpia las proyecciones vigentes y no incrementa `resolution_cycle`; una validación posterior inicia
un ciclo nuevo y la evidencia de resolución pertenece a su ciclo.

También incorpora D-01 —la reversión siempre se ejecuta y el saldo, definido como la diferencia
entre créditos válidos y consumidos, puede resultar negativo—, C-01 —anulación de pendientes que
siguieran sin acreditar— y C-08 —redacción del saldo y criterio de reversión—. El autocierre a las
72 horas transcurridas desde `resolved_at`, las APIs, la evidencia privada y los efectos de ledger
de S10 están activos en producción. S10 no incorporó una dependencia nueva de correo: la
ampliación del sistema de notificaciones permanece fuera de alcance y `BREVO_API_KEY` sigue
ausente. S11, S12 y S13 no fueron iniciados durante este cierre.

---

## Sprint 11 — Metodología, instrumentación, experimentos, análisis y evidencia

**Fechas planificadas:** 10/10/2026 – 16/10/2026
**Estado:** En curso — S11-A y S11-B en `PASS`; rebaseline custodial pendiente; S11-C bloqueada
**Fecha real de cierre:** —

> **Rebaseline S11-A (21/09/2026).** El título histórico “Créditos, ranking del mes en curso y
> privacidad” se conserva como propuesta previa, no como trabajo ejecutado. Ranking, saldo visible,
> privacidad aplicada al ranking y canje/redención se separan como
> `FUTURE_FEATURE / DEPENDENCY`; EX-11 queda diferido. El nuevo Sprint 11 se organiza en S11-A
> metodología, S11-B instrumentación, S11-C experimentos, S11-D análisis y S11-E evidencia/cierre.

### Artefactos metodológicos terminados

- `docs/tesis/S11A_CATALOGO_Y_MATRIZ.md`: catálogo literal PG/PE/OG/OE/HE/HT y matriz
  tesis→sistema→evidencia.
- `docs/tesis/S11A_DICCIONARIO_INDICADORES.md`: indicadores, protocolo HT-1 y manifiesto crítico
  HT-5.
- `docs/tesis/S11A_ESCENARIOS_Y_PROTOCOLO.md`: EX-01…EX-11, matriz de eventos,
  `resolution_cycle` y protocolo común.
- `docs/tesis/S11A_DATOS_EVIDENCIA_Y_REBASELINE.md`: datasets, aislamiento, privacidad,
  retención, cadena de evidencia y rebaseline.
- `docs/tesis/S11A_POLITICA_CUSTODIA_Y_ACCESO.md`: política aprobada de retención,
  responsables nominales, primary/backup/restore, acceso y destrucción para cerrar P02/P03.

La versión `S11A-v1.0` fue redactada contra
`main@635d89956ca0632d620916dc0ab298af48079ec4` y quedó oficialmente versionada en
`main@35f850eb24df552805fe8694f50fcbb3588a4141`. S11-A no modificó código ni ejecutó
experimentos.

Gate formal de cierre: `S11A_METHODOLOGY_FROZEN`.

### Decisión metodológica previa a S11-C2

- **Autoridad:** Monty Emmanuel Pohl Amarilla, autor y custodio.
- **Decisión humana explícita:** **OPCIÓN A — mantener `S11A-v1.0`**.
- EX-02 y EX-03 permanecen independientes: cada escenario usa su propio run, reset, reporte y
  evidencia; EX-03 no reutiliza el reporte de EX-02.
- Esta constancia no modifica S11-A, indicadores, matriz de eventos, denominadores, hipótesis ni
  harness, y no crea una nueva versión metodológica.
- La constancia no incorpora una firma criptográfica y no autoriza por sí sola la ejecución de
  C2. Los gates de credenciales operacionales deben resolverse por separado.

### Incidente de continuidad HMAC y decisión C (21/09/2026)

- El diagnóstico `S11_HMAC_DIAGNOSTIC_COMPLETE` clasificó el incidente como
  `KEY_CONTINUITY_FAILURE`. La clave `S11_CUSTODY_HMAC_KEY` actualmente disponible no verifica
  la evidencia histórica de P02/P03 ni de C1; tampoco lo hace `S11_DOCUMENT_HMAC_SECRET`. No hay
  `key_id` histórico disponible y el runtime no implementa keyring ni versionado de claves.
- Los JSON históricos conservan integridad de bytes verificable mediante SHA-256, pero su
  autenticidad HMAC histórica no puede verificarse con las claves autorizadas disponibles. No se
  consideran falsos ni corruptos y permanecen en sus rutas originales sin resignar, sobrescribir,
  eliminar o mover destructivamente.
- Monty Emmanuel Pohl Amarilla registró la **DECISIÓN C**: conservar esos artefactos como
  `HISTORICAL_UNVERIFIABLE`, iniciar una nueva época de custodia con una clave estable y repetir
  P02/P03 y C1 antes de ejecutar EX-02. La decisión completa y los hashes preservados constan en
  [`S11_HMAC_CUSTODY_CONTINUITY_INCIDENT.md`](runbooks/S11_HMAC_CUSTODY_CONTINUITY_INCIDENT.md).
- La nueva clave custodial fue configurada fuera de Git y el runtime comprobó el fingerprint no
  secreto `7205a04f93cc3da9`. Se mantuvo durante C1 y debe continuar estable para los runs
  posteriores y durante toda la retención aplicable. Para las repeticiones P02/P03 y C1,
  `S11_HOST_GID=1003` se exportó al proceso desde `id -g`; `.env` no fue modificado.
- El intento previo S11-C2/EX-02 queda `S11C2_EX02_NOT_DEMONSTRATED` por una causa de
  infraestructura/custodia. No es un `FAIL` funcional: no se creó un run C2 y el experimento no
  comenzó.
- Gates vigentes: S11-A `PASS`; S11-B `PASS`; metodología Opción A `PASS`; diagnóstico HMAC
  `COMPLETE`; P02/P03 `PASS`; C1 `PASS`. EX-02 y EX-03 no fueron ejecutados ni autorizados y
  requieren un pre-gate separado. No se avanza a S11-C3.
- La decisión no modifica `S11A-v1.0`, objetivos, hipótesis, indicadores, fórmulas, escenarios,
  denominadores, criterios de decisión, matriz de eventos, harness ni la independencia de EX-02 y
  EX-03.

### Pruebas realizadas

- Gate inicial: rama `main`, HEAD exacto y working tree limpio.
- Control documental: IDs, clasificaciones, estados, escenarios, umbrales, hashes R2 y referencias
  cruzadas verificados sobre los artefactos S11-A.
- S11-B/P02-P03: 109 tests de `experiments_app` verifican manifests, datasets,
  aislamiento/reset, export read-only, instrumentación HTTP/Celery, trazabilidad, harness y
  custodia/redacción; 60 corresponden específicamente a evidencia y custodia.
- P02/P03: la verificación histórica `s11-custody-20260921-p02p03-v5` se ejecutó en dos
  contenedores efímeros separados y sus bytes siguen preservados. Sus artefactos `complete.json`
  y `attestation.json` están ahora `HISTORICAL_UNVERIFIABLE` por `KEY_CONTINUITY_FAILURE`; no
  sirven como atestación criptográfica para el gate vigente.
- Repetición P02/P03: `s11-custody-20260921-p02p03-v6` se ejecutó con la nueva época HMAC y quedó
  `VERIFIABLE_WITH_CURRENT_CUSTODY_KEY`. Los seis registros autenticados pasaron SHA-256/HMAC,
  frontera entre contenedores, backup, restore, matriz nominal, retención, gates de clasificación
  y destrucción controlada del canario. `125` tests de `experiments_app` y `manage.py check`
  pasaron. Gate: `S11_P02_P03_REPEATED_PASS`.
- C1: la verificación histórica `s11-c1-20260921-isolation-repro-reset-v1` fue ejecutada y su
  `complete.json` conserva el SHA-256 registrado. El artefacto está
  `HISTORICAL_UNVERIFIABLE` por el mismo incidente y permanece preservado.
- Repetición C1: el run `s11-8448491d-3347-5c00-9f44-d8ae110e9095` verificó aislamiento,
  generación byte a byte reproducible de `s11-functional-v1`, reset idempotente con canarios,
  manifest `COMPLETED`, evidencia persistente y backup/restore. Primary, backup y restore
  conservaron siete registros con SHA-256/HMAC, clasificación y retención válidos bajo la nueva
  época. El paquete `s11-c1-20260921-isolation-repro-reset-v2` quedó
  `VERIFIABLE_WITH_CURRENT_CUSTODY_KEY`; su `complete.json` tiene SHA-256
  `18170777ea896e3ef8bd6b752aac40d695f84c3a652282c5c69433c18fd748f0`. Pasaron `125` tests de
  `experiments_app` y `manage.py check`. Gate: `S11C1_REPEATED_PASS`.
- Backend completo: 2095 tests, 95 % de cobertura. Ruff, `manage.py check`,
  `makemigrations --check --dry-run` y el compose combinado pasan. El frontend no cambió; se
  conserva su baseline previo de 360 tests, ESLint, typecheck y build.
- No se ejecutó Playwright S11, ningún escenario oficial ni ninguna medición HT.

### Problemas encontrados

- EX-01/HE tiene cuestionario en el Anexo G, pero carece de formulario aprobado, dataset y N
  observados; permanece `NOT_DEMONSTRATED`.
- La atestación histórica P02/P03 continúa inutilizable por pérdida de continuidad HMAC, pero la
  repetición independiente `s11-custody-20260921-p02p03-v6` cerró nuevamente P02/P03 en `PASS`.
- La evidencia C1 histórica también permanece `HISTORICAL_UNVERIFIABLE`, pero la repetición
  independiente `s11-c1-20260921-isolation-repro-reset-v2` cerró C1 en `PASS` bajo la nueva época.
- El intento previo S11-C2/EX-02 no inició el experimento y queda
  `S11C2_EX02_NOT_DEMONSTRATED` por infraestructura/custodia, no como fallo funcional.
- R2 no fue ejecutada y su evidencia válida no existe.
- La cobertura global previa no decide HT-5; falta instrumentación de cobertura acotada.
- El harness sigue exigiendo confirmación explícita por run. La clave HMAC custodial estable debe
  conservarse para todos los runs posteriores; las verificaciones P02/P03 y C1 no utilizaron
  `--custody-approved` ni ejecutaron escenarios funcionales.

### Commits relevantes

- S11-A: `35f850eb24df552805fe8694f50fcbb3588a4141`
  (`docs: freeze S11-A methodology baseline`).
- S11-B: `feat: prepare S11 experimental instrumentation`.

### Observaciones y desvíos respecto del plan

Los estados de decisión se restringen a `PASS`, `FAIL` y `NOT_DEMONSTRATED`. Dependencias,
capacidad implementada, CI previa y despliegue no se registran como resultados experimentales.
Los 16 reportes productivos observados al corte no son una muestra y quedan excluidos de toda
medición S11.

S11-B incorpora manifests inicial/final, datasets sintéticos versionados, overlay Docker aislado,
reset idempotente, exportación ORM read-only, telemetría correlacionada HTTP/Celery, harness
full-stack futuro para EX-02…EX-08 y custodia persistente con clasificación, redacción y SHA-256.
EX-09 y EX-10 quedan preparados a nivel de insumos e instrumentación para S11-C. Esta preparación
no contiene `PASS/FAIL` de hipótesis, resultados académicos ni cambios sobre producción.

La política `S11-CUSTODY-v1.0` fija retención `INTERNAL=90 días` y
`EXPERIMENTAL=365 días` desde `finished_at`; `PUBLIC` exige aprobación explícita de Monty
Emmanuel Pohl Amarilla; `SENSITIVE` permanece deshabilitado y `SECRET` nunca se persiste.
Primary, backup y restore se ubican respectivamente en `var/s11-runs`, `var/s11-backup` y
`var/s11-restore`. La evidencia histórica permanece `HISTORICAL_UNVERIFIABLE`, pero la repetición
independiente bajo la nueva época dejó `S11A-P02=PASS`, `S11A-P03=PASS` y C1 `PASS`. EX-02 y EX-03
no se ejecutaron ni quedaron autorizados por este cierre; requieren su propio pre-gate.

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
> procedimiento manual de migración de Heroku.
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
| Latencia de red Heroku ↔ Supabase | Medida y documentada | — | Pendiente |
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
| ADR-0004 | Ledger auditable con importes inmutables | S2 | Aceptada — el archivo existe desde el cierre del Sprint 2 y declara ese estado; la tabla arrastraba la marca de planificación |
| ADR-0005 | Google OAuth 2.0 + JWT | S3 | Aceptada — el archivo existe desde el cierre del Sprint 3 y declara ese estado; la tabla arrastraba la marca de planificación |
| ADR-0006 | Identidad HMAC y almacenamiento privado | S4 | Aceptada — escrita el 22/08/2026 con las doce decisiones cerradas en la Fase A.1 |
| ADR-0007 | Almacenamiento de fotografías | S6 | Aceptada — escrita e integrada el 28/08/2026; fija bucket privado, validación/normalización, hash original y compensación acotada |
| ADR-0008 | Integración Gemini desacoplada mediante adapter de IA | S8 | Aceptada — escrita en S8-A el 29/08/2026; fija la frontera del proveedor, el fake con la misma interfaz y la prohibición del proveedor real en CI |
| ADR-0009 | Clasificación IA asíncrona mediante Celery | S8 | Aceptada — escrita en S8-A el 29/08/2026; distingue los cinco modos de fallo y fija idempotencia, reintentos acotados y la semántica de `CLASIFICADO` |
| ADR-0010 | Créditos por institución y conversión 1:1 | S9 | Aceptada — integrada mediante PR #35 |
| ADR-0011 | Privacidad configurable del ranking | S4 | Aceptada — escrita el 22/08/2026 y ampliada con la recuperación de la preferencia (`GET` + `PATCH`); la resolución del nombre visible es del S11 |
| ADR-0012 | Detección de duplicados por proximidad | S6 | Aceptada — escrita e integrada el 28/08/2026; warning advisory privacy-safe, radio PostGIS y ausencia explícita de IA |
| ADR-0013 | Política de reversión, revalidación y ciclos de resolución | S10 | `ACCEPTED` — implementada, desplegada y verificada en producción durante el cierre de S10 |
| ADR-0014 | Modelo de usuario personalizado desde la primera migración | S1 | Aceptada |
