# Runbook — Migración de infraestructura Railway → Heroku (baseline S9)

**Estado:** MIGRACIÓN COMPLETA. Baseline S9 operativo en Heroku, Storage verificado y Vercel
apuntando al backend nuevo. Queda una deuda de seguridad abierta (§11).
**Fecha de la verificación:** 2026-09-17.
**App:** `ciudadania-activa-api` · `https://ciudadania-activa-api-5b4f8e81977b.herokuapp.com`
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
| Backend Heroku | **web.1 up + worker.1 up**, `{"status":"ok","db":true,"redis":true}` | `GET /api/v1/health/` |
| Frontend Vercel | **200**, todavía apuntando a la URL vieja de Railway | `GET /` |

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

## 10. Estado real tras el despliegue

### Verificado en Heroku

```text
app            ciudadania-activa-api   (stack container, región us, release v6)
web.1          up      gunicorn config.wsgi:application --bind 0.0.0.0:$PORT
worker.1       up      celery ... ready, 2 tareas (ai.classify_report, notifications.send_notification)
beat           0 dynos (S9 no tiene CELERY_BEAT_SCHEDULE)
redis          heroku-redis:mini  redis-symmetrical-73308  (rediss://, ssl_cert_reqs=none)
health         {"status":"ok","version":"0.1.0","db":true,"redis":true}
migraciones    reports_app [X]0001 [X]0002 · rewards_app [X]0001 — ninguna S10
datos          reports=16 users=4 institutions=3 categories=10 audit_logs=9 · SRID 4326 · PostGIS 3.3.7
API            /api/v1/public/categories/ → 10 categorías
               /api/v1/public/reports/?bbox=… → 10 reportes (consulta espacial real)
CORS           permite https://ciudadania-activa.vercel.app, rechaza otros orígenes
```

El worker registra **solo** las dos tareas de S9. La ausencia de `reports.auto_close` confirma
que el código desplegado es el baseline y no S10.

### Secretos heredados de Railway — cargados y verificados

Los cuatro valores se copiaron del panel de Railway el 2026-09-18 y se cargaron como config vars
de Heroku. Nunca pasaron por el repositorio ni por un archivo.

```text
DOCUMENT_HMAC_SECRET        presente   → verificación de identidad operativa
SUPABASE_SERVICE_ROLE_KEY   presente   → Storage operativo
GOOGLE_OAUTH_CLIENT_ID      presente   → login con Google operativo
DJANGO_SECRET_KEY           presente   → igualado al de producción
```

`SUPABASE_URL` se había derivado del project-ref antes de tener acceso al panel; las URLs
firmadas resuelven contra ese mismo proyecto, de modo que la derivación era correcta.

Storage comprobado contra los objetos reales, en **solo lectura**:

```text
bucket privado  = verificaciones     bucket reportes = reportes
signed_url      OK en ambos buckets, host = el proyecto Supabase original
download        OK byte-exacto: 16720 y 57077 bytes, coinciden con storage.objects
objeto ausente  falla limpio, sin exponer el bucket
buckets         siguen privados (public = f), 2 objetos cada uno, nada modificado
```

Detalle menor, **preexistente y no introducido por la migración**: un objeto inexistente levanta
`StorageUnavailableError` en vez de `StorageObjectNotFoundError`, porque Supabase responde `400`
y no `404` a la descarga. El comportamiento es idéntico al que había en Railway; queda anotado
para cuando se toque `apps/core/services/storage.py`.

**`REPORT_AUTO_CLOSE_HOURS` no se migró**: `main` no la lee, la consume S10.

### Vercel — repuntado y verificado

`NEXT_PUBLIC_API_URL` = `https://ciudadania-activa-api-5b4f8e81977b.herokuapp.com`, y el
frontend se redesplegó (deployment `j1r07tkc7`, aliasado a `ciudadania-activa.vercel.app`).

Comprobado sobre los **bundles realmente servidos**, no sobre el panel:

```text
8 chunks analizados → 1 host de backend: ciudadania-activa-api-…herokuapp.com
referencias a railway.app: 0
GET / y GET /mapa → 200
```

Y las tres llamadas que hace el navegador, con el `Origin` de Vercel:

```text
/api/v1/health/                   200  status=ok    ACAO = https://ciudadania-activa.vercel.app
/api/v1/public/categories/        200  10 items     ACAO = https://ciudadania-activa.vercel.app
/api/v1/public/reports/?bbox=…    200  10 items     ACAO = https://ciudadania-activa.vercel.app
```

#### Por qué el panel de Vercel no dejaba cambiarla

La variable existía con tipo **Secret** (sensible) y prefijo `NEXT_PUBLIC_`, que son requisitos
contradictorios: el prefijo ordena incrustar el valor en el bundle del navegador y el tipo
prohíbe exponerlo. Vercel bloquea esa combinación y sugiere convertirla a *Config*, pero **no
permite convertir el tipo de una variable existente**. Hay que borrarla y recrearla:

```bash
vercel env rm  NEXT_PUBLIC_API_URL production --yes
printf 'https://ciudadania-activa-api-5b4f8e81977b.herokuapp.com' \
  | vercel env add NEXT_PUBLIC_API_URL production      # se crea como Config
```

`vercel env ls` muestra después un blob `eyJ2IjoidjIi…`, que es el sobre cifrado y no el valor.
Para confirmar el valor real hay que usar `vercel env pull`. **Ese archivo trae todas las
variables de producción en claro: hay que borrarlo en cuanto se lea.**

`NEXT_PUBLIC_GOOGLE_CLIENT_ID` arrastra la misma contradicción (Secret + prefijo público). Hoy
funciona porque se creó antes de que Vercel impusiera la restricción, pero cualquier edición
futura va a chocar con el mismo diálogo.

---

## 11. Deuda abierta — secretos de backend en el proyecto de Vercel

`vercel env ls` sobre el proyecto `ciudadania-activa` devuelve **30 variables, y 28 son del
backend**. El frontend solo necesita las dos `NEXT_PUBLIC_*`.

Entre las 28 hay credenciales de producción de primer orden:

```text
DATABASE_URL                 DJANGO_SECRET_KEY            DOCUMENT_HMAC_SECRET
SUPABASE_SERVICE_ROLE_KEY    GOOGLE_OAUTH_CLIENT_SECRET   EMAIL_HOST_PASSWORD
TELEGRAM_BOT_TOKEN           GEMINI_API_KEY               REDIS_URL
```

El README ya registra esta deuda y distingue la configuración mínima requerida del estado real
del proyecto de Vercel. Las 28 variables sobrantes siguen presentes hasta que exista una operación
de seguridad separada para retirarlas y rotar las credenciales afectadas.

**Alcance preciso, sin alarmismo:** Next.js solo incrusta en el bundle del navegador las
variables con prefijo `NEXT_PUBLIC_`, así que estas 28 **no** se están sirviendo a los usuarios
—lo verificamos leyendo los chunks—. El problema es de radio de exposición: están disponibles
para cualquier paso de build, para cualquier código server-side del frontend y para cualquier
persona con acceso al proyecto de Vercel, sin que nada de eso las necesite. Un `vercel env pull`
las baja todas en claro, como acabamos de comprobar.

**Remediación sugerida**, que no se ejecutó porque borrar variables del proyecto de producción
es destructivo y excede este gate:

1. confirmar que ningún build ni ruta server-side del frontend las lee;
2. eliminar las 28 del proyecto de Vercel, dejando solo las dos `NEXT_PUBLIC_*`;
3. **rotar** las que quedaron expuestas —especialmente `DOCUMENT_HMAC_SECRET`, que no puede
   rotarse sin recalcular `citizen_profiles.document_hmac`, así que necesita su propio plan—;
4. actualizar el README después de la remediación para registrar que la deuda quedó cerrada.

El paso 3 es el que tiene coste real y conviene planificarlo aparte.
