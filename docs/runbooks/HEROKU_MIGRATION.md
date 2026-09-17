# Runbook — Migración de infraestructura Railway → Heroku (baseline S9)

**Estado:** BLOQUEADO en la Fase D (acceso a Heroku).
**Fecha de la verificación:** 2026-09-17.
**Alcance:** mover el consumidor de la base (backend Django + Celery) de Railway a Heroku,
manteniendo **la misma** instancia Supabase. No es un gate de S10.

> **S10 permanece bloqueado.** No se despliega `feat/s10-implementation`, no se hace merge de
> PR #39 y no se toca `da232c70a07309597296b6576f9b04f8d16919be`.

---

## 1. Punto de partida verificado

| Componente | Estado al 2026-09-17 | Evidencia |
|---|---|---|
| Supabase PostgreSQL | **Vivo**, 17.6 | `SELECT version()` vía Session Pooler |
| PostGIS | **3.3.7** en esquema `gis` | `gis.PostGIS_Lib_Version()` |
| Datos de producción | **Presentes** (16 reportes, 4 usuarios, 3 instituciones) | conteos reales, no estimaciones |
| Migraciones aplicadas | **31**, ninguna de S10 | `django_migrations` |
| Storage | 2 buckets **privados**, 4 objetos | `storage.buckets` |
| Backend Railway | **INEXISTENTE** — `404 Application not found` | `GET /api/v1/health/` |
| Frontend Vercel | **200**, pero apunta a la API caída | `GET /` |

La API de producción está caída desde que expiró Railway. La base **no** está comprometida:
la migración cambia únicamente quién consume la base, no la base.

### Trampa de las estadísticas

`pg_stat_user_tables.n_live_tup` devuelve **0 en todas las tablas**. Es un artefacto del
reinicio tras despausar (`pg_postmaster_start_time()` = 2026-09-17 14:13 UTC), no pérdida de
datos. **Siempre confirmar con `count(*)` real antes de declarar pérdida de datos.**

---

## 2. Topología objetivo

```text
Vercel (frontend, sin cambios)
        │  NEXT_PUBLIC_API_URL → dominio Heroku
        ▼
Heroku app única: ciudadania-activa-api   (stack: container)
        ├── proceso web     → gunicorn
        ├── proceso worker  → celery worker
        └── proceso beat    → celery beat   (definido, escalado a 0 en S9)
        │
        ├── Heroku Key-Value Store (Redis) → REDIS_URL
        └── Supabase (PostgreSQL + PostGIS + Storage), sin cambios
```

**Una sola app Heroku con tres tipos de proceso**, no tres apps. Comparten build, config vars
y release: es la estructura más simple compatible con el proyecto y evita triplicar el
inventario de variables.

### Por qué stack `container` y no buildpack

El buildpack Python de Heroku **no** provee GDAL, GEOS ni PROJ. GeoDjango
(`django.contrib.gis.db.backends.postgis`) no arranca sin ellas. `backend/Dockerfile` ya las
instala (`gdal-bin`, `libgdal-dev`, `libgeos-dev`, `libproj-dev`) y es el mismo build que
Railway usaba. Reutilizarlo elimina toda una clase de fallos de arranque.

### Cómo se entrega el código

Railway usaba `Root Directory: /backend`. Heroku no tiene equivalente, y `heroku.yml` no
permite fijar un contexto de build distinto de la raíz del árbol recibido. Se resuelve
empujando `backend/` como subtree:

```bash
git subtree push --prefix backend heroku-baseline main
```

Así el contexto de build es `backend/`, `backend/heroku.yml` queda en la raíz del árbol que
Heroku recibe y el `Dockerfile` no necesita ningún cambio de rutas.

---

## 3. Migraciones automáticas: controladas

Railway ejecutaba `python manage.py migrate --noinput` en su **pre-deploy**, configurado en el
panel de Railway, **no en el repositorio**. Por eso no hay nada que "desactivar" en el código:
hay que **no reintroducirlo**.

`backend/heroku.yml` **no define fase `release:`**. Esa ausencia es la medida de control y está
documentada dentro del propio archivo para que no se "arregle" por descuido.

Consecuencia: el baseline S9 arranca contra el esquema existente sin alterarlo. Las
migraciones pasan a ser una operación manual y deliberada:

```bash
# Solo cuando un gate lo autorice, y siempre con pg_dump previo.
heroku run --app ciudadania-activa-api python manage.py migrate --noinput
```

---

## 4. Inventario de variables de entorno (baseline S9)

Verificado contra `main@730d69ed` (`backend/config/settings/base.py`, `prod.py`) y `.env.example`.

### Secretos — nunca imprimir valores

| Variable | Propósito |
|---|---|
| `DJANGO_SECRET_KEY` | Obligatoria: `prod.py` aborta el arranque si falta |
| `DATABASE_URL` | Session Pooler de Supabase, `sslmode=require` como mínimo |
| `REDIS_URL` | Caché + broker + result backend de Celery |
| `DOCUMENT_HMAC_SECRET` | HMAC-SHA-256 de la cédula (Sprint 4) |
| `SUPABASE_SERVICE_ROLE_KEY` | Acceso a Storage privado |
| `GEMINI_API_KEY` | Clasificación asistida por IA |
| `BREVO_API_KEY` | Correo transaccional |
| `GOOGLE_OAUTH_CLIENT_ID` | OAuth (público de hecho, se trata como config sensible) |

### Configuración no secreta

| Variable | Valor para el baseline |
|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.prod` |
| `DJANGO_DEBUG` | `False` |
| `DJANGO_ALLOWED_HOSTS` | dominio Heroku definitivo (obligatoria: `prod.py` aborta si falta) |
| `CORS_ALLOWED_ORIGINS` | `https://ciudadania-activa.vercel.app` |
| `DB_SEARCH_PATH` | `public,gis,extensions` — sin `gis`, GeoDjango no resuelve `geometry` |
| `SUPABASE_URL` | proyecto original |
| `SUPABASE_STORAGE_BUCKET_PRIVATE` | `verificaciones` |
| `SUPABASE_STORAGE_BUCKET_REPORTS` | `reportes` |
| `API_VERSION`, `GEMINI_MODEL`, `BREVO_API_URL`, `DEFAULT_FROM_EMAIL` | según `.env` actual |
| `AI_CLASSIFICATION_ENABLED`, `AI_CONFIDENCE_THRESHOLD`, `AI_MAX_RETRIES`, `AI_REQUEST_TIMEOUT_SECONDS` | según `.env` actual |
| `JWT_ACCESS_TOKEN_LIFETIME_MINUTES`, `JWT_REFRESH_TOKEN_LIFETIME_DAYS` | según `.env` actual |
| `REPORT_DUPLICATE_RADIUS_METERS`, `PUBLIC_MAP_CACHE_TTL_SECONDS` | según `.env` actual |

### Variables que NO existen en este proyecto

Aparecen en listas genéricas de migración; aquí serían ruido o error:

| Nombre asumido | Realidad |
|---|---|
| `DATABASE_SCHEMA` | No existe. Es **`DB_SEARCH_PATH`** |
| `CSRF_TRUSTED_ORIGINS` | `main` no la lee. La API es JWT, sin cookies de sesión cross-site |
| `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | **Derivadas** de `REDIS_URL` en `base.py`. Fijarlas a mano introduce deriva |
| `SUPABASE_ANON_KEY` | El backend no la usa |
| `SUPABASE_STORAGE_BUCKET` | Son **dos** variables, `_PRIVATE` y `_REPORTS` |
| `REPORT_AUTO_CLOSE_HOURS` | Está en `.env.example` pero **`main` no la lee**: la consume S10 |

---

## 5. Redis

**Proveedor:** Heroku Key-Value Store (`heroku-redis`). El plan gratuito ya no existe; el plan
pago más pequeño cubre el uso real (caché + broker de un worker con `--concurrency=2`).

Aporta `REDIS_URL` automáticamente. Dos cuidados:

1. **TLS.** Los planes que exigen TLS entregan `rediss://` con certificado autofirmado. Celery
   y `redis-py` fallan la validación salvo que la URL lleve `?ssl_cert_reqs=none`. Como
   `CELERY_BROKER_URL` y `CELERY_RESULT_BACKEND` derivan de `REDIS_URL`, el sufijo debe ir en
   `REDIS_URL` y corregirse **cada vez que Heroku rota la credencial**.
2. **Persistencia.** El broker no es la fuente de verdad: las tareas son idempotentes y el
   estado vive en PostgreSQL. La política de evicción por defecto es suficiente para S9.

---

## 6. Celery

| Proceso | Comando | Baseline S9 |
|---|---|---|
| worker | `celery -A config.celery:app worker --loglevel=info --concurrency=2` | `ps:scale worker=1` |
| beat | `celery -A config beat --loglevel=info` | `ps:scale beat=0` |

**`main@730d69ed` no define `CELERY_BEAT_SCHEDULE`.** El scheduler
`reports-auto-close-every-15-minutes` (tarea `reports.auto_close`, cada 15 min) lo introduce
S10. Levantar Beat en el baseline no ejecutaría nada, y dejarlo a 0 garantiza que ninguna tarea
de S10 se dispare antes de tiempo.

Orden obligatorio: **primero worker, después beat.** Cuando S10 se autorice, Beat se escala a
**exactamente 1** dyno; dos instancias duplicarían cada cierre automático.

---

## 7. Secuencia de despliegue del baseline

Solo tras cumplir las precondiciones (Supabase verificado, backup fresco, acceso a Heroku).

```bash
heroku login
heroku create ciudadania-activa-api --stack container --region us
heroku addons:create heroku-redis:mini --app ciudadania-activa-api
heroku config:set --app ciudadania-activa-api DJANGO_SETTINGS_MODULE=config.settings.prod ...
heroku git:remote --app ciudadania-activa-api --remote heroku-baseline

# SHA del baseline: 730d69ede7033c3487b68f79b1828fe6f2f43180
git subtree push --prefix backend heroku-baseline main

heroku ps:scale --app ciudadania-activa-api web=1 worker=1 beat=0
```

`DJANGO_ALLOWED_HOSTS` y `CORS_ALLOWED_ORIGINS` solo pueden fijarse definitivamente cuando se
conozca el dominio Heroku; el primer deploy puede requerir un segundo `config:set`.

### Health checks a ejecutar después

```bash
curl -s https://<app>.herokuapp.com/api/v1/health/    # → {"status":"ok","db":true,"redis":true}
heroku run --app ciudadania-activa-api python manage.py showmigrations   # reports_app <=0002, rewards_app <=0001
heroku ps --app ciudadania-activa-api                 # web=1 worker=1 beat=0
heroku logs --app ciudadania-activa-api --dyno worker --tail   # "celery@... ready"
```

`/api/v1/health/` ya cubre DB y Redis en una sola llamada, y está exento de
`SECURE_SSL_REDIRECT` (`SECURE_REDIRECT_EXEMPT` en `prod.py`), así que responde también por HTTP
plano sin `X-Forwarded-Proto` — el mismo motivo por el que funcionaba en Railway.

---

## 8. Frontend (Vercel)

Cambio mínimo y **posterior** al deploy: `NEXT_PUBLIC_API_URL` → dominio Heroku, y redeploy.
No se toca nada más. `CORS_ALLOWED_ORIGINS` en Heroku debe incluir el dominio de Vercel antes
de repuntar el frontend, o el primer request fallará por CORS.

---

## 9. Rollback

### Rollback de aplicación (Heroku)

```bash
heroku releases --app ciudadania-activa-api
heroku releases:rollback vNN --app ciudadania-activa-api
```

Cada `config:set` y cada deploy crean un release; el rollback restaura build y config juntos.

### Rollback de frontend (Vercel)

Revertir `NEXT_PUBLIC_API_URL` y promover el deployment anterior.

### Rollback de datos

**No es necesario para esta migración.** Solo cambia el consumidor de la base; el esquema y los
datos no se tocan, y sin fase `release:` ningún deploy puede alterarlos. El rollback de datos
solo entra en juego si alguien ejecuta `migrate` manualmente.

**Recovery point:**

```text
~/backups/ciudadania-activa/pre_heroku_migration_20260917_111906.dump
formato custom - 328 673 bytes - 624 entradas TOC - pg_restore --list OK
```

Restauración (nunca sobre producción sin autorización explícita):

```bash
pg_restore --exclude-schema=vault --no-owner --no-acl -d "<TARGET_URL>" <dump>
```

El destino necesita PostGIS compatible con **3.3.7**. La extensión `supabase_vault` no existe
fuera de Supabase: `--exclude-schema=vault` evita el error esperado.

---

## 10. Bloqueo actual

```text
HEROKU_ACCESS_UNAVAILABLE
```

No hay CLI de Heroku instalada, ni `~/.netrc`, ni `~/.heroku`, ni `HEROKU_API_KEY`, ni remote
git de Heroku. No se puede inspeccionar apps, pipelines, addons, regiones ni stacks, y por
tanto no se puede desplegar ni validar web/worker/beat/Redis.

**Desbloqueo:** instalar la CLI y autenticarse en una sesión interactiva
(`curl https://cli-assets.heroku.com/install.sh | sh` y `heroku login`), o exportar un
`HEROKU_API_KEY` de una cuenta con acceso al proyecto. Hecho eso, la Fase D se reanuda desde
`heroku apps`.
