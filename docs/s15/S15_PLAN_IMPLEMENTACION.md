# Sprint 15 Plan de implementación

**Seguimiento vigente:** [validación y estado actual](S15_INFORME_VALIDACION.md). Este enlace distingue la segunda etapa de los cortes anteriores conservados en este documento.

**Estado:** v1.1, decisiones del usuario actualizadas el 20/10/2026; S15 en ejecución.
**Calendario original:** 07/11/2026–13/11/2026. **Inicio real:** 20/10/2026.
**Base:** `origin/main@3d89182`, con S14 integrado y cierre productivo documentado.
**Rama local:** `feat/s15-validation`.

S15 debe verificar rendimiento, congelar datos reproducibles y reunir los resultados y la
documentación técnica para la defensa. La revisión completa posterior a S14 se realizó sobre
su árbol final y sus documentos de cierre. Las decisiones del usuario del 20/10/2026 permiten
completar mediciones de rendimiento locales representativas y usar el entorno remoto existente
para las comprobaciones que requieran Heroku ↔ Supabase; no se exige contratar otro entorno.
La evaluación IA espera las fotos y el Excel/ZIP que el usuario ya preparó con Claude.

La defensa está prevista aproximadamente para el **16/12/2026**; los resultados técnicos
se necesitan antes para preparar el documento. Por decisión del usuario, la incorporación
al `.docx` se realizará **después de cerrar los 16 sprints**: se preparan evidencias y cambios
en el repositorio durante S15/S16, sin condicionar sus cierres a esa incorporación posterior.

El [registro de revisión](S15_REVISION_PLAN.md) detalla los problemas encontrados en v0.1,
y las correcciones del plan v0.2. La [apertura tras S14](S15_APERTURA_Y_DEPENDENCIAS.md)
actualiza las dependencias, el alcance autorizado y las decisiones de la etapa actual.
El [registro de decisiones y pendientes](S15_DECISIONES_Y_PENDIENTES.md) concreta el reparto
propuesto de fotos, presupuesto y tareas después de las respuestas más recientes.

## Alcance y fuentes

El alcance base sigue siendo [Plan de desarrollo §25](../PLAN_DESARROLLO.md#25-sprint-15--carga-dataset-definitivo-y-documentación).
Se concreta usando [progreso](../PROGRESO_SPRINTS.md), [backlog](../BACKLOG.md),
[API](../API.md), [diccionario de datos](../DICCIONARIO_DATOS.md),
[cambios pendientes de tesis](../CAMBIOS_TESIS_PENDIENTES.md) y los ADR vigentes.
La base final de S14 consta en su [informe de pruebas](../s14/S14_INFORME_PRUEBAS.md),
[decisiones](../s14/S14_DECISIONES.md) y [guía full-stack](../s14/S14_E2E_FULLSTACK.md).

Se prevén seis bloques: preparación, dataset y trazabilidad, carga, evaluación de IA,
usabilidad y capturas, y consolidación documental. Las correcciones se limitarán a defectos
demostrados o a optimizaciones justificadas por mediciones. Se conservan los contratos de API.

Permanecen fuera del alcance nuevas funciones del producto, recompensas reales, Telegram,
la API real de X y refactorizaciones estructurales sin una causa medida. Mostrar el motivo
de rechazo en el panel sigue como mejora post-MVP según S14-D16; no se incorpora para la
demostración sin revisar su alcance. La etiqueta final y la defensa pertenecen a S16.

## Diagnóstico para la planificación

| Tema | Situación observada | Consecuencia para S15 |
|---|---|---|
| Estado de S14 | Cerrado el 20/10: PR #54 integrado, CI del candidato 6/6 y Heroku v26 registrados; PR #55 incorpora el cierre productivo | Base de S15 fijada en `3d89182`; conservar alcances y límites de la evidencia S14 |
| Infraestructura | Operación documentada en Heroku, Vercel y Supabase; el §25 aún menciona Railway | Usar el entorno existente para Heroku ↔ Supabase; no exigir un entorno remoto adicional. La carga final puede medirse localmente con configuración representativa y límites explícitos |
| Dataset de mapa | `seed_reports` genera 14 snapshots sintéticos sin la historia completa | Reutilizar catálogos y criterios geográficos; construir el dataset definitivo con servicios de dominio |
| Datos de S14 | Seed orientado a ocho flujos E2E, Storage falso y sesiones sembradas | Reutilizar herramientas después de su integración, sin presentarlo como dataset final ni como prueba de Google o servicios externos |
| Carga | No hay escenarios k6 versionados; existe el ejecutor Python EX-10 de S11 | Preparar un protocolo de ingeniería propio para S15, separado de cualquier reapertura experimental |
| IA | OpenAI con prompt v2; el resultado histórico de EX-09 es 19/28 con otro protocolo | Definir una evaluación independiente del flujo actual antes de efectuar llamadas |
| Tesis | Hay diferencias acumuladas de arquitectura, API, modelo, alcance y resultados | Preparar cambios y evidencias por hito; incorporar al `.docx` después del cierre de los 16 sprints, distinguiendo siempre preparado de incorporado |

La [adjudicación S11](../s11/S11_D_ANALISIS_Y_DECISIONES.md) y su
[dossier](../s11/S11_E_DOSSIER_Y_CIERRE.md) siguen siendo la referencia histórica:
HT5 `PASS`, HT2 `FAIL`, y HE, HT1, HT3, HT4 y HT6 `NOT_DEMONSTRATED`.
Las verificaciones nuevas llevarán identidad, protocolo y resultados propios. No se
sobrescriben esos veredictos ni se reabren EX-10, EX-11 o su custodia por ejecutar S15.

## Revisión posterior al cierre de S14

La revisión de entrada se realizó con la evidencia registrada en el árbol final. No se
repitieron las suites históricas ni se consultó producción para reinterpretar ese cierre.
El detalle y las fuentes están en la apertura de S15; esta tabla conserva los controles.

| Condición | Evidencia que se debe revisar |
|---|---|
| Base final | PR integrado, SHA de `main`, alcance final y correcciones posteriores a los carriles |
| Backend | Ruff, formato, migraciones, suite de dominio, umbral ≥ 80 %, escenarios S-01…S-12 y comparación API = SQL |
| Cobertura | Resultado del producto separado del manifiesto HT5-01; mantener el universo y las restricciones metodológicas de cada uno |
| Experimentos | Salida real de la suite correspondiente y GOV-T054; explicar discrepancias locales sin aceptarlas como CI verde |
| Frontend | Formato, pruebas completas, lint, tipos, build y todas las E2E de CI |
| Flujos reales | Evidencia F1…F8, vídeos y hashes; distinguir API real, API interceptada y servicios sustituidos |
| Revisiones | Revisión independiente y de seguridad de S14, resolución de hallazgos y riesgos aceptados |
| Operación | Estado efectivo de despliegue y árbol publicado; reconciliación y smoke si se autorizó desplegar S14 |
| Documentación | Informe, matriz de pruebas, decisiones, API y Anexo H preparados por S14, con los pendientes finales explícitos |

El despliegue S14 quedó registrado en Heroku v26. El smoke autenticado con Google y
la observación del primer correo real de Brevo se registran por separado; no se convierten
en nuevos requisitos de cierre de S14. Las mediciones S15 identificarán su propio árbol
y configuración; el despliegue previo no constituye evidencia de rendimiento S15.

P0 se divide en **P0-L**, preparación local sin cuota ni datos del entorno existente,
y **P0-M**, habilitación específica de cada medición final. P0-L comprende el generador,
el verificador, el ejecutor offline y los escenarios de carga con medios sintéticos.
P0-M ya no exige un entorno remoto adicional: el rendimiento final se puede medir localmente
con perfil representativo; la comprobación Heroku ↔ Supabase usará el entorno existente.
Antes de mutar ese entorno se fijarán una ventana, el respaldo verificable y el alcance de
los datos de ensayo. Las mediciones de solo lectura se distinguirán de la carga y los cambios.

Las fotos/oro siguen pendientes de entrega; el usuario aportará el Excel/ZIP existente.
El presupuesto API disponible es de **USD 5–10 en total**, compartido entre evaluación,
pruebas personales y demostración en vivo. La reserva para pruebas/demo se fijará antes de
llamar al proveedor; esta actualización no consume cuota ni ejecuta pruebas remotas.
P0-M se cerrará por carril con sus entradas y protocolo, sin bloquear tareas independientes
por la falta de fotos. Ninguna decisión de planificación garantiza el cumplimiento de umbrales.

## Preparación y contratos de medición

**Protocolo implementado:** [validación local](S15_PROTOCOLO_VALIDACION.md).
Los resultados y límites actuales están en el [informe local](S15_INFORME_PREPARACION_LOCAL.md)
y su [índice de evidencias](S15_INDICE_EVIDENCIAS_LOCAL.md).

Fijar SHA inicial, versiones, zona `America/Asuncion`, fecha de referencia del dataset,
escenarios, denominadores, umbrales, abortos, comandos y ubicación de evidencias. Cada
ejecución identificará protocolo, commit, configuración no secreta, dataset y sus hashes.
Una ejecución incompleta o inválida se distinguirá de una medición válida que incumple el umbral.

La matriz `docs/s15/S15_MATRIZ_REQUISITOS_EVIDENCIA.md` enlazará requisito, hipótesis,
escenario, preestado, instrumento, resultado esperado y artefacto. Debe cubrir también los
requisitos que una suite de ocho flujos no demuestra por sí sola:

| Hipótesis de referencia | Evidencia nueva que se debe diseñar en P0 |
|---|---|
| HT-1 | HTTP y persistencia bajo carga, encolado y ejecución asíncrona correlacionados; perfil y proveedores explícitos |
| HT-2 | Puntuación primaria propuesta de categoría, institución y prioridad exactas; publicar además categoría/prioridad según el texto de la tesis, sin intercambiar métricas |
| HT-3 | Matriz sintética de normalización/igualdad/desigualdad/unicidad y comprobación de no persistencia de la cédula canario en esquema, datos, logs y exports |
| HT-4 | Ocho flujos más clasificación/asignación y reconciliación por evento; detallar qué integración real o sustituta cubre cada tramo |
| HT-5 | Cobertura del manifiesto crítico correcto, separada de la compuerta de producto |
| HT-6 | S-01…S-12, ranking/privacidad, canje, remanente y fronteras mensuales, con resultados económicos exactos |

Esta matriz no readjudica S11. El uso de estas verificaciones como evidencia académica
nueva exige definir en P0 protocolo prospectivo, alcance y regla de síntesis. HE/encuesta
seguirá separada de los resultados técnicos y HTP no se obtendrá promediando indicadores.

Separar tres conjuntos: dataset funcional para la demostración, payloads consumibles de
carga y casos de evaluación IA con etiquetas de oro. Compartir un único conjunto mutable
haría que las mediciones alterasen los resultados de la demostración.

Para las mediciones locales, preparar bases y almacenamiento sintético propios de S15,
separados de S14 y de S11, además de una instancia Redis dedicada: cambiar solo el prefijo
de caché no aísla broker, resultados ni tareas Celery, que comparten `REDIS_URL`. Los
procesos web/worker y el scheduler deberán apuntar al mismo entorno S15 y nunca consumir
colas de otros sprints. Al usar el entorno remoto existente, inventariar procesos y datos
presentes y evitar borrados globales de caché o cola; preservar/restaurar sus datos si una
prueba de escritura lo requiere, según el respaldo y la ventana definidos. Revisar los guards de las herramientas S14 antes de reutilizarlas. Los
artefactos privados y volcados irán fuera del repositorio, con un índice publicable y hashes.
No modificar los archivos congelados por GOV-T054 ni el aparato histórico para habilitar S15.

Generar datos históricos con Beat y workers detenidos; controlar el reloj en cada servicio
invocado por la herramienta y recoger los efectos `on_commit`. Al habilitar procesos,
drenar o reconstruir solo las colas propias según el manifiesto: dejar mensajes antiguos
puede ejecutar correos, IA o autocierres sobre otro snapshot. Definir explícitamente qué
notificaciones deben existir y cuáles se entregan a un sustituto local. La ausencia de
claves reales no evita el encolado ni garantiza una generación libre de efectos externos.

## Dataset definitivo y trazabilidad

**Implementación en preparación local:** comando Django `generate_final_dataset` en
`backend/apps/core/management/commands/`, verificador independiente y pruebas de sus
invariantes. Su estado ejecutable y resultados se registrarán en el avance de S15.

El generador tendrá semilla y versión, fecha de referencia explícita, identificadores de
caso deterministas y modo de inspección previo a escribir. Validará antes de mutar DEBUG,
nombre/host de DB, Redis, buckets/servidor Storage y destino de artefactos contra una lista
explícita de entornos sintéticos permitidos. No borrará datos ajenos. La propuesta para
repetirlo es verificar y salir sin escribir si el manifiesto coincide; ante datos parciales
o divergencia, abortar y reconstruir un entorno dedicado vacío mediante un paso separado.

Congelar entradas byte a byte y verificar salidas por invariantes semánticas. Documentar
la normalización de timestamps, UUID o códigos de canje que los servicios generen al
ejecutarse; no prometer volcados idénticos ni cambiar contratos productivos para conseguirlo.

| Requisito mínimo del §25 | Construcción y comprobación previstas |
|---|---|
| ≥ 20 reportes en Gran Asunción y categorías del catálogo | Tabla de casos con ubicación, categoría, prioridades BAJA/MEDIA/ALTA y origen; fotos válidas y hashes distintos |
| ≥ 10 ciudadanos verificados y ≥ 30 confirmaciones | Proponer al menos 12 ciudadanos con puntos para comprobar el corte Top 10; identidad sintética por servicios, sin autoconfirmación ni duplicación |
| Cuatro estados del ledger | Casos explícitos `PENDIENTE`, `ACREDITADO`, `ANULADO` y `REVERTIDO`, derivados de eventos legítimos |
| Recompensa de 1.000 créditos por institución | Catálogo sintético y saldo financiado por reportes/confirmaciones de esa institución |
| Canjes válidos y segundo intento mensual bloqueado | Cupo por ciudadano, institución y mes local; comprobar la negativa y la ausencia de un segundo débito |
| Saldo institucional negativo tras una reversión posterior al canje | Construir acreditaciones, canjear y revertir por el servicio administrativo; verificar saldo, débitos y auditoría |
| Ventana simulada de 60 días | Reloj de generación controlado, eventos en meses distintos y acreditaciones fechadas por `credited_at` |
| Top 10 del mes actual | Puntos del mes anterior excluidos, más de 10 candidatos, desempate en posiciones 10/11 y las cuatro preferencias de privacidad |
| S-01…S-12 reproducibles | Mapear cada escenario a actores, preestado, acción, resultado y evidencia; restaurar preestados para los casos incompatibles |

Como dimensionamiento inicial se propone evaluar **36 reportes**, ajustables al diseñar
los escenarios. Treinta ciclos completos pueden financiar 1.000 créditos para tres
ciudadanos distintos, uno por institución, y los demás cubrir estados y límites temporales.
La matriz deberá confirmar montos y distribución antes de congelar el tamaño: el mínimo de
20 reportes, por sí solo, no garantiza saldo suficiente para todos los canjes previstos.
No completar saldos insertando créditos sin su historia de dominio. Los 36 casos son una
hipótesis de tamaño, no una matriz ya validada: se ampliarán si los preestados o saldos lo exigen.

Definir estados de ensayo separados además del snapshot de exhibición final:

- Antes del canje: saldo ≥ 1.000 obtenido por servicios; un caso con excedente para probar
  que el remanente se conserva, además del caso con 1.000 exactos.
- Después del canje: repetir en la misma institución y mes debe devolver
  `MONTHLY_LIMIT_REACHED` sin un segundo débito, incluso si también falta saldo.
- Después de revertir una acreditación ya gastada: comprobar el saldo negativo y la validez
  del vale. Para demostrar `INSUFFICIENT_BALANCE`, ensayar el mes siguiente por el instante
  explícito del servicio o restaurar su checkpoint en ese período posterior, conservando
  la redención y el débito del mes anterior.
- S-10: crear un reporte `x_mock` sin ciudadano de origen, validarlo y confirmarlo con un
  ciudadano verificado; el origen gana cero y el confirmante diez, con auditoría propia.

La matriz fijará importes exactos, orden y checkpoints para S-01…S-12. Un mundo ya canjeado
no puede reutilizarse como preestado de un canje exitoso. El límite mensual se comprueba antes
del saldo en el servicio; por eso una sola negativa no demuestra ambas reglas.
Financiar con primeras validaciones/resoluciones: la revalidación de un ciclo posterior no
repone los puntos de creación ni confirmaciones antiguas y no equivale a otros 100 al autor.
Para los casos negativos comprobar también que no se agregan canjes, movimientos ni correos.

La generación comprobará roles activos, perfiles realmente aprobados y membresías de los
operadores; invocar un servicio no reproduce automáticamente todos los permisos HTTP.
Usar envío/revisión sintéticos para identidades y promoción auditada para operadores cuando
formen parte del denominador. Delimitar explícitamente el bootstrap técnico, incluido el
primer administrador, y verificar por API las negativas de rol/tenant relevantes.
Para las prioridades y la historia de clasificación sintéticas, proponer respuestas
preparadas inyectadas por el parámetro `adapter` de la orquestación existente, únicamente
desde la herramienta S15 aislada y con procedencia sintética explícita. No añadir un
proveedor falso al runtime productivo ni contar esas respuestas como evaluación de IA real.

El modo de inspección no invocará uploads ni servicios con efectos. Mantener un journal
privado de etapas y objetos propios con estado incompleto/completo. No asumir que una
transacción que abarque todo el generador compensa uploads ya hechos: ante un fallo parcial,
conservar diagnóstico y reconstruir solo su entorno dedicado según el manifiesto.

Construir la secuencia mediante servicios reales y un reloj controlado en la herramienta
aislada, conservando validaciones del producto. Los snapshots históricos del mapa y las
fixtures unitarias con saldo insertado no sirven como evidencia de trazabilidad completa.
La reconstrucción de casos temporales incluirá el mes de referencia y al menos el anterior,
sin depender del día en que se ejecute el generador.
Los 60 días se definirán con extremos exactos en hora local; pueden abarcar tres meses
calendario. Incluir acreditaciones justo antes y después del corte de mes.

La API pública y los canjes usan el mes real del servidor. Conservar para cada medición
la fecha real y su versión congelada: no fechar anticipadamente una corrida en diciembre.
Para la defensa prevista aproximadamente el 16/12/2026, generar una versión de demostración
con ancla de diciembre y manifiesto propio, y comprobar la API durante ese mes. S15 y sus
resultados técnicos no esperan a diciembre: se ensayan ahora con la fecha real, y la versión
de diciembre es una preparación posterior de la demostración. Para reproducir resultados de
otro preestado, restaurar su checkpoint: el parámetro `now` de los selectores cambia la
ventana mensual, pero consulta los estados actuales y no reconstruye un saldo histórico
anterior a una reversión. Separar esa comprobación de la API en vivo. Si la defensa cambia de mes, generar una
versión nueva con otra ancla, manifiesto y capturas; conservar la versión congelada anterior.
El preflight de demostración comprobará que el mes del manifiesto coincide con el servido
por la aplicación antes de verificar ranking y cupo mensual.
Al generar un canje fechado, controlar también `created_at`, auditoría y notificaciones;
pasar solo `operation_at` al servicio no fija todos sus timestamps.

El acceso de demo local reutilizará, tras integrar S14, la emisión de sesiones sintéticas
por herramienta aislada y cookies del BFF. Las identidades ficticias no tienen login Google
real. Regenerar los tokens por rol al restaurar y antes de la demo, usando la hora real y
los TTL vigentes; no congelarlos en el dataset ni añadir un endpoint de acceso al producto.
El archivo de actores será privado, con permisos restrictivos y fuera del paquete público.
Una demo con Google real requiere cuentas de prueba segregadas y un smoke específico.
El usuario preparará cuentas de municipalidad, ANDE, administrador y ciudadano; queda
confirmar la cuenta ESSAP y un segundo ciudadano para los recorridos que exigen personas
distintas, como confirmaciones y negativas por rol. Las cuentas reales y sus secretos no
formarán parte del manifiesto público ni sustituirán las identidades sintéticas del ensayo.

El verificador consultará SQL directamente y lo contrastará con API y resultados esperados:
saldos por usuario/institución, puntos pendientes e históricos, ranking mensual, canjes,
versiones/ciclos de validación, historial de estados y `audit_logs`. La matriz enumerará
cada evento obligatorio y su vínculo con actor, reporte, ciclo y movimiento. El criterio
será **100 % de eventos obligatorios presentes**, con un denominador publicado; contar
acciones distintas o filas totales no basta. Incluir las redenciones en la exportación.
Derivar las expectativas de la matriz congelada, sin aceptar como oráculo los cálculos
del propio generador. Verificar vínculos y unicidad de confirmación, original/reversión,
canje/débito y auditoría. Ambas filas de una reversión quedan fuera del saldo cuando están
`REVERTIDO`: no descontar por segunda vez la compensación.
Reconciliar también notificaciones esperadas y su deduplicación, distinguiendo creación
del registro, encolado y entrega: un correo `PENDIENTE` no acredita envío al proveedor.

Para dashboards, fijar expectativas de primera validación/resolución por historial, ventanas
de seis meses y SLA configurado/sin configurar. Los umbrales de SLA que se usen serán valores
de demostración explícitos. Verificar supresión pública k = 5 y complementaria: tres canjes
de tres personas deben poder verse suprimidos; no se baja k ni se fabrican canjes para llenar
una captura. Esta verificación del dataset no amplía automáticamente el perfil de carga S13.

**Salida:** generador congelado, matriz S-01…S-12, manifiesto de datos y medios, informe
de invariantes y procedimiento de respaldo/restauración ensayado en una base limpia.
La copia de demostración se conserva separada de las pruebas que consumen o alteran casos.

El respaldo incluirá DB y bytes de objetos privados referenciados por selfies, reportes y
evidencias, con claves relativas, MIME y hashes en un manifiesto privado. Detener escrituras,
worker y Beat durante el snapshot; un `pg_dump` no contiene esos objetos. Restaurar ambos
componentes, verificar todos los hashes/enlaces, regenerar capacidades firmadas y sesiones,
iniciar solo los procesos previstos y ejecutar el verificador y el guion de demo. Documentar
versiones y extensiones PostgreSQL/PostGIS; una lista de contenido del dump no prueba restauración.
Separar en el manifiesto hash original de entrada, hash de bytes normalizados almacenados
y hash compuesto de evidencia/ciclo. `file_hash` y `content_hash` no son intercambiables
con el hash del objeto descargado. La restauración empezará con DB y Storage vacíos y
limpiará solo la caché/cola S15 antes de validar; el Storage S14 en memoria requiere conservar
y reponer sus objetos, no solo reiniciar el proceso.

## Carga y optimización guiada por evidencia

**Implementación propuesta:** escenarios `tools/performance/k6/` e informe
`docs/s15/S15_INFORME_RENDIMIENTO.md`. Confirmar versión e instalación de k6 al ejecutar.

Cubrir creación multipart de reportes, lecturas públicas de mapa, ranking y canje. Añadir
las métricas de S13 al perfil de lecturas si la revisión de alcance confirma capacidad.
Separar escenarios y métricas por endpoint; un promedio agregado no demostrará sus umbrales.

El perfil final local usará un servidor de aplicación adecuado y **DEBUG=false**, con
procesos, recursos, TLS/proxy cuando aplique, latencia de Storage, tamaños de imagen y límites
registrados. No trasladar del E2E S14 el servidor de desarrollo, TTL de un segundo ni la falta
de worker como si fueran configuración representativa. Se conservarán los TTL reales acordados
y se publicará la topología local y su límite de extrapolación: el resultado no mide Heroku.
El harness actual permite exclusivamente destinos locales; cualquier ajuste para observar el
entorno remoto existente es trabajo controlado pendiente, sin retirar sus guards globalmente.
El mínimo de 36 reportes es funcional, no una demostración de escala de datos: fijar el
volumen de lectura y justificarlo; cualquier escala adicional será un ensayo separado.

La entrada primaria propuesta para k6 será Django HTTP directo, con JWT Bearer de actores
sintéticos emitidos fuera de la ventana. Las cookies `ca_refresh` del BFF no autentican
esa API. Renovación, login Google y recorrido por Vercel/BFF se identificarán como otras
mediciones; no mezclar sus tiempos con los de Django. El build frontend se verificará
por separado, con `NEXT_PUBLIC_API_URL` fijado antes de compilar y sin reutilizar un servidor
de desarrollo accidentalmente activo.

1. Validar primero el harness local, con datos ficticios, Storage falso y sin llamadas
   a OpenAI o Brevo. Esa corrida comprueba el harness y no representa infraestructura productiva.
2. Antes de la congelación final, ejecutar una medición exploratoria con presupuesto acotado;
   corregir únicamente defectos o cuellos demostrados y verificar la regresión afectada.
   Congelar después un perfil local representativo: topología, capacidad, tamaño de datos,
   caché, worker/Beat y efectos externos identificados. Las repeticiones no necesitan otro
   entorno remoto ni cuota IA si usan sustitutos declarados. Para observar el entorno remoto
   existente, separar el acceso de solo lectura de las pruebas que muten datos; preparar
   primero la ventana, el respaldo y los límites de carga y gasto de estas últimas.
3. Para creación, proponer 10 ciudadanos nuevos con cinco requests únicos cada uno en
   una ventana de 60 s, sin calentamiento de escritura. Verificar previamente los límites
   reales de `report_create`; reiniciar solo el entorno sintético entre repeticiones.
   Registrar cada intento y respuesta, incluidas negativas, timeouts y errores. Fijar la
   barrera inicial y el límite de llegada: no usar una tasa de 50 inicios como si fueran
   50 creaciones. Las finalizadas fuera de los 60 s no cuentan para ese throughput.
   Propuesta: diez usuarios concurrentes, cinco escrituras secuenciales por usuario y reloj
   monotónico del generador. Contar respuestas 201 contractuales recibidas antes del corte,
   reconciliadas después con una fila de reporte, media, historial y crédito pendiente por
   UUID/hash; `created_at` no prueba por sí solo el instante de commit. Registrar por separado
   persistencias tras timeout o fuera de ventana. Sin replay automático de escrituras.
4. Para canje, usar ciudadanos distintos con saldo legítimo y recompensa disponible;
   separar éxitos de los casos negativos previstos. Medir el segundo canje bloqueado como
   regla funcional, sin contarlo como rendimiento de un canje exitoso. Para N éxitos por
   tanda, presupuestar 1.000 × N créditos legítimos y suficientes cupos usuario/institución/mes;
   si solo se usa autoría resuelta, son diez ciclos completos por éxito. Dimensionar su
   dataset de carga aparte; los tres canjes de demostración no sostienen una muestra de cola.
5. Capturar series de latencia, solicitudes iniciadas/completadas, éxitos persistidos
   dentro de la ventana, errores, uso de recursos y observación de cola/worker. Describir
   explícitamente si la clasificación y las notificaciones usan proveedores reales,
   sustitutos o están desactivadas. Un perfil sin IA no demuestra por sí solo HT-1.
   Fijar un plazo de drenaje y resultados esperados por reporte/notificación, no solo conteos
   de tareas. Una creación 201 puede convivir con fallo de encolado, y una tarea finalizada
   con IA desactivada no es una clasificación. Conciliar pendientes, reintentos y fallos
   con sus filas/auditorías. Declarar cómo se controlan los barridos de Beat y autocierres;
   no iniciar Beat libremente sobre reportes históricos resueltos más antiguos que 72 h.

Congelar duración, tasa/concurrencia y repeticiones de mapa, ranking y canje en el protocolo,
antes de medir. Incluir caché fría y caliente con bbox y filtros reproducibles. No desactivar
permisos, restricciones mensuales ni throttles para conseguir los objetivos.

Cada perfil incluirá una tabla de presupuesto de solicitudes por identidad e IP:
`report_create` 5/h por usuario y `reward_redeem` 10/h; mapa y lecturas anónimas cuentan por
IP y comparten su scope entre rutas. En la base actual mapa/ranking permiten 120/min por
scope, sujeto a lo que cierre S14. Distribuir solo tráfico de clientes legítimos y espaciar
tandas; no falsificar `X-Forwarded-For` ni agregar JWT al mapa para intentar cambiar su clave.
Prechecks, calentamiento y verificador HTTP también consumen límites y pueden calentar caché.
Reservarles presupuesto o una fase fuera de medición, y purgar únicamente el entorno S15
cuando el protocolo lo prevea. No usar `cache.clear()` en una base compartida.

Definir estado HTTP, shape y aserción semántica por camino de éxito. La propuesta exige que
todos sus checks contractuales pasen y cero errores imprevistos para aceptar ese escenario;
las negativas deliberadas se miden aparte. Publicar latencia de éxitos y errores separadas
y no aprobar P50/P99 con respuestas 429 rápidas o datos incorrectos. Las peticiones multipart usarán
los campos `category`, `description`, `photo`, `lat`, `lng`, MIME/extensión válidos y tamaño
de foto ≤ 5 MiB antes/después de normalizar; la unicidad se aplica a bytes, no a nombres de archivo.

Separar lectura estable de carga mixta. Para cada una registrar bbox/filtros/página
canónicos, TTL, estado inicial y evidencia de hit/miss. El alta de reportes invalida la
generación del mapa; una lectura previamente calentada puede dejar de estarlo en la mezcla.
Enfriar solo las claves del escenario sin borrar contadores, broker o resultados.

Registrar método de percentil, población, exclusiones, cantidad de muestras y versiones de
k6. Con 50 creaciones, P99 es una observación próxima al máximo y tendrá un alcance limitado;
no describirla como estimación estable de una cola poblacional. La propuesta es repetir tres
ventanas independientes con actores nuevos o preestados restaurados, publicar cada ventana
y evitar que el promedio o un pool oculte un incumplimiento. Los perfiles de lecturas podrán
reunir más muestras dentro de sus límites. Para canje, P0 decidirá si puede financiarse una
muestra suficiente o si solo se informará latencia exploratoria, dejando sin demostrar el
criterio que corresponda. El umbral de 2.000 ms no cambia por disponer de pocas muestras.

| Indicador | Objetivo y evidencia |
|---|---|
| P50 de endpoints críticos | ≤ 800 ms; población y resultado por endpoint |
| P99 bajo carga | ≤ 2.000 ms; muestras suficientes y número de observaciones publicado |
| Creación de reportes | ≥ 50 éxitos persistidos/min; contrastar cliente, API y SQL, no solo tasa de solicitudes enviadas |
| Heroku ↔ Supabase | RTT observado y tiempo de consultas separados del tiempo HTTP total, con método y límites documentados |
| Cola y atomicidad | Encolado/completitud observable, ausencia de pérdida silenciosa y consistencia del ledger tras la carga |

Instrumentar tiempos de aplicación y de acceso a DB sin registrar credenciales, parámetros
personales ni URLs firmadas. Una consulta mínima mide RTT de acceso a la base, que también
incluye trabajo de servidor; no llamarla latencia de red pura. Si no es posible separar
componentes con observaciones correlacionadas, declarar esa limitación. No restar percentiles
independientes para estimar red. Una corrida local no sustituye la medición entre proveedores. Esta última se realizará
sobre el Heroku ↔ Supabase existente, sin exigir infraestructura adicional; se empezará con
observaciones acotadas de solo lectura. Antes de escrituras o carga que alteren ese entorno,
preparar respaldo, ventana y recuperación. Separar ese resultado del benchmark local.

El preflight comprobará auth, entorno, reloj, capacidad/saldo/cupos, catálogos, bbox de entrada
≤ 0,5° por eje y su expansión canónica conforme al contrato, fotos JPEG/PNG válidas y únicas,
límites de subida, conectividad, workers y
prohibición de salidas externas no previstas. Definir abortos por destino incorrecto,
actividad de otro entorno, pérdida de correlación o efectos externos imprevistos. Conservar
errores y corridas abortadas. HTTP 429/4xx/5xx en el camino de éxito son resultados fallidos,
no muestras a eliminar; los negativos deliberados tendrán escenario y denominador propios.
La reconciliación final usará actores, IDs de respuesta y diferencias SQL antes/después,
sin añadir campos de respuesta ni habilitar el middleware experimental S11 en otro entorno.

Si falla un umbral, revisar planes de consulta, N+1, índices y cachés. Aplicar la corrección
mínima y repetir el escenario afectado desde un preestado equivalente, conservando la
medición anterior. Mantener umbrales y documentar diferencias de configuración. S13 añade
una tarea de notificación por reporte: EX-10 histórico no es una comparación directa.
Conservar una matriz de impacto de cada corrección sobre los resultados existentes: índices,
caché, procesos o código compartido pueden exigir repetir varios escenarios. Antes de cerrar,
toda evidencia aceptada corresponderá al árbol y configuración final, o tendrá una justificación
de equivalencia para cambios solo documentales. No seleccionar la mejor corrida de varias.

## Evaluación independiente de IA

**Entregables propuestos:** protocolo y resultados separados en `docs/s15/`, manifiesto
de casos, ejecutor multimodal y script de puntuación reproducible. Se podrá reutilizar el parser y el scoring
existentes después de comprobar que su definición coincide con el indicador elegido.

El usuario entregará el Excel/ZIP de recolección que ya preparó con Claude; todavía no está
disponible en el repositorio y no se recreará otra plantilla. Al recibirlo, validar sus
campos y adaptar el importador al formato existente.

Como dimensionamiento **propuesto, aún sin aprobación ni congelación**, recolectar **120
reportes distintos: 100 para la evaluación final y 20 para piloto, pruebas personales y demo**.
El reparto por categoría/prioridad está en el registro de decisiones. Los 20 auxiliares no
entran en el denominador final; si una foto se usa para ajustar el prompt o explorar respuestas,
no puede incorporarse después al conjunto reservado de 100. Una escena diferente constituye
un caso; varias fotos de la misma incidencia no multiplican el número de reportes independientes.

El **oro** son las etiquetas humanas de referencia: categoría, institución y prioridad
esperadas para cada caso, asignadas con la rúbrica antes de ver la respuesta del modelo.
Las sugerencias de Claude pueden ayudar a organizar o revisar, pero no sustituyen la revisión
y adjudicación humanas ni constituyen por sí solas etiquetas independientes.

Fijar antes de llamar al proveedor: población, cantidad de casos, representación de categorías,
fotos autorizadas, etiquetas de oro humanas, definición de concordancia, tratamiento de
errores/ausencias, reintentos, modelo, prompt y parámetros. Congelar hashes y etiquetas antes
de observar predicciones. Los casos conocidos de ajuste v2 no serán el conjunto independiente.

La propuesta primaria conserva la concordancia exacta de CA-4: categoría **e institución
y prioridad** correctas en un mismo caso. Publicar también el resultado categoría/prioridad
que enuncia literalmente HT-2 en la tesis, como indicador identificado aparte. P0 confirmará
esta correspondencia antes de congelar el protocolo; no elegir después el porcentaje más alto.
Los diagnósticos de confianza e inconsistencia no se mezclarán en ese numerador.
Puntuar la sugerencia parseada antes de policy, con nulos solo contra nulos: un slug
inventado no se transforma en un acierto por convertirse a `None` al resolver el catálogo.

Separar evaluación del proveedor tras parseo de la aplicación asistida por policy. Esta
última depende de confianza ≥ 0,75, inconsistencia y mapeo al catálogo, y tendrá resultados
propios: una salida exacta no elegible para aplicación y una categoría persistida sin cambio
no son el mismo resultado. Fijar catálogo/slugs, schema y rúbrica v2; una categoría inicial
equivocada no implica por sí sola inconsistencia visual. Resolver desacuerdos de etiquetas
antes de la ejecución y no reutilizar etiquetas diagnósticas v1 incompatibles.
Congelar catálogos activos y sus relaciones, comprobar la tabla incluida en el prompt y
registrar hash/MIME/tamaño de los bytes normalizados realmente enviados. Separar conjuntos
por escena y origen, no solo por hash: recortes o recompresiones de fotos ya usadas no son
casos independientes. Proponer doble revisión humana de etiquetas ambiguas antes de predecir.

La propuesta es evaluar el flujo actual con fotos inéditas y texto, OpenAI y prompt v2,
registrando versiones exactas y fecha. El ejecutor oficial de EX-09 es de texto y no cubre
esa evaluación multimodal. `ai_compare_prompts` puede ayudar a explorar, pero omite entradas
inválidas y no constituye por sí solo una evaluación formal con denominador congelado.

El ejecutor nuevo reutilizará las fronteras del adaptador actual, sin ampliar el esquema
congelado de EX-09. Debe registrar por caso hash de entrada y foto, versión de normalización,
solicitud/configuración identificable, modelo solicitado/devuelto cuando exista, respuesta
minimizada verificable, parseo, error/timeout, número de intentos y uso observado. Conservar
información suficiente para volver a puntuar sin nuevas llamadas. Las claves de API quedan
excluidas de los artefactos. Fotos completas, respuestas sensibles e identificadores del proveedor irán en evidencia privada según su
necesidad, con permisos y plazo de conservación definidos; la publicación será minimizada.
Probar el ejecutor offline con respuestas válidas, inválidas, timeout y casos ausentes antes
de usar cuota real. Un puntuador sin ese ejecutor no resuelve la recolección de evidencia.
Construir la solicitud por lista blanca, sin oro, notas del evaluador, identidad, rutas o
URLs firmadas. Revisar contenido textual/visual y EXIF antes del envío. La custodia S15
identificará responsable, acceso, conservación, respaldo y exportación pública revisada;
`store=False` no se interpretará como retención cero del proveedor.

Congelar N y el mínimo de aciertos `ceil(0,85 × N)` sin redondeo decisorio. La propuesta es
una pasada de evaluación sin selección del mejor intento; cualquier política distinta se
fijará antes. Errores, ausencias o respuestas no parseables no desaparecerán del denominador:
el protocolo decidirá prospectivamente si invalidan la corrida o cuentan como desacierto,
y conservará el registro por caso. No usar un conjunto de transporte de imágenes 2×2 como
evaluación visual. El enlace entre casos IA y el dataset funcional se documentará sin exigir
que todos los reportes usados para financiar canjes consuman llamadas al proveedor.
Nombrar por separado capacidad clasificatoria en una corrida válida y comportamiento
operativo que incluye indisponibilidad; explicitar reintentos de SDK, script y Celery,
pacing, abortos y recuperación. Una corrida directa del proveedor no acredita sus reintentos
productivos ni la persistencia por worker. Como diagnósticos, informar cobertura de aplicación,
tasa de revisión y errores entre casos elegibles, sin asignarles el umbral del 85 %.

Publicar aciertos/total, casos fallidos y distribución de errores; la meta RNF-05 sigue
siendo ≥ 85 %. Registrar tokens observados y costo usando precios explícitos y fecha,
distinguiendo clasificaciones persistidas de consumo total. El límite compartido es de
USD 5–10; fijar una reserva explícita para pruebas personales y demo en vivo y estimar el
costo con el modelo realmente configurado y un piloto acotado antes de usar las 100 entradas
reservadas. Si la estimación supera la parte disponible, presentar el ajuste antes de gastar;
no reducir ni seleccionar el denominador después de conocer respuestas. Un resultado inferior será
un incumplimiento medido. Ajustar el prompt exigiría otra versión y otro conjunto reservado,
sin reutilizar la evaluación independiente como conjunto de entrenamiento.
El uso desconocido de intentos fallidos se marcará desconocido, no cero. Separar costo
observado, estimación con su cobertura tarifaria y consumo reconciliado con el proveedor.

El diseño académico de HT-2 y del resto de hipótesis se decidirá en P0, antes de medir;
la síntesis se redactará en el hito final con esas reglas. Cualquier reapertura formal de S11
se tramita por sus protocolos y fuentes, fuera de esta propuesta de verificación de ingeniería.

## Usabilidad y capturas para la tesis

Retomar B4 diferida por S14-D17 mediante `ux-heuristics`, sobre el dataset definitivo,
en escritorio y móvil. Revisar navegación por rol, formularios, errores, foco/teclado,
contraste, tablas y gráficos, privacidad del ranking y carga lenta. Conservar campos,
consentimientos y validaciones necesarios para identidad, privacidad y trazabilidad.

Priorizar defectos que impiden completar los flujos o acceder a la información. La corrección
tipográfica del gráfico pertenece a S14: comprobar su resultado integrado antes de abrir
trabajo nuevo. Aplicar las skills de UI pertinentes solo a las correcciones que se acuerden.

Fijar viewport/dispositivo, red, estado de caché y navegación inicial para medir tiempos
de carga y probar conexión lenta en un build de producción. Conservar registros de red,
tiempos y fallos, además de capturas; una captura correcta no mide rendimiento. Presentar
las observaciones del navegador por separado de los umbrales de API y no inventar una
meta de Lighthouse o Core Web Vitals que el proyecto no ha comprometido.

Preparar un guion de capturas para ciudadano, operador, administrador y vistas públicas,
con datos sintéticos y estados representativos. Guardar imágenes y vídeos privados fuera
del repo si contienen sesiones o datos sensibles; publicar únicamente material revisado.
Si se corrige una interfaz, ejecutar el flujo afectado y los checks frontend del README.

## Configuración y secretos al preparar la demostración

Retomar S14-D19 y [runbook §§12–13](../runbooks/HEROKU_MIGRATION.md#12-rotación-de-secretos)
como trabajo operativo condicionado, con inventario de pendientes, impacto, orden de cambio,
comprobación y recuperación. Preparar primero el procedimiento revisable; la carga local del
dataset no autoriza reemplazar datos ni identidades de producción.

Priorizar `SUPABASE_SERVICE_ROLE_KEY` y credenciales de `DATABASE_URL`, comprobar todos sus
consumidores e invalidar las cuatro credenciales heredadas sin uso que el runbook identifica.
`DOCUMENT_HMAC_SECRET` se conserva estable si se mantienen identidades existentes. Rotarla
requiere un plan explícito de compatibilidad o recreación/reverificación autorizada; no se
trata como un cambio rutinario de variable. `S11_CUSTODY_HMAC_KEY` pertenece a S11 y solo se
trata por su procedimiento si se decide reabrirlo.

La revisión final de configuración deberá cubrir autenticación, autorización, datos personales,
API, almacenamiento y secretos con `security-best-practices`, como exige AGENTS.md: DEBUG,
HTTPS/cabeceras, CORS, límites y topología de proxies, acceso a base/buckets y ausencia de
secretos o volcados versionados. La aceptación documentada de `CERT_NONE` en S14-D18 se
mantiene como riesgo que debe figurar en el cierre; no implica una corrección pendiente automática.

## Documentación y entrega a S16

Consolidar README, API, diccionario de datos, ADR y diagramas con el árbol final: Heroku,
OpenAI, Brevo HTTPS, 17 entidades tras el retiro de Telegram, X exclusivamente simulado,
ciclos del ledger y límites reales del prototipo. Distinguir decisiones históricas de
instrucciones operativas vigentes; no reescribir resultados pasados para uniformar nombres.

Usar todas las filas abiertas de `CAMBIOS_TESIS_PENDIENTES.md` como checklist para preparar
las evidencias y los cambios, incorporando lo que entregue S14. Prestar atención a CT-03,
CT-15, CT-22/23, CT-29/30/32/41/43, CT-55/58 y las filas finales de S13/S14. Preparar los cambios
de Anexos A, B, C, D y H y de los otros apartados indicados, incluyendo dataset, API real,
versiones, diagramas y resultados. Conservar el calendario planificado del Anexo F y registrar
las fechas reales por separado.

**Decisión del usuario:** actualizar nuevamente el `.docx` solamente después de terminar
completamente los 16 sprints y sus pruebas. Esa incorporación no es un requisito para cerrar
S15 ni S16. Durante ambos sprints se dejan preparados y trazados el texto, los diagramas,
las capturas y las evidencias que permitirán realizarla después, antes de la entrega académica.
Una fila solo pasará a `INCORPORADO_EN_TESIS` tras comprobar su incorporación efectiva.
Reutilizar el Anexo H preparado por S14 en lugar de crear una segunda versión divergente.
La entrega posterior a S16 incluirá revisión de tablas, figuras, referencias cruzadas y
resultados; no se presentará un borrador Markdown como documento académico ya actualizado.

Entregar un índice de evidencias con SHA, protocolo, entorno, comandos, resultados y hashes,
tabla RNF-01…RNF-07 e hipótesis con sus límites, lista de defectos y riesgos aceptados,
procedimiento de restauración probado y guion de demostración ensayado. El paquete debe
permitir a S16 congelar y defender el trabajo sin desarrollo pendiente de S15.

## Secuencia propuesta y reparto

| Paso | Dependencia | Trabajo que puede avanzar en paralelo | Salida |
|---|---|---|---|
| P0-L | S14 cerrado y revisión del plan; respuestas del usuario registradas | Instrumentos, formatos y aislamiento locales | Base y preparación local verificadas |
| A local | P0-L | Escenarios k6 y ejecutor IA offline; borradores documentales | Dataset sintético y verificador; formatos de entrada |
| P0-M local | A local y perfil representativo congelado | Plan de repeticiones, cuotas, worker y observabilidad | Habilitación de rendimiento final local, sin esperar fotos ni otro entorno remoto |
| P0-M IA | Excel/ZIP, fotos autorizadas, oro humano y protocolo prospectivo | Estimación del presupuesto compartido y reserva demo | Conjunto final y gasto habilitados antes de llamadas |
| P0-M remoto | Destino existente y método de observación definidos | Plan de respaldo/ventana para cualquier mutación | Comprobación Heroku ↔ Supabase con alcance explícito |
| B | A y P0-M correspondiente a cada carril | Evaluación IA independiente; preparación de evidencias para tesis | Mediciones válidas y correcciones mínimas |
| C | A estable | Usabilidad/capturas y comprobación API = SQL con cuentas por rol | Flujos y material visual verificados |
| D | A–C y evaluación IA | Revisión independiente y revisión de seguridad | Informe técnico, checklist preparado y entrega a S16; sin actualización `.docx` |

El reparto técnico separará dataset/verificador, harness de carga, evaluación IA y
documentación/UI. El integrador controlará archivos compartidos, bases y una única ejecución
de cada medición final. Ningún carril trabajará sobre la base o evidencia de otro.

Las siguientes rutas son propuestas a crear. `EVIDENCIA_S15` designa una ubicación privada
duradera fuera del repositorio público y de `/tmp`, que se concretará en P0. El reparto por
carril deberá asignarse a personas o agentes en la revisión posterior a S14.

| Artefacto | Ruta propuesta | Responsable funcional | Criterio de cierre |
|---|---|---|---|
| Verificador del dataset | `backend/apps/core/final_dataset/verification.py` | Carril de datos | Reconciliación SQL/API y eventos obligatorios completa en copia restaurada |
| Matriz de casos y preestados | `docs/s15/S15_MATRIZ_DATOS.md` | Carril de datos | Mínimos, S-01…S-12 y fechas trazados |
| Manifiesto de datos y medios | `EVIDENCIA_S15/dataset/manifest.json` | Carril de datos | Versión, ancla, semilla, hashes y restauración comprobados |
| Protocolo e informe IA | `docs/s15/S15_PROTOCOLO_IA.md` y `docs/s15/S15_INFORME_IA.md` | Carril de IA | Protocolo fijado antes de llamadas y resultado con denominador completo |
| Puntuador IA | `tools/evaluation/s15_score.py` | Carril de IA | Mismos resultados al procesar respuestas congeladas; no omite casos |
| Ejecutor IA multimodal | `tools/evaluation/s15_run.py` y `EVIDENCIA_S15/ai/` | Carril de IA | Recolección offline probada, entradas y resultados por caso completos |
| Índice de evidencias | `docs/s15/S15_INDICE_EVIDENCIAS.md` | Integrador | Fuentes, hashes, custodia y límites comprobados sin publicar secretos |

Como distribución inicial de los siete días originales: día 1 preparación y matriz de datos;
días 2–3 generador/verificador y harness; días 4–5 carga y evaluación IA, con usabilidad en
paralelo; día 6 consolidación; día 7 revisión, regresión y margen de corrección. Es una
estimación que se recalculará según entrega de fotos y decisiones académicas, sin exigir
un entorno remoto nuevo. Los resultados técnicos deben estar disponibles antes de preparar
el documento para la defensa aproximada del 16/12/2026. No trasladar desarrollo al S16 de
dos días; la actualización del `.docx` se programa expresamente después de los 16 sprints.
Revisar al terminar el día 3 si dataset restaurable, harness y entradas IA están listos.
Si no lo están, actualizar capacidad/fechas y comunicar qué criterios quedan en riesgo,
antes de consumir la reserva de cierre. La rotación o los cambios operativos deberán
preceder las mediciones finales que puedan invalidar; no dejarlos para después del freeze.

## Verificación y criterios de aceptación

Al implementar, ejecutar los comandos aplicables del README y de los workflows finales de
S14: backend lint/formato, migraciones, pruebas con cobertura y suites separadas de producto
y experimentos; frontend formato, pruebas, lint, tipos, build y E2E. Usar bases de pruebas
aisladas para regresión. Comprobar el dataset definitivo mediante su verificador y los flujos
contra una copia restaurada: ejecutar pytest en su propia base no verifica por sí solo los
datos cargados para la demostración.

| Criterio | Evidencia requerida para aceptar S15 |
|---|---|
| RNF-01/02/03 | P50 ≤ 800 ms, P99 ≤ 2.000 ms, ≥ 50 reportes/min, con entorno, población y resultados verificables |
| RNF-04 | Cobertura ≥ 80 % del universo definido; distinguir producto de HT5-01 |
| RNF-05 | Concordancia ≥ 85 % en evaluación independiente válida |
| RNF-06/07 | Ocho flujos críticos y S-01…S-12 al 100 %, con límites de sustitución de servicios explícitos |
| Datos y trazabilidad | Todos los mínimos del dataset, reconstrucción reproducible y 100 % de eventos obligatorios enlazados |
| Calidad | Cero defectos altos abiertos; revisión independiente y de seguridad, con riesgos aceptados explícitos |
| Documentación | Checklist académico preparado, evidencias accesibles y consistentes, restauración y guion ensayados; incorporación al `.docx` posterior a S16 |
| Alcance de medición | Rendimiento final local representativo y medición Heroku ↔ Supabase existente identificados por separado; no exigir entorno remoto nuevo ni extrapolar el resultado local |

Una suite verde no implica que todas las hipótesis estén demostradas. Si un umbral no se
cumple o falta evidencia, se registra el resultado y su causa, sin declarar aceptado ese
criterio. La decisión sobre alcance, nuevas mediciones y presentación académica se tomará
con el resultado real; no se promete un `PASS` por planificación.
Clasificar severidad y resolución de cada hallazgo antes de cerrar. Un defecto alto abierto
no pasa a cumplir el criterio por renombrarlo riesgo aceptado; cualquier excepción exige
dejar el criterio incumplido y documentar la decisión sobre la entrega.

## Lista para la próxima revisión

- Conservar la base y arrastres de S14 registrados en la apertura; revalidar si cambia el árbol.
- Congelar el perfil local representativo y sus repeticiones; conservar S11 como histórico
  y definir prospectivamente el uso académico de la nueva evidencia.
- Recibir el Excel/ZIP preparado con Claude, validar fotos/oro humanos y confirmar el reparto
  propuesto de 100 casos finales más 20 auxiliares; no crear otra plantilla mientras se espera.
- Estimar consumo API dentro de USD 5–10 y fijar la reserva para pruebas personales/demo
  antes de consumir cuota; mantener el conjunto final separado del piloto.
- Confirmar ESSAP y un segundo ciudadano, además de las cuentas ofrecidas por el usuario;
  ensayar el recorrido completo API = SQL y navegador.
- Concretar observación Heroku ↔ Supabase en el entorno existente; antes de mutarlo, preparar
  respaldo, ventana y recuperación. Ajustar el harness de forma controlada si corresponde.
- Concretar la ventana operativa de secretos y el tratamiento de las identidades existentes.
- Conservar resultados con fecha real y preparar una versión propia de demo para diciembre.
- Dejar preparados cambios y evidencias de tesis; actualizar el `.docx` después de cerrar
  los 16 sprints. Ese trabajo posterior no bloquea el cierre técnico de S15 ni S16.
- Cerrar P0-M por carril y revisar su protocolo antes de consumir cuota o medir fuera del
  entorno local. Todo criterio obligatorio sin evidencia seguirá declarado pendiente.
