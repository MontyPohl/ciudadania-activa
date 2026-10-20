# ADR-0009 — Clasificación IA asíncrona mediante Celery

> **Actualización 20/10/2026.** La tarea y persistencia descritas aquí ya están implementadas;
> la IA está activa en Heroku v22 con OpenAI y prompt v2. La selección por
> `AI_PROMPT_VERSION` (default v1) y los tokens de `ai_client_app.0003` se detallan en
> [ADR-0016](ADR-0016-proveedor-de-ia-openai.md) y el [contrato](../AI_CLASSIFICATION_CONTRACT.md).
> Se conserva la política de estados, locks y reintentos. Los contadores guardados no suman
> intentos descartados ni reintentos y no constituyen un registro de facturación completo.

**Estado:** Aceptada
**Sprint:** 8
**Fecha:** 2026-08-29

## Contexto

El §6.3 del plan fija la decisión arquitectónica más importante del proyecto: qué es
asíncrono y qué no. Toda escritura sobre el ledger de incentivos es síncrona y
transaccional; la clasificación por IA es lo único que se encola en el flujo de creación de
un reporte.

El motivo es concreto. La creación de un `Report` escribe, en una sola transacción, el
reporte, su media, el asiento inicial de historial, la transacción `REPORT_CREATED` de 20
puntos pendientes y el `AuditLog`. Delegar cualquiera de esas piezas a una cola dejaría
reportes sin puntos ante una caída del worker. La clasificación, en cambio, es una
sugerencia: puede llegar tarde, o no llegar, sin romper nada.

El andamiaje ya existe desde el Sprint 6. `create_report` registra el encolado con
`transaction.on_commit(..., robust=True)`, de modo que la tarea se publica después del
commit y un fallo del broker no revierte el reporte. La tarea `ai.classify_report` está
registrada y es no-op. Hay tests que verifican que un fallo del broker no revierte la
creación ni pierde los 20 puntos pendientes.

Lo que falta decidir es qué ocurre cuando esa tarea deje de ser no-op: qué garantiza, qué
no garantiza, y en qué estado queda un reporte en cada modo de fallo.

## Decisión

### 1. La creación es síncrona; la clasificación, post-commit

La creación del `Report` permanece síncrona y transaccional. La clasificación se ejecuta en
un worker de Celery, disparada por un evento —la creación— y no por un reloj. No se
introduce Celery Beat para esto.

El encolado sigue ocurriendo en `on_commit` con `robust=True`: la tarea nunca ve un reporte
que no esté confirmado en base de datos, y un broker caído no puede revertir una
transacción ya comprometida.

### 2. Un fallo de IA nunca revierte la creación

Ningún modo de fallo de la clasificación —broker, worker, proveedor, parser— revierte el
`Report`, su media, su historial ni su transacción de 20 puntos pendientes. Es CA-5, y ya
está cubierto por tests para el caso del broker.

### 3. Cinco situaciones distintas, cinco desenlaces distintos

Confundirlas es el error que este ADR quiere evitar: **tres son fallos y dos no lo son**.

| Situación | ¿Es un fallo técnico? | Estado del reporte | Sugerencia aplicada |
| --- | --- | --- | --- |
| Fallo del broker al encolar | Sí | `CREADO` | No |
| Fallo del proveedor tras agotar reintentos | Sí | `CREADO` | No |
| Salida inválida: JSON, esquema o campo desconocido | Sí, del proveedor | `CLASIFICADO` | No · revisión humana |
| Baja confianza (`< 0.75`) | **No** | `CLASIFICADO` | No · revisión humana |
| Inconsistencia (`inconsistency == true`) | **No** | `CLASIFICADO` | No · revisión humana |

Una salida inválida **sí** produce una fila de `AiClassification` con valores nulos,
`inconsistency_flag = true` y una justificación redactada por el backend: hubo respuesta y
queda auditada. Un proveedor que no responde no produce ninguna: no hubo resultado.

### 4. `CLASIFICADO` significa «hay resultado persistido»

El reporte avanza a `CLASIFICADO` cuando existe un resultado persistido y auditable, se
aplique o no la sugerencia. Baja confianza e inconsistencia son desenlaces legítimos de una
clasificación, no errores.

Si el proveedor no llegó a responder, el reporte permanece `CREADO`: no hay nada que
clasificar y el operador lo verá como pendiente de clasificación, que es exactamente lo que
es.

### 5. Reintentos acotados solo para errores transitorios

Timeout, `429`, `5xx` y errores temporales de red se reintentan con backoff exponencial y
jitter, con un máximo de 3. Credencial inválida, configuración inválida, esquema inválido y
mapeo inválido **no** se reintentan: el resultado no cambiaría y consumiría cuota.

Nunca hay reintento sin límite. Es la diferencia con `notifications_app`, que no reintenta
en absoluto: allí el fallo queda en un estado consultable; aquí un timeout aislado sí
merece un segundo intento, pero acotado.

### 6. Idempotencia obligatoria

Celery no garantiza entrega única. La tarea inicial debe ser idempotente: `transaction.atomic`,
`select_for_update` sobre el `Report` y una guarda explícita de estado antes de aplicar
cualquier cambio.

Una segunda entrega accidental no puede aplicar la sugerencia dos veces, cambiar el estado
otra vez, duplicar el historial ni duplicar el `AuditLog` de decisión aplicada.

El reproceso **manual** es una operación distinta y sigue permitido: por eso
`ai_classifications` no lleva unicidad por reporte y no se le añade.

### 7. La IA nunca produce `RECHAZADO`

Ninguna ruta de esta tarea —éxito, baja confianza, inconsistencia, salida inválida o fallo
del proveedor— puede llevar un reporte a `RECHAZADO`. Es una transición humana.

## Alternativas consideradas

| Alternativa | Motivo de descarte |
| ------------------------------------------ | ------------------------------------------------------------------------------------- |
| Clasificar de forma síncrona en el POST | Ata la latencia del ciudadano a un proveedor externo y rompe CA-1 y CA-5 |
| Encolar dentro de la transacción | La tarea podría ejecutarse antes del commit y no encontrar el reporte |
| Reintentos ilimitados | Consume cuota indefinidamente ante un error no transitorio y oculta el fallo real |
| No reintentar nunca, como en `notifications_app` | Un timeout aislado desperdiciaría una clasificación recuperable |
| Marcar `CLASIFICADO` aunque el proveedor no responda | Mentiría sobre el estado: no existe clasificación que consultar |
| Dejar el reporte en un estado de error propio | Añade un estado al enum del Anexo A sin que ninguna fuente lo exija; `CREADO` ya expresa «sin clasificar» |
| Confiar en la entrega única de Celery | No la garantiza; produciría dobles aplicaciones y duplicados de historial |

## Consecuencias

- La respuesta HTTP de creación no depende del proveedor (CA-1).
- Un proveedor caído degrada la funcionalidad sin romperla: los reportes se acumulan en
  `CREADO` y el operador clasifica manualmente.
- Un reporte puede quedar indefinidamente en `CREADO` si el broker falla en el momento del
  encolado. No hay barrido de reparación, y `on_commit(robust=True)` solo deja un warning.
  Es una limitación conocida de este diseño; un reproceso manual (S8-D) es la vía de
  recuperación prevista.
- La idempotencia exige bloquear la fila del `Report` en cada clasificación, con un coste
  de contención aceptable para el volumen del prototipo.
- Los tests corren con `CELERY_TASK_ALWAYS_EAGER`, que no ejercita redelivery ni
  concurrencia real: las guardas de idempotencia deben probarse invocando la tarea dos
  veces de forma explícita.
