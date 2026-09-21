# S11-A — Datos, evidencia, privacidad y rebaseline de tesis

**Versión congelada:** `S11A-v1.0`

**Baseline:** `main@635d89956ca0632d620916dc0ab298af48079ec4`

**Alcance:** política metodológica y de custodia; no implementa instrumentación ni ejecuta experimentos.

## 0. Índice de artefactos S11-A

| Código | Cobertura | Documento |
|---|---|---|
| S11-A-01 | Catálogo metodológico | `S11A_CATALOGO_Y_MATRIZ.md` §2 |
| S11-A-02 | Matriz tesis → sistema → evidencia | `S11A_CATALOGO_Y_MATRIZ.md` §3 |
| S11-A-03 | Diccionario de indicadores | `S11A_DICCIONARIO_INDICADORES.md` §§1–2 |
| S11-A-04 | Matriz de escenarios/eventos | `S11A_ESCENARIOS_Y_PROTOCOLO.md` §§2–5 |
| S11-A-05 | Manifiesto de backend crítico | `S11A_DICCIONARIO_INDICADORES.md` §4 |
| S11-A-06 | Protocolo experimental | `S11A_ESCENARIOS_Y_PROTOCOLO.md` §6 |
| S11-A-07 | Política de datos/evidencia | Este documento, §§2–8 |
| S11-A-08 | Rebaseline formal S11 | Este documento, §§1 y 9–11 |

## 1. Rebaseline formal de Sprint 11

La propuesta histórica “Sprint 11 — Créditos, ranking del mes en curso y privacidad” queda registrada como **propuesta previa**. No se borra ni se interpreta como trabajo terminado. Desde este corte, Sprint 11 se redefine como ciclo de demostración metodológica y evidencia:

| Etapa | Alcance autorizado | Salida exigida | No incluido |
|---|---|---|---|
| S11-A | Congelación metodológica y rebaseline | Los cuatro documentos S11-A y gate documental | Código, ejecución, datos reales |
| S11-B | Instrumentación | `run_id`, generadores/harness, extractores, manifests, almacenamiento persistente y redacción automática | Interpretación de resultados o cambio de umbrales |
| S11-C | Experimentos | EX-02…EX-10 válidos; EX-01 sólo si se resuelven sus bloqueos | Producción, ranking/canje, reintentos selectivos |
| S11-D | Análisis | Cálculos congelados, errores, limitaciones y decisiones por indicador/hipótesis | Ajustar método a los resultados |
| S11-E | Evidencia y cierre | Dossier auditable, trazabilidad tesis→artefacto y actualización académica | Afirmaciones de impacto/adopción no medidas |

Ranking mensual, saldo visible, privacidad aplicada al ranking y canje/redención se clasifican `FUTURE_FEATURE / DEPENDENCY`. EX-11 permanece `DEFERRED`; su ausencia no se disfraza con tablas, fixtures o inserts manuales. La implementación futura requerirá autorización y su propio protocolo previo.

La planificación histórica de S12–S16 no es evidencia ni queda automáticamente ejecutada, cancelada o renumerada por este documento. Cualquier cambio de cronograma posterior debe registrarse como decisión separada.

## 2. Familias de datos congeladas

| ID / versión | Uso | Tamaño/composición | Semilla | SHA-256 | Estado |
|---|---|---|---|---|---|
| `s11-functional-v1` | EX-02…EX-08 y pruebas de trazabilidad | 20 reportes totalmente sintéticos: dos por cada una de las diez categorías del catálogo congelado; cobertura de tres instituciones, estados/ciclos y actores previstos | `20260921` con namespace `functional` | Se calcula sobre el artefacto generado en S11-B; hasta entonces `UNRESOLVED/BLOCKED` | Especificación congelada, artefacto no generado |
| `s11-load-v1` | EX-10 | 50 payloads y 50 imágenes únicos; diez usuarios verificados sintéticos × cinco requests | `20260921` con namespace `load` | Se calcula en S11-B; hasta entonces `UNRESOLVED/BLOCKED` | Especificación congelada, artefacto no generado |
| `s8-evaluation-v1` | EX-09 | 28 casos, etiquetas de oro de categoría/institución/prioridad/inconsistencia | Dataset ya congelado | `3bc54d591dc104aeabeecb2f5cc7c7a38e70ba042e7f045321ab33ae9b5efad4` | Existente; R2 no ejecutada |
| Encuesta | EX-01 | Cuestionario existente en Anexo G; N observado no disponible; la tesis calcula aproximadamente 150 respuestas válidas, con fuente poblacional aún por verificar | No aplica | Se calcula al preservar formulario/export original | Respuestas `MISSING`; formulario digital, aprobación, ventana y custodia `UNRESOLVED/BLOCKED` |

La semilla no basta para reproducibilidad: el manifest debe fijar también generador y versión, orden, catálogo, locale/zona horaria, dependencias, configuración y hash del resultado. Si cambia cualquier elemento, se crea una versión nueva; nunca se edita silenciosamente `v1`.

### 2.1 Reglas de contenido sintético

- Las personas, cédulas, emails, tokens, fotos, evidencias, ubicaciones y descripciones son inventadas y llevan canarios inequívocos.
- Las coordenadas pertenecen a un polígono urbano sintético válido para las reglas geoespaciales, pero no se copian de hogares o reportes reales.
- Las imágenes de reporte/evidencia son generadas o creadas para el experimento, sin rostros, matrículas, documentos ni metadatos de dispositivo.
- La selfie de identidad sintética puede mostrar una cédula **ficticia**. Aun así se clasifica `SENSITIVE`, porque prueba un flujo diseñado para datos personales.
- Se prohíbe muestrear, transformar o “anonimizar” reportes productivos para construir fixtures.
- Los **16 reportes observados en producción al corte** no son muestra, benchmark, dataset ni evidencia experimental. No entran en numeradores, denominadores, capturas o ejemplos de S11.

## 3. Aislamiento y reproducibilidad

### 3.1 Entorno

Todos los experimentos técnicos se ejecutan en una base, buckets, cache y colas exclusivos y reseteables, con credenciales propias no productivas. Los servicios deben usar las versiones del commit/lockfiles registrados. Se prohíbe conectar un runner S11 a URL, base, Redis, bucket o proveedor de identidad productivos.

Antes del run se registra: commit limpio; `methodology_version`; dataset/semilla/SHA; imagen y dependencias; motor DB/PostGIS/Redis; migraciones aplicadas; configuración no secreta; timestamp UTC; zona horaria; `run_id`; contadores/snapshots iniciales. Después se registran postcondiciones, artefactos y cleanup.

`run_id` es una exigencia metodológica cuya propagación se implementará en S11-B. Debe correlacionar manifest, escenario, request/caso, reporte, tarea, log, extracción y archivo; no debe exponerse como dato personal ni reutilizarse.

### 3.2 Reset

El reset sólo puede dirigirse al entorno experimental explícitamente identificado. Debe verificar host/database/bucket allowlisted, guardar conteos pre-reset, vaciar filas/objetos/cache/cola del run anterior, reaplicar fixture versionado y comprobar conteos esperados. El post-cleanup repite conteos y registra residuos. No se autoriza reset sobre producción.

### 3.3 Artefactos y hashes

Cada run conserva fuera de `/tmp`:

1. manifest inicial y final;
2. entrada sintética o referencia por hash;
3. resultados crudos por request/caso;
4. stdout/stderr redactados;
5. export pre/post minimizado;
6. informe calculado;
7. decisión `PASS|FAIL|NOT_DEMONSTRATED`;
8. `SHA256SUMS` de todos los archivos y registro de cleanup.

Un artefacto se considera recuperable sólo si existe, abre, coincide con su hash y su clasificación/acceso están registrados. Un resumen escrito no reconstruye evidencia primaria perdida.

## 4. Clasificación de información

Se utiliza exactamente uno de estos niveles por artefacto: `PUBLIC`, `INTERNAL`, `EXPERIMENTAL`, `SENSITIVE` o `SECRET`. Cuando un archivo mezcle niveles, se aplica el más restrictivo según `SECRET > SENSITIVE > EXPERIMENTAL > INTERNAL > PUBLIC`.

| Tipo | Clasificación por defecto | Contenido permitido | Regla |
|---|---|---|---|
| Metodología, esquemas y resultados académicos agregados aprobados | PUBLIC | Fórmulas, umbrales, conteos agregados, limitaciones | Publicar sólo tras revisión; nunca datos de fila |
| Borradores académicos y decisiones aún no aprobadas | INTERNAL | Análisis y comentarios sin PII/secreto | Acceso al equipo; pueden promoverse a PUBLIC |
| Dataset/reportes sintéticos y resultados por caso sin secreto | EXPERIMENTAL | IDs sintéticos, descripciones/fotos generadas, etiquetas | Deben llevar versión, semilla/hash y aviso “SYNTHETIC” |
| Logs ya redactados | INTERNAL | eventos, tiempos, status, `run_id`, IDs sintéticos | Si aparece dato sensible, el archivo completo pasa a SENSITIVE hasta redacción o se rechaza |
| Exports de filas, coordenadas exactas, HMAC, encuesta sin anonimizar | SENSITIVE | sólo campos imprescindibles y acceso nominal | Cifrado, mínimo privilegio, sin publicación |
| Selfies, fotografías/evidencias privadas y capturas que las muestren | SENSITIVE | binario sintético imprescindible y metadatos mínimos | URL firmada/token se elimina; captura sólo si no hay alternativa estructurada |
| Screenshots de UI/logs sin dato de fila | INTERNAL | estado visual necesario, con redacción comprobada | No usar como sustituto de resultados estructurados |
| Credenciales, API keys, JWT/refresh, cookies, secreto HMAC, claves de firma | SECRET | nunca dentro de artefactos o repositorio | Inyección efímera por gestor/entorno; no log, captura, hash público ni export |

Un número de cédula, real o ficticio utilizable como canario, no se conserva en texto plano en resultados. El HMAC es `SENSITIVE`, no anónimo. Una selfie puede reidentificar visualmente y siempre es `SENSITIVE` aunque el número textual no esté en la base.

## 5. Redacción y minimización

### 5.1 Campos prohibidos

Nunca deben aparecer en logs, reportes, exports, screenshots ni dossier: credenciales, API keys, tokens bearer/JWT/refresh, cookies, secreto HMAC, claves privadas, URLs firmadas completas, número de cédula en texto plano, email/nombre real, datos productivos o PII no necesaria.

### 5.2 Transformaciones permitidas

- IDs sintéticos: conservar UUID completo cuando sea necesario para reconciliar; de otro modo usar alias determinista del run.
- HMAC/hash de objeto: conservar sólo cuando sea necesario para demostrar unicidad/integridad y clasificar `SENSITIVE`.
- Coordenadas: conservar exactas sólo en export `SENSITIVE`; en informe usar agregados o redondeo.
- Timestamps: conservar UTC y precisión necesaria para performance; evitar correlación con actividad real porque no se usa producción.
- Texto libre: allowlist/canarios; escaneo previo a persistir artefactos.
- URLs firmadas y tokens: sustituir por `[REDACTED]`; no basta truncarlos.
- Excepciones: conservar clase/código y mensaje sanitizado, no headers/request completo.

La redacción genera un artefacto derivado y no modifica silenciosamente el original bajo custodia. Si un original contiene `SECRET` o PII prohibida por error, el run se invalida, se restringe el acceso, se registra el incidente y se aplica el procedimiento de eliminación autorizado.

## 6. Acceso, retención y eliminación

| Clase | Acceso | Retención | Eliminación |
|---|---|---|---|
| PUBLIC | Lectura pública después de aprobación | Según política documental institucional | Versionada; no se reescribe evidencia histórica |
| INTERNAL | Equipo de tesis y revisores autorizados | Duración exacta `UNRESOLVED/BLOCKED` | Borrado verificable al vencer |
| EXPERIMENTAL | Equipo/revisores; sólo datos sintéticos | Duración exacta `UNRESOLVED/BLOCKED` | Borrado verificable preservando, si se autoriza, resultados públicos agregados |
| SENSITIVE | Acceso nominal de mínimo privilegio y almacenamiento cifrado | Duración exacta `UNRESOLVED/BLOCKED` | Borrado verificable de objeto, copias y accesos; registrar constancia |
| SECRET | Sólo proceso/gestor de secretos durante la operación | Sin retención en artefactos | Desmontar/revocar al terminar; rotar si hubo exposición |

No se inventan plazos porque el corpus no fija una duración institucional válida. Antes de EX-01 o de cualquier run que conserve `SENSITIVE`, el responsable académico y el custodio deben aprobar por escrito duración, ubicación, respaldo, responsables y procedimiento de destrucción. Esta decisión pendiente **bloquea la ejecución afectada**, no la congelación de S11-A. Hasta resolverla, no se recolectan datos reales y los artefactos sensibles de prueba sólo pueden existir efímeramente en el entorno controlado.

## 7. Cadena de evidencia

### 7.1 Identificación

Formato recomendado: `S11-<scenario>-<run_id>-<artifact_type>-<sequence>`. El manifest asigna a cada archivo: ID, ruta relativa, tamaño, MIME, clase, creador, timestamp UTC, SHA-256, fuente, transformación y artefacto padre.

### 7.2 Niveles

- **Primaria:** request/response crudo redactado, resultado por caso, fila/objeto extraído, log de ejecución y configuración registrada.
- **Derivada:** percentil, porcentaje, tabla, gráfico o decisión calculados de evidencia primaria mediante método congelado.
- **Contextual:** código, ADR, dependencia, CI previa o despliegue. Explica el sistema, pero no sustituye el run.

Una dependencia nunca se etiqueta como primaria o derivada. El análisis debe enlazar cada número a sus IDs primarios y al comando/versión que lo derivó.

### 7.3 Plantilla obligatoria de resultado

```text
criterion_id:
methodology_version: S11A-v1.0
run_id:
status: PASS | FAIL | NOT_DEMONSTRATED
observed_value:
threshold_or_predicate:
valid_run: true | false
primary_evidence_ids: []
derived_evidence_ids: []
dependency_ids: []
limitations: []
invalidity_reason: null | <reason>
decided_at_utc:
reviewer:
```

- `PASS`: run válido, completo, recuperable y umbral/predicado satisfecho.
- `FAIL`: run válido, completo y recuperable que incumple el umbral/predicado.
- `NOT_DEMONSTRATED`: no ejecutado, inválido, incompleto, no recuperable, bloqueado o dependiente de `FUTURE_FEATURE`.

No se permiten `PARTIAL`, `IN_PROGRESS`, “casi PASS”, colores intermedios o promedios que oculten un criterio fallido. Los diagnósticos pueden informar valores sin recibir veredicto propio.

## 8. Reglas de síntesis

1. HE exige simultáneamente HE-01 y HE-02; HE-03/04 sólo interpretan.
2. HT-1 exige los cuatro indicadores HT1 y un run válido.
3. HT-2 exige R2 válida de 28 casos y HT2-01; HT2-02/03 sólo diagnostican.
4. HT-3 exige HT3-01 y HT3-02.
5. HT-4 exige HT4-01 y HT4-02 al 100 % en el alcance actual, pero su texto completo permanece `NOT_DEMONSTRATED` mientras EX-11 esté diferido.
6. HT-5 se decide sólo con el manifiesto crítico congelado, nunca con cobertura global.
7. HT-6 exige 100 % de reglas ejecutadas y, para la hipótesis completa, EX-11 disponible; hasta entonces es `NOT_DEMONSTRATED`.
8. La hipótesis técnica principal no se obtiene promediando: requiere todas HT en `PASS` y sin dependencia obligatoria diferida.
9. PG/OG además requieren la evidencia empírica declarada; un resultado técnico no la reemplaza.

## 9. Decisiones metodológicas congeladas

| ID | Decisión |
|---|---|
| S11A-D01 | S11 es metodología→instrumentación→experimentos→análisis→evidencia; la propuesta previa de ranking/créditos queda histórica. |
| S11A-D02 | Toda prueba técnica usa datos sintéticos aislados; los 16 reportes productivos quedan excluidos. |
| S11A-D03 | Los únicos veredictos son `PASS`, `FAIL`, `NOT_DEMONSTRATED`. |
| S11A-D04 | R2 usa 28 casos, dos lotes 14+14, modelo/hash/prompt congelados, pacing 20 s, timeout 60 s, cero reintentos; cualquier error invalida el run. |
| S11A-D05 | HT-1 mide `POST /api/v1/reports/` con 10 usuarios×5 requests, ventana 60 s, 50/min, P50≤800 ms y P99≤2.000 ms. |
| S11A-D06 | HT-5 usa sólo el manifiesto crítico versionado; cobertura global no decide. |
| S11A-D07 | `resolution_cycle` representa ciclos de validación; nace en 0, validación incrementa, reversión conserva y revalidación vuelve a incrementar. |
| S11A-D08 | Ranking/redención son `FUTURE_FEATURE / DEPENDENCY`; EX-11 está diferido y no se imputa como evidencia. |
| S11A-D09 | Existencia de código, tabla, test, CI o deployment es contexto, no resultado experimental. |
| S11A-D10 | Evidencia primaria se guarda persistentemente con hash; `/tmp` no es custodia. |

## 10. Decisiones pendientes y bloqueos de ejecución

| ID | Decisión pendiente | Efecto |
|---|---|---|
| S11A-P01 | Aprobación/consentimiento, formulario digital fiel al Anexo G, canales concretos de muestreo, ventana real y custodio de EX-01 | EX-01/HE `UNRESOLVED/BLOCKED`; no recolectar respuestas. La población, mapeo de ítems, fórmulas y umbrales ya están congelados en el diccionario |
| S11A-P02 | Duraciones, ubicación, respaldo, responsables y destrucción para `INTERNAL`, `EXPERIMENTAL` y `SENSITIVE` | Bloquea todo run que deba conservar esas clases |
| S11A-P03 | Ubicación persistente concreta y matriz nominal de acceso a artefactos S11 | Bloquea S11-C/E; S11-B debe implementarla sin cambiar la política |

Los SHA-256 futuros de `s11-functional-v1` y `s11-load-v1` son valores computados después de generar los artefactos especificados, no decisiones metodológicas pendientes. Si no se publican antes del primer run, ese run es inválido.

## 11. Gate entre etapas

- **Entrada S11-B:** S11-A congelada; cualquier enmienda metodológica versionada antes de implementar.
- **Entrada S11-C:** instrumentación verificada con datos ficticios; P02/P03 resueltos; datasets/hashes publicados; entorno aislado demostrado. P01 sólo bloquea EX-01.
- **Entrada S11-D:** artefactos primarios completos y hashes válidos; runs inválidos se analizan como diagnóstico, no como FAIL.
- **Entrada S11-E:** resultados recalculables, revisión de redacción, trazabilidad completa y limitaciones explícitas.

## 12. Gate de cierre S11-A

Estado formal del gate:

`S11A_METHODOLOGY_FROZEN`

Este estado indica que la metodología de S11-A fue congelada para las siguientes etapas de instrumentación, experimentación, análisis y evidencia. No implica que los experimentos hayan sido ejecutados ni que las hipótesis hayan sido demostradas.
