# S11-A — Matriz de escenarios, eventos y protocolo experimental

**Versión congelada:** `S11A-v1.1`. Enmienda de `S11A-v1.0` limitada a la instancia experimental de EX-09/HT-2; ver `S11A_DATOS_EVIDENCIA_Y_REBASELINE.md` §13.

**Baseline:** `main@635d89956ca0632d620916dc0ab298af48079ec4`

**Naturaleza:** especificación previa a la ejecución; no presenta resultados.

## 1. Convenciones de ejecución

- **Error controlado:** rechazo previsto por contrato (dominio o HTTP 4xx), con código/mensaje categorizable, sin excepción no capturada y sin escritura parcial. Puede ser la postcondición esperada de un paso negativo.
- **Error no controlado:** excepción no capturada, HTTP 5xx inesperado, crash, timeout no previsto, estado parcial, fuga entre tenants o divergencia respecto de la postcondición.
- **PASS de escenario:** todos los pasos y controles negativos esperados alcanzan su postcondición, no hay error no controlado y todos los artefactos obligatorios son recuperables.
- **FAIL de escenario:** ejecución válida y completa con al menos una postcondición incumplida o error no controlado.
- **NOT_DEMONSTRATED:** no ejecutado, ejecución inválida/incompleta, evidencia perdida o dependencia bloqueante.

Cada escenario es independiente salvo que declare expresamente otra cosa. Los actores, descripciones, imágenes, cédulas, coordenadas y tokens son sintéticos. Ningún dato productivo puede copiarse al entorno experimental.

## 2. Matriz maestra

| ID | Nombre | Objetivo / hipótesis | Clasificación actual | Decisión actual |
|---|---|---|---|---|
| EX-01 | Encuesta ciudadana | OE-1 / HE | MISSING | NOT_DEMONSTRATED |
| EX-02 | Identidad + creación en ciclo 0 | OE-2/3/5 / HT-3, HT-4, HT-6 | REQUIRES_INSTRUMENTATION | NOT_DEMONSTRATED |
| EX-03 | Confirmación + validación 0→1 | OE-4/5/6 / HT-4, HT-6 | REQUIRES_INSTRUMENTATION | NOT_DEMONSTRATED |
| EX-04 | Evidencia + resolución | OE-4/5/6 / HT-4, HT-6 | REQUIRES_INSTRUMENTATION | NOT_DEMONSTRATED |
| EX-05 | Autocierre | OE-5/6 / HT-4 | REQUIRES_INSTRUMENTATION | NOT_DEMONSTRATED |
| EX-06 | Reversión + revalidación | OE-4/5/6 / HT-4, HT-6 | REQUIRES_INSTRUMENTATION | NOT_DEMONSTRATED |
| EX-07 | Rechazo antes de validar | OE-4/5/6 / HT-4, HT-6 | REQUIRES_INSTRUMENTATION | NOT_DEMONSTRATED |
| EX-08 | Trazabilidad integral | OE-2/3/5/6 / HT-3, HT-4, HT-6 | REQUIRES_INSTRUMENTATION | NOT_DEMONSTRATED |
| EX-09 | Evaluación IA | OE-5/6 / HT-2 | MISSING | NOT_DEMONSTRATED |
| EX-10 | Rendimiento y asincronía | OE-3/5/6 / HT-1 | REQUIRES_INSTRUMENTATION | NOT_DEMONSTRATED |
| EX-11 | Ranking y redención | OE-4/6 / HT-4, HT-6 | FUTURE_FEATURE | NOT_DEMONSTRATED |

Las clasificaciones pertenecen al vocabulario tesis→sistema. Una capacidad puede existir en el baseline y, aun así, el escenario requerir instrumentación para producir evidencia correlacionada.

### 2.1 Matriz normalizada EX-02…EX-10: operación esperada

| scenario_id | preconditions | actor | action | expected event | expected state | expected cycle |
|---|---|---|---|---|---|---|
| EX-02 | Identidad sintética pendiente; catálogo/storage limpios | ciudadano autor + admin | enviar/aprobar identidad; crear reporte; probar documento duplicado | `identity.submitted`, `identity.approved`, `report.created` | identidad aprobada; reporte `CREADO` o `CLASIFICADO` si IA termina | 0 |
| EX-03 | Reporte activo no validado y dos ciudadanos verificados | confirmador + operador A | confirmar, probar bloqueos y validar | `report.confirmed`, `report.validated` | `ASIGNADO`, institución/validación presentes | 0 hasta confirmar; 1 al validar |
| EX-04 | Reporte `ASIGNADO`, ciclo 1 | operador A | iniciar, probar resolución sin evidencia, cargar evidencia idempotente y resolver | `report.processing_started`, `report.resolution_evidence_created`, `report.resolved` | `RESUELTO`, `resolved_at` presente | conserva 1 |
| EX-05 | Reporte `RESUELTO` con reloj controlado | `SYSTEM` | ejecutar autocierre antes y después del cutoff; repetir | `report.closed` sólo al ser elegible | `CERRADO`, `closed_at` presente | conserva 1 |
| EX-06 | Reporte validado/resuelto o cerrado en ciclo 1 | admin + operador A | revertir, repetir/stale, revalidar, probar evidencia vieja y resolver con nueva | `report.validation_reverted`, `report.validated`, eventos de proceso/evidencia/resolución | tras reversión `CLASIFICADO`; tras revalidar `ASIGNADO`; final `RESUELTO` | conserva 1 al revertir; sube a 2 al revalidar |
| EX-07 | Reporte no validado con rewards pendientes | operador A | rechazar; intentar repetir y validar | `report.rejected` | `RECHAZADO`, sin validación | conserva 0 |
| EX-08 | Dos tenants, actores autorizados/prohibidos, entorno limpio | autor, confirmador, operadores A/B, admin, anónimo | recorrer flujo completo e intentos prohibidos | conjunto de eventos del flujo y rechazos sin mutación | proyección final reconciliada; tenant B sin acceso | según evento; no hay saltos ni decrementos |
| EX-09 | protocolo `s11-ex09-openai-v1`, límites y crédito del proveedor verificados en el preflight, hashes/config congelados | ejecutor + OpenAI | ejecutar los 28 casos en una sola sesión secuencial | resultados experimentales por caso; no evento de dominio | 28 casos válidos o run inválido | no aplica |
| EX-10 | Entorno aislado, 10 usuarios, 50 payloads, worker 2 | VU + API + worker | 10×5 POST en ventana de 60 s y reconciliar tareas | `report.created` y tarea correlacionada por éxito | reportes/rewards/tareas consistentes | 0 para reportes recién creados |

### 2.2 Matriz normalizada EX-02…EX-10: evidencia y decisión

| scenario_id | expected audit entry | expected reward transaction | expected timestamp relation | expected invariant | evidence artifact | PASS condition | FAIL condition | NOT_DEMONSTRATED condition |
|---|---|---|---|---|---|---|---|---|
| EX-02 | `identity.submitted`, `identity.approved`, `report.created` (y `ai.report.classified` sólo si completa) | un `REPORT_CREATED +20/+20 PENDIENTE` | envío ≤ aprobación < creación; eventos IA posteriores a creación | HMAC único, cero cédula textual, media privada, ciclo 0 | responses, esquema/filas, audit, ledger, objeto/hash | todas las postcondiciones y bloqueos controlados | run válido con texto plano, duplicado aceptado, reward/ciclo incorrecto | no ejecutado, inválido o evidencia incompleta |
| EX-03 | `report.confirmed`, luego `report.validated` | +20/+10 acreditados en ciclo 1 y `VALIDATION_BONUS +30/+30` | confirmación ≤ validación; `credited_at` de pendientes coincide con instante de validación | una confirmación, un bonus, sin incremento por confirmar | responses, confirmation, audit, history, ledger pre/post | estado/ciclo/montos/unicidad exactos | run válido con cualquier discrepancia | no ejecutado, inválido o no reconciliable |
| EX-04 | processing/evidence/resolved, en ese orden | un `RESOLUTION_BONUS +50/+50 ACREDITADO` | evidencia creada ≤ `resolved_at`; audit/reward pertenecen a la misma operación | sólo evidencia del ciclo vigente habilita; clave idempotente no duplica | responses, evidencia/hash, audit, history, ledger | todas las transiciones y controles exactos | run válido con resolución sin evidencia, duplicación o ciclo/monto incorrecto | no ejecutado, inválido o artefacto privado no verificable |
| EX-05 | un `report.closed` de actor `SYSTEM` y modo automático | ninguna nueva | antes de cutoff no cierra; `closed_at ≥ resolved_at + 72 h` | mismo ciclo, reejecución sin duplicar | config/reloj, task output, estado, audit/history, ledger diff | frontera, idempotencia y ausencia de reward exactas | run válido con cierre prematuro/tardío, ciclo/reward extra | reloj/config/run no verificable o evidencia incompleta |
| EX-06 | reversal→validation→processing/evidence/resolution | compensaciones del ciclo 1, nuevo +30 y +50 del ciclo 2; creación no se reactiva | validación1 < reversión < revalidación2 < evidencia2 ≤ resolución2 | ciclo monotónico; stale controlado; evidencia1 no sirve en ciclo2 | audit/history, pares ledger, evidencias por ciclo, responses | compensación, ciclos e idempotencia exactos | run válido con pérdida histórica, doble emisión o uso de evidencia vieja | no ejecutado, inválido o pares no reconciliables |
| EX-07 | un `report.rejected` | +20/+10 pendientes pasan a `ANULADO`; ningún bonus/reversal | creación/confirmación < rechazo; `validated_at` permanece null | rechazo sólo prevalidación; repetición no muta | responses, report, audit/history, ledger pre/post | estado/ciclo/anulación exactos | run válido con acreditación, compensación o mutación repetida | no ejecutado, inválido o evidencia incompleta |
| EX-08 | una entrada por mutación autorizada; ninguna por intentos que el contrato no audita | ledger reconciliado por evento/ciclo | timestamps no decrecientes por cadena causal; cierre/reversión posteriores a resolución | cero acceso cross-tenant, 100 % de celdas obligatorias trazables | matriz evento×audit×history×ledger×evidence, responses | HT4-01/02 al 100 % del escenario | omisión, evento no controlado, acceso cruzado o inconsistencia | no ejecutado, interceptado en vez de full-stack, inválido o incompleto |
| EX-09 | ninguna entrada `AuditLog` del dominio; manifest experimental en su lugar | ninguna | requests secuenciales, sin concurrencia; 60 s máximo por request | 28 casos, cero retry, parámetros/hashes idénticos | JSON/JSONL por caso, informe único, stderr y hashes | run válido y CA-4 ≥85 % | run válido, 28/28 respuestas válidas y CA-4 <85 % | cualquier error/timeout/falta/divergencia/pérdida o no ejecución |
| EX-10 | un `report.created` por creación exitosa | un `REPORT_CREATED +20/+20 PENDIENTE` por éxito | commit ≤ enqueue; task start ≥ enqueue; HTTP no requiere `task_end` (sin imponer orden artificial task_end/http_end) | 50 intentos únicos reconciliados, throttle real y sin IA síncrona | salida cruda, config, timestamps, filas, cola, logs y hashes | regla compuesta HT-1 | run válido que incumple throughput/latencia/asincronía | caída de harness, correlación/config/artefactos incompletos o no ejecución |

## 3. Escenarios congelados

### EX-01 — Encuesta ciudadana

- **Actores:** persona participante, responsables académicos y custodio del dataset.
- **Precondición:** formulario digital fiel al cuestionario del Anexo G y con hash, consentimiento, población/reglas HE-01…04, ventana y plan de custodia aprobados. El cuestionario existe; el formulario desplegable, la ventana, las aprobaciones y el custodio están `UNRESOLVED/BLOCKED` al congelar S11-A.
- **Datos sintéticos:** no aplica a las respuestas; sólo pueden usarse respuestas ficticias para probar el cálculo y deben quedar separadas/rotuladas.
- **Estado inicial:** existe el cuestionario en la tesis, pero no un dataset de respuestas verificable.
- **Secuencia:** administrar el instrumento sin alterar ítems; exportar; preservar original; depurar con reglas previas; calcular HE-01…HE-04; generar tabla de exclusiones y hashes.
- **Estado final esperado:** dataset original y derivado trazables, N real declarado y resultados reproducibles.
- **Evidencia:** instrumento/versionado, aprobación/consentimiento aplicable, export original, codebook, log de depuración, dataset minimizado, script/comando de cálculo, resultados y hashes.
- **Cleanup:** revocar accesos temporales y aplicar la retención aprobada; nunca borrar la evidencia primaria fuera de la política.
- **Criterio:** PASS/FAIL de HE según HE-01/02; si falta cualquiera de las precondiciones o evidencias, `NOT_DEMONSTRATED`.
- **Dependencias:** aprobación académica/ética, formulario fiel al Anexo G, canales de muestreo, ventana, custodia y retención; no son evidencia.
- **Clasificación / estado:** `MISSING`; `NOT_DEMONSTRATED`.

### EX-02 — Identidad verificada y creación, ciclo 0

- **Actores:** `citizen_author`, `admin_identity`; servicios Google fake, almacenamiento privado, API y worker.
- **Precondición:** base aislada; secreto HMAC efímero; categoría/institución activas; cuenta Google sintética; ciudadano no verificado; buckets experimentales vacíos; `run_id` activo.
- **Datos sintéticos:** cédula canario, selfie artificial que puede contener esa cédula, foto urbana artificial única, descripción y coordenadas dentro del área permitida.
- **Estado inicial:** `CitizenProfile` sin aprobación; no existe reporte, media, historial ni ledger del caso.
- **Secuencia:** login sintético; envío y revisión manual de identidad; comprobación de que sólo persiste HMAC; alta repetida con la misma cédula para observar rechazo controlado; creación de reporte; captura inmediata antes de validación.
- **Estado final esperado:** identidad `APROBADO`; un reporte `CREADO` o `CLASIFICADO` si terminó la tarea IA; `resolution_cycle=0`; media privada; historial inicial; `REPORT_CREATED +20/+20 PENDIENTE`; ninguna cédula textual en base/log/export.
- **Evidencia:** responses, esquema/filas minimizadas, hash de objetos, HMAC enmascarado, historial, auditoría, ledger, trazas de tarea y resultado del intento duplicado.
- **Cleanup:** borrar objetos y resetear base/Redis con el procedimiento del run; preservar sólo artefactos redactados.
- **Criterio PASS/FAIL:** PASS si HT3-01/02 y todas las postcondiciones se cumplen; FAIL ante ejecución válida con texto plano persistido, duplicado aceptado, ciclo distinto de 0 o ledger incorrecto.
- **Dependencias:** correlación S11-B y fixture de identidad; no cuentan como evidencia.
- **Clasificación / estado:** `REQUIRES_INSTRUMENTATION`; `NOT_DEMONSTRATED`.

### EX-03 — Confirmación y validación, ciclo 0→1

- **Actores:** `citizen_author`, `citizen_confirmer`, `operator_A`.
- **Precondición:** dos ciudadanos sintéticos verificados; operador con membresía activa; reporte activo del autor en ciclo 0 y atribuido a institución A.
- **Datos sintéticos:** reporte de EX-03 independiente, tokens y catálogo congelados.
- **Estado inicial:** reporte `CREADO|CLASIFICADO`, `resolution_cycle=0`, autor con `+20 PENDIENTE`, sin validación.
- **Secuencia:** confirmar una vez por ciudadano ajeno; repetir confirmación y probar auto-confirmación como rechazos controlados; validar por operador A; repetir validación como rechazo controlado.
- **Estado final esperado:** una confirmación; ésta no cambia el ciclo; luego estado `ASIGNADO`, `validated_at/by` e institución A, `resolution_cycle=1`; `+20` y `+10` pasan a `ACREDITADO` en ciclo 1; bonus autor `+30` acreditado; ninguna duplicación.
- **Evidencia:** responses, confirmación, estado, historial, auditoría, ledger antes/después y constraints.
- **Cleanup:** reset aislado; preservar exports minimizados y hashes.
- **Criterio PASS/FAIL:** PASS si ciclo, estados, montos, unicidad y errores controlados coinciden exactamente; FAIL ante run válido con discrepancia.
- **Dependencias:** runner/correlación S11-B.
- **Clasificación / estado:** `REQUIRES_INSTRUMENTATION`; `NOT_DEMONSTRATED`.

### EX-04 — Evidencia y resolución

- **Actores:** `operator_A`, `citizen_author` como observador autorizado.
- **Precondición:** reporte `ASIGNADO`, validado, institución A, `resolution_cycle=1`; sin evidencia del ciclo.
- **Datos sintéticos:** archivo de evidencia artificial y descripción; clave de idempotencia fija del escenario.
- **Estado inicial:** sin `ResolutionEvidence`; sin bonus de resolución.
- **Secuencia:** pasar a `EN_PROCESO`; intentar resolver sin evidencia y observar rechazo controlado; cargar evidencia en ciclo 1; repetir misma clave/contenido para probar idempotencia; intentar misma clave con contenido distinto y observar rechazo; resolver.
- **Estado final esperado:** una evidencia privada del ciclo 1; reporte `RESUELTO`, `resolved_at` presente, ciclo aún 1; bonus autor `+50/+50 ACREDITADO` del ciclo 1; historial/auditoría correlacionados.
- **Evidencia:** responses, metadatos/hash de evidencia, ledger, historial, auditoría, timestamps y accesos autorizados/denegados.
- **Cleanup:** borrar objeto privado experimental después de preservar metadatos redactados; reset de entorno.
- **Criterio PASS/FAIL:** PASS si sólo la evidencia del ciclo vigente habilita resolución y no hay duplicación; FAIL ante discrepancia válida. La evidencia prueba el registro técnico, no la resolución real del problema urbano.
- **Dependencias:** almacenamiento privado aislado y correlación S11-B.
- **Clasificación / estado:** `REQUIRES_INSTRUMENTATION`; `NOT_DEMONSTRATED`.

### EX-05 — Autocierre

- **Actores:** worker/Beat `SYSTEM`; operador como creador del estado previo.
- **Precondición:** reporte `RESUELTO`, ciclo 1, `resolved_at` fijado por reloj controlado al límite de elegibilidad; evidencia/bonus ya existentes.
- **Datos sintéticos:** reloj congelado y reporte independiente.
- **Estado inicial:** `closed_at=null`; tarea de autocierre sin ejecuciones pendientes.
- **Secuencia:** ejecutar antes del cutoff y verificar no-op; avanzar reloj al cutoff documentado de 72 h; ejecutar tarea; reejecutar para idempotencia.
- **Estado final esperado:** `CERRADO`, `closed_at` presente, ciclo 1 sin incremento, historial y auditoría `SYSTEM` con `closure_mode=automatic`; ningún movimiento adicional de incentivos.
- **Evidencia:** configuración/cutoff, salida de tarea, estado pre/post, historial, auditoría y ledger sin cambio.
- **Cleanup:** restaurar reloj/config y resetear base/broker.
- **Criterio PASS/FAIL:** PASS si frontera temporal, idempotencia y trazabilidad coinciden; FAIL ante ejecución válida con cierre prematuro/tardío, cambio de ciclo o reward extra.
- **Dependencias:** reloj controlable y runner de tareas S11-B.
- **Clasificación / estado:** `REQUIRES_INSTRUMENTATION`; `NOT_DEMONSTRATED`.

### EX-06 — Reversión y revalidación

- **Actores:** `admin`, `operator_A`, `citizen_author`, `citizen_confirmer`.
- **Precondición:** reporte validado y resuelto/cerrado en ciclo 1, con evidencia ciclo 1 y ledger acreditado; motivo válido; `expected_cycle=1`.
- **Datos sintéticos:** el caso independiente conserva identificadores del ledger/evidencia del ciclo 1.
- **Estado inicial:** proyección validada ciclo 1.
- **Secuencia:** reversión administrativa; repetición idempotente; intento con ciclo obsoleto como rechazo controlado; revalidación por operador A; pasar a `EN_PROCESO`; intentar resolver usando sólo evidencia del ciclo 1 y observar rechazo; cargar evidencia ciclo 2 y resolver.
- **Estado final esperado:** tras reversión, `CLASIFICADO`, ciclo permanece 1, timestamps de validación/resolución/cierre limpiados, transacciones del ciclo 1 `REVERTIDO` y compensaciones `REVERSAL`; tras revalidación, `ASIGNADO` y ciclo 2, bonus de validación del ciclo 2 sin reactivar `REPORT_CREATED`; evidencia vieja no habilita resolución; nueva evidencia ciclo 2 sí; bonus de resolución ciclo 2 único.
- **Evidencia:** estado e historial por paso, auditoría, pares transacción/compensación, evidencias por ciclo, respuestas idempotentes/stale.
- **Cleanup:** reset aislado y borrado de objetos tras preservar metadatos.
- **Criterio PASS/FAIL:** PASS si ciclo monotónico, compensación, no reactivación e aislamiento de evidencia son exactos; FAIL ante run válido con discrepancia.
- **Dependencias:** runner/correlación S11-B.
- **Clasificación / estado:** `REQUIRES_INSTRUMENTATION`; `NOT_DEMONSTRATED`.

### EX-07 — Rechazo antes de validar

- **Actores:** `citizen_author`, `citizen_confirmer`, `operator_A`.
- **Precondición:** reporte activo no validado en ciclo 0 con creación y confirmación pendientes; motivo del catálogo.
- **Datos sintéticos:** reporte y confirmación independientes.
- **Estado inicial:** `CREADO|CLASIFICADO`, ciclo 0, `validated_at=null`, `+20` y `+10 PENDIENTE`.
- **Secuencia:** rechazo por operador competente; repetición y validación posterior como rechazos controlados.
- **Estado final esperado:** `RECHAZADO`, motivo tipificado, ciclo 0, ambas transacciones `ANULADO`, sin acreditación/compensación/bonus; historial y auditoría.
- **Evidencia:** responses, estado, historial, auditoría y ledger pre/post.
- **Cleanup:** reset aislado.
- **Criterio PASS/FAIL:** PASS si sólo se anulan pendientes y no aparece reward adicional; FAIL ante discrepancia válida.
- **Dependencias:** runner/correlación S11-B.
- **Clasificación / estado:** `REQUIRES_INSTRUMENTATION`; `NOT_DEMONSTRATED`.

### EX-08 — Trazabilidad integral y autorización

- **Actores:** autor, confirmador, `operator_A`, `operator_B`, admin y anónimo.
- **Precondición:** tenants A/B activos; un reporte nuevo asignable a A; matriz rol×acción congelada.
- **Datos sintéticos:** reporte, fotos/evidencias y canarios propios; sin reutilizar EX-02…07.
- **Estado inicial:** entorno limpio y contadores cero para el `run_id`.
- **Secuencia:** identidad→creación→clasificación fake→confirmación→validación→proceso→evidencia→resolución→cierre manual→reversión; intercalar intentos de operador B, anónimo y ciclo obsoleto; consultar historia/auditoría como actores permitidos/no permitidos.
- **Estado final esperado:** toda mutación autorizada tiene el artefacto exigido; todo intento prohibido es controlado y no altera estado; tenant B no observa datos privados; ledger/evidencia/ciclos se reconcilian.
- **Evidencia:** matriz evento×historial×auditoría×ledger×evidencia, responses, export minimizado, conteos y hashes.
- **Cleanup:** reset y revocación de tokens sintéticos; conservar dossier redactado.
- **Criterio PASS/FAIL:** PASS con HT4-01/02 al 100 % para el escenario; FAIL por omisión, acceso cruzado o evento no controlado.
- **Dependencias:** `run_id` y extractor S11-B; éstos no son evidencia.
- **Clasificación / estado:** `REQUIRES_INSTRUMENTATION`; `NOT_DEMONSTRATED`.

### EX-09 — Evaluación IA

- **Actores:** ejecutor autorizado y proveedor OpenAI; no intervienen usuarios reales.
- **Precondición:** protocolo `s11-ex09-openai-v1`. El preflight atesta sin secreto los límites y el crédito del proveedor. La credencial sólo está disponible en el entorno. Commit, config, prompt y dataset son los congelados, y el directorio persistente es seguro.
- **Datos sintéticos:** 28 casos `s8-evaluation-v1`, SHA-256 `3bc54d591dc104aeabeecb2f5cc7c7a38e70ba042e7f045321ab33ae9b5efad4`.
- **Estado inicial:** ningún resultado de EX-09; cero llamadas realizadas por el run.
- **Secuencia:** preflight sin consumir llamada; los 28 casos en orden, en una sola sesión, uno por vez, con timeout de 60 s y cero reintentos; persistir y verificar hash; calcular métricas una sola vez.
- **Estado final esperado:** 28 respuestas válidas y correlacionadas, parámetros invariantes, informe único y diagnósticos; cualquier error invalida la corrida completa.
- **Evidencia:** manifest, JSON/JSONL por caso, stderr redactado, informe único, versiones/hashes, atestación de límites y crédito documentada sin secreto y hashes de todos los artefactos.
- **Cleanup:** retirar secreto del proceso y revocar acceso temporal; no borrar artefactos persistentes.
- **Criterio PASS/FAIL:** sólo con 28 válidas: PASS si CA-4 ≥85 % (≥24/28), FAIL si <85 %. Con error/timeout/caso faltante/divergencia/evidencia perdida: `NOT_DEMONSTRATED`.
- **Dependencias:** límites, crédito y proveedor externos; son condiciones, no evidencia de exactitud.
- **Instancia anterior:** en `S11A-v1.0` esta ficha fijaba el proveedor Gemini y el protocolo `s8-e2-r2` (dos lotes de 14 en ventanas RPD distintas, pacing de 20 s y cuota ≥14 por lote). Esa configuración se sustituyó antes de ejecutarse; nunca produjo un resultado de EX-09.
- **Clasificación / estado:** `MISSING`; `NOT_DEMONSTRATED`.

### EX-10 — Rendimiento y asincronía

- **Actores:** 10 ciudadanos sintéticos verificados, generador de carga, API, PostgreSQL/PostGIS, Redis y worker Celery.
- **Precondición:** entorno aislado reseteado; dataset de 50 requests; throttle real 5/h por usuario; worker concurrencia 2; adaptador fake; health/readiness verdes; telemetría `run_id` activa.
- **Datos sintéticos:** 50 descripciones/fotos/ubicaciones únicas de versión y SHA congelados.
- **Estado inicial:** cero reportes/rewards/tareas del run y caches/colas vacías.
- **Secuencia:** barrera de 10 VU; cada uno envía cinco requests secuenciales en 60 s; capturar duraciones y HTTP; esperar sólo después de la ventana para inspeccionar tareas; reconciliar 50 intentos con base/ledger/cola.
- **Estado final esperado:** artefactos completos para HT1-01…04, sin request perdido o duplicado.
- **Evidencia:** salida cruda de carga, config, timestamps, histogramas, filas minimizadas, métricas de cola, logs redactados y hashes.
- **Cleanup:** reset base/Redis/buckets y preservar artefactos en almacenamiento persistente.
- **Criterio PASS/FAIL:** regla HT-1 del diccionario; una caída de harness o evidencia incompleta deja `NOT_DEMONSTRATED`.
- **Dependencias:** instrumentación/generador S11-B; no son evidencia.
- **Clasificación / estado:** `REQUIRES_INSTRUMENTATION`; `NOT_DEMONSTRATED`.

### EX-11 — Ranking y redención (diferido)

- **Actores previstos:** ciudadanos, operador institucional y admin.
- **Precondición:** implementación funcional autorizada de ranking mensual, saldo/canje y privacidad, más contratos actualizados. No se satisface en el baseline.
- **Datos sintéticos previstos:** usuarios con puntos mensuales/históricos, cuatro visibilidades, saldos por institución y concurrencia de canje.
- **Estado inicial / secuencia / final:** `UNRESOLVED` hasta que exista el diseño implementado; no se simula con inserts manuales ni con la mera presencia de tablas.
- **Evidencia prevista:** API/UI/servicios, suite de reglas, ledger, privacidad, unicidad mensual y trazas concurrentes.
- **Cleanup:** se fijará antes de ejecutar.
- **Criterio:** mientras no exista la capacidad, `NOT_DEMONSTRATED`; no corresponde `FAIL` por ausencia planificada.
- **Dependencias:** implementación futura; `FUTURE_FEATURE / DEPENDENCY`.
- **Clasificación / estado:** `FUTURE_FEATURE`; `NOT_DEMONSTRATED`.

## 4. Semántica congelada de `resolution_cycle`

`cycle 0 = reporte antes de validación`.

1. Todo reporte nace en `resolution_cycle=0`: es el ciclo **prevalidación**.
2. La validación humana fija institución/`validated_at`/`validated_by`, cambia a `ASIGNADO` e incrementa exactamente una vez `0→1` o `n→n+1` tras una reversión.
3. Una confirmación nunca incrementa el ciclo. Si ocurre antes de validar queda pendiente; si ocurre durante una validación activa se acredita en el ciclo vigente.
4. Evidencia, `EN_PROCESO`, `RESUELTO`, cierre manual y autocierre conservan el ciclo vigente.
5. La reversión conserva el número del ciclo revertido; no lo decrementa. La revalidación posterior es el evento que lo incrementa.
6. La evidencia sólo habilita resolución cuando su `resolution_cycle` coincide con el vigente.
7. `resolution_cycle` no es un contador genérico de resoluciones, confirmaciones o cambios de estado.
8. Una evidencia técnica demuestra que se cargó un artefacto para el ciclo; no demuestra que el problema urbano quedó realmente solucionado.

## 5. Matriz evento → estado → evidencia → incentivo

| Evento | Estado/ciclo esperado | Puntos/créditos esperados | Historial/auditoría/evidencia | Duplicidad/idempotencia | Escenario |
|---|---|---|---|---|---|
| Creación | `CREADO`, ciclo 0 | autor `+20/+20 PENDIENTE` | historial inicial; auditoría/response; foto privada | un `REPORT_CREATED`; foto hash duplicada controlada | EX-02 |
| Clasificación IA válida | `CLASIFICADO`, ciclo 0 | sin cambio | `AiClassification`, historial/auditoría IA | tarea idempotente según contrato | EX-08/09 |
| Confirmación prevalidación | estado y ciclo 0 sin cambio | confirmador `+10/+10 PENDIENTE` | `ReportConfirmation`; evento/response esperado | unique ciudadano+reporte; autor/repetido controlados | EX-03 |
| Validación humana | `ASIGNADO`, ciclo 1 | pendientes `ACREDITADO`; autor `+30/+30`; institución 1:1 | historial y `report.validated` | repetición controlada; un bonus por ciclo | EX-03 |
| Inicio de proceso | `EN_PROCESO`, mismo ciclo | sin cambio | historial y auditoría | no-op coherente o transición inválida controlada | EX-04 |
| Carga de evidencia | estado/ciclo sin cambio; evidencia ciclo vigente | sin cambio | objeto privado, hash, auditoría/response | misma clave+contenido no duplica; clave+otro contenido rechaza | EX-04 |
| Resolución | `RESUELTO`, mismo ciclo | autor `+50/+50 ACREDITADO` | evidencia vigente requerida, historial y auditoría | un bonus por ciclo; sin evidencia se rechaza | EX-04 |
| Autocierre/manual | `CERRADO`, mismo ciclo | sin cambio | historial y auditoría con modo | reejecución no duplica | EX-05/08 |
| Reversión | `CLASIFICADO`, conserva ciclo n | acreditados del ciclo `REVERTIDO` + compensaciones negativas; pendientes aplicables `ANULADO` | historial, `report.validation_reverted` y pares de ledger | repetición no duplica; `expected_cycle` obsoleto se rechaza | EX-06 |
| Revalidación | `ASIGNADO`, ciclo n+1 | nuevo `+30/+30`; no reactiva creación histórica | historial y auditoría | un bonus por nuevo ciclo | EX-06 |
| Evidencia vieja tras revalidar | sin cambio | sin cambio | rechazo controlado; evidencia n permanece histórica | no habilita resolver n+1 | EX-06 |
| Rechazo prevalidación | `RECHAZADO`, ciclo 0 | participaciones pendientes `ANULADO`; sin bonus/compensación | historial y `report.rejected` | repetición/validación posterior controladas | EX-07 |

Esta tabla es también el denominador de HT6-01 para reglas actuales. Ranking mensual y redención no se añaden al denominador hasta que EX-11 deje de ser `FUTURE_FEATURE`; mientras tanto, HT-6 completa permanece `NOT_DEMONSTRATED`.

### 5.1 Matriz decisoria específica de HT-6

| Regla | Escenario / evento | Puntos y créditos | Estado transaccional | Reversión | Duplicidad / idempotencia | Ciclo | Evidencia decisoria |
|---|---|---|---|---|---|---|---|
| R-01 | EX-02 / creación | autor `+20/+20` | nace `PENDIENTE` | si llegó a acreditarse en el ciclo revertido: original `REVERTIDO` y compensación `-20/-20`; si se rechaza antes: `ANULADO` | un `REPORT_CREATED` por reporte; una falla atómica no deja duplicado | sin ciclo mientras pendiente; se atribuye al ciclo 1 al validar | reporte, reward, constraint y audit/response |
| R-02 | EX-03 / confirmación válida | confirmador `+10/+10` | `PENDIENTE` antes de validar; `ACREDITADO` si ya hay validación vigente | acreditado del ciclo revertido: original `REVERTIDO` y compensación `-10/-10`; pendiente al revertir/rechazar: `ANULADO` | unique ciudadano+reporte; autor/repetición rechazados sin nuevo reward | no incrementa; se atribuye al ciclo de acreditación | confirmación, reward, response y constraint |
| R-03 | EX-03/06 / validación | autor `+30/+30` y acreditación 1:1 de pendientes | `ACREDITADO` | original `REVERTIDO` y compensación `-30/-30` al revertir su ciclo | un `VALIDATION_BONUS` por reporte+ciclo; reintento no duplica | incrementa exactamente `n→n+1` | reporte, historial, auditoría y rewards acreditados |
| R-04 | EX-04/06 / resolución | autor `+50/+50` | `ACREDITADO` | original `REVERTIDO` y compensación `-50/-50` al revertir su ciclo | un `RESOLUTION_BONUS` por reporte+ciclo; exige evidencia vigente | conserva el ciclo validado | evidencia, reporte, historial, auditoría y reward |
| R-05 | EX-03/04/06 / conversión | `1 punto acreditado = 1 crédito` de la institución validadora para todos los deltas positivos actuales | mismo estado del movimiento | toda compensación conserva razón `1:1` con signo opuesto | no hay fila de saldo mutable ni doble emisión | institución y ciclo quedan fijados al acreditar | comparación exacta `impact_delta == credit_delta`, institución y ciclo |
| R-06 | EX-06 / reversión | suma algebraica de compensaciones opuesta a movimientos acreditados del ciclo | originales y compensaciones `REVERTIDO`; pendientes aplicables `ANULADO` | es la propia regla; no borra historia ni modifica deltas originales | segunda llamada no crea otra compensación; ciclo stale se rechaza | conserva n; revalidación posterior abre n+1 | pares `related_transaction`, sumas por usuario/institución/ciclo y auditoría |
| R-07 | EX-07 / rechazo prevalidación | no acredita ni bonifica; `+20/+10` dejan de aportar | pendientes pasan a `ANULADO` | no genera compensación porque nunca se acreditaron | segundo rechazo/validación posterior controlados | permanece 0 | rewards, reporte, historial y auditoría |
| R-08 | EX-11 / ranking mensual | sólo puntos acreditados del mes; valor aún no ejecutable | FUTURE_FEATURE | regla futura debe excluir anulados/revertidos | regla futura | FUTURE_FEATURE | `NOT_DEMONSTRATED`; no entra al denominador actual |
| R-09 | EX-11 / redención | créditos institucionales, límite una por institución/usuario/mes | FUTURE_FEATURE | regla futura debe convivir con saldo negativo tras reversión | regla futura de unicidad/serialización | FUTURE_FEATURE | `NOT_DEMONSTRATED`; no entra al denominador actual |

Para el subtotal actual, el denominador es R-01…R-07 y cada regla debe satisfacer **todas** sus celdas: una suma correcta con estado/ciclo/evidencia incorrectos es `FAIL`. R-08/R-09 no se cuentan como aciertos ni fallos y mantienen la hipótesis HT-6 completa en `NOT_DEMONSTRATED`.

## 6. Protocolo experimental común

### 6.1 Identidad del run

Antes de mutar el entorno, cada ejecución debe producir un manifest inmutable con:

- `methodology_version` vigente al iniciar el run (`S11A-v1.1`; los runs anteriores a la enmienda registran `S11A-v1.0`) y SHA-256 de los cuatro documentos S11-A;
- commit y estado limpio; versión de dataset/fixture, semilla y SHA-256;
- versiones de SO/runtime/dependencias, imagen/lockfiles y servicios DB/Redis;
- configuración no secreta relevante, zona horaria y reloj;
- timestamp UTC de inicio y un `run_id` único;
- escenario, ejecutor/rol, comandos y directorio persistente de salida.

El formato y la propagación técnica de `run_id` se implementan en S11-B; esta es la especificación congelada, no evidencia de que ya exista.

### 6.2 Pre-check, reset y post-check

1. Verificar baseline/config/hashes y ausencia de secretos en argumentos/logs.
2. Resetear únicamente base, buckets, cache y colas del entorno experimental identificado; registrar conteos cero o snapshot inicial.
3. Cargar fixtures desde semilla/version/hash y registrar conteos.
4. Ejecutar una sola vez el escenario según su protocolo, sin cambios ad hoc.
5. Capturar resultados crudos antes de calcular agregados.
6. Ejecutar post-checks de filas, constraints, estados, ledger, auditoría, objetos y colas.
7. Generar manifiesto de artefactos con tamaño, MIME, clasificación y SHA-256.
8. Limpiar el entorno experimental y demostrar conteos post-cleanup; los artefactos persistentes no se guardan en `/tmp`.

### 6.3 Artefactos mínimos

Manifest de run; stdout/stderr redactados; resultados por caso/request; configuración no secreta; pre/post-checks; export minimizado; informe agregado; hashes; registro de cleanup y una tabla de decisión. Capturas sólo cuando aporten evidencia no disponible en formato estructurado y siempre redactadas.

### 6.4 Invalidez

Un run es inválido si cambia método, código, dataset, prompt o config durante su ejecución; reutiliza datos productivos; pierde artefactos primarios; contiene secretos/PII prohibida; no puede reconciliar intentos con resultados; se reintentan selectivamente casos; o se ejecuta sobre un entorno no aislado. Un run inválido nunca es `FAIL`: queda `NOT_DEMONSTRATED` y se conserva el diagnóstico de invalidez por separado.
