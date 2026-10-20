# S15 — Revisión de seguridad de la preparación local

**Fecha:** 20/10/2026. **Base:** `3d89182`, rama `feat/s15-validation`, con
cambios locales sin commit. **Estado:** revisión estática inicial ampliada a los
ajustes de medición, correo sustituido, observación del worker, ejecutor API/SQL
y diagnóstico remoto de consultas constantes. La ampliación final incorpora
preparación de navegador, relay y forwarder locales, Playwright, medición
descriptiva del frontend y los dos comandos de arranque `gthread/4`. Los cambios
de carga y UI conservan además sus revisiones independientes específicas.

La revisión no identificó defectos nuevos críticos ni altos en las fronteras
examinadas. Esto permite continuar las comprobaciones locales; no certifica el
despliegue ni cierra los riesgos heredados de S14. Los secretos, el estado remoto,
el envío real de correo y los proveedores externos no se inspeccionaron ni se
ejecutaron durante esta revisión.

## Alcance y método

Se aplicaron las skills versionadas
[security-best-practices](../../.agents/skills/security-best-practices/SKILL.md) y
[requesting-code-review](../../.agents/skills/requesting-code-review/SKILL.md),
con las referencias Django, Next.js y React pertinentes. El revisor es distinto
de los implementadores originales. La revisión fue de solo lectura, con una
excepción posterior autorizada por el integrador: la corrección mínima de
S15-SEC-05 y su contraprueba. Esa corrección se entrega también al revisor final
independiente. No se ejecutaron pruebas contra la base compartida, llamadas
remotas, consumo de cuotas, rotaciones ni operaciones S11 desde esta revisión.

El alcance incluye `backend/apps/core/final_dataset`, sus management commands,
`tools/evaluation`, `tools/performance`, `tools/s15` y el nuevo workflow
`ci-s15-tools.yml`. Se inspeccionaron además las fronteras existentes de permisos,
identidad, mapa público, JWT, cookie del BFF, Storage y ajustes de producción.
No se leyeron `.env`, respaldos, credenciales ni datos de personas reales.

La ampliación cubre específicamente `local_profile.py`,
`measurement_settings.py`, `mail_server.py`, `worker_receipts.py`,
`remote_db_probe.py`, `api_flows.py` y las pruebas de perfil, correo, probe y
recorridos. Se inspeccionó el código de pruebas; las corridas generales se
acreditan en el informe del integrador. El revisor reprodujo la selección de un
proxy ficticio en memoria y, tras corregirla, ejecutó las diez pruebas unitarias
del instrumento API sin Django/DB/proveedores. Las diez pasaron; Ruff y formato
de los dos archivos modificados pasaron en la imagen de pruebas existente con
`--network none`.

La ampliación final de solo lectura incluye `browser_profile.py`,
`browser_settings.py`, `browser_seed.py`, `storage_forwarder.py`,
`browser_relay.py`, `test_browser_support.py`,
`frontend/e2e-s15-fullstack/**`, `frontend/playwright.s15-fullstack.config.ts`,
`frontend/scripts/s15_page_performance.mjs` y los cambios de arranque de
`backend/Dockerfile` y `backend/heroku.yml`. No se ejecutaron servidores,
navegadores, cargas ni operaciones Docker de inspección desde esa ampliación;
se inspeccionaron sus fuentes y pruebas. No equivale a una segunda auditoría
exhaustiva de todos los componentes visuales modificados.

No se realizó análisis remoto de dependencias ni una prueba de penetración. Las
conclusiones se refieren al código observado y al alcance local sintético.

## Controles observados en los cambios S15

| Frontera | Evidencia inspeccionada | Resultado y límite |
|---|---|---|
| Generación y acceso SQL | [isolation.py:11](../../backend/apps/core/final_dataset/isolation.py#L11), [generator.py:48](../../backend/apps/core/final_dataset/generator.py#L48) | Lista cerrada de DB/host/PostGIS, Redis dedicado `/0`, Storage local, buckets S15 y directorio privado; la generación exige DB vacía y confirmación de procesos detenidos. Esta última es una declaración del operador, no detección de procesos. |
| Bloqueo de efectos externos durante generación | [runtime.py:35](../../backend/apps/core/final_dataset/runtime.py#L35) | Storage se sustituye explícitamente, tareas se capturan y las peticiones `requests` se bloquean durante el contexto. La red interna del ejecutor añade la frontera de red; el parche de Python no se presenta como sandbox de cualquier biblioteca. |
| Ejecutor Docker | [check_local.sh:15](../../tools/s15/check_local.sh#L15) | Requiere recursos etiquetados y red interna; checkout de solo lectura, `.env` enmascarado, configuración sintética y claves de proveedores vacías. El ejecutor no se emplea para ejecutar código no confiable. |
| Archivos del dataset | [files.py:13](../../backend/apps/core/final_dataset/files.py#L13), [files.py:54](../../backend/apps/core/final_dataset/files.py#L54) | JSON temporal y objetos con `0600`, publicación atómica del JSON, rechazo de rutas fuera del almacenamiento y symlinks. Los hashes comprueban integridad accidental; no autentican al autor de un manifiesto modificado junto con su hash. |
| Sesiones de ensayo | [export_final_dataset_actors.py:31](../../backend/apps/core/management/commands/export_final_dataset_actors.py#L31) | Solo bajo el guard local; cuentas activas `s15-…@example.test`, sesiones privadas fuera del dataset congelado. El archivo contiene bearer y refresh: se conserva como evidencia privada, nunca como anexo publicable. |
| SQL antes/después | [reconcile_creation.py:56](../../tools/s15/reconcile_creation.py#L56), [reconcile_creation.py:397](../../tools/s15/reconcile_creation.py#L397) | Entradas/salida privadas en `/out/s15`, sin symlinks ni sobrescritura, consultas parametrizadas y transacción `REPEATABLE READ READ ONLY` comprobada en PostgreSQL. No toma un resultado HTTP como prueba de persistencia. |
| Storage sintético | [storage_server.py:20](../../tools/s15/storage_server.py#L20), [storage_server.py:117](../../tools/s15/storage_server.py#L117), [storage_server.py:181](../../tools/s15/storage_server.py#L181) | Escucha solo en loopback, exige credencial sintética, restringe buckets y rutas, limita subida a 5 MiB, no sobrescribe, firma bucket/clave/caducidad y limita la emisión a 300 s. No registra peticiones ni capacidades. Su comprobación de firma/MIME es un sustituto de ensayo, no el validador de imágenes del producto ni un servicio para Internet. |
| Privacidad del request IA | [s15_common.py:407](../../tools/evaluation/s15_common.py#L407), [s15_common.py:487](../../tools/evaluation/s15_common.py#L487) | Lista blanca de descripción, categoría/institución inicial, catálogos e imagen normalizada; no se envían oro, identidad, rutas, procedencia ni metadatos de custodia. La revisión humana de texto/foto sigue siendo necesaria para detectar datos personales dentro de su contenido. |
| Opt-in y evidencia IA | [s15_run.py:183](../../tools/evaluation/s15_run.py#L183), [s15_run.py:435](../../tools/evaluation/s15_run.py#L435), [s15_common.py:517](../../tools/evaluation/s15_common.py#L517) | Modo por defecto offline; live exige bandera explícita, conjunto reservado y catálogo compatible. Clave por entorno, mensajes estáticos, respuestas minimizadas y archivos privados fuera de repo/temporales. No existe un tope monetario automático; el presupuesto requiere controlar la corrida y el proveedor. |
| Integridad de corrida IA | [s15_run.py:278](../../tools/evaluation/s15_run.py#L278), [s15_run.py:339](../../tools/evaluation/s15_run.py#L339) | Errores conocidos del instrumento invalidan la corrida y se rechaza su promoción por `resume`; el lock evita dos escritores. Un hash de evidencia no equivale a firma de autoría. |
| Destino de carga y credenciales | [contracts.js:47](../../tools/performance/lib/contracts.js#L47), [runtime.js:7](../../tools/performance/k6/runtime.js#L7), [runtime.js:36](../../tools/performance/k6/runtime.js#L36), [cli.mjs:108](../../tools/performance/cli.mjs#L108) | Solo HTTP loopback y puerto explícito; redirects desactivados y rechazo de HTTP debug; entorno reducido al ejecutar k6. JWT, sesiones y series se conservan en archivos privados. La comprobación offline de claims no sustituye la autenticación real del preflight HTTP. |
| Workflow nuevo | [ci-s15-tools.yml:20](../../.github/workflows/ci-s15-tools.yml#L20) | Permisos `contents: read`, sin `pull_request_target`, sin credenciales ni llamada real de IA; pruebas offline. No se afirma ejecución de CI remota durante esta revisión. |

## Ampliación: medición, worker y recorrido API/SQL

| Componente | Evidencia inspeccionada | Resultado y límite |
|---|---|---|
| Perfil previo a la carga de Django | [local_profile.py:10](../../tools/s15/local_profile.py#L10), [measurement_settings.py:7](../../tools/s15/measurement_settings.py#L7) | Se exigen DB/Redis/Storage/correo locales exactos y claves sintéticas, con proveedores reales y S11 apagados. El guard se repite tras importar `base` para detectar variables cargadas desde `.env`. No se imprimen los valores rechazados. |
| Ajustes de medición | [measurement_settings.py:14](../../tools/s15/measurement_settings.py#L14) | `DEBUG=False`, hosts acotados, CORS cerrado, proveedor IA/Google/S11 desactivados, TTL explícitos y Beat sin agenda. El transporte HTTP y las cookies sin `Secure` corresponden exclusivamente a loopback/red Docker interna; este módulo no modifica `prod.py` ni debe desplegarse. La red interna es una condición operativa adicional, no algo probado por un string del entorno. |
| Doble de Brevo | [mail_server.py:18](../../tools/s15/mail_server.py#L18), [mail_server.py:37](../../tools/s15/mail_server.py#L37), [mail_server.py:63](../../tools/s15/mail_server.py#L63) | Solo loopback y clave sintética, salida privada exclusiva `0600`, bloqueo contra symlinks, cuerpo máximo 1 MiB y autenticación antes de procesarlo. No envía correo ni conserva destinatario, asunto o texto: almacena ordinal, fecha y hash. La aceptación `201` no demuestra entrega externa. |
| Recibos del worker | [worker_receipts.py:35](../../tools/s15/worker_receipts.py#L35) | Solo dos nombres de tarea, validación del perfil antes de escribir y campos minimizados UUID/estado/tiempo; no guarda args, kwargs, resultados ni excepciones. Destino privado en `/out/s15`, `O_NOFOLLOW` y comprobación de permisos del descriptor. `SUCCESS` prueba que la tarea terminó, no que se clasificó con IA ni que se entregó correo real. |
| Recorridos API/SQL | [api_flows.py:47](../../tools/s15/api_flows.py#L47), [api_flows.py:890](../../tools/s15/api_flows.py#L890), [api_flows.py:972](../../tools/s15/api_flows.py#L972) | Exige URL loopback exacta, marca de copia exclusiva, perfil de medición, Gunicorn y proveedores apagados; SQL parametrizado, preestado por servicios y reporte exclusivo `0600`. No registra headers/bodies en los recibos HTTP y redacta la ruta de capacidades de evidencia. Los checks guardan valores observados y pueden contener nombres sintéticos, UUID y código de vale: el JSON completo sigue siendo privado y requiere minimización antes de publicarlo. |
| Diagnóstico Heroku ↔ Supabase | [remote_db_probe.py:19](../../tools/s15/remote_db_probe.py#L19), [remote_db_probe.py:42](../../tools/s15/remote_db_probe.py#L42), [remote_db_probe.py:52](../../tools/s15/remote_db_probe.py#L52) | Requiere bandera, ajustes productivos y dyno `run.*`, `DEBUG=False` y host Supabase; acota muestras/conexiones y timeouts. Ejecuta únicamente constantes y versiones, dentro de transacciones `READ ONLY` comprobadas y rollback explícito; revalida tras cada reconexión. La salida omite host, DSN, usuario, claves y datos de aplicación. Impone como mínimo `sslmode=require`: cifra la conexión, pero ese valor no acredita por sí solo verificación del nombre/certificado del servidor. |

El diagnóstico remoto se revisó sin ejecutarlo desde esta auditoría. El
integrador realizó la ejecución documentada en
[S15_DIAGNOSTICO_REMOTO.md](S15_DIAGNOSTICO_REMOTO.md); se distingue de una prueba
realizada por este revisor. Los mensajes de fallo se
reducen a texto fijo; no se publica el traceback de una excepción de conexión.
Su mediana/P99 describen consultas pequeñas desde un dyno temporal y no prueban
latencia de red pura, SLA HTTP ni capacidad sostenida.

### S15-SEC-05 — Baja, corregida: el cliente API heredaba proxies del entorno

**Ubicación:** [api_flows.py:149](../../tools/s15/api_flows.py#L149), constructor
de `Runner`, que antes creaba `build_opener(NoRedirects())`.

**Evidencia:** la construcción incluye el `ProxyHandler` predeterminado de
`urllib`. El revisor configuró `http_proxy` con un destino ficticio y vació
`NO_PROXY`/`no_proxy`, sin hacer ninguna petición: el opener resultante conservó
el proxy HTTP. Validar la URL loopback no elimina esa selección de transporte.

**Impacto:** con un proxy heredado, una solicitud que el instrumento supone
directa a loopback puede intentar pasar por él, incluyendo el bearer sintético.
En la topología observada la red Docker interna limita el alcance y las
credenciales son ficticias; no se acredita filtración de secretos reales.

**Corrección aplicada:** el opener incluye `ProxyHandler({})` y `NoRedirects()`.
La [contraprueba](../../tools/s15/test_api_flows.py#L47) configura proxies y elimina
las exclusiones de loopback, sin hacer peticiones, y comprueba que el opener no
conserve el handler de proxy. **Verificación:** diez unitarias del instrumento
PASS, Ruff y formato PASS. El integrador repitió el recorrido completo sobre otra
restauración: [180/180 checks y 72 solicitudes](S15_API_SQL_Y_FLUJOS.md), con el
instrumento final cuyo SHA-256 después de esta corrección es
`76ae0d83cff7a426186e01175af56ecc1fc878e8f253724bb2ed25b720edfcee`.

## Ampliación final: navegador, privacidad y arranque

No se identificaron nuevos defectos críticos, altos ni medios en esta ampliación
acotada. Se mantienen las fronteras y límites siguientes:

| Frontera | Evidencia inspeccionada | Resultado y límite |
|---|---|---|
| Mundo y ajustes de navegador | [browser_profile.py:6](../../tools/s15/browser_profile.py#L6), [browser_settings.py:7](../../tools/s15/browser_settings.py#L7) | El perfil reutiliza sin mutar el entorno la lista cerrada de recursos sintéticos y exige una marca propia. Valida antes y después de cargar `base`, conserva `DEBUG=False`, permite CORS únicamente desde `http://127.0.0.1:3115` y apaga Google/IA/S11/Beat. No amplía el CORS ni la política de producción. |
| Preestado y sesiones | [browser_seed.py:62](../../tools/s15/browser_seed.py#L62) | Lee el manifiesto privado y sellado, exige 36 reportes y actor de identidad nuevo, financia por servicios dentro de una copia propia y escribe refresh/canario en archivos privados. El canario se elige antes de enviar el formulario; no se publican sus valores en los recibos de verificación. La comprobación de aislamiento de red pertenece a la preparación operativa, además del perfil de ajustes. |
| Oráculo y búsqueda de número textual | [browser_seed.py:234](../../tools/s15/browser_seed.py#L234), [browser_seed.py:369](../../tools/s15/browser_seed.py#L369), [browser_seed.py:545](../../tools/s15/browser_seed.py#L545) | Contrasta historial/ciclo/actores, evidencia, ledger y vínculos de auditoría con SQL; enlaza el journey con corrida/dataset/sujetos. Recalcula HMAC y busca tanto la forma canónica como la formateada en el dump lógico y logs suministrados, sin guardar el número ni valores divergentes. Acredita solo esas superficies: las entradas, selfie, trazas y vídeos se excluyen expresamente y no prueba HT3 general. |
| Forwarder de imágenes firmadas | [storage_forwarder.py:28](../../tools/s15/storage_forwarder.py#L28) | Solo GET del prefijo firmado, upstream fijo loopback, proxies y redirects desactivados, cuerpo máximo 5 MiB y MIME JPEG/PNG para éxito. No reenvía Authorization, cookies ni cabeceras del cliente; no registra rutas/capacidades. Las firmas las valida el Storage local. Su bind `0.0.0.0:18517` pertenece al contenedor de la red interna; el acceso del host se limita mediante el relay loopback. |
| Relay host → Docker interno | [browser_relay.py:14](../../tools/s15/browser_relay.py#L14), [browser_relay.py:21](../../tools/s15/browser_relay.py#L21), [browser_relay.py:81](../../tools/s15/browser_relay.py#L81) | Contenedor y red por nombre fijo, etiquetas S15, perfil esperado, una única red interna y dirección privada dentro de su subnet. Escucha solo `127.0.0.1`, con dos puertos fijos, 64 conexiones y timeouts. No se convierte en un proxy a destinos elegidos por el navegador ni imprime el JSON completo de `docker inspect` o sus variables. La validez de la topología se comprueba al arrancar; no se afirma supervisión continua de cambios posteriores. |
| Archivos de actores y salida E2E | [privatePaths.ts:6](../../frontend/e2e-s15-fullstack/privatePaths.ts#L6), [actors.ts:51](../../frontend/e2e-s15-fullstack/actors.ts#L51), [playwright.s15-fullstack.config.ts:14](../../frontend/playwright.s15-fullstack.config.ts#L14) | Rutas canónicas bajo el árbol privado S15 del usuario, sin symlinks; contrato regular `0600` y errores sin contenido. Comprueba esquema, UUID, correos sintéticos, mes y aritmética esperada. La corrida exige una carpeta nueva y la mantiene `0700`; los JSON propios se crean `0600` sin sobrescritura. |
| Sesión y navegación | [actors.ts:139](../../frontend/e2e-s15-fullstack/actors.ts#L139), [flows.spec.ts:81](../../frontend/e2e-s15-fullstack/flows.spec.ts#L81) | Introduce refresh sintético en cookie HttpOnly del BFF; no modifica el código de login ni afirma Google real. Contextos separados, service workers bloqueados, HTTP permitido solo para los orígenes locales declarados y WebSocket limitado al frontend. API/Storage no se sustituyen con respuestas de prueba. Las teselas externas se bloquean y esa exclusión debe conservarse al interpretar capturas y resultados. |
| Medición pública descriptiva | [s15_page_performance.mjs:81](../../frontend/scripts/s15_page_performance.mjs#L81), [s15_page_performance.mjs:244](../../frontend/scripts/s15_page_performance.mjs#L244) | Rutas y orígenes constantes, contexto anónimo nuevo, bloqueo de mutaciones salvo refresh anónimo del BFF, salida privada exclusiva y entradas ligadas al journey/postcheck aprobados. Conserva rutas sin query, metadatos mínimos y elementos LCP sin texto. No usa sesiones de ciudadanos ni presenta las muestras locales como auditoría de seguridad o medición poblacional de Core Web Vitals. |
| Comando de aplicación | [Dockerfile:24](../../backend/Dockerfile#L24), [heroku.yml:24](../../backend/heroku.yml#L24) | Mantiene usuario de contenedor sin root y agrega `--worker-class gthread --threads 4` al web, sin cambiar autenticación, TLS, secretos, permisos, workers Celery ni añadir fase release/migraciones. El [informe local](S15_INFORME_RENDIMIENTO.md) documenta las tres ventanas. La mayor concurrencia implica hasta cuatro hilos con sus conexiones: es configuración preparada, no prueba de despliegue ni del presupuesto remoto al escalar. |

Las trazas Playwright, vídeos, capturas de identidad, contrato de actores,
selfie sintética y logs brutos son **artefactos privados**. Pueden contener
refresh, access, capacidades firmadas, número canario, nombres sintéticos y
código de vale. El permiso `0700` del directorio limita su lectura aunque una
biblioteca cree archivos interiores con otros permisos; no los convierte en
material publicable. Para anexos se seleccionan capturas revisadas o recibos
minimizados, nunca el archivo de traza o el contrato completo.

La fuente revisada permite ejecutar y verificar F1…F8; su resultado observado se
registra por el integrador en [los flujos de navegador](S15_FLUJOS_NAVEGADOR.md).
Esta revisión no atribuye una corrida de navegador a su propio revisor ni marca
como terminado un recorrido que todavía figure pendiente en su informe.

## Autenticación, autorización y datos personales del producto

Los instrumentos nuevos son comandos locales, no endpoints. No añaden una forma
HTTP de emitir sesiones ni sustituyen los permisos del producto.

- [base.py:213](../../backend/config/settings/base.py#L213) conserva autenticación
  JWT y denegación por defecto. [permissions.py:35](../../backend/apps/core/permissions.py#L35)
  consulta rol y verificación del usuario real; el permiso de institución consulta
  su vínculo activo y el objeto concreto. No se observó que S15 introdujera roles
  confiados a claims enviados por el cliente.
- [identity.py:174](../../backend/apps/auth_app/services/identity.py#L174) exige la
  clave de identidad, guarda HMAC y compara con `compare_digest`.
  [admin_app/views.py:100](../../backend/apps/admin_app/views.py#L100) restringe
  el detalle y la firma de la selfie a `IsAdmin`. La selfie sigue siendo sensible
  aunque el número textual no se persista en claro.
- [map_app/serializers.py:54](../../backend/apps/map_app/serializers.py#L54)
  mantiene la lista blanca pública y redondea las coordenadas. La descripción es
  pública por contrato; no se debe introducir identidad real en las descripciones
  de demostración o evaluación.
- [cookie.ts:33](../../frontend/src/lib/auth/cookie.ts#L33) conserva refresh
  `HttpOnly`, `SameSite=Lax`, `Secure` en producción y path acotado. El backend usa
  bearer para la API; los comandos locales no cambian este mecanismo.
- [prod.py:3](../../backend/config/settings/prod.py#L3) conserva `DEBUG=False`,
  HTTPS/cookies seguras y requisitos de host/secreto. La configuración desplegada
  no se comprobó: estas observaciones son de código, no una afirmación del runtime.

## Riesgos heredados y pendientes operativos

No se presentan como defectos introducidos por S15 ni como trabajo ya resuelto.
Las prioridades siguientes describen el efecto operativo; no prueban que una
credencial esté comprometida.

### S15-SEC-01 — Alta prioridad operativa: rotaciones pendientes de S14-D19

**Evidencia:** [S14_DECISIONES.md:37](../s14/S14_DECISIONES.md#L37) difiere la
rotación de `SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL` y cuatro claves heredadas.
**Impacto:** una credencial antigua con acceso que no se haya invalidado conserva
su capacidad; la service role y la conexión DB tienen alcance privilegiado.
**Estado:** abierto, sin valores inspeccionados ni rotación ejecutada por esta pasada.
**Tratamiento:** ejecutar el procedimiento con consumidores, orden de actualización,
verificación y recuperación documentados. No cambiar rutinariamente
`DOCUMENT_HMAC_SECRET` si se conservan identidades: el código y D19 explican que
eso rompe la correspondencia de los HMAC existentes. No rotar la clave del
aparato S11 sin reabrir ese trabajo.

### S15-SEC-02 — Media: TLS Redis sin verificación de certificado

**Evidencia:** [base.py:272](../../backend/config/settings/base.py#L272) y la
aceptación explícita [S14-D18](../s14/S14_DECISIONES.md#L36).
**Impacto:** el cifrado de la conexión no autentica el certificado del servidor.
**Estado:** riesgo aceptado abierto; no es una corrección automática pendiente.
**Tratamiento:** mantenerlo en la entrega técnica y académica; reevaluar solo si
se cambia el proveedor o dispone de una cadena de confianza verificable. Esta
revisión no desactiva ni cambia la configuración para intentar demostrarlo.

### S15-SEC-03 — Media: protección de main no acreditada como resuelta

**Evidencia:** [S14-D20](../s14/S14_DECISIONES.md#L38) registra la falta de
protección efectiva. El nuevo workflow pide permisos de lectura, pero no impone
por sí mismo políticas de integración.
**Impacto:** no se garantiza que una integración deba pasar CI y revisión.
**Estado:** heredado, sin consulta remota nueva.
**Tratamiento:** conservar la limitación; preparar PR y comprobaciones no equivale
a afirmar que la rama esté protegida.

### S15-SEC-04 — Media: logout no revoca el refresh en el servidor

**Evidencia:** [base.py:247](../../backend/config/settings/base.py#L247),
[logout/route.ts:8](../../frontend/src/app/api/auth/logout/route.ts#L8).
**Impacto:** un refresh previamente obtenido puede seguir usándose hasta vencer
aunque se borre la cookie local. El valor por defecto es siete días.
**Estado:** limitación histórica explícita de ADR-0005, sin cambio S15.
**Tratamiento:** no presentar logout como revocación de credenciales; excluir
sesiones de los anexos y renovar las sintéticas fuera de cada medición. Un cambio
del modelo de revocación requiere trabajo de producto independiente.

## Condiciones para extender esta revisión

1. La ampliación estática de ajustes locales e instrumento API/SQL ya se realizó,
   incluido el fix de S15-SEC-05 y el recorrido repetido por el integrador. La
   ampliación final cubre también navegador y arranque. Consolidar sus resultados
   realmente observados y revisar cambios posteriores en estas fronteras antes
   de integrar; carga y UI conservan sus revisiones propias.
2. Antes de usar material IA real, cerrar responsable, acceso, conservación,
   respaldo y revisión del export público. Son entradas de custodia pendientes
   en [el ejecutor](../../tools/evaluation/README.md#evidencia-recuperación-y-custodia),
   no una certificación que pueda inferirse de permisos `0600`.
3. La autorización para usar el entorno remoto existente no modifica por sí sola
   el guard local del harness. Cualquier habilitación remota debe revisarse con el
   destino y controles concretos antes de ejecutar carga.
4. Adjuntar al cierre las verificaciones realmente ejecutadas del árbol final.
   Este informe no convierte los conteos previos de pruebas en pruebas ejecutadas
   por el revisor ni reemplaza los flujos completos de API y navegador.

Siguen pendientes las fotos/oro y la corrida IA presupuestada, las cuentas y
comprobaciones reales de Google/correo, las rotaciones asignadas en S14-D19 y la
integración/CI del árbol final. El diagnóstico remoto de constantes sí fue
observado por el integrador; no se usa para declarar cerrados esos puntos.
Tampoco se desplegó `gthread/4` ni se rotaron secretos desde esta revisión.
El orden operativo y los consumidores figuran en
[S15_OPERACION_Y_DEMO.md](S15_OPERACION_Y_DEMO.md).

**Conclusión de esta pasada:** no hay nuevos hallazgos críticos/altos de código
abiertos dentro del alcance estático revisado. La preparación local puede seguir;
los riesgos heredados y comprobaciones finales permanecen expresos.
