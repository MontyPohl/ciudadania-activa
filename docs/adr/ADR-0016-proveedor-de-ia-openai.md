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

La integración con Google Gemini acumuló contratiempos técnicos y operativos. El más
visible quedó documentado en el propio adapter: en la primera medición real (S8-E2) las
28 llamadas devolvieron 404 y ninguna se clasificó, porque la Interactions API lanza
excepciones de una jerarquía distinta de la del resto del SDK. El equipo decidió cambiar
de proveedor.

ADR-0008 previó este escenario —«Costos o cambio de proveedor de IA» figura como riesgo
del backlog— y la arquitectura lo absorbe: el dominio solo conoce
`classify(request) -> str`.

Hay tres restricciones que el nuevo proveedor debe cumplir sin rebajar el contrato S8-A:

- entrada de **imagen inline** en base64, nunca por URL ni por un almacenamiento del
  proveedor (D11);
- **salida estructurada** contra el esquema cerrado de seis campos (D3);
- **no conservar** la petición en el proveedor, como hacía `store=False` en Gemini.

Además, S8-E2-R2 y EX-09 (S11) están **prerregistrados contra Gemini**
(`gemini-3.6-flash`) y archivan el commit del código que ejecutan.

## Decisión

### 1. OpenAI con la Responses API

Se añade `apps/ai_client_app/adapters/openai.py`, que implementa la misma interfaz que
el adapter de Gemini contra la Responses API del SDK oficial `openai` (serie 3.x):

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
familia GPT-6. Si la concordancia no alcanza el umbral, el siguiente candidato es
`gpt-6-sol`, sin tocar código.

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

`gemini.py`, `google-genai` y las variables `GEMINI_*` se mantienen, porque S8-E2-R2 y
EX-09 se ejecutan contra Gemini y su evidencia archiva el commit del código. Por eso el
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
| Retirar Gemini por completo | Dejaría inejecutables las corridas prerregistradas S8-E2-R2 y EX-09 |
| Importar esquema y texto desde `gemini.py` | Acopla el adapter nuevo al SDK de Google y a un archivo que forma parte de evidencia archivada |

## Consecuencias

- Cambiar de proveedor en un entorno se hace con `AI_PROVIDER` y las variables del
  proveedor, sin despliegue de código.
- **La concordancia de CA-4 debe medirse de nuevo contra OpenAI.** Los resultados
  obtenidos o prerregistrados contra Gemini no se transfieren. La medición exige un
  protocolo prerregistrado propio, con modelo, esfuerzo de razonamiento, timeout,
  dataset y prompt fijados. Esa decisión metodológica queda pendiente y no forma parte de
  este ADR.
- `gpt-6-luna` es un alias sin *snapshot* fechado: OpenAI puede actualizarlo. Toda
  medición registra la fecha junto al modelo.
- Retención: `store=False` evita que la respuesta quede guardada, pero la política de
  datos de la API puede conservar registros durante un tiempo limitado para vigilar
  abusos, salvo acuerdo de retención cero con OpenAI. Hay que confirmarlo antes de
  activar la clasificación con reportes reales.
- Conviven dos SDK de proveedor en `requirements/base.txt`. El de OpenAI trae `httpx2`,
  un paquete distinto del `httpx` que usa `google-genai`; no entran en conflicto.
- Un entorno que tuviera `AI_CLASSIFICATION_ENABLED=true` con solo `GEMINI_*` deja de
  clasificar hasta que declare `AI_PROVIDER`. Es el comportamiento buscado: falla
  cerrado.
