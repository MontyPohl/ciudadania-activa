# S11 — Política de custodia y acceso a evidencia experimental

**Estado:** `APROBADO PARA S11 EXPERIMENTAL`

**Fecha de aprobación:** 21/09/2026

**Política:** `S11-CUSTODY-v1.0`

**Alcance:** cierre de `S11-A-P02` y `S11-A-P03`. Esta política no modifica
`S11A-v1.0`, no autoriza por sí sola un run, no sustituye el gate operativo del harness y no
constituye una ejecución de EX-02…EX-10.

## 1. Propósito y alcance

Esta política fija responsables, retención, ubicaciones y permisos para la evidencia S11.
Aplica a manifests, logs redactados, entradas y resultados sintéticos, artefactos procesados y
derivados, metadata, índices, hashes, backups, restauraciones y constancias de destrucción.

Quedan fuera:

- datos o servicios productivos;
- datos reales de ciudadanos;
- ejecución de EX-01…EX-11;
- almacenamiento persistente de `SENSITIVE` durante S11-C;
- cualquier persistencia de `SECRET`.

## 2. Personas y responsabilidades aprobadas

| Identidad | Responsabilidad aprobada |
|---|---|
| Monty Emmanuel Pohl Amarilla | Investigador principal, responsable del desarrollo, autoridad final de decisión, custodio operativo, administrador de evidencia y única autoridad para aprobar `PUBLIC`. |
| Guadalupe Galeano | Coautora e investigadora; puede ejecutar experimentos autorizados, leer, revisar y escribir evidencia cuando corresponda al flujo experimental. No puede aprobar `PUBLIC`. |
| Edward Vazquez | Profesor y responsable académico. Esta política no le concede acceso técnico a almacenamiento, backup, restore ni ejecución. |

Las cuentas de proceso son identidades distintas: `S11_APPLICATION_PROCESS`, `S11_TEST_RUNNER`,
`S11_STORAGE_PROCESS`, `S11_BACKUP_PROCESS` y `S11_RESTORE_PROCESS`. No representan personas ni
heredan permisos humanos.

## 3. Clasificación y retención

Cada artefacto conserva una sola clasificación. Si mezcla clases, se aplica la más restrictiva
según `SECRET > SENSITIVE > EXPERIMENTAL > INTERNAL > PUBLIC`.

| Clase | Retención aprobada | Inicio del plazo | Resultado al vencer |
|---|---:|---|---|
| `PUBLIC` | Permanente, sólo después de aprobación explícita | Aprobación de publicación | Evidencia histórica versionada; nunca publicación automática. |
| `INTERNAL` | 90 días | `finished_at` del manifest final | Destrucción verificable de primary, backup y restore. |
| `EXPERIMENTAL` | 365 días | `finished_at` del manifest final | Destrucción verificable; sólo sobreviven agregados `PUBLIC` aprobados. |
| `SENSITIVE` | No se persiste durante S11-C | No aplica | Intento rechazado mientras el flag permanezca deshabilitado. |
| `SECRET` | 0 días | No aplica | Nunca se persiste, respalda o restaura. |

`EvidenceStore` registra `created_at`, `expires_at`, `retention_anchor`, `retention_started_at`,
clasificación y SHA-256. Mientras el run está abierto, el plazo es provisional respecto de
`created_at`. Al finalizar, `s11_finalize_run` comprueba que el `finished_at` del payload coincida
exactamente con el usado para calcular retención, fija los registros `INTERNAL` y `EXPERIMENTAL`
contra ese instante y marca `retention_anchor=finished_at`. `PUBLIC` conserva como inicio la fecha
de su aprobación y `retention_anchor=publication_approval`.

La finalización toma un lock de filesystem durante lectura, construcción del manifest, reanclaje,
escritura y commit; antes de modificar escribe un journal HMAC fuera del run. Ante excepción, kill
o crash, el siguiente intento restaura índice/hashes previos o reconoce una finalización ya
confirmada. El cierre crea además `.s11-finalized`, un sello HMAC ligado al ID y SHA-256 del manifest
final y a su `finished_at`. Su presencia o la entrada firmada del manifest en el inventario bloquean
nuevos artefactos, registros de streams y altas tardías incluso si alguien elimina el sello. Backup
y restore conservan esos valores byte a byte. El manifest final debe cumplir exactamente
`RunManifest`, identificar el run/escenario/`finished_at` que se cierra, contener un único manifest
inicial válido y referenciar, en orden, todo el inventario firmado previo. Tipos, SHA Git/SHA-256 y
timestamps UTC se validan en runtime; código, configuración, dataset, entorno, seed y demás campos
de procedencia deben coincidir con el manifest inicial. Un `artifact_id` o ruta ya
indexados no pueden volver a registrarse con otro nombre o contenido.

El procedimiento de destrucción falla cerrado mientras cualquier artefacto sujeto a plazo conserve
el ancla provisional `created_at`; para runs reales exige el manifest/sello y recalcula cada
`expires_at`. Si el manifest `INTERNAL` vence a los 90 días antes que evidencia `EXPERIMENTAL`, el
sello autenticado permanece como ancla histórica: sólo admite sobrevivientes `EXPERIMENTAL`
correctamente fijados a 365 días y/o `PUBLIC` con aprobación válida. Así la eliminación del manifest
no acorta ni vuelve indestructible la retención restante. El canario P02/P03 usa el ancla explícita
y separada `verification_finished_at`, que nunca equivale a un manifest experimental.

Una regla institucional superior prevalece sobre estos plazos. La excepción debe quedar
documentada antes de aplicarse y nunca puede habilitar `SECRET`.

## 4. Gate de publicación

Todo artefacto experimental nace como `INTERNAL` o `EXPERIMENTAL`. Un resultado sólo puede
registrarse como `PUBLIC` cuando Monty Emmanuel Pohl Amarilla emite una aprobación explícita.

La aprobación debe identificar ID de aprobación, artefacto, SHA-256 exacto del contenido, fecha
UTC, autoridad, identificador de clave y versión de política. Monty conserva la clave privada
Ed25519 de 32 bytes fuera de Git, de los repositorios de evidencia y de los procesos de aplicación,
worker, test runner, almacenamiento, backup y restore. Esos procesos reciben exclusivamente la
clave pública mediante `S11_PUBLICATION_APPROVAL_PUBLIC_KEY` (32 bytes representados por 64
caracteres hexadecimales) y por tanto pueden verificar, pero no emitir, una aprobación atribuible a
Monty. La clave privada no se inyecta por variable de entorno ni se incorpora a artefactos,
metadata, backup o restore.

La API verifica la firma Ed25519, el identificador de su clave pública y la identidad exacta
`Monty Emmanuel Pohl Amarilla`; una cadena con su nombre, una firma generada con otra clave o una
aprobación ligada a otro artefacto/hash no autoriza nada. La clave pública debe conservarse durante
toda la vida verificable de los artefactos —incluido `PUBLIC` permanente—. Una rotación exige una
migración controlada que mantenga verificable la clave anterior; esta política no inventa una
ubicación para la clave privada. Ni el éxito de un experimento, ni un commit, ni la revisión
académica, ni el acceso de una coautora constituyen aprobación de publicación.

Procedimiento de aprobación `PUBLIC`:

1. producir primero el artefacto no público, ya redactado, y calcular sus bytes canónicos y
   SHA-256;
2. Monty revisa el artefacto y, si decide publicarlo, firma offline con Ed25519 un objeto que fija
   `approval_id`, `artifact_id`, `content_sha256`, `approved_at`, `approved_by`, `key_id` y
   `policy_version`;
3. transferir al flujo S11 sólo el objeto de aprobación firmado, nunca la clave privada;
4. el runtime verifica clave pública, firma, autoridad, artefacto, hash y versión antes de aceptar
   la clasificación `PUBLIC`; cualquier discrepancia falla cerrado;
5. conservar juntos artefacto, aprobación y metadata autenticada. La aprobación no cambia ni
   publica automáticamente ningún artefacto distinto.

Una clave separada, `S11_CUSTODY_HMAC_KEY`, autentica con dominios distintos cada registro, el
inventario completo `artifacts.jsonl`, el sello de cierre, los journals de finalización y
destrucción, y los registros de verificación:
clasificación, rutas, hashes, retención, aprobación, presencia y orden. Esta clave operacional no
concede autoridad de publicación y se conserva fuera de Git durante toda la retención; rotarla
exige verificar con la anterior y volver a autenticar de forma controlada. Backup, restore y
destrucción exigen los compromisos aplicables. Por ello, reclasificar `PUBLIC` como `INTERNAL`,
borrar su aprobación o su línea completa, o reconstruir sólo `SHA256SUMS`, no supera la
verificación normal de custodia.

## 5. Ubicaciones aprobadas

| Función | Host | Contenedor |
|---|---|---|
| Primary | `/home/monty/proyectos/ciudadania-activa/var/s11-runs` | `/s11-evidence` |
| Backup | `/home/monty/proyectos/ciudadania-activa/var/s11-backup` | `/s11-backup` |
| Restore | `/home/monty/proyectos/ciudadania-activa/var/s11-restore` | `/s11-restore` |

Las tres raíces son no productivas, distintas, persistentes respecto de recreaciones de
contenedores, exteriores a `/tmp` y excluidas de Git. Los directorios se crean con modo `0700` y
los archivos con `0600`. La aplicación rechaza raíces iguales, anidadas, demasiado amplias o bajo
`/tmp`. El overlay exige `S11_HOST_UID` y `S11_HOST_GID` para ejecutar backend y worker con la
identidad del operador del host; así los modos privados no dejan la evidencia inaccesible al
custodio nominal.

`var/s11-objects` (`/s11-objects`) sigue siendo almacenamiento técnico temporal y reseteable. No
es primary, backup ni restore y no puede presentarse como evidencia custodial.

## 6. Matriz nominal y de procesos

Los permisos son independientes: `READ` no implica `WRITE`; `WRITE` no implica `DELETE`;
`DELETE` sólo se concede dentro del procedimiento de destrucción autorizado.

| Identidad | `PUBLIC` | `INTERNAL` | `EXPERIMENTAL` | `SENSITIVE` | `SECRET` |
|---|---|---|---|---|---|
| Monty Emmanuel Pohl Amarilla | READ, WRITE, ADMIN, PUBLISH, BACKUP, RESTORE | READ, WRITE, ADMIN, EXECUTE, REVIEW, BACKUP, RESTORE; DELETE procedural | Igual que INTERNAL | NONE | NONE |
| Guadalupe Galeano | READ | READ, WRITE, EXECUTE, REVIEW | READ, WRITE, EXECUTE, REVIEW | NONE | NONE |
| Edward Vazquez | NONE técnico | NONE técnico | NONE técnico | NONE | NONE |
| `S11_APPLICATION_PROCESS` | NONE | READ, WRITE | READ, WRITE | NONE | NONE persistente |
| `S11_TEST_RUNNER` | NONE | READ, WRITE, EXECUTE | READ, WRITE, EXECUTE | NONE | NONE persistente |
| `S11_STORAGE_PROCESS` | NONE | READ, WRITE; DELETE procedural | READ, WRITE; DELETE procedural | NONE | NONE |
| `S11_BACKUP_PROCESS` | READ, WRITE, BACKUP | READ, WRITE, BACKUP; DELETE procedural | READ, WRITE, BACKUP; DELETE procedural | NONE | NONE |
| `S11_RESTORE_PROCESS` | READ, WRITE, RESTORE | READ, WRITE, RESTORE; DELETE procedural | READ, WRITE, RESTORE; DELETE procedural | NONE | NONE |

La matriz ejecutable usa denegación por defecto y registra tanto decisiones permitidas como
denegadas en la evidencia de verificación. El acceso directo al filesystem queda limitado por los
permisos del host; los flujos S11 deben usar las operaciones de custodia y no modificar archivos
directamente.

## 7. Backup

Por cada run retenido, el backup copia el árbol completo de evidencia, incluidos, cuando existan:

- manifest inicial y final;
- `raw/`, `processed/`, `derived/`, `logs/` y `metadata/`;
- `artifacts.jsonl`;
- `SHA256SUMS`.

Antes de copiar se verifican marcador, índice y hashes. El destino debe estar ausente para impedir
sobrescrituras silenciosas. Después de copiar se comparan todos los archivos por ruta relativa y
SHA-256. Clasificación, `run_id` y `expires_at` se conservan byte a byte. El backup no extiende la
retención. Validación, copia y comprobación mantienen el mismo lock de mutación del primary usado
por finalización, restore y destrucción; no puede aparecer una copia nueva fuera del inventario de
una destrucción concurrente.

## 8. Restore

Restore parte exclusivamente de un backup verificado y crea un árbol nuevo bajo
`var/s11-restore/runs/<run_id>`. Nunca sobrescribe primary ni una restauración anterior. La copia
sólo es válida si su inventario y todos sus SHA-256 coinciden exactamente con el backup. Todo el
restore mantiene también el lock común del primary.

## 9. Destrucción

La destrucción se evalúa por artefacto, no por run: una evidencia `EXPERIMENTAL` o `PUBLIC` no
prolonga la retención de otra `INTERNAL`. Procede sólo para los registros cuyo `expires_at` venció
y cuando una identidad con permiso `DELETE` actúa dentro del procedimiento autorizado:

1. cargar y validar el índice;
2. comprobar expiración con timestamp UTC;
3. enumerar primary, backup y restore;
4. escribir una intención mínima `PENDING`, su SHA-256 y su HMAC antes de eliminar;
5. eliminar esos artefactos en las tres raíces y reindexar los que siguen vigentes;
6. verificar ausencia de los vencidos e integridad de los restantes;
7. registrar la constancia final, su SHA-256 y su HMAC.

La intención y la constancia contienen clasificación, IDs de artefactos/run, ubicaciones,
timestamp UTC, identidad ejecutora, método y resultado. La intención permite auditar un fallo
posterior a una eliminación parcial. Un journal privado autenticado por HMAC, con hash enlazado
desde la intención, permite reanudar idempotentemente cada raíz desde su estado previo o posterior
y se elimina sólo al emitir la constancia final. La operación completa —lectura, intención,
eliminación/reindexado en las tres raíces, verificación y constancia— mantiene el mismo lock de
mutación del primary para impedir escrituras concurrentes entre la decisión y la destrucción.
Cada terna JSON/SHA-256/HMAC de transacción, intención y constancia se prepara por completo y sólo
se considera visible cuando existe su marker `.commit`, publicado mediante temporal, `fsync` y
`os.replace`. Un proceso interrumpido antes del marker deja un bundle no confirmado que el mismo ID
descarta y reconstruye desde el journal autenticado; una interrupción posterior al borrado puede
reanudar y emitir la constancia sin repetir ni omitir ubicaciones.
Antes de reanudar se valida el HMAC, la partición
exacta del inventario, las firmas de sus registros, la expiración y que ninguna clasificación
permanente entre en la destrucción. Ningún registro conserva payloads o secretos ni reconstituye el
artefacto destruido. Cuando ya no quedan artefactos se elimina el árbol del run completo.

## 10. SENSITIVE

`S11_ALLOW_SENSITIVE_PERSISTENCE=false` permanece fijado en el overlay S11. No existe ni se
inventa `S11_SENSITIVE_ROOT`. Toda operación `SENSITIVE` se deniega antes de escribir. Habilitarla
en el futuro exigiría una decisión separada, ubicación cifrada y aprobación explícita; no forma
parte de esta política para S11-C.

## 11. SECRET

`SECRET` nunca entra en manifests, logs, artifacts, exports, backups o restores. Sólo puede
existir efímeramente en el proceso o gestor autorizado. Un intento de persistencia falla cerrado.
Al terminar se desmonta o revoca; ante exposición se rota. La verificación usa un canario sintético
y demuestra que su valor no aparece en primary, backup ni restore.

## 12. Verificación técnica

La verificación de custodia usa un identificador propio, no un run experimental, y se ejecuta en
dos procesos o contenedores diferentes:

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

La fase `prepare` crea un canario exclusivamente sintético en primary, lo indexa, calcula su
SHA-256, registra `expires_at` y prueba los gates `PUBLIC`, `SENSITIVE` y `SECRET`. Para probar la
primitiva Ed25519 usa un par efímero exclusivamente sintético y comprueba, por separado, que el
runtime rechaza esa firma frente a la clave pública configurada; la clave sintética no autoriza ni
produce un artefacto `PUBLIC`. La fase
`complete` debe recuperar la evidencia anterior, verificar persistencia, crear backup, restaurar,
comparar hashes, simular expiración con reloj controlado, destruir las tres copias y emitir una
constancia. Ambas fases registran hostname y PID; `container-recreated` sólo se acepta cuando los
hostnames difieren, e `independent-process` sólo cuando difiere al menos el PID o el hostname.
Ambas fases calculan y comparan un snapshot SHA-256 del código, configuración, pruebas y esta
política contra el baseline aprobado. Después del commit, `attest` vuelve a comparar el snapshot y
liga la evidencia al SHA Git final: exige que el objeto exista, que sea `HEAD` y calcula los hashes
directamente desde sus blobs, no desde una cadena declarada por el operador. También exige que el
baseline aprobado sea ancestro Git del commit atestado.

La evidencia queda en:

```text
var/s11-runs/custody-verifications/<verification_id>/
```

Cada JSON indica clasificación e identificadores y posee archivos `.sha256` y `.hmac`. La
verificación no ejecuta el harness, no usa `--custody-approved`, no abre conexiones productivas y
no produce resultados de hipótesis.

## 13. Criterio de aprobación y cambios

Esta versión fue aprobada con las decisiones expresas de los autores:

- autoridad final y custodio operativo: Monty Emmanuel Pohl Amarilla;
- coautora/investigadora autorizada: Guadalupe Galeano;
- responsable académico sin permisos técnicos implícitos: Edward Vazquez.

Cualquier cambio de plazos, ubicaciones, identidades, autoridad `PUBLIC` o habilitación de
`SENSITIVE` requiere una revisión versionada de esta política. `SECRET` no puede habilitarse por
una excepción de esta política.
