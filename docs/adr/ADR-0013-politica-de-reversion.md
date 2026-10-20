# ADR-0013 — Política de reversión, revalidación y ciclos de resolución

**Estado:** ACCEPTED
**Sprint:** 10
**Fecha:** 2026-09-10

Este ADR formaliza las decisiones arquitectónicas y de producto aprobadas en el
Decision Gate de S10. Su aceptación es exclusivamente normativa: **la implementación
de S10 requiere un gate posterior**. Este documento no autoriza cambios de código,
modelos, migraciones, API, frontend, infraestructura ni producción.

## Contexto

Sprint 9 incorporó la lectura institucional, la validación y el rechazo de reportes.
La validación es un evento humano registrado mediante `validated_at` y `validated_by`
que lleva el reporte a `ASIGNADO`; `VALIDADO` no es un estado de `Report`. Sprint 10
debe completar el ciclo institucional con procesamiento, evidencia de resolución,
resolución, cierre y reversión administrativa.

Una reversión puede ocurrir después de que un reporte haya generado puntos y créditos,
recibido evidencia o incluso alcanzado `CERRADO`. El sistema debe poder deshacer los
efectos económicos vigentes sin borrar la historia, reutilizar evidencia antigua ni
duplicar recompensas durante una revalidación. También debe conservar el aislamiento
institucional y producir resultados deterministas cuando dos operaciones compiten.

El modelo anterior no identificaba explícitamente ciclos de resolución. Sin esa
identidad no es posible distinguir con suficiente solidez qué evidencia y qué bonus
pertenecen a cada validación, ni imponer en base de datos que exista como máximo un
`VALIDATION_BONUS` y un `RESOLUTION_BONUS` por ciclo.

El Decision Gate también encontró contradicciones históricas:

- el plan describía el estado posterior a una reversión solo como «en revisión», aunque
  ese valor no forma parte de la máquina de estados;
- una transición histórica `ASIGNADO → RECHAZADO` entraba en conflicto con el contrato
  S9, que exige revertir primero un reporte validado;
- distintas fuentes atribuían el cierre manual al operador, administrador o ciudadano;
- el plan conservaba rutas genéricas `/api/v1/reports/...`, mientras S9 consolidó el
  boundary `/api/v1/operator/reports/...`;
- el plan mencionaba notificaciones en S10, mientras el contrato de notificaciones las
  situaba en S12 y S13.

Para el contrato nuevo de S10 prevalecen las decisiones de este ADR. Las fuentes
históricas deben interpretarse o corregirse posteriormente de acuerdo con él, sin
reescribir la historia del proyecto.

## Decisión

### 1. Máquina de estados y reversión — S10-D01

La máquina de estados queda definida así:

```text
∅ → CREADO
CREADO → CLASIFICADO
CREADO | CLASIFICADO → ASIGNADO       mediante validación
CREADO | CLASIFICADO → RECHAZADO

ASIGNADO → EN_PROCESO
EN_PROCESO → RESUELTO                 exige evidencia del ciclo vigente
RESUELTO → CERRADO                    manual o automático

ASIGNADO | EN_PROCESO | RESUELTO | CERRADO
    → CLASIFICADO                     solo reversión administrativa

CLASIFICADO → RECHAZADO               rechazo posterior a reversión
```

`RECHAZADO` es terminal. `CERRADO` solo puede abandonarse mediante una reversión
administrativa. No se admite un rechazo directo desde `ASIGNADO`: un reporte validado
debe revertirse primero y el rechazo posterior constituye una operación institucional
separada.

La reversión:

- es exclusiva del administrador global;
- puede ejecutarse desde `ASIGNADO`, `EN_PROCESO`, `RESUELTO` o `CERRADO`;
- devuelve el reporte a `CLASIFICADO`;
- conserva categoría, prioridad e institución responsable;
- limpia las proyecciones actuales `validated_at`, `validated_by`, `resolved_at` y
  `closed_at`;
- no elimina historial, auditoría, evidencia ni transacciones económicas;
- conserva la evidencia anterior ligada a su ciclo histórico, pero no permite usarla
  para resolver un ciclo posterior;
- no actúa como reasignación institucional.

«En revisión» no se usa como estado implícito: cualquier estado persistido debe
pertenecer al catálogo formal. Conservar `Report.institution` mantiene el reporte bajo
el tenant que asumió la responsabilidad. Una reasignación futura, si se necesitara,
requerirá un contrato independiente y no puede introducirse como efecto lateral de la
reversión.

### 2. Ciclos de revalidación — S10-D01B

`resolution_cycle` es un entero monotónico que identifica la validación vigente y sus
operaciones posteriores:

- `0` significa que el reporte nunca fue validado;
- la primera validación establece el ciclo `1`;
- una nueva validación posterior a una reversión incrementa el ciclo exactamente una
  vez y bajo lock del reporte;
- la reversión no incrementa el ciclo: revierte el ciclo vigente;
- el número de ciclo nunca disminuye;
- un ciclo nuevo comienza únicamente cuando la revalidación se confirma.

Ejemplo normativo:

```text
cycle 0
→ validation
→ cycle 1
→ processing
→ evidence
→ resolution
→ closure
→ reversal
→ CLASIFICADO, cycle sigue 1
→ validation
→ cycle 2
```

La implementación deberá persistir el ciclo en la proyección actual de `Report`, en
`ResolutionEvidence`, en `ReportStatusHistory` y en cada `RewardTransaction` asociada a
un ciclo. Los timestamps del reporte son la proyección del ciclo vigente; los registros
históricos conservan los instantes de los ciclos anteriores.

Las evidencias pertenecen inmutablemente al ciclo en el que fueron creadas. Resolver un
reporte exige al menos una evidencia del ciclo actual. Una evidencia de un ciclo
anterior permanece accesible como historia, pero no satisface esa precondición ni puede
reasignarse al nuevo ciclo.

Las transacciones `REPORT_CREATED` y `CONFIRMATION_CREATED` pendientes reciben el ciclo
vigente cuando se acreditan. Una confirmación creada después de la validación recibe el
ciclo vigente al nacer acreditada. El ciclo es metadata de trazabilidad y nunca altera
`impact_delta` o `credit_delta`.

Al revertir antes o después del cierre se limpian las proyecciones actuales de
resolución y cierre. La evidencia y los timestamps históricos del ciclo siguen siendo
reconstruibles mediante sus registros asociados.

### 3. Ledger y recompensas — S10-D01B/S10-D02

ADR-0004 y ADR-0010 continúan vigentes. Este ADR no reemplaza el ledger ni introduce un
contador económico mutable.

Se mantienen estas reglas:

- `REPORT_CREATED`: +20 impacto/+20 créditos, inicialmente `PENDIENTE`;
- `CONFIRMATION_CREATED`: +10/+10;
- `VALIDATION_BONUS`: +30/+30, `ACREDITADO`;
- `RESOLUTION_BONUS`: +50/+50, `ACREDITADO`;
- el saldo agrega únicamente `credit_delta` de filas `ACREDITADO`;
- los deltas económicos son inmutables;
- las transacciones históricas no se eliminan;
- una transacción acreditada que se revierte pasa a `REVERTIDO` y conserva
  `credited_at`;
- por cada original revertida se crea una fila `REVERSAL`, también `REVERTIDO`, con
  deltas inversos y `related_transaction_id` hacia la original;
- la salida de la original del conjunto `ACREDITADO` produce el efecto económico; la
  fila negativa `REVERSAL` es rastro auditable y no descuenta por segunda vez;
- las transacciones todavía `PENDIENTE` pasan a `ANULADO`;
- `REDEMPTION` no se revierte;
- no existe penalización adicional;
- una reversión puede dejar saldo institucional negativo y no invalida un vale ya
  emitido.

Debe existir como máximo un `VALIDATION_BONUS` y un `RESOLUTION_BONUS` por
`(report, resolution_cycle)`, garantizado mediante restricciones únicas de base de
datos. Una transacción original admite como máximo una fila `REVERSAL`. Ciclos
posteriores pueden generar nuevos bonus, sin reactivar las transacciones `REVERTIDO` de
ciclos anteriores.

### 4. Motivo de reversión — S10-D03

El motivo es obligatorio y se recibe como texto plano. Después de normalizar debe tener
entre 10 y 1000 caracteres, inclusive. La normalización consiste en:

1. convertir a Unicode NFC;
2. convertir CRLF a LF;
3. recortar whitespace exterior;
4. rechazar NUL y controles no imprimibles.

La longitud se calcula sobre el valor normalizado. El mismo valor se almacena en
`ReportStatusHistory.reason` y en el payload de auditoría de
`report.validation_reverted`. No se agrega un campo mutable de motivo a `Report`.

Pueden leer el motivo completo:

- el administrador global;
- el autor del reporte mediante su historial privado;
- un operador activo de la institución responsable.

El motivo no se expone en mapa, endpoints o listados públicos ni en notificaciones. La
interfaz debe advertir que no se introduzcan secretos, números de documento, datos
médicos ni información personal innecesaria. El acceso restringido no convierte esa
clase de datos en contenido aceptable.

### 5. Cierre manual — S10-D04

Solo un operador institucional activo de la institución almacenada en
`Report.institution` puede ejecutar `RESUELTO → CERRADO`. No pueden hacerlo:

- el ciudadano, aunque sea autor;
- un operador de otra institución;
- un operador cuya membresía esté inactiva;
- el administrador global.

El cierre requiere `RESUELTO`, validación vigente, institución asignada, ciclo vigente y
`resolved_at`. Repetir el cierre del mismo ciclo devuelve éxito como no-op idempotente:
no crea un segundo historial, auditoría ni efecto económico.

El cierre confirmado escribe `Report`, `ReportStatusHistory` y `report.closed` dentro de
la misma transacción. El administrador conserva separación de funciones: consulta
auditoría y revierte, pero no ejecuta el trabajo institucional.

### 6. Cierre automático — S10-D05

Un reporte es elegible cuando:

```text
status = RESUELTO
AND resolved_at <= timezone.now() - 72 horas
```

Son 72 horas transcurridas, no tres días calendario. Los timestamps se almacenan y
comparan en UTC y se presentan en `America/Asuncion`. El plazo usa una configuración
positiva `REPORT_AUTO_CLOSE_HOURS`, con valor por defecto `72`.

Celery Beat ejecutará el barrido cada 15 minutos. Nunca puede cerrar antes del cutoff;
el cierre efectivo puede ocurrir en la primera ejecución posterior. Cada candidato se
bloquea con `select_for_update()` y se vuelven a comprobar estado, ciclo y cutoff bajo
lock.

El cierre automático usa `actor_type = system`, `actor_user = null` y
`ReportStatusHistory.changed_by = null`. Registra una única acción `report.closed` con
`closure_mode = automatic`. Es idempotente, no genera reward y no crea, encola ni envía
notificaciones.

Los errores transitorios admiten hasta tres retries con backoff exponencial y jitter.
Un error permanente se registra de forma redactada y no elimina la elegibilidad: un
barrido posterior puede volver a evaluar el reporte.

### 7. Evidencia privada — S10-D06

`ResolutionEvidence` tiene este contrato:

- JPEG o PNG;
- máximo 5 MiB después de normalizar;
- máximo cinco evidencias por reporte y ciclo;
- una evidencia por request;
- descripción obligatoria de 1 a 1000 caracteres después de normalización como texto
  plano;
- creación permitida únicamente mientras el reporte está `EN_PROCESO`;
- asociación inmutable al ciclo vigente;
- sin actualización o eliminación individual durante S10.

Los archivos se guardan en almacenamiento privado. El backend genera claves con la
forma:

```text
resolution-evidence/{report_id}/{cycle}/{evidence_id}.{ext}
```

El nombre proporcionado por el cliente nunca forma parte de la ruta. El campo legado
`evidence_url` guarda la clave de objeto, no una URL pública ni firmada. La lectura
autorizada genera una URL firmada con 300 segundos de vigencia.

Pueden leer evidencia:

- el autor del reporte;
- un operador con membresía activa en la institución responsable;
- el administrador global.

El público, otros ciudadanos y operadores de otros tenants no pueden leerla. La
evidencia se conserva durante la vida del reporte, incluso después de una reversión, y
no se reutiliza en otro ciclo.

Se validan tamaño real del stream, extensión, MIME declarado y formato detectado. Los
tres deben coincidir. El backend corrige orientación, recodifica los píxeles y elimina
metadata. Se reutilizan así las garantías arquitectónicas y de seguridad de ADR-0007,
pero no su unicidad global por hash: dos evidencias visualmente iguales no son, por ese
solo hecho, la misma actuación institucional.

El upload ocurre antes de la transacción de base de datos. Si la persistencia posterior
falla, el backend intenta eliminar únicamente el objeto recién creado. El cleanup es
best-effort, no oculta el error original y no registra la clave completa. Un fallo del
storage produce `503` sin una fila parcial de evidencia.

### 8. API y tenancy — S10-D07

S10 extiende el boundary institucional consolidado por S9. Las rutas históricas
`/api/v1/reports/{id}/status/` y `/api/v1/reports/{id}/evidence/` no gobiernan las
mutaciones de operador.

#### Endpoints normativos

| Método y ruta | Actor | Contrato |
|---|---|---|
| `PATCH /api/v1/operator/reports/{id}/status/?institution_id={uuid}` | Operador | Transición a `EN_PROCESO`, `RESUELTO` o `CERRADO` |
| `GET /api/v1/operator/reports/{id}/evidence/?institution_id={uuid}` | Operador | Evidencia dentro de su tenant |
| `POST /api/v1/operator/reports/{id}/evidence/?institution_id={uuid}` | Operador | Crear una evidencia del ciclo vigente |
| `GET /api/v1/reports/{id}/evidence/` | Autor | Evidencia de su propio reporte |
| `GET /api/v1/admin/reports/{id}/evidence/` | Admin | Evidencia de cualquier reporte |
| `POST /api/v1/admin/reports/{id}/revert-validation/` | Admin | Revertir el ciclo indicado |
| `GET /api/v1/admin/audit-logs/` | Admin | Auditoría paginada y filtrable |

El cambio de estado recibe JSON estricto:

```json
{
  "status": "EN_PROCESO | RESUELTO | CERRADO",
  "expected_cycle": 1
}
```

La evidencia usa `multipart/form-data`, una parte `file`, una parte `description`, el
campo `cycle` y un header `Idempotency-Key` UUID. La key se persiste y es única dentro
del reporte, ciclo y operación. Repetir la misma key y el mismo contenido devuelve el
resultado original; reutilizarla con contenido distinto produce conflicto.

La reversión recibe JSON estricto:

```json
{
  "reason": "Texto normalizado de 10 a 1000 caracteres",
  "cycle": 1
}
```

Las respuestas de cambio de estado y reversión exponen como mínimo:

```json
{
  "id": "<report-uuid>",
  "status": "CLASIFICADO",
  "cycle": 1,
  "institution": {"id": "<uuid>", "name": "...", "slug": "..."},
  "validated_at": null,
  "resolved_at": null,
  "closed_at": null
}
```

La lectura autorizada de evidencia expone `id`, `cycle`, `description`, `created_at`,
`signed_url` y `expires_in = 300`; nunca devuelve la clave del objeto.

`institution_id` es obligatorio en todas las mutaciones del operador. El backend debe
validar bajo lock:

1. usuario activo;
2. rol `operator`;
3. membresía activa en el contexto solicitado;
4. institución activa;
5. coincidencia con `Report.institution`;
6. estado y ciclo vigentes.

El fallback `Category.default_institution` permanece limitado a lectura y routing previo
a la validación; no autoriza una mutación S10. Una sugerencia de IA nunca participa en
tenancy.

Un reporte o evidencia inexistente y uno fuera del tenant producen el mismo `404`, sin
revelar existencia. Todas las respuestas autenticadas de este contrato incluyen
`Cache-Control: private, no-store`. Los schemas rechazan campos desconocidos y
parámetros repetidos.

#### Errores estables

| HTTP | Código |
|---|---|
| `400` | `INVALID_REQUEST`, `INVALID_INSTITUTION_CONTEXT` |
| `401` | `AUTHENTICATION_REQUIRED` |
| `403` | `INSTITUTION_OPERATOR_REQUIRED`, `ADMIN_REQUIRED` |
| `404` | `REPORT_NOT_FOUND`, `EVIDENCE_NOT_FOUND` |
| `409` | `REPORT_STATE_NOT_ALLOWED`, `STALE_REPORT_CYCLE`, `RESOLUTION_EVIDENCE_REQUIRED`, `EVIDENCE_LIMIT_REACHED`, `IDEMPOTENCY_KEY_REUSED` |
| `413` | `EVIDENCE_TOO_LARGE` |
| `415` | `UNSUPPORTED_EVIDENCE_TYPE` |
| `503` | `STORAGE_UNAVAILABLE` |

La auditoría y los listados históricos usan páginas de 20 elementos y orden estable por
`created_at, id` descendente.

### 9. Auditoría — S10-D08

S10 adopta este catálogo estable:

| Acción | Actor | Payload mínimo |
|---|---|---|
| `report.processing_started` | operador, `human` | `report_id`, `institution_id`, `cycle`, `previous_status`, `new_status` |
| `report.resolution_evidence_created` | operador, `human` | `report_id`, `institution_id`, `cycle`, `evidence_id` |
| `report.resolved` | operador, `human` | `report_id`, `institution_id`, `cycle`, transición, `evidence_ids`, `resolution_bonus_transaction_id` |
| `report.closed` | operador `human` o `system` | `report_id`, `institution_id`, `cycle`, transición, `closure_mode` |
| `report.validation_reverted` | admin, `human` | `report_id`, `institution_id`, `cycle`, transición, `reason`, IDs de originales revertidas, reversals creadas y pendientes anuladas |

La revalidación reutiliza `report.validated` e incorpora `cycle`; no crea un alias
semántico nuevo. `AuditLog.created_at` es el timestamp canónico del evento. Los no-op no
generan auditoría.

Los payloads no contienen:

- bytes de archivos;
- URLs firmadas;
- object keys;
- descripción de evidencia;
- secretos;
- información personal innecesaria.

La auditoría administrativa solo es visible para administradores globales. La
información disponible para el ciudadano se entrega mediante sus contratos de estado e
historial, no mediante acceso a `AuditLog`.

`GET /api/v1/admin/audit-logs/` admite los filtros `action`, `actor_type`,
`actor_user_id`, `report_id`, `institution_id`, `created_from`, `created_to` y `page`.
Usa páginas de 20, orden `created_at, id` descendente y `private, no-store`. S10 no
ofrece edición ni eliminación de auditoría; su retención acompaña la vida del reporte y
del ledger.

### 10. Notificaciones — S10-D09

**S10 no crea, encola ni envía notificaciones.** Sus operaciones producen estado,
historial, auditoría y, cuando corresponde, ledger.

- las notificaciones de rewards y redenciones corresponden a S12;
- las notificaciones del ciclo de reportes corresponden a S13;
- los enums o modelos existentes no habilitan por sí solos un envío;
- destinatarios, contenido, privacidad, idempotencia, retries y manejo de fallos se
  resolverán en los sprints que implementen esas notificaciones.

El éxito de una operación S10 no depende del broker, email ni proveedor externo de
notificaciones.

### 11. Concurrencia e idempotencia — S10-D10

El aislamiento de base de datos es `READ COMMITTED`. `Report` es la raíz del agregado
serializado y las operaciones críticas usan `select_for_update()`. Rol, usuario activo,
membresía, tenant, estado y ciclo se vuelven a comprobar después de adquirir los locks.

El orden global es:

```text
Operator:
User → InstitutionOperator → Report → Category → Institution → RewardTransaction

Admin:
User → Report → Category → Institution → RewardTransaction

System:
Report
```

Las filas de reward se materializan en orden estable `created_at, id`. Estado, historia,
auditoría y ledger forman una sola transacción.

`PATCH status` es naturalmente idempotente: si el target ya fue alcanzado en el mismo
ciclo, devuelve `200` no-op. La evidencia usa `Idempotency-Key`. La reversión exige el
ciclo esperado: repetir una reversión del mismo ciclo devuelve `200` no-op, mientras
que una petición de un ciclo antiguo nunca puede revertir un ciclo nuevo.

Los no-op no crean historial, auditoría ni reward. Además de los locks, constraints de
base de datos impiden bonus y reversals duplicados.

#### Resultados de carreras

| Carrera | Resultado normativo |
|---|---|
| Evidencia + resolución | Se serializan por `Report`. Si la evidencia confirma primero, la resolución puede continuar; si al resolver no hay evidencia vigente, `409 RESOLUTION_EVIDENCE_REQUIRED` |
| Resolución + autocierre | Una resolución nueva no cumple 72 horas. Sobre un reporte ya resuelto, la resolución repetida es no-op |
| Cierre manual + automático | El primero crea el cierre; el segundo devuelve no-op. Solo una historia y una auditoría |
| Reversión + revalidación | Se serializan. Revalidar antes de completar la reversión devuelve conflicto; si la reversión gana, la revalidación crea el ciclo siguiente |
| Reversión + cierre | Si el cierre gana, la reversión todavía puede revertir `CERRADO`; si la reversión gana, el cierre recibe `409 REPORT_STATE_NOT_ALLOWED` |
| Resolución duplicada | El primer request resuelve y acredita; el segundo devuelve no-op |
| Reward duplicado | Lock y constraint por reporte/ciclo/tipo permiten una sola fila |
| Auditoría duplicada | Solo la mutación ganadora audita; el no-op no crea una segunda entrada |

Los errores de autenticación producen `401`; rol o membresía insuficientes, `403`;
recurso inexistente o cross-tenant, `404`; estado o ciclo obsoleto, `409`.

### 12. Migraciones y backfill — S10-D01B/S10-D02

La implementación futura requerirá, como mínimo:

- persistir `resolution_cycle` en los agregados aprobados;
- incorporar `closed_at` a la proyección actual de `Report`;
- asociar evidencia, historial y reward al ciclo;
- persistir la key de idempotencia de evidencia;
- añadir unicidad de `VALIDATION_BONUS` y `RESOLUTION_BONUS` por reporte/ciclo;
- garantizar como máximo una `REVERSAL` por transacción original;
- revisar los datos existentes;
- asignar ciclo `1` a reportes ya validados y a sus registros históricos/económicos
  asociados cuando la evidencia permita determinarlo correctamente;
- conservar ciclo `0` para reportes nunca validados.

El backfill debe fallar de forma visible ante datos ambiguos; no puede fabricar una
relación de ciclo silenciosamente. La forma exacta de las migraciones y sus pruebas se
determinará en el gate de implementación. Este ADR no ejecuta ninguna migración.

## Alternativas consideradas

| Alternativa | Motivo del descarte |
|---|---|
| Crear un estado permanente `VALIDADO` | Duplica un evento ya representado por campos de validación y contradice la máquina vigente |
| Crear `EN_REVISION` | Introduce un estado no respaldado; `CLASIFICADO` ya representa un reporte revisable antes de validarse |
| Revertir a `CREADO` | Perdería la semántica de la clasificación ya efectuada y podría disparar reprocesamiento innecesario |
| Eliminar evidencia al revertir | Destruye trazabilidad de la actuación institucional |
| Reutilizar evidencia entre ciclos | Permite resolver un nuevo ciclo sin demostrar una nueva actuación |
| Usar un contador económico como ciclo | Mezcla identidad operacional con saldos y contradice el ledger derivado |
| No persistir el ciclo | Impide constraints sólidos y vuelve ambiguos evidencia, bonus y reversiones posteriores |
| Usar `SERIALIZABLE` global | Aumenta contención y retries cuando el lock explícito del agregado resuelve las carreras conocidas |
| Permitir cierre ciudadano | No existe contrato de aceptación ciudadana y debilita la responsabilidad institucional |
| Permitir cierre al administrador | Rompe separación de funciones sin necesidad normativa |
| Usar las rutas genéricas históricas | Ignora el boundary institucional y las garantías de tenancy consolidadas en S9 |
| Emitir notificaciones desde S10 | Reintroduce dependencia de broker/proveedor y contradice la asignación a S12/S13 |

## Consecuencias

### Positivas

- La reversión conserva una historia completa y reconstruible.
- Cada validación, evidencia, resolución, cierre y bonus pertenece a un ciclo inequívoco.
- Los constraints ofrecen una defensa adicional frente a recompensas duplicadas.
- El boundary institucional conserva aislamiento entre tenants.
- La evidencia histórica no se pierde ni se reutiliza como si fuera nueva.
- Las mutaciones y reintentos tienen resultados idempotentes definidos.
- Las carreras conocidas tienen un resultado linealizable y auditable.
- El cierre automático no introduce efectos económicos o de notificación.

### Costos

- Aumenta la complejidad del modelo y del backfill.
- Se requieren columnas, constraints y migraciones adicionales.
- Los servicios deben respetar un orden global de locks.
- La evidencia histórica aumenta el almacenamiento acumulado.
- La API incorpora ciclo esperado, idempotencia y más respuestas de conflicto.
- El cierre automático requiere una tarea periódica y tratamiento explícito de fallos.
- Los clientes anteriores deberán adaptarse al contrato S10 antes de usar las nuevas
  operaciones.

## Riesgos

- Datos históricos inconsistentes o incompletos pueden impedir un backfill automático.
- Una autorización comprobada solo antes del lock podría permitir una carrera con la
  revocación de membresía.
- Un orden de locks distinto en servicios futuros puede introducir deadlocks.
- Un fallo simultáneo de persistencia y cleanup puede dejar evidencia huérfana.
- Una indisponibilidad de Storage puede impedir cargar o leer evidencia.
- Una migración o constraint incorrectos pueden bloquear datos históricos legítimos.
- Clientes que omitan `expected_cycle`, `cycle` o `Idempotency-Key` recibirán errores de
  contrato.
- El texto libre del motivo puede contener información sensible si el administrador
  ignora las restricciones de contenido.
- Una caída prolongada de Celery retrasa cierres automáticos, aunque no adelanta el
  cutoff ni pierde la elegibilidad.

## Tests requeridos para la implementación futura

La autorización de implementación deberá exigir, al menos:

1. **Máquina de estados:** cada transición válida e inválida y ausencia de `VALIDADO` o
   `EN_REVISION`.
2. **Reversión:** desde los cuatro estados permitidos, limpieza de proyecciones,
   conservación de institución/categoría/prioridad y rechazo desde estados no válidos.
3. **Ciclos:** 0 → 1, incremento tras reversión, monotonía, asociación de historia,
   evidencia y rewards.
4. **Backfill:** nunca validado, validado activo, resuelto/cerrado y datos ambiguos.
5. **Ledger:** originales `REVERTIDO`, pendientes `ANULADO`, reversals relacionadas,
   redenciones intactas, saldo negativo y deltas inmutables.
6. **Unicidad:** un bonus por tipo/ciclo y una reversal por original, incluida
   concurrencia real en PostgreSQL.
7. **Evidencia:** MIME/extensión/contenido, límite antes y después de normalizar,
   descripción, cantidad por ciclo, metadata eliminada y no reutilización.
8. **Storage:** upload fallido, DB fallida después del upload, cleanup fallido y firma
   indisponible sin filtrar claves.
9. **Permisos:** ciudadano, operador correcto, operador ajeno/inactivo y administrador
   para cada endpoint.
10. **Tenancy:** contexto ausente, malformado, repetido, inactivo, cross-tenant y cambio
    de membresía bajo lock.
11. **Idempotencia:** status repetido, evidence key repetida, key reutilizada con otro
    payload, reversión repetida y ciclo obsoleto.
12. **Auditoría e historial:** payload exacto y minimizado, actor/type correcto, no-op y
    rollback sin registros parciales.
13. **Cierre automático:** cutoff exacto, nunca antes de 72 horas, actor system, retries,
    configuración y ausencia de reward/notificación.
14. **Carreras:** los ocho casos definidos en este ADR con un único efecto persistido.
15. **API:** schemas estrictos, códigos estables, multipart, paginación, URLs firmadas,
    `private, no-store` y ocultación de recursos cross-tenant.
16. **Frontend:** acciones visibles solo al actor autorizado, confirmaciones claras,
    errores estables, ciclo actualizado y ausencia de filtración de evidencia.

## Compatibilidad con ADR existentes

### ADR-0004 — Ledger auditable con importes inmutables

No se reemplaza el modelo de ledger. Los saldos siguen derivados de filas
`ACREDITADO`, los deltas permanecen inmutables y los cambios económicos son síncronos y
transaccionales. `resolution_cycle` es metadata contextual; no es saldo, delta ni
contador de puntos.

La implementación deberá ampliar de forma explícita la metadata que puede asignarse al
acreditar una fila pendiente, sin alterar su importe. Las restricciones por ciclo
complementan las garantías de unicidad existentes.

### ADR-0007 — Almacenamiento privado de fotografías de reportes

No se modifica su arquitectura de seguridad: bucket privado, claves controladas por el
servidor, validación del contenido, recodificación sin metadata, upload previo a DB y
cleanup best-effort. S10 reutiliza esas garantías para evidencia de resolución.

No se hereda la unicidad global por hash de `ReportMedia`: la evidencia de resolución
tiene identidad y límites por ciclo.

### ADR-0010 — Créditos por institución y conversión 1:1

No se reemplazan la conversión 1:1, la atribución a la institución validante, la
inmutabilidad histórica ni los saldos derivados. Una reversión no traslada créditos y
puede dejar saldo institucional negativo. Los ciclos posteriores pueden generar los
nuevos bonus aprobados sin reactivar movimientos revertidos.

## Estado de implementación

ADR-0013 permanece **ACCEPTED**. Sprint 10 se implementó, desplegó, verificó y
reconcilió el 21/09/2026; PR #39 quedó integrado mediante `7ae82b8`. Se aplicaron
las migraciones y el backfill bajo el gate registrado en el
[plan de release S10](../runbooks/S10_RELEASE_PLAN.md). La condición de autorización
original ya se cumplió; los contratos de ciclos, evidencia y reversión no se modifican
por esta actualización documental del 20/10/2026.

## Referencias

- [ADR-0004 — Ledger auditable con importes inmutables](ADR-0004-ledger-auditable.md)
- [ADR-0007 — Almacenamiento privado de fotografías de reportes](ADR-0007-almacenamiento-de-fotografias.md)
- [ADR-0010 — Créditos por institución y conversión 1:1](ADR-0010-creditos-por-institucion-y-conversion-1-1.md)
- [Plan de desarrollo](../PLAN_DESARROLLO.md), RF-10, RF-11, RF-15, RF-16, RF-27,
  RF-28, RF-40, RF-49 y RF-50; §6.5 y Sprint 10
- [Contrato API](../API.md), §7 y cierre de alcance de Sprint 9
- [Diccionario de datos](../DICCIONARIO_DATOS.md)
- [Decisiones de incentivos](../decisiones-incentivos.md)
- [Cambios pendientes de la tesis](../CAMBIOS_TESIS_PENDIENTES.md)
