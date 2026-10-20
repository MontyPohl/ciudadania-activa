# ADR-0016 — OpenAI como proveedor de la clasificación asistida por IA

**Estado:** Aceptada
**Sprint:** 11
**Fecha:** 2026-09-26

> **Relación con ADR-0008.** Este ADR **no** sustituye a ADR-0008: la frontera del
> proveedor, el fake, el parser como frontera de seguridad y la prohibición de usar el
> proveedor real en CI siguen vigentes. Lo que cambia es **qué** proveedor está detrás del
> adapter. ADR-0008 nombra a Gemini porque era el proveedor previsto; donde dice
> `GEMINI_API_KEY` debe leerse «la clave del proveedor activo».

## Contexto

La integración con Google Gemini acumuló contratiempos técnicos y operativos. Estos son
los que constan en el repositorio:

- la primera corrida de S8-E2, con `gemini-2.5-flash`, devolvió 404 en los 28 casos
  porque ese modelo ya no estaba disponible para usuarios nuevos (`a47db36`); además,
  el adapter no clasificaba esas excepciones, que la Interactions API lanza desde una
  jerarquía propia (`1a6c7ac`);
- la corrida canónica con `gemini-3.6-flash` quedó `NOT_DEMONSTRATED`, con 22 errores
  de 28, bajo un nivel gratuito de RPM 5 y RPD 20;
- la recuperación R1 quedó `ABORTED`;
- R2 no llegó a ejecutarse.

El equipo decidió cambiar de proveedor.

ADR-0008 previó este escenario —«Costos o cambio de proveedor de IA» figura como riesgo
del backlog— y la arquitectura lo absorbe: el dominio solo conoce
`classify(request) -> str`.

Hay tres restricciones que el nuevo proveedor debe cumplir sin rebajar el contrato S8-A:

- entrada de **imagen inline** en base64, nunca por URL ni por un almacenamiento del
  proveedor (D11);
- **salida estructurada** contra el esquema cerrado de seis campos (D3);
- **no conservar** la petición en el proveedor, como hacía `store=False` en Gemini.

Además, S8-E2-R2 y la preparación de EX-09 (S11, `73e5037`) se prerregistraron contra
Gemini (`gemini-3.6-flash`) y archivan el commit del código. Ninguna de las dos se
ejecutó. La decisión metodológica del 26/09/2026 (`docs/PROGRESO_SPRINTS.md`, S11, P1–P9)
las cierra como preparaciones históricas: Gemini no es proveedor de ninguna medición
futura.

## Decisión

### 1. OpenAI con la Responses API

Se añade `apps/ai_client_app/adapters/openai.py`, que implementa la misma interfaz que
el adapter de Gemini contra la Responses API del SDK oficial `openai`, fijado en `3.19.2`:

| Aspecto | Implementación |
| --- | --- |
| Instrucciones | `instructions` = prompt versionado renderizado, sin la descripción |
| Datos del caso | turno `user` con `input_text` (descripción delimitada como no confiable) |
| Imagen | bloque `input_image` con *data URL* base64; sin Files API ni URLs |
| Salida | `text.format` = `json_schema` con `strict: true` y el esquema v1 |
| Retención | `store=False` |
| Timeout | `AI_REQUEST_TIMEOUT_SECONDS`, en **segundos** (Gemini usaba milisegundos) |
| Reintentos del SDK | `max_retries=0`: Celery sigue siendo la única capa que reintenta |
| Identificadores | no se envían `user`, `safety_identifier` ni `metadata` (D1) |

### 2. Modelo y razonamiento por configuración

`OPENAI_MODEL` no tiene valor por defecto en `settings` (CT-32). El modelo elegido es
`gpt-6-luna`: acepta imagen, admite Structured Outputs y es el más económico de la
familia GPT-6. Técnicamente, `gpt-6-sol` podría configurarse sin tocar código, pero eso no
permite cambiar de modelo tras una medición. Cambiar de modelo después de ver una
concordancia baja sería elegir el resultado. El primer run válido es vinculante, y
cualquier otro modelo sería una medición nueva, prerregistrada como tal.

`OPENAI_REASONING_EFFORT` fija `reasoning.effort`. Vacío omite el parámetro y deja el
valor por defecto del modelo. Es un parámetro que afecta al resultado, así que se
documenta junto al modelo en cualquier medición.

### 3. Proveedor explícito: `AI_PROVIDER`

`runtime.get_runtime_adapter` elige el adapter por `AI_PROVIDER` (`openai` o `gemini`).
**No hay valor por defecto**: vacío o desconocido equivale a no clasificar, aunque haya
claves presentes. Tener credenciales de dos proveedores en el mismo entorno no puede
decidir por sí solo cuál clasifica. Se mantiene el interruptor
`AI_CLASSIFICATION_ENABLED` como condición previa.

### 4. El adapter de Gemini se conserva sin cambios

`gemini.py`, `google-genai` y las variables `GEMINI_*` se mantienen como código
histórico. S8-E2 y la preparación no ejecutada de EX-09 archivan el commit de ese código,
y retirarlo impediría releerlo tal como se prerregistró. No vuelven a ejecutarse contra
Gemini. Por eso el
adapter de OpenAI **replica** el esquema y el texto de entrada en lugar de importarlos
de `gemini.py`. Una **prueba de paridad** exige que ambos sean idénticos: cambiar de
proveedor no puede cambiar de paso lo que se le pregunta al modelo.

### 5. Errores

La traducción sigue la política de ADR-0008:

- `APIConnectionError` (incluido `APITimeoutError`) es transitorio.
- `APIStatusError` 408, 429 y 5xx es transitorio.
- El resto de códigos es permanente.
- Un error ajeno al SDK no se clasifica.

**Excepción:** un 429 con `code = insufficient_quota` es **permanente**. Significa que
la cuenta no tiene crédito, y esperar no lo arregla.

Una respuesta con `status` distinto de `completed` es permanente, igual que una salida
sin texto. Una negativa del modelo llega como bloque `refusal`, no como texto, y por
tanto también es permanente.

### 6. La suite nunca ve una credencial real

`config/settings/test.py` vacía `AI_PROVIDER` y las claves y modelos de ambos
proveedores. `base.py` lee el `.env` local, y la clave real de OpenAI vive ahí.

## Alternativas consideradas

| Alternativa | Motivo de descarte |
| --- | --- |
| Anthropic (Claude) | Viable y con migración de baja fricción. La Responses API es más cercana a la Interactions API ya implementada y OpenAI admite `store=False` explícito. Queda como segunda opción |
| Mistral | Buena opción de soberanía de datos en la UE, pero su rendimiento en visión con nuestro dataset estaba sin validar |
| Amazon Bedrock / Azure AI Foundry | Aportan contrato empresarial y región, a costa de configurar IAM. Sin exigencia institucional que lo justifique hoy |
| Modelo abierto autoalojado (vLLM/Ollama) | Máxima privacidad, pero añade operación de GPU que el proyecto no puede sostener |
| DeepSeek (API oficial) | Datos procesados y almacenados en China, sin retención cero. Su visión era experimental (agosto de 2026) y su modo JSON no garantiza el esquema |
| Retirar Gemini por completo | Borraría el código que archivan S8-E2 y la preparación histórica de EX-09 |
| Importar esquema y texto desde `gemini.py` | Acopla el adapter nuevo al SDK de Google y a un archivo que forma parte de evidencia archivada |

## Consecuencias

- Cambiar de proveedor en un entorno se hace con `AI_PROVIDER` y las variables del
  proveedor, sin despliegue de código.
- **La concordancia de CA-4 debe medirse de nuevo contra OpenAI.** Los resultados
  obtenidos o prerregistrados contra Gemini no se transfieren. La medición exige un
  protocolo prerregistrado propio, con modelo, esfuerzo de razonamiento, timeout,
  dataset y prompt fijados. Esa decisión metodológica no forma parte de este ADR: quedó
  congelada el 26/09/2026 como `s11-ex09-openai-v1`, con la enmienda `S11A-v1.1`
  (`docs/PROGRESO_SPRINTS.md`, S11).
- La documentación oficial lista para `gpt-6-luna` un único *snapshot* con el mismo ID y
  sin fecha, así que no hay versión fechada que fijar. Toda medición registra la fecha y
  el `model` que devuelve cada respuesta. `reasoning.effort` vacío delega en el valor por
  defecto del modelo, hoy `medium`, y `detail: "auto"` delega en su tamaño de imagen por
  defecto. Una medición debe fijar ambos de forma explícita.
- Retención: con `store=False`, `/v1/responses` no guarda estado de aplicación. Aun así,
  OpenAI conserva los datos 30 días para vigilar abusos, salvo que la organización tenga
  retención cero (ZDR), que requiere aprobación previa de OpenAI. Las imágenes que el
  clasificador marca como posible CSAM se retienen incluso con ZDR. `store=False` no
  equivale, por tanto, a retención cero. Hay que resolverlo antes de activar la
  clasificación con reportes reales. Fuente: guía oficial «Your data», consultada el
  26/09/2026.
- Conviven dos SDK de proveedor en `requirements/base.txt`. El de OpenAI trae `httpx2`,
  un paquete distinto del `httpx` que usa `google-genai`; no entran en conflicto.
- Un entorno que tuviera `AI_CLASSIFICATION_ENABLED=true` con solo `GEMINI_*` deja de
  clasificar hasta que declare `AI_PROVIDER`. Es el comportamiento buscado: falla
  cerrado.


## Ampliación post-S12 — Prompt v2 y uso (19–20/10/2026)

PR #49 añade `AI_PROMPT_VERSION`: default **v1**, ID auditado `s8-classification-v1`;
**v2** usa `classification_v2.txt`, ID `classification-v2`. La selección solo se aplica
al adapter OpenAI; una versión desconocida lo desactiva, sin red ni escrituras. Gemini
histórico conserva v1. La prueba de paridad de proveedores cubre v1; no exige igualar
el nuevo prompt v2 con Gemini.

La v2 añade criterios de prioridad por categoría, tabla categoría → institución,
desempates, reglas de evidencia y aviso ante peligro (CT-57). Mantiene esquema de seis
campos, parser y umbral 0,75. No se reescribe v1 ni se transfiere a v2 el resultado
oficial EX-09. La exploración de 13 casos conocidos mejora la triple de 4/13 a 12/13,
con tokens promedio aproximados de 1.800 a 3.800; tiene sesgo optimista y exige validación
con fotos nuevas. Detalle en [post-S12](../PROGRESO_SPRINTS.md#post-s12--estabilización-y-activación-productiva-1920102026).

PR #47 añade los contadores nullables `input_tokens`, `output_tokens` y `total_tokens`
(`ai_client_app.0003`). OpenAI conserva la interfaz `classify(request) -> str` y ofrece
`classify_with_usage` para que la orquestación capture metadata de uso. El resumen
`ai_usage_summary` calcula estimaciones sobre filas persistidas con precios explícitos;
no suma todas las llamadas facturadas. `ai_try_classification` y `ai_compare_prompts`
permiten exploración manual sin persistir clasificaciones/auditoría y consumen cuota
aunque el flag productivo esté apagado. Ver [API §9](../API.md#9-comandos-de-gestión-de-ia-post-s12).

Heroku **v22** (20/10 00:35 UTC) desplegó `744e295` (árbol de backend de `d9e1c11`),
aplicó `0003` y activó IA: `AI_CLASSIFICATION_ENABLED=true`, `AI_PROVIDER=openai`,
`OPENAI_MODEL=gpt-6-luna`, `OPENAI_REASONING_EFFORT=low`, `AI_PROMPT_VERSION=v2`.
A las 01:25 UTC todavía había 0 clasificaciones; activación no equivale a medición de
calidad en producción. La consideración de retención del proveedor expuesta en este ADR
sigue siendo una limitación: estos hechos no acreditan ZDR ni un cambio de política.
