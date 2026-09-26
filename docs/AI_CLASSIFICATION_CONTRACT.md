# Contrato de clasificación asistida por IA — v1

**Versión:** 1
**Estado:** ACCEPTED FOR S8
**Fecha:** 2026-08-29
**Alcance:** S8-A (contrato y documentación)
**Proveedor:** Google Gemini **previsto, no integrado todavía**

> **Actualización 2026-09-26 (ADR-0016).** El proveedor pasa a ser **OpenAI** (Responses
> API, `OPENAI_MODEL`), elegido con `AI_PROVIDER`. El contrato D1–D20 no cambia: misma
> lista blanca, mismo esquema v1 y mismo parser como frontera. Donde este documento dice
> Gemini o `GEMINI_*`, léase «el proveedor activo». Gemini se conserva solo para las
> corridas prerregistradas S8-E2-R2 y EX-09.

Este documento cierra por escrito el contrato de la clasificación asistida por IA del
Sprint 8 **antes** de implementar cualquier integración real. Es la fuente normativa para
S8-B (adapter y parser), S8-C (tarea, persistencia y auditoría), S8-D (API) y S8-E
(dataset y concordancia).

Nada de lo aquí decidido está implementado. `ai.classify_report` sigue siendo un no-op
([`tasks.py:12`](../backend/apps/ai_client_app/tasks.py)), no hay SDK instalado y no hay
secretos configurados.

## Fuente normativa y numeración de anexos

El prompt base procede del **Anexo H.3 «Prompt base para IA»** de
`docs/Tesis_Ciudadania_Activa_Revisada_Recompensas.docx`, que es la versión **revisada** de
la tesis y la fuente que sigue `PLAN_DESARROLLO.md`.

`docs/Tesis_Ciudadania_Activa.pdf` es la versión **anterior** y numera el mismo anexo como
H.2, porque no contiene el apartado de escenarios del módulo de incentivos que la revisión
insertó como H.2. La numeración de ambos documentos difiere en un lugar a partir de ahí:

| Apartado | PDF (versión anterior) | DOCX revisado (**normativo**) |
| --- | --- | --- |
| Dataset sintético | H.1 | H.1 |
| Escenarios del módulo de incentivos (S-01…S-10) | *no existe* | H.2 |
| Prompt base para IA | H.2 | **H.3** |
| Plan de pruebas | H.3 | H.4 |
| Pruebas de seguridad incluidas | H.4 | H.5 |
| Resultados esperados | H.5 | H.6 |

**Las referencias a «Anexo H.3» (prompt) y «Anexo H.2» (escenarios S-01…S-10) de
`PLAN_DESARROLLO.md` son correctas** y no se modifican. Quien consulte el PDF versionado
debe aplicar esta tabla de equivalencia.

---

## D1 — Entrada al proveedor: lista blanca explícita

El payload se construye por **lista blanca**, campo a campo. **Nunca** serializando un
`Report`, un serializer de dominio ni un `**vars(obj)`.

### Permitido

| Campo | Origen |
| --- | --- |
| `description` | `Report.description` |
| `current_category_slug` | `Report.category.slug` |
| `current_institution_slug` | `Report.institution.slug` · **nullable** |
| imagen normalizada | bytes de `ReportMedia`, ver D11 |
| `allowed_category_slugs` | catálogo cerrado, ver D2 |
| `allowed_institution_slugs` | catálogo cerrado, ver D2 |
| `allowed_priorities` | `ReportPriority`: `BAJA`, `MEDIA`, `ALTA` |

### Prohibido

`citizen_id` · email · nombre · `google_oauth_id` · datos de verificación de identidad ·
`location` · `latitude` · `longitude` · dirección · `report_id` · `created_at` ·
`updated_at` · `file_hash` · `RewardTransaction` · puntos · créditos · confirmaciones ·
cookies · tokens · JWT · cualquier secreto.

La regla desarrolla [`PLAN_DESARROLLO.md`](PLAN_DESARROLLO.md) §18, *Seguridad y permisos*:
«se envían **únicamente** descripción, categoría, institución elegida e imagen: ningún dato
identificatorio del ciudadano». Las coordenadas **no** están en esa lista y por tanto no se
envían.

## D2 — Catálogo cerrado

La IA **no puede crear** categorías ni instituciones. La autoridad sigue siendo
`admin_app.catalogs` y las tablas `categories` / `institutions`.

- La salida usa **slugs exactos** ya existentes: `category_slug`, `institution_slug`.
- El prompt recibe el conjunto permitido como dominio cerrado.
- Un valor fuera del conjunto **no** se crea, **no** se corrige creativamente y **no** se
  aproxima por similitud.

Un valor no mapeable produce:

```
suggested_category / suggested_institution = NULL
inconsistency = true
=> revisión humana
```

Referencia: los FK de `AiClassification` ya son nullable y `PROTECT` precisamente para
sostener este caso ([`models.py:25-40`](../backend/apps/ai_client_app/models.py)).

## D3 — Esquema de salida JSON v1

```json
{
  "category_slug": "baches",
  "institution_slug": "municipalidad-de-asuncion",
  "priority": "ALTA",
  "confidence": 0.87,
  "inconsistency": false,
  "justification": "La imagen y la descripción muestran un bache profundo en vía principal."
}
```

| Campo | Tipo | Reglas |
| --- | --- | --- |
| `category_slug` | `string` \| `null` | debe pertenecer a `allowed_category_slugs` |
| `institution_slug` | `string` \| `null` | debe pertenecer a `allowed_institution_slugs` |
| `priority` | `string` | exactamente `BAJA`, `MEDIA` o `ALTA` |
| `confidence` | `number` | `0.0 ≤ confidence ≤ 1.0`, inclusive |
| `inconsistency` | `boolean` | sin coerción desde string ni número |
| `justification` | `string` | no vacía, **máximo 1000 caracteres** |

**Política de campos desconocidos: RECHAZAR.** El esquema es estricto y cerrado. Una
respuesta con campos adicionales **no valida** y se trata como salida inválida (D8-B). No
se ignoran silenciosamente: un proveedor que empieza a devolver campos nuevos es un cambio
de contrato que debe verse, no absorberse.

Ningún campo fuera de esta tabla puede ser fuente de una decisión de dominio.

## D4 — `hubo_cambio` se deriva, no se confía

El **Anexo H.3** de la tesis revisada incluye `hubo_cambio` entre los campos que el modelo
debe devolver. **Este contrato se desvía deliberadamente de ese punto.**

`hubo_cambio` es una comparación entre la sugerencia y el estado actual del `Report`: es un
hecho que el backend conoce con certeza y el modelo solo puede estimar. Delegarlo al
proveedor introduce una fuente de error evitable en un dato verificable localmente.

- El prompt v1 **no** pide `hubo_cambio`.
- El esquema v1 **no** lo acepta (D3: campos desconocidos ⇒ rechazo).
- El backend lo **deriva** comparando la sugerencia validada con `Report.category`,
  `Report.institution` y `Report.priority`.
- **No** se persiste como campo propio (ver D17).

El resto del Anexo H.3 se conserva en intención; la desviación se limita a este campo y a
la sustitución de nombres libres por slugs (D2).

## D5 — Umbral de confianza

```
AI_CONFIDENCE_THRESHOLD = 0.75
```

| Condición | Efecto |
| --- | --- |
| `confidence >= 0.75` **y** `inconsistency == false` **y** todos los valores mapean | sugerencia **elegible** para aplicación automática asistida |
| `confidence < 0.75` | persistir clasificación · **no** aplicar al `Report` · revisión humana |
| `inconsistency == true` | persistir clasificación · **no** aplicar al `Report` · revisión humana |
| algún valor no mapeable | persistir clasificación · **no** aplicar al `Report` · revisión humana |

En todos los casos la clasificación **se persiste y se audita**. La diferencia está en si la
sugerencia se aplica al `Report` o queda como propuesta para el operador.

> **No confundir dos métricas distintas.** `AI_CONFIDENCE_THRESHOLD = 0.75` es la confianza
> autodeclarada del modelo sobre **un** caso. El **85 %** de CA-4 / RNF-05 es la concordancia
> agregada contra las etiquetas de oro de un dataset. No se comparan ni se derivan la una de
> la otra.

## D6 — La decisión final es humana

La clasificación es **asistida**, nunca autónoma.

**La IA puede:** sugerir categoría · sugerir institución · sugerir prioridad · marcar
inconsistencia · justificar · aportar `confidence`.

**La IA no puede:** rechazar un reporte · validar institucionalmente · acreditar puntos ·
emitir créditos · resolver o cerrar un reporte · sobrescribir decisiones humanas
posteriores · crear entradas de catálogo.

> **`RECHAZADO` queda explícitamente prohibido como transición originada por IA, bajo
> cualquier circunstancia.** Ninguna respuesta del proveedor, ninguna confianza, ninguna
> marca de inconsistencia puede producirla. Es una regla del plan
> ([`PLAN_DESARROLLO.md`](PLAN_DESARROLLO.md) §18) y del riesgo «Errores de IA» del
> [backlog](BACKLOG.md).

## D7 — Semántica de `CREADO → CLASIFICADO`

`CLASIFICADO` significa exactamente:

> **La ejecución de clasificación IA terminó y existe un resultado persistido y auditable.**

Cubre los cuatro desenlaces con resultado:

- clasificación aplicable;
- clasificación de baja confianza;
- clasificación inconsistente;
- clasificación que requiere revisión humana por no ser mapeable.

Por tanto: **baja confianza no es un fallo técnico** e **`inconsistency == true` tampoco lo
es**. Ambos terminan en `CLASIFICADO` con revisión humana pendiente.

| Situación | Estado resultante |
| --- | --- |
| Resultado persistido (cualquiera de los cuatro casos) | `CLASIFICADO` |
| Fallo total del proveedor tras agotar reintentos | permanece `CREADO` |
| Fallo del broker al encolar | permanece `CREADO` |
| Worker caído | permanece `CREADO` |

**La creación del `Report` nunca se revierte por un fallo de IA** (CA-5). El encolado ya
ocurre post-commit y con `robust=True`
([`services/reports.py:190`](../backend/apps/reports_app/services/reports.py)), de modo que
un fallo de cola no afecta al reporte ni a la transacción `PENDIENTE` de 20 puntos.

## D8 — Salida inválida: tres casos distintos

### A · Respuesta válida pero no mapeable

Esquema correcto, pero `category_slug` o `institution_slug` no pertenecen al catálogo.

- registrar el intento;
- `inconsistency_flag = true`;
- revisión humana;
- **nunca** crear catálogo;
- puede terminar en `CLASIFICADO`, porque existe resultado persistible.

### B · JSON o esquema inválido

JSON malformado, campo faltante, tipo incorrecto, valor fuera de dominio o **campo
desconocido** (D3).

- no aplicar ninguna sugerencia;
- registrar intento auditable;
- marcar revisión humana;
- **nunca** `RECHAZADO`.

La fila de `AiClassification` resultante **sí se persiste**, con esta forma:

```
suggested_category    = NULL
suggested_institution = NULL
suggested_priority    = ""
confidence            = NULL
inconsistency_flag    = true
justification         = mensaje técnico controlado por el backend
```

La `justification` en este caso la redacta el **backend**, no el modelo: es texto propio y
acotado, nunca la respuesta cruda del proveedor.

### C · Fallo técnico del proveedor

Timeout, 429, 5xx, error de red, credencial inválida. Ver D9.

## D9 — Política de reintentos

```
AI_MAX_RETRIES = 3
backoff        = exponencial
jitter         = sí
```

**Errores transitorios** — timeout, `429`, `5xx`, error temporal de red ⇒ reintento acotado
con backoff exponencial y jitter, máximo 3.

**Errores no transitorios** — API key inválida, configuración inválida, esquema inválido,
mapeo inválido ⇒ **sin reintento automático**. Reintentar no cambia el resultado y consume
cuota.

> **Nunca reintento sin límite.** Agotados los reintentos, el `Report` permanece `CREADO`
> (D7) y el fallo queda registrado.

La implementación concreta (nivel adapter frente a nivel Celery, política del SDK) se
decide en S8-B sin alterar esta intención.

## D10 — Idempotencia

La tarea de clasificación inicial **debe ser idempotente**.

**Regla:** la clasificación inicial solo puede aplicar cambios si el `Report` continúa en un
estado compatible con clasificación inicial.

La futura implementación requiere:

- `transaction.atomic`;
- `select_for_update` sobre el `Report`;
- guarda explícita de estado antes de aplicar;
- ningún efecto de dominio duplicado ante redelivery de Celery.

Una segunda entrega accidental de la tarea inicial **no debe**: aplicar la sugerencia dos
veces · cambiar `status` otra vez · duplicar `ReportStatusHistory` · duplicar el `AuditLog`
de decisión aplicada.

El **reproceso manual** (S8-D) es una operación **distinta** y sigue permitido: por eso
`ai_classifications` no tiene unicidad por reporte y no se le añade
([`models.py:18-19`](../backend/apps/ai_client_app/models.py)).

El precedente del repositorio es
[`notifications_app/tasks.py`](../backend/apps/notifications_app/tasks.py): la tarea bloquea
la fila y relee su estado para que dos entregas simultáneas produzcan un solo efecto.

**S8-A no añade campos ni constraints.** Solo fija la política.

## D11 — Imagen

- El bucket `reportes` **sigue privado**. No se hace público.
- **No** se envía `service_role` al proveedor.
- **No** se persisten URLs firmadas para la clasificación: son credenciales temporales y
  `audit_logs` es permanente.

**Vía elegida:** el worker descarga los bytes mediante el servicio interno de Storage y
envía los **bytes normalizados inline** al proveedor.

Los bytes almacenados ya están normalizados y sin metadata EXIF (ADR-0007), de modo que la
imagen enviada no arrastra geolocalización ni datos de cámara.

La implementación podrá añadir un `download()` a
[`core/services/storage.py`](../backend/apps/core/services/storage.py), que hoy expone
`upload`, `create_signed_url` y `delete`. **No se implementa en S8-A.**

## D12 — Privacidad

La descripción ciudadana es un dato **funcional necesario**: sin ella no hay clasificación.
No se envía ninguna PII identificatoria (D1).

El prompt debe tratar el texto ciudadano como **UNTRUSTED USER CONTENT** y delimitarlo
claramente. Ninguna instrucción contenida en `description` puede modificar el esquema, el
catálogo permitido, las decisiones del sistema ni las reglas de rechazo.

## D13 — Prompt injection

Defensa mínima contractual:

1. instrucciones del sistema separadas del texto ciudadano;
2. `description` delimitada explícitamente como contenido no confiable;
3. catálogo permitido incluido como dominio cerrado;
4. esquema JSON estricto;
5. **validación en backend obligatoria**;
6. la salida del modelo nunca se ejecuta como instrucción;
7. la IA nunca puede ordenar `RECHAZADO`.

> **El parser del backend es la frontera de seguridad.** No se pretende resolver prompt
> injection con instrucciones al modelo: las instrucciones reducen la probabilidad, la
> validación contra catálogo cerrado y esquema estricto acota el daño.

## D14 — Auditoría

```
actor_type = ai
evento     = ai.report.classified
```

`ActorType.AI` ya existe ([`core/enums.py:53`](../backend/apps/core/enums.py)) y
[`core/services/audit.py`](../backend/apps/core/services/audit.py) exige pasarlo de forma
explícita para decisiones de IA.

El payload debe permitir reconstruir:

`report_id` · `classification_id` · `prompt_version` · `model_name` · `raw_response` ·
`decision` · `applied` · `requires_human_review`.

**No se guarda:** API key · cabeceras · cookies · JWT · `service_role` · URLs firmadas ·
datos identificatorios del ciudadano.

## D15 — `prompt_version` en lugar del prompt renderizado

El prompt renderizado completo **no** se duplica en cada `AuditLog`.

El prompt se versiona en Git; la auditoría guarda su identificador:

```
prompt_version = s8-classification-v1
```

**Interpretación explícita de un requisito documental.**
[`PLAN_DESARROLLO.md`](PLAN_DESARROLLO.md) §18 pide auditar «el prompt utilizado». Guardar
el prompt renderizado en cada invocación replicaría la descripción íntegra del ciudadano
dentro de `audit_logs` —tabla permanente— una vez por clasificación y otra por cada
reproceso. Este contrato lo interpreta como **minimización de datos**: se audita *qué*
prompt se usó, no una copia del texto ciudadano embebido en él.

La trazabilidad se reconstruye como:

```
prompt versionado en Git
  + inputs de dominio persistidos (Report, categoría, institución)
  + clasificación persistida (AiClassification)
  + raw_response del AuditLog
```

Eso reproduce la decisión sin duplicar el dato personal. Cada versión del prompt es
inmutable en Git y direccionable por su identificador.

## D16 — `raw_response`

`raw_response` **no será columna de `AiClassification`**. Se reserva para el payload del
`AuditLog`.

```
MAX_AI_RAW_RESPONSE_CHARS = 8000
```

La respuesta se trunca a ese límite antes de auditarse. **Nunca** se almacenan cabeceras,
autenticación de la petición, API key ni metadata sensible de transporte.

## D17 — Modelo de datos futuro

`AiClassification` **permanece sin cambios en S8-A**. No se crea migración, no se ejecuta
`makemigrations` ni `migrate`.

La futura migración `0002` —**especificada aquí, no creada**— añade exactamente dos campos:

| Campo | Tipo previsto | Semántica | Compatibilidad histórica |
| --- | --- | --- | --- |
| `justification` | `TextField` | justificación normalizada de la clasificación | `blank` / default vacío para filas previas |
| `model_name` | `CharField(max_length=100)` | modelo del proveedor efectivamente usado (CT-32, O-05) | `blank` / default vacío para filas previas |

**No se añaden por ahora:** `raw_response` (D16) · `prompt` (D15) · `hubo_cambio` (D4) ·
estado de clasificación · error de IA · `task_id` · `provider_request_id`.

**No se introduce constraint de unicidad por reporte**: el modelo debe seguir admitiendo
reprocesos (D10).

`justification` es exigida literalmente por CA-2 («el resultado queda persistido con
confianza **y justificación**»). `model_name` lo exige CT-32.

> El test [`test_solo_estan_los_campos_del_anexo_a`](../backend/apps/ai_client_app/tests/test_models.py)
> congela deliberadamente el conjunto actual de campos. La migración `0002` deberá
> actualizarlo en el mismo cambio: es el mecanismo previsto para que ningún campo entre sin
> decisión.

## D18 — Frontend

**S8 no adelanta la bandeja ni el detalle de operador del Sprint 9.**

El bloque visual «Clasificación asistida por IA» —sugerencia, confianza, justificación y
aviso «Requiere revisión humana»— se **difiere al Sprint 9**, junto con el detalle operativo
del reporte que lo aloja.

Motivo: ese bloque vive, según [`PLAN_DESARROLLO.md`](PLAN_DESARROLLO.md) §18, «en el detalle
del reporte visible para operadores y administradores», y ese detalle es entregable del
Sprint 9 (ruta `(operator)/bandeja`). Hoy `frontend/src/app/(operator)/` solo contiene un
layout. Construir media pantalla de operador dentro de S8 contaminaría el sprint siguiente.

S8 entrega: backend · persistencia · auditoría · API de operador/admin definida para S8 ·
tests · evaluación. El `AiClassificationAdmin` ya registrado
([`admin.py`](../backend/apps/ai_client_app/admin.py)) permite inspeccionar resultados
mientras tanto.

> **Esto no es deuda de S8.** Es alcance del Sprint 9, documentado aquí para que no se
> contabilice como incompleto al cerrar S8.

## D19 — Concordancia ≥ 85 % (CA-4)

CA-4 **permanece como requisito real del Sprint 8**. Estrategia de cumplimiento:

1. crear dentro de S8 un dataset etiquetado de **al menos 20 casos**;
2. construir un **runner determinista** de concordancia;
3. validar el runner con mock/fake **en CI**;
4. **no** usar el proveedor real en CI;
5. medir la concordancia real contra Gemini en un **gate externo separado**;
6. esa medición real es **necesaria antes del cierre definitivo del Sprint 8**.

> **Un mock que alcanza el 85 % no demuestra CA-4.** El mock valida la maquinaria de
> medición, no la capacidad del modelo. La medición con proveedor real queda **fuera de los
> tests de CI** por coste, cuota y no determinismo.

Relacionado: [`PROGRESO_SPRINTS.md`](PROGRESO_SPRINTS.md), «Concordancia IA vs etiquetas de
oro», y CT-32, que remite la documentación del modelo usado al informe de resultados.

## D20 — Configuración futura

Nombres de settings previstos. **Ninguno se implementa en S8-A**; se documentan para que
S8-B los cablee sin reabrir la discusión.

| Setting | Valor contractual | Notas |
| --- | --- | --- |
| `GEMINI_API_KEY` | — | secreto; solo variables de entorno del backend; ya en `.env.example` |
| `GEMINI_MODEL` | — | modelo por variable de entorno (CT-32 / O-05); ya en `.env.example` |
| `AI_CONFIDENCE_THRESHOLD` | `0.75` | D5 |
| `AI_MAX_RETRIES` | `3` | D9 |
| `AI_REQUEST_TIMEOUT_SECONDS` | `20` | timeout total de la petición |

Si el SDK exige más adelante separar *connect* y *read timeout*, S8-B lo resuelve
**conservando la intención total de 20 segundos**, sin reabrir este contrato.

---

## Resumen ejecutable

| Punto | Valor cerrado |
| --- | --- |
| Umbral de confianza | `0.75` |
| Objetivo de concordancia | `85 %` |
| Salida del modelo | slugs del catálogo cerrado |
| Valor de catálogo desconocido | revisión humana, nunca creación |
| ¿La IA puede rechazar? | **NO** |
| `CLASIFICADO` | resultado de IA persistido y auditable |
| Fallo del proveedor | el reporte permanece `CREADO` |
| UI de operador | Sprint 9 |
| Proveedor real en CI | **NO** |
| Medición real con Gemini | gate separado, antes de cerrar S8 |
| Versión del prompt | `s8-classification-v1` |
| Campos de la migración futura | `justification` + `model_name` |
| Columna `raw_response` | **NO** (va al `AuditLog`) |
