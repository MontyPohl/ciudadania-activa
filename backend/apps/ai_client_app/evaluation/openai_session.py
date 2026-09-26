"""Protocolo `s11-ex09-openai-v1` y su sesión única contra la Responses API.

Implementa la instancia OpenAI de EX-09 que congeló la decisión S11-C3 del
26/09/2026 (`docs/PROGRESO_SPRINTS.md`, P5–P6) con la enmienda `S11A-v1.1`. No es
una recuperación de S8-E2: la serie R (`recovery.py`) pertenece a la configuración
Gemini, que queda histórica y no se toca.

Lo que cambia frente a R2 es sólo lo que depende del proveedor. Siguen idénticos el
dataset y sus bytes, el prompt, el esquema de salida, el parser, la comparación
exacta de CA-4 (`scoring.compare_case`) y su umbral. Por eso este módulo reutiliza
esas piezas en lugar de reescribirlas.

Tres reglas del protocolo se hacen estructurales:

- **Una sola sesión, secuencial, sin pausas.** Los 28 casos van en el orden del
  dataset, uno por vez. No hay lotes, ventanas RPD ni pacing: eran restricciones
  del nivel gratuito de Gemini y no se heredan.
- **Ninguna imagen sale.** El dataset no tiene imágenes y `detail=auto` no está
  documentado para `gpt-6-luna`. Si una petición llevara imagen, la sesión se
  detiene antes de enviarla.
- **Nada se reintenta.** El SDK hace un único intento (`max_retries=0`), este
  módulo no repite ningún caso y Celery no interviene. Un fallo queda registrado
  con su clase; nunca se convierte en un desacuerdo de clasificación.

La clave entra explícita y no se conserva. El módulo no lee `OPENAI_API_KEY` ni
ninguna otra variable de entorno.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..parser import ClassificationParseError, parse_classification_response
from ..prompts import PROMPT_VERSION
from .dataset import EvaluationDataset
from .execution import load_verified_dataset, verify_prompt
from .recovery import RecoveryProtocolError
from .runner import build_request
from .sanitization import sanitize_error_message
from .scoring import CaseResult, compare_case, failed_case

VALID = "VALID"
INVALID = "INVALID"
_NANOSEGUNDOS_POR_MILISEGUNDO = 1_000_000

#: Clases de error de un caso. Todas invalidan el run; ninguna es un desacuerdo.
ERROR_CLASSES = (
    "timeout",
    "network_error",
    "rate_limit_429",
    "insufficient_quota",
    "authentication",
    "authorization",
    "provider_4xx",
    "provider_5xx",
    "provider_incomplete",
    "malformed_response",
    "schema_validation",
    "duration_exceeded",
    "image_present",
    "unexpected_provider_error",
)

#: Mensajes del parser congelado que indican que la salida ni siquiera es JSON
#: utilizable. El resto de sus rechazos son violaciones del esquema v1.
_PARSER_MALFORMED = ("invalid top-level type", "invalid raw type", "invalid json")


class OpenAiProtocolError(RuntimeError):
    """Los inputs no son los congelados. Se detecta antes de construir el cliente."""


@dataclass(frozen=True, slots=True)
class OpenAiProtocol:
    """Valores absolutos de un protocolo OpenAI de EX-09.

    Los nombres `version`, `dataset_*` y `prompt_sha256` coinciden con los de
    `RecoveryProtocol` para que la verificación de bytes de `execution.py` se aplique
    sin cambios.
    """

    version: str
    provider: str
    api: str
    endpoint: str
    model_name: str
    reasoning_effort: str
    image_detail: str
    store: bool
    request_timeout_seconds: int
    sdk_package: str
    sdk_version: str
    sdk_max_retries: int
    script_retries: int
    celery_retries: int
    dataset_version: str
    dataset_sha256: str
    total_cases: int
    prompt_version: str
    prompt_sha256: str
    schema_name: str
    schema_sha256: str
    sessions: int
    concurrency: int
    inter_request_delay_seconds: int

    def fields(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__slots__}


#: `s11-ex09-openai-v1`, congelado el 26/09/2026 (P6). No se edita: un cambio de
#: cualquier valor es un protocolo nuevo que se prerregistra antes de ejecutar.
S11_EX09_OPENAI_V1 = OpenAiProtocol(
    version="s11-ex09-openai-v1",
    provider="openai",
    api="responses",
    endpoint="POST /v1/responses",
    model_name="gpt-6-luna",
    reasoning_effort="low",
    image_detail="auto",
    store=False,
    request_timeout_seconds=60,
    sdk_package="openai",
    sdk_version="3.19.2",
    sdk_max_retries=0,
    script_retries=0,
    celery_retries=0,
    dataset_version="s8-evaluation-v1",
    dataset_sha256="3bc54d591dc104aeabeecb2f5cc7c7a38e70ba042e7f045321ab33ae9b5efad4",
    total_cases=28,
    prompt_version="s8-classification-v1",
    prompt_sha256="f3c803984806a3b934db0417f6cc3ce59a49cdde1e30b4c86b862c2e11c665ca",
    schema_name="classification_v1",
    schema_sha256="ef2a7d354dc1f4526e6776c22e2356afa9881370618de75343aef339c5425f23",
    sessions=1,
    concurrency=1,
    inter_request_delay_seconds=0,
)


# --- Medidas de lo que realmente se envía ----------------------------------------------


def canonical_sha256(value: Any) -> str:
    """SHA-256 de la serialización canónica de S11 (`experiments_app.canonical`).

    Se replica aquí para no hacer depender la evaluación de la infraestructura S11;
    una prueba exige que ambas coincidan byte a byte.
    """
    content = (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def schema_sha256() -> str:
    """Hash del esquema que el adaptador pone en `text.format`, no de una copia."""
    from ..adapters.openai import build_classification_schema

    return canonical_sha256(build_classification_schema())


def installed_sdk_version(package: str = "openai") -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def adapter_constants() -> dict[str, Any]:
    """Constantes del adaptador que el protocolo fija. Se leen, no se copian."""
    from ..adapters import openai as adapter

    return {
        "sdk_max_retries": adapter._SDK_MAX_RETRIES,
        "image_detail": adapter._DETALLE_DE_IMAGEN,
        "schema_name": adapter._NOMBRE_DEL_ESQUEMA,
    }


def verify_protocol_inputs(
    *, protocol: OpenAiProtocol, dataset_path: Path | None = None
) -> tuple[EvaluationDataset, dict[str, str]]:
    """Todo lo que no cuesta una llamada, antes de que exista un cliente.

    Devuelve el dataset construido desde los bytes verificados y los digests medidos.
    """
    try:
        dataset, dataset_digest = load_verified_dataset(
            protocol=protocol, dataset_path=dataset_path
        )
        prompt_digest = verify_prompt(protocol=protocol)
    except RecoveryProtocolError as exc:
        raise OpenAiProtocolError(str(exc)) from exc
    errors = []
    if PROMPT_VERSION != protocol.prompt_version:
        errors.append(f"prompt-version:{PROMPT_VERSION}")
    measured_schema = schema_sha256()
    if measured_schema != protocol.schema_sha256:
        errors.append(f"schema-sha256:{measured_schema}")
    version = installed_sdk_version(protocol.sdk_package)
    if version != protocol.sdk_version:
        errors.append(f"sdk-version:{version}")
    constants = adapter_constants()
    for name in ("sdk_max_retries", "image_detail", "schema_name"):
        if constants[name] != getattr(protocol, name):
            errors.append(f"adapter-{name}:{constants[name]}")
    case_ids = [case.id for case in dataset.cases]
    if len(case_ids) != protocol.total_cases or len(set(case_ids)) != len(case_ids):
        errors.append(f"cases:{len(case_ids)}/{len(set(case_ids))}")
    if errors:
        raise OpenAiProtocolError(f"Los inputs no son los de {protocol.version}: {errors}")
    return dataset, {
        "dataset_sha256": dataset_digest,
        "prompt_sha256": prompt_digest,
        "schema_sha256": measured_schema,
        "sdk_version": version,
    }


# --- Clasificación de errores ----------------------------------------------------------


def classify_error(exc: BaseException) -> str:
    """Clase del fallo de un caso, decidida por tipo y código HTTP estructurado.

    El adaptador traduce los errores del SDK y encadena el original en `__cause__`;
    aquí se lee esa causa. Los rechazos del parser congelado se separan por su
    categoría: salida no JSON o violación del esquema v1.
    """
    import openai

    from ..exceptions import PermanentAiProviderError

    if isinstance(exc, ClassificationParseError):
        message = str(exc)
        if any(message.startswith(prefix) for prefix in _PARSER_MALFORMED):
            return "malformed_response"
        return "schema_validation"
    cause = exc.__cause__
    if isinstance(cause, openai.APITimeoutError):
        return "timeout"
    if isinstance(cause, openai.APIConnectionError):
        return "network_error"
    if isinstance(cause, openai.APIStatusError):
        status = cause.status_code
        if status == 429:
            return "insufficient_quota" if cause.code == "insufficient_quota" else "rate_limit_429"
        if status == 401:
            return "authentication"
        if status == 403:
            return "authorization"
        if 500 <= status <= 599:
            return "provider_5xx"
        return "provider_4xx"
    if isinstance(exc, PermanentAiProviderError) and cause is None:
        # `status` distinto de `completed`, salida vacía o negativa del modelo.
        return "provider_incomplete"
    return "unexpected_provider_error"


# --- Sesión ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CaseRecord:
    """Un caso tal como ocurrió. Sin clave, sin cabeceras y sin texto del prompt."""

    sequence: int
    case_id: str
    provider_response_id: str | None
    provider_request_id: str | None
    model: str | None
    timestamp_start: str
    timestamp_end: str
    duration_ms: int
    validity: str
    category_slug: str | None
    institution_slug: str | None
    priority: str | None
    error_class: str | None
    error_message: str | None
    image_bytes_sent: int


@dataclass(frozen=True, slots=True)
class SessionEvaluation:
    """Evidencia de la sesión. No contiene CA-4: se calcula al decidir, una sola vez."""

    protocol_version: str
    provider: str
    api: str
    model_requested: str
    reasoning_effort: str
    image_detail: str
    store: bool
    request_timeout_seconds: int
    sdk_version: str
    sdk_max_retries: int
    dataset_version: str
    dataset_sha256: str
    prompt_sha256: str
    schema_sha256: str
    started_at: str
    finished_at: str
    attempted_cases: int
    images_sent: int
    aborted_reason: str | None
    cases: tuple[CaseRecord, ...]
    results: tuple[CaseResult, ...]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def run_openai_session(
    *,
    protocol: OpenAiProtocol,
    api_key: str,
    dataset_path: Path | None = None,
    client_factory: Callable[..., object] | None = None,
    adapter_factory: Callable[..., object] | None = None,
    clock: Callable[[], datetime] = _utc_now,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
) -> SessionEvaluation:
    """Ejecuta los 28 casos una sola vez, en orden y sin pausas.

    Las factorías entran por parámetro para que las pruebas usen dobles sin red.
    Cuando no se pasan, se usan el `build_client` y el adaptador reales.
    """
    if not api_key:
        raise ValueError("La clave del proveedor no puede estar vacía.")
    dataset, measured = verify_protocol_inputs(protocol=protocol, dataset_path=dataset_path)

    if client_factory is None or adapter_factory is None:
        from ..adapters.openai import OpenAiClassificationAdapter, build_client

        client_factory = client_factory or build_client
        adapter_factory = adapter_factory or OpenAiClassificationAdapter
    client = client_factory(api_key=api_key, timeout_seconds=protocol.request_timeout_seconds)
    adapter = adapter_factory(
        client=client, model=protocol.model_name, reasoning_effort=protocol.reasoning_effort
    )
    limit_ms = protocol.request_timeout_seconds * 1000

    started_at = clock()
    records: list[CaseRecord] = []
    results: list[CaseResult] = []
    aborted_reason = None
    for sequence, case in enumerate(dataset.cases, start=1):
        request = build_request(case, dataset)
        if request.image_bytes:
            # Nunca se envía: el protocolo sólo es válido sin imágenes.
            aborted_reason = f"image-present:{case.id}"
            stamp = _utc(clock())
            records.append(
                CaseRecord(
                    sequence=sequence,
                    case_id=case.id,
                    provider_response_id=None,
                    provider_request_id=None,
                    model=None,
                    timestamp_start=stamp,
                    timestamp_end=stamp,
                    duration_ms=0,
                    validity=INVALID,
                    category_slug=None,
                    institution_slug=None,
                    priority=None,
                    error_class="image_present",
                    error_message=None,
                    image_bytes_sent=0,
                )
            )
            results.append(failed_case(case, "ImagePresent"))
            break

        timestamp_start = clock()
        inicio = monotonic_ns()
        call = None
        error_class = None
        error_message = None
        try:
            call = adapter.classify_with_metadata(request)
            parsed = parse_classification_response(call.text)
        except Exception as exc:
            error_class = classify_error(exc)
            error_message = sanitize_error_message(exc)
            result = failed_case(case, type(exc).__name__)
            parsed = None
        else:
            result = compare_case(case, parsed)
        duration_ms = max(0, (monotonic_ns() - inicio) // _NANOSEGUNDOS_POR_MILISEGUNDO)
        timestamp_end = clock()
        if error_class is None and duration_ms > limit_ms:
            # El SDK aplica el timeout por fase; la matriz 2.2 exige 60 s por request.
            error_class = "duration_exceeded"
            result = failed_case(case, "DurationExceeded")

        records.append(
            CaseRecord(
                sequence=sequence,
                case_id=case.id,
                provider_response_id=call.response_id if call else None,
                provider_request_id=call.request_id if call else None,
                model=call.model if call else None,
                timestamp_start=_utc(timestamp_start),
                timestamp_end=_utc(timestamp_end),
                duration_ms=duration_ms,
                validity=VALID if error_class is None else INVALID,
                category_slug=parsed.category_slug if parsed else None,
                institution_slug=parsed.institution_slug if parsed else None,
                priority=parsed.priority if parsed else None,
                error_class=error_class,
                error_message=error_message,
                image_bytes_sent=len(request.image_bytes),
            )
        )
        results.append(result)

    return SessionEvaluation(
        protocol_version=protocol.version,
        provider=protocol.provider,
        api=protocol.api,
        model_requested=protocol.model_name,
        reasoning_effort=protocol.reasoning_effort,
        image_detail=protocol.image_detail,
        store=protocol.store,
        request_timeout_seconds=protocol.request_timeout_seconds,
        sdk_version=measured["sdk_version"],
        sdk_max_retries=adapter_constants()["sdk_max_retries"],
        dataset_version=dataset.dataset_version,
        dataset_sha256=measured["dataset_sha256"],
        prompt_sha256=measured["prompt_sha256"],
        schema_sha256=measured["schema_sha256"],
        started_at=_utc(started_at),
        finished_at=_utc(clock()),
        attempted_cases=sum(1 for record in records if record.error_class != "image_present"),
        images_sent=sum(1 for record in records if record.image_bytes_sent),
        aborted_reason=aborted_reason,
        cases=tuple(records),
        results=tuple(results),
    )
