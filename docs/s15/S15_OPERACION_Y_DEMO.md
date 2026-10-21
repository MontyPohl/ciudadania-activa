# S15 — preparación operativa y demostración

**Fecha:** 20/10/2026. Este procedimiento prepara las comprobaciones restantes del
despliegue existente y la entrega a S16. No registra como ejecutadas rotaciones,
restauraciones remotas, login Google, correo real ni carga de fotografías inéditas.
La defensa se prevé aproximadamente para el **16/12/2026**; no es necesario esperar
a esa fecha para obtener resultados técnicos.

## Comprobaciones en el despliegue existente

El [diagnóstico Heroku ↔ Supabase](S15_DIAGNOSTICO_REMOTO.md) ya observó consultas
constantes de solo lectura. Las pruebas funcionales con escritura necesitan el
respaldo y las cuentas siguientes. Se utilizará el entorno actual, según la decisión
del usuario, sin exigir contratar otro.

1. Identificar versión de frontend/backend, migraciones, procesos activos, mes local
   y datos que se conservarán. Seleccionar cuentas de ensayo de admin, Municipalidad,
   ANDE, ESSAP y dos ciudadanos verificados distintos. Registrar identificadores en
   un archivo privado; nunca contraseñas, tokens o cédulas en el informe público.
2. Antes de las escrituras, respaldar coordinadamente DB y objetos privados/públicos.
   Conservar dump completo, listado de objetos, bytes y hashes; comprobar lectura del
   dump con `pg_restore --list` y correspondencia de objetos. El ensayo local de
   restauración no sustituye este respaldo remoto. El listado del dump, por sí solo,
   tampoco acredita una restauración.
3. Fijar una ventana breve con el usuario cuando las cuentas y el respaldo estén
   preparados. Enumerar los reportes/archivos de ensayo con prefijo identificable,
   cantidad máxima y recorrido; no realizar un reset global ni vaciar Redis remoto.
   La prueba consiste en pocos casos seriales, no en ejecutar k6 contra producción.
4. Recorrer Google → sesión del BFF → creación → consulta pública → confirmación
   por otro ciudadano → validación institucional → evidencia → resolución → saldo
   y ranking. Registrar estado/código/eventos; comprobar permisos con la institución
   ajena y ciudadano sin privilegios. No reutilizar un caso ya canjeado como si
   todavía tuviera su saldo previo.
5. Comprobar un correo real con su notificación correlacionada, sus intentos y la
   observación de recepción del destinatario. `ENVIADO` y aceptación HTTP del proveedor
   no demuestran por sí solos llegada a la bandeja. La IA real se prueba después del
   piloto presupuestado; la foto reservada de evaluación no se consume como ensayo.
6. Comparar API y persistencia del recorrido, incluyendo ledger, estado/ciclo,
   evidencia y avisos. Conservar fallos y reintentos como tales. Publicar solo un
   recibo minimizado; vídeos, fotos, dump y sesiones permanecen privados.
7. Comprobar salud y acceso tras el ensayo. Conservar o retirar únicamente los casos
   identificados conforme al plan de ventana. No usar la restauración completa como
   mecanismo habitual de limpieza: sobrescribiría cualquier cambio ajeno posterior
   al respaldo. Si hubiera una incidencia, evaluar primero su alcance y el punto de
   recuperación con el usuario.

## Retiro de foto y evidencia públicas

Si un reporte ya validado requiere retirar sus medios públicos:

1. Un **admin** ejecuta `POST /api/v1/admin/reports/{id}/revert-validation/`, con
   `cycle` vigente y `reason` que explique el retiro. Esto devuelve el reporte a
   `CLASIFICADO`, revierte los créditos del ciclo y registra auditoría. Desde ese
   momento los proxies dejan de servir foto y evidencia: verifican el estado en cada
   lectura, aunque un listado cacheado conserve las rutas.
2. Comprobar que las rutas públicas de foto y evidencia responden `404`. Después,
   la institución o el admin pueden rechazar el reporte si corresponde.

La institución sola no puede retirar medios de un reporte ya validado: el rechazo
sin reversión previa falla con `VALIDATION_REVERSAL_REQUIRED`. La foto solo se
publica desde `ASIGNADO` y la evidencia en `RESUELTO`/`CERRADO`.

## Secretos pendientes: orden y consumidores

La decisión [S14-D19](../s14/S14_DECISIONES.md) y el
[runbook vigente, §§12–13](../runbooks/HEROKU_MIGRATION.md#12-rotación-de-secretos)
siguen siendo las fuentes del estado. No se leyeron valores para preparar esta tabla
ni se presume que hayan sido invalidados en los proveedores.

| Elemento | Acción preparada y consumidor | Comprobación posterior |
|---|---|---|
| `SUPABASE_SERVICE_ROLE_KEY` | Rotar en la consola Supabase y actualizar el consumidor backend/worker en Heroku; comprobar otros consumidores antes de invalidar la anterior | Subida, firma, lectura autorizada y denegación de acceso ajeno a objetos de ensayo |
| `DATABASE_URL` | Rotar la contraseña en Supabase y actualizar conexión de web, workers y herramientas operativas que la usen; preparar recuperación si falla el cambio | Health DB, consulta autenticada y recorrido corto sin error de conexión |
| `GOOGLE_OAUTH_CLIENT_SECRET` heredado | Invalidar en GCP; el flujo activo no lo consume | Google login vigente sigue funcionando; no publicar el valor anterior |
| `EMAIL_HOST_PASSWORD` heredado | Invalidar la credencial SMTP heredada en Brevo; la integración activa utiliza su API | Correo por la API activa y evidencia de recepción |
| `TELEGRAM_BOT_TOKEN` heredado | Invalidar en su proveedor; Telegram está fuera del MVP | Constancia de invalidación sin reintroducir integración |
| `GEMINI_API_KEY` heredada | Invalidar en Google AI Studio; el proveedor activo es OpenAI | La selección activa no depende de esa clave |
| `DOCUMENT_HMAC_SECRET` | Conservar mientras se mantienen identidades existentes. Una rotación requiere recrearlas/reverificarlas o implementar una transición específica | No cambiar como si fuera una contraseña ordinaria: un secreto distinto rompe la comparación de HMAC existentes |
| `S11_CUSTODY_HMAC_KEY` | Fuera de este cambio: pertenece a la custodia histórica S11 | GOV-T054 conserva su alcance y sus archivos congelados |

Las acciones de consola asignadas al usuario en D19 permanecen pendientes. Su ejecución
debe registrar fecha, consumidor actualizado, resultado y revocación de la anterior,
sin conservar valores en tickets o logs. Ante un fallo de actualización, recuperar el
consumidor con una credencial válida; no asumir que una clave revocada puede reactivarse.
No se cambia de modelo ni se consume presupuesto IA para verificar una rotación de DB.

## Guion técnico para S16 y diciembre

Preparar un guion corto con ciudadano autor, confirmante, operador y admin: mapa,
alta, confirmación, validación, evidencia/resolución, movimientos, ranking y vale
simulado. La [pasada API/SQL](S15_API_SQL_Y_FLUJOS.md) y la auditoría
[UX](S15_AUDITORIA_UX.md) son evidencia técnica previa; no reemplazan el ensayo
con las cuentas de la defensa ni las integraciones reales pendientes.

El snapshot de octubre contiene fechas y canjes mensuales de octubre. Para diciembre,
conservarlo como evidencia histórica y generar otra versión anclada en diciembre,
con manifiesto y hashes propios; verificar mes, ranking, cupo, saldo previo al canje,
medios y sesiones renovadas. No corregir timestamps o créditos directamente en SQL
para fabricar un preestado. Reservar una copia sin consumir para repetir la demo.

Preparar además captura/vídeo privado de un ensayo correcto y recibos de evidencia
para explicar una indisponibilidad externa. Ese respaldo audiovisual se identifica
como grabación; no se presenta como ejecución en vivo ni sustituye los datos de prueba.
El piloto y las pruebas personales comparten el presupuesto total de USD 5–10 y deben
conservar una reserva para la demostración.

Al cerrar S15, entregar a S16 resultados, límites, riesgos abiertos, índice de hashes,
procedimiento de reproducción/restauración y guion. La actualización consolidada de
la tesis `.docx` se hará **después de terminar los 16 sprints**, según instrucción del
usuario. Se necesita conocer aparte la fecha límite de entrega del documento para
reservar tiempo de redacción y revisión antes de la defensa.


## Comprobación adicional antes de publicar runtime

El [corte de validación](S15_INDICE_VALIDACION.md) preserva un timeout Gunicorn
posterior a las ventanas medidas. Su causa no se estableció. Antes de desplegar el
ajuste gthread/4, comprobar estabilidad bajo períodos prolongados y tratar la causa
pendiente. La recuperación controlada local sí se observó: reemplazo en 33,055 s,
30 health antes/después y diez tablas sin cambios; conserva ese alcance acotado.
No usar las ventanas cortas como garantía de operación continua. La entrega S15
se publica con el comando sync de v26 en `heroku.yml` y `Dockerfile`; gthread/4
queda pendiente para una entrega aparte, después de la prueba de estabilidad
prolongada. Este documento no registra un nuevo despliegue ejecutado.
