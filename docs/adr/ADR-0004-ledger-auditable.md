# ADR-0004 — Ledger auditable con importes inmutables

**Estado:** Aceptada
**Sprint:** 2 (estructura) · se aplica en los Sprints 6 a 12
**Fecha:** 2026-08-20

## Contexto

La tesis exige poder reconstruir el 100 % de los movimientos de puntos y créditos
(Tabla 6, RF-50) y prohíbe explícitamente el patrón `user.points += 50`: todo saldo
debe ser derivado. El módulo de incentivos, además, admite que una validación
institucional se revierta más tarde y que el saldo institucional resultante quede
negativo (D-01).

Un diseño con contadores mutables por usuario e institución sería más simple de
consultar, pero destruiría la trazabilidad: al sumar y restar sobre una misma celda no
queda rastro de qué acción produjo cada variación, y una reversión posterior sería
indistinguible de una corrección manual.

También se consideró —y se descartó— reescribir los importes durante la acreditación:
crear la fila `REPORT_CREATED` con `credit_delta = 0` y actualizarla a 20 al validar. Eso
convertiría al ledger en un registro mutable **en su contenido económico**, que es
exactamente lo que la tesis pide evitar.

## Decisión

**1. Los saldos no se almacenan.** No existe ninguna tabla de balances, contadores por
usuario o institución, ni ranking materializado. Toda magnitud se obtiene por agregación
sobre `reward_transactions` filtrando por `status`.

**2. Los importes son inmutables.** `impact_delta` y `credit_delta` se fijan al insertar
la fila y no se modifican nunca. `REPORT_CREATED` nace con 20/20 y
`CONFIRMATION_CREATED` con 10/10, ambos en `PENDIENTE`. La conversión 1:1 entre punto
acreditado y crédito institucional queda determinada en el origen.

**3. Lo que cambia es el estado, la institución y las marcas temporales.** Al acreditar,
la fila fija `status`, `institution_id`, `credited_at` y, desde S10, la metadata
`resolution_cycle` (ADR-0013), sin modificar importes. Mientras una fila está
`PENDIENTE` no participa de ningún saldo, porque todas las fórmulas filtran
`status = 'ACREDITADO'`: los 20 puntos existen como valor potencial de la contribución,
no como saldo utilizable.

**4. Cuatro estados, no tres.** `PENDIENTE` · `ACREDITADO` · `ANULADO` · `REVERTIDO`.
`ANULADO` describe la transacción que estuvo pendiente y nunca se acreditó porque el
reporte fue rechazado; `REVERTIDO`, la que sí estuvo acreditada y luego se revirtió. La
distinción permite auditar por separado las pérdidas por rechazo y por reversión.

**5. Las tres marcas temporales existen desde la migración inicial.** `credited_at`,
`voided_at` y `reversed_at` se crean con la tabla, no se agregan después. Un
`CheckConstraint` garantiza su coherencia con el estado:

| `status` | `credited_at` | `voided_at` | `reversed_at` |
|---|---|---|---|
| `PENDIENTE` | NULL | NULL | NULL |
| `ACREDITADO` | NOT NULL | NULL | NULL |
| `ANULADO` | NULL | NOT NULL | NULL |
| `REVERTIDO` | indiferente | NULL | NOT NULL |

`REVERTIDO` deja `credited_at` libre a propósito: la fila original acreditada debe
conservarlo como rastro, y una fila `REVERSAL` nace `REVERTIDO` sin haber estado nunca
acreditada.

**6. La reversión no compensa dos veces.** Al revertir, las filas `ACREDITADO` pasan a
`REVERTIDO` y se inserta una fila `REVERSAL` de importes negativos con
`related_transaction_id` apuntando a la original. El efecto económico real proviene de
que las originales **dejan de estar `ACREDITADO`**; la fila negativa nace ya `REVERTIDO`
y por tanto queda fuera de toda suma. Es rastro de auditoría, no un segundo descuento.

**7. Dos invariantes se garantizan en base de datos** mediante índices únicos parciales:
un solo `REPORT_CREATED` por reporte y un solo `CONFIRMATION_CREATED` por confirmación.
Desde S10 se complementan con un bonus de cada tipo por `(report, resolution_cycle)`
y una `REVERSAL` por transacción original. Revalidar tras una reversión genera un ciclo
nuevo, sin reactivar los anteriores. S12 añade una única fila `REDEMPTION` por vale y
CHECKs de forma/enlace. Ver [diccionario](../DICCIONARIO_DATOS.md).

**8. Todas las claves foráneas del ledger son `PROTECT`.** Un movimiento económico no
puede perder su sujeto, su origen ni su institución: borrar un usuario o un reporte con
historia económica está bloqueado por la base de datos.

**9. Las escrituras son síncronas y transaccionales.** `reports_app` invoca las funciones
de dominio de `rewards_app.services.ledger` dentro de su propia `transaction.atomic()`.
**Celery no registra ningún movimiento económico**: una falla del worker dejaría reportes
validados sin puntos acreditados y rompería el criterio de trazabilidad (ver ADR-0009).

## Alternativas consideradas

| Alternativa | Motivo del descarte |
|---|---|
| Contadores mutables por usuario e institución | Destruye la trazabilidad exigida por RF-50 y hace indistinguible una reversión de una corrección manual |
| Reescribir `credit_delta` al acreditar | Convierte el ledger en mutable en su contenido económico; una auditoría posterior no podría releer el importe original |
| Tabla de saldos con bloqueo de fila para el canje | No hay “fila de saldo” que bloquear si el saldo es derivado; el canje se serializa bloqueando el `User` (C-04) |
| Impedir la reversión si los créditos ya se canjearon | Contradice D-01: la reversión siempre puede ejecutarse y el saldo resultante puede ser negativo |
| Registrar los movimientos por evento asíncrono | Rompe la atomicidad respecto de la validación institucional (O-09, ADR-0009) |
| `CHECK` que ligue `status` con `event_type` | Sin evidencia documental que lo respalde; sobre-restringiría los servicios de S9-S12 |

## Consecuencias

- Los saldos exigen agregaciones; se mitigan con los índices
  `(user_id, institution_id, status, created_at)` y `(status, credited_at)` y con caché en
  Redis para el ranking y el mapa (ADR-0003).
- El saldo institucional puede resultar **negativo** tras revertir créditos ya canjeados.
  Es el comportamiento correcto y deliberado (D-01): la interfaz lo muestra explicado, sin
  penalización adicional, y el vale emitido conserva su validez.
- Cualquier servicio que inserte una fila `ACREDITADO` de nacimiento —`VALIDATION_BONUS`,
  `RESOLUTION_BONUS`, `REDEMPTION`— **debe** fijar `credited_at` en el mismo `INSERT`, o el
  `CheckConstraint` rechazará la operación. No es un efecto colateral: sin ese valor la
  fila quedaría fuera del Top 10 del mes, que imputa el mes por `credited_at` (O-08).
- El admin de Django expone el ledger y los canjes en **solo lectura** (sin alta, cambio ni
  borrado): un formulario editable permitiría violar la semántica que este ADR establece.
- El Sprint 2 entrega la estructura y el contrato
  (`rewards_app.services.ledger` con sus cinco funciones declaradas y sin implementar); las
  reglas económicas se conectan en los Sprints 6, 7, 9, 10 y 12.

## Referencias

`PLAN_DESARROLLO.md` §6.3, §6.5, §8, §30 · correcciones C-01, C-07, C-08, C-09 ·
decisiones 16 y 19 · D-01 · `decisiones-incentivos.md` · Anexo A.1/A.2/A.4/A.6 ·
filas CT-06, CT-07, CT-09, CT-10 de `CAMBIOS_TESIS_PENDIENTES.md` ·
[DICCIONARIO_DATOS.md](../DICCIONARIO_DATOS.md)
