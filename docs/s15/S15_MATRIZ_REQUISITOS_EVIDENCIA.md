# Sprint 15 Matriz de requisitos y evidencia

**Seguimiento vigente:** [validación y estado actual](S15_INFORME_VALIDACION.md). Este enlace distingue la segunda etapa de los cortes anteriores conservados en este documento.

**Corte:** 20/10/2026. **Base:** `3d89182`. **Protocolo local:** `s15-local-v1`.
**Alcance actual:** validación local con datos sintéticos, mediciones con proveedores
sustituidos y diagnóstico remoto acotado de solo lectura. P0-M se resuelve por carril;
la evaluación IA y el cierre completo de S15 siguen pendientes.

**Decisiones posteriores del usuario:** [registro actualizado](S15_DECISIONES_Y_PENDIENTES.md).
Las mediciones de volumen se ejecutaron localmente y las comprobaciones remotas usarán
el despliegue existente; un segundo entorno remoto deja de ser una dependencia. El
presupuesto API es USD 5–10 y la defensa se prevé aproximadamente el 16/12/2026. La
incorporación consolidada al `.docx` se realizará después de S16, con la evidencia técnica
preparada antes. Las observaciones E-DAT…E-GOV conservan su alcance original.

Esta matriz desarrolla el [plan de implementación](S15_PLAN_IMPLEMENTACION.md) y el
[protocolo de validación local](S15_PROTOCOLO_VALIDACION.md). Los comandos, artefactos
privados y resultados consolidados se recogen en el
[informe de preparación local](S15_INFORME_PREPARACION_LOCAL.md). Las referencias a RNF e hipótesis identifican el requisito que
necesita evidencia; **no constituyen una adjudicación** ni modifican los veredictos de S11.

Los estados distinguen instrumento probado, ejecución local observada, consolidación
pendiente y medición final pendiente. Una suite del instrumento no sustituye la medición
del producto; tampoco se suman resultados de versiones diferentes para presentar una
verificación final del árbol actual.

## Evidencia disponible al corte actual

| Identificador | Ejecución observada | Resultado y alcance | Límite y seguimiento |
| --- | --- | --- | --- |
| E-DAT | Verificación fresca del dataset funcional original con el verificador reforzado | **1.329/1.329 controles correctos**, auditoría obligatoria **268/268** y notificaciones **116/116** | SQL, ubicación/SRID/institución y bytes originales comprobados. Las notificaciones pendientes del snapshot no demuestran entrega |
| E-REST | Restauraciones nuevas de DB y objetos en mundos API y navegador propios | **1.329/1.329 controles correctos** antes de consumir cada copia | No se atribuye ese resultado a la copia histórica de 1.225 checks; sus resultados originales se conservan en el informe de preparación |
| E-IA | Suite offline fresca de `tools/evaluation/`, con dependencias del backend | **28 pruebas unitarias correctas**, sin omisiones por dependencias; formato, puntuación, replay, errores e integridad del instrumento | No hay evaluación independiente con fotos inéditas ni oro humano; no es evidencia de concordancia RNF-05 |
| E-CAR | Suite offline fresca de `tools/performance/` | **37 pruebas unitarias correctas**, incluido binding de bytes exactos manifiesto→k6→serie→SQL→reporte | Las series legadas quedan diagnósticas; las pruebas unitarias no se cuentan como solicitudes de carga |
| E-AUX | Suite fresca completa de `tools/s15/` en contenedor sin red externa | **43 pruebas correctas** de Storage, SQL, API, perfiles, navegador, relay, correo y guards remotos | Instrumentos probados, no servicios externos ni llamadas a IA |
| E-HTTP | Seis ventanas k6 con SQL antes/después y tareas correlacionadas | Referencia sync/1: W1 incumple latencia, W2/W3 cumplen. Final gthread/4: **3/3 ventanas**, cada una con 50 persistencias en 60 s, cero errores, **P50 234–377 ms y P99 470–1.008 ms** | Perfil local explícito; no se combinan ventanas ni se oculta W1. IA noop y correo sustituido; no extrapola a Heroku/Supabase |
| E-READ | Cuatro perfiles k6 con expectativas SQL congeladas sobre 86 reportes | **360/360 lecturas correctas**; SQL posterior sin cambios; caché por claves/PTTL y comandos, con hits inferidos y llenados separados | M1 conserva caché no verificada; ventanas pobladas pueden vencer durante la serie. No se estima P99 frío con un solo fallo |
| E-API | Recorrido HTTP real final 005 y comparación SQL independiente | **180/180 comprobaciones**, 72 solicitudes esperadas; negativas con hashes de contenido de nueve tablas | Gunicorn, DEBUG=False y proveedores sustituidos/desactivados; no incluye renderizado de navegador |
| E-UX | Auditoría y correcciones de interfaz | **67/67 E2E**, 417 pruebas Vitest; build, tipos, lint, formato y seis regresiones de rutas correctos | API interceptada en esta suite; no acredita proveedores ni backend real |
| E-REM | Dyno temporal Heroku hacia Supabase y smoke HTTP público | **30 SELECT 1**, mediana 66,18 ms; cinco aperturas, mediana 409,30 ms; seis rutas HTTP 200 | Solo lectura/TLS/rollback; no RTT puro, API autenticada ni capacidad productiva. Corresponde al despliegue existente |
| E-BRW | Recorrido nativo con API local real y SQL independiente | **9/9 navegador y 63/63 controles SQL**, F1…F8 más dashboard | Storage sintético; canario ausente del dump y cuatro logs examinados, entradas privadas excluidas |
| E-NAV | Serie descriptiva de pantallas posterior a F8 | **18/18 observadas**, N=3 por ruta/perfil; muestras y proxies conservados | No acredita CLS/INP/P99 ni usuarios de campo; discrepancia de TTFB móvil documentada |
| E-CAN | Suplemento en mundo independiente | **3/3 canjes 201**, saldo 1.010→10, tres duplicados 409; diez tablas conciliadas | N=3 exploratorio; notificaciones pendientes, sin adjudicación RNF-02 |
| E-ID | Matriz técnica de identidad y esquema local | **220 casos** dentro de E-REG; HMAC único, sin columna de número y canario local comprobado | Subconjunto de la suite global, no una ejecución adicional ni readjudicación HT-3 |
| E-REG | Suite fresca del backend de dominio | **2.349 pruebas correctas, 0 fallos y 0 errores, en 225,49 s**; cobertura de producto **94,46 %** sobre **7.351 sentencias, 407 sin cubrir** | Resultado local de esa suite; mismo universo de compuerta, sin excluir herramientas Django nuevas |
| E-HT5 | Reporte separado sobre los mismos datos de cobertura de la suite final | **96,91 %**, con **2.233 sentencias y 69 sin cubrir**, según el manifiesto HT5-01 | No intercambiar este denominador con producto ni adjudicar nuevamente HT-5 |
| E-GOV | Custodia GOV-T054 con selección exacta | **1 prueba correcta** de GOV-T054 | No se presenta como repetición de la suite experimental completa ni reapertura de S11 |

Fuentes operativas: [dataset y verificador](../../backend/apps/core/final_dataset/README.md),
[entorno y auxiliares S15](../../tools/s15/README.md),
[harness de carga](../../tools/performance/README.md) y
[evaluación IA](../../tools/evaluation/README.md). Los resultados anteriores se identifican
por comando, versión y hashes en el informe; esta tabla no publica sesiones, fotografías,
volcados ni rutas sensibles de evidencia.

## Requisitos, preestados e instrumentos

| Requisito o referencia | Escenario | Preestado necesario | Instrumento | Resultado esperado | Evidencia que debe conservarse | Estado P0-L | Estado para S15 completo |
| --- | --- | --- | --- | --- | --- | --- | --- |
| P0: aislamiento y procedencia | Preparar y verificar el entorno sintético | PostGIS, Redis y Storage propios; consumidores ajenos excluidos; sin credenciales reales | `tools/s15/check_local.sh` y guards del generador | Destinos permitidos; broker, resultados y caché dedicados; ningún efecto sobre S11, S14 o producción | Configuración no secreta, etiquetas/red, procesos y versiones | Perfil local DEBUG=False/Gunicorn y mundos DB/Redis/Storage propios; relay browser de loopback revisado | Demostrado para recursos sintéticos; proveedores y cuentas reales se comprueban por separado |
| P0: custodia y reproducibilidad | Congelar entradas y registrar cada corrida | SHA, protocolo, semilla, fecha local, denominadores y destino privado definidos | Manifiestos de datos, carga e IA y sus validadores | Entradas identificables; archivos privados; ausencia de secretos en material publicable; abortos conservados | Hashes, comandos, tiempos, estado completo o inválido y revisión de exportación | Índices privados por carril y binding de bytes exactos; fallos y versiones conservados | Consolidar índice global y custodia del material IA que aún no se recibió |
| §25: mínimos del dataset | Generar reportes, ciudadanos, confirmaciones y medios | DB migrada sin datos de negocio; catálogos estándar; web, workers y Beat detenidos | `generate_final_dataset` y `verify_final_dataset` | Al menos 20 reportes, 10 ciudadanos y 30 confirmaciones; categorías, prioridades y medios válidos y distintos | Manifiesto, receta, oráculo, journal y comprobación SQL/bytes | E-DAT: 36 reportes, 12 ciudadanos, 32 confirmaciones; 1.329/1.329 correctos | Snapshot de octubre conservado; versión de diciembre con identidad propia para la demo |
| §25: historia y eventos obligatorios; referencia HT-4 | Contrastar cada transición, ledger, auditoría y notificación | Dataset generado o restaurado desde su propio manifiesto | Verificador de SQL independiente y oráculo declarativo | Relaciones y cardinalidades exactas; eventos obligatorios enlazados; no confundir avisos pendientes con entrega | Numerador/denominador de eventos, fallos y vínculos por caso | E-DAT: auditoría 268/268 y notificaciones 116/116; E-API 180/180; carga con tareas correlacionadas | Clasificación IA y correo reales pendientes; no confundir filas, aceptación local y delivery |
| §25: restauración DB y objetos | Restaurar en destinos vacíos y verificar | Dump y bytes coordinados; procesos detenidos; configuración compatible y colas propias | Restauración operativa, verificador y Storage local | SQL y objetos coinciden; hashes originales/normalizados conservados; acceso comprobado después | Comandos de respaldo/restauración, hashes y reporte de verificación | E-REST: nuevas restauraciones API y navegador1.329/1.329; medios firmados locales comprobados | Navegador/postchequeo en curso; respaldo del despliegue antes de futuras escrituras |
| RNF-01: P50; referencia HT-1 | Medir endpoints críticos sin IA síncrona | Árbol y entorno congelados; población, perfiles y caché declarados | Escenarios k6 y reporter local | P50 ≤ 800 ms sobre población contractual, con errores separados | Series completas, estimador, N, preflight, caché y configuración | E-HTTP: candidato3/3, P50 234–377ms; E-READ360/360 con poblaciones separadas | Umbral observado en perfil local declarado; no extrapolación productiva |
| RNF-02: P99; referencia HT-1 | Medir colas de latencia bajo carga | Muestras y repeticiones fijadas; financiación y cupos suficientes para canjes | k6 y análisis por endpoint/ventana | P99 ≤ 2.000 ms sin sustituir fallos por respuestas rápidas ni promediar ventanas para ocultarlos | Distribución por población, fallos, tardíos y presupuesto de muestras | E-HTTP: candidato3/3, P99 470–1.008ms; se conserva baselineW1 incumplido | N50 por creación limita cola poblacional; canjes y estratos fríos no adjudican P99 estable |
| RNF-03: creación; referencia HT-1 | Diez ciudadanos, cinco POST secuenciales por ciudadano | Cuotas limpias; 50 fotos únicas; barrera y ventana monotónica de 60 s; DB dedicada | k6 `create`, export SQL y reconciliador | Al menos 50 respuestas 201 completas antes del corte y persistencia correlacionada; tardíos y errores explícitos | Resultados por caso, UUID/hash, ventana, SQL antes/después y export reconciliado | E-HTTP: cada una de tres ventanas finales completa50altas201 y SQL en60s, cero errores | Objetivo observado por ventana local; sin proyectar capacidad sostenida desde ráfagas breves |
| Referencia HT-1: completitud asíncrona | Seguir encolado, clasificación y notificación por reporte | Workers propios habilitados bajo perfil explícito; plazo de drenaje y proveedores definidos | Correlación HTTP → SQL → tarea → clasificación/auditoría/notificación | Estado final observado por caso; indisponibilidad, reintentos y sustituciones declarados | IDs internos minimizados, tiempos y conteo esperado/observado por etapa | En cada ventana100task IDs SUCCESS,50noopsIA y50avisos ENVIADO/1intento,50aceptacioneslocales y cola vacía | Proveedor IA desactivado y correo sustituido: clasificación/delivery reales pendientes |
| RNF-04; referencia HT-5 | Ejecutar regresión y cobertura del árbol final | DB de prueba aislada y universos de cobertura fijados | Comandos del README y `tools/s15/check_local.sh` | Compuertas aplicables correctas; cobertura ≥ 80 % del universo comprometido, con producto separado de HT5-01 | JUnit, cobertura, configuración de denominadores, lint, formato y migraciones | E-REG2.349PASS,94,46%producto; E-HT5 96,91%separado; E-GOV1/1PASS | Conservar universos; CI remoto y árbol integrado se registran después sin readjudicar S11 |
| RNF-05; referencia HT-2: instrumento | Validar, recolectar por intento y volver a puntuar offline | Fixtures de transporte identificadas como sintéticas; sin cliente real | `s15_run.py`, `s15_score.py` y suite IA | N íntegro; triple exacta antes de policy; secundaria separada; nulos estrictos; errores y corrupción nunca omitidos | Registros minimizados, hashes, política de errores, uso conocido/desconocido y score reproducible | E-IA: 28 pruebas correctas del instrumento; sin cuota real | Habilita preparar la evaluación, no acredita concordancia |
| RNF-05; referencia HT-2: evaluación independiente | Clasificar fotos y texto reservados con prompt v2 | Fotos inéditas autorizadas, oro con doble revisión, N/modelo/catálogo/protocolo congelados y presupuesto | Ejecutor multimodal con adaptador actual, habilitado explícitamente | Primaria categoría/institución/prioridad; decisión `100 × aciertos ≥ 85 × N`; categoría/prioridad publicada aparte | Entradas congeladas, intento por caso, errores, tokens observados y límites de costo | Pendiente: no hay fotos inéditas ni oro disponibles; ninguna llamada real | Evaluación válida pendiente; no adjudicado |
| Referencia HT-3: identidad y privacidad | Matriz de igualdad, desigualdad, normalización y unicidad; búsqueda de canario sintético | Valores sintéticos prospectivos, variantes y superficies de inspección fijados | Servicios/API de identidad y consultas de esquema, datos, logs y exports | Igualdad/desigualdad y unicidad correctas; cédula canario ausente de esquema, datos, logs y exports; sin publicar su valor | Casos, resultados, búsquedas y superficies cubiertas o excluidas | E-ID: 220 pruebas existentes, esquema READ ONLY y E-BRW: HMAC/canario local | [Matriz técnica y límites](S15_INFORME_IDENTIDAD.md) comprobados; uso académico separado, no adjudicado |
| RNF-06; referencia HT-4 | Ocho flujos críticos, clasificación y asignación | Copia restaurada, sesiones a reloj real, roles/membresías y servicios reales/sustitutos declarados | E2E del README, API real y reconciliación de eventos | Ocho flujos correctos; contratos BFF/API y permisos por rol/tenant observados | JUnit, vídeos/capturas y vínculos con SQL/eventos; límites por proveedor | Nuevo recorrido S15 nativo 9/9: F1…F8 y dashboard, API real local y Storage sintético | Postchequeo SQL/canario 63/63 aprobado; Google, IA y correo reales siguen separados |
| RNF-07; referencia HT-6 | S-01…S-12 y API = SQL | Preestados compatibles por escenario, incluidos mes y cupo; financiación por servicios | Matriz detallada siguiente, generador, verificador y controles HTTP | Reglas al 100 %, importes exactos y negativas sin nuevos débitos, canjes o avisos | Observaciones por paso, ledger/auditoría y conciliación API/SQL | E-DAT 1.329 y E-API 180/180, incluidos permisos, canje legítimo 1060→60 y reversión→−140 | Las observaciones históricas S-01…S-12 se conservan; ver alcance HTTP exacto en informe API |
| RNF-01/02 y §25: mapa público | Listado, detalle y categorías; perfiles de caché declarados | Dataset estable, expectativas congeladas y presupuesto de cuota por IP | k6 `map`, expectativas semánticas y observación externa de caché | Respuestas y población correctas; caché fría/caliente identificada sin inferirla solo de latencia | Series, contratos, N y evidencia de caché/TTL | E-READ: dos perfiles mapa 90/90 cada uno, SQL antes/después; hits inferidos/llenados/PTTL separados | M1 caché no verificada; M2 inicialmente poblada con expiración natural, no P99 frío estable |
| §25 y RNF-07: mes, ranking y privacidad | Corte mensual, empate 10/11 y cuatro preferencias de privacidad | Más de diez candidatos; eventos antes/después del mes; ancla compatible con la demo | Oráculo/SQL y API pública de ranking | Mes anterior excluido; orden/desempate y Top 10 exactos; privacidad conforme a preferencia | Fechas/casos, agregación independiente y respuesta pública revisada | SQL/oráculo y E-API correctos; dos perfiles ranking 90/90 y privacidad/Top 10 exactos | Conservar corte de octubre; regenerar y comprobar mes de diciembre para la demo |
| §25: usabilidad y frontend | Recorrer interfaces y medir carga con red/dispositivo declarados | Build de producción; dataset estable; viewport, caché y perfil de red fijados | Navegador/E2E y auditoría UX | Flujos accesibles y comprensibles; tiempos de frontend separados de latencias API | Capturas, mediciones, defectos priorizados y correcciones verificadas | E-UX 67 E2E/417 Vitest, cuatro hallazgos corregidos; recorrido real 9/9 | E-NAV: 18 navegaciones observadas, perfiles y límites documentados; no sustituye rendimiento de campo |
| Cierre S15 y entrega a S16 | Consolidar evidencia técnica, riesgos y demostración | Resultados finales disponibles y checklist académico preparado | Informe, índice, revisión independiente/seguridad y ensayo de demo | Fuentes accesibles; resultados, riesgos y pendientes explícitos; material listo para incorporación posterior | Documentación técnica, índice de hashes y procedimiento de restauración ensayado | Informes, runbook operativo y revisión independiente/seguridad disponibles; integración en curso | Restan IA, proveedores/cuentas reales y operación acordada; .docx después de S16 |

## Preestados de incentivos que deben conservarse

Las expectativas siguientes corresponden a la receta S15, no a una repetición literal de
los importes de fixtures S14. El generador registra observaciones de sus pasos; esas
observaciones **no reconstruyen por sí solas un preestado** para volver a ejecutar HTTP.
El SQL y los bytes del mundo original quedaron comprobados con el verificador final:
1.329/1.329 controles, auditoría 268/268 y notificaciones 116/116. Ese resultado no
sustituye las comprobaciones API específicas de cada preestado ni convierte las
observaciones en snapshots restaurables. La restauración anterior conserva su propio
resultado de 1.225/1.225 antes de utilizar esa copia para HTTP.

| Escenario | Preestado/caso de la receta | Acción e instrumento | Resultado esperado | Evidencia requerida | Estado al corte |
| --- | --- | --- | --- | --- | --- |
| S-01 | R002 aún no creado; autor verificado | Crear por servicio de dominio | Autor +20 de impacto pendiente; sin crédito disponible | Reporte, media, historial inicial y movimiento de creación | SQL de la receta; E-API y E-BRW verifican creación equivalente en sus propios casos |
| S-02 | R002 activo, sin validar | Validar con operador de su institución | Autor acredita 20 + 30; crédito institucional +50; eventos enlazados | Ledger, validación, historial, auditoría y avisos esperados | SQL de la receta; E-API y E-BRW verifican validación y +50 en sus propios casos |
| S-03 | R002 activo; confirmante verificado distinto del autor | Confirmar antes de validar y luego validar | Confirmante +10 pendiente y después +10 acreditado/crédito de esa institución | Confirmación y movimiento ligados por el mismo reporte/caso | SQL de la receta; E-API comprueba confirmación antes de validar y acreditación posterior |
| S-04 | R002 validado y todavía sin primera resolución | Resolver por servicio de dominio | Autor +50 adicionales; total propio del primer ciclo = 100 | Resolución, ciclo, historial, auditoría y bono único | SQL de la receta; E-API y E-BRW comprueban evidencia, resolución y bono único |
| S-05 | R002 activo; autor como solicitante | Intentar autoconfirmación | Negativa por autoconfirmación; sin confirmación ni movimientos nuevos | Código, conteos antes/después y auditoría pertinente | SQL de la receta; E-API comprueba negativa HTTP y nueve tablas sin cambios |
| S-06 | R002 activo; mismo confirmante ya registrado | Repetir confirmación | Negativa por duplicación; sin filas o efectos nuevos | Código y comparación de confirmaciones/ledger | SQL de la receta; E-API comprueba duplicación HTTP y nueve tablas sin cambios |
| S-07 | R001 acreditado y posteriormente gastado según la receta | Revertir por administrador | Revertir todas las acreditaciones del reporte; no descontar dos veces la compensación | Movimientos originales y compensatorios, ciclo e historial | SQL de la receta; E-API comprueba reversión tras gastar con auditoría y compensaciones |
| S-08 | C00 = 1.000, C01 = 1.050 y C02 = 1.000 créditos institucionales antes de canjear | Tres canjes legítimamente financiados | Tres vales de 1.000; C01 conserva 50 de remanente; impacto intacto | Canje, código privado, débito y saldos por institución | SQL conserva esos tres importes; E-CAN usa otros actores con 1.010→10, tres UUID/débitos conciliados |
| S-09 | Ya existe canje del ciudadano en la misma institución y mes | Intentar segundo canje | `MONTHLY_LIMIT_REACHED`, aun si también falta saldo; sin segundo débito, canje o aviso | Negativa, preestado y cardinalidades exactas | SQL de la receta; E-API y E-CAN comprueban MONTHLY_LIMIT_REACHED sin mutación |
| S-10 | R033 ingestado como `x_mock`, sin ciudadano de origen | Confirmar con ciudadano verificado y validar | Fuente sin premio; confirmante acredita +10 | Origen, confirmación, ledger y auditoría | SQL/oráculo del original; X permanece como origen simulado, sin API real de X |
| S-11 | R032 con +20 del autor y +10 del confirmante pendientes | Rechazar por servicio de dominio | Ambos pendientes anulados; ningún crédito acreditado por ese reporte | Motivo, estado, ledger e historial/auditoría | SQL de la receta; E-API comprueba rechazo y anulación de pendientes |
| S-12 | R001 ya canjeado; reversión posterior; prueba de saldo en mes posterior o checkpoint compatible | Revertir y después intentar canje sin saldo | C00 queda en −100, vale vigente; intento posterior devuelve `INSUFFICIENT_BALANCE` sin nuevo débito | Saldo, reversión, vale conservado, fecha y negativa | SQL conserva −100; E-API observa su propio preestado con −140, vale intacto y negativa por saldo |

R031 conserva participación pendiente en el extremo inicial de la ventana de 60 días;
R034 financia el excedente de C01; R035 comprueba reversión/revalidación del ciclo 2 sin
reactivar la creación antigua; R036 aporta resolución del mes previo y autocierre tras
72 horas. R001 y R002 sitúan acreditaciones a ambos lados del cambio de mes. Estas
condiciones también requieren evidencia por evento y fecha, no solo saldos finales.

## Límites de interpretación y siguiente actualización

Los resultados nuevos son evidencia de ingeniería S15 con identidad propia. No reabren
EX-09, EX-10 o EX-11, no cambian HT-1…HT-6 y no sustituyen HE/encuesta. El uso académico
y la regla de síntesis se fijarán prospectivamente en P0-M; HTP no se obtiene promediando
indicadores técnicos.

El dataset original, las nuevas restauraciones, suites, API, carga, UX, navegador y
medición descriptiva de pantallas tienen los resultados por carril de la tabla vigente.
La restauración histórica conserva sus 1.225 controles y el original inicial sus 1.257;
no se les atribuyen los 1.329 del verificador reforzado. La matriz S-01…S-12 distingue
la receta original de los casos equivalentes de las corridas HTTP posteriores.
Permanecen fotos/oro, evaluación independiente IA, cuentas/proveedores reales y operación
acordada, además del cierre/integración técnica. La incorporación consolidada a la tesis
corresponde a después de completar S16.
