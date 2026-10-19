# S11-D — Análisis y decisiones

Estado: **cerrado el 19/10/2026 con EX-10 `NOT_DEMONSTRATED`**. Este documento adjudica los resultados de S11 con la evidencia conservada; no convierte pruebas de integración en resultados experimentales ni declara PASS por evidencia ausente.

## Alcance y evidencia

La primera medición HT5 corresponde a `c45a78c949fbc70b67cef2eb762ee2916f0257ea`. El aparato EX-10 v2 y sus correcciones operativas quedaron integrados en `main` hasta `28b417982e9e04be5b7e50e164732c8e6cf45e03` (árbol `939e8f6d0adb372f76ccda08e5b6be16f2a86b3b`), que es la revisión fuente montada en los intentos oficiales W22 de c49, c51 y c52. Los hashes de las 33 fuentes críticas y los 81 archivos de tests de HT5 permanecen idénticos en ese alcance, por lo que se reutiliza la medición sin repetirla.

La evidencia de ejecución se conserva fuera del repositorio, en la raíz local de ejecución autónoma (en adelante `R`, `s11-autonomous-execution-20261005T075433Z`), y los runs históricos en `var/s11-runs/`. Ninguno de estos artefactos se publica con esta actualización; el [dossier S11-E](S11_E_DOSSIER_Y_CIERRE.md) indexa sus SHA-256. Reutilizar evidencia no equivale a volver a ejecutar las pruebas históricas.

## Resultados adjudicados

| Experimento | Resultado | Interpretación |
| --- | --- | --- |
| EX-01 / HE | No ejecutado; `NOT_DEMONSTRATED` | No hay encuesta, dataset ni recolección aprobada que permitan inferir satisfacción ciudadana. |
| EX-02 a EX-08 | PASS histórico | Evidencia de los flujos evaluados en C2; no amplía sus poblaciones ni capacidades. |
| EX-09 / HT2 | FAIL válido, 19/28 = 67,86 % | Run OpenAI `s11-a156a68c-c422-5e57-ae2b-3336d3b55e6c`; no se repite para mejorar el resultado. |
| EX-10 / HT1 | **`NOT_DEMONSTRATED`** | Único run oficial: `s11-0ff9a5d1-2309-5f40-b4a7-dabad703e43a`, terminal `NOT_DEMONSTRATED`, `binding=false` (`broker:dynamic-residual`; HT1-01…04 calculados PASS pero no vinculantes). Los intentos posteriores de abrir un run nuevo (W22) no llegaron a abrirlo; véase la sección siguiente. |
| EX-11 | Diferido | No se presenta como experimento ejecutado ni como capacidad verificada. |
| HT5 | PASS: 96,76 % | 2119/2190 líneas ejecutables, exactamente 33 archivos críticos; umbral de 80 % aplicado con comparación de enteros. |

## EX-10: intentos W22 y causa de cierre

Tras G0-A2 (semántica post-run del broker) se preparó un nuevo ciclo oficial con autoridad local W18 (génesis única, conservada) y verificación de sesión v2. Todos los intentos se ejecutaron una sola vez con aprobación exacta del operador, sin reintento automático, y cada fallo se clasificó con una relectura de solo lectura posterior.

| Sesión | Intento | Resultado | Causa raíz |
| --- | --- | --- | --- |
| c47 | v3327 / v3346 | CP2 `UNCERTAIN`, sin run ID | `fixed-native-envelope-binding-invalid` (fuente `ex10_prepare.py` omitida del contexto) y luego `native-attachment-local-client`. |
| c51 | v3395, v3397, v3399 | Fallos previos a la acción nativa; CP1 intacto | IPC sin directorio, IPC 0700 del rol equivocado y pin SHA-256 obsoleto de la policy instalada. |
| c51 | v3400 (19/10/2026) | CP2 `UNCERTAIN`, sin run ID | `fixed-native-envelope-binding-invalid`: la configuración B5 heredaba de c49 38 raíces de sesión previas y el contexto registrado de c51 tenía 40. |
| c52 | v3403 (19/10/2026) | CP2 `UNCERTAIN`, sin run ID | El envelope se construyó (causa anterior corregida), pero la cabecera firmada de la autoridad (TTL 240 s) venció antes de componer el bridge: `EXPIRED_OBSERVATION` → `native-bridge-composition-denied`. |

En todos los casos la autoridad quedó intacta: B3 (`ledger.sqlite3`, `056e5a7a…`), B4 (`coordination.sqlite3`, `1cc05aa4…`, sin reserva) y witness (`e16b46b1…`) conservan los bytes de la génesis del 15/10/2026. No hubo ACK aplicado, run nuevo ni efectos sobre los datos del SUT. Los workers nativos terminaron por sí solos con exit 3.

El fallo de c52 es **estructural**: en este host, la fase previa (~49 s) más la preparación nativa READY/preflight y las lecturas de fuentes (~243 s) superan la vigencia de 240 s de la observación firmada que se toma al inicio. La ampliación de READY a 300 s (c47) conservó deliberadamente ese TTL. Corregirlo exigiría refrescar la observación antes de la composición o cambiar el presupuesto/TTL, es decir, modificar el aparato o su política temporal. Por decisión del operador del 19/10/2026, **S11 se cierra con EX-10 `NOT_DEMONSTRATED`** en lugar de alterar la política para obtener un resultado. Las sesiones c47, c51 y c52 se conservan en CP2 `UNCERTAIN` (solo admiten `inspect`); no se repite START sobre ellas ni se repite la génesis.

## Decisiones operativas

El operador autorizó continuar, reutilizar evidencia vigente y ejecutar solamente la validación necesaria (`TESTING_MODE=IMPACT_BASED`). La telemetría pasiva se guarda fuera del repositorio. No se instala Graphify ni se implementa Hybrid Impact Engine.

La ejecución y la confianza se concentran, por decisión del operador, en esta máquina. La clasificación es `LOCAL_SINGLE_HOST_LIMITED_ASSURANCE`: no demuestra independencia de host, hipervisor ni administración de Docker. La copia externa del ancla (W19) está en la misma máquina y no prueba independencia física.

Los diagnósticos de c45/c46 (NetworkID en estado `created`, propiedad UID/GID 62042, reloj del adaptador de lectura, inventario B5 de montajes y vencimiento READY120) se conservan en `R/09-preexecution/` con sus checkpoints. Ninguno constituye un resultado científico de EX-10.

La selección prospectiva de HT5 fijó 81 archivos de tests de los seis dominios críticos antes de medir. Conserva las 33 fuentes productivas incluidas, también cuando no reciban hits, y el umbral del 80 %. La selección evita repetir las suites masivas del aparato experimental; no equivale a una nueva ejecución de toda la CI.

## Medición HT5 y diagnósticos

La medición ejecutó seis grupos con hasta cinco workers simultáneos, según RAM disponible. Conserva seis bases de cobertura nuevas: 2119/2190 líneas, 96,7579908675799 %, con los 33 archivos presentes. No se mezclaron datos anteriores ni diagnósticos posteriores. Los informes, hashes de bases y JUnit están en `R/09-preexecution/W25-critical-coverage-measurement-v3128/`.

Los JUnit iniciales contienen 1888 registros: 1884 PASS, dos FAIL y dos errores de setup. Los dos fallos de almacenamiento requerían un overlay escribible para la fixture `backend/.s11-test-artifacts`; los dos errores transaccionales ocurrieron al restaurar fixtures serializadas antes de sus cuerpos de test. Una ejecución dirigida de esos cuatro casos, con bases nuevas y el entorno corregido, obtuvo 4 PASS. No se repitieron los 1884 aprobados ni se volvió a medir cobertura. La adjudicación está en `R/09-preexecution/W25-coverage-and-fixture-adjudication-v3135/adjudication.json`. Esto no declara una CI completa en PASS.

## Autoridad prospectiva y síntesis

La decisión OD07-A fue registrada mediante autoridad expresamente delegada antes de inspeccionar cobertura. Mantiene HE/EX01 `NOT_DEMONSTRATED` y EX11 `DEFERRED/FUTURE_FEATURE`. El universo HT5 nuevo sustituye exclusivamente Gemini por OpenAI, conserva 33 archivos y el umbral de 80 %; cambiar el proveedor modifica el denominador aunque conserve su cardinalidad. Las identidades metodológicas históricas de EX09/EX10/run 1 no cambian.

Las reglas originales se conservan en [Diccionario de indicadores](../tesis/S11A_DICCIONARIO_INDICADORES.md), [Protocolo §5/§5.1](../tesis/S11A_ESCENARIOS_Y_PROTOCOLO.md) y [Reglas de síntesis §8](../tesis/S11A_DATOS_EVIDENCIA_Y_REBASELINE.md). HE exige HE01 y HE02; HT1 exige sus cuatro indicadores y un run válido y vinculante; HT2 exige EX09 válido y HT2-01, con HT2-02/03 solo diagnósticos; HT3 exige HT3-01 y HT3-02. HT4 exige ambos indicadores al 100 % en alcance actual, pero su hipótesis completa es `NOT_DEMONSTRATED` mientras EX11 esté diferido. HT6 exige todas las reglas actuales y EX11 disponible para la hipótesis completa. HTP exige todas las HT en PASS y ninguna dependencia obligatoria diferida; no se obtiene por promedio ni se declara PASS con HT2 FAIL. PG/OG requieren además evidencia empírica; los resultados técnicos no reemplazan HE.

## Indicadores y límites de la evidencia conservada

| Indicador | Denominador/criterio original | Evidencia y límite |
| --- | --- | --- |
| HT1-01…04 | Run EX-10 válido y vinculante. | **NOT_DEMONSTRATED.** El run 1 calculó HT1-01…04 en PASS (P50 313,84 ms; P99 569,51 ms), pero su terminal es `NOT_DEMONSTRATED` con `binding=false`; G0-A2 no se aplica retroactivamente. No existe un segundo run oficial. |
| HT3-01 | Cero hallazgos de cédula textual en esquema, filas, logs y exports, desde precheck a cleanup. No es un cociente de perfiles HMAC. | **NOT_DEMONSTRATED.** Los exports minimizados conservados no documentan un inventario y escaneo íntegro del canario sintético durante toda la ventana precheck–cleanup. La presencia de HMAC y la ausencia de texto en un export redactado no demuestran cero persistencias prohibidas. |
| HT3-02 | Todos los casos HMAC congelados: normalización, igualdad/inequidad, duplicado rechazado y cero colisiones. | **NOT_DEMONSTRATED para el indicador completo.** EX02 conserva PASS para el escenario de unicidad ejecutado, pero los artefactos no contienen una reconciliación exhaustiva de todos los casos HMAC congelados con su denominador. |
| HT4-01 | Siete escenarios actuales EX02…EX08. | 7/7 escenarios con ejecución completada y PASS histórico; los intentos inválidos previos se conservan y no entran al denominador. |
| HT4-02 | Todas las celdas obligatorias evento×estado/historial/auditoría/ledger/evidencia de la matriz §5. | **NOT_DEMONSTRATED para la completitud exigida.** Siete validaciones PASS con `complete_input=true` y `missing=[]`, sin reconciliación cuantificada de todas las celdas. HT4 completa permanece `NOT_DEMONSTRATED` por EX11 diferido. |
| HT5-01 | Unión de statements de las 33 fuentes fijadas, sin tests; ≥ 80 % sin redondeo decisorio. | 2119/2190 = 96,7579908675799 %, **PASS**; 33/33 presentes. |
| HT6-01 | R01…R07 de §5.1; cada regla debe satisfacer todas sus celdas. R08/R09 (EX11) no entran al subtotal. | **NOT_DEMONSTRATED para el subtotal decisorio completo.** Los escenarios PASS y exports no incluyen una adjudicación exhaustiva por regla; no sustentan un 7/7 de reglas. |
| HTP-01 | Conjunción de HT1…HT6 y requisitos trazados, sin dependencias obligatorias diferidas. | **No PASS:** HT2 es FAIL válido y HT1, HT3, HT4, HT6 y HE quedan `NOT_DEMONSTRATED`. No se infiere satisfacción ni impacto urbano. |

## Síntesis final

| Hipótesis | Veredicto S11 |
| --- | --- |
| HE | `NOT_DEMONSTRATED` (EX-01 no ejecutado) |
| HT1 | `NOT_DEMONSTRATED` (EX-10 sin run vinculante) |
| HT2 | `FAIL` válido (19/28) |
| HT3 | `NOT_DEMONSTRATED` |
| HT4 | `NOT_DEMONSTRATED` (HT4-01 7/7; HT4-02 incompleto; EX11 diferido) |
| HT5 | `PASS` (96,76 %) |
| HT6 | `NOT_DEMONSTRATED` (EX11 diferido) |
| HTP | No PASS |

Estos veredictos no revocan los resultados históricos de EX02…EX08 ni transforman evidencia ausente en FAIL. No se modifican el prerregistro congelado, la tesis DOCX ni el Anexo G.

## Trabajo futuro identificado

- Rediseñar la secuencia de apertura de EX-10 para que la observación firmada de la autoridad se refresque inmediatamente antes de la composición del bridge (o justificar formalmente otro presupuesto temporal) y volver a intentar un run vinculante en una sesión nueva.
- Separar en CI las suites largas del aparato EX-10 en shards o ejecución manual, porque el job único actual no cabe en el límite de 6 h.
- Ejecutar EX-01 (encuesta) y EX-11 cuando existan las condiciones aprobadas.
