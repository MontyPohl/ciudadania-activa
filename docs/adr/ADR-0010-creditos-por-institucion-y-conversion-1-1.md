# ADR-0010 — Créditos por institución y conversión 1:1

**Estado:** Aceptada
**Sprint:** 9
**Fecha:** 2026-09-02

## Contexto

El sistema reconoce una misma contribución ciudadana con dos magnitudes relacionadas,
pero de naturaleza distinta. Los puntos de impacto expresan participación global y
alimentan el reconocimiento público; los créditos permiten canjear recompensas ofrecidas
por una institución concreta. El ledger debe conservar esa distinción, acreditar los
movimientos de forma trazable y mantener una historia económica estable aunque un reporte
se reasigne o su validación se revierta posteriormente.

## Decisión

### 1. Puntos globales y créditos institucionales

Los puntos de impacto son globales y no consumibles. Los créditos son institucionales y
consumibles: solo integran el saldo de la institución guardada en cada movimiento. Los
créditos acreditados no vencen.

Cada movimiento positivo convierte **1 punto de impacto en 1 crédito institucional**. Por
eso sus deltas positivos son iguales: `REPORT_CREATED` aporta 20/20,
`CONFIRMATION_CREATED` 10/10 y `VALIDATION_BONUS` 30/30.

### 2. La institución validante determina los créditos

La institución de los créditos es la institución que realiza la validación humana del
reporte. En la misma transacción, los movimientos de creación y confirmación que estaban
`PENDIENTE` pasan a `ACREDITADO` y reciben esa institución y un `credited_at` común. No se
duplican ni se reescriben sus importes.

La bonificación por validación se registra como una fila separada
`VALIDATION_BONUS`, con +30/+30, estado `ACREDITADO`, el reporte y la institución
validante. Un reporte sin autor no genera esta bonificación, aunque sí permite acreditar
las confirmaciones válidas.

Una confirmación creada después de la validación nace acreditada para la institución
definitiva del reporte y usa como `credited_at` su propio instante de creación, no el
instante histórico de validación.

### 3. La atribución histórica queda congelada

Una vez acreditado, cada movimiento conserva la institución que validó la contribución.
Una reasignación futura del reporte no traslada créditos entre instituciones ni modifica
movimientos históricos. La institución almacenada en el ledger es parte de la evidencia
económica de la validación, no una proyección del routing actual del reporte.

### 4. Los saldos siempre se derivan

No existen contadores mutables de puntos o créditos. Los saldos se calculan agregando las
filas pertinentes del ledger, de acuerdo con su estado e institución, como establece
ADR-0004. Consumir créditos genera un movimiento económico; no reduce un contador
separado ni consume los puntos globales.

### 5. Reversión posterior

La reversión de una validación pertenece al Sprint 10. Se representará mediante los
movimientos compensatorios ya previstos en el diseño del ledger, sin borrar ni trasladar
la atribución histórica. Si hubo un canje anterior, la reversión puede dejar negativo el
saldo institucional; el vale emitido se conserva. La política operativa detallada de
reversión corresponde a ADR-0013 cuando esa decisión se formalice.

## Alternativas consideradas

| Alternativa | Motivo de descarte |
|---|---|
| Un saldo global único para créditos | Impediría atribuir y consumir recompensas dentro de la institución que financia el incentivo |
| Convertir puntos a créditos durante el canje | Perdería la institución validante y rompería la conversión auditable en el momento de acreditación |
| Duplicar los movimientos pendientes al validar | Contaría dos veces la contribución y separaría artificialmente origen y acreditación |
| Trasladar créditos cuando se reasigna el reporte | Reescribiría la historia económica y haría depender saldos pasados del routing presente |
| Guardar contadores de saldo | Introduciría una segunda fuente de verdad susceptible de divergir del ledger |

## Consecuencias

- Los puntos acumulados por una persona atraviesan instituciones y no se gastan; los
  créditos se consultan y consumen por institución.
- La acreditación es síncrona y atómica con la validación del reporte. Si falla el ledger,
  no quedan estado, historial o auditoría parcialmente persistidos.
- Los deltas originales permanecen inmutables; la acreditación modifica únicamente
  estado, institución y marca temporal.
- Consultar saldos requiere agregaciones sobre el ledger, mitigadas por sus índices.
- Las reasignaciones y reversiones posteriores deben respetar la institución histórica de
  cada movimiento.

## Referencias

ADR-0004 (ledger auditable e importes inmutables) · ADR-0009 (escrituras económicas
síncronas) · ADR-0013 (política de reversión, cuando corresponda) ·
`PLAN_DESARROLLO.md` §6.3, §6.5 y Sprint 9 · RF-35, RF-38, RF-40 y RF-50 · D-01
