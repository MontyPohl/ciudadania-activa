# S11-C3 — Ejecutor de EX-09, instancia OpenAI (`s11-ex09-openai-v1`)

Procedimiento de operación del ejecutor de EX-09 contra OpenAI. Aplica la decisión del
26/09/2026 que congeló el protocolo (`docs/PROGRESO_SPRINTS.md`, P5–P7) y la enmienda
`S11A-v1.1`. No define ningún valor: todos salen de
`apps.ai_client_app.evaluation.openai_session.S11_EX09_OPENAI_V1`, que el preflight
compara con el prerregistro literal y con los bytes reales.

La preparación Gemini (`S11_EX09_EXECUTOR.md`, `73e5037`) es histórica y no se ejecuta.

## Componentes

| Componente | Función |
| --- | --- |
| `backend/apps/ai_client_app/evaluation/openai_session.py` | Protocolo congelado, verificación de inputs, sesión única y clases de error |
| `backend/apps/experiments_app/ex09_openai.py` | Identidad, preflight local, custodia, atestaciones, validez, CA-4 y decisión |
| `backend/apps/experiments_app/management/commands/s11_ex09_openai.py` | Subcomandos `preflight`, `provider-preflight`, `start`, `record`, `run`, `finalize` y `status` |
| `backend/apps/experiments_app/ex09_openai_launcher.py` y `backend/scripts/s11_ex09_openai.py` | Lanzador de host: verifica el commit y el working tree limpio y arma el entorno mínimo |
| `infra/docker-compose.s11-ex09-openai.yml` | Contenedor efímero: sin `env_file`, base, Redis ni puertos |

Sólo `run` contacta al proveedor. El resto, incluido `provider-preflight`, no hace
ninguna llamada de red.

## Protocolo que el ejecutor impone

| Parámetro | Valor |
| --- | --- |
| Escenario / protocolo / metodología | EX-09 / `s11-ex09-openai-v1` / `S11A-v1.1` |
| Proveedor / API / modelo | OpenAI / Responses API, `POST /v1/responses` / `gpt-6-luna` |
| SDK | `openai==3.19.2`, fijado en `requirements/base.txt` y verificado en el preflight |
| `reasoning.effort` / `detail` / `store` | `low`, siempre explícito / `auto` / `false` |
| Timeout | 60 s por request (ver abajo) |
| Reintentos | SDK `max_retries=0` (un intento); script 0; Celery no interviene |
| Partición | 28 casos, una sesión, en orden, uno por vez, sin pausas |
| Dataset / prompt / esquema | SHA-256 `3bc54d59…5ad4` / `f3c80398…65ca` / `ef2a7d35…5f23` |
| CA-4 | `PASS` ≥ 24/28, `FAIL` ≤ 23/28, sólo con 28 respuestas válidas |

- **El timeout real.** El SDK convierte `timeout=60.0` en un `httpx2.Timeout` con 60 s
  **por fase** (conexión, escritura, lectura y pool), no en un plazo total. Por eso el
  ejecutor mide la duración total de cada caso. Un caso de más de 60 s queda
  `duration_exceeded` e invalida el run, según la matriz 2.2 de S11-A («60 s máximo por
  request»).
- **Sin reintentos ocultos.** El bucle del SDK es `range(max_retries + 1)`: con 0 hay un
  único intento. Las pruebas lo comprueban contra el SDK real con un transporte en memoria
  para 408, 429, 500, 503, timeout y fallo de conexión.
- **Sin imágenes.** El dataset no tiene imágenes y el runner envía `image_bytes=b""`. El
  preflight construye las 28 peticiones reales sin enviarlas y verifica que ninguna lleve
  `input_image`. Si durante la sesión apareciera una imagen, la sesión se detiene antes de
  enviarla y el run queda `NOT_DEMONSTRATED`.

## Garantías del contenedor efímero

- **Imagen y montaje.** Se lanza con `docker compose run --rm` desde la imagen construida
  sobre `backend/`, con el backend montado en solo lectura. Ni la imagen ni el contenedor
  ven el `.env` del repositorio: el preflight lo comprueba en `environment-boundary`.
- **Credenciales productivas vacías.** `OPENAI_API_KEY`, `GEMINI_API_KEY`, `SUPABASE_*`,
  `BREVO_API_KEY` y `DOCUMENT_HMAC_SECRET` se fuerzan a vacío, y `AI_PROVIDER` también.
  `AI_CLASSIFICATION_ENABLED` y `S11_FAKE_AI_ENABLED` quedan apagados.
- **La clave del run.** La clave temporal llega sólo a los procesos de
  `provider-preflight` y de `run`, como `S11_EX09_PROVIDER_KEY`, pasada por nombre
  (`-e`). `run` la exige y la usa. `provider-preflight` la acepta si está exportada,
  sólo para calcular su huella, y nunca llama al proveedor. El lanzador rechaza
  cualquier otra acción mientras esa variable siga exportada, y el comando rechaza
  cualquier otro subcomando si la recibe. Sólo se registra su huella: los 16 primeros
  hexadecimales de su SHA-256.
- **Límite conocido.** La salida de red no está restringida por firewall al dominio del
  proveedor.

## Secuencia de una corrida

Exportar en la sesión del operador `S11_CUSTODY_HMAC_KEY` y
`S11_PUBLICATION_APPROVAL_PUBLIC_KEY`. Nunca se imprimen. `<SHA>` es el commit de
preparación, con el working tree limpio.

```text
python3 backend/scripts/s11_ex09_openai.py build     --expected-git-sha <SHA>
python3 backend/scripts/s11_ex09_openai.py preflight --expected-git-sha <SHA>
#   → gate READY_FOR_PROVIDER_PREFLIGHT; ready_for_execution siempre false
# crear la clave temporal del run en la consola del proyecto; exportarla sin imprimirla
s11_ex09_openai.py provider-preflight --expected-git-sha <SHA> --tier "<tier>" \
    --rpm <N> --tpm <N> (--rpd <N> | --rpd-absent) --credit-status sufficient|insufficient|unknown \
    --spend-limit absent|compatible|reached|unknown \
    --retention-status standard|zero-data-retention|extended|unknown
#   → sin run_id ni evidencia; ready_for_execution sólo con todos los criterios en PASS
# unset S11_EX09_PROVIDER_KEY (start y record la rechazan)
s11_ex09_openai.py start  --expected-git-sha <SHA> --nonce ex-09-openai-operator-approved-001 → run_id
s11_ex09_openai.py record --expected-git-sha <SHA> --run-id <RUN> --event limits_check \
    --tier "<tier>" --rpm <N> --tpm <N> [--rpd <N>] --credit-available yes|no --spend-limit-reached yes|no
# volver a exportar la misma clave temporal, sin imprimirla
s11_ex09_openai.py run    --expected-git-sha <SHA> --run-id <RUN>
# unset S11_EX09_PROVIDER_KEY
s11_ex09_openai.py record --expected-git-sha <SHA> --run-id <RUN> --event credential_removed
# revocar la clave en la consola
s11_ex09_openai.py record --expected-git-sha <SHA> --run-id <RUN> --event access_revoked \
    --credential-fingerprint <huella de 16 hex>
s11_ex09_openai.py record --expected-git-sha <SHA> --run-id <RUN> --event cleanup
s11_ex09_openai.py finalize --expected-git-sha <SHA> --run-id <RUN>
```

## Preflight del proveedor, sin `run_id`

`provider-preflight` es el gate administrativo previo a `start`. Repite el preflight
local y añade las atestaciones del operador. **No crea `run_id`, manifest, paquete de
evidencia ni registros de casos, no escribe nada en `var/s11-runs/` y no llama al
proveedor**: sólo imprime su resultado, como el preflight local.

- **Consola, no API.** La API no expone el crédito, los límites, el gasto ni la
  retención a una clave de proyecto. El operador los lee en la consola de OpenAI y los
  pasa como argumentos. Lo que no pasa queda `UNKNOWN` y nunca se convierte en `PASS`.
- **RPD.** `--rpd <N>` si el límite existe; `--rpd-absent` si la consola muestra que no
  existe. Sin ninguno de los dos queda `UNKNOWN`. Un RPD ausente se registra `ABSENT`,
  sin valor: no se asume infinito.
- **Retención.** `standard` es `store=false` más la vigilancia de abusos de hasta 30
  días que la identidad ya declara, y `zero-data-retention` es ZDR aprobada. Las dos son
  compatibles con la política de `S11A-v1.1` para datos sintéticos sin PII. `extended`
  (cualquier retención o uso mayor, por ejemplo compartir datos para entrenamiento)
  queda `BLOCKED`.
- **Modelo.** Registra `configured_model = gpt-6-luna` y `availability =
  NOT_DEMONSTRATED`. No se hace ninguna petición de prueba: la primera petición del run
  demuestra el modelo, la respuesta, el request id y el modelo servido. La validez de la
  clave tampoco se demuestra antes.
- **TPM.** Se registra el límite, pero su compatibilidad queda `NOT_DEMONSTRATED`: P6
  excluye `max_output_tokens` y los tokens de razonamiento no están acotados, así que el
  consumo de la sesión no se deriva del protocolo. El preflight informa, como base para
  una regla futura, el tamaño exacto en bytes UTF-8 de lo que recibiría el modelo en
  las 28 peticiones (no son tokens). Mientras no exista una regla metodológica de
  consumo, `ready_for_execution` no puede ser `true`.

`provider_preflight` es `FAIL` si algún criterio falla (`rpm` o `rpd` < 28, crédito
insuficiente, tope de gasto alcanzado, retención `extended`); `PASS` sólo si todos los
criterios están en `PASS` (`rpd` ausente no cuenta); y `NOT_DEMONSTRATED` en otro caso.
Los criterios son el preflight local, la credencial, el crédito, RPM, RPD, TPM, el
gasto y la retención.

## Límites y crédito dentro del run

El run no depende del preflight del proveedor ni hereda sus valores. No se fija ningún
valor. En `limits_check` el operador atesta lo que ve en la consola para
`gpt-6-luna`, a nivel de organización y de proyecto: el tier, los límites RPM, TPM y, si
existe, RPD, si queda crédito y si se alcanzó el límite de gasto.

La comprobación queda `OK` sólo con estas condiciones, derivadas de los 28 casos:

- RPM ≥ 28;
- RPD ≥ 28, si existe;
- crédito disponible;
- límite de gasto no alcanzado.

Sin una comprobación `OK`, `run` se rechaza sin llamar al proveedor. Cualquier 429 durante
la sesión, incluido `insufficient_quota`, invalida el run.

## Validez y decisión

`finalize` relee la evidencia custodiada y decide entre tres estados.

- **`NOT_DEMONSTRATED`.** Lo produce cualquiera de estas situaciones:
  - caso ausente, duplicado, inesperado, fuera de rango o fuera de orden;
  - respuesta sobrante o perdida;
  - `response.id` o request id ausente o repetido;
  - `model` devuelto distinto entre casos;
  - cualquier clase de error: `timeout`, `network_error`, `rate_limit_429`,
    `insufficient_quota`, `authentication`, `authorization`, `provider_4xx`,
    `provider_5xx`, `provider_incomplete`, `malformed_response`, `schema_validation`,
    `duration_exceeded`, `image_present` o `unexpected_provider_error`;
  - divergencia de configuración, SDK o hashes;
  - pérdida de artefactos;
  - fallo de custodia.
- **`PASS` o `FAIL`.** Sólo con 28 respuestas válidas: `PASS` con CA-4 ≥ 24/28 y `FAIL`
  con ≤ 23/28, calculados con el `compare_case` y el umbral congelados.

Un error nunca se cuenta como desacuerdo. El manifest final queda `COMPLETED` para `PASS`
o `FAIL` e `INVALID` con `invalidity_reason` para `NOT_DEMONSTRATED`.

## Paquete de evidencia

Bajo `var/s11-runs/runs/<run_id>/`, escenario `EX-09`, con la custodia estándar de
`S11-CUSTODY-v1.0` (`SHA256SUMS` actualizado en cada alta):

| Artefacto | Clasificación |
| --- | --- |
| `run-manifest.initial.json`, `run-manifest.final.json` (`S11A-v1.1`) | INTERNAL |
| `ex09-openai-limits-check-NN.json` | INTERNAL |
| `ex09-openai-credential-provisioned`, `-credential-removed`, `-access-revoked`, `-cleanup` | INTERNAL |
| `ex09-openai-preflight.json`, `ex09-openai-session-manifest.json` | INTERNAL |
| `ex09-openai-stderr.txt` (redactado) | INTERNAL |
| `ex09-openai-session.json` (un registro por caso, sin agregados) | EXPERIMENTAL |
| `ex09-openai-decision.json` | EXPERIMENTAL |

Ningún artefacto contiene la clave, cabeceras de autorización ni el texto del prompt. `_persist` rechaza cualquier contenido que incluya la clave y el stderr pasa por
`evaluation.sanitization`.

## Pruebas locales

`backend/apps/ai_client_app/tests/test_openai_session.py` y
`backend/apps/experiments_app/tests/test_ex09_openai.py` son **LOCAL TEST /
NON-EXPERIMENTAL**:

- los sockets están bloqueados;
- el SDK real sólo habla con un transporte en memoria;
- el adaptador de la sesión es un doble;
- la custodia vive en `backend/.s11-test-artifacts/`;
- la clave es inventada.

Ninguna de esas pruebas es evidencia de EX-09.
