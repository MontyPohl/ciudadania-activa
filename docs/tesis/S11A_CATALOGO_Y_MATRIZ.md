# S11-A — Catálogo metodológico y matriz tesis → sistema → evidencia

**Versión congelada:** `S11A-v1.1`. Enmienda de `S11A-v1.0` limitada a la instancia experimental de EX-09/HT-2; ver `S11A_DATOS_EVIDENCIA_Y_REBASELINE.md` §13.

**Baseline de código:** `main@635d89956ca0632d620916dc0ab298af48079ec4`

**Naturaleza:** prerregistro metodológico; no contiene resultados experimentales.

**Estado de las decisiones al congelar:** `NOT_DEMONSTRATED`.

## 1. Regla de congelación

Este documento conserva el significado de las preguntas, objetivos e hipótesis de la tesis revisada y fija, antes de medir, cómo se enlazan con el sistema y con evidencia admisible. Desde `S11A-v1.0`:

- una corrección editorial que no cambie población, variable, fórmula, umbral, escenario o criterio puede registrarse como errata;
- todo cambio metodológico exige una nueva versión y un addendum fechado **antes** de la siguiente ejecución;
- un cambio posterior a una ejecución impide comparar esa ejecución con la versión modificada;
- una dependencia, un archivo de código, una tabla, un test existente o un despliegue no constituyen por sí mismos evidencia de cumplimiento;
- los únicos veredictos de hipótesis o criterios son `PASS`, `FAIL` y `NOT_DEMONSTRATED`.

Fuentes rectoras: `docs/Tesis_Ciudadania_Activa_Revisada_Recompensas.docx` (§§1.2–1.6), `docs/PLAN_DESARROLLO.md`, `docs/PROGRESO_SPRINTS.md`, `docs/tesis/ACTUALIZACION_DOCUMENTO_TESIS.md`, ADR aceptados y el código del baseline. Ante una contradicción de estado técnico prevalecen código, migraciones, servicios y pruebas del baseline; esa prevalencia no autoriza a reescribir una formulación académica.

## 2. Catálogo congelado

`Estado` es el veredicto metodológico al corte, no el estado editorial del texto. `Tipo de evidencia` nombra la evidencia mínima necesaria, no una evidencia ya obtenida.

| ID | Texto congelado | Fuente documental y sección | Indicador relacionado | Escenario relacionado | Tipo de evidencia exigida | Estado | Observaciones |
|---|---|---|---|---|---|---|---|
| PG | ¿De qué manera el diseño e implementación de un prototipo funcional de la plataforma Ciudadanía Activa, basado en verificación de identidad, geolocalización, Inteligencia Artificial vía API, transparencia e incentivos a la participación, permite demostrar la viabilidad técnica de un modelo de gestión urbana participativa para el contexto de Asunción, Paraguay? | Tesis revisada, §1.2 | HTP-01 y conjunto HE/HT | EX-01…EX-10; EX-11 diferido | Síntesis de encuesta y dossier técnico trazable | NOT_DEMONSTRATED | Viabilidad técnica no equivale a adopción, impacto urbano ni implantación institucional. |
| PE-1 | ¿Qué evidencias empíricas, recogidas mediante una encuesta a ciudadanos del área metropolitana de Asunción, sustentan la necesidad de una plataforma centralizada de reporte de problemas urbanos y la disposición a utilizar mecanismos de confirmación e incentivos? | Tesis revisada, §1.3 | HE-01…HE-04 | EX-01 | Dataset e informe de encuesta válidos | NOT_DEMONSTRATED | No existe dataset de encuesta en el repositorio. |
| PE-2 | ¿Qué tecnologías de desarrollo web, verificación de identidad y geolocalización son más adecuadas para garantizar la confiabilidad y trazabilidad de los reportes ciudadanos en el prototipo? | Tesis revisada, §1.3 | HT3-01, HT3-02, HT4-02 | EX-02, EX-08 | Pruebas, inspección de esquema y trazas sintéticas | NOT_DEMONSTRATED | La adecuación se limita al prototipo y al criterio operacional congelado. |
| PE-3 | ¿Cómo puede integrarse un servicio de Inteligencia Artificial vía API para clasificar reportes, detectar inconsistencias y apoyar la identificación de posibles duplicados sin sustituir la revisión humana? | Tesis revisada, §1.3 | HT2-01…HT2-03 | EX-09 | Evaluación EX-09 (`s11-ex09-openai-v1`) y trazas de revisión humana | NOT_DEMONSTRATED | La inconsistencia es diagnóstica; la decisión institucional sigue siendo humana. |
| PE-4 | ¿Cómo puede diseñarse un sistema de puntos, créditos institucionales, ranking y recompensas simuladas que incentive participación útil y mantenga controles básicos contra abuso? | Tesis revisada, §1.3 | HT6-01 | EX-02…EX-07; EX-11 diferido | Suite de reglas y, para lo diferido, implementación y prueba futuras | NOT_DEMONSTRATED | Ledger/ciclos existen; ranking y canje no son capacidades funcionales actuales. No se inferirá motivación a partir de puntos. |
| PE-5 | ¿Qué arquitectura de software garantiza la escalabilidad, seguridad y mantenibilidad del prototipo para una eventual transición a un entorno productivo posterior? | Tesis revisada, §1.3 | HT1-01…HT1-04, HT5-01 | EX-10 | Carga, telemetría acotada y cobertura crítica | NOT_DEMONSTRATED | “Garantiza” se operacionaliza sólo contra umbrales técnicos; no como garantía absoluta o SLA. |
| PE-6 | ¿Qué escenarios sintéticos permiten evidenciar el funcionamiento integral del sistema, incluyendo ciudadanos, operadores, administradores, confirmaciones, acreditación y reversión de puntos, ranking y canje? | Tesis revisada, §1.3 | HT4-01, HT4-02, HT6-01 | EX-02…EX-11 | Ejecuciones aisladas con artefactos persistentes | NOT_DEMONSTRATED | EX-11 queda expresamente diferido porque ranking/canje son `FUTURE_FEATURE`. |
| OG | Diseñar e implementar un prototipo funcional de la plataforma Ciudadanía Activa que demuestre la viabilidad técnica de un modelo de gestión urbana participativa con verificación de identidad, geolocalización, Inteligencia Artificial vía API, transparencia e incentivos a la participación ciudadana, sustentado por evidencia empírica recogida mediante encuesta y validado mediante escenarios de uso con datos sintéticos. | Tesis revisada, §1.4 | HTP-01 y conjunto HE/HT | EX-01…EX-10; EX-11 diferido | Matriz consolidada de resultados y artefactos | NOT_DEMONSTRATED | El objetivo completo no se declara cumplido mientras falten encuesta, mediciones o funciones explícitas. |
| OE-1 | Caracterizar empíricamente la percepción ciudadana sobre la gestión de reportes urbanos en Asunción, la disposición de uso de una plataforma centralizada y la aceptación de mecanismos de confirmación e incentivos. | Tesis revisada, §1.5 | HE-01…HE-04 | EX-01 | Encuesta depurada, diccionario, cálculo y reporte | NOT_DEMONSTRATED | No se inventan participantes, respuestas ni resultados. |
| OE-2 | Identificar y caracterizar los requerimientos funcionales y no funcionales relacionados con verificación de identidad, geolocalización, clasificación inteligente, trazabilidad e incentivos. | Tesis revisada, §1.5 | Matriz completa | EX-02…EX-10 | Catálogo y trazabilidad documental | NOT_DEMONSTRATED | S11-A fija el método; el cumplimiento integral se decide después de medir. |
| OE-3 | Diseñar la arquitectura del prototipo, incluyendo el modelo de datos, los flujos asíncronos, las interfaces y los mecanismos de seguridad, garantizando modularidad y escalabilidad. | Tesis revisada, §1.5 | HT1-01…HT1-04, HT3-01, HT5-01 | EX-02, EX-08, EX-10 | Arquitectura versionada, carga y pruebas críticas | NOT_DEMONSTRATED | Modularidad diseñada no demuestra escalabilidad. |
| OE-4 | Diseñar e implementar el módulo de incentivos con puntos pendientes y acreditados, créditos por institución, confirmaciones ciudadanas, ranking mensual, privacidad configurable y canje simulado. | Tesis revisada, §1.5 | HT6-01 | EX-02…EX-07; EX-11 diferido | Suite de reglas; implementación/pruebas futuras para EX-11 | NOT_DEMONSTRATED | El objetivo conserva su texto; ranking y canje se separan como `FUTURE_FEATURE / DEPENDENCY`. |
| OE-5 | Implementar el prototipo funcional con frontend, backend, base de datos geoespacial, procesamiento asíncrono e integración con IA vía API. | Tesis revisada, §1.5 | HT1-04, HT2-01, HT4-01 | EX-02…EX-10 | Evidencia de integración sintética y evaluación IA | NOT_DEMONSTRATED | Existencia de componentes no demuestra precisión, rendimiento o flujo integral. |
| OE-6 | Validar técnicamente el prototipo mediante pruebas unitarias, integración, carga y flujos end-to-end sobre un dataset sintético representativo del contexto urbano de Asunción. | Tesis revisada, §1.5 | HT1-01…HT6-01 | EX-02…EX-10 | Dossier experimental completo | NOT_DEMONSTRATED | Los E2E interceptados existentes no sustituyen un full-stack real; los fixtures no representan población real. |
| HE | Una proporción significativa (≥ 60 %) de los ciudadanos encuestados del área metropolitana de Asunción reporta haber identificado problemas urbanos durante el último año sin contar con un canal estructurado para denunciarlos, y manifiesta disposición de uso (≥ 70 %) ante la propuesta de una plataforma centralizada de reporte ciudadano con identidad verificada. De manera exploratoria, el instrumento recogerá también la aceptación de confirmaciones comunitarias e incentivos como mecanismos de participación. | Tesis revisada, §1.6.1 | HE-01…HE-04 | EX-01 | Dataset de encuesta, instrumento, depuración y cómputo reproducible | NOT_DEMONSTRATED | La tesis proyecta aproximadamente 150 respuestas válidas, sujeto a verificación académica; el N real es `UNRESOLVED`. |
| HT-1 | La arquitectura asíncrona basada en Celery y Redis permite procesar reportes con latencia percibida baja y mantener un throughput sostenido de al menos 50 reportes por minuto en pruebas sintéticas. | Tesis revisada, §1.6.3; Plan, RNF-01…03 | HT1-01…HT1-04 | EX-10 | Reporte de carga, histogramas y diagnóstico de cola | NOT_DEMONSTRATED | Los umbrales de latencia documentados son P50 ≤ 800 ms y P99 ≤ 2.000 ms. |
| HT-2 | El servicio de Inteligencia Artificial vía API clasifica correctamente categoría y urgencia en al menos 85 % de los reportes del dataset sintético, contrastado contra etiquetas de oro. | Tesis revisada, §1.6.3 | HT2-01…HT2-03 | EX-09 | Ejecución de EX-09 (`s11-ex09-openai-v1`) sobre 28 casos congelados | NOT_DEMONSTRATED | La operación congelada exige coincidencia conjunta de categoría, institución y prioridad; no cambia el umbral. |
| HT-3 | El mecanismo de identidad verificada-privada basado en HMAC-SHA-256 de la cédula y Google OAuth garantiza unicidad del identificador sin almacenar la cédula en texto plano. | Tesis revisada, §1.6.3 | HT3-01, HT3-02 | EX-02, EX-08 | Pruebas e inspección de base sintética | NOT_DEMONSTRATED | No implica anonimato total, irreversibilidad matemática absoluta ni imposibilidad de reidentificación. La selfie puede mostrar la cédula y es sensible. |
| HT-4 | Los flujos end-to-end principales —registro, creación de reporte, clasificación IA, asignación, validación, confirmación, resolución, consulta pública, ranking y canje— se ejecutan sin errores no controlados y con trazabilidad. | Tesis revisada, §1.6.3 | HT4-01, HT4-02 | EX-02…EX-08; EX-11 diferido | Full-stack E2E y extracción de trazas | NOT_DEMONSTRATED | Ranking/canje se conservan en el texto, pero no se simulan como si existieran. |
| HT-5 | La cobertura de pruebas unitarias del backend alcanza al menos 80 % de las líneas críticas del dominio. | Tesis revisada, §1.6.3 | HT5-01 | EX-02…EX-10 | Reporte de cobertura limitado al manifiesto crítico | NOT_DEMONSTRATED | La cobertura global previa no decide HT-5. |
| HT-6 | El módulo de incentivos aplica correctamente el 100 % de las reglas definidas en los escenarios sintéticos: 20 puntos pendientes por creación, acreditación y bonus de 30 al validar, 50 al resolver, 10 por confirmación válida, conversión 1:1 a créditos, reversión de acreditaciones, ranking mensual con puntos acreditados y límite de un canje por institución y usuario al mes. | Tesis revisada, §1.6.3 | HT6-01 | EX-02…EX-07; EX-11 diferido | Suite parametrizada y extracción del ledger | NOT_DEMONSTRATED | El 100 % se calcula sólo sobre reglas ejecutables del alcance declarado; las reglas futuras no se cuentan como PASS ni como FAIL, sino que mantienen la hipótesis completa en `NOT_DEMONSTRATED`. |

### Hipótesis técnica principal conservada como contexto

La hipótesis técnica principal de §1.6.2 —prototipo sobre Next.js, Django REST Framework, PostgreSQL/PostGIS, Celery/Redis y Google Gemini API, sometido a aproximadamente 20 reportes sintéticos— no recibe un ID adicional porque el catálogo solicitado se decide mediante HT-1…HT-6. Su lectura operacional es `HTP-01`: sólo puede ser `PASS` cuando todas las hipótesis técnicas aplicables sean `PASS`, no exista un componente requerido clasificado como `FUTURE_FEATURE` y el dossier sea internamente consistente. En cualquier otro caso es `FAIL` si hubo una ejecución válida que incumplió, o `NOT_DEMONSTRATED` si falta ejecución o alcance.

## 3. Matriz tesis → sistema → evidencia

Cada fila usa **exactamente una** clasificación: `EXISTING`, `DERIVABLE`, `REQUIRES_INSTRUMENTATION`, `MISSING` o `FUTURE_FEATURE`. `EXISTING` describe una capacidad verificable en el baseline; no es un veredicto de hipótesis.

| Tesis | Afirmación operacional / objeto del sistema | Fuente técnica del baseline | Clasificación | Evidencia que debe producirse | Decisión actual |
|---|---|---|---|---|---|
| PG / OG | Núcleo de identidad, reporte geolocalizado, IA asistiva, trazabilidad y ledger | `backend/apps/auth_app/`, `reports_app/`, `ai_client_app/`, `rewards_app/`, `core/` | EXISTING | Inventario firmado por commit y trazas de EX-02…EX-10 | NOT_DEMONSTRATED |
| PG / OG | Síntesis de viabilidad contra todos los umbrales | Diccionario y protocolo S11-A | REQUIRES_INSTRUMENTATION | Tabla de resultados S11-D y dossier S11-E | NOT_DEMONSTRATED |
| PG / OG | Evidencia empírica ciudadana | Ningún dataset de encuesta localizado | MISSING | Instrumento, consentimiento, datos depurados y cálculo | NOT_DEMONSTRATED |
| PE-1 / OE-1 / HE | Respuestas de encuesta y denominadores válidos | No existe fuente de respuestas | MISSING | EX-01 | NOT_DEMONSTRATED |
| PE-2 / OE-2 | HMAC, control por rol/institución, geolocalización e historial | servicios, permisos, modelos, ADR | EXISTING | Pruebas/inspección EX-02 y EX-08 | NOT_DEMONSTRATED |
| PE-2 / OE-2 | Indicadores de unicidad y trazabilidad calculables sobre ejecución sintética | Esquema y logs del baseline | DERIVABLE | Export SQL minimizado y hashes | NOT_DEMONSTRATED |
| PE-3 / OE-5 / HT-2 | Adaptador del proveedor, parser, orquestación, revisión institucional y ejecutor de EX-09 | `backend/apps/ai_client_app/`. El adaptador Gemini y el harness R2 del baseline son históricos; el adaptador OpenAI (ADR-0016) y el ejecutor de `s11-ex09-openai-v1` están pendientes de integración y preparación | REQUIRES_INSTRUMENTATION | Manifiesto de versión, prompt y dataset | NOT_DEMONSTRATED |
| PE-3 / HT-2 | Resultado de EX-09 recuperable | No existe artefacto válido persistente de EX-09; la instancia R2 con Gemini nunca se ejecutó | MISSING | EX-09 completo | NOT_DEMONSTRATED |
| PE-4 / OE-4 / HT-6 | Creación, confirmación, validación, resolución y reversión en ledger | `rewards_app/services/ledger.py` y servicios de reportes | EXISTING | EX-02…EX-07 y extracción del ledger | NOT_DEMONSTRATED |
| PE-4 / OE-4 / HT-6 | Resultado 100 % de reglas actuales | Datos de una ejecución sintética | DERIVABLE | Matriz regla×escenario | NOT_DEMONSTRATED |
| PE-4 / OE-4 / HT-6 | Ranking mensual y canje/redención | No existe flujo funcional completo | FUTURE_FEATURE | EX-11 después de implementación autorizada | NOT_DEMONSTRATED |
| PE-5 / OE-3 / HT-1 | HTTP desacoplado de la clasificación IA; Celery/Redis configurados | `reports_app/services/reports.py`, `ai_client_app/tasks.py`, config Celery | EXISTING | Inspección de cola y configuración en EX-10 | NOT_DEMONSTRATED |
| PE-5 / OE-3 / HT-1 | Throughput, P50, P99 y tiempos de cola de una ejecución aislada | No existe harness/telemetría S11 persistente | REQUIRES_INSTRUMENTATION | EX-10 | NOT_DEMONSTRATED |
| PE-5 / OE-3 / HT-5 | Universo crítico versionado | Manifiesto en el diccionario S11-A | EXISTING | Hash de manifiesto | NOT_DEMONSTRATED |
| PE-5 / OE-3 / HT-5 | Cobertura de líneas limitada al universo crítico | CI actual sólo informa cobertura global de `apps` | REQUIRES_INSTRUMENTATION | Reporte coverage S11-C | NOT_DEMONSTRATED |
| PE-6 / OE-6 / HT-4 | Servicios/API para creación, validación, confirmación, evidencia, resolución, cierre, reversión, revalidación y rechazo | `reports_app` y `rewards_app` | EXISTING | EX-02…EX-08 full-stack | NOT_DEMONSTRATED |
| PE-6 / OE-6 / HT-4 | Correlación automática `run_id` entre pasos, eventos y artefactos | No existe implementación S11 | REQUIRES_INSTRUMENTATION | Instrumentación S11-B | NOT_DEMONSTRATED |
| PE-6 / OE-6 / HT-4 | Ranking/canje dentro del E2E original | Sin flujo funcional completo | FUTURE_FEATURE | EX-11 diferido | NOT_DEMONSTRATED |
| HT-3 | HMAC de documento, unicidad y ausencia de columna textual de cédula | `auth_app/services/identity.py`, esquema y constraints | EXISTING | EX-02/EX-08 con datos sintéticos | NOT_DEMONSTRATED |
| HT-3 | Conteo de colisiones, duplicados rechazados y campos prohibidos | Datos/esquema de la ejecución | DERIVABLE | Export minimizado de inspección | NOT_DEMONSTRATED |
| HT-4 | Resultado y trazabilidad por escenario | Estados, historiales, auditoría y ledger | DERIVABLE | Extractor S11-B y EX-02…EX-08 | NOT_DEMONSTRATED |

### 3.1 Crosswalk completo de trazabilidad

Esta vista hace explícita la cadena requerida. Cuando una formulación mezcla capacidad actual y futura se divide en filas; cada fila conserva una sola clasificación.

| Tesis | Componente / comportamiento | Dato observable | Indicador | Escenario | Artefacto de evidencia | Limitación principal | Clasificación |
|---|---|---|---|---|---|---|---|
| PG / OG | Síntesis del prototipo y sus umbrales | decisiones HE/HT y requisitos trazados | HTP-01 + HE/HT | EX-01…EX-10 | matriz final S11-E con IDs/hash | viabilidad técnica no es adopción/impacto | REQUIRES_INSTRUMENTATION |
| PG / OG | ranking/redención exigidos por alcance original | ausencia de flujo funcional verificable | HT4-01, HT6-01 | EX-11 | ninguno actual; protocolo futuro | tabla/modelo no equivale a función | FUTURE_FEATURE |
| PE-1 / OE-1 / HE | percepción y disposición ciudadana | respuestas válidas y denominadores | HE-01…HE-04 | EX-01 | export, codebook, depuración y cálculo | autorreporte/muestreo; encuesta inexistente | MISSING |
| PE-2 / OE-2 | identidad HMAC, geolocalización, autorización y traza | esquema, respuestas, estados, audits e historial | HT3-01/02, HT4-02 | EX-02, EX-08 | export minimizado + suite/trace full-stack | no prueba anonimato total ni idoneidad universal | DERIVABLE |
| PE-3 | parser/orquestación IA y revisión humana | predicción por caso, inconsistency y decisión humana | HT2-01…03 | EX-09 y EX-08 | EX-09 por caso + audit/estado | dataset sintético; IA no es autoridad | REQUIRES_INSTRUMENTATION |
| OE-5 | frontend/backend/geodatos/async/IA integrados | resultado de escenarios y tareas correlacionadas | HT1-04, HT2-01, HT4-01 | EX-02…EX-10 | manifests, traces y EX-09 | existencia de componentes no prueba desempeño | REQUIRES_INSTRUMENTATION |
| PE-4 / OE-4 | ledger actual de creación/confirmación/validación/resolución/reversión | movimientos, montos, estado, institución y ciclo | HT6-01 | EX-02…EX-07 | matriz regla×escenario y export ledger | regla correcta no prueba motivación | DERIVABLE |
| PE-4 / OE-4 | ranking mensual, privacidad aplicada y redención | ningún dato observable actual admisible | HT6-01 | EX-11 | futuro | no se imputa desde tablas | FUTURE_FEATURE |
| PE-5 / OE-3 / HT-1 | creación HTTP desacoplada de Celery | éxitos/min, duraciones, enqueue/task timestamps | HT1-01…04 | EX-10 | raw load, histogramas y trace de cola | prueba corta no es SLA/uptime | REQUIRES_INSTRUMENTATION |
| PE-5 / OE-3 / HT-5 | reglas críticas del backend | líneas ejecutables/cubiertas del manifiesto | HT5-01 | EX-02…EX-10 | `coverage.json` filtrado + log pytest | cobertura no prueba corrección/calidad | REQUIRES_INSTRUMENTATION |
| PE-6 / OE-6 / HT-4 | flujos principales y trazabilidad | postcondiciones, errores, history/audit/ledger/evidence | HT4-01/02 | EX-02…EX-08 | runner full-stack y matriz de reconciliación | escenarios finitos no prueban operación sostenida | REQUIRES_INSTRUMENTATION |
| PE-6 / OE-6 / HT-6 | reglas de incentivos actuales | celdas exactas de R-01…R-07 | HT6-01 | EX-02…EX-07 | ledger/estado/ciclo por regla | HT-6 completa espera EX-11 | DERIVABLE |
| HT-2 | acuerdo exacto con gold | triple exacta por los 28 casos válidos | HT2-01…03 | EX-09 | sesión única de 28 casos con hashes | cualquier error invalida; no generaliza a población real | MISSING |
| HT-3 | HMAC único y ausencia de cédula textual | canarios ausentes, HMAC/constraints y rechazo duplicado | HT3-01/02 | EX-02, EX-08 | inspección de esquema/base/logs y tests | HMAC no impide toda reidentificación | DERIVABLE |

La cadena para cada objetivo general/específico se completa por sus filas compartidas y por el catálogo §2; ningún artefacto contextual se promueve a resultado primario.

## 4. Regla de decisión y lectura de dependencias

1. Una hipótesis es `PASS` sólo si la ejecución es válida, todos sus indicadores decisorios alcanzan el umbral y todos los artefactos obligatorios son recuperables y coinciden por hash.
2. Es `FAIL` sólo si una ejecución válida y completa incumple al menos un umbral decisorio.
3. Es `NOT_DEMONSTRATED` si no se ejecutó, la ejecución fue inválida/incompleta, falta evidencia recuperable, existe una dependencia requerida `FUTURE_FEATURE`, o hay una decisión `UNRESOLVED/BLOCKED` que impide aplicar el método.
4. Los indicadores diagnósticos nunca rescatan un indicador decisorio fallido.
5. Una dependencia se registra en la columna correspondiente, pero no se presenta como evidencia.

## 5. Limitaciones congeladas

- No se demostrará causalidad de incentivos sobre participación.
- Cantidad de reportes no equivale a impacto urbano.
- `RESUELTO` registra una decisión operativa; no prueba por sí solo que el problema real desapareció.
- Confirmaciones no equivalen a satisfacción ciudadana.
- Puntos o créditos no equivalen a motivación.
- Un smoke test no equivale a disponibilidad sostenida.
- Fixtures o casos sintéticos no representan una población real.
- E2E con API interceptada no equivale a full-stack real.
- Existencia o ejecución del servicio IA no equivale a exactitud.
- Prueba técnica no equivale a adopción.
