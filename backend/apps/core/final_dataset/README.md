# Dataset funcional S15 local

Herramientas de ingeniería aisladas del runtime del producto. No llaman a Google,
OpenAI, Brevo ni X. La clasificación usa `adapter=` en la orquestación real;
Storage se sustituye temporalmente por disco privado y Celery captura solicitudes
sin encolarlas. Los avisos se conservan `PENDIENTE`: no acreditan entrega.

## Contrato y comandos

Requiere `DEBUG=True`, PostGIS `ciudadania_s15` en `ca-s15-db`/loopback,
Redis dedicado `redis://ca-s15-redis:6379/0` (o loopback:6395/0), con cache,
broker y resultados coincidentes. Storage declara
`SUPABASE_URL=http://127.0.0.1:18515` y buckets `s15-private`/`s15-reports`.
Necesita una clave **sintética** `DOCUMENT_HMAC_SECRET`. La lista cerrada está
en `isolation.py`; no reutilizar entornos S11/S14 ni configuración productiva.

La raíz privada debe estar bajo `/out/s15` (persistente, recomendada) o
`/tmp/ciudadania-s15` (pruebas desechables), fuera del repositorio y sin symlinks.
Los archivos se crean 0600 y la raíz 0700. Con DB migrada sin usuarios,
reportes ni eventos y con los catálogos estándar 3/10:

```sh
python manage.py generate_final_dataset --reference-date 2026-10-20 --seed 15 --reports 36 --artifact-root /out/s15 --dry-run
# Detener web, workers y Beat propios antes de la generación histórica.
python manage.py generate_final_dataset --reference-date 2026-10-20 --seed 15 --reports 36 --artifact-root /out/s15 --processes-stopped
python manage.py verify_final_dataset /out/s15/s15-functional-v1-2026-10-20-15-36/manifest.json --artifact-root /out/s15
python manage.py export_final_dataset_actors /out/s15/s15-functional-v1-2026-10-20-15-36/manifest.json --artifact-root /out/s15 --output /out/s15/actors.json
```

`--dry-run` no consulta SQL, crea objetos, escribe archivos ni emite JWT. En el
verificador solo inspecciona el manifiesto. La exportación emite sesiones con el
reloj real y todos los roles, en un archivo separado: `schemaVersion:1`,
`actors:[{id,userId,role,verified,accessToken,refreshToken,email,institutionId?}]`.
Carga debe filtrar ciudadanos verificados. Los JWT no forman parte del dataset.

Un replay verifica y sale sin modificar DB, archivos ni tiempos de modificación.
Datos parciales, journal incompleto, hashes o invariantes divergentes abortan;
no hay borrado, continuación ni reparación automática. Ante un fallo, la DB
revierte la transacción global y los uploads propios quedan identificados en el
journal. Reconstruir en otro entorno vacío mediante un paso operativo separado.

## Casos y comprobación

La ventana cerrada del manifiesto abarca exactamente 60 días en `America/Asuncion`.
R001 acredita inmediatamente antes del cambio de mes y R002 después. Las entradas
son mosaicos JPEG/PNG distintos sin personas, texto ni EXIF. UUID de servicios
pueden variar; identificadores lógicos, semilla, bytes y oráculos son reproducibles.

| Casos | Historia y resultado |
|---|---|
| R001–R030 | 30 primeras resoluciones: 10 por beneficiario/institución, 1.000 créditos por persona; 30 confirmaciones distribuidas entre otros 9 ciudadanos |
| R002 | S-01…S-04 observados antes/después de crear, confirmar, validar y resolver; S-05 autoconfirmación y S-06 duplicada rechazadas sin filas nuevas |
| R031 | Participación pendiente +20; extremo inicial de los 60 días |
| R032 | Rechazo S-11 anula autor +20 y confirmante +10 |
| R033 | Ingesta X simulada S-10 sin ciudadano de origen; confirmante gana +10 al validar |
| R034 | Validación +50 para excedente: beneficiario C01 llega a 1.050 y conserva 50 tras canjear |
| R035 | Validación, reversión y revalidación ciclo 2: no reactiva creación antigua |
| R036 | Resolución del mes previo y autocierre real tras 72 horas; impacto anterior excluido del ranking mensual |
| Canjes | S-08 tres vales de 1.000; S-09 segundo intento mensual rechazado; S-07/S-12 revierten R001 después del canje: C00 queda en −100 y el vale se conserva; intento en el mes siguiente devuelve saldo insuficiente |

El estado final contiene 12 ciudadanos aprobados por servicios de identidad,
4 preferencias de privacidad, más de 10 candidatos y empate en posiciones 10/11.
El ranking se contrasta por SQL con el oráculo; el recorte y presentación HTTP
de privacidad requieren la verificación API separada.

El verificador **solo lee** SQL y bytes. Contrasta movimientos completos y sus
timestamps, cuatro estados, saldos/pendientes, ranking, ciclos/historial, auditoría,
notificaciones y sus vínculos, canjes/débitos, objetos y hashes original/normalizado/
compuesto contra el oráculo declarativo de `oracle.py`, sin usar selectores del
producto. Publica numerador y denominador de eventos obligatorios y de checks.
El hash del manifiesto detecta divergencia accidental; no es una firma de autenticidad.

Los checkpoints y observaciones son registros de estado, **no respaldos restaurables**.
Consultar `now` sobre el ledger final no reconstruye el pasado. El resultado incluye
`not_verified`: permisos HTTP/API=SQL, restauración DB+Storage/as-of, Google/IA/correo
reales, supresión pública k=5, métricas SLA de API y demo de navegador. No readjudica S11.
El servidor REST local separado debe servir
`<artifact-root>/<dataset-id>/objects/<bucket>/<key>` para una demo HTTP posterior.

Pruebas: `pytest -q apps/core/tests/test_final_dataset.py` desde `backend/`;
formato/lint: `ruff format --check apps/core/final_dataset apps/core/management
apps/core/tests/test_final_dataset.py` y `ruff check` sobre esas rutas.
