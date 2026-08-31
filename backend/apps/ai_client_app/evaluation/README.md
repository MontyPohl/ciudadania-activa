# Evaluación de concordancia de la clasificación IA (S8-E1)

Infraestructura para medir **CA-4 / RNF-05**: la concordancia de la clasificación
asistida por IA contra etiquetas de oro, con un objetivo de **≥ 85 %**.

## Qué demuestra esto y qué no

Ejecutar el benchmark con el adaptador falso **no demuestra CA-4**. Valida que el
dataset carga y es coherente, que los contratos encajan, que el parser real se
aplica, que las métricas se calculan bien y que el resultado es reproducible.

La capacidad del modelo solo puede medirse contra el proveedor real, en un gate
propio, fuera de la CI. Es lo que fija el contrato S8-A (D19): *«Un mock que
alcanza el 85 % no demuestra CA-4»*.

Con el adaptador falso el resultado actual es **bajo a propósito**: ese adaptador
repite la categoría que eligió el ciudadano y propone siempre prioridad `MEDIA`,
así que falla en casi todos los casos que exigen corregir. **El dataset no fue
diseñado para maximizar su puntuación.** Si algún día superase el umbral sin
cambiar de comportamiento, habría que sospechar del dataset, no celebrar el
número.

## Métrica

### Oficial: CA-4

**`ca4_concordance_pct`.** Un caso concuerda cuando coinciden exactamente
`category_slug`, `institution_slug` y `priority`.

La fuente es el **Anexo H de la tesis**: *«Exactitud esperada >= 85% sobre
categoria/institucion/prioridad»*. Es el criterio normativo y **la única métrica a
la que se aplica el umbral del 85 %**.

### Diagnóstico: concordancia estricta

**`strict_concordance_pct`.** Añade `inconsistency` a la comparación: exige que el
modelo, además de clasificar bien, haya reconocido cuándo la descripción
contradice lo que el ciudadano eligió.

**No es CA-4 y no tiene umbral propio**, porque no es un criterio de aceptación de
la tesis. Sirve para interpretar el resultado: un caso puede cumplir CA-4 y fallar
la estricta; lo contrario es imposible, porque la estricta es CA-4 más una
condición.

`inconsistency` conserva su etiqueta de oro y su exactitud propia
(`inconsistency_accuracy_pct`). Queda fuera del criterio oficial, no de la
medición.

### Qué queda fuera, y por qué

- **`confidence`** no puntúa. Es una estimación del propio modelo, no una
  etiqueta humana: incluirla mediría cuánto se cree el modelo a sí mismo.
- **`justification`** no se puntúa semánticamente. Es texto libre y dos
  redacciones distintas pueden ser igual de correctas. Se exige que exista, que
  sea texto y que pase el parser —incluido el límite de 1000 caracteres—, pero su
  contenido no entra en el porcentaje.

### Métricas de diagnóstico

`category_accuracy_pct`, `institution_accuracy_pct`, `priority_accuracy_pct` e
`inconsistency_accuracy_pct` indican **dónde** falla el modelo. No sustituyen a la
concordancia primaria: sirven para interpretarla.

### Casos fallidos

Un caso que lanza excepción o cuya respuesta el parser rechaza cuenta como **no
concordante** y aparece en `errors_count`. No es un empate ni un caso excluido: en
producción tampoco habría clasificación. La corrida continúa con los demás.

### Nulos

`null` solo coincide con `null`. Una sugerencia ausente no es un acierto parcial
de la etiqueta que faltaba.

## Dataset

`dataset_s8_v1.json` — versión `s8-evaluation-v1`, **28 casos**.

- **100 % sintético.** Descripciones inventadas para el benchmark. No proceden de
  reportes reales y no contienen nombres, contactos, direcciones ni coordenadas.
  El formato además rechaza campos con esos nombres.
- **Taxonomía congelada dentro del archivo**: 10 categorías, 3 instituciones y las
  tres prioridades, coincidentes con el catálogo del proyecto al crearlo.
- **Cobertura**: todas las categorías activas, todas las instituciones, las tres
  prioridades, casos consistentes e inconsistentes, casos donde la elección del
  ciudadano ya es correcta y casos que exigen corregir categoría o institución.

### Criterio de etiquetado de `inconsistency`

- `true` cuando la descripción **contradice** la categoría elegida —el ciudadano
  eligió «baches» y describe cables con chispas—.
- `false` cuando hay imprecisión pero no contradicción —eligió «otros» para algo
  que encaja en una categoría concreta—.

Las etiquetas se derivaron de la semántica de cada descripción y de la taxonomía,
**nunca** de la salida de ningún adaptador.

### Congelación

Una prueba fija el SHA-256 del archivo. Modificarlo la rompe a propósito: si el
benchmark debe cambiar, la decisión correcta es crear `dataset_s8_v2` y no editar
v1, porque hacerlo invalidaría en silencio cualquier comparación con mediciones
anteriores. Lo mismo vale si el catálogo de la aplicación cambia en un sprint
futuro: el dataset v1 sigue midiendo lo que medía.

## Uso

```
python manage.py evaluate_ai_classification
python manage.py evaluate_ai_classification --format json
```

Solo admite `--adapter fake`. El código de salida indica si la evaluación **se
ejecutó**, no si superó el 85 %: con un doble, un porcentaje bajo sigue
significando que la maquinaria funciona.

## Protocolo prerregistrado para S8-E2

Fijado **antes** de obtener ninguna medición válida, para que el resultado no
pueda elegirse después.

| Parámetro | Valor |
| --- | --- |
| Modelo | `gemini-3.6-flash` |
| Proveedor | Gemini Interactions API |
| `store` | `False` |
| Dataset | `s8-evaluation-v1`, **28 casos** |
| SHA-256 del dataset | `3bc54d591dc104aeabeecb2f5cc7c7a38e70ba042e7f045321ab33ae9b5efad4` |
| Prompt | `s8-classification-v1` |
| Métrica oficial | CA-4: categoría + institución + prioridad, exactos |
| Umbral | **≥ 85.00 %** |
| Mínimo con 28 casos | **24 / 28** (23/28 = 82.14 % es FAIL) |
| Métrica estricta | diagnóstico, sin umbral |
| Corridas canónicas | **1** |
| Reintentos selectivos | **0** |

### Por qué el modelo cambió, y por qué eso no invalida la medición

El modelo inicialmente seleccionado para S8-E2 fue **`gemini-2.5-flash`**. La
corrida contra él **no produjo ninguna clasificación**: los 28 casos devolvieron
HTTP 404 y el informe se cerró con `errors_count = 28`, de modo que CA-4 quedó
como `NOT_DEMONSTRATED`. Eso no es un 0 % de capacidad ni un FAIL del modelo: es
la ausencia de medición.

Un diagnóstico posterior, con una única llamada mínima, reprodujo el 404 y el
proveedor indicó la causa: ese modelo **ya no está disponible para usuarios
nuevos**, y señaló `gemini-3.6-flash` como reemplazo. Otra llamada mínima
—entrada sintética, sin dataset, sin prompt de clasificación y sin esquema—
confirmó conectividad con `gemini-3.6-flash`.

La sustitución se registra aquí porque el momento importa. `gemini-3.6-flash`
**no ha visto todavía ningún caso del benchmark**: no hay puntuación suya que
haya podido influir en la decisión. El motivo es exclusivamente la
indisponibilidad técnica del modelo original para este proyecto, **no** un
resultado observado.

Cambiar de modelo *después* de ver una puntuación baja sería elegir el resultado.
Cambiarlo antes de la primera medición válida, por una causa ajena al
rendimiento y dejándolo escrito, no lo es. De ahí este prerregistro.

### Lo que sigue congelado

Dataset, ground truth, taxonomía, prompt, definición de CA-4 y umbral **no
cambian**. La sustitución afecta a un único parámetro del experimento: qué modelo
se evalúa.

## Resultado de la corrida canónica S8-E2

Se ejecutó **una sola vez**, sin reintentos selectivos. Resultado:

| | |
| --- | --- |
| Modelo | `gemini-3.6-flash` |
| Casos intentados | **28 / 28** |
| Clasificaciones válidas | **6** |
| Errores | **22**, todos `TransientAiProviderError` |
| `ca4_concordance_pct` | 21.43 % |
| `RUN_VALID_FOR_CA4` | **NO** |
| `CA4_RESULT` | **`NOT_DEMONSTRATED`** |

**El 21.43 % no es un FAIL de CA-4.** Es un porcentaje calculado sobre una
corrida inválida: 22 de los 28 casos no produjeron clasificación alguna y el
denominador siguen siendo 28. Leerlo como capacidad del modelo atribuiría al
modelo una incapacidad que la corrida no midió. La regla de validez —congelada
antes de ver cualquier número— dice que un solo error invalida la corrida.

La evidencia de esa corrida se conserva íntegra y **no se sobrescribe**.

### Causa técnica confirmada

El diagnóstico offline estableció que los 22 fallos salieron de
`interactions.create`, con exactamente **un intento HTTP por caso** (SDK con
`attempts=1`, cero reintentos de script y sin Celery), en 28 llamadas
secuenciales sin pausa. La corrida entera duró 320.6 s.

La consulta a AI Studio cerró el diagnóstico:

| Límite | Valor observado |
| --- | --- |
| Nivel | **gratuito** |
| RPM | **5** |
| RPD | **20** |
| TPM | 6.74 K de 250 K — **no fue el límite alcanzado** |

Con eso, `RATE_LIMIT_CONFIRMED`: el protocolo original era **inejecutable por
construcción**. 28 solicitudes no caben en una cuota diaria de 20, y lanzarlas
sin pausa excede 5 por minuto.

Lo que **no** es recuperable es la naturaleza exacta de cada uno de los 22
fallos. El adaptador distinguía «respondió 429» de «no respondió a tiempo», pero
el runner guardaba solo el nombre de la clase y esa distinción se perdió al
serializar. Afirmar que los 22 fueron HTTP 429 iría más allá de la evidencia.

## Protocolo prerregistrado de recuperación S8-E2-R1

Fijado **antes** de volver a llamar al proveedor. Enmienda **solo el transporte**
del experimento; lo sustantivo queda intacto.

| Parámetro | Valor |
| --- | --- |
| `protocol_version` | `s8-e2-r1` |
| Corridas de recuperación | **1** |
| Casos totales | **28**, orden original del dataset |
| Lotes | **2 fijos: 14 + 14** |
| Lote 1 | casos 1–14 |
| Lote 2 | casos 15–28 |
| Ventanas de cuota | **una ventana RPD distinta por lote** |
| Pacing entre solicitudes | **20 s** (3 por minuto, bajo el RPM 5) |
| Timeout por solicitud | **20 s** (sin cambios) |
| Reintentos SDK / script / Celery | **0 / 0 / 0** (sin cambios) |
| Reintentos selectivos de casos | **0** |
| Modelo, dataset, ground truth, prompt, scoring, umbral | **sin cambios** |

**El pacing no es el timeout.** El timeout acota cuánto se espera *una*
respuesta; el pacing acota cada cuánto se pide *la siguiente*. Coinciden en 20 s
por casualidad, y el protocolo los registra por separado para que un cambio en
uno no se confunda con un cambio en el otro.

### Pre-flight obligatorio por lote

Antes de cada lote hay que comprobar **visualmente y en solo lectura** en AI
Studio que hay cuota diaria disponible **≥ 14**. Si no la hay, el lote no se
ejecuta y se espera otra ventana. No se gastan llamadas en comprobar la cuota:
gastarlas consumiría justo el recurso que se está midiendo.

### Los dos lotes son UNA sola corrida

No son dos benchmarks. Después del lote 1 el estado obligatorio es
`R1_IN_PROGRESS`: **no se calcula PASS ni FAIL**, y no se toma ninguna decisión
metodológica a partir de él. Esto no depende de la disciplina de quien ejecute:
`BatchEvaluation` **no guarda ningún porcentaje**, así que el dato no existe
hasta combinar los 28.

Si entre lotes cambiara el commit, el modelo, el dataset, el prompt, el scoring,
el timeout, el pacing o la instrumentación, la corrida R1 queda invalidada y no
debe continuar. `combine_batches` lo comprueba y aborta.

Los lotes **no son datasets**: `select_batch` recorta por índice sobre el archivo
congelado. No hay un segundo JSON que pueda divergir del original.

### Regla de validez de R1

Idéntica a la de la corrida canónica, sobre los 28 combinados:

- `attempted_cases != 28` → `NOT_DEMONSTRATED`;
- `errors_count > 0` → `NOT_DEMONSTRATED`;
- 28 intentados y 0 errores → **≥ 24 concordantes = PASS**, **≤ 23 = FAIL**.

No se excluyen errores, no se sustituyen casos, no se repiten los fallidos. El
mínimo de 24 se **deriva** del umbral del 85 % en `minimum_concordant_cases()`,
no se escribe a mano.

### Instrumentación añadida

Por caso se registra ahora, además de lo anterior:

| Campo | Contenido |
| --- | --- |
| `error_type` | nombre de la excepción (ya existía) |
| `error_message` | mensaje **sanitizado**; conserva «respondió 429» frente a «no respondió a tiempo» |
| `duration_ms` | entero ≥ 0, medido con `time.perf_counter_ns()` |

La sanitización redacta por patrones explícitos —claves, cabeceras de
autorización, `Bearer`, cadenas de conexión, `query strings` de URL y tokens
opacos largos— y **no** convierte el mensaje en texto genérico: eso reproduciría
exactamente el problema que la instrumentación existe para resolver.

La duración viaja **fuera** del informe canónico, en `EvaluationRun.diagnostics`.
El informe conserva así su determinismo byte a byte, que es lo que permite
archivarlo como evidencia comparable.

### Evidencia esperada de R1

```
/tmp/s8-e2-r1-batch-01.json
/tmp/s8-e2-r1-batch-02.json
/tmp/s8-e2-r1-combined.json
```

Cada lote registra `protocol_version`, `model_name`, `dataset_version`,
`dataset_sha256`, `prompt_version`, `batch_number`, `batch_case_ids`,
`inter_request_delay_seconds`, `request_timeout_seconds`, `results` y
`diagnostics`. El combinado demuestra que la unión de ambos lotes son los 28
casos, que la intersección es vacía y que el orden es el del dataset.

## Siguiente paso

**R1 NO se ha ejecutado.** No se ha llamado al proveedor desde la corrida
canónica. CA-4 sigue `NOT_DEMONSTRATED` y el Sprint 8 sigue en curso.
