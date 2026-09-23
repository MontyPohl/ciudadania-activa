# S11-B — Instrumentación experimental reproducible

## Estado y límites

Esta infraestructura prepara ejecuciones futuras de `EX-02` a `EX-10` según
`S11A-v1.0`. No contiene resultados académicos y no autoriza una ejecución. `EX-01`
(encuesta) y `EX-11` (ranking/redemption) permanecen fuera del mecanismo.

Las decisiones de custodia `S11A-P02` y `S11A-P03` están formalizadas en
[`S11A_POLITICA_CUSTODIA_Y_ACCESO.md`](../tesis/S11A_POLITICA_CUSTODIA_Y_ACCESO.md). La
verificación histórica `s11-custody-20260921-p02p03-v5` se conserva, pero sus artefactos quedaron
`HISTORICAL_UNVERIFIABLE` por `KEY_CONTINUITY_FAILURE`. La decisión humana de iniciar una nueva
época y repetir P02/P03 y C1 está documentada en
[`S11_HMAC_CUSTODY_CONTINUITY_INCIDENT.md`](S11_HMAC_CUSTODY_CONTINUITY_INCIDENT.md).
P02/P03 se repitieron con `s11-custody-20260921-p02p03-v6` y quedaron en `PASS`, con evidencia
`VERIFIABLE_WITH_CURRENT_CUSTODY_KEY`. C1 se repitió con el run
`s11-8448491d-3347-5c00-9f44-d8ae110e9095` y quedó en `PASS`, también verificable con la clave
actual. Esto completa el rebaseline de custodia, pero no ejecuta ni autoriza EX-02/EX-03: cada
escenario requiere su propio pre-gate. `--custody-approved` continúa siendo una afirmación
explícita del operador por run y no se utilizó en las verificaciones P02/P03 o C1.

`EX-02` está **demostrado**: el run `s11-4df882ed-9dfe-59a2-888b-ca1efc98d6af` (`main@721462a`)
cerró `COMPLETED` con las catorce fases del harness en `rc=0`, y la auditoría independiente de su
evidencia custodiada lo confirmó como `S11C2_EX02_PASS_CONFIRMED`. `EX-03` también está
**demostrado**: el run `s11-6dc3ea66-08c9-51ef-9fe3-70e7dcc67959` (`main@3696d01`) cerró
`COMPLETED` con las catorce fases en `rc=0`, trazabilidad y custodia en `PASS`, y gate
`S11C2_EX03_PASS_CONFIRMED`. `EX-04` también está **demostrado**: el run
`s11-03adb9bc-c9e4-5f8f-b64c-c4360d0c2085` (`main@79a6f28`) cerró `COMPLETED` con las catorce
fases en `rc=0`, trazabilidad y custodia en `PASS`, y gate `S11C2_EX04_PASS`. EX-06 y EX-08
**no** se han ejecutado: sus garantías siguen sin demostrar y cada uno exige su propio pre-gate.

Lo precedieron cinco intentos que quedan como evidencia histórica `NOT_DEMONSTRATED`, conservados
sin reinterpretar: `s11-3b48870b-7979-542b-8a46-1c6dfce7bed8` (`npm` ausente),
`s11-d407a461-ca75-5809-97bd-bcfea7593f3e` (directorio de resultados root-owned, `A1`),
`s11-41eaa7d0-67c8-53b1-aa9a-38068859dd1f` (el harness omitía el separador `--` de npm y ejecutó
la suite ordinaria `e2e/`, `A2`), `s11-95954a43-507b-5093-824d-8f0c83c1a1a7` (la guarda de
configuración de identidad exigía credenciales Supabase que el overlay aislado deja vacías a
propósito, `A5`) y `s11-6efb78d7-ca14-523f-b7e5-7c5d6ee14fad` (la constante PNG escrita a mano en
el spec era un archivo corrupto que `Image.verify()` rechazaba, `A6`). Los defectos de harness y
preflight se corrigieron en `9e2d55d`, el de configuración por adaptador en `83a27f1` y el del
fixture en `721462a`. La desviación `A4` —la pérdida irreversible de artefactos no custodiados
durante el control negativo de la primera corrección— está registrada en
[`S11_A4_EVIDENCE_LOSS_INCIDENT.md`](S11_A4_EVIDENCE_LOSS_INCIDENT.md), junto con la deuda
operativa pendiente (`frontend/.next/` parcialmente root-owned y la comprobación de nivel proceso
que CI no ejerce).

Los flags S11 están apagados por defecto y el guard rechaza todo target que no cumpla
simultáneamente:

- `S11_ENVIRONMENT=isolated-experiment`;
- PostgreSQL local con base prefijada `ciudadania_s11_`;
- Redis local, base lógica `11`;
- storage local marcado, fuera de `/tmp`;
- secreto documental efímero, clave HMAC custodial estable y clave pública Ed25519 de aprobación
  aportados por el operador, nunca versionados.

El overlay [docker-compose.s11.yml](../../infra/docker-compose.s11.yml) no contiene
endpoints ni credenciales productivas. No usar Heroku, Supabase ni Vercel en este flujo.

## Datasets congelados

Los insumos versionados están en
`backend/apps/experiments_app/data/` y su registro es
`dataset-registry.json`:

| Dataset             | Escenarios                       |       Seed | SHA-256                                                            |
| ------------------- | -------------------------------- | ---------: | ------------------------------------------------------------------ |
| `s11-functional-v1` | EX-02…EX-08                      | `20260921` | `66074d1617c4e1b7a32b854090d26556faa9b35a000452787ff3b47d6f0edc1c` |
| `s8-evaluation-v1`  | EX-09, sólo referencia congelada |          — | `3bc54d591dc104aeabeecb2f5cc7c7a38e70ba042e7f045321ab33ae9b5efad4` |
| `s11-load-v1`       | EX-10                            | `20260921` | `c6883b59dc861fb07fae730594c929fdd572a9ed71aee6d8b85c87f71ede0acb` |

Para regenerar en una ruta nueva —el comando se niega a sobrescribir—:

```bash
docker compose -f infra/docker-compose.yml run --rm --no-deps backend \
  python manage.py s11_generate_dataset s11-functional-v1 \
  --seed 20260921 --output /app/var/generated/s11-functional-v1.json
```

La misma seed produce bytes canónicos idénticos. Para verificar los dos artefactos
versionados:

```bash
sha256sum backend/apps/experiments_app/data/s11-functional-v1.json \
  backend/apps/experiments_app/data/s11-load-v1.json
```

Sólo se usan aliases, UUID v5, imágenes PNG mínimas y ubicaciones sintéticas. No se
incluyen cédulas, teléfonos, correos ni ubicaciones reales.

## Crear, verificar y resetear el entorno

Antes de un run futuro, confirmar que la política de custodia sigue vigente y definir fuera de Git
un secreto documental efímero, una clave HMAC custodial y la clave pública Ed25519 de Monty. La
clave HMAC debe conservarse durante toda la retención; rotarla exige verificar con la anterior y
volver a autenticar de forma controlada. La clave privada Ed25519 permanece offline y nunca se
inyecta al backend o al worker:

```bash
export S11_DOCUMENT_HMAC_SECRET='<valor efímero de al menos 32 bytes>'
export S11_CUSTODY_HMAC_KEY='<clave HMAC custodial externa y estable de al menos 32 bytes>'
export S11_PUBLICATION_APPROVAL_PUBLIC_KEY='<clave pública Ed25519: 64 caracteres hexadecimales>'
export S11_HOST_UID="$(id -u)"
export S11_HOST_GID="$(id -g)"
docker compose -p ciudadania-s11 \
  -f infra/docker-compose.yml -f infra/docker-compose.s11.yml \
  up -d db redis backend worker
```

El backend conserva el puerto `8000` dentro de la red Compose, pero el overlay S11
no publica `8000` como puerto fijo del host. Solicita a Docker un puerto efímero
limitado a `127.0.0.1`; el harness consulta la asignación exclusivamente con
`docker compose port backend 8000`, construye una única `S11_BACKEND_URL` y espera
`/api/v1/health/` antes de preparar el run. Ni el operador, Playwright ni el spec
seleccionan o descubren el puerto por su cuenta.

Los comandos siguientes migran únicamente la base aislada, reponen los catálogos y
comprueban que existen 3 instituciones, 10 categorías y 0 reportes:

```bash
docker compose -p ciudadania-s11 \
  -f infra/docker-compose.yml -f infra/docker-compose.s11.yml \
  exec -T backend python manage.py s11_environment create

docker compose -p ciudadania-s11 \
  -f infra/docker-compose.yml -f infra/docker-compose.s11.yml \
  exec -T backend python manage.py s11_environment verify

docker compose -p ciudadania-s11 \
  -f infra/docker-compose.yml -f infra/docker-compose.s11.yml \
  exec -T backend python manage.py s11_environment reset
```

`reset` ejecuta `flush` sólo sobre `ciudadania_s11_experiment`, limpia Redis DB 11 y
vacía exclusivamente el storage que contiene `.s11-isolated-object-root`. Es idempotente
y falla cerrado si falta el marcador.

## Manifest y harness full-stack

El harness real admite `EX-02`…`EX-08`. Levanta frontend, backend, PostgreSQL/PostGIS,
Redis y un worker Celery con concurrencia 2; Playwright no intercepta la API. Esa capacidad está
demostrada de extremo a extremo para `EX-02` —run `s11-4df882ed-9dfe-59a2-888b-ca1efc98d6af`—,
para `EX-03` —run `s11-6dc3ea66-08c9-51ef-9fe3-70e7dcc67959`— y para `EX-04` —run
`s11-03adb9bc-c9e4-5f8f-b64c-c4360d0c2085`—. Hasta el commit `9e2d55d` el
harness nunca llegaba a seleccionar la configuración S11 —el defecto A2 lo hacía arrancar con la
ordinaria—. `EX-05`…`EX-08` siguen admitidos por el harness pero **no ejecutados**; la fase de
`shutdown` detiene backend y worker, y no define teardown para DB, Redis, red ni volumen. El
flujo es:

```text
start → discover backend port → health check → setup → manifest inicial
      → execute → export read-only → validate → reset → log redactado
      → manifest final → shutdown
```

### Preflight frontend del host

El frontend usa **npm** con `frontend/package-lock.json` (lockfile v3). Next.js exige
Node `>=20.9.0` y Playwright exige Node `>=20`. Antes de autorizar un run, preparar las
dependencias y comprobar la CLI sin ejecutar escenarios:

```bash
node --version
npm --version
cd frontend
npm ci
npm exec playwright -- --version
npx playwright install --list
```

El harness repite automáticamente las comprobaciones de Node, npm, lockfile y CLI local antes de
levantar Compose, y añade tres verificaciones más: que exista `frontend/playwright.s11.config.ts`;
que `npm exec playwright -- --version` responda la versión de Playwright («Version X.Y.Z») y no la
de npm —una respuesta con formato de npm delata que npm está interceptando los argumentos
posteriores al paquete—; y que el directorio de resultados de Playwright
(`frontend/test-results/`, el valor por defecto porque la configuración S11 no declara `outputDir`)
exista o pueda crearse, sea escribible y no contenga entradas de otro UID. El preflight crea ese
directorio si falta y escribe y retira en él una sonda efímera; la ruta está en `.gitignore`, de
modo que el árbol sigue limpio. Playwright borra ese directorio entero al empezar, así que un
artefacto heredado de otro dueño —por ejemplo un `.last-run.json` de un contenedor que corrió como
root— aborta el run: el preflight lo rechaza con el motivo explícito y **no** corrige dueños ni
permisos por su cuenta, porque eso excede al proceso experimental. Si falta cualquiera, el
**preflight host** aborta sin crear un `run_id`, manifest ni evidencia experimental.

El comando del escenario se construye como `npm exec playwright -- test
--config=playwright.s11.config.ts --grep=@EX-NN`. El separador `--` es obligatorio: sin él npm
consume `--config` y `--grep` como flags propios y Playwright arranca con la configuración por
defecto, es decir la suite ordinaria `e2e/` en lugar del escenario S11. El harness compara el
comando contra esa forma exacta —ningún argumento adicional, porque Playwright conserva el último
`--config`— y aborta en el precheck si no coincide. Si el preflight ya pasó, el run y su manifest inicial ya existen, y un
ejecutable desaparece durante una fase anterior a la persistencia del log —incluido el arranque de
Playwright—, la fase queda registrada con timestamp, comando, excepción y código `127`;
`harness-execution.json` v2 y el manifest final quedan ambos `FAILED`. El campo
`invalidity_reason` continúa reservado
exclusivamente para manifests `INVALID`; un manifest `FAILED` lo conserva en `null` por contrato.

`harness-execution.json` v2 añade `recorded_at` y `started_at` por fase. Los artefactos históricos
v1 permanecen válidos e inmutables; no se reinterpretan ni reescriben. Fallos en el registro,
custodia, finalización o apagado posteriores al log son fallos de cierre y no están cubiertos por la
garantía anterior; deben reportarse sin convertirlos en resultados funcionales.

Los tests que simulan fallos del storage externo deben ejecutarse fuera del adaptador local S11,
por ejemplo con `S11_LOCAL_STORAGE_ENABLED=false`. Con el overlay S11 activo, el adaptador local
evita deliberadamente `requests` y esos casos no prueban la ruta externa.

Una ejecución futura autorizada se inicia así:

```bash
export S11_DOCUMENT_HMAC_SECRET='<valor efímero de al menos 32 bytes>'
export S11_CUSTODY_HMAC_KEY='<clave HMAC custodial externa y estable de al menos 32 bytes>'
export S11_PUBLICATION_APPROVAL_PUBLIC_KEY='<clave pública Ed25519: 64 caracteres hexadecimales>'
export S11_HOST_UID="$(id -u)"
export S11_HOST_GID="$(id -g)"
python backend/scripts/s11_harness.py \
  --scenario EX-02 \
  --nonce operator-approved-001 \
  --expected-git-sha "$(git rev-parse HEAD)" \
  --custody-approved
```

El árbol Git debe estar limpio. El `run_id` es UUID v5 a partir de timestamp de inicio,
SHA de Git, dataset, escenario, seed y nonce explícito: no usa aleatoriedad no controlada.
El host verifica `HEAD` y limpieza antes de iniciar; el manifest también fija versiones de
Python/Django/Celery/PostgreSQL/PostGIS/Redis, hashes de requirements, fingerprint de
migraciones, tags de imagen declarados y contadores iniciales del estado aislado.
El manifest registra además el host loopback, URL runtime, puerto host asignado,
puerto interno `8000`, mecanismo `docker-compose-dynamic`, timestamp de descubrimiento
y servicios Compose relevantes. El número efímero no forma parte de la metodología:
la reproducibilidad consiste en solicitarlo, descubrirlo, registrarlo y propagar la
misma URL a todos los consumidores. Si Compose no informa una publicación IPv4 loopback
válida o el health check no responde, el harness falla cerrado y nunca usa `8000` como fallback.
Los fallos de arranque, descubrimiento o health previos al manifest abortan sin crear
un run ni evidencia experimental. Después de crear el manifest inicial, un fallo se
propaga al proceso, se registra y produce un manifest `FAILED`; nunca se convierte
silenciosamente en éxito. El harness no calcula ni declara HT-1…HT-6.

EX-09 queda preparado mediante el registro del dataset R2 y el exportador; EX-10 mediante
`s11-load-v1`, instrumentación HTTP/Celery y el overlay aislado. Ambos están
`NOT_READY` para ejecución oficial: sus ejecutores y sus mediciones corresponden a S11-C,
no a S11-B. Este estado no invalida la preparación de infraestructura de B6 y evita
confundirla con una ejecución experimental.

## Exportación read-only y trazabilidad

El exportador acepta un scope externo minimizado, ejecuta la transacción con
`SET TRANSACTION READ ONLY` y además bloquea SQL de escritura. Exporta, con orden estable:

- reportes, estados, historial y ciclos;
- confirmaciones y evidencia de resolución;
- ledger de recompensas;
- clasificación de IA relevante para R2;
- auditoría y relaciones mínimas de actores/catálogos.

`source`, `derived` e `interpretation` están separados. `derived` sólo expone la presencia
y longitud del HMAC; nunca su valor. `interpretation` permanece `null`. La reconciliación
produce exclusivamente `PASS`, `FAIL` o `NOT_DEMONSTRATED`, pero B no la ejecuta sobre
datos oficiales ni incorpora esos estados como resultados de tesis.

Los headers `X-S11-Run-ID`, `X-S11-Scenario-ID` y `X-S11-Request-ID` correlacionan HTTP,
Celery, eventos de dominio, auditoría y artefactos. Los logs HTTP guardan método, endpoint
sin query string, inicio, fin, duración total/DB y status. Celery guarda enqueue, inicio,
fin, duración y estado; nunca argumentos ni resultados de tareas.

## Custodia, clasificación y hashes

La raíz persistente local es `var/s11-runs/` (montada como `/s11-evidence`) y no `/tmp`:

```text
var/s11-runs/runs/<run_id>/
├── raw/<scenario_id>/
├── processed/<scenario_id>/
├── derived/<scenario_id>/
├── logs/<scenario_id>/
├── metadata/<scenario_id>/
└── SHA256SUMS
```

Cada artefacto registra `run_id`, `scenario_id`, tipo, clasificación, timestamp, tamaño,
origen, ruta relativa, `expires_at`, ancla de retención y SHA-256 en
`metadata/artifacts.jsonl`. El manifest inicial fija
código/configuración/dataset; el final añade timestamp, status y referencias de artefactos.
Al finalizar, `INTERNAL` y `EXPERIMENTAL` se fijan contra el `finished_at` exacto del manifest;
`PUBLIC` conserva como inicio su aprobación. Un sello HMAC `.s11-finalized` ligado al manifest y el
inventario firmado impiden cualquier alta o append tardío. Los artefactos son inmutables y un nombre
repetido falla. El manifest final cumple el contrato exacto `RunManifest` y referencia todo el
inventario previo; tampoco puede repetirse un `artifact_id` bajo otro nombre.

Política aplicada:

- `PUBLIC`, `INTERNAL` y `EXPERIMENTAL`: permitidos con metadatos;
- `PUBLIC`: además exige aprobación Ed25519 ligada a artefacto/hash/fecha y emitida offline por
  Monty Emmanuel Pohl Amarilla; backend y worker reciben sólo la clave pública de verificación;
- cada registro y el inventario completo se firman por HMAC sobre clasificación, retención, hashes,
  aprobación, presencia y orden; alterar, degradar u omitir una entrada invalida backup, restore y
  destrucción;
- `SENSITIVE`: rechazado durante S11-C; el flag permanece apagado en el overlay;
- `SECRET`: nunca persistido.

Antes de escribir JSON/JSONL se redactan claves de autorización, cookies, contraseñas,
tokens, secretos, cédulas, correos, JWT y firmas de URL. Los tokens efímeros de fixtures
sólo viven en memoria/stdout del proceso y el log custodial recibe la versión redactada.

Para verificar una custodia terminada:

```bash
cd var/s11-runs/runs/<run_id>
sha256sum --check SHA256SUMS
```

Una diferencia de hash, artefacto ausente, export incompleto, fallo de cleanup o falta de
correlación invalida el run. Ningún contenido de esta carpeta debe copiarse a producción
ni interpretarse como impacto ciudadano real.

### Backup, restore y destrucción

- Primary: `var/s11-runs/` (`/s11-evidence`).
- Backup: `var/s11-backup/` (`/s11-backup`).
- Restore: `var/s11-restore/` (`/s11-restore`).
- Objetos reseteables, no custodiales: `var/s11-objects/` (`/s11-objects`).

Backup y restore rechazan sobrescrituras, preservan la estructura completa y comparan cada
archivo por SHA-256. Ambos mantienen el mismo lock del primary que la destrucción para que ninguna
copia aparezca después de enumerar ubicaciones. `INTERNAL` vence 90 días y `EXPERIMENTAL` 365 días después de
`finished_at`. Una destrucción sólo puede ejecutarse tras `expires_at`, elimina en primary, backup
y restore sólo los artefactos vencidos, reindexa los vigentes, comprueba ausencia y conserva una
intención previa y una constancia final sin payload. JSON, intención, constancia y journal se
autentican con HMAC además de SHA-256. Si una raíz falla después de otra, el journal privado permite
reanudar el mismo ID sin repetir ni bloquear las operaciones ya completadas. Cada bundle
JSON/SHA/HMAC se confirma con un marker `.commit` atómico; un crash antes del marker se recupera
con el mismo ID, incluso si ocurrió al preparar la constancia posterior al borrado.
La destrucción mantiene el lock de mutación del primary durante decisión, borrado, reindexado y
constancia, y rechaza todo run que aún conserve retención provisional anclada a `created_at`. Si el
manifest `INTERNAL` ya venció pero quedan artefactos `EXPERIMENTAL`, el sello HMAC permanece como
ancla histórica verificable hasta completar sus 365 días; los artefactos `PUBLIC` aprobados
permanecen.

### Repetir la verificación de custodia

Esta comprobación no ejecuta escenarios ni utiliza `--custody-approved`. Antes de iniciarla deben
estar exportadas también `S11_CUSTODY_HMAC_KEY`, `S11_PUBLICATION_APPROVAL_PUBLIC_KEY`,
`S11_HOST_UID="$(id -u)"` y `S11_HOST_GID="$(id -g)"`. Para esta verificación puede usarse una
clave pública de canario distinta de toda clave real: no se publica ningún artefacto y la privada
del canario no se inyecta al contenedor. Debe usarse un ID nuevo y
ejecutar cada fase en un contenedor/proceso diferente. El reporte registra hostname y PID y rechaza
un límite de persistencia que no pueda demostrar:

```bash
docker compose -p ciudadania-s11 \
  -f infra/docker-compose.yml -f infra/docker-compose.s11.yml \
  run --rm --no-deps backend python manage.py s11_verify_custody prepare \
  --verification-id <verification_id> \
  --source-root /s11-source \
  --baseline-sha d3b492a7f5d203fc1dbd8c63e1732b7fd9227656

docker compose -p ciudadania-s11 \
  -f infra/docker-compose.yml -f infra/docker-compose.s11.yml \
  run --rm --no-deps backend python manage.py s11_verify_custody complete \
  --verification-id <verification_id> \
  --source-root /s11-source \
  --baseline-sha d3b492a7f5d203fc1dbd8c63e1732b7fd9227656 \
  --persistence-boundary container-recreated
```

La evidencia se conserva bajo `var/s11-runs/custody-verifications/<verification_id>/`. Verificar
los sidecars SHA desde ese directorio con `sha256sum --check <archivo>.sha256`; el comando de
custodia valida además cada `.hmac` antes de consumirlo. Tras crear el commit y
confirmar que el working tree está limpio, ejecutar una tercera fase con los mismos `--source-root`
y `--baseline-sha`. `attest` comprueba que el SHA exista, coincida con `HEAD` y que sus blobs tengan
los mismos hashes verificados; además comprueba que el baseline aprobado sea ancestro del commit:

La imagen de backend debe estar construida desde el `Dockerfile.dev` versionado, que incluye Git.
Como `/s11-source` es un montaje compuesto read-only cuyo directorio raíz pertenece a `root`, la
excepción `safe.directory` se limita al contenedor efímero mediante `GIT_CONFIG_*`; no se escribe
configuración Git global ni local.

```bash
docker compose -p ciudadania-s11 \
  -f infra/docker-compose.yml -f infra/docker-compose.s11.yml \
  run --rm --no-deps \
  -e GIT_CONFIG_COUNT=1 \
  -e GIT_CONFIG_KEY_0=safe.directory \
  -e GIT_CONFIG_VALUE_0=/s11-source \
  backend python manage.py s11_verify_custody attest \
  --verification-id <verification_id> \
  --source-root /s11-source \
  --baseline-sha d3b492a7f5d203fc1dbd8c63e1732b7fd9227656 \
  --commit-sha <sha_git_completo>
```
