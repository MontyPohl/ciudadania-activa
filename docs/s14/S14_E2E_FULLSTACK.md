# Sprint 14 — E2E full-stack local

**Corrida registrada:** 20/10/2026, `07f3e08`, base `dc8c4b8`.
**Resultado:** 8/8 flujos F1…F8 más la captura del dashboard: **9/9 en 57,7 s**,
sin excepciones `pageerror`. Post-chequeo del ledger: **32 `ACREDITADO` y
1 `PENDIENTE`**. La captura a 1280 px confirma la corrección tipográfica D17.
Videos, captura, log y `SHA256SUMS`: **fuera del repositorio**; hashes publicados
en el [informe](S14_INFORME_PRUEBAS.md#51-integridad-de-la-evidencia).

## 1. Alcance y requisitos

Esta capa recorre la UI con Chromium, el BFF de Next.js y la API real de Django,
con PostGIS y Redis locales. Usa el servidor REST de Storage falso
`backend/scripts/s14_fake_storage.py`, sin modificar el cliente de producto.
No usa `experiments_app` ni el backend experimental de Storage de S11.

Requiere dependencias backend/frontend y Chromium ya instalados, la imagen local
`ca-s12-backend-test:dev`, la red `s12-dev-net`, PostGIS `s12-dev-db` y Redis
`s12-dev-redis`. No reconstruyas la imagen si no cambió su configuración o sus
requirements. Reservá los puertos de loopback 58014 (API), 59014 (Storage) y
3114 (frontend). El config de Playwright fija 3114; los otros puertos son
configurables en el entorno local.

Usá una base exclusiva llamada `ciudadania_s14e2e`, recién creada, y **solo la base
lógica 4 de Redis local**. La preparación y la limpieza siguientes borran ese
mundo sintético; no se ejecutan contra bases compartidas o productivas. Si ya hay
una corrida vigente, conservá primero su evidencia. No cargues el `.env` real.

## 2. Configuración local sin valores de credenciales en el repo

Desde la raíz del repositorio, prepará variables para las carpetas externas y un
archivo de entorno local que contenga exclusivamente valores sintéticos. No
copies su contenido a la documentación ni lo imprimas en logs.

```bash
S14_WORKTREE=$(pwd -P)
S14_OUTPUT=$(mktemp -d)
S14_LOCAL_ENV=$(mktemp)
chmod 600 "$S14_LOCAL_ENV"
```

Completá `S14_LOCAL_ENV` con estas claves antes de continuar. Los nombres de
variables se documentan; los valores sensibles no se incluyen como ejemplos.
Las rutas `/work` y `/out` de los comandos son **internas al contenedor**.

| Variable | Configuración requerida |
|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.dev` (`DEBUG=True`) |
| `DJANGO_SECRET_KEY` | Valor sintético exclusivo de este entorno local |
| `DATABASE_URL` | PostGIS local, base `ciudadania_s14e2e`; host permitido por el seed |
| `REDIS_URL` | Redis local `s12-dev-redis`, base lógica 4 |
| `SUPABASE_URL` | `http://127.0.0.1:59014` (servidor falso) |
| `SUPABASE_SERVICE_ROLE_KEY` | Valor sintético; el mismo que recibe `--service-role` del servidor falso |
| `SUPABASE_STORAGE_BUCKET_PRIVATE` | Nombre de bucket sintético para verificaciones |
| `SUPABASE_STORAGE_BUCKET_REPORTS` | Nombre de bucket sintético para reportes y evidencia |
| `DOCUMENT_HMAC_SECRET` | Valor sintético exclusivo del mundo local |
| `PUBLIC_RANKING_CACHE_TTL_SECONDS` | `1` |
| `PUBLIC_MAP_CACHE_TTL_SECONDS` | `1` |
| `DASHBOARD_METRICS_CACHE_TTL_SECONDS` | `1` |

Fijá `AI_CLASSIFICATION_ENABLED=false` y dejá vacías las claves de proveedores
externos. El comando las neutraliza explícitamente y monta únicamente `backend/`
en modo lectura: así `config/settings/base.py` no puede cargar el `.env` de la
raíz del repositorio dentro del contenedor. No levantes worker ni Beat para esta
corrida. El servicio real de creación encola sus tareas en Redis, pero no se ejecutan ni salen correos. El seed nunca crea movimientos del
ledger a mano.

## 3. Levantar el backend y sembrar

El servidor falso guarda objetos en memoria y soporta carga, lectura, firma y
borrado mediante la API REST que usa el cliente de producto. Al reiniciarlo se
pierden esos objetos; por eso se prepara el mundo completo desde cero.

```bash
docker exec s12-dev-db dropdb -U ciudadania --if-exists ciudadania_s14e2e
docker exec s12-dev-db createdb -U ciudadania ciudadania_s14e2e
docker exec s12-dev-redis redis-cli -n 4 FLUSHDB

docker run -d --name s14-e2e-web --network s12-dev-net \
  -p 127.0.0.1:58014:8000 -p 127.0.0.1:59014:59014 \
  --user "$(id -u):$(id -g)" \
  --env-file "$S14_LOCAL_ENV" \
  -e AI_CLASSIFICATION_ENABLED=false -e S11_EXPERIMENTS_ENABLED=false \
  -e BREVO_API_KEY= -e OPENAI_API_KEY= -e GEMINI_API_KEY= \
  -v "$S14_WORKTREE/backend":/work/backend:ro \
  -v "$S14_OUTPUT":/out -w /work/backend \
  ca-s12-backend-test:dev sh -c \
  'python scripts/s14_fake_storage.py --host 0.0.0.0 --port 59014 \
     --service-role "$SUPABASE_SERVICE_ROLE_KEY" &
   python manage.py migrate --no-input && python manage.py runserver 0.0.0.0:8000'
```

Esperá a que `http://127.0.0.1:58014/api/v1/health/` responda `ok`, con DB y Redis
disponibles. Recién entonces ejecutá la siembra:

```bash
docker exec s14-e2e-web python manage.py shell -c \
  "import runpy; runpy.run_path('scripts/s14_e2e_seed.py', run_name='__main__')"
```

El seed aborta antes de escribir si `DEBUG` no es verdadero, el nombre de base
no es `ciudadania_s14e2e`, el host no pertenece a la allowlist local
(`s12-dev-db`, `s14-dev-db`, `localhost`, `127.0.0.1`), Storage no apunta a loopback,
ya existen usuarios o falta el directorio externo de salida.

Siembra catálogos, ciudadanos sintéticos, un operador y un admin. Construye por
servicios nueve reportes resueltos (900 créditos de ANDE), un reporte vecino para
el aviso de duplicado y una recompensa simulada. Las fotos son distintas para
respetar la unicidad de `file_hash`. Genera `actors.json` en el volumen externo de
salida, con permisos `0600`, y no imprime su contenido. Ese archivo contiene
sesiones y datos sintéticos de identidad: no lo agregues al repositorio ni a la
evidencia publicable.

## 4. Ejecutar Playwright

```bash
set -o pipefail
cd "$S14_WORKTREE/frontend"
S14_E2E_API_URL=http://127.0.0.1:58014 \
S14_E2E_ACTORS="$S14_OUTPUT/actors.json" \
S14_E2E_OUT="$S14_OUTPUT/fullstack" \
  npm run test:e2e:fullstack 2>&1 | tee "$S14_OUTPUT/fullstack.log"
```

`playwright.fullstack.config.ts` construye el frontend con `NEXT_PUBLIC_API_URL`
apuntando a la API local y levanta `next start` en loopback: no hace falta
arrancar otro frontend. Usa un worker, ejecución serial, cero reintentos, video
siempre y trazas/capturas ante fallos. La fixture común verifica los `pageerror`
también en los contextos de cada actor.

La sesión entra por la cookie `ca_refresh` emitida localmente por el seed, con
`HttpOnly` y path `/api/auth`. El BFF hace el refresh real contra Django.
Se bloquean las teselas externas de OSM; las llamadas a la API del dominio van
al backend real.

| Flujo | Comprobación |
|---|---|
| F1 | Envío de identidad con selfie, revisión admin y aprobación; selfie desde Storage falso |
| F2 | Creación con aviso de posible duplicado, continuación y respuesta 201 |
| F3 | Consulta pública anónima del reporte y confirmación de otro ciudadano: +10 pendientes |
| F4 | Validación institucional y acreditación: el autor pasa de 900 a 950 créditos |
| F5 | Atención, evidencia y resolución: el autor llega a 1000 créditos |
| F6 | Ranking público refleja los puntos acreditados |
| F7 | Saldo por institución y movimientos del reporte |
| F8 | Canje simulado y recuperación del mismo vale en el segundo intento mensual |
| Captura | `/institucion/metricas` a 1280 × 800, archivo `metricas-1280.png`; revisión visual D17 |

Los ocho flujos forman una cadena serial. Si falla un paso, corregí lo afectado,
recreá la base, Redis 4 y Storage, y repetí la cadena necesaria. Un `--grep @Fn`
aislado no repone el estado ni los IDs construidos en pasos anteriores; usalo
solo si se prepararon explícitamente sus precondiciones. Registrá el fallo y la
repetición sin sobrescribir la evidencia anterior.

## 5. Post-chequeo e integridad

El post-chequeo consulta solo estados y conteos:

```bash
docker exec s12-dev-db psql -U ciudadania -d ciudadania_s14e2e -At \
  -c 'SELECT status, count(*) FROM reward_transactions GROUP BY status ORDER BY status' \
  | tee -a "$S14_OUTPUT/fullstack.log"
```

Conservá **fuera del repositorio** los `.webm` del directorio de resultados
(incluidos los de los actores), `metricas-1280.png` y `fullstack.log`, junto con
un manifiesto `SHA256SUMS`. Para verificarlo, desde esa carpeta de evidencia:

```bash
sha256sum -c SHA256SUMS
```

El manifiesto del 20/10 incluye 15 videos, el log y la captura. Sus 17 hashes se
transcriben en el informe. No publiques `actors.json` ni el archivo de entorno.

## 6. Límites y relación con las otras verificaciones

No automatiza el login real de Google ni ejecuta clasificación por IA, envío de
correo o Storage real de Supabase (D09). La prueba de refresh del BFF sí es real;
las notificaciones permanecen `PENDIENTE`. Los resultados no miden carga ni
re-adjudican HT-4 o HT-6, y EX-11 sigue diferido (D10).

La capa `frontend/e2e/` usa API simulada, con ocho etiquetas @F focalizadas en
verde en C2. Por el diccionario S11A no cuenta para HT4-01. La verificación
completa del frontend pasó: format, lint, typecheck, 417 pruebas Vitest en
44 archivos, build y 58 E2E con build de producción. Todos los E2E usan la
fixture de `pageerror`.

El [PR #54](https://github.com/MontyPohl/ciudadania-activa/pull/54) se abrió el
20/10: `test` (dominio y compuerta) pasó en 3 min, y `build` y `frontend-e2e`
están en verde; `ci-experiments` seguía en curso al escribir. El despliegue está
pendiente de aprobación del usuario.

## 7. Limpieza local

Después de copiar y verificar la evidencia, detené el contenedor del arnés y
eliminá únicamente su base y su Redis lógico. Conservá los servicios compartidos
del entorno local.

```bash
docker rm -f s14-e2e-web
docker exec s12-dev-db dropdb -U ciudadania ciudadania_s14e2e
docker exec s12-dev-redis redis-cli -n 4 FLUSHDB
rm -- "$S14_LOCAL_ENV"
```

El archivo externo `actors.json` se descarta cuando ya no se necesitan las
sesiones locales. No elimines la carpeta de evidencia verificada como parte de
esta limpieza.
