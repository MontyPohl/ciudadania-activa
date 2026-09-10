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
class_a = 2
class_b = 14
unknown = 0
inconsistent = 0
```

Guardar la salida en el registro operacional. Si el conteo, los UUID identificados o cualquier
clase difieren, no aplicar la migración y registrar `ABORT`. No corregir datos silenciosamente.

## Migración

Aplicar con los escritores todavía detenidos:

```bash
python manage.py migrate --no-input
```

La migración vuelve a clasificar el universo completo dentro de una transacción y no escribe hasta
haber validado las 16 filas. Una base nueva sin reportes también es válida. Cualquier otro universo,
fila desconocida o inconsistencia aborta la operación completa.

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
