# Sprint 15 Informe de preparación local

**Seguimiento vigente:** [validación y estado actual](S15_INFORME_VALIDACION.md). Este enlace distingue la segunda etapa de los cortes anteriores conservados en este documento.

**Fecha:** 20/10/2026. **Protocolo:** `s15-local-v1`.
**Base:** `3d891824c9c56d166c62c728dff4119bfeb6742e` más cambios locales de
`feat/s15-validation`, sin commit ni publicación. **S15 sigue en ejecución.**

**Decisiones posteriores:** [alcance y pendientes actualizados](S15_DECISIONES_Y_PENDIENTES.md).
Se elimina la exigencia de otro entorno remoto y se traslada la incorporación del `.docx`
para después de S16. Las cifras y límites de las ejecuciones de este informe se conservan.

Se revisó el plan completo contra el cierre integrado de S14 y se implementaron los
instrumentos locales autorizados. El usuario confirmó que faltan fotos inéditas con
etiquetas de oro y un entorno remoto de ensayo. Los resultados de esta etapa prueban
el funcionamiento de herramientas y casos sintéticos; las mediciones finales conservan
las entradas y condiciones pendientes de la [apertura](S15_APERTURA_Y_DEPENDENCIAS.md).

## Implementación

| Componente | Comportamiento y alcance |
|---|---|
| [Dataset funcional](../../backend/apps/core/final_dataset/README.md) | Generación por servicios de dominio, reloj acotado, eventos `on_commit` capturados, clasificación inyectada y medios sintéticos persistentes; replay de solo lectura |
| [Evaluación IA](../../tools/evaluation/README.md) | Formatos separados de fotos/oro/exclusiones/respuestas, validación offline, parser de producto, puntuación exacta antes de policy, replay/resume/rescore y errores explícitos |
| [Carga local](../../tools/performance/README.md) | Preflights, diez ciudadanos × cinco creaciones, lecturas limitadas por scope, canjes/negativas, series correlacionadas y reporte sin mezclar errores con latencias exitosas |
| [Entorno y reconciliación](../../tools/s15/README.md) | Runner aislado, Storage REST privado persistente y export SQL PostgreSQL readonly antes/después, sin derivar la persistencia de las respuestas HTTP |
| CI | Nueva compuerta offline de herramientas; la compuerta backend existente incorpora los tests del dataset. El workflow aún no se ejecutó remotamente |

No se cambiaron endpoints, modelos ni migraciones del producto. Los comandos de dataset
son explícitos: no se ejecutan durante el arranque de la aplicación. La generación exige
una base dedicada vacía y rechaza datos parciales o destinos incompatibles.

## Aislamiento observado

Se usaron PostGIS 17/PostGIS 3.5 y Redis 7 en `ca-s15-local`, red Docker interna.
`check_local.sh` monta el checkout readonly, oculta `.env` y fija configuración sintética.
Las dos fixtures existentes que prueban el adaptador S11 escriben en un tmpfs descartable;
ningún archivo congelado de S11 se modificó por ese montaje.

La restauración y el HTTP usaron otra red interna, `ca-s15-restore-local`, con sus propias
DB/Redis. `ca-s15-web` ejecutó Django 5.2.18 con `DEBUG=True` y Storage en loopback;
k6 compartió su namespace de red. No hubo workers ni Beat, llamadas IA, Google, Brevo,
Supabase real ni tráfico hacia producción. El encolado local de creación no acredita
ejecución de tareas ni entrega de avisos. No se midió el BFF de Next.js.

Los artefactos privados persisten fuera del checkout:

- Mundo original y pruebas: `~/.local/share/ciudadania-activa/s15/20261020/out/s15/`.
- Restauración y carga: `~/.local/share/ciudadania-activa/s15/20261020/restoration/out/s15/`.

Los JWT están en exports separados del manifiesto, emitidos con el reloj real. No se
versionan volcados, objetos, sesiones ni URLs firmadas. El [índice](S15_INDICE_EVIDENCIAS_LOCAL.md)
publica hashes y localizadores relativos, sin credenciales.

## Dataset funcional y restauración

Receta `s15-functional-v1`, semilla 15, fecha de referencia 20/10/2026 y ventana de
60 días en `America/Asuncion`. Contiene 36 reportes, 12 ciudadanos aprobados,
tres operadores y un administrador; 32 confirmaciones, tres canjes de 1.000 y los
cuatro estados del ledger. Incluye financiación legítima, remanente de 50,
reversión posterior al canje con saldo −100, revalidación en otro ciclo, rechazo,
autocierre, fronteras mensuales e ingesta X simulada. Las fotos no contienen personas.

El verificador final del mundo original pasó **1.257/1.257 controles**, con auditoría
obligatoria **268/268** y notificaciones **116/116**. Compara SQL y bytes con el
oráculo declarativo, sin usar selectores del producto para construir sus expectativas.
Los avisos siguen pendientes: sus filas no demuestran entrega. El replay no repara
ni vuelve a financiar saldos; ante divergencia falla.

Se conservaron un dump lógico completo y los objetos. Se restauraron en una DB nueva
creada desde `template0`, con Redis separado y procesos de aplicación detenidos. La
verificación de esa restauración pasó **1.225/1.225 controles** con la versión del
verificador anterior al refuerzo de enlaces de auditoría. Después se utilizó esa
copia para el ensayo HTTP; el mundo original de 36 reportes permanece conservado.
La cifra 1.257 corresponde a la verificación final del original, no a una segunda
verificación del mundo restaurado antes de consumirlo.

El primer intento de restauración falló porque el readiness sobre socket Unix coincidió
con el servidor temporal de inicialización de PostGIS. El reinicio requirió recuperación
del WAL; un plazo de espera insuficiente también se conservó como intento fallido.
Se esperó finalmente respuesta TCP real y se reconstruyó exclusivamente la DB de destino
antes de repetir `pg_restore --exit-on-error`. La guía incorpora el readiness TCP y exige
comprobar su éxito antes de continuar. No se borró el origen para solucionar el ensayo.

## Ensayo HTTP de creación

Se ejecutó **k6 v2.3.0** sobre Django de desarrollo y la copia restaurada, con diez
ciudadanos, cinco fotos PNG distintas por persona y una barrera monotónica común.
Se mantuvieron las cuotas del producto y no hubo reintentos automáticos.

| Indicador observado | Resultado de `s15_http_smoke_20261020_1` |
|---|---|
| Casos esperados / iniciados / completados | 50 / 50 / 50 |
| Respuestas válidas | 50; HTTP 201, estado y recompensa pendiente 20/20 correctos |
| Errores inesperados o respuestas ausentes | 0 / 0 |
| Persistencias reconciliadas dentro de 60 s | 50 |
| P50 / P99 de los 50 éxitos | 222,07 ms / 496,81 ms |
| Método de percentiles | Interpolación lineal en `(N−1)×p` |
| Resultado del reporter con SQL | `LOCAL_RECONCILED`, sin persistencias tardías ni sin respuesta |

El export SQL se obtuvo en transacciones `REPEATABLE READ READ ONLY`, antes y después
del HTTP. Vincula caso, ciudadano, descripción, hash original y UUID con una fila nueva
de reporte, media, historial inicial y evento pendiente de 20/20. El baseline y los
exports están vinculados al manifiesto y al mapa de actores por hashes.

El primer reporte sin SQL quedó correctamente como `NOT_DEMONSTRATED`. Al importar
la serie creada por Docker se detectaron permisos 0644 y el preflight la rechazó;
se restringieron a 0600 dentro de su directorio privado 0700 antes de reconciliar.
Las siguientes ejecuciones deben fijar `umask 077` también dentro del contenedor k6.

Una ventana de un servidor de desarrollo con sustitutos demuestra el circuito del
instrumento. No demuestra capacidad sostenida, una cola P99 poblacional, ejecución
asíncrona, rendimiento Heroku/Supabase ni los RNF finales. Los objetivos originales
P50 ≤800 ms, P99 ≤2.000 ms y ≥50 reportes/min permanecen vigentes para la medición final.

## Ensayos de lectura

Se congelaron expectativas mediante SQL independiente readonly antes de las lecturas,
sin usar las respuestas HTTP como oráculo: copia consumida con 86 reportes, 85 públicos
en el bbox, página de 20 y Top 10 mensual sobre 12 perfiles con las cuatro preferencias
de privacidad. Mapa y ranking usan manifiestos/runIds separados, sin repetir creación.
TTL declarado de 60 s y caché `unverified`: no se etiquetan como fría ni caliente.

La primera ventana de mapa devolvió 90 respuestas HTTP 200, pero el instrumento produjo
nueve falsos fallos en detalle. Se preserva como **INVALID_INSTRUMENT**. Un diagnóstico
sobre el mismo JSON encontró diferencias de orden de claves al serializar objetos de
k6; sus valores eran iguales. Se reemplazó `JSON.stringify` por igualdad estructural
estricta: ignora el orden de claves, mantiene tipos, campos exactos y orden de arrays.
Se añadieron dos regresiones y se repitió el ensayo con el mismo oráculo SQL.

La ventana corregida de mapa pasó **90/90**, sin errores: 30 listados, 30 detalles y
30 consultas de categorías. Los percentiles por ruta figuran abajo. Un arranque posterior
de ranking abortó antes de HTTP porque una normalización de permisos del integrador
quitó el bit ejecutable al wrapper Docker privado; se conservó ese intento y se corrigió
únicamente el permiso del wrapper antes de una nueva corrida.

| Población local | N | P50 ms | P99 ms |
|---|---|---|---|
| Listado de mapa | 30 | 4,45 | 38,78 |
| Detalle público | 30 | 38,31 | 45,10 |
| Categorías públicas | 30 | 21,13 | 63,20 |
| Ranking mensual | 90 | 22,95 | 28,45 |

La nueva corrida de ranking pasó **90/90**, sin errores, contra el Top 10 y las
preferencias congelados por SQL. El intento abortado previo no emitió solicitudes;
el runId identifica la misma población y el directorio `ranking-window-2` identifica
la ejecución efectiva.

Se respetó el presupuesto conjunto de mapa por IP, incluidos los diagnósticos. Son
lecturas de ingeniería del entorno local; no constituyen mediciones finales de RNF
ni una prueba independiente de hit/miss de caché.
Las consultas SQL posteriores conservaron cardinalidades y oráculos de mapa/ranking.
La actividad paralela del mismo host no estuvo controlada como en un ensayo de capacidad.
Al finalizar se detuvo `ca-s15-web`; DB, Redis y artefactos quedan conservados para revisión.

## Evaluación IA offline

La suite final tiene **28 pruebas**. Cubre fotos y normalización reales del producto,
formatos, conjunto exacto de casos/oro, secretos fuera del request, parser, triple exacto
categoría/institución/prioridad, nulos, casos desconocidos, denominador fijo y frontera
del 85 % sin redondeo. También prueba errores, interrupciones, uso desconocido y custodia
local de intentos.

Se ejecutó además la CLI final en un contenedor `--network none`:
`validate → replay → rescore → resume`. Conservó resultado `REPLAY_ONLY`, **1/4**,
con `missing`, `parse_error` y `timeout`; score e intentos permanecieron idénticos al
recalcular y reanudar. Es una prueba del instrumento con cuatro entradas sintéticas,
no concordancia de IA ni una observación de RNF-05.

## Compuertas y revisión

La verificación completa final se registra en esta tabla y en el índice de evidencia.

| Verificación | Resultado |
|---|---|
| Ruff backend / formato | PASS; 462 archivos formateados |
| Ruff herramientas / formato / sintaxis shell | PASS; 11 archivos Python formateados |
| Migraciones | Sin cambios pendientes |
| Suite backend de dominio y cobertura | **2.347/2.347 PASS**, 181,92 s; producto **94,46 %**, 7.346 sentencias y 407 sin cubrir |
| Cobertura HT5-01 | **96,91 %**, 2.233 sentencias y 69 sin cubrir, en el manifiesto congelado de 33 archivos y sobre los mismos datos de la suite |
| Custodia GOV-T054 | **1/1 PASS** con selección exacta del caso; no equivale a repetir toda la suite experimental |
| IA offline / Storage y SQL / contratos de carga | 28 / 11 / 28 pruebas PASS |
| Dataset + spatial, regresión de fixtures | 31/31 PASS |
| Revisión independiente | Sin Critical/Important restantes en código de preparación local, tras correcciones |

La primera suite completa terminó con 2 fallos y 6 errores: montaje readonly de fixtures
Storage y conflicto de `contenttypes` al combinar nuevas pruebas transaccionales con
las pruebas existentes de rollback serializado. Se corrigieron el montaje del runner y
los marcadores de los tests nuevos; el intento fallido se conserva, sin sumar sus pruebas
a otra ejecución para declarar una pasada completa.

La configuración vigente de cobertura incluye los comandos y herramientas Django nuevos:
por eso crece el denominador de producto respecto de S14. No se excluyeron para mejorar
el porcentaje. El universo crítico de HT5-01 permanece congelado y se informa aparte.

Una ejecución adicional de los tests Node con `umask 077` reveló que su fixture de
permisos inseguros dependía del umask del proceso. Se fijó explícitamente el preestado
0644 en ese test y la suite volvió a pasar. El control del instrumento se conservó:
rechaza archivos accesibles por grupo/otros antes de consumirlos.

La revisión independiente motivó correcciones con regresiones: los errores conocidos del
instrumento IA invalidan la corrida permanentemente; `resume` y `rescore` no convierten
evidencia ausente/corrupta en un resultado válido. El verificador del dataset comprueba
tipo, autor y ciclo de los bonus, IDs de evidencias y movimientos anulados/revertidos.

No se editó frontend; sus controles de lint, tipos, build y navegador no se presentan
como pruebas nuevas de S15. La suite experimental completa tampoco se repitió: el alcance
S15 no modifica ese aparato. Su custodia se comprueba por separado.

## Continuación del sprint

La [matriz](S15_MATRIZ_REQUISITOS_EVIDENCIA.md) distingue lo observado en este primer
corte. Las decisiones posteriores autorizaron usar el despliegue existente y fijaron
presupuesto API USD 5–10: ya no se exige un entorno remoto separado. La segunda etapa
continúa carga, API/SQL, navegador, UX y diagnóstico remoto, con sus resultados efectivos
en el [informe vigente](S15_INFORME_VALIDACION.md). Las fotos/oro aún no se recibieron;
las rotaciones no se presentan como ejecutadas. La incorporación del `.docx` se hará
después de terminar los 16 sprints.

Los resultados históricos de S11 y sus límites se mantienen; estas verificaciones de
ingeniería no los readjudican. S16 y el cierre de S15 no están habilitados por este informe.
