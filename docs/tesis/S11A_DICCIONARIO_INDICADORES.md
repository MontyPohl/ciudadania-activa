# S11-A — Diccionario de indicadores, HT-1 y universo crítico HT-5

**Versión congelada:** `S11A-v1.0`

**Baseline:** `main@635d89956ca0632d620916dc0ab298af48079ec4`

**Estado inicial de todos los resultados:** `NOT_DEMONSTRATED`.

Este diccionario se lee junto con `S11A_CATALOGO_Y_MATRIZ.md`. La clasificación metodológica de cada indicador usa sólo `OBSERVABLE`, `DERIVABLE`, `REQUIRES_INSTRUMENTATION` o `NOT_AVAILABLE`. No representa el veredicto: los únicos veredictos siguen siendo `PASS`, `FAIL` y `NOT_DEMONSTRATED`.

## 1. Indicadores de HE

### HE-01 — Problemas observados sin canal estructurado

- **Relación:** HE, PE-1, OE-1; EX-01.
- **Clasificación:** `NOT_AVAILABLE`.
- **Definición operacional:** proporción de respuestas válidas cuyo ítem 5 del Anexo G no es `Nunca` y cuyo ítem 9 no selecciona `app/sitio oficial`. Para este indicador, `app/sitio oficial` es el único canal estructurado explícito del cuestionario; `no reporté` y los canales informales cuentan como ausencia de uso de canal estructurado.
- **Fórmula / unidad:** `(numerador / denominador) × 100`; porcentaje con dos decimales.
- **Numerador:** respuestas válidas que satisfacen conjuntamente `Q5 ≠ Nunca` y ausencia de `app/sitio oficial` en Q9.
- **Denominador / población:** todas las respuestas válidas de personas de 18 años o más, residentes del área metropolitana de Asunción y con smartphone/Internet, que contestaron Q5 y Q9; N observado `UNRESOLVED`.
- **Fuente / ventana / granularidad:** Anexo G de la tesis revisada más export y codebook versionados; ventana real de levantamiento `UNRESOLVED`; una fila por respuesta depurada.
- **Inclusiones:** consentimiento voluntario; Q1 confirma ≥18; Q2 pertenece a Asunción o municipio metropolitano elegible; Q4 no es `No utilizo smartphone con Internet`; Q5 y Q9 completos.
- **Exclusiones:** pruebas del formulario, respuesta fuera de población, mismo ID técnico de respuesta repetido —se conserva la primera completa— o faltante en Q5/Q9. No se imputa ni se intenta reidentificar para deduplicar.
- **Esperado / umbral:** `≥ 60.00 %`.
- **Tipo de resultado / método:** decisorio; estadística descriptiva reproducible sobre el dataset congelado.
- **Limitaciones:** autorreporte, muestreo no probabilístico por conveniencia y sesgo de recuerdo; Q9 mide canal utilizado, no disponibilidad objetiva, por lo que “sin canal estructurado” es un proxy explícito y no una constatación institucional; no estima incidencia urbana real.
- **Estado:** `NOT_DEMONSTRATED`.

### HE-02 — Disposición de uso con identidad verificada

- **Relación:** HE, PE-1, OE-1; EX-01.
- **Clasificación:** `NOT_AVAILABLE`.
- **Definición operacional:** proporción de respuestas válidas que contestan `Definitivamente sí` o `Probablemente sí` en Q18 **y** `Sí sin problemas` o `Sí con reservas` en Q20 del Anexo G.
- **Fórmula / unidad:** `(numerador / denominador) × 100`; porcentaje con dos decimales.
- **Numerador:** respuestas que satisfacen conjuntamente las categorías favorables de Q18 y Q20.
- **Denominador / población:** respuestas válidas de la misma población de HE-01 con Q18 y Q20 completas; N observado `UNRESOLVED`.
- **Fuente / ventana / granularidad:** Anexo G, export y codebook versionados; ventana `UNRESOLVED`; una fila por respuesta.
- **Inclusiones / exclusiones:** reglas de elegibilidad/deduplicación de HE-01; se excluyen faltantes de Q18/Q20 sin imputación.
- **Esperado / umbral:** `≥ 70.00 %`.
- **Tipo de resultado / método:** decisorio; estadística descriptiva reproducible.
- **Limitaciones:** intención declarada no equivale a uso real, adopción ni retención.
- **Estado:** `NOT_DEMONSTRATED`.

### HE-03 — Aceptación de confirmaciones comunitarias

- **Relación:** HE exploratoria, PE-1, OE-1; EX-01.
- **Clasificación:** `NOT_AVAILABLE`.
- **Definición / fórmula / unidad:** Q21 favorable si responde `Sí mucho` o `Sí en algunos casos`; `(favorables / respuestas válidas a Q21) × 100`, porcentaje.
- **Población / fuente / ventana / granularidad:** población y reglas de HE-01; Anexo G/export; ventana `UNRESOLVED`; una fila por respuesta.
- **Inclusiones / exclusiones:** iguales a HE-02, sin imputación.
- **Esperado / umbral:** no tiene umbral; se informa descriptivamente.
- **Tipo / método:** diagnóstico exploratorio.
- **Limitaciones:** no decide HE y no demuestra comportamiento posterior.
- **Escenario / estado:** EX-01; `NOT_DEMONSTRATED`.

### HE-04 — Aceptación de incentivos

- **Relación:** HE exploratoria, PE-1, PE-4, OE-1; EX-01.
- **Clasificación:** `NOT_AVAILABLE`.
- **Definición / fórmula / unidad:** Q22 favorable si puntúa 4 o 5 en la escala 1–5; `(favorables / respuestas válidas a Q22) × 100`, porcentaje.
- **Población / fuente / ventana / granularidad:** población y reglas de HE-01; Anexo G/export; ventana `UNRESOLVED`; una fila por respuesta.
- **Inclusiones / exclusiones:** iguales a HE-02, sin imputación.
- **Esperado / umbral:** no tiene umbral; se informa descriptivamente.
- **Tipo / método:** diagnóstico exploratorio.
- **Limitaciones:** una valoración favorable no prueba motivación ni un efecto causal sobre participación.
- **Escenario / estado:** EX-01; `NOT_DEMONSTRATED`.

HE sólo puede ser `PASS` si una ejecución válida de EX-01 alcanza simultáneamente HE-01 y HE-02. Si una ejecución válida incumple cualquiera, HE es `FAIL`. HE-03 y HE-04 no alteran el veredicto. Si falta formulario fiel al Anexo G, dataset, elegibilidad, ventana, consentimiento o cálculo reproducible, HE es `NOT_DEMONSTRATED`. La tesis calcula aproximadamente 150 respuestas válidas con `Z=1,96`, `e=8 %`, `p=q=0,5` y muestreo no probabilístico por conveniencia; es meta muestral documentada, no N observado ni base para inferencia poblacional estricta.

## 2. Indicadores técnicos

### HT1-01 — Throughput sostenido de creación

- **Relación:** HT-1, PE-5, OE-3, OE-6; EX-10.
- **Clasificación:** `REQUIRES_INSTRUMENTATION`.
- **Definición operacional:** reportes sintéticos creados con éxito mediante `POST /api/v1/reports/` durante la ventana medida de 60 segundos.
- **Fórmula / unidad:** `creaciones exitosas persistidas / (duración medida en segundos / 60)`; reportes/minuto.
- **Numerador:** requests con HTTP `201`, respuesta válida, fila `Report` persistida y movimiento `REPORT_CREATED` único persistido.
- **Denominador / población:** duración real de la ventana; población de 50 requests predefinidos, diez usuarios verificados sintéticos, cinco requests secuenciales por usuario.
- **Fuente / ventana / granularidad:** salida cruda del generador, timestamps monotónicos del cliente e inspección post-run; exactamente 60 s, una observación por request y un agregado por run.
- **Inclusiones:** autenticación ya emitida, multipart completo, validación, normalización/almacenamiento de foto, transacción, ledger y encolado `on_commit` del trabajo IA.
- **Exclusiones:** preparación/reset, creación de usuarios/tokens, warm-up, espera hasta completar la clasificación IA y generación del informe.
- **Esperado / umbral:** `≥ 50 reportes/min`.
- **Tipo / método:** decisorio; prueba de carga aislada descrita en §3.
- **Limitaciones:** prueba sintética corta; no acredita capacidad sostenida, disponibilidad ni SLA productivo.
- **Estado:** `NOT_DEMONSTRATED`.

### HT1-02 — Latencia P50 de creación

- **Relación / escenario:** HT-1, OE-3, OE-6; EX-10.
- **Clasificación:** `REQUIRES_INSTRUMENTATION`.
- **Definición / fórmula / unidad:** percentil 50 por método nearest-rank de la duración cliente entre inicio del envío y recepción completa de respuesta para requests exitosos; milisegundos.
- **Numerador / denominador / población:** no aplica cociente; conjunto de duraciones de todos los `201` del run de 50 requests.
- **Fuente / ventana / granularidad:** salida cruda del generador; misma ventana de HT1-01; una duración por request.
- **Inclusiones / exclusiones:** mismas de HT1-01; requests fallidos se reportan aparte y hacen fallar throughput, pero no se mezclan en el percentil.
- **Esperado / umbral:** `≤ 800 ms`, documentado en Tesis §3.10 y Plan RNF-01.
- **Tipo / método:** decisorio; nearest-rank, sin descartar outliers.
- **Limitaciones:** latencia cliente en el entorno experimental; no representa todas las redes o dispositivos.
- **Estado:** `NOT_DEMONSTRATED`.

### HT1-03 — Latencia P99 de creación

- **Relación / escenario:** HT-1, OE-3, OE-6; EX-10.
- **Clasificación:** `REQUIRES_INSTRUMENTATION`.
- **Definición / fórmula / unidad:** percentil 99 nearest-rank sobre el mismo conjunto de HT1-02; milisegundos.
- **Numerador / denominador / población, fuente, ventana, granularidad, inclusiones y exclusiones:** iguales a HT1-02.
- **Esperado / umbral:** `≤ 2.000 ms`, documentado en Tesis §3.10 y Plan RNF-02.
- **Tipo / método:** decisorio; nearest-rank. Con 50 éxitos corresponde al 50.º valor ordenado.
- **Limitaciones:** con 50 observaciones el P99 coincide con el máximo y tiene alta incertidumbre; se conserva porque el umbral académico está fijado.
- **Estado:** `NOT_DEMONSTRATED`.

### HT1-04 — Desacoplamiento asíncrono

- **Relación / escenario:** HT-1, OE-3, OE-5; EX-10.
- **Clasificación:** `REQUIRES_INSTRUMENTATION`.
- **Definición / fórmula / unidad:** para cada creación exitosa, el HTTP finaliza sin esperar el resultado IA y el trabajo queda correlacionado con la cola; `tarea correlacionada / creación exitosa`, porcentaje, más latencia de cola diagnóstica.
- **Numerador / denominador / población:** tareas encoladas correlacionadas / creaciones exitosas del run; las 50 solicitudes previstas.
- **Fuente / ventana / granularidad:** timestamps `http_start`, `http_end`, `enqueue`, `task_start`, `task_end` asociados a `run_id` y `report_id`; por request/tarea.
- **Inclusiones:** broker Redis real, Celery no-eager, worker con concurrencia `2` como `backend/heroku.yml`, adaptador fake determinista para no medir el proveedor.
- **Exclusiones:** proveedor externo y cualquier reintento no previsto.
- **Esperado / umbral:** predicado estructural para todas las creaciones exitosas: HTTP no espera resultado y existe correlación de encolado. La latencia de cola se informa sin umbral inventado.
- **Tipo / método:** decisorio estructural con diagnóstico temporal.
- **Limitaciones:** requiere instrumentación S11-B; no prueba throughput del proveedor IA.
- **Estado:** `NOT_DEMONSTRATED`.

**Regla HT-1:** `PASS` exige run válido, HT1-01, HT1-02 y HT1-03 dentro de umbral, y HT1-04 satisfecho. Una ejecución completa que incumpla cualquiera produce `FAIL`. Fallo del harness, pérdida de correlación o artefactos incompletos produce `NOT_DEMONSTRATED`.

### HT2-01 — Concordancia conjunta CA-4

- **Relación / escenario:** HT-2, PE-3, OE-5, OE-6; EX-09.
- **Clasificación:** `OBSERVABLE` una vez ejecutado R2; actualmente sin observación.
- **Definición / fórmula / unidad:** caso concordante sólo cuando `category_slug`, `institution_slug` y `priority` coinciden exactamente con sus tres etiquetas de oro; `(casos concordantes / 28) × 100`, porcentaje.
- **Numerador / denominador / población:** casos conjuntos exactos / 28 casos del dataset `s8-evaluation-v1`.
- **Fuente / ventana / granularidad:** dos lotes R2 de 14 en ventanas RPD distintas; un registro por caso y un agregado único tras combinar 28.
- **Inclusiones:** modelo `gemini-3.6-flash`, prompt y parser congelados, nulos sólo coinciden con nulos.
- **Exclusiones:** confidence y semántica de justification; no hay reruns selectivos.
- **Esperado / umbral:** `≥ 85.00 %`, equivalente a al menos `24/28`.
- **Tipo / método:** decisorio; comparación exacta.
- **Limitaciones:** dataset sintético pequeño; concordancia no prueba exactitud en reportes reales.
- **Estado:** `NOT_DEMONSTRATED`.

### HT2-02 — Exactitud por campo

- **Relación / escenario:** HT-2; EX-09.
- **Clasificación:** `DERIVABLE` de una ejecución R2 válida.
- **Definición / fórmula / unidad:** exactitud separada de categoría, institución y prioridad; `(coincidencias exactas del campo / 28) × 100`, tres porcentajes.
- **Población / fuente / ventana / granularidad / inclusiones / exclusiones:** iguales a HT2-01.
- **Esperado / umbral:** sin umbral; diagnóstico.
- **Tipo / método:** diagnóstico de error por campo.
- **Limitaciones:** no sustituye HT2-01 aunque sus valores sean altos.
- **Estado:** `NOT_DEMONSTRATED`.

### HT2-03 — Inconsistencia

- **Relación / escenario:** HT-2 y PE-3; EX-09.
- **Clasificación:** `DERIVABLE` de una ejecución R2 válida.
- **Definición / fórmula / unidad:** `(predicciones booleanas de inconsistency exactas / 28) × 100`; porcentaje; también se presenta concordancia estricta que añade este campo a HT2-01.
- **Población / fuente / ventana / granularidad / inclusiones / exclusiones:** iguales a HT2-01.
- **Esperado / umbral:** sin umbral; diagnóstico.
- **Tipo / método:** diagnóstico; matriz de confusión booleana.
- **Limitaciones:** no participa en el `PASS/FAIL` de HT-2.
- **Estado:** `NOT_DEMONSTRATED`.

**Validez R2:** protocolo `s8-e2-r2`; dataset SHA-256 `3bc54d591dc104aeabeecb2f5cc7c7a38e70ba042e7f045321ab33ae9b5efad4`; prompt `s8-classification-v1` con SHA-256 `f3c803984806a3b934db0417f6cc3ce59a49cdde1e30b4c86b862c2e11c665ca`; timeout 60 s; pacing 20 s; dos lotes fijos de 14; cero reintentos. Cualquier error, timeout, caso ausente, divergencia de hash/parámetro o artefacto perdido invalida el run: HT-2 queda `NOT_DEMONSTRATED`, no `FAIL`. Los dos lotes forman una sola corrida y no se interpreta el primero por separado.

### HT3-01 — Ausencia de cédula textual persistida

- **Relación / escenario:** HT-3, PE-2, OE-3; EX-02 y EX-08.
- **Clasificación:** `DERIVABLE`.
- **Definición / fórmula / unidad:** cero columnas/campos de persistencia, logs o exports experimentales con la cédula sintética en texto plano; conteo de hallazgos prohibidos.
- **Numerador / denominador / población:** hallazgos de texto plano / conjunto de esquema, filas, logs y exports del run.
- **Fuente / ventana / granularidad:** inspección de esquema y búsqueda exacta de canarios sintéticos desde pre-check hasta cleanup; por campo/artefacto.
- **Inclusiones:** base, logs capturados y artefactos S11; selfie se inspecciona por clasificación, no mediante OCR.
- **Exclusiones:** valor sólo en memoria durante normalización/cotejo; secretos nunca se exportan.
- **Esperado / umbral:** `0` hallazgos.
- **Tipo / método:** decisorio; inspección automatizada con canarios.
- **Limitaciones:** no prueba anonimato, irreversibilidad absoluta ni ausencia en sistemas fuera del alcance.
- **Estado:** `NOT_DEMONSTRATED`.

### HT3-02 — Unicidad por HMAC

- **Relación / escenario:** HT-3; EX-02 y EX-08.
- **Clasificación:** `DERIVABLE`.
- **Definición / fórmula / unidad:** documentos sintéticos normalizados iguales producen el mismo HMAC y el segundo alta activa es rechazada por unicidad; documentos distintos producen HMAC distintos en el dataset; conteos y predicado booleano.
- **Numerador / denominador / población:** casos esperados satisfechos / casos HMAC congelados del fixture.
- **Fuente / ventana / granularidad:** resultados de prueba y constraints de la base aislada; por caso.
- **Inclusiones:** normalización, HMAC con secreto efímero de experimento, constraint única y revisión humana sintética.
- **Exclusiones:** afirmaciones criptográficas universales o ataques de diccionario fuera del protocolo.
- **Esperado / umbral:** `100 %` de casos congelados y cero colisiones entre entradas distintas.
- **Tipo / método:** decisorio; prueba parametrizada e inspección.
- **Limitaciones:** evidencia acotada al conjunto sintético.
- **Estado:** `NOT_DEMONSTRATED`.

### HT4-01 — Flujos críticos sin errores no controlados

- **Relación / escenario:** HT-4, PE-6, OE-5, OE-6; EX-02…EX-08.
- **Clasificación:** `REQUIRES_INSTRUMENTATION`.
- **Definición / fórmula / unidad:** `(escenarios actuales que alcanzan el estado esperado sin excepción, HTTP 5xx, crash o timeout no previsto / escenarios actuales ejecutados) × 100`; porcentaje.
- **Numerador / denominador / población:** EX-02…EX-08 aprobados / siete escenarios; EX-09/10 deciden otras hipótesis y EX-11 está diferido.
- **Fuente / ventana / granularidad:** runner full-stack y manifiestos por escenario dentro de un run; escenario.
- **Inclusiones:** errores esperados que devuelven código/documento contractual y no alteran estado se registran como controlados.
- **Exclusiones:** UI con API interceptada y funcionalidades de EX-11.
- **Esperado / umbral:** `100 %`.
- **Tipo / método:** decisorio; E2E full-stack e inspección postcondición.
- **Limitaciones:** conjunto sintético finito, no disponibilidad continua.
- **Estado:** `NOT_DEMONSTRATED`.

### HT4-02 — Completitud de trazabilidad

- **Relación / escenario:** HT-4, PE-2, PE-6; EX-02…EX-08.
- **Clasificación:** `REQUIRES_INSTRUMENTATION`.
- **Definición / fórmula / unidad:** `(eventos esperados con estado/historial/auditoría/ledger/evidencia exigidos y correlacionables / eventos esperados por la matriz congelada) × 100`; porcentaje.
- **Numerador / denominador / población:** celdas obligatorias satisfechas / celdas obligatorias de la matriz de eventos del protocolo.
- **Fuente / ventana / granularidad:** export SQL/API minimizado posterior a cada escenario; evento.
- **Inclusiones:** creación, validación, confirmación, evidencia, transición, autocierre, reversión, revalidación y rechazo.
- **Exclusiones:** intentos rechazados cuando el contrato no exige persistirlos; se conserva el response como evidencia de control.
- **Esperado / umbral:** `100 %`.
- **Tipo / método:** decisorio; reconciliación evento×artefacto.
- **Limitaciones:** `AuditLog` es append-only por política de aplicación, no WORM o prueba criptográfica.
- **Estado:** `NOT_DEMONSTRATED`.

### HT5-01 — Cobertura del backend crítico

- **Relación / escenario:** HT-5, PE-5, OE-3, OE-6; EX-02…EX-10.
- **Clasificación:** `REQUIRES_INSTRUMENTATION`.
- **Definición / fórmula / unidad:** porcentaje de líneas ejecutables cubiertas en la unión exacta de archivos `INCLUDED` del manifiesto §4, según `coverage.py`/`pytest-cov` bloqueado por el proyecto.
- **Fórmula:** `(líneas ejecutadas del universo / líneas ejecutables del universo) × 100`.
- **Numerador / denominador / población:** líneas cubiertas / statements ejecutables reportados para los archivos incluidos; universo fijado por commit y hash de manifiesto.
- **Fuente / ventana / granularidad:** artefactos `coverage.json`, XML/HTML y log de pytest de una ejecución limpia; archivo y agregado.
- **Inclusiones:** sólo rutas `INCLUDED`, sin tests.
- **Exclusiones:** filas `EXCLUDED`, código generado, migraciones y líneas excluidas por `coverage.py` justificadas en el reporte.
- **Esperado / umbral:** `≥ 80.00 %`.
- **Tipo / método:** decisorio; ejecución completa de tests asociados, combinación de cobertura sólo si comparten commit/config.
- **Limitaciones:** cobertura no demuestra corrección, calidad o seguridad; el porcentaje global previo no se reutiliza.
- **Estado:** `NOT_DEMONSTRATED`.

### HT6-01 — Cumplimiento de reglas de incentivos

- **Relación / escenario:** HT-6, PE-4, OE-4; EX-02…EX-07 y EX-11.
- **Clasificación:** `DERIVABLE` para reglas actuales; los componentes de EX-11 son `FUTURE_FEATURE` en la matriz de trazabilidad.
- **Definición / fórmula / unidad:** `(reglas ejecutables satisfechas exactamente / reglas ejecutables previstas para el alcance del run) × 100`; porcentaje.
- **Numerador / denominador / población:** reglas de evento, monto, estado, institución, ciclo, reversión, duplicidad e idempotencia satisfechas / reglas actuales congeladas en la matriz de eventos.
- **Fuente / ventana / granularidad:** ledger, reporte, confirmaciones, historial y auditoría del run; una fila por regla×escenario.
- **Inclusiones:** creación `+20` pendiente, confirmación válida `+10`, validación/acreditación `+30`, conversión 1:1, resolución `+50`, rechazo/anulación, reversión/revalidación por ciclo, unicidad e idempotencia.
- **Exclusiones:** ranking y canje hasta implementar EX-11; no se eliminan del texto de HT-6 ni se cuentan como aprobados.
- **Esperado / umbral:** `100 %` de las reglas ejecutables y cero discrepancias.
- **Tipo / método:** decisorio para el subconjunto ejecutado; comparación exacta.
- **Limitaciones:** puntos correctos no prueban motivación ni impacto; mientras EX-11 siga diferido, HT-6 completa permanece `NOT_DEMONSTRATED` aunque el subconjunto actual alcance 100 %.
- **Estado:** `NOT_DEMONSTRATED`.

### HTP-01 — Cumplimiento técnico compuesto

- **Relación / escenario:** PG/OG e hipótesis técnica principal; EX-02…EX-11.
- **Clasificación:** `DERIVABLE`.
- **Definición / fórmula / unidad:** conjunción lógica de HT-1…HT-6 y requisitos funcionales/no funcionales trazados; predicado.
- **Numerador / denominador / población:** no aplica cociente; universo de hipótesis técnicas.
- **Fuente / ventana / granularidad:** decisiones firmadas de S11-D/E para una misma versión metodológica; hipótesis.
- **Inclusiones:** todos los indicadores decisorios y limitaciones.
- **Exclusiones:** HE, que sustenta pertinencia empírica pero no rescata un fallo técnico.
- **Esperado / umbral:** todas HT en `PASS` y ninguna dependencia obligatoria diferida.
- **Tipo / método:** decisorio compuesto, sin promediar.
- **Limitaciones:** no prueba adopción o impacto urbano.
- **Estado:** `NOT_DEMONSTRATED`.

## 3. Protocolo de carga congelado para HT-1

### 3.1 Operación y entorno

- **Operación:** creación autenticada de reporte ciudadano.
- **Endpoint:** `POST /api/v1/reports/`.
- **Request:** multipart con categoría activa, descripción sintética única, JPEG/PNG sintético válido y coordenadas dentro del dominio permitido; token de ciudadano sintético verificado.
- **Éxito:** HTTP `201`, cuerpo contractual válido, `Report` persistido una sola vez y `RewardTransaction(REPORT_CREATED)` única.
- **Arquitectura:** PostgreSQL/PostGIS y Redis reales en entorno aislado; Celery no-eager; un worker con `--concurrency=2`; adaptador IA fake; mismas versiones bloqueadas del baseline.
- **Carga:** diez usuarios concurrentes, un VU por usuario, cinco requests secuenciales por VU; 50 requests totales dentro de una ventana medida de 60 s.
- **Justificación de concurrencia:** el scope `report_create` permite 5/h por usuario; diez identidades evitan modificar o eludir el throttle y permiten el objetivo de 50 creaciones.
- **Dataset:** 50 payloads/fotos sintéticos únicos generados desde la semilla y versión congeladas; no se copian reportes productivos.
- **Ventana:** empieza con la barrera simultánea de los diez VU y termina a los 60 s; todo request iniciado dentro de la ventana se registra, y los que terminen después cuentan como no exitosos para throughput.
- **Warm-up:** sólo health/readiness fuera de la medición; no se crea un reporte de calentamiento.
- **Reset:** base y objetos privados experimentales vacíos, Redis/throttles limpios, broker sin tareas y workers listos; el procedimiento exacto se versionará en S11-B.

### 3.2 Validez y exclusiones

El run es válido si conserva commit/config, emite exactamente 50 requests, no cambia dataset ni concurrencia, captura todos los tiempos y persiste pre/post-checks y hashes. Un HTTP 4xx/5xx es un resultado fallido válido y reduce el throughput; una caída del generador, pérdida de resultados, divergencia de configuración o falta de artefactos invalida el run y deja HT-1 en `NOT_DEMONSTRATED`.

No entran en los percentiles el login previo, aprovisionamiento de fixtures, reset, espera de finalización IA, generación/copia de artefactos ni limpieza. Sí entran autenticación del request, parsing, almacenamiento de foto, transacción de reporte/ledger y encolado al commit. La inspección de cola confirma asincronía, pero su latencia se reporta sin inventar un umbral.

## 4. Manifiesto congelado del backend crítico para HT-5

La unidad de inclusión es el **archivo completo**: todos sus statements ejecutables forman el denominador, aunque la columna `Símbolos` explique qué lo vuelve crítico. Cambiar esta lista exige versión metodológica previa a medir.

| Módulo | Archivo | Funciones/clases críticas | Estado | Razón | Relación HT |
|---|---|---|---|---|---|
| core | `backend/apps/core/enums.py` | `ReportStatus`, `ReportPriority`, `RejectionReason`, `ActorType` | INCLUDED | Vocabulario de estados, prioridad, rechazo y actor | HT-4, HT-6 |
| core | `backend/apps/core/permissions.py` | `IsCitizen`, `IsVerifiedCitizen`, `IsInstitutionOperator`, `IsAdmin`, `IsOperatorOfReportInstitution` | INCLUDED | Denegación por rol y tenant | HT-3, HT-4 |
| core | `backend/apps/core/services/audit.py` | `log` | INCLUDED | Escritura de trazabilidad transversal | HT-4 |
| core | `backend/apps/core/services/storage.py` | upload/download/signed URL/delete/redaction | INCLUDED | Frontera de evidencia e imágenes privadas | HT-3, HT-4 |
| auth | `backend/apps/auth_app/models.py` | `User`, `CitizenProfile` y constraints | INCLUDED | Identidad, rol, estado y HMAC persistido | HT-3, HT-5 |
| auth | `backend/apps/auth_app/services/accounts.py` | `login_with_google`, `resolve_user` y helpers | INCLUDED | Vinculación de identidad Google | HT-3 |
| auth | `backend/apps/auth_app/services/google.py` | `verify_id_token`, `GoogleIdentity` | INCLUDED | Verificación de identidad del proveedor | HT-3 |
| auth | `backend/apps/auth_app/services/identity.py` | normalize/HMAC/match, selfie, submit/resubmit | INCLUDED | Regla central de identidad verificada-privada | HT-3 |
| admin | `backend/apps/admin_app/models.py` | `Institution`, `Category`, `InstitutionOperator` y constraints | INCLUDED | Catálogos y alcance institucional | HT-2, HT-4 |
| admin | `backend/apps/admin_app/services/identity_review.py` | `review`, cotejo, bloqueo, auditoría | INCLUDED | Aprobación/rechazo humano de identidad | HT-3, HT-4 |
| reports | `backend/apps/reports_app/models.py` | `Report`, `ReportMedia`, `ReportStatusHistory`, `ResolutionEvidence`, `ReportConfirmation` | INCLUDED | Estado, ciclo, evidencia y constraints | HT-4, HT-6 |
| reports | `backend/apps/reports_app/selectors.py` | querysets institucionales y `find_possible_duplicates` | INCLUDED | Tenancy, contexto y duplicados consultivos | HT-4, HT-5 |
| reports | `backend/apps/reports_app/services/photos.py` | `prepare_report_photo` y validadores | INCLUDED | Evidencia inicial, hash y normalización | HT-4 |
| reports | `backend/apps/reports_app/services/reports.py` | validación, `create_report`, `_persist_report`, enqueue | INCLUDED | Creación atómica y frontera asíncrona | HT-1, HT-4, HT-6 |
| reports | `backend/apps/reports_app/services/confirmations.py` | eligibility y `confirm_report` | INCLUDED | Confirmación, antiabuso y +10 | HT-4, HT-6 |
| reports | `backend/apps/reports_app/services/validation.py` | `validate_report`, `reject_report` | INCLUDED | Validación/rechazo y comienzo de ciclo | HT-4, HT-6 |
| reports | `backend/apps/reports_app/services/lifecycle.py` | `transition_report_status` y transiciones | INCLUDED | Máquina de estados y resolución/cierre | HT-4, HT-6 |
| reports | `backend/apps/reports_app/services/resolution_evidence.py` | creación, acceso, ciclo e idempotencia | INCLUDED | Evidencia del ciclo vigente | HT-4, HT-6 |
| reports | `backend/apps/reports_app/services/evidence_access.py` | URL/token de acceso | INCLUDED | Acceso privado controlado | HT-4 |
| reports | `backend/apps/reports_app/services/reversal.py` | `revert_report_validation` y guards | INCLUDED | Reversión administrativa | HT-4, HT-6 |
| reports | `backend/apps/reports_app/services/auto_close.py` | cutoff/eligibility/close | INCLUDED | Autocierre determinista | HT-4 |
| reports | `backend/apps/reports_app/tasks.py` | `auto_close_reports`, retry/failure | INCLUDED | Ejecución asíncrona del autocierre | HT-1, HT-4 |
| rewards | `backend/apps/rewards_app/models.py` | `RewardTransaction`, `RewardRedemption` y constraints | INCLUDED | Ledger y constraints; existencia de redención no prueba canje | HT-6 |
| rewards | `backend/apps/rewards_app/services/ledger.py` | creación, confirmación, validación, rechazo, resolución, reversión | INCLUDED | Reglas actuales de incentivos | HT-6 |
| IA | `backend/apps/ai_client_app/models.py` | `AiClassification` y constraints | INCLUDED | Persistencia de clasificación y revisión | HT-2, HT-4 |
| IA | `backend/apps/ai_client_app/parser.py` | parser estricto y validadores | INCLUDED | Validez estructural de salida | HT-2 |
| IA | `backend/apps/ai_client_app/catalog_mapping.py` | resolución de categoría/institución | INCLUDED | Aplicación segura de slugs | HT-2 |
| IA | `backend/apps/ai_client_app/policy.py` | umbral, resolución, cambios | INCLUDED | Política de aplicación asistiva | HT-2, HT-4 |
| IA | `backend/apps/ai_client_app/media.py` | carga de imagen y MIME | INCLUDED | Entrada minimizada al proveedor | HT-2, HT-3 |
| IA | `backend/apps/ai_client_app/orchestration.py` | `process_report_classification` y helpers | INCLUDED | Orquestación, auditoría y concurrencia | HT-1, HT-2, HT-4 |
| IA | `backend/apps/ai_client_app/tasks.py` | retry y `classify_report` | INCLUDED | Ejecución asíncrona y fallos controlados | HT-1, HT-2 |
| IA | `backend/apps/ai_client_app/adapters/runtime.py` | `RuntimeAdapter`, `get_runtime_adapter` | INCLUDED | Selección segura del proveedor | HT-1, HT-2 |
| IA | `backend/apps/ai_client_app/adapters/gemini.py` | schema, cliente, adaptador y traducción de errores | INCLUDED | Contrato real con el proveedor | HT-2 |

### Exclusiones explícitas

| Patrón/módulo | Estado | Razón | Verificación alternativa / relación |
|---|---|---|---|
| `**/migrations/**` | EXCLUDED | Código generado/histórico; incluirlo inflaría el denominador | Migración en base vacía y checks de esquema; HT-3/4/6 |
| `**/tests/**` | EXCLUDED | Los tests producen cobertura, no forman el producto medido | Suite y listado de tests en artefactos |
| `**/admin.py`, `**/apps.py`, `**/__init__.py` | EXCLUDED | Registro/configuración sin reglas críticas del dominio | Smoke/config checks |
| vistas y serializers HTTP | EXCLUDED | HT-5 mide las reglas críticas en servicios, como fija el Plan §28; el transporte se verifica aparte | Integración API y EX-02…EX-08; HT-4 |
| `ai_client_app/evaluation/**` | EXCLUDED | Harness experimental, no dominio operativo | EX-09 y sus propios tests; HT-2 |
| `map_app/**` | EXCLUDED | Publicación/lectura, no regla crítica enumerada para HT-5 | Integración funcional; no decide HT-5 |
| `notifications_app/**` | EXCLUDED | Canal auxiliar y no regla decisoria congelada | Pruebas específicas; dependencia |
| `analytics_app/**`, `social_ingestion_app/**` | EXCLUDED | Fuera del flujo técnico decisorio actual | Trabajo futuro |
| frontend completo | EXCLUDED | HT-5 dice backend | EX-02…EX-08 para comportamiento full-stack |
| ranking/canje futuros | EXCLUDED | `FUTURE_FEATURE`; no se cubre código inexistente o tabla sin flujo | EX-11 después de implementación |

El informe HT-5 debe publicar el hash de este documento, commit, versiones de Python/pytest/coverage, comando, listado de archivos realmente medidos y cobertura por archivo. Si cualquier ruta `INCLUDED` falta del reporte, el resultado es inválido y HT-5 queda `NOT_DEMONSTRATED`.
