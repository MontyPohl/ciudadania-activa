# S10 — Ventana de mantenimiento para `resolution_cycle`

Esta operación se ejecuta una sola vez al desplegar las migraciones iniciales de S10.
No crea historia, auditoría, evidencia ni transacciones: únicamente agrega metadata de ciclo a
las 16 filas históricas autorizadas.

## Precondición obligatoria

Antes de ejecutar el comando o `migrate`, detener todos los procesos capaces de escribir:

```text
web writers: OFF
worker writers: OFF
scheduled writers: OFF
```

No basta con activar modo mantenimiento en el frontend. Deben detenerse las instancias web, los
workers y cualquier scheduler/Beat en la plataforma. Mantener una única consola operacional con
acceso a PostgreSQL.

## Auditoría de solo lectura

Con los escritores detenidos y antes de aplicar migraciones:

```bash
python manage.py audit_resolution_cycle_backfill \
  --web-writers-off \
  --worker-writers-off \
  --scheduled-writers-off
```

El comando imprime la identificación completa por clase y un SHA-256 determinista del universo.
El resultado autorizado es exactamente:

```text
total_reports = 16
synthetic = 14
cycle_0 = 2
cycle_1 = 0
unknown = 0
inconsistent = 0
```

Guardar la salida en el registro operacional. Si el conteo, los UUID identificados o cualquier
clase difieren, no aplicar la migración y registrar `ABORT`. No corregir datos silenciosamente.

### Acciones de auditoría compatibles con el ciclo 0

El clasificador exige que todas las entradas de auditoría del reporte pertenezcan a:

```text
report.created · ai.report.classified · report.rejected · report.confirmed
```

**`report.confirmed` es compatible con el ciclo 0.** Una confirmación ciudadana no cambia
`report.status`, no escribe `resolution_cycle`, no crea historial y no guarda el reporte: solo la
validación abre ciclo (`services/validation.py`), y el trigger de `0005` impide que otra operación
lo avance. Un reporte confirmado sin validar sigue en `CREADO`, que es ciclo 0 por el `default=0`
del modelo.

Permitirla no puede ocultar un reporte validado: `validated_at is not None` clasifica como
`CYCLE_1` antes de llegar a este filtro.

Las acciones que exigen un ciclo `>= 1` siguen produciendo `UNKNOWN` en el ciclo 0:
`report.validated`, `report.validation_reverted`, `report.resolved`, `report.closed` y
`report.resolution_evidence_created`.

Los conteos autorizados **no cambian** por esto: siguen siendo 16 / 14 / 2 / 0 / 0 / 0.

## Migración

Aplicar con los escritores todavía detenidos:

```bash
python manage.py migrate --no-input
```

La migración vuelve a clasificar el universo completo dentro de una transacción y no escribe hasta
haber validado las 16 filas. Una base nueva sin reportes también es válida. Cualquier otro universo,
fila desconocida o inconsistencia aborta la operación completa.

El clasificador compartido de migración y preflight vive en
`apps/reports_app/services/resolution_cycle_backfill.py`. Es parte inmutable del contrato de esta
migración histórica: no debe modificarse, moverse ni eliminarse después de integrar S10 sin crear
antes una migración correctiva y volver a verificar bases nuevas desde cero.

## Validación posterior

Comprobar antes de reactivar procesos:

```sql
SELECT resolution_cycle, count(*)
FROM reports
GROUP BY resolution_cycle
ORDER BY resolution_cycle;
```

El resultado histórico esperado es `cycle 0 = 16`, `cycle 1 = 0`. Verificar además que no se hayan
creado filas nuevas en `report_status_history`, `resolution_evidences`, `reward_transactions` ni
`audit_logs`. Después de registrar la evidencia, reactivar primero web, luego workers y por último
los schedulers.
