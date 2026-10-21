# S15 — revisión independiente de implementación

**Corte:** 20/10/2026, rama `feat/s15-validation`, base `3d89182`.
Se aplicaron las skills versionadas de revisión y verificación. El revisor de código
fue distinto de los autores de los instrumentos y de las correcciones de interfaz.
El dictamen final no dejó **Critical ni Important pendientes en el alcance revisado**.
La [revisión de seguridad](S15_REVISION_SEGURIDAD.md) conserva su método y riesgos propios.

## Hallazgos corregidos

| Frontera | Defecto demostrado | Corrección y comprobación |
|---|---|---|
| Recuperación IA offline | Una corrida con fallo conocido del instrumento o intentos ausentes/corruptos podía recuperar una apariencia válida | Invalidación terminal y rechazo de promoción mediante resume/rescore; regresiones y CLI sin red. No constituye evaluación IA |
| Dataset y eventos | El verificador necesitaba vínculos más estrictos de bonus, ciclos, evidencias, movimientos y auditoría | Comparación SQL/oráculo reforzada, con pruebas que corrompen campos aislados y exigen fallo sin reparar ni refinanciar |
| Binding de carga | Editar metadatos de una corrida podía cambiar su propósito sin corresponder a los bytes medidos | SHA-256 de bytes exactos en preflight, snapshot, ejecución, validación k6 antes de HTTP, starts/outcomes y SQL; legado diagnóstico sin promoción retroactiva. Suite Node 37/37 |
| Ubicación e institución | Alterar solo ubicación/SRID o institución no cambiaba el resultado del verificador | Proyección espacial y de institución por caso. Dos regresiones fallaron antes del fix y pasaron después; el original y las nuevas restauraciones pasaron 1.329/1.329 |
| Cliente HTTP local | `urllib` heredaba proxies de entorno aun con destino loopback | `ProxyHandler({})`, sin redirecciones, y contraprueba con proxy configurado; 10 pruebas API correctas. Recorrido real repetido como 005 con el hash final |
| Trazabilidad de navegador | El postchequeo podía aceptar estado/ledger correctos sin historial, evidencia o auditoría del recorrido | SQL independiente de secuencia, ciclo, actores, evidencia y vínculos económicos/auditoría; regresiones de borrado/corrupción, más guards del journey |
| Rutas privadas | Validar una ruta normalizada y leer su texto original permitía el caso `link/../actors.json` | Rechazo de entradas no canónicas y symlinks de ancestros; lectura de la ruta validada. Seis regresiones Node, incluida demostración de dos lecturas distintas antes del fix |
| Journey del navegador | Ocho nombres de flujo no bastaban para descartar fallo tardío o contexto distinto | Coincidencia de dataset, runId, fuente, actores, período e IDs; cierre sin errores y dashboard capturado. El exit 0 de Playwright se conserva como condición independiente |

Los fixes se verificaron antes de aceptar la evidencia correspondiente. Los intentos
fallidos y los resultados de versiones anteriores permanecen identificados; no se
reescriben como si hubieran usado el instrumento corregido.

## Ámbitos de la última pasada

El revisor leyó el diff frontend de contraste, focos, títulos y texto del perfil;
los nuevos perfiles de medición/navegador; postverificador SQL/canario; Storage firmado;
forwarder GET y relay host→Docker interno. Confirmó destinos fijos, proxies desactivados,
límites, ruta privada y ausencia de logs de cuerpos/credenciales en esas fronteras.

También leyó los comandos equivalentes de `backend/Dockerfile` y `backend/heroku.yml`,
que preparan `gthread` con cuatro hilos. Contrastó el
[informe de rendimiento](S15_INFORME_RENDIMIENTO.md) con los seis JSON reconciliados,
sus hashes/percentiles, cuatro reportes de lecturas, negativas y recheck de las 575
fuentes congeladas. Conservó W1 incumplida y los límites del canje/caché. El cambio
de comando se preparó después de las mediciones; no implica despliegue de Heroku.

El protocolo descriptivo de frontend se revisó sin ejecutar Chromium. Distingue
18 navegaciones locales, ventana de ocho segundos, candidatos LCP/suma de shifts
como proxies, nulos y errores; no estima INP, TBT, P99 ni P75 de usuarios.

El revisor ejecutó comprobaciones offline propias: 37 pruebas del harness en la
revisión de binding, 10 de API tras el fix de proxy, ocho pruebas puras del soporte
de navegador, seis de rutas y dos del relay, además de sintaxis y diff. Estos conteos
se superponen con las suites de los autores: **no se suman como casos nuevos**.
No ejecutó carga, DB, conexiones remotas, browser/build ni proveedores externos.

## Alcance del dictamen

La aprobación de código no sustituye ejecuciones. Los resultados efectivos y pendientes
se registran en el [informe vigente](S15_INFORME_VALIDACION.md), con fuentes por carril.
Las trazas, vídeos, contratos de sesiones, dumps, objetos y canario son privados; una
revisión satisfactoria no habilita publicarlos completos.

Esta revisión no cierra S15, no adjudica hipótesis históricas, no demuestra concordancia
IA ni delivery real, y no ejecuta rotaciones o publicación. Los riesgos heredados de
S14-D18/D19/D20 siguen diferenciados de los defectos nuevos corregidos.


## Revisión posterior de los resultados finales

Otra pasada independiente reprodujo en memoria el fallo del cursor JSONB con la
fuente preservada y la fixture de auditoría: nueve fallos con la versión anterior,
cero con la corrección. Ejecutó las dos regresiones nuevas y contrastó 002 frente a
003, **63/63** sobre los mismos artefactos. No hubo cambios de producto para aprobar.

Contrastó las 18 muestras, sus resúmenes, fuente y hashes de entradas con el informe
de frontend. Confirmó medianas/rangos, proxies y la limitación observada del TTFB
móvil; no dejó Critical/Important. Detectó un Minor de filtros CI: se amplió a
`backend/**` en PR/push para incluir fotos, contratos, configuración y dependencias.
La compatibilidad Node 22 del import TypeScript y sintaxis del script quedó probada.
Los resultados de revisión se superponen con las suites y no aumentan sus conteos.


La pasada posterior también contrastó el suplemento de tres canjes contra HTTP/SQL,
verificó las 255 entradas de su índice y cerró el Minor de CI. Al reabrir las 1.779
rutas del benchmark encontró la misma excepción del log vivo tardío: **1.778 iguales**,
un timeout posterior al corte, con causa no establecida. Confirmó que HTTP/tareas de
C3 terminaron antes. El [índice actual](S15_INDICE_VALIDACION.md) preserva esa incidencia;
preparar el PR no acredita estabilidad prolongada ni autoriza el despliegue.


La revisión del diagnóstico de recuperación posterior confirmó los 33,055 s,
30 health previos/posteriores, once fallos controlados y el índice positivo intacto.
Señaló que el presupuesto por iteraciones no era un deadline estricto; un
postchequeo independiente comparó el tiempo observado con 135 s. Se documenta ese
límite sin repetir o cambiar la serie. El mensaje genérico de SIGKILL no demuestra
OOM ni explica el incidente espontáneo. El dictamen sigue limitado a preparar el PR.
