# Sprint 15 — rendimiento local representativo

**Corte:** 20/10/2026. **Protocolo:** `s15-local-v1`, manifiestos con
`purpose: local-measurement` y vínculo SHA-256 exacto. Base `3d89182` más las
fuentes S15 congeladas durante las cargas. La referencia Gunicorn `sync` de un
worker pasó dos de tres ventanas y **falló W1 por latencia**. El candidato de un
worker `gthread` con cuatro hilos pasó las tres réplicas locales, con persistencia
y tareas reconciliadas en cada una. No se mezclan ambas configuraciones ni se
selecciona la mejor corrida para ocultar las demás.

Esta evidencia justifica proponer la configuración del candidato para integración.
El cambio de `Dockerfile`/`heroku.yml` está preparado, pendiente de integración/publicación, después de medir;
**no se desplegó ni se midió HTTP productivo**. El resultado de referencia sigue
siendo variable, con una ventana válida incumplida. Las adjudicaciones de S11
permanecen sin cambios.

## Perfil y entradas

| Elemento                | Configuración observada                                                                                                  |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Aplicación              | Django 5.2.18, Python 3.11.17, Gunicorn 22.0.0, `DEBUG=False`                                                            |
| Web de referencia W1–W3 | Un worker `sync`, un hilo; límite 2 CPU y 1 GiB                                                                          |
| Candidato C1–C3         | Un worker `gthread`, cuatro hilos; los mismos límites                                                                    |
| Asíncrono               | Celery 5.4.0, pool `solo`, concurrencia 1; límite 1 CPU y 512 MiB; Beat detenido                                         |
| Datos                   | PostgreSQL 17/PostGIS 3.5 y Redis 7 propios de la medición; sin límites CPU/memoria Docker adicionales para DB/Redis     |
| Integraciones           | Storage REST y correo sustituidos en loopback; IA desactivada; ninguna llamada a proveedores                             |
| Transporte              | HTTP directo a `127.0.0.1:8000` dentro de una red Docker interna; sin BFF, TLS ni proxy remoto                           |
| Instrumento             | Node 22; k6 2.3.0, commit `e088784614`, Go 1.27.1; sin k6 Cloud/telemetría                                               |
| Host                    | Ryzen 7 1700X, 16 CPU lógicas, aproximadamente 15 GiB RAM; carga del host observada 4,37/4,23/5,15 al registrar contexto |
| Fotos                   | 50 PNG sintéticos distintos de 64×64, 11.691–11.792 bytes cada uno; distribución distinta de las fotos reales            |

Root y los carriles API/UX reservaron CPU: no se ejecutaron builds, pytest completo
ni browser E2E concurrentes con estas ventanas. Los servicios Docker y el sistema
operativo continuaron activos; el host no estuvo dedicado ni aislado por CPU.
El contexto y los límites efectivos se conservaron mediante Docker inspect,
lectura de settings dentro del contenedor web y `docker stats`.

El snapshot original contiene **36 reportes**. Su manifiesto tiene SHA-256
`5270cc85378ae9ddb681199da16e660f3d5fdcf9ee7e4dfc0e2ea97ed0d71f3a` y el dump
`69f659a257feab17f9de94a7dc0fbd3c6d0c7557b4de0ea97730a34a448f19c7`.
Cada ventana de creación restauró una copia independiente de ese dump y Storage,
con Redis dedicado vaciado únicamente entre restauraciones, con web y worker
propios detenidos. No se alteraron los mundos original, restauración o API.
Cada copia terminó con 86 reportes; las lecturas usan la copia consumida de W3.
`datasetSha256` conserva la identidad de la entrada, y `datasetId` identifica la
ventana/estado consumido; el SHA de la entrada no describe un dump posterior.

## Creación y persistencia

Se fijaron diez ciudadanos verificados, cinco payloads únicos y secuenciales por
ciudadano, una barrera común y una ventana de 60 segundos. No se reintentaron
escrituras. Cada réplica debía completar al menos 50 altas dentro de la ventana,
sin errores inesperados, con P50 ≤800 ms y P99 ≤2.000 ms sobre éxitos completos.
Las cuotas permanecieron en cinco creaciones/hora por ciudadano; la restauración
separada de cada entorno evita reutilizar un estado ya consumido.

| Ventana | Worker web / hilos | Iniciadas / HTTP 201 / SQL | P50 ms  | P99 ms  | Latencia |
| ------- | ------------------ | -------------------------- | ------- | ------- | -------- |
| W1      | sync / 1           | 50 / 50 / 50               | 2725.86 | 4226.27 | FAIL     |
| W2      | sync / 1           | 50 / 50 / 50               | 660.81  | 1041.32 | PASS     |
| W3      | sync / 1           | 50 / 50 / 50               | 654.69  | 767.30  | PASS     |
| C1      | gthread / 4        | 50 / 50 / 50               | 257.73  | 668.91  | PASS     |
| C2      | gthread / 4        | 50 / 50 / 50               | 234.32  | 469.80  | PASS     |
| C3      | gthread / 4        | 50 / 50 / 50               | 376.55  | 1008.04 | PASS     |

Las seis ventanas completaron sus 50 respuestas antes de los 60 segundos,
con cero errores, interrupciones o respuestas sin correlación. Última respuesta
respecto de la barrera: W1 15,292 s; W2 3,679 s; W3 3,433 s; C1 1,621 s;
C2 1,372 s; C3 2,386 s. Se acredita el objetivo de **50 altas por ventana de
60 segundos**, sin extrapolar esas terminaciones breves a capacidad sostenida.
W1 conserva exit 99 de k6 y exit 1 del CLI por sus umbrales incumplidos.

SQL independiente, en `REPEATABLE READ READ ONLY`, demostró ausencia previa de
los cincuenta casos/hashes y existencia posterior exacta por caso, ciudadano,
descripción, ID de respuesta y hash de foto. Se comprobaron una foto y su autor,
un historial inicial y un movimiento `REPORT_CREATED` pendiente de 20 puntos y
20 créditos por reporte. El informe cruza esa existencia posterior con los
HTTP 201 completados dentro de la barrera; no se instrumentó por separado el
instante de commit SQL de cada petición.

Los percentiles usan interpolación lineal, índice `(N−1)×p`, sin combinar ventanas.
Con N=50, P99 se aproxima al máximo observado: superar el umbral local no demuestra
una cola poblacional estable ni una tasa de fallos productiva.

## Tareas y correo

En **cada una de las seis réplicas** se observaron 100 task IDs únicos con
`SUCCESS`: 50 tareas `ai.classify_report` vinculadas exactamente a los nuevos
reportes y 50 `notifications.send_notification` vinculadas a sus notificaciones.
El plazo de drenaje era de hasta 90 segundos; las colas `celery`, `unacked` y
`unacked_index` quedaron vacías.

SQL confirmó 50 notificaciones `REPORTE_CREADO`, estado `ENVIADO`, un intento y
sin próximo reintento. El sustituto de correo aceptó 50 peticiones, con ordinales
1…50. Los recibos guardan fecha, ordinal y hash, sin cuerpos: **diez hashes de
payload distintos son correctos**, porque cada destinatario recibe cinco veces
la misma plantilla. El primer comprobador privado de W1 exigió incorrectamente
50 hashes distintos; su FAIL se conserva junto al informe corregido, sin repetir
las creaciones ni los envíos.

IA estaba desactivada: las 50 tareas correspondientes terminaron como **noop** y
SQL conservó cero clasificaciones nuevas. `SUCCESS` acredita la ejecución local
de la tarea, no clasificación automática ni concordancia IA. La aceptación del
sustituto y `ENVIADO` no acreditan entrega por Brevo.

## Lecturas, SQL y caché

Antes del primer HTTP se congeló un oráculo propio mediante SQL crudo de solo
lectura y proyección Python simple, sin selectores ni serializadores del producto
y sin usar respuestas HTTP como esperado. Contiene 86 reportes, **85 públicos en
el bbox**, veinte IDs de la página en orden, detalle, categorías y Top 10 mensual
con privacidad resuelta. SHA-256 del oráculo:
`dbe291e790f88421ebf2a5c88d487b6bc4f3731e85e8e702f7a09439bfd5501a`.

Cada perfil ejecutó 90 solicitudes distribuidas en 60 segundos: mapa alterna
listado/detalle/categorías, treinta por ruta, con el scope compartido de 120/min
por IP; ranking usa noventa. Las ventanas inicialmente pobladas declaran un
warmup adicional fuera de la población: presupuesto 91/120. No se repitieron
creaciones en estos manifiestos. Las 360 lecturas completaron con cero errores
y el SQL posterior conservó las cantidades y expectativas semánticas previas.
Todas estas lecturas corresponden al perfil de referencia `sync`.

| Ventana                  | Ruta             | N   | P50 ms | P99 ms | Latencia |
| ------------------------ | ---------------- | --- | ------ | ------ | -------- |
| M1, caché no verificada  | `map_list`       | 30  | 5.33   | 38.83  | PASS     |
| M1, caché no verificada  | `map_detail`     | 30  | 39.17  | 42.88  | PASS     |
| M1, caché no verificada  | `map_categories` | 30  | 22.28  | 25.08  | PASS     |
| M2, inicialmente poblada | `map_list`       | 30  | 5.32   | 73.60  | PASS     |
| M2, inicialmente poblada | `map_detail`     | 30  | 38.08  | 41.77  | PASS     |
| M2, inicialmente poblada | `map_categories` | 30  | 21.56  | 30.55  | PASS     |
| R1, inicialmente ausente | `ranking`        | 90  | 23.92  | 31.37  | PASS     |
| R2, inicialmente poblada | `ranking`        | 90  | 23.91  | 29.79  | PASS     |

El TTL configurado no se tomó como prueba de caché. Se capturaron comandos
GET/SETEX y claves públicas con Redis MONITOR filtrado en memoria; no se guardaron
valores, comandos del broker, tokens ni claves de throttle. PTTL previo a las
ventanas pobladas: mapa 59.837 ms y ranking 59.825 ms; en ranking se capturó
SETEX de 60.000 ms. No se ejecutó `cache.clear`, DELETE ni FLUSHDB durante lecturas.

El primer filtro de mapa omitía `KEY_PREFIX=ciudadania_activa`; su archivo quedó
vacío. Esa corrida conserva sus HTTP válidos, pero su estado de caché queda
**no verificado**, aunque el nombre de carpeta diga `initially-absent`.
No se relabeló retroactivamente el manifiesto. El filtro se corrigió antes de la
siguiente corrida, que sí verificó PTTL positivo.

GET seguido de SETEX antes del siguiente GET de esa clave identifica un fallo y
llenado; GET sin llenado se clasifica como acierto **inferido** a partir del código
de caché y la ausencia de escritores. La unión por timestamp con cada outcome
separa el warmup de la población medida:

| Población de caché observada     | Aciertos inferidos | Fallos y llenados medidos | P50 del acierto ms | Latencia del único fallo ms |
| -------------------------------- | ------------------ | ------------------------- | ------------------ | --------------------------- |
| M2, listado                      | 29                 | 1                         | 5,32               | 62,87                       |
| R1, ranking inicialmente ausente | 89                 | 1                         | 23,91              | 30,90                       |
| R2, ranking inicialmente poblado | 89                 | 1                         | 23,89              | 28,60                       |

M2 y R2 tienen además un llenado del warmup fuera de N. El TTL venció naturalmente
durante sus ventanas de 60 segundos; ninguna fue íntegramente caliente. Detalle
y categorías no usan estas claves de listado/ranking. Un solo fallo por estrato
no permite estimar P99 frío; tampoco se afirma limpieza de la caché del sistema
operativo o de PostgreSQL.

## Negativas y límite de canje

Se ejecutaron tres casos separados con preestado SQL legítimo: los tres canjes
mensuales ya existentes bloqueaban otro intento. Se obtuvieron **3/3 HTTP 409
`MONTHLY_LIMIT_REACHED`**, cero errores inesperados y mediana 58,38 ms. Antes y
después coincidieron los hashes de contenido de nueve tablas de dominio, además
de sus cantidades: ningún segundo canje, débito, correo o auditoría fue agregado.

No se ejecutó un canje exitoso sobre un mundo ya canjeado, no se insertaron créditos
ni se cambió el mes del servidor para fabricar elegibilidad. El rendimiento de
**canje exitoso queda NOT_DEMONSTRATED** en este carril. N=3 de negativas no es
una muestra de canjes exitosos y no caracteriza una cola P99; otras negativas
funcionales pertenecen al [carril API](S15_API_SQL_Y_FLUJOS.md).

## Comparación de configuración y recursos

El candidato se fijó antes de C1 en `candidate-protocol.json`, con tres réplicas,
los mismos casos, fotos, recursos y restauraciones que la referencia. Su SHA-256:
`c2ffe0ef5548e207ba22f039dc2adfa2ecb1fdcbb02c585eb39b0eb9aa22b333`.
No se adaptaron umbrales después de observarlo. P50 y P99 pasaron en C1, C2 y C3;
W1 permanece fallida y W2/W3 conservan sus valores completos.

Gunicorn documenta que un worker `sync` atiende una petición a la vez y que
`gthread` distribuye conexiones en un pool de hilos.
[Diseño oficial de Gunicorn 22](https://github.com/benoitc/gunicorn/blob/22.0.0/docs/source/design.rst).
La configuración oficial de ejemplo de Heroku utiliza `gthread` para esperas de
E/S y recuerda dimensionar las conexiones de servicios según workers e hilos.
[Ejemplo oficial de Heroku](https://github.com/heroku/python-getting-started/blob/main/gunicorn.conf.py).
Los cuatro hilos permiten más peticiones simultáneas y pueden abrir más conexiones
Django/Redis; se debe presupuestar ese total al integrar/desplegar.

Los tiempos k6 de creación estuvieron dominados por `http_req_waiting`; conexión
y bloqueo del cliente promediaron menos de 1 ms en la referencia. Las medias de
espera fueron W1 2.796,34 ms, W2 675,87 ms y W3 624,55 ms, frente a C1 304,23 ms,
C2 254,62 ms y C3 449,69 ms. Esto es compatible con una reducción de cola/esperas
al aumentar concurrencia, pero la espera HTTP incluye cola del servidor, SQL,
normalización, Storage y otros trabajos. No se midieron spans internos ni tiempos
SQL/Storage por petición; CPU baja por sí sola **no demuestra la causa de W1**.

`docker stats` tomó las siguientes muestras durante creación y drenaje; 100 %
representa un núcleo, por lo que un proceso multihilo puede superar 100 %.
Son picos de muestras espaciadas, no máximos continuos ni desglose por petición.

| Ventana | Muestras web | CPU web pico | Memoria web pico | Memoria worker pico | CPU DB pico |
| ------- | ------------ | ------------ | ---------------- | ------------------- | ----------- |
| W1      | 20           | 13.58 %      | 113.4MiB         | 98MiB               | 25.47 %     |
| W2      | 5            | 50.44 %      | 140.1MiB         | 142.5MiB            | 97.98 %     |
| W3      | 3            | 47.59 %      | 268.1MiB         | 97.53MiB            | 95.01 %     |
| C1      | 4            | 132.37 %     | 223.3MiB         | 97.51MiB            | 186.45 %    |
| C2      | 3            | 13.66 %      | 212.5MiB         | 94.66MiB            | 12.80 %     |
| C3      | 3            | 73.87 %      | 134.4MiB         | 95.68MiB            | 12.13 %     |

Los máximos observados caben en los límites web/worker, pero el muestreo breve
puede omitir picos. El orden referencia→candidato no fue aleatorizado y los resets
no limpian cachés del host/PostgreSQL: la comparación es evidencia local de
ingeniería, sin atribución causal exclusiva a los hilos ni garantía de estabilidad
sostenida. La referencia es compatible con defaults de Gunicorn/contenedor y
`WEB_CONCURRENCY` remoto vacío; **no se inspeccionó el proceso web remoto efectivo**.
No se extrapolan estos números a Heroku/Supabase, RTT remota o fotos de producción.

## Custodia, reproducción y verificación

Artefactos privados:
`~/.local/share/ciudadania-activa/s15/20261020/performance/out/s15/`.
Incluyen actores recientes, manifiestos, snapshots exactos, SQL antes/después,
series k6, recibos, stats, comandos y scripts de orquestación reproducibles.
Los archivos son 0600, directorios 0700 y scripts/wrapper 0700; el wrapper aplica
`umask 077` dentro de k6 Docker. Los originales diagnósticos de preparación y
restauración se conservan en sus carpetas previas.

Los 575 archivos backend/tools congelados coincidieron antes de cada fase y en
el recheck final posterior a C3, sin exclusiones. Vector de hashes SHA-256:
`9b989f9990559a2a31b78184dc6ad31c271826c81ae89345cd81f36598a336d3`.
Los cambios posteriores de configuración y postverificador de navegador no se
atribuyen retroactivamente a estas mediciones.

`preflight`, `manifest.snapshot.json`, `execution.json`, resumen y puntos
`s15_started`/`s15_outcome` conservan el SHA de los bytes exactos del manifiesto;
k6 lo recalcula antes de HTTP. SQL debe aportar el mismo hash para creación.
Editar `purpose`/`environment`, aun manteniendo runId/dataset, invalida la unión.
Las series antiguas sin binding solo producen diagnóstico no promovible; no se
migraron hashes anteriores. `promotable:true` significa vínculo local completo,
no que todos los umbrales ni un RNF productivo hayan pasado.

| Ventana | SHA-256 manifiesto                                                 | SHA-256 informe reconciliado                                       |
| ------- | ------------------------------------------------------------------ | ------------------------------------------------------------------ |
| W1      | `42ea07c113f5fd23087683191aa5fb72f77db9ee8e2411853e5fb59b94ff6cc5` | `f5474f902952ed80c864d94eefa2888b0f452714043fa9cb0464ea62aafd4e6e` |
| W2      | `e001ea66a84a0d2f7f24f24b0d3d7d1793ebf18c59efd0b5f5bd8673bb43bd26` | `c64d39b6ab216c443f884a9d09711e226882b2b45cf7097852af31ac3e29a15f` |
| W3      | `370c7490e54db2cd05bf11e768544e2685d9d4d085c3be611f5c99410083b9d5` | `0efcd683b1323a74426ab34e1c1e3fd685a7a5160ac6b683c53c8f7a8cb67c15` |
| C1      | `1fba7fa29a5d661ceed9d433867b3a82b012407d172aa7e7220b052d5cbe1f0e` | `54257fca5004255d97d377a3383a416402fff8681f137d30da90b3c004a0d113` |
| C2      | `5d73f3be0f7568ceba7c39c4e92906155af865fe15dbf7b40377ddda21f0980b` | `6fea5abaee7a33b4df0f8a3a8f12352c995ecf6232c18d21e4bc55212b7f48dc` |
| C3      | `a46b649b71a2607f085367da59b060eff647054ea832d36f70dd2f32d31eaa98` | `893d6e62ec38404fdfb55b277167af5e62d3a89a0851197d34d3c64d76bafe38` |

Resumen privado consolidado SHA-256:
`41e5c87b01c7fa6f85d4990dddfbe2a88f9f0facb0a83e9b786640304eeabe20`.
Índice privado `artifact-index.json` (1.779 archivos), SHA-256:
`361393c91cb0f2e3045ac5c20bd51cabecf8db7d2ea814cfe237b3aa5193897b`.
El índice incluye informes de lecturas y negativas, scripts,
SQL, recibos y las incidencias conservadas. No se publican sus sesiones ni datos.

Comandos de reproducción del instrumento, con archivos/entornos nuevos y guardados:

```bash
node --test tools/performance/tests/*.test.mjs
node tools/performance/cli.mjs preflight /ruta/privada/create-manifest.json
node tools/performance/cli.mjs run /ruta/privada/create-manifest.json create --out /ruta/privada/nueva-corrida --k6 /ruta/privada/k6-docker-measure
node tools/performance/cli.mjs report /ruta/privada/create-manifest.json /ruta/privada/nueva-corrida/series.jsonl --profile create --sql /ruta/privada/sql-after.json --out /ruta/privada/informe.json
```

La restauración, emisión de actores, SQL, worker y recolector se ejecutan fuera del
CLI con los comandos guardados en el índice privado. No repetir estas escrituras
sobre una copia consumida. El [README del instrumento](../../tools/performance/README.md)
detalla contratos, poblaciones, cuotas y modo diagnóstico.

Verificación del instrumento: 37 pruebas Node, incluyendo cambios de metadatos,
hashes inválidos/parciales de series y SQL, formato viejo no promovible, igualdad
estructural y una ejecución nueva legítima; formato Prettier y sintaxis Node.
La revisión independiente cerró los hallazgos Critical/Important del binding antes
de iniciar estas ventanas. El informe de resultados se remite a revisión por separado.

## Suplemento independiente: tres canjes positivos y controles de duplicación

Después del cierre del índice de 1.779 archivos se ejecutó **otro mundo**,
`ca-s15-redeem-*`, con red interna, DB/Redis/objetos propios. No se consumió ni
alteró el benchmark anterior. El nuevo ensayo es `harness-smoke`, N=3 exploratorio;
no se mezcla con la población de creación ni adjudica RNF-02 o P99 poblacional.
Protocolo privado `prospective-protocol-002.json`, fijado antes de ejecutar:
`eb610f0c90ae705573d5d3f26e053758c28a9cd8e7287c29912d353ae9d40feb`.
La revisión previa corrigió readiness TCP y restauración sobre template0, antes
de crear este mundo. No hubo una corrida bajo la fuente anterior.

La restauración original pasó **1.329/1.329** antes de consumirla. C09, C10 y C11,
sin canje mensual en Municipalidad, ANDE y ESSAP respectivamente, partían de diez
créditos legítimos cada uno. **30 reportes nuevos** financiados por creación,
validación, evidencia y primera resolución mediante servicios llevaron cada saldo
a **1.010**. SQL independiente comprobó los cuatro estados de historial, ciclo 1,
una evidencia y los movimientos +20/+30/+50 de cada reporte. No se insertó ledger
directamente ni se cambió reloj o cupo.

Runtime: Gunicorn 22, un proceso gthread con cuatro hilos, DEBUG=False,
web 2 CPU/1 GiB; k6 1 CPU/512 MiB, loopback sin puertos publicados.
Worker/Beat detenidos y ningún proveedor real. Los tres canjes retornaron HTTP 201:

| Caso | HTTP | Latencia HTTP ms |
|---|---:|---:|
| positive_redeem_1 | 201 | 135.370 |
| positive_redeem_2 | 201 | 133.971 |
| positive_redeem_3 | 201 | 138.289 |

Mediana **135,370 ms**; rango **133.971–138.289 ms**.
El reporter conserva su interpolación P99 de 138,231 ms como dato exploratorio,
con su advertencia explícita; tres observaciones no caracterizan la cola.

Por caso, el UUID HTTP coincide con un único canje SQL de 1.000 créditos y
remanente **10**. Hay un débito acreditado −1.000, impacto sin cambio, auditoría
exacta y una notificación `CANJE_REALIZADO` pendiente, cero intentos. Se compararon
hashes de todas las filas previas de **diez tablas**: solo se añadieron tres filas
en cada una de `reward_redemptions`, `reward_transactions`, `notifications` y
`audit_logs`; ninguna fila previa cambió. El export SQL usa transacción
`REPEATABLE READ READ ONLY` y no depende de serializers/selectores de negocio.

Después, otros **tres intentos** recibieron `409 MONTHLY_LIMIT_REACHED`. La segunda
población tiene manifiesto propio y no amplía N positivo; SQL completo antes/después
idéntico en las diez tablas. Ambos reportes conservaron binding `BOUND`, tres
resultados esperados, cero errores y exit 0. Las notificaciones siguen pendientes:
no se observó envío real ni drenaje en este suplemento.

Artefactos privados en `performance-positive/out/s15/`:

- Manifiesto positivo: `09a5738b0555330eb032894f3c38b3bf8e61bbee32871e7f3130011d79b44646`.
- Conciliación SQL positiva: `bac6cf60f7301428d8317c474c294ebcc8f2874040286cfa4d583576b4bfe055`.
- Controles de duplicación: `b7fde16fb488ae843855fe0bce6978a1801957205eaaf3ceab1fc859ce8869f7`.
- Índice de 255 archivos: `207b62c783dfdec274676d33379d1b566782132c49781b1753a82ae7496ba09e`.

Se conservaron dump final validado, objetos, snapshots SQL y series completas. Los
índices y resumen del benchmark anterior conservaron sus hashes originales después
de este suplemento; no se reetiquetó ni sobrescribió su corte.


### Incidencia operativa posterior al corte

La relectura final del índice cerrado encontró **1.778/1.779 rutas coincidentes**:
el log Gunicorn de C3 pasó de cero a 218 bytes después del corte, con timeout a
20:31:51 UTC y salida de worker a 20:31:53. Las 50 respuestas de C3 terminaron a
20:14:55 y sus tareas a 20:14:57; sus series/recibos conservan hashes y resultados.
La causa del timeout no está establecida y no se atribuye automáticamente a gthread.
No se acredita estabilidad prolongada ni recuperación a partir de ventanas cortas.
Antes de publicar el cambio de runtime se requiere comprobar esos aspectos.

El [índice de validación](S15_INDICE_VALIDACION.md) documenta el log vivo, copia
inmutable posterior y recibo de la diferencia. No se modificó el índice antiguo ni
se truncó el log para forzar un hash. La revisión independiente confirmó las otras
1.778 coincidencias y el orden temporal. No hubo despliegue del ajuste.
