# S10 — Plan de despliegue y rollback

Este runbook prepara la continuación controlada del despliegue de S10, pero **no la autoriza ni la
ejecuta**. No deben producirse nuevas modificaciones de producción hasta recibir una autorización
explícita posterior al release gate.

## Estado operativo previo a la próxima ventana

- La aplicación productiva fue revertida al baseline S9 mediante Heroku rollback.
- Permanecen aplicadas únicamente las migraciones aditivas
  `reports_app.0003_add_resolution_cycle_columns` y
  `rewards_app.0002_add_resolution_cycle_column`.
- El backfill `reports_app.0004` y todas las migraciones S10 posteriores no se ejecutaron.
- Las columnas `resolution_cycle` permanecen vacías; no se modificaron datos de negocio ni se
  restauró el respaldo.
- La próxima ventana debe repetir la auditoría de solo lectura con el candidato corregido que
  admite `report.confirmed` en ciclo 0. Las dos migraciones ya aplicadas se verifican, no se
  revierten ni se reaplican de forma destructiva.

## 1. Artefacto y condiciones de entrada

- Baseline S9: `730d69ede7033c3487b68f79b1828fe6f2f43180`.
- Implementación y remediación auditadas hasta
  `0fff30c522cad6febddc14ac8154cf7c070745f9`, más la corrección obligatoria
  `7e31953f20943b2d2a79e9df41f74502a5267f78`; registrar el SHA exacto del candidato reconciliado
  del PR #39 antes de cualquier despliegue.
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

## 3. Orden exacto de despliegue

Todos los pasos requieren una ventana de mantenimiento. No se reactivan writers entre migraciones.

1. Congelar el artefacto autorizado y registrar su SHA. Pausar despliegues automáticos.
2. Activar mantenimiento y detener **todos** los writers: backend web, workers Celery, Beat y
   cualquier comando o integración capaz de escribir en la base.
3. Crear y validar el respaldo lógico previo. No continuar si el respaldo no es legible.
4. Ejecutar, con el código nuevo y contra la base aún no migrada, la auditoría solo lectura:

   ```bash
   python manage.py audit_resolution_cycle_backfill \
     --web-writers-off \
     --worker-writers-off \
     --scheduled-writers-off
   ```

5. Confirmar con `showmigrations` que las dos migraciones aditivas ya aplicadas siguen registradas:

   ```bash
   python manage.py showmigrations reports_app rewards_app
   ```

   Deben aparecer aplicadas `reports_app.0003_add_resolution_cycle_columns` y
   `rewards_app.0002_add_resolution_cycle_column`, mientras `reports_app.0004` permanece sin
   aplicar. Cualquier diferencia bloquea la continuación; no revertir ni reaplicar columnas como
   respuesta automática.

6. Ejecutar el backfill atómico:

   ```bash
   python manage.py migrate reports_app 0004_backfill_resolution_cycle --no-input
   ```

7. Validar en modo solo lectura que el universo completo conserva exactamente 16 reportes, todos
   en ciclo 0 (2 históricos reales y 14 snapshots sintéticos); verificar además historial y ledger
   por ciclo. Si algo difiere,
   mantener writers apagados y aplicar el rollback de la sección 5.
8. Aplicar restricciones y migraciones restantes:

   ```bash
   python manage.py migrate --no-input
   python manage.py showmigrations
   python manage.py check
   ```

9. Arrancar el backend web nuevo todavía bajo mantenimiento. Comprobar health, versión y lecturas
   autenticadas sin ejecutar mutaciones de negocio.
10. Arrancar los workers Celery nuevos y verificar que `reports.auto_close` esté registrado. Luego
    arrancar **una sola** instancia de Beat y confirmar que publica la tarea con la periodicidad
    configurada.
11. Activar el frontend nuevo y ejecutar smoke tests de autenticación, vistas ciudadana,
    institucional y administrativa, sin usar datos personales reales como evidencia.
12. Ejecutar las verificaciones posteriores de la sección 4. Sólo entonces retirar mantenimiento y
    restaurar el tráfico de writers.

El predeploy automático que ejecute `migrate` después de los pasos explícitos debe observar un
no-op. No se permite que ese hook sustituya la secuencia controlada anterior.

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
