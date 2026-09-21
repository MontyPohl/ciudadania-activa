# S10 — Registro de despliegue y rollback

Este runbook registra el procedimiento controlado que desplegó S10 el 21/09/2026 y conserva la
estrategia de rollback para incidentes posteriores. No autoriza nuevas modificaciones de
producción: cualquier despliegue, migración, restauración o cambio de configuración requiere su
propio gate.

## Estado operativo verificado al cierre

- Candidato: `171f8c8b8f448a4092e73aeb5f67406e6b81ef09`.
- Integración de PR #39 en `main`: `7ae82b865d90c4cade02dd3378b44107f088b7dd`.
- Heroku: deploy `v13`, release vigente `v14`, `web=1`, `worker=1`, `beat=1`, todos `UP`.
- El gate posterior al merge no ejecutó un nuevo despliegue de Heroku; `v14` permaneció vigente.
- Configuración S10: `REPORT_AUTO_CLOSE_HOURS=72`; `BREVO_API_KEY=ABSENT`.
- Migraciones aplicadas: `reports_app.0003`–`0007` y `rewards_app.0002`–`0004`.
- Datos: 16 reportes en ciclo 0, 2 historiales en ciclo 0, 3 transacciones históricas con ciclo
  nulo, 9 auditorías y 16 geometrías; universo de IDs/geometrías sin cambios.
- Vercel: deployment `Production` del merge, SHA coincidente y estado `SUCCESS`; el metadato
  GitHub `production_environment=false` se aceptó como discrepancia de integración, sin modificarlo.

## 1. Artefacto y condiciones de entrada

- Baseline S9: `730d69ede7033c3487b68f79b1828fe6f2f43180`.
- Implementación y remediación auditadas hasta
  `0fff30c522cad6febddc14ac8154cf7c070745f9`, más la corrección obligatoria
  `7e31953f20943b2d2a79e9df41f74502a5267f78`. El candidato reconciliado y desplegado fue
  `171f8c8b8f448a4092e73aeb5f67406e6b81ef09`.
- ADR-0013 debe conservar el checksum registrado por el gate.
- CI backend, frontend y E2E debe estar verde sobre el artefacto exacto a desplegar.
- Debe existir un respaldo lógico reciente, legible con `pg_restore --list`, y un destino/proceso
  de restauración previamente comprobado.
- El comando de auditoría previa debe terminar con `READONLY_AUDIT_COMPLETE` y los conteos
  aprobados. Cualquier
  ambigüedad, evidencia histórica inesperada o diferencia de universo bloquea el despliegue.

## 2. Variables por proceso

No se incorporan secretos nuevos en S10.

| Proceso | Variables requeridas | Variables S10 opcionales |
|---|---|---|
| Backend web | `DATABASE_URL`, `REDIS_URL`, `DJANGO_SECRET_KEY`; además las credenciales ya existentes de HMAC/Storage para los endpoints que las usan | Ninguna |
| Celery worker | `DATABASE_URL`, `REDIS_URL`, `DJANGO_SECRET_KEY`; Brevo sólo para las tareas de correo ya existentes | `REPORT_AUTO_CLOSE_HOURS` (entero positivo; default `72`) |
| Celery Beat | `DATABASE_URL`, `REDIS_URL`, `DJANGO_SECRET_KEY` | `REPORT_AUTO_CLOSE_HOURS` puede estar presente por consistencia, aunque Beat sólo agenda |
| Frontend | `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | Ninguna nueva en S10 |

Si `REPORT_AUTO_CLOSE_HOURS` se sobrescribe, todos los workers que consuman `reports.auto_close`
deben recibir el mismo valor. Debe existir una sola instancia de Beat.

## 3. Orden exacto ejecutado

No se reactivaron writers entre migraciones. La secuencia efectiva fue:

1. Crear el backup previo en un archivo `.partial`.
2. Esperar al proceso real de `pg_dump`; exigir `exit=0`, `size > 0` y
   `pg_restore --list PASS`; renombrar sólo después de validar. El respaldo final fue
   `pre_s10_corrected_retry_20260921_030350.dump`, 328954 bytes, SHA-256
   `370392f09cafbe8971a181d729fb278ac4346e495d9b3210a304986f60f4940e`, 624 entradas TOC.
3. Abrir la ventana de mantenimiento.
4. Escalar `web=0`.
5. Escalar `worker=0`.
6. Escalar `beat=0` y comprobar directamente que no quedaban dynos persistentes.
7. Hacer push fast-forward del artefacto backend exacto al remote de Heroku.
8. Ejecutar, con el código nuevo y la base aún sin backfill, la auditoría sólo lectura:

   ```bash
   python manage.py audit_resolution_cycle_backfill \
     --web-writers-off \
     --worker-writers-off \
     --scheduled-writers-off
   ```

   El resultado obligatorio y observado fue `total=16`, `synthetic=14`, `cycle_0=2`,
   `cycle_1=0`, `unknown=0`, `inconsistent=0`, incluido `report.confirmed → CYCLE_0`.

9. Aplicar únicamente el backfill atómico:

   ```bash
   python manage.py migrate reports_app 0004_backfill_resolution_cycle --no-input
   ```

10. Verificar el checkpoint: 16 reportes sin nulos y en ciclo 0; 2 historiales sin nulos y en
    ciclo 0; 3 rewards, 9 auditorías, 16 geometrías y las mismas huellas de IDs/geometrías.
11. Aplicar, en orden y sin migraciones ajenas a S10, `reports_app.0005`, `0006`, `0007`,
    `rewards_app.0003` y `0004`; después ejecutar `showmigrations` y `check`.
12. Configurar únicamente `REPORT_AUTO_CLOSE_HOURS=72`. Mantener `BREVO_API_KEY` ausente.
13. Escalar `web=1`.
14. Escalar `worker=1` y confirmar Redis, proceso `ready` y tareas registradas.
15. Escalar exactamente `beat=1` y confirmar el scheduler `reports.auto_close` cada 15 minutos.
16. Ejecutar smoke tests de backend, frontend, worker, Beat y datos.

> En esta infraestructura, el push al remote de Heroku inicia el despliegue.

> El backup debe validarse después de que `pg_dump` haya terminado realmente. No validar el archivo
> mientras el proceso siga activo.

El primer archivo observado con 0 bytes estaba siendo escrito por una sesión `pg_dump` todavía
activa; no fue corrupción de PostgreSQL. La secuencia `.partial → esperar → exit=0 → size>0 →
pg_restore PASS → rename final` evita repetir esa validación prematura.

Como referencia, las verificaciones finales equivalentes son:

```bash
python manage.py showmigrations
python manage.py check
```

Heroku no tiene fase `release` ni predeploy automático. Si, después de los pasos explícitos, se
repite manualmente `migrate`, debe observar un no-op; esa comprobación nunca sustituye la secuencia
controlada anterior.

## 4. Verificación posterior

- No hay migraciones pendientes y los constraints/trigger de ciclo están presentes.
- Los 16 reportes conservan la clasificación aprobada
  `SYNTHETIC=14/CYCLE_0=2/CYCLE_1=0/UNKNOWN=0/INCONSISTENT=0` y todos quedan en
  `resolution_cycle=0`.
- No se crearon evidencias, bonus, reversals, auditorías o historiales espurios durante el backfill.
- Backend responde los códigos contractuales; las lecturas sensibles incluyen
  `Cache-Control: private, no-store`.
- El worker registra `reports.auto_close`, Beat es único y no hay cierres duplicados.
- Frontend puede leer `cycle`, subir evidencia con `Idempotency-Key`, refrescar ante
  conflicto `409` y mostrar auditoría paginada.
- Métricas y logs no contienen fotos, descripciones, tokens, object keys ni datos personales.

Resultado observado: backend health 200 con `db=true` y `redis=true`; rutas públicas,
autenticación y endpoints S10 con códigos contractuales; frontend 200 en `/`, `/login` y `/mapa`;
bundles apuntando a Heroku y cero referencias Railway. `production_data_universe_changed=NO`.

Pendientes deliberadamente no corregidos en este gate: `BREVO_API_KEY=ABSENT`, HSTS pendiente,
Redis TLS con `CERT_NONE` e IA intencionalmente deshabilitada.

## 5. Estrategia de rollback

### Antes del backfill

Mantener writers apagados, retirar el artefacto nuevo y volver al artefacto S9. Las columnas
aditivas anulables pueden permanecer: no es necesario borrarlas para recuperar servicio. Antes de
reactivar, comprobar que el código S9 ignora esas columnas y que ninguna migración de backfill fue
registrada.

### Después de aplicar sólo el esquema aditivo

La opción preferida es conservar las columnas vacías y volver al artefacto S9. No ejecutar una
migración destructiva durante el incidente. Documentar el punto alcanzado y reanudar con un
artefacto corregido en una nueva ventana.

### Después del backfill y antes de activar servicios

No borrar ni reescribir metadatos históricos automáticamente. Mantener writers apagados, capturar
los conteos y decidir entre un forward-fix o restaurar el respaldo completo en una operación
separada y expresamente autorizada. Una restauración sustituye toda la base y exige confirmar su
punto de recuperación.

### Después de activar S10 o de aceptar escrituras reales

No revertir migraciones, ciclos, evidencia, ledger, historial ni auditoría. Detener Beat, workers y
backend; activar mantenimiento; conservar toda evidencia; y desplegar un forward-fix. El frontend
puede volver temporalmente a una versión compatible sólo si el contrato backend sigue aceptando
sus lecturas. Restaurar un respaldo después de escrituras reales implica pérdida de datos y
requiere una autorización destructiva independiente.

## 6. Compatibilidad durante la ventana

- El backend S9 ignora las columnas aditivas, pero no debe aceptar escrituras durante el backfill:
  no asigna ciclos ni satisface las invariantes nuevas.
- El backend S10 no debe arrancar antes de finalizar las migraciones porque consulta las columnas
  y constraints nuevos.
- Los workers S9 deben estar detenidos antes de migrar; los workers S10 arrancan después del
  backend y de la validación del esquema.
- El frontend S10 se activa después del backend S10. El frontend anterior puede usarse sólo para
  rollback visual y nunca para eludir los contratos de ciclo del backend.
