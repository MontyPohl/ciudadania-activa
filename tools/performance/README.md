# Carga S15, exclusivamente local

Este carril valida el harness y mide escenarios sintéticos contra Django HTTP directo.
No emite veredictos de RNF productivos, no mide Heroku ↔ Supabase y no modifica S11.
No instala herramientas, inicia servidores, genera sesiones ni configura proveedores.
El árbol de producto no incorpora dependencias ni endpoints para estas pruebas.

Se necesitan Node 22+, Python con Pillow de las dependencias existentes del backend,
y un ejecutable **local** de k6 al ejecutar carga. `node --test` no necesita k6 ni servidor.
La versión concreta de k6 queda en `execution.json`; no deducir ejecución real de los tests
unitarios ni de la presencia de estos scripts.

```bash
node --test tools/performance/tests/*.test.mjs
node tools/performance/cli.mjs template --out /tmp/s15-private-template
node tools/performance/cli.mjs preflight /tmp/s15-private-template/manifest.json
```

El último comando rechazará el template hasta sustituir los actores/tokens de ejemplo.
La plantilla genera **50 PNG válidos de 64×64**, con píxeles distintos, para diez ciudadanos
y cinco escrituras por ciudadano. No representa tamaños de imágenes de producción.
Reemplazar también categoría, dataset/hash, SHA completo, mes, expectativas de lectura,
puerto y configuración registrada. El directorio de salida debe ser nuevo y su padre existir.
Para fijar otro Python usar `--python /ruta/al/python` en `preflight` y `run`.

El preflight es **offline**: comprueba contratos, presupuestos, permisos de archivos,
hashes, claims/expiración JWT y decodifica/normaliza cada foto mediante la función vigente
`prepare_report_photo`, sin Django, DB, Redis, uploads ni red. No verifica la firma JWT,
la existencia del dataset, cuotas ya consumidas, configuración efectiva del servidor,
aislamiento de procesos, saldo legítimo ni estado de caché. Estas son observaciones separadas.
No usarlo como prueba de que se bloqueó toda salida de red del servidor.

El manifiesto se lee una sola vez: su SHA-256 corresponde a los **bytes exactos**, sin
reescribir JSON. `run` guarda `manifest.snapshot.json` idéntico al archivo leído y vincula
ese hash en preflight, `execution.json`, resumen y series. k6 recalcula el SHA antes de
cualquier HTTP y aborta si difiere. Un wrapper Docker debe reenviar también
`-e S15_MANIFEST_SHA256`, junto con `S15_MANIFEST`, `S15_PROFILE` y `S15_SUMMARY`.
El hash identifica la configuración declarada; no certifica por sí mismo los settings
efectivos del servidor ni constituye una firma de autenticidad de los artefactos.

El destino solo acepta `http://127.0.0.1:PUERTO` o `http://localhost:PUERTO`, sin path,
usuario, query ni fragmento. `localhost` se fija a `127.0.0.1` en k6, no hay redirects,
imports remotos ni modo para autorizar dominios externos. El runner elimina variables
de proxy/opciones heredadas de k6, usa configuración vacía y desactiva telemetría.
Los scripts no registran tokens, headers, cuerpos, nombres ni URLs firmadas.
No activar `--http-debug` ni ejecutar en k6 Cloud.

Manifiestos, actores, fotos y resultados deben quedar **fuera del repositorio** y ser
archivos privados (sin permisos de grupo/otros, normalmente `0600`; directorios `0700`).
El runner aplica `umask 077`, rehúsa sobrescribir salidas y comprueba paths reales,
incluidos symlinks. [actors.example.json](examples/actors.example.json) contiene solo
placeholders, nunca una sesión utilizable. Un archivo de demo puede incluir operadores
y administradores; los casos referenciados por carga deben usar ciudadanos verificados.
Las sesiones se emiten por la herramienta local de dataset y se regeneran antes de medir.

## Contrato del manifiesto

`schemaVersion: 1`, `protocol: "s15-local-v1"`, `runId` único por ventana,
`baseUrl`, `datasetId`, `datasetSha256`, `referenceMonth: "YYYY-MM"` y `actorsFile`
absoluto son obligatorios. `datasetSha256` identifica la entrada congelada, no el estado
posterior a las escrituras. `actorsFile` tiene `{schemaVersion:1, actors:[...]}`;
cada actor aporta `id`, `userId` UUID, `role`, `verified` y `accessToken`.

`purpose: "harness-smoke"` permite DEBUG y TTL de fixtures para comprobar la mecánica.
`purpose: "local-measurement"` exige `environment.debug:false` y TTL mayores a un segundo.
Ambos siguen siendo locales. `environment` registra `commit` (SHA de 40 caracteres),
`applicationServer`, `mapCacheTtlSeconds` y `rankingCacheTtlSeconds`; anotar además
procesos, capacidad, tamaños, topología y efectos observados en la evidencia de la ejecución.
El validador comprueba declaraciones, no inspecciona settings efectivos ni afirma equivalencia productiva.

`isolation` exige `synthetic:true`, `externalProviders:"substituted-or-disabled"`,
`workers:"stopped"` o `"local-substitutes"` y `beat:"stopped"`.
DB, Redis (broker/resultados incluidos) y Storage deben pertenecer al entorno dedicado.
No basta un prefijo de caché ni la ausencia de API keys para garantizar aislamiento.
Workers detenidos implican que esta prueba no demuestra clasificación ni completitud asíncrona.
Un ensayo con workers locales necesita observar por separado las filas, auditoría,
reintentos, notificaciones esperadas y plazo de drenaje. Beat no se inicia desde este carril.

| Sección        | Contrato                                                                                                                      |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `create`       | `actorIds`: diez distintos; `payloads`: cincuenta, cinco por actor                                                            |
| Payload        | `caseId`, `actorId`, `category` UUID, `description` no vacía, `lat`, `lng`, `photo:{path,mime,sha256}`                        |
| Foto           | JPEG/PNG real, extensión/MIME coincidentes, ≤5 MiB original y normalizada; hashes y píxeles únicos                            |
| `map`          | `requests`: múltiplo de tres, 3..99; `durationSeconds:60`; bbox, stateFilter, page, expected                                  |
| `ranking`      | `requests`: 1..100; `durationSeconds:60`; `expected:{period,entries}` exactos                                                 |
| Cada lectura   | `precheckRequests`, `warmupRequests`, `verificationRequests`, `cacheState`, `cacheEvidence`                                   |
| `map.expected` | `count`, `ids` de la página en orden, `detailId`, `detail` con campos semánticos esperados, `categories:[{id,slug}]` en orden |
| `redeem`       | `successes:[{caseId,actorId,rewardId,institutionId,cost:1000,balanceBefore,balanceAfter,funding:"domain-services"}]`          |
| `negatives`    | `cases:[{caseId,actorId,rewardId,institutionId,expectedStatus:409,expectedCode}]`                                             |

Negativas permitidas: `MONTHLY_LIMIT_REACHED`, `INSUFFICIENT_BALANCE` y `REWARD_INACTIVE`.
Para saldo insuficiente agregar `balance` exacto (puede ser negativo) y
`missingCredits:1000-balance`. El cupo mensual se comprueba antes del saldo: no reutilizar
el mismo preestado para demostrar ambas reglas. El caso mensual se ejecuta después del canje
o desde su checkpoint; el de saldo requiere otro actor o un checkpoint/mes apropiado.
El catálogo HTTP se comprueba antes de mutar para detectar mes, saldo y cupo incompatibles.
`REWARD_INACTIVE` debe tener preestado verificado aparte porque puede no aparecer en catálogo.

Para N canjes exitosos se necesitan 1.000×N créditos legítimos y cupos distintos por
ciudadano/institución/mes. No rellenar saldos directamente. **Tres éxitos solo dan latencias
exploratorias; no demuestran una cola P99.** No se inventa una muestra financiada.

## Ejecución y poblaciones

Preparar el servidor y sus servicios sintéticos fuera de estos comandos. La carga muta
el entorno dedicado: conservar preestado, congelar SHA/configuración y detenerse ante efectos
externos o pérdida de correlación. Un resultado abortado se conserva; no se reintentan escrituras.

```bash
node tools/performance/cli.mjs run /ruta/privada/manifest.json create --out /ruta/privada/create-window-1
node tools/performance/cli.mjs run /ruta/privada/manifest.json map --out /ruta/privada/map-cold
node tools/performance/cli.mjs run /ruta/privada/manifest.json ranking --out /ruta/privada/ranking-warm
node tools/performance/cli.mjs run /ruta/privada/manifest.json redeem --out /ruta/privada/redeem
node tools/performance/cli.mjs run /ruta/privada/manifest.json negative --out /ruta/privada/negative
```

Se puede elegir binario local con `--k6 /ruta/k6`. El runner no instala k6. Cada comando
ejecuta su propio `setup`: health con DB/Redis y, para escrituras, `/auth/me/` por actor.
Los canjes consultan además catálogo antes de la barrera. Ninguno usa login Google ni BFF.
Health/auth/catálogo quedan fuera de las poblaciones y no consumen los scopes medidos;
también consumen recursos del servidor y se registran como fase `preflight`.

Creación tiene diez VU, una iteración por VU y cinco POST **secuenciales** por ciudadano,
sin calentamiento de escritura. Todos esperan una barrera común del reloj de ejecución
monotónico de k6. Se deja de iniciar al llegar a 60.000 ms y se conserva la respuesta
de las operaciones en curso: una finalización posterior no cuenta para throughput.
`s15_started` registra intentos y `s15_outcome` cada retorno, incluso 429/4xx/5xx/timeout.
Interrupciones sin retorno se muestran por diferencia. No se usa `created_at` como reloj de commit.
Cada multipart contiene exactamente `category`, `description`, `photo`, `lat`, `lng` una vez.

Mapa alterna listado, detalle y categorías en un solo presupuesto `public_map` por IP
(90 solicitudes = 30 por ruta en la plantilla). Ranking usa su scope `public_ranking` por IP.
No se envían JWT al mapa/ranking ni se falsifica `X-Forwarded-For`. Cada perfil reserva
prechecks, calentamiento y verificación; su suma con las peticiones medidas no supera 120/min.
Se deben esperar ventanas de cuota limpias entre tandas; el validador no inspecciona contadores.
Creación conserva 5/h por ciudadano y canje 10/h, incluyendo negativas. Repetir creación
requiere ciudadanos nuevos o restaurar exclusivamente su entorno/checkpoint según protocolo.

Las lecturas se programan durante 60 s, con un VU. El último retorno puede terminar antes
del segundo 60 y no se usa la duración total del proceso como denominador de throughput.
Si la lentitud impide iniciar el número fijado antes del corte, el reporte resulta incompleto.
El bbox de entrada no supera 0,5° por eje; se publica su expansión canónica, que puede llegar
a 0,501°. Las expectativas deben congelarse desde el dataset, sin usar respuestas actuales
para hacer pasar las aserciones.

Para caché fría y caliente usar manifests/runIds distintos, bbox/filtros/página idénticos,
presupuesto explícito y observación externa de claves/hit/miss/TTL. El harness **no** ejecuta
`cache.clear()`, no enfría por latencia ni altera throttles/broker. El calentamiento se realiza
fuera de la población y se anota en `warmupRequests`; el runner no lo inventa ni lo hace solo.
Una etiqueta `cold`/`warm` es una declaración; el reporte conserva `independentlyVerified:false`.
Categorías/detalle no comparten necesariamente la caché del listado. La escritura invalida la
generación de mapa: este carril mide lecturas estables, no afirma caché caliente en carga mixta.

Por endpoint se exigen todos los checks contractuales y cero errores imprevistos.
Latencias exitosas y de errores se publican separadas. Objetivos observados inalterados:
**P50 ≤800 ms, P99 ≤2.000 ms y 50 creaciones persistidas dentro de 60 s**.
La latencia medida por este carril es el tiempo monotónico del generador alrededor de
`http.request` (incluye subida y conexión); las métricas HTTP nativas quedan en la serie.
Los percentiles interpolan linealmente en `(N−1)×p`, con N publicado y sin exclusiones ocultas.
P99 de 50 observaciones queda cerca del máximo; no demuestra una estimación estable poblacional.
Repetir tres ventanas independientes, publicar cada una y conservar también incumplimientos;
el reporter rechaza mezclar runIds/perfiles y no combina corridas ni elige la mejor.

## Reporte y SQL

Cada ejecución conserva `preflight.json`, `execution.json`, `manifest.snapshot.json`, `series.jsonl`, `summary.json`
y `report.json` privados. En la serie, `s15_outcome.data.value` es el tiempo de finalización
en ms desde la barrera. Sus tags incluyen `case_id`, `actor_id`, `endpoint`, `status`, `ok`,
`response_id`, `photo_sha256`, `start_ms`, `latency_ms` y `manifest_sha256`, nunca el JWT ni el código del vale.
El tag `manifest_sha256` debe estar en **todos** los starts/outcomes de una corrida y
coincidir con los bytes del manifiesto usado al reportar, también para lecturas sin SQL.
Un hash inválido, distinto o parcialmente ausente invalida el reporte. Cambiar `purpose`,
DEBUG, topología u otra metadata conservando runId/dataset no permite reutilizar la serie.
El índice publicable deberá referir estos archivos por hashes y ubicación privada.

Sin SQL, `creation.status` es `NOT_DEMONSTRATED`, `promotable:false` y `throughputPass:false`, aunque haya
cincuenta 201. El exit 0 de `run` demuestra checks HTTP y umbrales locales de k6, no
persistencia ni RNF. `report` exige además la reconciliación vinculada para aprobar throughput de creación.
En lecturas, `promotable:true` exige el binding de la serie; representa elegibilidad de
esa evidencia local, sin convertirla en un veredicto productivo ni reemplazar los demás checks.

Las series anteriores sin el tag, los objetos sin bytes originales y un export SQL
sin `manifestSha256` se conservan como **diagnóstico no promotable**. El reporter no
añade hashes retroactivos: publica `manifestSha256:null`, propósito `unbound-diagnostic`
y checks aceptados falsos cuando falta el binding de la serie. No editar los artefactos
viejos ni sus hashes para presentarlos como una nueva medición. Usar una nueva ventana.

```bash
node tools/performance/cli.mjs report /ruta/privada/create-window-1/manifest.snapshot.json /ruta/privada/create-window-1/series.jsonl --profile create --sql /ruta/privada/reconciliation.json --k6-version 'VERSION_OBSERVADA' --out /ruta/privada/reconciled-report.json
```

El export SQL debe provenir de consultas reales de solo lectura antes/después del entorno
local, identificadas con su método, comandos y hashes en la evidencia. El reporter **no ejecuta
SQL ni certifica cómo se obtuvo el export**. Consume este contrato mínimo:

```json
{
  "schemaVersion": 1,
  "runId": "ID_DE_ESTA_VENTANA",
  "datasetSha256": "SHA256_DEL_DATASET",
  "manifestSha256": "SHA256_EXACTO_DEL_MANIFIESTO_DE_ESTA_CORRIDA",
  "method": "postgresql-before-after",
  "rows": [
    {
      "caseId": "create_1",
      "actorId": "load_citizen_1",
      "reportId": "UUID_RECIBIDO",
      "photoSha256": "SHA256_ORIGINAL",
      "reportRows": 1,
      "mediaRows": 1,
      "initialHistoryRows": 1,
      "pendingCreditRows": 1,
      "impactDelta": 20,
      "creditDelta": 20
    }
  ]
}
```

Las consultas deben ligar ciudadano, media/hash original, historial inicial y evento
`REPORT_CREATED/PENDIENTE` de 20/20 al reporte real, excluir filas previas y detectar
duplicación. Se comprueba coincidencia por caso/actor/UUID/hash y cardinalidad; se separan
persistencias fuera de ventana o sin respuesta. Las invariantes de canje/negativas
(débitos, canjes y notificaciones antes/después) y completitud de worker requieren su
export/observación independiente; los checks HTTP de este carril no las sustituyen.

El export SQL de creación debe conservar el `manifestSha256` original y coincidir
exactamente con manifiesto y serie. Coincidir solo en runId o dataset no es suficiente.
El SHA de datos y el SHA del manifiesto tienen funciones distintas.
Para uso Node directo, `buildReport(manifest, points, {profile, manifestBytes, sql})`
y `reconcileCreation(outcomes, sql, manifest, {manifestBytes})` reciben los bytes originales;
calculan el hash internamente y rechazan un objeto que no corresponda a esos bytes.
Omitirlos genera únicamente diagnóstico, aunque el caller suministre un campo hash calculado aparte.

Las APIs utilizadas se contrastaron con documentación primaria de Grafana:
[multipart con `http.file`](https://grafana.com/docs/k6/latest/examples/data-uploads/),
[reloj y ejecución](https://grafana.com/docs/k6/latest/javascript-api/k6-execution/),
[export JSON](https://grafana.com/docs/k6/latest/get-started/results-output/),
[resumen local](https://grafana.com/docs/k6/latest/results-output/end-of-test/custom-summary/)
y [SHA-256 de bytes y texto](https://grafana.com/docs/k6/latest/javascript-api/k6-crypto/sha256/)
y [interpolación en el código oficial](https://github.com/grafana/k6/blob/master/metrics/sink.go).
Registrar siempre la versión realmente ejecutada; estas referencias no prueban un smoke.
