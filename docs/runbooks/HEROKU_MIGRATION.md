# Runbook — Migración de infraestructura Railway → Heroku (baseline S9)

**Estado al 20/10/2026:** migración completa; S10 y S12 desplegados. Heroku v24,
backend de `main@d9e1c11` desplegado en v22, IA v2 y Brevo activos. Vercel conserva
solo `NEXT_PUBLIC_API_URL` y `NEXT_PUBLIC_GOOGLE_CLIENT_ID` como Config; secretos y
rotaciones se documentan en §§11–13. No se presume cerrada ninguna rotación pendiente.
**App:** `ciudadania-activa-api` · `https://ciudadania-activa-api-5b4f8e81977b.herokuapp.com`
**Lectura histórica:** §§1–4 y §§6–10 registran el baseline S9 del 17–18/09;
no describen el estado productivo actual ni mantienen el bloqueo original de S10.
§11 conserva la remediación ejecutada el 18/09; §5 y §14 incorporan el ajuste Redis.
El inventario vigente, deploy por reconciliación y corte productivo están en **§15**.

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
   y `redis-py` fallan la validación salvo que la URL lleve `?ssl_cert_reqs=none`. Heroku
   borra ese sufijo **cada vez que rota la credencial** (v16 y v18 tiraron worker y beat), así
   que desde el post-S12 `base.py` lo añade solo: toda URL `rediss://` sin `ssl_cert_reqs`
   recibe el valor de `REDIS_SSL_CERT_REQS` (por defecto `none`) antes de derivar caché,
   broker y result backend. Un `ssl_cert_reqs` explícito en la URL siempre gana; ya no hace
   falta editar `REDIS_URL` a mano.
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

**En el baseline S9 no se migró `REPORT_AUTO_CLOSE_HOURS`.** Desde S10 está configurada
y se consume en `base.py`; Beat está activo con una única instancia (ver §15).

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

`NEXT_PUBLIC_GOOGLE_CLIENT_ID` tenía la misma contradicción (Secret + prefijo público).
Se corrigió a Config durante la remediación del 18/09 descrita en §11.

---

## 11. Remediación S9.5 — secretos de backend retirados de Vercel

**Ejecutada el 2026-09-18.** El proyecto de Vercel cargaba 30 variables de entorno, de las que
solo 2 son consumidas por el frontend. Las otras 28 se retiraron de `production` y de `preview`.

### Auditoría de consumo (corte histórico del 18/09/2026)

El frontend lee **exactamente dos** variables de configuración, más dos que no son config vars:

| Variable | Dónde se lee |
|---|---|
| `NEXT_PUBLIC_API_URL` | `src/lib/api/client.ts`, `src/lib/api/publicReports.ts`, `src/lib/auth/backend.ts` |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | `src/features/auth/GoogleSignInButton.tsx` |
| `NODE_ENV` | `src/lib/auth/cookie.ts` — la inyecta Next.js, no es config var |
| `CI` | `playwright.config.ts` — solo en tests, nunca en producción |

Se revisó además: `next.config.ts` (no declara bloque `env`), los scripts de `package.json` (ningún
uso de variables), las tres API routes de `src/app/api/auth/` (solo mencionan «env» en comentarios),
y no existe ningún acceso dinámico del tipo `process.env[...]` ni destructuring. No hay `middleware`.

**Prueba empírica:** tras retirar las 28, el build de producción se completó sin errores. Ninguna
era `BUILD_REQUIRED`.

### Clasificación de las 30 (corte histórico del 18/09/2026)

| Clasificación | N.º | Contenido |
|---|---|---|
| `NEXT_PUBLIC_REQUIRED` | 2 | Conservadas |
| `BACKEND_ONLY`, ya duplicadas en Heroku | 15 | Retirar la copia de Vercel no pierde nada |
| `UNUSED` — nada en `main` las lee | 9 | Ver abajo |
| `BACKEND_ONLY`, ausentes en Heroku | 3 | `GEMINI_API_KEY`, `GEMINI_MODEL`, `DEFAULT_FROM_EMAIL` |
| Solo S10 | 1 | `REPORT_AUTO_CLOSE_HOURS` |

Las 9 muertas: `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`,
`EMAIL_USE_TLS` (el envío va por la API HTTPS de Brevo, no por SMTP — ADR-0015),
`GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`, `TELEGRAM_BOT_TOKEN` y
`SUPABASE_STORAGE_BUCKET_PUBLIC` (sustituida por `_REPORTS`).

**`GOOGLE_OAUTH_CLIENT_SECRET` nunca se usó.** `apps/auth_app/services/google.py` valida el ID
token con `verify_oauth2_token`, comprobando que `aud == GOOGLE_OAUTH_CLIENT_ID`. No hay
intercambio de código de autorización, así que no interviene ningún client secret.

Las 3 ausentes en Heroku no se migraron y no hacía falta: `AI_CLASSIFICATION_ENABLED` tiene
`default=False` y no está definida en ningún entorno, de modo que `GEMINI_API_KEY` y `GEMINI_MODEL`
no se leen; `DEFAULT_FROM_EMAIL` tiene valor por defecto en el código.

Dato de contexto: Railway, el backend que realmente sirvió producción, tenía **20** variables. Las
28 de Vercel eran una copia antigua de un `.env` completo, no la configuración viva.

### Trampa operativa: `vercel env pull` no devuelve los valores sensibles

Para una variable de tipo **Secret**, `vercel env pull` escribe el literal `[SENSITIVE]` en lugar
del valor. Un intento de respaldar las 28 por esa vía produjo 29 placeholders y ningún valor real.

Consecuencias prácticas:

- **No se puede respaldar una variable de tipo Secret con la CLI.** Solo la UI de Vercel la revela.
- Un valor de tipo *Config* sí se lee correctamente.
- Antes de escribir en otro sistema algo leído con `env pull`, comprobar que no es `[SENSITIVE]`.

### Estado final de la remediación del 18/09/2026

```text
Vercel production:  NEXT_PUBLIC_API_URL (Config) · NEXT_PUBLIC_GOOGLE_CLIENT_ID (Config)
Vercel preview:     0 variables
Vercel development: 0 variables
Heroku:             16 variables, todas las que S9 necesita
```

`NEXT_PUBLIC_GOOGLE_CLIENT_ID` estaba registrada como *Secret* pese a llevar prefijo público, la
misma contradicción que bloqueaba `NEXT_PUBLIC_API_URL`. Se corrigió a *Config*. Como la CLI no
podía leer su valor, se verificó primero que el `client_id` incrustado en los chunks de `/login`
coincide con el `GOOGLE_OAUTH_CLIENT_ID` de Heroku — deben coincidir o el login no funcionaría — y
se recreó con ese valor.

### Verificación de fuga en bundles públicos

Se descargaron los 11 chunks únicos servidos en `/`, `/mapa` y `/login` (658 KB) y se buscaron 13
patrones: `railway.app`, `postgres://`, `postgis://`, `rediss://`, `service_role`, `GOCSPX-`
(client secrets de Google), `AIza…` (claves de Google API), `bot<id>:<token>` (Telegram),
`eyJhbGciOi` (JWT, formato de la service-role key), `SECRET_KEY`, `DOCUMENT_HMAC`, `DJANGO_SECRET`.

**Cero coincidencias en los trece.** Las únicas referencias presentes son las esperadas: el host de
Heroku y el `client_id` público de Google.

---

## 12. Rotación de secretos

Los secretos que estuvieron innecesariamente en Vercel deben considerarse expuestos.

| Secreto | Estado | Detalle |
|---|---|---|
| `DJANGO_SECRET_KEY` | **ROTADO** | 64 caracteres vía `secrets.choice`. Ver más abajo |
| `DOCUMENT_HMAC_SECRET` | **SIN ROTAR, a propósito** | Ver §13 |
| `SUPABASE_SERVICE_ROLE_KEY` | **PENDIENTE** | Requiere la consola de Supabase + actualizar Heroku |
| `DATABASE_URL` | **PENDIENTE** | Requiere rotar la contraseña en Supabase + actualizar Heroku |
| `REDIS_URL` | No requiere acción | La credencial es de Heroku y nunca sirvió desde Vercel |
| `GOOGLE_OAUTH_CLIENT_SECRET` | **PENDIENTE** | Consola de GCP. El código no lo usa: puede eliminarse en vez de rotarse |
| `EMAIL_HOST_PASSWORD` | **PENDIENTE** | Consola de Brevo. El código no usa SMTP |
| `TELEGRAM_BOT_TOKEN` | **PENDIENTE** | Invalidar en el proveedor; Telegram quedó fuera del MVP (CT-43), no se usará en S13 |
| `GEMINI_API_KEY` | **PENDIENTE** | Google AI Studio |

Las pendientes exigen la consola de su proveedor y no pueden automatizarse desde aquí. Las cuatro
últimas no están en el inventario vigente de Heroku ni las consume el proveedor activo;
invalidarlas no requiere actualizar la configuración productiva. `GEMINI_*` se conserva
como código histórico, con selección explícita de proveedor.

**Prioridad:** `SUPABASE_SERVICE_ROLE_KEY` y `DATABASE_URL` son las de mayor impacto, porque dan
acceso directo a los datos de producción.

### Rotación de `DJANGO_SECRET_KEY`

`base.py` documenta que SimpleJWT no declara `SIGNING_KEY` y usa `SECRET_KEY` por defecto. La
consecuencia, ya asumida en el código, es que rotarla **invalida los tokens de acceso y refresco
activos**: los usuarios tienen que volver a iniciar sesión.

Se consideró seguro porque `django_session` tenía 0 filas y hay 4 usuarios. Tras la rotación:
health en verde, `manage.py check` sin incidencias, API pública sirviendo.

---

## 13. `DOCUMENT_HMAC_SECRET`: por qué no se rota

**No se rotó, y no debe rotarse sin un plan explícito.**

`citizen_profiles.document_hmac` almacena el HMAC-SHA-256 de la cédula, y el secreto participa en
su generación y en su verificación. Hay 2 registros persistidos. Reemplazar el secreto por uno
nuevo volvería esos HMAC irreconciliables de forma permanente, **sin ningún error visible**: las
cédulas ya verificadas dejarían de coincidir y la detección de duplicados fallaría en silencio.

Un secreto plausible pero incorrecto es peor que la ausencia del secreto, porque la ausencia falla
cerrado (`IdentityConfigurationError` → 503) y el valor incorrecto no.

### Estrategia de rotación futura

Cualquiera de estas tres, con pruebas de compatibilidad y rollback antes de ejecutarse:

1. **Doble secreto en transición.** Introducir `DOCUMENT_HMAC_SECRET_PREVIOUS`: se firma con el
   nuevo y se verifica contra ambos. Requiere tocar `apps/auth_app/services/identity.py`.
2. **Re-HMAC controlado.** Solo es posible si las cédulas en claro están disponibles, lo cual
   contradice el propósito del HMAC. En la práctica implica re-verificar a los ciudadanos.
3. **Migración con columna de versión.** Añadir `document_hmac_version` y convivir con ambos
   esquemas hasta que no queden registros de la versión antigua.

La opción 1 es la única que no exige contacto con los ciudadanos. Ninguna es un cambio de variable
de entorno: todas necesitan código, migración y su propio gate.

Mientras no se rote, el secreto sigue expuesto por haber estado en Vercel. **Es una deuda de
seguridad real y consciente**, no un descuido.

---

## 14. Deuda de infraestructura registrada

| Ítem | Detalle |
|---|---|
| `REDIS_URL` y `ssl_cert_reqs=none` | **Rotación resuelta en PR #46**: `config.redis_url.with_ssl_cert_reqs` añade el parámetro a URL TLS que no lo traen; `REDIS_SSL_CERT_REQS` default `none`, URL explícita prevalece. Sigue pendiente evaluar validación de certificado; la normalización no cambia `CERT_NONE` |
| `preview` sin variables públicas | `preview` no tenía `NEXT_PUBLIC_API_URL` ni `NEXT_PUBLIC_GOOGLE_CLIENT_ID` ni antes ni después de la remediación, de modo que los deployments de preview apuntan a una API vacía. Es preexistente y no lo introdujo esta tarea |
| `StorageObjectNotFoundError` | Un objeto ausente levanta `StorageUnavailableError`, porque Supabase responde `400` y no `404`. Preexistente |
| `BREVO_API_KEY` y remitente | **Configurados en v23/v24 (20/10)**. Correo operativo para identidad y canje. Falta dominio propio autenticado: remitente Gmail con advertencias DKIM/DMARC y riesgo de spam |


---

## 15. Operación vigente — corte 20/10/2026, 01:25 UTC

### Inventario de Heroku (nombres, sin valores)

Estas son las **25 variables presentes** al corte, no una lista de todos los settings
que el código admite. No copies sus valores al repositorio, logs de revisión ni Vercel.
Los tres tipos de proceso de esta app Heroku comparten config vars; indicar que una clave
la consume el worker no implica que Heroku la aísle del proceso web.

```text
AI_CLASSIFICATION_ENABLED
AI_PROMPT_VERSION
AI_PROVIDER
API_VERSION
BREVO_API_KEY
CORS_ALLOWED_ORIGINS
DATABASE_URL
DB_SEARCH_PATH
DEFAULT_FROM_EMAIL
DJANGO_ALLOWED_HOSTS
DJANGO_DEBUG
DJANGO_SECRET_KEY
DJANGO_SETTINGS_MODULE
DOCUMENT_HMAC_SECRET
GOOGLE_OAUTH_CLIENT_ID
OPENAI_API_KEY
OPENAI_MODEL
OPENAI_REASONING_EFFORT
REDIS_URL
REPORT_AUTO_CLOSE_HOURS
REPORT_DUPLICATE_RADIUS_METERS
SUPABASE_SERVICE_ROLE_KEY
SUPABASE_STORAGE_BUCKET_PRIVATE
SUPABASE_STORAGE_BUCKET_REPORTS
SUPABASE_URL
```

`REDIS_SSL_CERT_REQS` es un setting opcional del código, **no está en ese inventario**.
Default `none`; se añade únicamente a `rediss://` que no lleve `ssl_cert_reqs` antes de
derivar caché/broker/result backend. La URL explícita gana, y `redis://` queda intacta.
Con otro proveedor y certificado verificable se puede configurar `required`.
`AI_CONFIDENCE_THRESHOLD`, `AI_MAX_RETRIES`, `AI_REQUEST_TIMEOUT_SECONDS`,
`BREVO_API_URL`, TTLs y lifetimes JWT usan sus defaults al no estar definidos.
Las variables `GEMINI_*` no están configuradas en este inventario; Gemini queda histórico.

Storage se consume en web **y en el worker de IA**, que descarga fotos privadas de reportes.
La clave HMAC corresponde al flujo de identidad; Brevo solo se consume al enviar desde
worker. Ninguno de esos secretos debe volver a Vercel.

### Desplegar backend con un commit de reconciliación del subtree

El simple `git subtree push` de §2 describe el primer baseline. El historial de Heroku
ya tiene reconciliaciones; para las entregas siguientes se usó un commit cuyo **árbol es
exactamente el backend aprobado** y cuyos padres son el tip remoto y el nuevo split.
Se preserva el historial del remote, sin `--force` ni resolver archivos del backend a mano.

Ejecutá desde un checkout limpio del candidato aprobado y con el remote `heroku-baseline`
configurado para esta app. En una actualización estructural, primero generá/validá el
respaldo, activá mantenimiento y detené todos los writers (`web=0 worker=0 beat=0`),
como indica [README](../../README.md#migraciones-estructurales-orden-obligatorio).
No ejecutes esta receta sobre un merge documental en curso.

```bash
# Fijar y revisar el candidato; no dejar que cambie durante el procedimiento.
CA_RELEASE_SHA=$(git rev-parse HEAD)
git fetch heroku-baseline main
CA_REMOTE_SHA=$(git rev-parse refs/remotes/heroku-baseline/main)
CA_SPLIT_SHA=$(git subtree split --prefix=backend "$CA_RELEASE_SHA")
CA_BACKEND_TREE=$(git rev-parse "$CA_RELEASE_SHA:backend")
test "$(git rev-parse "$CA_SPLIT_SHA^{tree}")" = "$CA_BACKEND_TREE" || exit 1
CA_RECONCILIATION_SHA=$(git commit-tree "$CA_BACKEND_TREE"   -p "$CA_REMOTE_SHA" -p "$CA_SPLIT_SHA"   -m "chore(release): reconciliar subtree de Heroku con $CA_RELEASE_SHA")
test "$(git rev-parse "$CA_RECONCILIATION_SHA^{tree}")" = "$CA_BACKEND_TREE" || exit 1
git merge-base --is-ancestor "$CA_REMOTE_SHA" "$CA_RECONCILIATION_SHA" || exit 1
git push heroku-baseline "$CA_RECONCILIATION_SHA:refs/heads/main"
```

Si el remote avanzó, el push debe rechazarse: volvé a inspeccionar el tip y generá otra
reconciliación; no fuerces. Registrá candidato, split, reconciliación, árbol y release.
El push construye/despliega enseguida y **no aplica migraciones**: falta fase `release`.

```bash
heroku run --app ciudadania-activa-api python manage.py showmigrations
heroku run --app ciudadania-activa-api python manage.py migrate --plan
# Solo las migraciones aprobadas y con respaldo previo validado:
heroku run --app ciudadania-activa-api python manage.py migrate --noinput
heroku run --app ciudadania-activa-api python manage.py migrate --check
heroku run --app ciudadania-activa-api python manage.py check
heroku ps:scale --app ciudadania-activa-api web=1
heroku ps:scale --app ciudadania-activa-api worker=1
heroku ps:scale --app ciudadania-activa-api beat=1
heroku ps --app ciudadania-activa-api
```

Verificá cada proceso en ese orden, health, DB/Redis y smokes del cambio antes de retirar
mantenimiento. Conservá un solo Beat; generá/validá el respaldo posterior y verificá
invariantes de negocio en solo lectura. No deshagas una migración mediante `rollback` de
Heroku: ese comando revierte aplicación/configuración, no el esquema ni los datos.

El método quedó registrado en S10 (`9676e94`, release v13), S12 (`9944e11`, v19) y
activación IA (`744e295`, v22). `744e295` tiene como padres `9944e11` y `abe9376`;
su árbol coincide con `backend/` de `d9e1c11`.

**S13 (20/10/2026, release v25):** candidato `828e01a` (head del PR #52), split
`d558d81`, reconciliación `82eafce` (padres `744e295` y `d558d81`), árbol de `backend/`
`04f79c7`. Antes de migrar: conteo en solo lectura (0 filas de Telegram, 0 `PENDIENTE`) y
`pg_dump` `ciudadania_activa_20261020_133500_pre_s13.dump` (SHA-256 `c34b9483…`, 66
tablas con datos). `migrate --plan` listó solo `reports_app.0008`,
`notifications_app.0003` y `social_ingestion_app.0002`, aplicadas con `migrate`, `migrate
--check` y `check` limpios. Respaldo posterior `ciudadania_activa_20261020_135203_post_s13.dump`
(SHA-256 `1d2ad048…`, 65 tablas). Tras el merge (`6e8e883`), `origin/main:backend` es el
mismo árbol desplegado. Beat agenda desde v25 también `notifications.dispatch_due` (cada
5 min) y `rewards.notify_available_rewards` (09:00 locales); el worker las registra.
Desvío menor: el mantenimiento se retiró con `web=1` arriba y antes de escalar worker y
beat, no al final. Estos hashes son trazabilidad, no una
instrucción para volver a desplegar un candidato antiguo.

### IA, Brevo y comprobación productiva

**v22 (20/10 00:35 UTC):** migración `ai_client_app.0003` aplicada y clasificación
encendida con OpenAI, `gpt-6-luna`, esfuerzo `low` y prompt v2. El default del código
sigue v1; OpenAI falla cerrado con versión desconocida. Los tokens se guardan por
clasificación persistida y `ai_usage_summary` los consulta con precios explícitos.
Los comandos `ai_try_classification`/`ai_compare_prompts` son manuales, llaman a OpenAI
incluso sin el flag de producción y no generan filas de clasificación/auditoría.
Ver [API §9](../API.md#9-comandos-de-gestión-de-ia-post-s12).

**v23/v24:** clave de API Brevo y `DEFAULT_FROM_EMAIL` configurados. El remitente es una
cuenta Gmail verificada; se omite su dirección. Brevo advierte DKIM predeterminado y
DMARC por dominio gratuito: se necesita dominio propio autenticado para reducir riesgo
de spam. Hay plantillas de `VERIFICACION_APROBADA`, `VERIFICACION_RECHAZADA` y
`CANJE_REALIZADO`; el resto y reintentos automáticos son S13. El canje no envía el código
del vale en el correo. Para recuperar un `FALLIDO` se usa `resend_notification <uuid>`
tras corregir la causa; repetir la tarea directamente no lo reenvía.

**Corte 01:25 UTC:** web/worker/beat arriba; health OK, migraciones al día y check limpio.
0 clasificaciones IA, 16 reportes, 0 recompensas, 0 canjes; dos notificaciones
`VERIFICACION_APROBADA` en `ENVIADO` (del 23/08 y 29/08, previas a Heroku). No se infiere que todos los tipos se hayan probado
ni que el proveedor haya entregado cada mensaje a la bandeja de entrada.

Respaldos externos en `~/s12-deploy-20261019/`:
`pre-ai-20261020T002812Z.dump` (SHA-256 abreviado `f5ebc1dd…`) y
`post-ai-20261020T003549Z.dump`. No se publica ni inventa un hash completo no aportado.
La medición exploratoria v2 y los pendientes reales están en
[post-S12](../PROGRESO_SPRINTS.md#post-s12--estabilización-y-activación-productiva-1920102026).
