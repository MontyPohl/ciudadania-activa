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
diseñado para maximizar su puntuación.** Si algún día la superase sin cambiar de
comportamiento, habría que sospechar del dataset, no celebrar el número.

## Métrica

La documentación del proyecto **no define la fórmula**. El contrato S8-A y RNF-05
fijan el umbral y la fuente; el Anexo H de la tesis acota el alcance a
«categoria/institucion/prioridad», sin decir si el acierto se cuenta por caso o
por campo.

Ante esa ausencia se adopta la definición **conservadora**, y se registra la
discrepancia en lugar de esconderla:

- **`concordance_pct` (primaria).** Un caso concuerda solo si coinciden
  exactamente los cuatro campos etiquetables: `category_slug`,
  `institution_slug`, `priority` e `inconsistency`.
- **`thesis_concordance_pct` (contraste).** El criterio del Anexo H: los tres
  primeros campos, sin `inconsistency`.

La primaria es **más estricta**, así que un resultado que la supera también
supera el criterio del Anexo. Ambas se reportan para que la comparación sea
posible sin reejecutar nada.

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

## Siguiente paso

S8-E2 debe ejecutar **este mismo dataset** contra el proveedor real, fuera de la
CI, y solo entonces podrá declararse si CA-4 se cumple.
