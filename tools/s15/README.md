# Verificación local de S15

El entorno usa PostGIS y Redis exclusivos, una red Docker interna sin salida a proveedores,
la imagen de pruebas ya usada por S14 y artefactos privados fuera del repositorio. No
contiene credenciales reales. Los valores de prueba del runner son exclusivos de datos sintéticos.

Preparación desde la raíz, si todavía no existen estos recursos:

```bash
docker network create --internal --label ciudadania.sprint=15 ca-s15-local
docker run -d --name ca-s15-db --network ca-s15-local --label ciudadania.sprint=15 \
  -e POSTGRES_USER=ciudadania -e POSTGRES_PASSWORD=s15-local-database-only \
  -e POSTGRES_DB=ciudadania_s15 postgis/postgis:17-3.5
docker run -d --name ca-s15-redis --network ca-s15-local --label ciudadania.sprint=15 redis:7-alpine
docker exec ca-s15-db pg_isready -h 127.0.0.1 -U ciudadania -d ciudadania_s15
docker exec ca-s15-redis redis-cli ping
```

`check_local.sh` comprueba etiquetas y red interna antes de ejecutar. Monta el checkout de
solo lectura y oculta `.env`; todas las conexiones quedan fijadas a estos servicios locales.
Las dos pruebas existentes del adaptador S11 usan un tmpfs descartable en
`backend/.s11-test-artifacts`; esto permite sus fixtures sin escribir en el checkout
ni abrir un experimento S11. Esperar éxito real del readiness TCP antes de migrar o
restaurar: el socket Unix también puede responder durante la inicialización temporal.
El comando se ejecuta en `backend/` dentro del contenedor, sin interpretar argumentos como shell.
La imagen predeterminada es `ca-s12-backend-test:dev`; `S15_TEST_IMAGE` permite usar una imagen
equivalente con las dependencias ya instaladas. El script no instala paquetes.

```bash
bash tools/s15/check_local.sh ruff check .
bash tools/s15/check_local.sh ruff format --check .
bash tools/s15/check_local.sh python manage.py makemigrations --check --no-input
bash tools/s15/check_local.sh pytest -p no:cacheprovider --ignore=apps/experiments_app \
  --cov=apps --cov-config=coverage-gate.toml --cov-report=term-missing \
  --junitxml=/out/s15/junit-domain.xml
bash tools/s15/check_local.sh coverage report --rcfile=coverage-ht5.toml
```

Los resultados se escriben de forma predeterminada en
`~/.local/share/ciudadania-activa/s15/20261020/out/`; `S15_LOCAL_OUTPUT` admite otra carpeta
absoluta duradera, fuera del checkout y de `/tmp`. No se versionan reportes privados ni sesiones.
El script usa `config.settings.test` por defecto. `S15_LOCAL_SETTINGS=config.settings.dev`
permite usar los ajustes de demostración local con DEBUG habilitado y los guards propios
del generador; ningún otro módulo de ajustes se admite. No arrancar web, worker o Beat
durante la generación.

```bash
S15_LOCAL_SETTINGS=config.settings.dev bash tools/s15/check_local.sh \
  python manage.py generate_final_dataset --reference-date 2026-10-20 \
  --artifact-root /out/s15 --dry-run
S15_LOCAL_SETTINGS=config.settings.dev bash tools/s15/check_local.sh \
  python manage.py generate_final_dataset --reference-date 2026-10-20 \
  --artifact-root /out/s15 --processes-stopped
```

## Storage y conciliación de carga

El servidor persistente escucha exclusivamente en loopback del proceso web. Ejecutarlo
en su mismo contenedor/namespace de red, con credencial sintética y la copia del mundo
destinada a consumir en el ensayo HTTP. No servir el mundo funcional original si se
quiere conservar su snapshot de demostración.

```bash
python /work/tools/s15/storage_server.py \
  --root /out/s15/s15-functional-v1-2026-10-20-15-36 --port 18515
```

El cliente Django utiliza las variables sintéticas del runner. Storage autentica las
operaciones, restringe buckets y rutas, rechaza sobrescrituras y emite capacidades locales
de hasta 300 s. No registra tokens ni claves. No sustituye una prueba de Supabase real.

El export SQL usa el mismo entorno Django local. Preparar primero el manifiesto de carga
y el export de actores según [el harness](../performance/README.md), en archivos privados
0600 bajo `/out/s15`, dentro de directorios 0700. Desde el contenedor de ese entorno:

```bash
python /work/tools/s15/reconcile_creation.py before \
  --manifest /out/s15/http-smoke-1/manifest.json \
  --actors-file /out/s15/http-smoke-1/actors.json \
  --output /out/s15/http-smoke-1/sql-before.json
# Ejecutar una sola ventana de creación; conservar su serie íntegra.
python /work/tools/s15/reconcile_creation.py after \
  --manifest /out/s15/http-smoke-1/manifest.json \
  --actors-file /out/s15/http-smoke-1/actors.json \
  --baseline /out/s15/http-smoke-1/sql-before.json \
  --output /out/s15/http-smoke-1/sql-after.json
```

El alias `--actors-file` permite resolver la ruta host dentro del contenedor conservando
el hash de los bytes originales del manifiesto. El baseline exige que no existan los
casos/hashes antes de empezar. `after` verifica su integridad y consulta PostgreSQL real
en una transacción `REPEATABLE READ READ ONLY`. No repara, no escribe datos de negocio,
no consume la serie HTTP y no sobrescribe evidencia anterior. El reporter k6 combina
después ambas observaciones por caso/actor/UUID/hash. Solo reconcilia creación: no
certifica débitos de canjes, drenaje de workers ni cumplimiento de RNF productivos.

Si se ejecuta k6 mediante Docker, fijar `umask 077` **dentro** del contenedor además
de proteger el directorio host. El umask del cliente Docker no se aplica a los archivos
creados por el daemon. El preflight rechaza series con permisos de grupo/otros.

Una pasada de estas pruebas no mide Heroku ↔ Supabase, concordancia IA ni carga productiva.
Los escenarios k6 y el ejecutor de evaluación tienen preflights y procedimientos separados.
Los recursos persisten para poder revisar los artefactos; detenerlos o eliminarlos requiere
comprobar antes que ya no se necesita su mundo sintético.

## Perfil de medición y observación de tareas

`measurement_settings.py` es un módulo exclusivo de herramientas: importa la base del
producto con `DEBUG=False`, Gunicorn, TTL explícitos y Beat deshabilitado. Su guard en
`local_profile.py` comprueba variables antes y después de importar la base. Exige los
destinos DB/Redis/Storage sintéticos cerrados y proveedores IA/Google desactivados; no
admite usarlo contra el despliegue. Los ajustes dev/test del producto no se modifican.

La imagen `Dockerfile.measurement` añade Gunicorn 22.0.0 a la imagen de pruebas existente.
Se construye con contexto `tools/s15`, sin copiar `.env` ni credenciales:

```sh
docker build -f tools/s15/Dockerfile.measurement \
  -t ca-s15-measurement:22.0.0 tools/s15
```

Preparar **otra copia** de DB, Redis y objetos para cada ensayo que consuma el mundo.
Las tareas de creación se observan cargando `tools.s15.worker_receipts` mediante
`celery --include`. `S15_TASK_RECEIPTS` debe apuntar a un archivo privado bajo `/out/s15`.
Los recibos contienen UUID de tarea y objetivo, estado y duración, sin argumentos,
direcciones, mensajes ni resultados. La clasificación deshabilitada puede terminar como
`SUCCESS` sin clasificar: se informa como no-op, nunca como acierto de IA.

`mail_server.py` sustituye la API de correo en loopback:18516. Sólo acepta una clave
sintética, limita el cuerpo y crea un recibo privado con ordinal, fecha y hash del payload.
No envía correo ni guarda destinatario/cuerpo. Una notificación `ENVIADO` bajo este perfil
prueba aceptación del sustituto local, no entrega de Brevo. El plazo y la conciliación
deben observarse por reporte/tarea/notificación, además de los totales.

Las ejecuciones y sus límites están en el [informe de rendimiento](../../docs/s15/S15_INFORME_RENDIMIENTO.md).
Los manifiestos nuevos se vinculan por SHA-256 de bytes exactos en preflight, ejecución,
serie k6 y SQL. Un artefacto legado sin ese vínculo conserva carácter diagnóstico y no
se promueve editando sus metadatos; véase [el contrato de carga](../performance/README.md).

## Diagnóstico remoto de solo lectura

`remote_db_probe.py` es independiente del harness de carga local. Exige ajustes de
producción, un dyno temporal Heroku y `--confirm-existing-deployment-readonly`; valida
Supabase como destino, TLS y límites de muestras antes de conectar. Ejecuta únicamente
constantes y metadatos de versión/sesión en transacciones `READ ONLY`, timeout local y
rollback. No consulta filas de negocio ni habilita carga remota. No publica DSN, host,
usuario, clave o traceback de conexión.

El [diagnóstico observado](../../docs/s15/S15_DIAGNOSTICO_REMOTO.md) conserva metodología,
series privadas y hashes. Es tiempo de acceso a DB desde un dyno temporal: no representa
RTT puro, HTTP, el BFF ni capacidad del proceso web activo.

## Pruebas offline de auxiliares

Con las dependencias de pruebas del backend, desde la raíz:

```sh
python -m unittest discover -s tools/s15 -p 'test_*.py' -v
ruff check --config backend/pyproject.toml tools/s15
ruff format --check --config backend/pyproject.toml tools/s15
bash -n tools/s15/check_local.sh
```

Estas pruebas no abren conexiones remotas ni consumen API de IA. Las ejecuciones reales
de API/SQL y workers se documentan aparte y conservan su propio preestado y evidencia.


## Navegador con API real local

El [carril nativo S15](../../docs/s15/S15_FLUJOS_NAVEGADOR.md) usa una restauración
propia, `browser_seed.py` para preparar y después verificar con SQL/HMAC, y sesiones
privadas para F1…F8. `browser_settings.py` permite únicamente el origen local; relay y
forwarder resuelven el acceso loopback a Docker interno. No repetir preparación ni
escrituras sobre un mundo consumido. Los vídeos/trazas pueden contener credenciales
sintéticas y no se versionan. La [serie descriptiva de pantallas](../../docs/s15/S15_RENDIMIENTO_FRONTEND.md)
se ejecuta solo después de aprobar navegador y postchequeo, con build y CPU exclusivos.
