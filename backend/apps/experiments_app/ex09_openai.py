"""Ejecutor de EX-09, instancia OpenAI (`s11-ex09-openai-v1`), bajo S11-C3.

Aplica la decisión metodológica del 26/09/2026 (`docs/PROGRESO_SPRINTS.md`, P5–P6)
y la enmienda `S11A-v1.1`. No redefine ningún valor del protocolo: los lee de
`S11_EX09_OPENAI_V1`, los compara con el prerregistro literal y verifica los bytes
reales antes de cualquier llamada.

Frente a la preparación Gemini (`ex09.py`, histórica, `73e5037`) cambia sólo lo que
dependía de aquel proveedor:

- **Una sola sesión.** Un único `run_id`, los 28 casos en orden y sin pausas. No
  hay lotes, ventanas RPD, cuota ≥14 ni límite de 7 días. La custodia es la
  estándar de `S11-CUSTODY-v1.0`, con `SHA256SUMS` actualizado en cada alta.
- **Límites y crédito en lugar de cuota.** El operador atesta, sin secreto, el
  tier, los límites y el crédito observados en la consola. Sus valores no se fijan
  en el protocolo: son `PARAMETER_TO_VERIFY_AT_PREFLIGHT`.
- **Una clave por run.** Llega sólo a los procesos de `provider-preflight` (que sólo
  calcula su huella) y de `run`, como `S11_EX09_PROVIDER_KEY`. Sólo se registra su
  huella.

Se conservan las reglas estructurales de S11-C3: la decisión se deriva de la
evidencia custodiada al finalizar, sólo existen `PASS`, `FAIL` y `NOT_DEMONSTRATED`,
y un run ejecutado nunca se vuelve a lanzar.

El preflight local nunca declara el run listo para ejecutar. Como mucho devuelve
`READY_FOR_PROVIDER_PREFLIGHT`: los límites, el crédito y la credencial se verifican
después, en el preflight del proveedor (`provider-preflight`). Ninguno de los dos crea
`run_id`, escribe evidencia ni llama al proveedor.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apps.ai_client_app.evaluation.dataset import EvaluationDataset
from apps.ai_client_app.evaluation.openai_session import (
    S11_EX09_OPENAI_V1,
    VALID,
    OpenAiProtocolError,
    SessionEvaluation,
    adapter_constants,
    installed_sdk_version,
    run_openai_session,
    verify_protocol_inputs,
)
from apps.ai_client_app.evaluation.recovery import (
    RESULT_FAIL,
    RESULT_NOT_DEMONSTRATED,
    RESULT_PASS,
    classify_run,
    minimum_concordant_cases,
)
from apps.ai_client_app.evaluation.runner import build_report, build_request
from apps.ai_client_app.evaluation.sanitization import sanitize_text
from apps.ai_client_app.evaluation.scoring import CA4_MIN_CONCORDANCE_PCT, CaseResult

from .canonical import canonical_json_bytes
from .constants import DEFAULT_SEED, METHODOLOGY_VERSION, ArtifactKind, Classification, RunStatus
from .custody_policy import PUBLICATION_POLICY_VERSION
from .evidence import EvidencePolicyError, EvidenceStore, validate_finalized_state
from .ex09 import (
    RunState,
    capture_stderr,
    compare_identity,
    credential_fingerprint,
    filesystem_type,
    identity_sha256,
    load_state,
    read_credential,
    redact_stderr,
)
from .manifest import ArtifactReference, RunManifest, create_manifest, finalize_manifest

SCENARIO_ID = "EX-09"
PROTOCOL = S11_EX09_OPENAI_V1
DATASET_ID = PROTOCOL.dataset_version
DECISION_REFERENCE = "S11-C3 s11-ex09-openai-v1 (docs/PROGRESO_SPRINTS.md, 26/09/2026)"
FROZEN_METHODOLOGY_VERSION = "S11A-v1.1"

OFFICIAL_MODE = "OFFICIAL"
LOCAL_TEST_MODE = "LOCAL TEST / NON-EXPERIMENTAL"
_EXPERIMENT_IDS = {
    OFFICIAL_MODE: "S11C3-EX09-OPENAI",
    LOCAL_TEST_MODE: "S11C3-EX09-OPENAI-LOCAL-TEST-NON-EXPERIMENTAL",
}

PROVIDER_KEY_ENV = "S11_EX09_PROVIDER_KEY"
OPERATOR = "Monty Emmanuel Pohl Amarilla"
ENVIRONMENT_NAME = "s11c3-ex09-openai-ephemeral-container"
PARAMETER_TO_VERIFY = "PARAMETER_TO_VERIFY_AT_PREFLIGHT"
#: Peticiones que la cuenta debe admitir: los 28 casos, sin margen de reintento.
MINIMUM_REQUESTS = PROTOCOL.total_cases

READY_FOR_PROVIDER_PREFLIGHT = "READY_FOR_PROVIDER_PREFLIGHT"
NOT_READY = "NOT_READY"
CHECK_PASS = "PASS"
CHECK_FAIL = "FAIL"
CHECK_NOT_EVALUATED = "NOT_EVALUATED"
LIMITS_OK = "OK"
LIMITS_INSUFFICIENT = "INSUFFICIENT"

#: Valores congelados en P6, escritos a mano. Se comparan con `PROTOCOL` para que
#: una edición accidental del protocolo no pase inadvertida.
PREREGISTERED = {
    "version": "s11-ex09-openai-v1",
    "provider": "openai",
    "api": "responses",
    "endpoint": "POST /v1/responses",
    "model_name": "gpt-6-luna",
    "reasoning_effort": "low",
    "image_detail": "auto",
    "store": False,
    "request_timeout_seconds": 60,
    "sdk_package": "openai",
    "sdk_version": "3.19.2",
    "sdk_max_retries": 0,
    "script_retries": 0,
    "celery_retries": 0,
    "dataset_version": "s8-evaluation-v1",
    "dataset_sha256": "3bc54d591dc104aeabeecb2f5cc7c7a38e70ba042e7f045321ab33ae9b5efad4",
    "total_cases": 28,
    "prompt_version": "s8-classification-v1",
    "prompt_sha256": "f3c803984806a3b934db0417f6cc3ce59a49cdde1e30b4c86b862c2e11c665ca",
    "schema_name": "classification_v1",
    "schema_sha256": "ef2a7d354dc1f4526e6776c22e2356afa9881370618de75343aef339c5425f23",
    "sessions": 1,
    "concurrency": 1,
    "inter_request_delay_seconds": 0,
}

#: Credenciales y endpoints productivos que el contenedor efímero debe tener vacíos.
#: `OPENAI_API_KEY` incluida: la única clave admitida es la temporal del run.
FORBIDDEN_ENVIRONMENT = (
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "BREVO_API_KEY",
    "DOCUMENT_HMAC_SECRET",
)
DISABLED_FLAGS = ("AI_CLASSIFICATION_ENABLED", "S11_FAKE_AI_ENABLED")
_TRUE_VALUES = {"1", "true", "yes", "on"}

#: Campos prohibidos en la petición: identificadores y parámetros que P6 excluye.
_FORBIDDEN_REQUEST_FIELDS = (
    "user",
    "safety_identifier",
    "metadata",
    "temperature",
    "max_output_tokens",
    "tools",
    "previous_response_id",
)

OPERATOR_EVENTS = ("limits_check", "credential_removed", "access_revoked", "cleanup")
_PREFIX = "ex09-openai-"
_LIMITS_TYPE = re.compile(rf"^{_PREFIX}limits-check-(\d{{2}})$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{16}$")


class Ex09OpenAiError(RuntimeError):
    """El ejecutor no puede continuar. Nunca contiene la clave del proveedor."""


class Ex09OpenAiRefusal(Ex09OpenAiError):
    """El paso se rechazó antes de cualquier llamada al proveedor.

    `terminal=True`: el run ya no puede continuar y sólo admite finalizarse como
    `NOT_DEMONSTRATED`. `False`: falta una condición que el operador puede cumplir sin
    consumir ninguna llamada.
    """

    def __init__(self, reasons: Sequence[str], *, terminal: bool):
        self.reasons = tuple(reasons)
        self.terminal = terminal
        kind = "terminal" if terminal else "pendiente"
        super().__init__(f"EX-09 OpenAI rechazado ({kind}): {list(self.reasons)}")


def _type(name: str) -> str:
    return f"{_PREFIX}{name}"


def _as_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise Ex09OpenAiError("Los timestamps de EX-09 deben incluir zona horaria.")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def experiment_id(evidence_mode: str) -> str:
    if evidence_mode not in _EXPERIMENT_IDS:
        raise Ex09OpenAiError(f"Modo de evidencia desconocido: {evidence_mode!r}.")
    return _EXPERIMENT_IDS[evidence_mode]


def check_protocol_frozen(protocol=PROTOCOL) -> list[str]:
    fields = protocol.fields()
    return sorted(
        name
        for name in set(PREREGISTERED) | set(fields)
        if fields.get(name) != PREREGISTERED.get(name)
    )


# --- Identidad -------------------------------------------------------------------------


def build_identity(
    *,
    git_sha: str,
    git_clean: bool,
    evidence_mode: str,
    dataset_path: Path | None = None,
) -> tuple[dict[str, Any], EvaluationDataset]:
    """Identidad invariante del run, construida desde los bytes y el código reales.

    Todo parámetro que pueda cambiar el resultado vive aquí. Los timestamps no,
    porque no forman parte de la configuración.
    """
    try:
        dataset, measured = verify_protocol_inputs(protocol=PROTOCOL, dataset_path=dataset_path)
    except OpenAiProtocolError as exc:
        raise Ex09OpenAiError(sanitize_text(str(exc))) from exc
    constants = adapter_constants()
    identity = {
        "schema_version": "s11c3-ex09-openai-identity-v1",
        "scenario_id": SCENARIO_ID,
        "experiment_id": experiment_id(evidence_mode),
        "evidence_mode": evidence_mode,
        "decision": DECISION_REFERENCE,
        "operator": OPERATOR,
        "methodology_version": METHODOLOGY_VERSION,
        "protocol_version": PROTOCOL.version,
        "provider": PROTOCOL.provider,
        "api": PROTOCOL.api,
        "endpoint": PROTOCOL.endpoint,
        "model_name": PROTOCOL.model_name,
        "reasoning_effort": PROTOCOL.reasoning_effort,
        "image_detail": PROTOCOL.image_detail,
        "store": PROTOCOL.store,
        "request_timeout_seconds": PROTOCOL.request_timeout_seconds,
        "timeout_semantics": (
            "SDK float timeout applies per phase (connect/read/write/pool); "
            "the executor invalidates any case whose total duration exceeds it"
        ),
        "retries": {
            "sdk_max_retries": constants["sdk_max_retries"],
            "script": PROTOCOL.script_retries,
            "celery": PROTOCOL.celery_retries,
            "selective_reruns": 0,
        },
        "sdk": {"package": PROTOCOL.sdk_package, "version": measured["sdk_version"]},
        "git_sha": git_sha,
        "git_clean": git_clean,
        "dataset_id": DATASET_ID,
        "dataset_sha256": measured["dataset_sha256"],
        "total_cases": len(dataset.cases),
        "case_ids": [case.id for case in dataset.cases],
        "prompt_version": PROTOCOL.prompt_version,
        "prompt_sha256": measured["prompt_sha256"],
        "schema_name": constants["schema_name"],
        "schema_sha256": measured["schema_sha256"],
        "partition": {
            "sessions": PROTOCOL.sessions,
            "cases": f"1-{PROTOCOL.total_cases}",
            "concurrency": PROTOCOL.concurrency,
            "inter_request_delay_seconds": PROTOCOL.inter_request_delay_seconds,
        },
        "expected_images": 0,
        "scoring": {
            "metric": "ca4_concordance_pct",
            "fields": ["category_slug", "institution_slug", "priority"],
            "denominator": PROTOCOL.total_cases,
            "threshold_pct": str(CA4_MIN_CONCORDANCE_PCT),
            "minimum_concordant_cases": minimum_concordant_cases(PROTOCOL.total_cases),
        },
        "provider_limits": {
            "status": PARAMETER_TO_VERIFY,
            "minimum_requests": MINIMUM_REQUESTS,
            "source": "operator attestation from the provider console; no provider call",
        },
        "retention": {
            "store": PROTOCOL.store,
            "zero_data_retention": "not assumed",
            "abuse_monitoring_days": 30,
            "data": "synthetic, no PII (S11A-D02)",
        },
        "credential": {
            "delivery": f"process environment variable {PROVIDER_KEY_ENV}",
            "persisted": False,
            "fingerprint": "sha256-prefix-16-hex",
            "one_key_per_run": True,
        },
        "custody_policy": PUBLICATION_POLICY_VERSION,
    }
    return identity, dataset


# --- Preflight local -------------------------------------------------------------------


class _CapturingResponses:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        raise _DryRun


class _CapturingClient:
    """Doble de cliente: registra los parámetros de la petición y nunca la envía."""

    def __init__(self):
        self.responses = _CapturingResponses()


class _DryRun(Exception):
    pass


def _captured_requests(dataset: EvaluationDataset):
    """Construye las 28 peticiones reales con un cliente que no envía nada.

    Produce, por caso, la petición del runner y los parámetros exactos que recibiría el
    SDK, o el nombre de la excepción si el adaptador no llegó a construirlos.
    """
    from apps.ai_client_app.adapters.openai import OpenAiClassificationAdapter

    client = _CapturingClient()
    adapter = OpenAiClassificationAdapter(
        client=client, model=PROTOCOL.model_name, reasoning_effort=PROTOCOL.reasoning_effort
    )
    for case in dataset.cases:
        request = build_request(case, dataset)
        try:
            adapter.classify(request)
        except _DryRun:
            yield case, request, client.responses.calls[-1], None
        except Exception as exc:
            yield case, request, None, type(exc).__name__


def request_shape_errors(dataset: EvaluationDataset) -> tuple[list[str], int]:
    """Comprueba las 28 peticiones reales construidas sin enviarlas.

    Comprueba sobre los parámetros exactos que recibiría el SDK: modelo, esfuerzo,
    `store`, esquema, ausencia de imagen y de identificadores. Devuelve los errores y
    el número de imágenes encontradas.
    """
    from apps.ai_client_app.evaluation.openai_session import canonical_sha256

    errors: list[str] = []
    images = 0
    for case, request, params, failure in _captured_requests(dataset):
        if request.image_bytes:
            images += 1
        if failure is not None:
            errors.append(f"dry-run:{case.id}:{failure}")
            continue
        blocks = [block for turn in params["input"] for block in turn["content"]]
        if params["model"] != PROTOCOL.model_name:
            errors.append(f"model:{case.id}")
        if params.get("reasoning") != {"effort": PROTOCOL.reasoning_effort}:
            errors.append(f"reasoning:{case.id}")
        if params.get("store") is not PROTOCOL.store:
            errors.append(f"store:{case.id}")
        text_format = params.get("text", {}).get("format", {})
        if (
            text_format.get("type") != "json_schema"
            or text_format.get("strict") is not True
            or text_format.get("name") != PROTOCOL.schema_name
            or canonical_sha256(text_format.get("schema")) != PROTOCOL.schema_sha256
        ):
            errors.append(f"schema:{case.id}")
        if any(block.get("type") == "input_image" for block in blocks):
            images += 1
        forbidden = sorted(set(params) & set(_FORBIDDEN_REQUEST_FIELDS))
        if forbidden:
            errors.append(f"forbidden-fields:{case.id}:{forbidden}")
    return errors, images


def environment_boundary_errors(environ: Mapping[str, str], *, repo_root: Path) -> list[str]:
    """El proceso no ve el `.env` del repositorio ni credenciales productivas."""
    errors = []
    if (Path(repo_root) / ".env").exists():
        errors.append("repo-dotenv-visible")
    errors.extend(
        f"forbidden-env:{name}" for name in FORBIDDEN_ENVIRONMENT if environ.get(name, "").strip()
    )
    errors.extend(
        f"enabled-flag:{name}"
        for name in DISABLED_FLAGS
        if environ.get(name, "").strip().lower() in _TRUE_VALUES
    )
    return errors


def client_configuration() -> dict[str, Any]:
    """Timeout y reintentos del cliente real, construido sin red y sin credencial."""
    from apps.ai_client_app.adapters.openai import build_client

    client = build_client(
        api_key="preflight-placeholder-not-a-credential",
        timeout_seconds=PROTOCOL.request_timeout_seconds,
    )
    return {"timeout": client.timeout, "max_retries": client.max_retries}


def run_preflight(
    *,
    git_sha: str,
    git_clean: bool,
    environ: Mapping[str, str],
    repo_root: Path,
    evidence_root: Path,
    evidence_mode: str,
    dataset_path: Path | None = None,
    mounts_path: Path = Path("/proc/self/mounts"),
    require_credential: bool = False,
) -> dict[str, Any]:
    """Comprobaciones deterministas previas a cualquier llamada. No usa la red.

    Nunca devuelve «listo para ejecutar»: los límites, el crédito y la credencial se
    verifican fuera, en el preflight del proveedor.
    """
    checks: list[dict[str, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "detail": detail, "result": CHECK_PASS if ok else CHECK_FAIL})

    check(
        "git",
        bool(_SHA1.fullmatch(git_sha)) and git_clean is True,
        "commit verificado por el lanzador en el host y working tree limpio",
    )
    check(
        "methodology",
        METHODOLOGY_VERSION == FROZEN_METHODOLOGY_VERSION,
        f"methodology_version {METHODOLOGY_VERSION}",
    )
    drift = check_protocol_frozen()
    check(
        "protocol-frozen",
        not drift,
        f"campos divergentes: {drift}" if drift else f"{PROTOCOL.version} congelado",
    )
    identity = None
    try:
        identity, dataset = build_identity(
            git_sha=git_sha,
            git_clean=git_clean,
            evidence_mode=evidence_mode,
            dataset_path=dataset_path,
        )
    except Ex09OpenAiError as exc:
        check("inputs", False, str(exc))
    else:
        check(
            "dataset",
            identity["dataset_sha256"] == PREREGISTERED["dataset_sha256"],
            f"{DATASET_ID}, sha256 {identity['dataset_sha256']}",
        )
        check(
            "cases",
            identity["total_cases"] == MINIMUM_REQUESTS
            and len(set(identity["case_ids"])) == MINIMUM_REQUESTS,
            f"{identity['total_cases']} casos únicos, una sesión, orden del dataset",
        )
        check(
            "prompt",
            identity["prompt_sha256"] == PREREGISTERED["prompt_sha256"],
            f"{identity['prompt_version']}, sha256 {identity['prompt_sha256']}",
        )
        check(
            "schema",
            identity["schema_sha256"] == PREREGISTERED["schema_sha256"],
            f"{identity['schema_name']}, sha256 {identity['schema_sha256']}",
        )
        check(
            "sdk-version",
            identity["sdk"]["version"] == PREREGISTERED["sdk_version"],
            f"openai {identity['sdk']['version']}",
        )
        shape_errors, images = request_shape_errors(dataset)
        check(
            "request-shape",
            not shape_errors,
            f"{shape_errors}"
            if shape_errors
            else (
                f"28 peticiones construidas sin enviar: {PROTOCOL.model_name}, "
                f"reasoning.effort={PROTOCOL.reasoning_effort}, store=false, esquema strict, "
                "sin identificadores"
            ),
        )
        check("image-absence", images == 0, f"{images} imágenes en 28 peticiones")
    check(
        "provider-model",
        (PROTOCOL.provider, PROTOCOL.api, PROTOCOL.model_name)
        == ("openai", "responses", "gpt-6-luna"),
        f"{PROTOCOL.provider} {PROTOCOL.endpoint} {PROTOCOL.model_name}",
    )
    check(
        "reasoning-effort",
        PROTOCOL.reasoning_effort == "low",
        f"reasoning.effort={PROTOCOL.reasoning_effort}, explícito",
    )
    check(
        "image-detail",
        adapter_constants()["image_detail"] == PROTOCOL.image_detail == "auto",
        "detail=auto; sin efecto porque ninguna petición lleva imagen",
    )
    check("store", PROTOCOL.store is False, "store=false; no equivale a retención cero")
    client = client_configuration()
    check(
        "timeout",
        client["timeout"] == float(PROTOCOL.request_timeout_seconds),
        f"cliente {client['timeout']} s por fase; duración total verificada por caso",
    )
    check(
        "retries",
        client["max_retries"] == 0
        and adapter_constants()["sdk_max_retries"] == 0
        and PROTOCOL.script_retries == 0
        and PROTOCOL.celery_retries == 0,
        f"SDK max_retries={client['max_retries']}; script 0; Celery no interviene",
    )
    key = read_credential({PROVIDER_KEY_ENV: environ.get(PROVIDER_KEY_ENV, "")})
    fingerprint = credential_fingerprint(key) if key else None
    if require_credential:
        check(
            "credential",
            key is not None,
            f"{PROVIDER_KEY_ENV} presente; huella {fingerprint}"
            if key
            else f"{PROVIDER_KEY_ENV} ausente",
        )
    else:
        checks.append(
            {
                "check": "credential",
                "detail": (
                    f"mecanismo: {PROVIDER_KEY_ENV}, sólo en los procesos de "
                    "provider-preflight y run"
                ),
                "result": CHECK_NOT_EVALUATED,
            }
        )
    boundary = environment_boundary_errors(environ, repo_root=repo_root)
    check(
        "environment-boundary",
        not boundary,
        f"{boundary}" if boundary else "sin .env del repositorio ni credenciales productivas",
    )
    fs_type = filesystem_type(evidence_root, mounts_path=mounts_path)
    check(
        "evidence-root", fs_type not in {None, "tmpfs", "ramfs"}, f"sistema de archivos {fs_type}"
    )
    checks.append(
        {
            "check": "provider-limits",
            "detail": (
                f"{PARAMETER_TO_VERIFY}: tier, RPM, TPM, RPD y crédito de {PROTOCOL.model_name}; "
                f"mínimo {MINIMUM_REQUESTS} peticiones"
            ),
            "result": CHECK_NOT_EVALUATED,
        }
    )
    local_ok = all(c["result"] in (CHECK_PASS, CHECK_NOT_EVALUATED) for c in checks)
    return {
        "schema_version": "s11c3-ex09-openai-preflight-v1",
        "scenario_id": SCENARIO_ID,
        "protocol_version": PROTOCOL.version,
        "methodology_version": METHODOLOGY_VERSION,
        "evidence_mode": evidence_mode,
        "result": CHECK_PASS if local_ok else CHECK_FAIL,
        "gate": READY_FOR_PROVIDER_PREFLIGHT if local_ok else NOT_READY,
        "ready_for_execution": False,
        "checks": checks,
        "provider_limits": {
            "tier": PARAMETER_TO_VERIFY,
            "rpm": PARAMETER_TO_VERIFY,
            "tpm": PARAMETER_TO_VERIFY,
            "rpd": PARAMETER_TO_VERIFY,
            "credit": PARAMETER_TO_VERIFY,
            "minimum_requests": MINIMUM_REQUESTS,
        },
        "identity_sha256": identity_sha256(identity) if identity else None,
        "credential_fingerprint": fingerprint,
        "provider_calls": 0,
    }


# --- Preflight del proveedor -----------------------------------------------------------
#
# Gate administrativo previo a `start`: nunca crea `run_id`, manifest, paquete de
# evidencia ni registros de casos, y nunca llama al proveedor. Como el preflight local,
# sólo imprime su resultado. Los límites, el crédito, el gasto y la retención no los
# expone la API a una clave de proyecto: son atestaciones del operador leídas en la
# consola. Lo que no se atesta queda `UNKNOWN` y nunca se convierte en `PASS`.
#
# El run no depende de este resultado: sigue exigiendo su propio `limits_check`
# custodiado entre `start` y `run`.

READY_FOR_EXECUTION = "READY_FOR_EXECUTION"
NOT_READY_FOR_EXECUTION = "NOT_READY_FOR_EXECUTION"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_NOT_DEMONSTRATED = "NOT_DEMONSTRATED"
STATUS_ABSENT = "ABSENT"
STATUS_BLOCKED = "BLOCKED"

CREDIT_STATUSES = ("sufficient", "insufficient", "unknown")
#: `absent`: la cuenta no tiene tope de gasto. `compatible`: lo tiene y no impide las 28
#: peticiones. `reached`: impediría la sesión.
SPEND_LIMIT_STATUSES = ("absent", "compatible", "reached", "unknown")
#: `standard`: `store=false` y la vigilancia de abusos de hasta 30 días que la identidad
#: ya declara como condición conocida. `zero-data-retention`: ZDR aprobada. `extended`:
#: cualquier retención o uso mayor (por ejemplo, compartir datos para entrenamiento),
#: que la política aceptada en S11A-v1.1 no cubre.
RETENTION_STATUSES = ("standard", "zero-data-retention", "extended", "unknown")

_MODEL_AVAILABILITY_NOTE = (
    "sin llamada al proveedor no se demuestra; la primera petición del run demuestra a "
    "la vez el modelo, la respuesta, el request id y el modelo servido"
)

#: Política de TPM decidida el 26/09/2026 (`docs/PROGRESO_SPRINTS.md`). TPM sólo es `PASS`
#: si se demuestra que el consumo máximo posible de la sesión cabe en el límite.
TPM_POLICY = "TPM_STRICT_DEMONSTRATED_MAXIMUM"
TPM_COMPATIBLE_MINIMUM_ONLY = "COMPATIBLE_MINIMUM_ONLY"
#: P6 no congela `max_output_tokens`, que en la Responses API acota a la vez la salida
#: visible y los tokens de razonamiento. Bajo `s11-ex09-openai-v1` no hay tope.
PROTOCOL_MAX_OUTPUT_TOKENS: int | None = None
#: Por qué no hay tokens de entrada exactos antes del run. Nunca se derivan de bytes.
INPUT_TOKENS_UNAVAILABLE = (
    "no local tokenizer for the model; the provider counter (POST /v1/responses/input_tokens) "
    "would send the prompt and the dataset, which the provider preflight forbids"
)


def evaluate_tpm(
    *,
    tpm_limit: int | None,
    input_tokens: Sequence[int] | None,
    max_output_tokens: int | None,
    total_requests: int = MINIMUM_REQUESTS,
) -> dict[str, Any]:
    """Aplica `TPM_STRICT_DEMONSTRATED_MAXIMUM`. Sólo `PASS` cuenta para el gate.

    - Límite desconocido: `UNKNOWN`.
    - Sin tokens de entrada exactos por petición: `NOT_DEMONSTRATED`.
    - Una sola petición supera el límite: `FAIL`, porque nunca cabe en un minuto. Es el
      único mínimo demostrable: la sesión puede repartirse en varios minutos, así que la
      entrada total no lo es.
    - Sin tope de salida: `COMPATIBLE_MINIMUM_ONLY`. El mínimo cabe, pero la salida y el
      razonamiento no están acotados y la sesión no queda garantizada.
    - Entrada total más `total_requests × max_output_tokens` dentro del límite: `PASS`,
      porque la sesión entera cabría en un solo minuto.
    - En otro caso, `NOT_DEMONSTRATED`: superar esa cota no demuestra un fallo.
    """
    if input_tokens is not None and (
        len(input_tokens) != total_requests
        or any(type(value) is not int or value <= 0 for value in input_tokens)
    ):
        raise Ex09OpenAiError(
            f"Los tokens de entrada deben ser {total_requests} enteros positivos exactos."
        )
    if max_output_tokens is not None and (
        type(max_output_tokens) is not int or max_output_tokens <= 0
    ):
        raise Ex09OpenAiError("max_output_tokens debe ser un entero positivo.")
    minimum = max(input_tokens) if input_tokens is not None else None
    maximum = (
        sum(input_tokens) + total_requests * max_output_tokens
        if input_tokens is not None and max_output_tokens is not None
        else None
    )
    if tpm_limit is None:
        status = STATUS_UNKNOWN
    elif minimum is None:
        status = STATUS_NOT_DEMONSTRATED
    elif minimum > tpm_limit:
        status = CHECK_FAIL
    elif maximum is None:
        status = TPM_COMPATIBLE_MINIMUM_ONLY
    elif maximum <= tpm_limit:
        status = CHECK_PASS
    else:
        status = STATUS_NOT_DEMONSTRATED
    return {
        "status": status,
        "policy": TPM_POLICY,
        "limit": "present" if tpm_limit is not None else "unknown",
        "value": tpm_limit,
        "input_tokens": (
            {"status": "EXACT", "total": sum(input_tokens), "max_request": minimum}
            if input_tokens is not None
            else {"status": "UNAVAILABLE", "reason": INPUT_TOKENS_UNAVAILABLE}
        ),
        "output_bound": {
            "max_output_tokens": max_output_tokens,
            "status": "BOUNDED" if max_output_tokens is not None else "UNBOUNDED",
            "covers": "visible output and reasoning tokens",
        },
        "demonstrable_minimum_tokens": minimum,
        "session_maximum_tokens": maximum,
    }


@dataclass(frozen=True, slots=True)
class ProviderAttestation:
    """Valores que el operador lee en la consola de OpenAI. `None` es desconocido."""

    tier: str | None = None
    rpm: int | None = None
    tpm: int | None = None
    rpd: int | None = None
    rpd_absent: bool = False
    credit_status: str = "unknown"
    spend_limit: str = "unknown"
    retention_status: str = "unknown"

    def __post_init__(self):
        for name in ("rpm", "tpm", "rpd"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise Ex09OpenAiError(f"{name} debe ser un entero no negativo o omitirse.")
        if self.rpd is not None and self.rpd_absent:
            raise Ex09OpenAiError("rpd y rpd_absent se excluyen: el límite existe o no.")
        if self.tier is not None and (not isinstance(self.tier, str) or not self.tier.strip()):
            raise Ex09OpenAiError("El tier se atesta como texto no vacío o se omite.")
        for name, allowed in (
            ("credit_status", CREDIT_STATUSES),
            ("spend_limit", SPEND_LIMIT_STATUSES),
            ("retention_status", RETENTION_STATUSES),
        ):
            if getattr(self, name) not in allowed:
                raise Ex09OpenAiError(f"{name} debe ser uno de {list(allowed)}.")


def session_input_measure(dataset: EvaluationDataset) -> dict[str, Any]:
    """Tamaño exacto de lo que recibiría el modelo en las 28 peticiones, sin enviarlas.

    Suma las instrucciones, el texto del usuario y el esquema de cada petición. Son
    bytes UTF-8, no tokens. Es un dato informativo: `evaluate_tpm` no lo recibe, porque
    no existe una conversión demostrable de bytes a tokens.
    """
    sizes = []
    for case, _request, params, failure in _captured_requests(dataset):
        if failure is not None:
            raise Ex09OpenAiError(f"No se pudo construir la petición de {case.id}.")
        texts = [params["instructions"]] + [
            block["text"]
            for turn in params["input"]
            for block in turn["content"]
            if block.get("type") == "input_text"
        ]
        schema = canonical_json_bytes(params["text"]["format"]["schema"])
        sizes.append(sum(len(text.encode("utf-8")) for text in texts) + len(schema))
    return {
        "requests": len(sizes),
        "input_utf8_bytes_total": sum(sizes),
        "input_utf8_bytes_max_request": max(sizes, default=0),
        "unit": "UTF-8 bytes, not tokens",
        "used_in_tpm_evaluation": False,
    }


def _parameter(status: str, **fields: Any) -> dict[str, Any]:
    return {"status": status, **fields}


def evaluate_provider_preflight(
    *,
    local: Mapping[str, Any],
    attestation: ProviderAttestation,
    credential_available: bool,
    credential_fingerprint_value: str | None,
    input_payload: Mapping[str, Any] | None,
    input_tokens: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Decide el preflight del proveedor. `PASS` sólo si todo criterio está demostrado.

    Un criterio que falla (`FAIL`, `BLOCKED`) hace el preflight `FAIL`; uno que no se
    pudo demostrar lo deja `NOT_DEMONSTRATED`. La disponibilidad del modelo nunca es
    criterio: se demuestra en la primera petición del run. TPM sólo cuenta como `PASS`;
    `COMPATIBLE_MINIMUM_ONLY` queda pendiente.
    """
    minimum = MINIMUM_REQUESTS
    local_ok = local.get("gate") == READY_FOR_PROVIDER_PREFLIGHT

    def threshold(value: int | None) -> str:
        if value is None:
            return STATUS_UNKNOWN
        return CHECK_PASS if value >= minimum else CHECK_FAIL

    if attestation.rpd_absent:
        rpd = _parameter(STATUS_ABSENT, limit="absent", value=None, minimum=minimum)
    else:
        rpd = _parameter(
            threshold(attestation.rpd),
            limit="present" if attestation.rpd is not None else "unknown",
            value=attestation.rpd,
            minimum=minimum,
        )
    tpm = evaluate_tpm(
        tpm_limit=attestation.tpm,
        input_tokens=input_tokens,
        max_output_tokens=PROTOCOL_MAX_OUTPUT_TOKENS,
    )
    parameters = {
        "local_preflight": _parameter(
            CHECK_PASS if local_ok else CHECK_FAIL, gate=local.get("gate")
        ),
        "credential": _parameter(
            CHECK_PASS if credential_available else STATUS_NOT_DEMONSTRATED,
            credential_available=credential_available,
            credential_fingerprint=credential_fingerprint_value,
            validity=STATUS_NOT_DEMONSTRATED,
            delivery=f"process environment variable {PROVIDER_KEY_ENV}, not persisted",
        ),
        "credit": _parameter(
            {"sufficient": CHECK_PASS, "insufficient": CHECK_FAIL}.get(
                attestation.credit_status, STATUS_UNKNOWN
            ),
            credit_status=attestation.credit_status,
        ),
        "rpm": _parameter(
            threshold(attestation.rpm),
            limit="present" if attestation.rpm is not None else "unknown",
            value=attestation.rpm,
            minimum=minimum,
        ),
        "rpd": rpd,
        "tpm": {**tpm, "input_payload": dict(input_payload) if input_payload else None},
        "spend_limit": _parameter(
            {"absent": CHECK_PASS, "compatible": CHECK_PASS, "reached": CHECK_FAIL}.get(
                attestation.spend_limit, STATUS_UNKNOWN
            ),
            spend_limit=attestation.spend_limit,
        ),
        "retention": _parameter(
            {
                "standard": CHECK_PASS,
                "zero-data-retention": CHECK_PASS,
                "extended": STATUS_BLOCKED,
            }.get(attestation.retention_status, STATUS_UNKNOWN),
            retention_status=attestation.retention_status,
            store=PROTOCOL.store,
            policy="S11A-v1.1: store=false, synthetic data without PII, ZDR not required",
        ),
    }
    criteria = {
        name: parameter["status"]
        for name, parameter in parameters.items()
        if parameter["status"] != STATUS_ABSENT
    }
    failed = sorted(
        name for name, status in criteria.items() if status in (CHECK_FAIL, STATUS_BLOCKED)
    )
    pending = sorted(
        name for name, status in criteria.items() if status != CHECK_PASS and name not in failed
    )
    if failed:
        result = CHECK_FAIL
    elif pending:
        result = STATUS_NOT_DEMONSTRATED
    else:
        result = CHECK_PASS
    ready = result == CHECK_PASS
    return {
        "provider_preflight": result,
        "ready_for_execution": ready,
        "gate": READY_FOR_EXECUTION if ready else NOT_READY_FOR_EXECUTION,
        "failed": failed,
        "pending": pending,
        "parameters": parameters,
        "tier": attestation.tier.strip() if attestation.tier else STATUS_UNKNOWN,
        "model": {
            "configured_model": PROTOCOL.model_name,
            "availability": STATUS_NOT_DEMONSTRATED,
            "note": _MODEL_AVAILABILITY_NOTE,
        },
    }


def run_provider_preflight(
    *,
    git_sha: str,
    git_clean: bool,
    environ: Mapping[str, str],
    repo_root: Path,
    evidence_root: Path,
    evidence_mode: str,
    attestation: ProviderAttestation,
    recorded_at: datetime,
    dataset_path: Path | None = None,
    mounts_path: Path = Path("/proc/self/mounts"),
) -> dict[str, Any]:
    """Preflight administrativo del proveedor. Sin red, sin `run_id` y sin evidencia."""
    local = run_preflight(
        git_sha=git_sha,
        git_clean=git_clean,
        environ=environ,
        repo_root=repo_root,
        evidence_root=evidence_root,
        evidence_mode=evidence_mode,
        dataset_path=dataset_path,
        mounts_path=mounts_path,
    )
    key = read_credential({PROVIDER_KEY_ENV: environ.get(PROVIDER_KEY_ENV, "")})
    input_payload = None
    try:
        _identity, dataset = build_identity(
            git_sha=git_sha,
            git_clean=git_clean,
            evidence_mode=evidence_mode,
            dataset_path=dataset_path,
        )
        input_payload = session_input_measure(dataset)
    except Ex09OpenAiError:
        # El preflight local ya registra el fallo de los inputs y no queda listo.
        pass
    decision = evaluate_provider_preflight(
        local=local,
        attestation=attestation,
        credential_available=key is not None,
        credential_fingerprint_value=credential_fingerprint(key) if key else None,
        input_payload=input_payload,
        # Sin mecanismo legítimo sin llamada (INPUT_TOKENS_UNAVAILABLE), no se inyectan.
        input_tokens=None,
    )
    report = {
        "schema_version": "s11c3-ex09-openai-provider-preflight-v1",
        "scenario_id": SCENARIO_ID,
        "protocol_version": PROTOCOL.version,
        "methodology_version": METHODOLOGY_VERSION,
        "evidence_mode": evidence_mode,
        "recorded_at": _as_utc(recorded_at),
        "operator": OPERATOR,
        "attestation": "operator, provider console read-only",
        "run_id": None,
        "persisted": False,
        "provider_calls": 0,
        "classification_requests": 0,
        "tpm_policy": TPM_POLICY,
        "provider_config": {
            "provider": PROTOCOL.provider,
            "api": PROTOCOL.api,
            "endpoint": PROTOCOL.endpoint,
            "configured_model": PROTOCOL.model_name,
            "reasoning_effort": PROTOCOL.reasoning_effort,
            "store": PROTOCOL.store,
            "request_timeout_seconds": PROTOCOL.request_timeout_seconds,
            "sdk_max_retries": PROTOCOL.sdk_max_retries,
        },
        "sdk_version": installed_sdk_version(),
        "identity_sha256": local["identity_sha256"],
        **decision,
        "local_preflight": local,
    }
    if key and key.encode("utf-8") in canonical_json_bytes(report):
        raise Ex09OpenAiError("El resultado del preflight contendría la clave del proveedor.")
    return report


# --- Estado y persistencia -------------------------------------------------------------


def limits_checks(state: RunState) -> list[dict[str, Any]]:
    checks = []
    for record in state.records:
        if _LIMITS_TYPE.fullmatch(record["artifact_type"]):
            checks.append(state.payloads.get(record["artifact_type"]) or {})
    return checks


def session_executed(state: RunState) -> bool:
    return state.has(_type("credential-provisioned"))


def _references(records: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "artifact_id": record["artifact_id"],
            "relative_path": record["relative_path"],
            "sha256": record["sha256"],
        }
        for record in records
    ]


def _persist(
    store: EvidenceStore,
    run_id: str,
    artifact_type: str,
    payload: Any,
    *,
    kind: ArtifactKind = ArtifactKind.METADATA,
    classification: Classification = Classification.INTERNAL,
    source: str,
    secret: str | None = None,
):
    content = canonical_json_bytes(payload)
    if secret and secret.encode("utf-8") in content:
        raise EvidencePolicyError("La evidencia contendría la clave del proveedor.")
    return store.persist_json(
        run_id=run_id,
        scenario_id=SCENARIO_ID,
        artifact_type=artifact_type,
        kind=kind,
        classification=classification,
        filename=f"{artifact_type}.json",
        payload=payload,
        source=source,
    )


def _event_payload(
    *, event: str, run_id: str, recorded_at: datetime, result: str, **fields: Any
) -> dict[str, Any]:
    return {
        "schema_version": "s11c3-ex09-openai-event-v1",
        "event": event,
        "run_id": run_id,
        "recorded_at": _as_utc(recorded_at),
        "operator": OPERATOR,
        "attestation": "operator",
        "result": result,
        **fields,
    }


def _require_open_run(state: RunState) -> None:
    initial = state.initial_manifest
    if initial is None:
        raise Ex09OpenAiError("El run no tiene manifest inicial.")
    if (
        initial.get("scenario_id") != SCENARIO_ID
        or initial.get("configuration", {}).get("protocol_version") != PROTOCOL.version
    ):
        raise Ex09OpenAiError(f"El run no pertenece a EX-09 {PROTOCOL.version}.")
    if state.finalized:
        raise Ex09OpenAiError("El run ya está finalizado y es inmutable.")


# --- Inicio ----------------------------------------------------------------------------


def start_run(
    store: EvidenceStore,
    *,
    git_sha: str,
    git_clean: bool,
    nonce: str,
    evidence_mode: str,
    started_at: datetime,
    timezone: str,
    dataset_path: Path | None = None,
) -> dict[str, Any]:
    """Asigna el `run_id` UUIDv5 y registra el manifest inicial (`S11A-v1.1`)."""
    if not git_clean or not _SHA1.fullmatch(git_sha):
        raise Ex09OpenAiError("EX-09 exige un commit verificado y un working tree limpio.")
    identity, _dataset = build_identity(
        git_sha=git_sha,
        git_clean=git_clean,
        evidence_mode=evidence_mode,
        dataset_path=dataset_path,
    )
    manifest = create_manifest(
        repo_root=Path("."),
        started_at=started_at,
        environment=ENVIRONMENT_NAME,
        timezone=timezone,
        seed=DEFAULT_SEED,
        dataset_id=DATASET_ID,
        dataset_sha256=identity["dataset_sha256"],
        scenario_id=SCENARIO_ID,
        experiment_id=identity["experiment_id"],
        execution_nonce=nonce,
        configuration=identity,
        expected_git_sha=git_sha,
        supplied_git_sha=git_sha,
        supplied_git_clean=git_clean,
    )
    if manifest.methodology_version != FROZEN_METHODOLOGY_VERSION:
        raise Ex09OpenAiError("Un run de EX-09 OpenAI sólo puede registrar S11A-v1.1.")
    if store.records(manifest.run_id):
        raise Ex09OpenAiError("El run_id ya tiene evidencia indexada; se exige un nonce nuevo.")
    record = store.persist_json(
        run_id=manifest.run_id,
        scenario_id=SCENARIO_ID,
        artifact_type="run-manifest-initial",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="run-manifest.initial.json",
        payload=manifest.to_dict(),
        source="s11_ex09_openai.start",
    )
    return {
        "run_id": manifest.run_id,
        "scenario_id": SCENARIO_ID,
        "protocol_version": PROTOCOL.version,
        "methodology_version": manifest.methodology_version,
        "evidence_mode": evidence_mode,
        "identity_sha256": identity_sha256(identity),
        "manifest_sha256": record.sha256,
        "status": manifest.status,
    }


# --- Atestaciones del operador ---------------------------------------------------------


def record_event(
    store: EvidenceStore,
    *,
    run_id: str,
    event: str,
    recorded_at: datetime,
    environ: Mapping[str, str],
    tier: str | None = None,
    rpm: int | None = None,
    tpm: int | None = None,
    rpd: int | None = None,
    credit_available: bool | None = None,
    spend_limit_reached: bool | None = None,
    credential_fingerprint_value: str | None = None,
    container_absent: bool = False,
) -> dict[str, Any]:
    """Registra una atestación del operador, autenticada por la custodia."""
    if event not in OPERATOR_EVENTS:
        raise Ex09OpenAiError(f"Evento desconocido: {event!r}.")
    with store.mutation():
        state = load_state(store, run_id)
        _require_open_run(state)
        if event == "limits_check":
            payload, artifact_type = _limits_check(
                state,
                recorded_at=recorded_at,
                tier=tier,
                rpm=rpm,
                tpm=tpm,
                rpd=rpd,
                credit_available=credit_available,
                spend_limit_reached=spend_limit_reached,
            )
        else:
            payload, artifact_type = _lifecycle_event(
                state,
                event=event,
                recorded_at=recorded_at,
                environ=environ,
                credential_fingerprint_value=credential_fingerprint_value,
                container_absent=container_absent,
            )
        record = _persist(store, run_id, artifact_type, payload, source=f"s11_ex09_openai.{event}")
    return {"artifact_type": artifact_type, "sha256": record.sha256, **payload}


def limits_result(
    *, rpm: int, rpd: int | None, credit_available: bool, spend_limit_reached: bool
) -> str:
    """`OK` sólo si la cuenta admite las 28 peticiones y tiene crédito disponible."""
    enough = rpm >= MINIMUM_REQUESTS and (rpd is None or rpd >= MINIMUM_REQUESTS)
    if enough and credit_available and not spend_limit_reached:
        return LIMITS_OK
    return LIMITS_INSUFFICIENT


def _limits_check(
    state: RunState,
    *,
    recorded_at: datetime,
    tier: str | None,
    rpm: int | None,
    tpm: int | None,
    rpd: int | None,
    credit_available: bool | None,
    spend_limit_reached: bool | None,
) -> tuple[dict[str, Any], str]:
    if session_executed(state):
        raise Ex09OpenAiRefusal(["limits-check-after-session-start"], terminal=False)
    if not isinstance(tier, str) or not tier.strip():
        raise Ex09OpenAiError("El tier observado es obligatorio.")
    for name, value in (("rpm", rpm), ("tpm", tpm)):
        if type(value) is not int or value < 0:
            raise Ex09OpenAiError(f"{name} debe ser un entero no negativo observado en la consola.")
    if rpd is not None and (type(rpd) is not int or rpd < 0):
        raise Ex09OpenAiError("rpd debe ser un entero no negativo, u omitirse si no existe.")
    if type(credit_available) is not bool or type(spend_limit_reached) is not bool:
        raise Ex09OpenAiError("El crédito y el límite de gasto deben atestarse explícitamente.")
    sequence = len(limits_checks(state)) + 1
    if sequence > 99:
        raise Ex09OpenAiError("Demasiadas comprobaciones de límites para un run.")
    payload = _event_payload(
        event="limits_check",
        run_id=state.run_id,
        recorded_at=recorded_at,
        result=limits_result(
            rpm=rpm,
            rpd=rpd,
            credit_available=credit_available,
            spend_limit_reached=spend_limit_reached,
        ),
        model=PROTOCOL.model_name,
        tier=tier.strip(),
        rpm=rpm,
        tpm=tpm,
        rpd=rpd,
        credit_available=credit_available,
        spend_limit_reached=spend_limit_reached,
        minimum_requests=MINIMUM_REQUESTS,
        source="provider console, read-only; no provider call",
    )
    return payload, _type(f"limits-check-{sequence:02d}")


def _lifecycle_event(
    state: RunState,
    *,
    event: str,
    recorded_at: datetime,
    environ: Mapping[str, str],
    credential_fingerprint_value: str | None,
    container_absent: bool,
) -> tuple[dict[str, Any], str]:
    artifact_type = _type(event.replace("_", "-"))
    if state.has(artifact_type):
        raise Ex09OpenAiRefusal([f"already-recorded:{artifact_type}"], terminal=False)
    provisioned = state.payload(_type("credential-provisioned"))
    if provisioned is None:
        raise Ex09OpenAiRefusal(["session-not-executed"], terminal=False)
    fingerprint = provisioned["credential_fingerprint"]
    fields: dict[str, Any] = {"credential_fingerprint": fingerprint}
    if event == "credential_removed":
        if read_credential({PROVIDER_KEY_ENV: environ.get(PROVIDER_KEY_ENV, "")}) is not None:
            raise Ex09OpenAiRefusal([f"{PROVIDER_KEY_ENV}-still-present"], terminal=False)
        fields["executor_process_ended"] = True
        fields["environment_variable_absent"] = True
    elif event == "access_revoked":
        if not state.has(_type("credential-removed")):
            raise Ex09OpenAiRefusal(["credential-not-removed"], terminal=False)
        if (
            not _FINGERPRINT.fullmatch(credential_fingerprint_value or "")
            or credential_fingerprint_value != fingerprint
        ):
            raise Ex09OpenAiRefusal(["fingerprint-mismatch"], terminal=False)
        fields["revoked_in"] = "provider console"
    elif event == "cleanup":
        if not state.has(_type("credential-removed")):
            raise Ex09OpenAiRefusal(["credential-not-removed"], terminal=False)
        if not container_absent:
            raise Ex09OpenAiRefusal(["container-present"], terminal=False)
        fields["ephemeral_container_absent"] = True
    payload = _event_payload(
        event=event, run_id=state.run_id, recorded_at=recorded_at, result=CHECK_PASS, **fields
    )
    return payload, artifact_type


# --- Ejecución de la sesión ------------------------------------------------------------


SessionRunner = Callable[..., SessionEvaluation]


def admission(state: RunState, *, identity: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Condiciones previas a la sesión. Devuelve `(pendientes, terminales)`."""
    pending: list[str] = []
    terminal: list[str] = []
    if session_executed(state) or state.has(_type("session")):
        terminal.append("session-already-attempted")
    divergent = compare_identity(state.initial_manifest.get("configuration", {}), identity)
    if divergent:
        terminal.append(f"identity-divergent:{','.join(divergent)}")
    if state.unreadable:
        terminal.append(f"unreadable:{','.join(state.unreadable)}")
    checks = limits_checks(state)
    if not checks:
        pending.append("limits-check-missing")
    elif checks[-1].get("result") != LIMITS_OK:
        pending.append("limits-insufficient")
    return pending, terminal


def execute_session(
    store: EvidenceStore,
    *,
    run_id: str,
    git_sha: str,
    git_clean: bool,
    environ: Mapping[str, str],
    repo_root: Path,
    clock: Callable[[], datetime],
    runner: SessionRunner = run_openai_session,
    evidence_mode: str = OFFICIAL_MODE,
    dataset_path: Path | None = None,
    mounts_path: Path = Path("/proc/self/mounts"),
) -> dict[str, Any]:
    """Ejecuta la sesión una sola vez y deja su evidencia custodiada.

    Todo lo que no cuesta una llamada ocurre antes de la primera. El resumen devuelto
    no contiene CA-4: la decisión se deriva al finalizar.
    """
    key = read_credential({PROVIDER_KEY_ENV: environ.get(PROVIDER_KEY_ENV, "")})
    if key is None:
        raise Ex09OpenAiRefusal([f"{PROVIDER_KEY_ENV}-missing"], terminal=False)
    fingerprint = credential_fingerprint(key)
    try:
        identity, _dataset = build_identity(
            git_sha=git_sha,
            git_clean=git_clean,
            evidence_mode=evidence_mode,
            dataset_path=dataset_path,
        )
    except Ex09OpenAiError as exc:
        raise Ex09OpenAiRefusal([f"identity:{exc}"], terminal=False) from exc

    with store.mutation():
        state = load_state(store, run_id)
        _require_open_run(state)
        pending, terminal = admission(state, identity=identity)
        custody_errors = store.verify(run_id)
        if custody_errors:
            terminal.append(f"custody:{','.join(custody_errors)}")
        if terminal:
            raise Ex09OpenAiRefusal(terminal + pending, terminal=True)
        if pending:
            raise Ex09OpenAiRefusal(pending, terminal=False)

        artifacts = []
        provisioned = _persist(
            store,
            run_id,
            _type("credential-provisioned"),
            _event_payload(
                event="credential_provisioned",
                run_id=run_id,
                recorded_at=clock(),
                result=CHECK_PASS,
                credential_fingerprint=fingerprint,
                delivery=f"process environment variable {PROVIDER_KEY_ENV}",
            ),
            source="s11_ex09_openai.run",
            secret=key,
        )
        preflight = run_preflight(
            git_sha=git_sha,
            git_clean=git_clean,
            environ=environ,
            repo_root=repo_root,
            evidence_root=store.root,
            evidence_mode=evidence_mode,
            dataset_path=dataset_path,
            mounts_path=mounts_path,
            require_credential=True,
        )
        preflight_record = _persist(
            store,
            run_id,
            _type("preflight"),
            {**preflight, "run_id": run_id, "recorded_at": _as_utc(clock())},
            source="s11_ex09_openai.run",
            secret=key,
        )
        artifacts += [asdict(provisioned), asdict(preflight_record)]
        if preflight["result"] != CHECK_PASS:
            raise Ex09OpenAiRefusal(["preflight-failed"], terminal=True)

        started_at = clock()
        execution_error = None
        session = None
        with capture_stderr() as captured:
            try:
                session = runner(protocol=PROTOCOL, api_key=key, dataset_path=dataset_path)
            except Exception as exc:
                # Se custodia y el run queda inválido; nunca se reintenta.
                execution_error = f"{type(exc).__name__}: {sanitize_text(str(exc))}"
        finished_at = clock()

        stderr_record = store.persist_bytes(
            run_id=run_id,
            scenario_id=SCENARIO_ID,
            artifact_type=_type("stderr"),
            kind=ArtifactKind.LOG,
            classification=Classification.INTERNAL,
            filename=f"{_type('stderr')}.txt",
            content=redact_stderr(captured.getvalue(), secret=key),
            mime_type="text/plain",
            source="s11_ex09_openai.run",
            transformation="evaluation-sanitization+secret-guard",
        )
        artifacts.append(asdict(stderr_record))
        if session is not None:
            session_record = _persist(
                store,
                run_id,
                _type("session"),
                asdict(session),
                kind=ArtifactKind.RAW,
                classification=Classification.EXPERIMENTAL,
                source="s11_ex09_openai.run",
                secret=key,
            )
            artifacts.append(asdict(session_record))
        session_manifest = {
            "schema_version": "s11c3-ex09-openai-session-manifest-v1",
            "run_id": run_id,
            "scenario_id": SCENARIO_ID,
            "protocol_version": PROTOCOL.version,
            "methodology_version": METHODOLOGY_VERSION,
            "evidence_mode": evidence_mode,
            "provider": PROTOCOL.provider,
            "model": PROTOCOL.model_name,
            "sdk_version": identity["sdk"]["version"],
            "commit_sha": git_sha,
            "git_clean": git_clean,
            "dataset_sha256": identity["dataset_sha256"],
            "prompt_sha256": identity["prompt_sha256"],
            "schema_sha256": identity["schema_sha256"],
            "reasoning_effort": PROTOCOL.reasoning_effort,
            "image_detail": PROTOCOL.image_detail,
            "timeout_seconds": PROTOCOL.request_timeout_seconds,
            "retry_policy": identity["retries"],
            "store": PROTOCOL.store,
            "credential_fingerprint": fingerprint,
            "identity_sha256": identity_sha256(identity),
            "start_timestamp": _as_utc(started_at),
            "end_timestamp": _as_utc(finished_at),
            "attempted_cases": session.attempted_cases if session else 0,
            "images_sent": session.images_sent if session else None,
            "execution_error": execution_error,
            "artifacts": _references(artifacts),
            "scored": False,
        }
        manifest_record = _persist(
            store, run_id, _type("session-manifest"), session_manifest, source="s11_ex09_openai.run"
        )
    return {
        "run_id": run_id,
        "evidence_mode": evidence_mode,
        "start_timestamp": session_manifest["start_timestamp"],
        "end_timestamp": session_manifest["end_timestamp"],
        "attempted_cases": session_manifest["attempted_cases"],
        "execution_error": execution_error,
        "credential_fingerprint": fingerprint,
        "session_manifest_sha256": manifest_record.sha256,
        "scored": False,
    }


# --- Validez de la sesión y CA-4 -------------------------------------------------------

#: Campos de la sesión que deben coincidir con la identidad del run.
_SESSION_IDENTITY_FIELDS = {
    "protocol_version": "protocol_version",
    "provider": "provider",
    "api": "api",
    "model_requested": "model_name",
    "reasoning_effort": "reasoning_effort",
    "image_detail": "image_detail",
    "store": "store",
    "request_timeout_seconds": "request_timeout_seconds",
    "dataset_sha256": "dataset_sha256",
    "prompt_sha256": "prompt_sha256",
    "schema_sha256": "schema_sha256",
}


def session_errors(session: Mapping[str, Any], *, identity: Mapping[str, Any]) -> list[str]:
    """Causas por las que la sesión no es una medición válida de los 28 casos.

    Cubre la configuración, la correlación (casos ausentes, duplicados, inesperados,
    fuera de rango o sobrantes) y la validez de cada respuesta. Cualquiera produce
    `NOT_DEMONSTRATED`, nunca `FAIL`.
    """
    errors: list[str] = []
    for session_field, identity_field in _SESSION_IDENTITY_FIELDS.items():
        if session.get(session_field) != identity.get(identity_field):
            errors.append(f"parameter-divergence:{session_field}")
    if session.get("sdk_version") != identity.get("sdk", {}).get("version"):
        errors.append("parameter-divergence:sdk_version")
    if session.get("sdk_max_retries") != identity.get("retries", {}).get("sdk_max_retries"):
        errors.append("parameter-divergence:sdk_max_retries")
    if session.get("images_sent") != 0:
        errors.append(f"images-sent:{session.get('images_sent')}")
    if session.get("aborted_reason"):
        errors.append(f"aborted:{session['aborted_reason']}")

    expected = list(identity.get("case_ids", []))
    cases = list(session.get("cases") or [])
    results = list(session.get("results") or [])
    observed = [case.get("case_id") for case in cases]
    if len(observed) != len(expected):
        errors.append(f"responses:{len(observed)}/{len(expected)}")
    duplicated = sorted({case_id for case_id in observed if observed.count(case_id) > 1})
    if duplicated:
        errors.append(f"duplicate:{','.join(map(str, duplicated))}")
    missing = sorted(set(expected) - set(observed))
    if missing:
        errors.append(f"missing:{','.join(missing)}")
    unexpected = sorted(set(map(str, observed)) - set(expected))
    if unexpected:
        errors.append(f"unexpected:{','.join(unexpected)}")
    if not errors and observed != expected:
        errors.append("out-of-order")
    sequences = [case.get("sequence") for case in cases]
    if sequences != list(range(1, len(cases) + 1)) or len(cases) > len(expected):
        errors.append("out-of-range")
    if [result.get("id") for result in results] != observed:
        errors.append("results-not-correlated")

    response_ids = [case.get("provider_response_id") for case in cases]
    if len(set(response_ids)) != len(response_ids):
        errors.append("provider-response-id-duplicated")
    models = {case.get("model") for case in cases}
    if len(models) > 1:
        errors.append("model-divergent-between-cases")
    limit_ms = identity.get("request_timeout_seconds", 0) * 1000
    for case in cases:
        case_id = case.get("case_id")
        if case.get("validity") != VALID or case.get("error_class"):
            errors.append(f"invalid:{case_id}:{case.get('error_class')}")
            continue
        if not case.get("provider_response_id") or not case.get("provider_request_id"):
            errors.append(f"uncorrelated:{case_id}")
        if not case.get("model"):
            errors.append(f"model-missing:{case_id}")
        if not isinstance(case.get("duration_ms"), int) or case["duration_ms"] > limit_ms:
            errors.append(f"duration:{case_id}")
        if case.get("image_bytes_sent") != 0:
            errors.append(f"image:{case_id}")
    return errors


def decide_ca4(
    results: Sequence[CaseResult], *, dataset: EvaluationDataset
) -> tuple[str, dict[str, Any] | None]:
    """CA-4 sobre 28 respuestas válidas, con el scoring y el umbral congelados.

    Con menos de 28 válidas no hay medición: `NOT_DEMONSTRATED`. Un error del
    proveedor nunca se cuenta como desacuerdo.
    """
    report = build_report(
        dataset, tuple(results), adapter_name="openai", model_name=PROTOCOL.model_name
    )
    valid, result = classify_run(report)
    if not valid:
        return RESULT_NOT_DEMONSTRATED, None
    return result, {
        "ca4_result": result,
        "ca4_concordant_cases": report.ca4_concordant_cases,
        "total_cases": report.total_cases,
        "ca4_concordance_pct": str(report.ca4_concordance_pct),
        "minimum_concordant_cases": minimum_concordant_cases(report.total_cases),
        "threshold_pct": str(CA4_MIN_CONCORDANCE_PCT),
        "diagnostics": {
            "category_accuracy_pct": str(report.category_accuracy_pct),
            "institution_accuracy_pct": str(report.institution_accuracy_pct),
            "priority_accuracy_pct": str(report.priority_accuracy_pct),
            "inconsistency_accuracy_pct": str(report.inconsistency_accuracy_pct),
            "strict_concordance_pct": str(report.strict_concordance_pct),
        },
    }


@dataclass(frozen=True)
class Evaluation:
    decision: str
    invalidating: tuple[str, ...]
    pending: tuple[str, ...]
    ca4: dict[str, Any] | None


def evaluate_run(
    store: EvidenceStore,
    state: RunState,
    *,
    dataset: EvaluationDataset,
    identity: Mapping[str, Any],
) -> Evaluation:
    """Decide sobre la evidencia custodiada. Sólo `PASS`, `FAIL` o `NOT_DEMONSTRATED`."""
    invalidating: list[str] = []
    pending: list[str] = []
    invalidating += [f"custody:{error}" for error in store.verify(state.run_id)]
    invalidating += [f"unreadable:{artifact}" for artifact in state.unreadable]
    invalidating += [
        f"identity-divergent:{name}"
        for name in compare_identity(state.initial_manifest.get("configuration", {}), identity)
    ]
    if state.initial_manifest.get("methodology_version") != FROZEN_METHODOLOGY_VERSION:
        invalidating.append("methodology-version")

    if not session_executed(state):
        return Evaluation(
            decision=RESULT_NOT_DEMONSTRATED,
            invalidating=tuple(invalidating),
            pending=("session-not-executed",),
            ca4=None,
        )
    checks = limits_checks(state)
    if not checks or checks[-1].get("result") != LIMITS_OK:
        invalidating.append("limits-check-not-ok")
    preflight = state.payload(_type("preflight"))
    if not preflight or preflight.get("result") != CHECK_PASS:
        invalidating.append("preflight-failed")
    manifest = state.payload(_type("session-manifest"))
    if manifest is None:
        invalidating.append("session-manifest-missing")
    else:
        if manifest.get("execution_error"):
            invalidating.append("execution-error")
        indexed = {record["artifact_id"]: record["sha256"] for record in state.records}
        if any(indexed.get(ref["artifact_id"]) != ref["sha256"] for ref in manifest["artifacts"]):
            invalidating.append("session-artifact-hash")
        if manifest.get("identity_sha256") != identity_sha256(identity):
            invalidating.append("session-identity")
    if not state.has(_type("stderr")):
        invalidating.append("stderr-missing")
    session = state.payload(_type("session"))
    if session is None:
        invalidating.append("session-missing")
    else:
        invalidating += session_errors(session, identity=identity)
    for event in ("credential-removed", "access-revoked", "cleanup"):
        if not state.has(_type(event)):
            pending.append(f"{event}-missing")

    ca4 = None
    decision = RESULT_NOT_DEMONSTRATED
    if not invalidating and not pending:
        try:
            results = [CaseResult(**item) for item in session["results"]]
        except (KeyError, TypeError) as exc:
            invalidating.append(f"results-unreadable:{type(exc).__name__}")
        else:
            decision, ca4 = decide_ca4(results, dataset=dataset)
            if ca4 is None:
                invalidating.append("ca4-not-computable")
    return Evaluation(
        decision=decision if decision in (RESULT_PASS, RESULT_FAIL) else RESULT_NOT_DEMONSTRATED,
        invalidating=tuple(invalidating),
        pending=tuple(pending),
        ca4=ca4 if decision in (RESULT_PASS, RESULT_FAIL) else None,
    )


def finalize_run(
    store: EvidenceStore,
    *,
    run_id: str,
    git_sha: str,
    git_clean: bool,
    clock: Callable[[], datetime],
    evidence_mode: str = OFFICIAL_MODE,
    abandon_reason: str | None = None,
    dataset_path: Path | None = None,
) -> dict[str, Any]:
    """Decide, persiste la decisión y sella el run con el manifest final.

    Un run al que le faltan la sesión o las atestaciones del operador no se finaliza
    salvo que el operador declare `abandon_reason`; entonces es `NOT_DEMONSTRATED`.
    """
    identity, dataset = build_identity(
        git_sha=git_sha,
        git_clean=git_clean,
        evidence_mode=evidence_mode,
        dataset_path=dataset_path,
    )
    with store.mutation():
        store.recover_finalization(run_id)
        state = load_state(store, run_id)
        _require_open_run(state)
        now = clock()
        known_custody_errors = set(store.verify(run_id))
        evaluation = evaluate_run(store, state, dataset=dataset, identity=identity)
        reasons = list(evaluation.invalidating)
        if evaluation.pending and not abandon_reason:
            raise Ex09OpenAiRefusal(evaluation.pending, terminal=False)
        decision = evaluation.decision
        if abandon_reason:
            reasons.append(f"abandoned:{sanitize_text(abandon_reason)}")
            reasons += list(evaluation.pending)
            decision = RESULT_NOT_DEMONSTRATED
        if decision == RESULT_NOT_DEMONSTRATED and not reasons:
            reasons.append("run-not-demonstrable")
        decision_payload = {
            "schema_version": "s11c3-ex09-openai-decision-v1",
            "run_id": run_id,
            "scenario_id": SCENARIO_ID,
            "protocol_version": PROTOCOL.version,
            "methodology_version": METHODOLOGY_VERSION,
            "evidence_mode": evidence_mode,
            "decided_at": _as_utc(now),
            "decision": decision,
            "reasons": reasons if decision == RESULT_NOT_DEMONSTRATED else [],
            "ca4": evaluation.ca4 if decision in (RESULT_PASS, RESULT_FAIL) else None,
            "credential_fingerprint": (state.payload(_type("credential-provisioned")) or {}).get(
                "credential_fingerprint"
            ),
            "allowed_decisions": [RESULT_PASS, RESULT_FAIL, RESULT_NOT_DEMONSTRATED],
        }
        existing = state.payload(_type("decision"))
        if existing is not None:
            if (existing["decision"], existing["reasons"]) != (
                decision_payload["decision"],
                decision_payload["reasons"],
            ):
                raise Ex09OpenAiError("La decisión custodiada no coincide con la recalculada.")
            decision_payload = existing
            decision_sha256 = state.record(_type("decision"))["sha256"]
        else:
            decision_sha256 = _persist(
                store,
                run_id,
                _type("decision"),
                decision_payload,
                kind=ArtifactKind.DERIVED,
                classification=Classification.EXPERIMENTAL,
                source="s11_ex09_openai.finalize",
            ).sha256
        records = store.records(run_id)
        initial = dict(state.initial_manifest)
        initial["artifact_references"] = tuple(
            ArtifactReference(**reference) for reference in initial.get("artifact_references", [])
        )
        manifest = RunManifest(**initial)
        finished_at = clock()
        if decision == RESULT_NOT_DEMONSTRATED:
            status, invalidity = RunStatus.INVALID, "; ".join(reasons)[:2000]
        else:
            status, invalidity = RunStatus.COMPLETED, None
        final = finalize_manifest(
            manifest,
            finished_at=finished_at,
            status=status,
            artifacts=[ArtifactReference(**reference) for reference in _references(records)],
            invalidity_reason=invalidity,
        )
        final_record = store.finalize_with_json_artifact(
            finished_at=finished_at,
            run_id=run_id,
            scenario_id=SCENARIO_ID,
            artifact_type="run-manifest-final",
            kind=ArtifactKind.METADATA,
            classification=Classification.INTERNAL,
            filename="run-manifest.final.json",
            payload=final.to_dict(),
            source="s11_ex09_openai.finalize",
        )
        verification = store.verify(run_id)
        if verification and not (
            decision == RESULT_NOT_DEMONSTRATED and set(verification) <= known_custody_errors
        ):
            raise Ex09OpenAiError(f"Falló la validación final de la custodia: {verification}")
        validate_finalized_state(
            state.run_root, store.records(run_id), custody_hmac_key=store.custody_hmac_key
        )
    return {
        "run_id": run_id,
        "evidence_mode": evidence_mode,
        "decision": decision,
        "reasons": decision_payload["reasons"],
        "decision_sha256": decision_sha256,
        "manifest_status": final.status,
        "manifest_sha256": final_record.sha256,
        "custody_verification": CHECK_FAIL if verification else CHECK_PASS,
        "custody_errors": verification,
    }


def run_status(store: EvidenceStore, run_id: str, *, now: datetime) -> dict[str, Any]:
    """Resumen de sólo lectura. No expone ninguna métrica."""
    state = load_state(store, run_id)
    checks = limits_checks(state)
    return {
        "run_id": run_id,
        "protocol_version": PROTOCOL.version,
        "finalized": state.finalized,
        "custody_errors": store.verify(run_id),
        "limits_checks": len(checks),
        "limits_ok": bool(checks) and checks[-1].get("result") == LIMITS_OK,
        "session_executed": session_executed(state),
        "credential_removed": state.has(_type("credential-removed")),
        "access_revoked": state.has(_type("access-revoked")),
        "cleanup": state.has(_type("cleanup")),
        "checked_at": _as_utc(now),
    }
