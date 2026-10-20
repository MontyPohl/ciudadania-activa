# S15 — índice de validación de la segunda etapa

Corte: **20/10/2026**, base `3d89182`, rama `feat/s15-validation`.
Los artefactos están en el árbol privado local
`~/.local/share/ciudadania-activa/s15/20261020/`; no se versionan dumps, sesiones,
fotos, vídeos, trazas, contratos de actores ni logs completos.
El [índice de preparación](S15_INDICE_EVIDENCIAS_LOCAL.md) conserva su corte previo.

## Comprobación del corte

`validation-final/out/s15/evidence-index-phase2.json`: **143 archivos**,
reabiertos y comprobados por SHA-256. Incluye resultados completos y fallos
preservados, JUnit, recibos y los índices anidados. SHA-256:
`173e9424424de1832b703aab9263ffedf1c663cd22638efbf29939d1f7468505`.
El recibo `evidence-index-verification.json` registra su verificación.

`source-fingerprint-final.json`: **810 archivos de código/configuración** de
backend/frontend/tools/workflows, obtenidos de Git, incluyendo nuevos no ignorados;
excluye documentación, imágenes y datos privados. Vector SHA-256:
`2153339adb08ea981c50eb2717c732d0c266d1c4f7ff0f8889d16c1b8e1b2e25`.
Se registra base, fecha y hash por archivo. Es el corte final de implementación,
no la afirmación de que cada corrida anterior usó esa versión: carga, API y
navegador conservan sus propias huellas y fuentes anteriores.

| Carril | Artefactos privados principales | Resultado o límite |
|---|---|---|
| Dominio | `validation-final/out/s15/domain-final.log`, JUnit y cobertura HT5 | 2.349 correctas; producto 94,46 % / HT5 96,91 %, universos separados |
| Custodia | `gov-t054-final.log`, JUnit, lint/formato y migraciones | GOV exacto 1/1; sin archivos congelados modificados |
| Herramientas | `tools-offline-final-002.log`, `ia-offline-final.log`, `performance-unit.log`, estilo 002 | 43 auxiliares, 28 IA y 37 Node, sin proveedores |
| Frontend | `frontend-final-*.log`, `frontend-final-checks.json` | Formato/lint/tipos/build y 417 pruebas / seis rutas correctos |
| UX | `ux/accessibility-all.json`, `evidence-index.json`, capturas | 67/67 con API interceptada; no confundir con navegador real |
| API/SQL | `api/out/s15/api-flows-005.json`, dump y preflight 005 | 180/180 sobre restauración 1.329/1.329; fallos previos conservados |
| Navegador | `browser/playwright-002*`, `out/s15/browser-postcheck-003.json` y copias de logs/dump | 9/9 y 63/63; primer postchequeo JSONB fallido y fuente previa conservados |
| Pantallas | `browser/performance-001/artifact-index.json` | 21/21 archivos de esa serie comprobados, 18 navegaciones, proxies explícitos |
| Canjes | `performance-positive/out/s15/artifact-index.json` | 255/255 comprobados; N=3 positivo y tres duplicados, SQL de diez tablas |
| Remoto | `remote/heroku-db-probe-001.*`, `public-http-smoke-002.json` | SQL constante/TLS/readonly y seis rutas públicas; sin escrituras |
| Identidad | `identity-schema-readonly.json`, JUnit y postchequeo de navegador | Esquema/HMAC único, 220 casos dentro del dominio y canario con superficies delimitadas |

## Excepción del log operativo posterior al benchmark

El índice cerrado de carga conserva **1.779 entradas**, SHA-256
`361393c91cb0f2e3045ac5c20bd51cabecf8db7d2ea814cfe237b3aa5193897b`.
Al reabrir todas las rutas se observaron **1.778 coincidencias y una diferencia**:
`candidate-3/gunicorn.log` estaba vacío al corte de las 20:20:17 UTC y posteriormente
recibió 218 bytes, con un timeout de worker a las 20:31:51 y salida a las 20:31:53.
C3 terminó HTTP a las 20:14:55 y sus tareas a las 20:14:57. No se atribuyen esos
mensajes tardíos a la ventana medida ni se ocultan como si el servidor siguiera
sin incidencias después de medir.

Se preservaron el índice original, el log vivo y una **copia inmutable posterior**
`validation-final/out/s15/benchmark-postcut-gunicorn.log`. Su recibo
`benchmark-postcut-custody.json` registra hora, rutas, tamaños/hashes esperados y
observados. La nueva copia está dentro de los 143 archivos verificados; no se
truncó ni reescribió el log para restaurar artificialmente la coincidencia antigua.
El índice anterior conserva su identidad, pero **no se afirma que sus 1.779 rutas
actuales sigan coincidiendo**. Las series medidas y sus conciliaciones sí coinciden.

La causa del timeout tardío no está establecida. Los ensayos cortos acreditan su
propio alcance; estabilidad prolongada y recuperación requieren comprobación antes
de publicar el ajuste Gunicorn. Este corte prepara un PR de revisión y no despliega.

## Lectura y uso

El [informe vigente](S15_INFORME_VALIDACION.md) enlaza resultados por carril;
la [revisión independiente](S15_REVISION_IMPLEMENTACION.md) y la
[revisión de seguridad](S15_REVISION_SEGURIDAD.md) registran sus alcances.
Los hashes públicos permiten localizar/comprobar originales; no vuelven anónimo
el contenido. Una exportación para tesis necesita selección y revisión propias.
No se actualiza todavía el `.docx` ni se cierra S15 con fotos/oro pendientes.


## Suplemento de entrega: CI y recuperación controlada

`validation-final/out/s15/delivery-index.json` añade **18 archivos** de CI y
`runtime-diagnostic/`, reabiertos y comprobados. SHA-256:
`f60ee79211aa77f0008c7b96e5e2889c4116d15d446db96f0df829d5e006b50a`.
No sobrescribe el índice de 143 archivos ni modifica el corte de carga.

El CI de código `bd0b0d5` pasó seis checks en [PR #56](https://github.com/MontyPohl/ciudadania-activa/pull/56):
backend, build, frontend-e2e, auxiliares, preview y comentario automático de Vercel.
Los logs de tres workflows se conservan completos, fuera del repositorio.

El diagnóstico posterior se hizo **solo en `ca-s15-redeem-web`**, con el mismo
perfil local gthread/4, sin tocar C3. Treinta health previos fueron correctos.
Se detuvo deliberadamente con `SIGSTOP` únicamente su worker Gunicorn, sin
escritores activos. El master reemplazó el proceso y health volvió a responder
en **33,055 s**: once sondeos fallidos durante la interrupción se conservan,
seguidos de treinta health correctos. SQL completo antes/después fue idéntico en
las diez tablas examinadas. No hubo proveedor real ni mutación de negocio.

El protocolo 002 y fuente quedaron fijados antes del ensayo. La primera ejecución
falló por una ruta no montada, antes de ejecutar el probe; su log y fuente se
conservan. El presupuesto de 135 s se implementó como iteraciones/timeouts, no un
deadline monotónico estricto; el postchequeo independiente confirmó que la duración
observada quedó dentro de ese presupuesto, sin editar ni repetir la serie.

Esto acredita recuperación **en esa interrupción controlada**, no la causa del
timeout espontáneo de C3 ni estabilidad prolongada. El texto genérico «Perhaps
out of memory?» de Gunicorn tras matar el worker detenido no demuestra OOM;
no se lo usa para explicar el incidente anterior. Los logs nuevos se preservan
en este suplemento; la copia final de logs del índice positivo sigue intacta.
