# S11-C3 — Ejecutor de EX-09

Procedimiento de operación del ejecutor de EX-09 preparado según la decisión metodológica
S11-C3 del 23/09/2026 (`docs/PROGRESO_SPRINTS.md`, D1–D4). No sustituye a esa decisión ni
al prerregistro S8-E2-R2: los aplica. Ningún valor del protocolo (modelo, dataset, prompt,
timeout, pacing, reintentos, scoring, umbral) se define aquí; todos salen de
`apps.ai_client_app.evaluation.recovery.R2` y se verifican contra los bytes reales.

## Componentes

| Componente | Función |
| --- | --- |
| `backend/apps/experiments_app/ex09.py` | Identidad, preflight, partición, correlación, custodia por lotes, decisión |
| `backend/apps/experiments_app/management/commands/s11_ex09.py` | Subcomandos `preflight`, `start`, `record`, `run-lot`, `finalize`, `status` |
| `backend/apps/experiments_app/ex09_launcher.py` y `backend/scripts/s11_ex09.py` | Lanzador de host: verifica commit y árbol limpio, arma el entorno mínimo |
| `infra/docker-compose.s11-ex09.yml` | Contenedor efímero: sin `env_file`, sin base, sin Redis, sin puertos |

Sólo `run-lot` contacta al proveedor. El resto no hace llamadas de red a Gemini.

## Garantías del contenedor efímero (D2)

- Se lanza con `docker compose run --rm` desde la imagen construida sobre `backend/` y con
  el backend montado **en solo lectura** desde el working tree limpio del commit verificado.
- No carga el `.env` del repositorio: no hay `env_file` y `REPO_ROOT/.env` no es visible
  dentro del contenedor. El preflight lo comprueba (`environment-boundary`).
- Credenciales productivas vacías (`GEMINI_API_KEY`, `SUPABASE_*`, `BREVO_API_KEY`,
  `DOCUMENT_HMAC_SECRET`) y rutas de IA alternativas apagadas (`AI_CLASSIFICATION_ENABLED`,
  `S11_FAKE_AI_ENABLED`). El preflight falla si alguna aparece.
- La clave temporal del lote llega sólo al proceso de `run-lot`, como
  `S11_EX09_PROVIDER_KEY`, pasada por nombre (`-e`) desde la sesión del operador. El
  lanzador rechaza cualquier otra acción mientras esa variable siga exportada, y el comando
  rechaza cualquier subcomando distinto de `run-lot` si la recibe.
- **Límite conocido:** la salida de red del contenedor no está restringida por firewall
  al dominio del proveedor. El contenedor no tiene más credencial que la clave del lote ni
  más servicio alcanzable que los públicos; la restricción es de contenido, no de red.

## Secuencia de una corrida

Exportar en la sesión del operador `S11_CUSTODY_HMAC_KEY` (clave custodial estable) y
`S11_PUBLICATION_APPROVAL_PUBLIC_KEY`. Nunca se imprimen. `<SHA>` es el commit de
preparación, con el working tree limpio.

```text
python3 backend/scripts/s11_ex09.py build     --expected-git-sha <SHA>
python3 backend/scripts/s11_ex09.py preflight --expected-git-sha <SHA> --lot lot-01
```

Lote 1:

```text
s11_ex09.py start  --expected-git-sha <SHA> --nonce ex-09-operator-approved-001  → run_id
s11_ex09.py record --expected-git-sha <SHA> --run-id <RUN> --lot lot-01 --event quota_check \
    --observed-remaining <N> --rpd-window <YYYY-MM-DD>
# crear la clave temporal del lote en la consola; exportarla sin imprimirla
s11_ex09.py run-lot --expected-git-sha <SHA> --run-id <RUN> --lot lot-01
# unset S11_EX09_PROVIDER_KEY
s11_ex09.py record  --expected-git-sha <SHA> --run-id <RUN> --lot lot-01 --event credential_removed
# revocar la clave en la consola
s11_ex09.py record  --expected-git-sha <SHA> --run-id <RUN> --lot lot-01 --event access_revoked \
    --credential-fingerprint <huella de 16 hex>
s11_ex09.py record  --expected-git-sha <SHA> --run-id <RUN> --lot lot-01 --event cleanup
```

Lote 2, en la primera ventana RPD disponible posterior al lote 1: la misma secuencia con
`--lot lot-02`, una **clave nueva** y `--first-available-window` en la comprobación de
cuota. Después:

```text
s11_ex09.py finalize --expected-git-sha <SHA> --run-id <RUN>
```

## Reglas que el ejecutor impone

- **Una sola corrida.** Un `run_id` UUIDv5, `lot-01` = casos 1–14 y `lot-02` = casos
  15–28. Un lote con cualquier artefacto de ejecución no se vuelve a lanzar.
- **Nada se puntúa por lote.** Ni el resumen ni el manifest de lote contienen CA-4. La
  combinación se persiste sólo si ambos lotes son válidos.
- **Rechazos sin coste.** Todo lo que no consume cuota se comprueba antes de la primera
  llamada. Un rechazo **pendiente** (falta la comprobación de cuota, la cuota es <14, la
  ventana todavía es la del lote 1, faltan las atestaciones del lote 1 o la clave es la del
  lote 1) no toca el run. Un rechazo **terminal** deja el run en `NOT_DEMONSTRATED`. Si
  aparece al lanzar el lote 2, se custodia además un registro de continuidad `FAIL`.
- **Continuidad (D3.4).** Antes del lote 2 se comparan la identidad completa (commit,
  dataset y su SHA-256, prompt y su SHA-256, protocolo, modelo, timeout, pacing,
  reintentos, scoring y partición), los hashes de los artefactos del lote 1 y la
  integridad de la custodia.
- **Ventana RPD (D3.5).** La cuota ≥14 y la ventana del proveedor (`--rpd-window`) son
  atestaciones del operador leídas en la consola, sin llamadas. La ventana del lote 2 debe
  ser posterior a la del lote 1 y la primera con cuota suficiente. Si una ventana
  posterior anterior tenía cuota suficiente y no se usó, el run queda `NOT_DEMONSTRATED`.
- **Límite operativo de 7 días (D3.5–D3.6).** Es una regla propia de S11-C3 y no del
  protocolo R2. El ejecutor la mide como 7 × 24 h desde el inicio de la ejecución del
  lote 1 (`started_at` de su manifest de lote) hasta el inicio del lote 2. Si se excede, el
  resultado es `NOT_DEMONSTRATED` y nunca `FAIL`. Un nuevo intento exige un `run_id` nuevo y
  los 28 casos.
- **Decisión (modelo de validez).** `finalize` relee y verifica toda la evidencia y decide
  entre tres estados. Es `PASS` o `FAIL` sólo con 28 respuestas válidas y correlacionadas
  y CA-4 ≥ o < 24/28. Cualquier otra condición da `NOT_DEMONSTRATED`, con sus motivos. El
  manifest final queda `COMPLETED` para `PASS` o `FAIL` e `INVALID` con `invalidity_reason`
  para `NOT_DEMONSTRATED`: es el estado de custodia, no el veredicto.
- **Finalización prematura.** `finalize` rechaza un run que aún puede continuar o al que le
  faltan atestaciones, salvo con `--abandon-reason`, que produce `NOT_DEMONSTRATED`.

## Paquete de evidencia

Bajo `var/s11-runs/runs/<run_id>/`, escenario `EX-09`:

| Artefacto | Clasificación |
| --- | --- |
| `run-manifest.initial.json`, `run-manifest.final.json` | INTERNAL |
| `ex09-lot-0N-quota-check-NN.json` (`quota_check`) | INTERNAL |
| `ex09-lot-0N-credential-provisioned.json`, `-credential-removed`, `-access-revoked` | INTERNAL |
| `ex09-lot-0N-cleanup.json` (contenedor ausente, verificado por el lanzador) | INTERNAL |
| `ex09-lot-0N-preflight.json`, `ex09-lot-0N-manifest.json`, `ex09-continuity.json` | INTERNAL |
| `ex09-lot-0N-stderr.txt` (redactado) | INTERNAL |
| `ex09-lot-0N-batch.json` (respuestas por caso, sin agregados) | EXPERIMENTAL |
| `ex09-combined.json`, `ex09-decision.json` | EXPERIMENTAL |
| `metadata/artifacts.jsonl` + `.hmac`, `SHA256SUMS`, `.s11-finalized` | custodia |

Ningún artefacto contiene la clave: `_persist` rechaza cualquier contenido que la incluya,
el stderr se sanitiza con las reglas de `evaluation.sanitization` y sólo se registra la
huella de 16 hexadecimales.

## Contradicción detectada y corregida en la implementación

D3.3 exige que entre lotes el run quede **sin `SHA256SUMS`**. `EvidenceStore`, la custodia
compartida `S11-CUSTODY-v1.0`, reescribía `SHA256SUMS` con cada artefacto indexado. La
especificación no se modificó: se añadió a `EvidenceStore` la opción
`rolling_checksums=False`, que usa sólo EX-09. Con ella el índice y su HMAC se renuevan en
cada alta y `SHA256SUMS` se escribe una única vez, al finalizar. La recuperación de una
finalización interrumpida restaura la ausencia del archivo. El valor por omisión sigue
siendo `True`, así que EX-02…EX-08 conservan su comportamiento sin cambios.

Queda una diferencia de orden que no se modificó. D3.8 enumera «manifest final;
`SHA256SUMS`; HMAC; sello». La finalización de `S11-CUSTODY-v1.0`, que D3.2 manda usar,
escribe el manifest final, renueva el HMAC, crea el sello y escribe el `SHA256SUMS`
definitivo al final, de modo que éste cubre también el sello. Cambiarla alteraría la
custodia compartida de C2.

## Pruebas locales

`backend/apps/experiments_app/tests/test_ex09.py` son pruebas **LOCAL TEST /
NON-EXPERIMENTAL**:

- los sockets están bloqueados;
- el cliente y el adaptador del SDK son dobles;
- la custodia vive en `backend/.s11-test-artifacts/`, que se borra al terminar;
- las claves son inventadas;
- el modo de evidencia `LOCAL TEST / NON-EXPERIMENTAL` queda en el manifest y en la
  decisión.

Ninguna de esas pruebas es evidencia de EX-09.
