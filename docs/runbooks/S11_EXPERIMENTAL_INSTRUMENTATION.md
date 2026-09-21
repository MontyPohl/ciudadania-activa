# S11-B — Instrumentación experimental reproducible

## Estado y límites

Esta infraestructura prepara ejecuciones futuras de `EX-02` a `EX-10` según
`S11A-v1.0`. No contiene resultados académicos y no autoriza una ejecución. `EX-01`
(encuesta) y `EX-11` (ranking/redemption) permanecen fuera del mecanismo.

Las decisiones de custodia `S11A-P02` y `S11A-P03` quedaron formalizadas en
[`S11A_POLITICA_CUSTODIA_Y_ACCESO.md`](../tesis/S11A_POLITICA_CUSTODIA_Y_ACCESO.md) y
verificadas con el canario `s11-custody-20260921-p02p03-v5`. Esto cierra esos dos bloqueos, pero
no autoriza automáticamente un run: `--custody-approved` sigue siendo una afirmación explícita del
operador para cada ejecución futura y no fue utilizado durante la verificación de custodia.

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

| Dataset | Escenarios | Seed | SHA-256 |
|---|---|---:|---|
| `s11-functional-v1` | EX-02…EX-08 | `20260921` | `66074d1617c4e1b7a32b854090d26556faa9b35a000452787ff3b47d6f0edc1c` |
| `s8-evaluation-v1` | EX-09, sólo referencia congelada | — | `3bc54d591dc104aeabeecb2f5cc7c7a38e70ba042e7f045321ab33ae9b5efad4` |
| `s11-load-v1` | EX-10 | `20260921` | `c6883b59dc861fb07fae730594c929fdd572a9ed71aee6d8b85c87f71ede0acb` |

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
Redis y un worker Celery con concurrencia 2; Playwright no intercepta la API. El flujo es:

```text
setup → manifest inicial → execute → export read-only → validate → reset
      → log redactado → manifest final → shutdown
```

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
Un fallo se propaga al proceso, se registra y produce un manifest `FAILED`; nunca se
convierte silenciosamente en éxito. El harness no calcula ni declara HT-1…HT-6.

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

```bash
docker compose -p ciudadania-s11 \
  -f infra/docker-compose.yml -f infra/docker-compose.s11.yml \
  run --rm --no-deps backend python manage.py s11_verify_custody attest \
  --verification-id <verification_id> \
  --source-root /s11-source \
  --baseline-sha d3b492a7f5d203fc1dbd8c63e1732b7fd9227656 \
  --commit-sha <sha_git_completo>
```
