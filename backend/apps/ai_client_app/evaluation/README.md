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

## Siguiente paso

S8-E2 debe ejecutar **este mismo dataset** contra `gemini-3.6-flash`, fuera de la
CI, en una sola corrida, y solo entonces podrá declararse si CA-4 se cumple.
