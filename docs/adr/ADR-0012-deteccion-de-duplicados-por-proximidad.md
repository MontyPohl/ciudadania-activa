# ADR-0012 — Detección de duplicados por proximidad

**Estado:** Aceptada
**Sprint:** 6
**Fecha:** 2026-08-28

## Contexto

Antes de crear un reporte conviene advertir si ya existe uno cercano de la misma categoría. La
señal debe ser útil sin bloquear una incidencia legítima ni afirmar una similitud que el sistema no
calcula. Tampoco puede exponer la ubicación precisa de otros ciudadanos.

PostGIS ya conserva `Report.location` como `Point` SRID 4326 con índice GIST. La API pública reduce
precisión, pero la comparación privada necesita el punto exacto proporcionado para el borrador.

## Decisión

### 1. Consulta explícita y acotada

`GET /api/v1/reports/possible-duplicates/` recibe `lat`, `lng` y `category_id`. Exige ciudadanía
verificada, aplica throttle y rechaza claves ausentes, desconocidas o repetidas.

La consulta filtra la misma categoría y los estados activos `CREADO`, `CLASIFICADO`, `ASIGNADO` y
`EN_PROCESO`. Usa un radio configurable, actualmente **100 m**, ordena por distancia y UUID y
devuelve como máximo cinco candidatos. El envelope del buffer permite usar el GIST y
`ST_DWithin` sobre geography decide la inclusión métrica.

### 2. Warning advisory

El frontend consulta una vez al entrar en confirmación. No consulta durante categoría,
descripción, foto, cada tecla o cada movimiento del mapa. Encontrar candidatos no bloquea el
submit; un error de consulta tampoco. El usuario decide si continúa.

Esta señal no es idempotencia, no reserva recursos y no impide dos creaciones concurrentes. No
reemplaza la deduplicación exacta de fotografía por SHA-256.

### 3. Respuesta privacy-safe

Cada candidato contiene solo UUID, categoría, institución nullable, descripción, estado,
prioridad y fecha. No devuelve distancia, coordenadas, ciudadano, media, hash, historial ni datos
de recompensa. La respuesta es `private, no-store`.

### 4. No se afirma similitud semántica

“Similar” significa únicamente misma categoría, estado activo y cercanía geográfica. No hay IA,
embeddings, comparación textual ni Gemini en este flujo.

### 5. Riesgo aceptado del query string

El contrato actual coloca coordenadas precisas en una URL privada. El cliente no las guarda en
navegación, historial, `localStorage`, `sessionStorage` o `IndexedDB`, pero proxies y plataformas
pueden registrar query strings. Se acepta el riesgo para el contrato de S6 y queda pendiente
revisar minimización/redacción de logs de infraestructura. La respuesta nunca devuelve esas
coordenadas.

## Alternativas consideradas

| Alternativa                                | Motivo de descarte                                                                    |
| ------------------------------------------ | ------------------------------------------------------------------------------------- |
| Bloquear el POST si hay candidato          | Confunde proximidad con identidad y puede impedir reportes legítimos                  |
| Reutilizar esta consulta como idempotencia | Un GET no puede garantizar exclusión ni resolver un POST incierto                     |
| Comparación semántica/IA                   | No está implementada, agrega costo y falsos positivos; Gemini pertenece a otro sprint |
| Devolver distancia o coordenadas           | Aumenta exposición sin ser necesaria para el warning                                  |
| Consultar en cada movimiento               | Filtra más coordenadas, aumenta carga y favorece carreras de respuestas               |

## Consecuencias

- El aviso es determinista, pequeño y aprovecha PostGIS.
- Puede haber falsos positivos y falsos negativos por categoría/radio; son aceptables porque no
  bloquea.
- La fotografía idéntica mantiene una defensa independiente y autoritativa.
- El query string preciso exige disciplina operativa de logs y una revisión posterior de
  privacidad.
