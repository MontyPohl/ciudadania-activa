"""Generador de carga y cálculo puro de EX-10 bajo el protocolo `s11-ex10-v1`.

Aplica la decisión G0 del 26/09/2026 (`docs/PROGRESO_SPRINTS.md`, «Decisión G0 —
protocolo de ejecución de EX-10»), fuente operativa de los parámetros que `S11A-v1.1`
deja abiertos. Este módulo no redefine ninguno: los fija como constantes, rechaza
cualquier otro valor y codifica las reglas de G0 como funciones puras.

Cubre la fase 1 del aparato de medida:

- el contrato del protocolo y la carga verificada de `s11-load-v1`;
- el plan determinista de 10 × 5 = 50 ítems, sin imágenes, coordenadas ni tokens;
- el generador: 10 VU tras una `threading.Barrier(10)`, lazo cerrado sin pausa, un
  único envío por ítem y 0 reintentos, sobre un transporte inyectable;
- el transporte de biblioteca estándar: una conexión TCP nueva por request, el cuerpo
  precodificado y un plazo total de 60 s desde `t_start`;
- la ventana, el throughput, P50/P99 nearest-rank, el histograma diagnóstico, el
  predicado de HT1-04 y la decisión `PASS`/`FAIL`/`NOT_DEMONSTRATED`.

No ejecuta nada por sí mismo, no escribe evidencia, no toca la base ni Celery y no
importa Django. La reconciliación con la base, los eventos reales, el drenaje, la
custodia y el lanzador pertenecen a fases posteriores y llegan aquí como datos.

Un HTTP 201 no es un éxito de EX-10. El intento queda `HTTP_ACCEPTED` y sólo pasa a
`RECONCILED_SUCCESS` con la evidencia persistida que exige G0. Sin esa evidencia, el
run no demuestra nada.

Aplica además la Decisión G0-A1 (27/09/2026, `G0_A1_DECISION_COMMIT`), addendum de G0
con las aclaraciones operativas de transporte y clasificación:

- Decisión A: un error capturado durante connect/send/receive que pertenece
  inequívocamente a una familia HTTP/red es un fallo válido del request; lo no
  atribuible es un fallo del aparato, `APPARATUS_FAILURE` (`transport_error_class`).
- Decisión B: `NOT_DEMONSTRATED` prevalece sobre `FAIL` si el run contiene cualquier
  causa que impide demostrarlo por completo (`decide`).
- Decisión C: toda respuesta HTTP final completa distinta de 201 es un fallo válido del
  request. Un `100 Continue` intermedio se consume y clasifica la respuesta final; el
  generador no envía `Expect: 100-continue`.
- Decisiones D y E: P50/P99 usan todos los 201 leídos por completo, con cuerpo
  contractual o no; un 201 truncado es un fallo de transporte y no entra
  (`latency_set`).

Los tokens, los bytes de imagen y las coordenadas sólo existen en memoria, dentro de
`PreparedRequest`. Ningún payload serializable los contiene.
"""

from __future__ import annotations

import base64
import binascii
import bisect
import contextlib
import errno
import hashlib
import http.client
import io
import ipaddress
import json
import math
import re
import socket
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from .canonical import canonical_json_bytes, sha256_bytes
from .constants import REQUEST_ID_WIRE_HEADER, RUN_ID_WIRE_HEADER, Decision, RunStatus
from .redaction import contains_secret

# --- Protocolo congelado (G0) ----------------------------------------------------------

PROTOCOL_ID = "s11-ex10-v1"
#: `s11-ex10-v1` está atado a esta versión de S11-A. No se lee de
#: `constants.METHODOLOGY_VERSION`: si la metodología cambiara, este protocolo no la
#: seguiría y haría falta uno nuevo.
METHODOLOGY_VERSION = "S11A-v1.1"
SCENARIO_ID = "EX-10"
DECISION_REFERENCE = "G0 s11-ex10-v1 (docs/PROGRESO_SPRINTS.md, 26/09/2026)"
#: Commit documental que incorpora G0; la identidad del run lo registra (G0).
DECISION_COMMIT = "1693d0ae562bfe777c496b616c9f8c8d5fa8a591"
#: Commit documental del addendum G0-A1 (27/09/2026). Forma parte de la identidad del
#: protocolo junto con el de G0: el addendum no reemplaza a G0 (G0-A1).
G0_A1_DECISION_COMMIT = "db7fe6649213a8d26f4ee60d36a797ee54f80322"

DATASET_ID = "s11-load-v1"
DATASET_SEED = 20260921
DATASET_SHA256 = "c6883b59dc861fb07fae730594c929fdd572a9ed71aee6d8b85c87f71ede0acb"
DATASET_PATH = Path(__file__).resolve().parent / "data" / f"{DATASET_ID}.json"

VIRTUAL_USERS = 10
REQUESTS_PER_USER = 5
TOTAL_REQUESTS = VIRTUAL_USERS * REQUESTS_PER_USER
WINDOW_NS = 60_000_000_000
#: El denominador del throughput es la ventana de 60 s exactos (MD-03): 1 minuto.
WINDOW_MINUTES = 1
REQUEST_TIMEOUT_NS = 60_000_000_000
GENERATOR_RETRIES = 0
PACING = "closed-loop-no-think-time"
THROTTLE_REPORT_CREATE = "5/hour"
WORKER_CONCURRENCY = 2
AI_MODEL_NAME = "s11-deterministic-fake-v1"
BROKER_URL = "redis://redis:6379/11"
DRAIN_LIMIT_SECONDS = 300

ENDPOINT_PATH = "/api/v1/reports/"
HOST_HEADER = "backend:8000"
SCENARIO_ID_WIRE_HEADER = "X-S11-Scenario-ID"

THROUGHPUT_THRESHOLD_PER_MINUTE = 50
P50_THRESHOLD_NS = 800_000_000
P99_THRESHOLD_NS = 2_000_000_000
#: Percentiles como fracción exacta: el rango se calcula en enteros, sin flotantes.
P50 = (50, 100)
P99 = (99, 100)
NOT_COMPUTABLE = "NOT_COMPUTABLE"

TASK_NAME = "ai.classify_report"
REPORT_STATUS_CREATED = "CREADO"
REPORT_STATUS_CLASSIFIED = "CLASIFICADO"
REWARD_STATUS_PENDING = "PENDIENTE"
#: `REPORT_CREATED`: +20 puntos de impacto, +20 créditos, `PENDIENTE`.
CREATED_REWARD = (20, 20, REWARD_STATUS_PENDING)
AUDIT_REPORT_CREATED = "report.created"
AUDIT_AI_CLASSIFIED = "ai.report.classified"

#: Clases de error HTTP/red que son fallos válidos del request (G0, MD-02). Decisión A
#: de G0-A1: la lista de G0 no es exhaustiva. Todo error capturado durante
#: connect/send/receive que pertenece inequívocamente a una familia HTTP/red es un
#: fallo válido; ver `transport_error_class`.
TRANSPORT_ERROR_CLASSES = (
    "timeout",
    "connection_refused",
    "connection_reset",
    "connection_aborted",
    "connection_error",
    "broken_pipe",
    "network_unreachable",
    "protocol_error",
    "incomplete_read",
)
#: `errno` del camino de red o del destino que llegan como `OSError` genérico. Python ya
#: convierte ECONNREFUSED, ECONNRESET, ECONNABORTED, EPIPE y ETIMEDOUT en subclases.
_NETWORK_ERRNO_CLASSES = {
    code: error_class
    for name, error_class in (
        ("EHOSTUNREACH", "network_unreachable"),
        ("ENETUNREACH", "network_unreachable"),
        ("EHOSTDOWN", "network_unreachable"),
        ("ENETDOWN", "network_unreachable"),
        ("ENETRESET", "connection_reset"),
    )
    if (code := getattr(errno, name, None)) is not None
}
#: Error que no puede atribuirse inequívocamente a HTTP/red: recursos locales
#: (`EMFILE`, `ENOBUFS`, `EADDRNOTAVAIL`…), resolución de nombres, errores de
#: programación o una clase desconocida. Es un fallo del aparato (Decisión A).
UNATTRIBUTABLE_ERROR = "unattributable_error"
#: Familia de un status HTTP distinto de 201. Todas son fallos válidos del request.
HTTP_STATUS_CLASSES = {
    1: "http_1xx",
    2: "http_2xx",
    3: "http_3xx",
    4: "http_4xx",
    5: "http_5xx",
}
_PHASES = frozenset({"connect", "send", "receive"})

#: Límites inferiores de los intervalos [a, b) del histograma diagnóstico (TD-04), en
#: ms. El último intervalo es [60 000, ∞).
HISTOGRAM_BOUNDS_MS = (*range(0, 2000, 100), 2000, 5000, 10000, 60000)

BARRIER_TIMEOUT_SECONDS = 30.0
#: Peor caso de un VU: 5 requests que agotan su plazo de 60 s, más margen.
JOIN_TIMEOUT_SECONDS = REQUESTS_PER_USER * REQUEST_TIMEOUT_NS / 1e9 + 60.0

PLAN_SCHEMA_VERSION = "s11-ex10-load-plan-v1"
RAW_SCHEMA_VERSION = "s11-ex10-load-raw-v1"
CALCULATION_SCHEMA_VERSION = "s11-ex10-ht1-calculation-v1"
DECISION_SCHEMA_VERSION = "s11-ex10-decision-v1"
#: SHA-256 canónico del plan que se deriva de `s11-load-v1` bajo `s11-ex10-v1`.
PLAN_SHA256 = "2b50aa48cfa975029ab38a4c0e17430f67469b185fce5aaed89414728e6934d7"

_EXPECTED_LOAD_SHAPE = {
    "virtual_users": VIRTUAL_USERS,
    "requests_per_user": REQUESTS_PER_USER,
    "total_requests": TOTAL_REQUESTS,
    "window_seconds": WINDOW_NS // 1_000_000_000,
    "worker_concurrency": WORKER_CONCURRENCY,
}
_REQUEST_FIELDS = frozenset(
    {
        "request_key",
        "user_alias",
        "sequence_for_user",
        "category_slug",
        "institution_slug",
        "priority",
        "description",
        "latitude",
        "longitude",
        "image",
    }
)
_IMAGE_FIELDS = frozenset({"filename", "mime_type", "sha256", "base64"})
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Copias de `runtime._RUN_ID` y `runtime._REQUEST_ID`: `runtime` importa Django y este
# módulo no. Una prueba compara ambos textos para que no diverjan.
_RUN_ID_FORMAT = re.compile(
    r"^s11-[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_REQUEST_ID_FORMAT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_FORMAT = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_FORMAT = re.compile(r"^[A-Za-z0-9._~+/=-]{16,4096}$")
#: Único detalle de error que se conserva: `TipoDeExcepción` o `TipoDeExcepción:ERRNO`.
#: Sin puntos, espacios ni `=`, para que nunca quepa un JWT, una URL o un mensaje.
_DETAIL_FORMAT = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}(?::[A-Z][A-Z0-9_]{0,31})?$")
_JSON_KEY_FORMAT = re.compile(r"^[a-z_]{1,40}$")

Clock = Callable[[], int]


class Ex10Error(RuntimeError):
    """El aparato de EX-10 no puede continuar. Nunca contiene tokens ni imágenes."""


class _Ex10Rejection(Ex10Error):
    label = "EX-10"

    def __init__(self, reasons: Iterable[str]):
        self.reasons = tuple(reasons)
        super().__init__(f"{self.label} rechazado: {list(self.reasons)}")


class Ex10DatasetError(_Ex10Rejection):
    label = DATASET_ID


class Ex10PlanError(_Ex10Rejection):
    label = "Plan EX-10"


class Ex10PreparationError(_Ex10Rejection):
    label = "Preparación EX-10"


class Ex10ApparatusError(Ex10Error):
    """Violación de la máquina de estados del generador: un fallo del aparato."""


def protocol_identity() -> dict[str, Any]:
    """Identidad completa de `s11-ex10-v1`. El ejecutor rechaza cualquier otra."""
    return {
        "protocol_id": PROTOCOL_ID,
        "methodology_version": METHODOLOGY_VERSION,
        "scenario_id": SCENARIO_ID,
        "decision_reference": DECISION_REFERENCE,
        "decision_commit": DECISION_COMMIT,
        "decision_addendum_commit": G0_A1_DECISION_COMMIT,
        "dataset": {"id": DATASET_ID, "seed": DATASET_SEED, "sha256": DATASET_SHA256},
        "load": {
            "virtual_users": VIRTUAL_USERS,
            "requests_per_user": REQUESTS_PER_USER,
            "total_requests": TOTAL_REQUESTS,
            "barrier_parties": VIRTUAL_USERS,
            "window_ns": WINDOW_NS,
            "pacing": PACING,
            "retries": GENERATOR_RETRIES,
        },
        "request": {
            "method": "POST",
            "endpoint": ENDPOINT_PATH,
            "host_header": HOST_HEADER,
            "timeout_ns": REQUEST_TIMEOUT_NS,
            "connection": "close",
            "new_tcp_connection_per_request": True,
        },
        "system_under_test": {
            "throttle_report_create": THROTTLE_REPORT_CREATE,
            "worker_concurrency": WORKER_CONCURRENCY,
            "ai_model_name": AI_MODEL_NAME,
            "broker_url": BROKER_URL,
        },
        "thresholds": {
            "ht1_01_min_per_minute": THROUGHPUT_THRESHOLD_PER_MINUTE,
            "ht1_02_p50_max_ns": P50_THRESHOLD_NS,
            "ht1_03_p99_max_ns": P99_THRESHOLD_NS,
            "percentile_method": "nearest-rank",
            "percentile_set": "all-http-201",
        },
        "drain_limit_seconds": DRAIN_LIMIT_SECONDS,
    }


def verify_protocol_identity(candidate: Mapping[str, Any]) -> list[str]:
    expected = protocol_identity()
    # Un campo ausente y un campo extra difieren aunque el otro lado valga `None`.
    missing = object()
    return [
        f"protocol-identity:{key}"
        for key in sorted(set(expected) | set(candidate))
        if candidate.get(key, missing) != expected.get(key, missing)
    ]


# --- Dataset ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LoadDataset:
    """`s11-load-v1` verificado. Contiene imágenes y coordenadas: sólo en memoria."""

    sha256: str
    payload: Mapping[str, Any] = field(repr=False)

    def request_rows(self) -> dict[str, Mapping[str, Any]]:
        return {row["request_key"]: row for row in self.payload["requests"]}


def load_dataset(path: Path = DATASET_PATH) -> LoadDataset:
    return parse_dataset(path.read_bytes())


def parse_dataset(raw: bytes) -> LoadDataset:
    """Verifica los bytes exactos de `s11-load-v1` y su estructura."""
    digest = sha256_bytes(raw)
    reasons = [] if digest == DATASET_SHA256 else [f"dataset-sha256:{digest}"]
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise Ex10DatasetError([*reasons, "dataset-json"]) from exc
    if not isinstance(payload, dict):
        raise Ex10DatasetError([*reasons, "dataset-json"])
    try:
        canonical = canonical_json_bytes(payload) == raw
    except (TypeError, ValueError):
        canonical = False
    if not canonical:
        reasons.append("dataset-not-canonical")
    reasons.extend(validate_dataset_payload(payload))
    if reasons:
        raise Ex10DatasetError(reasons)
    return LoadDataset(digest, payload)


def validate_dataset_payload(payload: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    for key, expected in (
        ("dataset_id", DATASET_ID),
        ("seed", DATASET_SEED),
        ("schema_version", "s11-synthetic-dataset-v1"),
        ("synthetic", True),
        ("load_shape", _EXPECTED_LOAD_SHAPE),
        ("scenario_coverage", [SCENARIO_ID]),
    ):
        if payload.get(key) != expected:
            reasons.append(f"dataset-field:{key}")
    users = payload.get("users")
    rows = payload.get("requests")
    if not isinstance(users, list) or not all(isinstance(user, dict) for user in users):
        return [*reasons, "dataset-users"]
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return [*reasons, "dataset-requests"]

    if len(users) != VIRTUAL_USERS:
        reasons.append(f"dataset-users:{len(users)}")
    aliases = [user.get("alias") for user in users]
    if len(set(aliases)) != len(aliases) or not all(isinstance(a, str) and a for a in aliases):
        reasons.append("dataset-user-aliases")
    user_ids = [user.get("id") for user in users]
    if len(set(user_ids)) != len(user_ids) or not all(_is_uuid(value) for value in user_ids):
        reasons.append("dataset-user-ids")
    for user in users:
        if user.get("role") != "citizen" or user.get("verified") is not True:
            reasons.append(f"dataset-user:{user.get('alias')}")

    if len(rows) != TOTAL_REQUESTS:
        reasons.append(f"dataset-requests:{len(rows)}")
    keys = [row.get("request_key") for row in rows]
    if len(set(keys)) != len(keys) or not all(isinstance(key, str) and key for key in keys):
        reasons.append("dataset-request-keys")
    sequences: dict[str, list[Any]] = {alias: [] for alias in aliases if isinstance(alias, str)}
    image_hashes: list[str] = []
    for index, row in enumerate(rows):
        label = row.get("request_key") or f"#{index}"
        if set(row) != _REQUEST_FIELDS:
            reasons.append(f"dataset-request-fields:{label}")
            continue
        if row["user_alias"] not in sequences:
            reasons.append(f"dataset-request-user:{label}")
        else:
            sequences[row["user_alias"]].append(row["sequence_for_user"])
        if not all(
            isinstance(row[name], str) and row[name] for name in ("category_slug", "description")
        ):
            reasons.append(f"dataset-request-text:{label}")
        if not (_finite(row["latitude"], 90) and _finite(row["longitude"], 180)):
            reasons.append(f"dataset-request-location:{label}")
        image_hash = _verified_image_hash(row["image"])
        if image_hash is None:
            reasons.append(f"dataset-request-image:{label}")
        else:
            image_hashes.append(image_hash)
    for alias, values in sequences.items():
        if values != list(range(1, REQUESTS_PER_USER + 1)):
            reasons.append(f"dataset-user-sequence:{alias}")
    if len(set(image_hashes)) != len(image_hashes):
        reasons.append("dataset-image-duplicate")
    return reasons


def _verified_image_hash(image: Any) -> str | None:
    if not isinstance(image, dict) or set(image) != _IMAGE_FIELDS:
        return None
    if image["mime_type"] != "image/png" or not isinstance(image["filename"], str):
        return None
    if not _is_safe_filename(image["filename"]):
        return None
    try:
        content = base64.b64decode(image["base64"], validate=True)
    except (binascii.Error, TypeError, ValueError):
        return None
    if not content.startswith(_PNG_SIGNATURE) or sha256_bytes(content) != image["sha256"]:
        return None
    return image["sha256"]


def _is_safe_filename(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9._-]{1,100}", value) is not None


def _finite(value: Any, bound: float) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and -bound <= value <= bound
    )


def _is_uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


# --- Plan de 50 ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanItem:
    """Ítem del plan: sin bytes de imagen, sin coordenadas y sin tokens."""

    index: int
    request_key: str
    request_id: str
    vu: int
    user_alias: str
    dataset_user_id: str
    sequence_for_user: int
    category_slug: str
    image_sha256: str

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "request_key": self.request_key,
            "request_id": self.request_id,
            "vu": self.vu,
            "user_alias": self.user_alias,
            "dataset_user_id": self.dataset_user_id,
            "sequence_for_user": self.sequence_for_user,
            "category_slug": self.category_slug,
            "image_sha256": self.image_sha256,
        }


@dataclass(frozen=True, slots=True)
class LoadPlan:
    dataset_sha256: str
    items: tuple[PlanItem, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": PLAN_SCHEMA_VERSION,
            "protocol_id": PROTOCOL_ID,
            "methodology_version": METHODOLOGY_VERSION,
            "scenario_id": SCENARIO_ID,
            "dataset_id": DATASET_ID,
            "dataset_sha256": self.dataset_sha256,
            "virtual_users": VIRTUAL_USERS,
            "requests_per_user": REQUESTS_PER_USER,
            "total_requests": TOTAL_REQUESTS,
            "items": [item.to_json() for item in self.items],
        }

    @property
    def sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.to_json()))

    @property
    def vus(self) -> tuple[int, ...]:
        return tuple(sorted({item.vu for item in self.items}))

    def for_vu(self, vu: int) -> tuple[PlanItem, ...]:
        return tuple(item for item in self.items if item.vu == vu)

    def by_key(self) -> dict[str, PlanItem]:
        return {item.request_key: item for item in self.items}


def request_id_for(request_key: str) -> str:
    """`X-S11-Request-ID` determinista por `request_key` (G0, generador)."""
    return f"{PROTOCOL_ID}:{request_key}"


def build_plan(dataset: LoadDataset) -> LoadPlan:
    """Plan 10 × 5: VU `n` es el usuario `n` del dataset, con sus 5 requests en orden."""
    users = dataset.payload["users"]
    vu_by_alias = {user["alias"]: number for number, user in enumerate(users, start=1)}
    id_by_alias = {user["alias"]: user["id"] for user in users}
    ordered = sorted(
        dataset.payload["requests"],
        key=lambda row: (vu_by_alias[row["user_alias"]], row["sequence_for_user"]),
    )
    plan = LoadPlan(
        dataset_sha256=dataset.sha256,
        items=tuple(
            PlanItem(
                index=index,
                request_key=row["request_key"],
                request_id=request_id_for(row["request_key"]),
                vu=vu_by_alias[row["user_alias"]],
                user_alias=row["user_alias"],
                dataset_user_id=id_by_alias[row["user_alias"]],
                sequence_for_user=row["sequence_for_user"],
                category_slug=row["category_slug"],
                image_sha256=row["image"]["sha256"],
            )
            for index, row in enumerate(ordered, start=1)
        ),
    )
    reasons = validate_plan(plan)
    if reasons:
        raise Ex10PlanError(reasons)
    return plan


def validate_plan(plan: LoadPlan) -> list[str]:
    reasons: list[str] = []
    items = plan.items
    if plan.dataset_sha256 != DATASET_SHA256:
        reasons.append("plan-dataset-sha256")
    if len(items) != TOTAL_REQUESTS:
        reasons.append(f"plan-size:{len(items)}")
    if [item.index for item in items] != list(range(1, len(items) + 1)):
        reasons.append("plan-index")
    if len({item.request_key for item in items}) != len(items):
        reasons.append("plan-request-key-duplicate")
    if len({item.request_id for item in items}) != len(items):
        reasons.append("plan-request-id-duplicate")
    for item in items:
        if item.request_id != request_id_for(item.request_key) or not _REQUEST_ID_FORMAT.fullmatch(
            item.request_id
        ):
            reasons.append(f"plan-request-id:{item.request_key}")
        if not _SHA256_FORMAT.fullmatch(item.image_sha256):
            reasons.append(f"plan-image-sha256:{item.request_key}")
    if plan.vus != tuple(range(1, VIRTUAL_USERS + 1)):
        reasons.append("plan-vus")
    aliases_by_vu = {vu: {item.user_alias for item in plan.for_vu(vu)} for vu in plan.vus}
    for vu, aliases in aliases_by_vu.items():
        sequences = [item.sequence_for_user for item in plan.for_vu(vu)]
        if sequences != list(range(1, REQUESTS_PER_USER + 1)):
            reasons.append(f"plan-vu-sequence:{vu}")
        if len(aliases) != 1:
            reasons.append(f"plan-vu-user:{vu}")
    all_aliases = [alias for aliases in aliases_by_vu.values() for alias in aliases]
    if len(set(all_aliases)) != len(all_aliases):
        reasons.append("plan-user-shared-between-vus")
    if list(items) != sorted(items, key=lambda item: (item.vu, item.sequence_for_user)):
        reasons.append("plan-order")
    return reasons


# --- Requests precodificados -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    """Request completo, precodificado antes de la barrera.

    `wire` contiene el token, la imagen y las coordenadas. Sólo vive en memoria: no
    tiene forma serializable, no se puede copiar con `pickle` y su `repr` lo omite.
    """

    item: PlanItem
    wire: bytes = field(repr=False)
    body_length: int
    boundary: str = field(repr=False)

    def __reduce__(self):
        raise TypeError("PreparedRequest no se serializa: contiene el token y la imagen.")


def boundary_for(request_key: str) -> str:
    digest = hashlib.sha256(f"{PROTOCOL_ID}:boundary:{request_key}".encode()).hexdigest()
    return f"s11ex10{digest[:32]}"


def prepare_requests(
    dataset: LoadDataset,
    plan: LoadPlan,
    *,
    run_id: str,
    tokens: Mapping[str, str],
    category_ids: Mapping[str, str],
) -> dict[str, PreparedRequest]:
    """Precodifica los 50 requests: cabeceras, multipart, boundary y `Content-Length`.

    `tokens` va por `user_alias` y `category_ids` por `category_slug`. Los errores
    nombran aliases y slugs, nunca valores de token. `Host` es el de G0 y no se
    configura.
    """
    reasons: list[str] = []
    if plan.to_json() != build_plan(dataset).to_json():
        reasons.append("plan-dataset-mismatch")
    if not _RUN_ID_FORMAT.fullmatch(run_id):
        reasons.append("run-id")
    aliases = {item.user_alias for item in plan.items}
    if set(tokens) != aliases:
        reasons.append("tokens:aliases")
    for alias in sorted(aliases & set(tokens)):
        token = tokens[alias]
        if not isinstance(token, str) or not _TOKEN_FORMAT.fullmatch(token):
            reasons.append(f"token-format:{alias}")
    slugs = {item.category_slug for item in plan.items}
    for slug in sorted(slugs - set(category_ids)):
        reasons.append(f"category-id-missing:{slug}")
    for slug in sorted(slugs & set(category_ids)):
        if not _is_uuid(category_ids[slug]):
            reasons.append(f"category-id-format:{slug}")
    if reasons:
        raise Ex10PreparationError(reasons)

    rows = dataset.request_rows()
    prepared: dict[str, PreparedRequest] = {}
    for item in plan.items:
        boundary = boundary_for(item.request_key)
        body = _report_body(
            rows[item.request_key], category_id=category_ids[item.category_slug], boundary=boundary
        )
        head = _request_head(
            token=tokens[item.user_alias],
            run_id=run_id,
            request_id=item.request_id,
            boundary=boundary,
            content_length=len(body),
        )
        prepared[item.request_key] = PreparedRequest(
            item=item, wire=head + body, body_length=len(body), boundary=boundary
        )
    return prepared


def _report_body(row: Mapping[str, Any], *, category_id: str, boundary: str) -> bytes:
    image = row["image"]
    content = base64.b64decode(image["base64"], validate=True)
    parts = [
        _text_part(boundary, "category", category_id),
        _text_part(boundary, "description", row["description"]),
        _text_part(boundary, "lat", repr(float(row["latitude"]))),
        _text_part(boundary, "lng", repr(float(row["longitude"]))),
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="{image["filename"]}"\r\n'
            f"Content-Type: {image['mime_type']}\r\n\r\n"
        ).encode("ascii")
        + content
        + b"\r\n",
        f"--{boundary}--\r\n".encode("ascii"),
    ]
    body = b"".join(parts)
    # 5 partes y el cierre: cualquier otra aparición del delimitador es una colisión.
    if body.count(f"--{boundary}".encode("ascii")) != len(parts):
        raise Ex10PreparationError([f"multipart-boundary-collision:{row['request_key']}"])
    return body


def _text_part(boundary: str, name: str, value: str) -> bytes:
    return (
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii")
        + value.encode("utf-8")
        + b"\r\n"
    )


def _request_head(
    *,
    token: str,
    run_id: str,
    request_id: str,
    boundary: str,
    content_length: int,
) -> bytes:
    lines = (
        f"POST {ENDPOINT_PATH} HTTP/1.1",
        f"Host: {HOST_HEADER}",
        f"Authorization: Bearer {token}",
        f"{RUN_ID_WIRE_HEADER}: {run_id}",
        f"{SCENARIO_ID_WIRE_HEADER}: {SCENARIO_ID}",
        f"{REQUEST_ID_WIRE_HEADER}: {request_id}",
        f"Content-Type: multipart/form-data; boundary={boundary}",
        f"Content-Length: {content_length}",
        "Connection: close",
    )
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


# --- Transporte ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Exchange:
    """Lo que devuelve un transporte por un envío: tiempos y respuesta o error.

    `body` sólo vive en memoria; el registro del intento guarda una forma mínima.
    """

    t_start_ns: int
    t_end_ns: int
    t_start_utc_ns: int
    t_end_utc_ns: int
    http_status: int | None = None
    body: bytes = field(default=b"", repr=False)
    error_class: str | None = None
    error_detail: str | None = None
    #: `connect`, `send` o `receive`: la operación de transporte en la que ocurrió.
    error_phase: str | None = None


class Transport(Protocol):
    def send(self, request: PreparedRequest, *, clock: Clock, wall_clock: Clock) -> Exchange:
        """Un único envío. Devuelve un `Exchange` para los errores HTTP/red capturados y
        deja propagarse cualquier otra excepción, que es un fallo del aparato."""


def transport_error_class(exc: BaseException) -> str | None:
    """Clase de un error HTTP/red capturado durante connect/send/receive (Decisión A).

    Se clasifica por familia de excepción, no por una lista cerrada:
    `http.client.HTTPException`, `ConnectionError`, `TimeoutError` y los `OSError`
    genéricos con un `errno` de red. Devuelve `None` si el error no es atribuible
    inequívocamente a HTTP/red; entonces es un fallo del aparato, nunca un `FAIL`.
    """
    if isinstance(exc, http.client.IncompleteRead):
        return "incomplete_read"
    # `RemoteDisconnected` es a la vez `ConnectionResetError` y `BadStatusLine`: el
    # servidor cerró sin responder, así que cuenta como reset.
    if isinstance(exc, ConnectionResetError):
        return "connection_reset"
    if isinstance(exc, http.client.HTTPException):
        return "protocol_error"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(exc, ConnectionAbortedError):
        return "connection_aborted"
    if isinstance(exc, BrokenPipeError):
        return "broken_pipe"
    if isinstance(exc, ConnectionError):
        return "connection_error"
    # Sólo el `OSError` genérico: sus subclases (`gaierror`, `PermissionError`…) ya
    # dicen de qué se trata y ninguna es inequívocamente de red.
    if type(exc) is OSError:
        return _NETWORK_ERRNO_CLASSES.get(exc.errno)
    return None


class StdlibHttpTransport:
    """Transporte de biblioteca estándar (TD-01 y TD-03).

    Cada envío abre una conexión TCP nueva contra la IP resuelta antes de la barrera,
    escribe el request precodificado, lee la respuesta con `http.client` y cierra el
    socket. El plazo es total, `REQUEST_TIMEOUT_NS` desde `t_start`, y cada operación
    del socket recibe sólo el tiempo restante. No hay pool, keep-alive ni reintento.
    """

    def __init__(self, address: tuple[str, int], *, connect: Callable[..., Any] | None = None):
        # Una IP literal: resolver un nombre caería dentro de `t_start..t_end` (G0).
        host, port = address
        try:
            ipaddress.ip_address(host)
        except ValueError as exc:
            raise Ex10PreparationError(["transport-address-not-ip"]) from exc
        if type(port) is not int or not 0 < port < 65536:
            raise Ex10PreparationError(["transport-port"])
        self._address = (host, port)
        # Se resuelve en cada envío para que las pruebas puedan bloquear el módulo.
        self._connect = connect

    def send(self, request: PreparedRequest, *, clock: Clock, wall_clock: Clock) -> Exchange:
        connect = self._connect or socket.create_connection
        sock = None
        status = None
        phase = "connect"
        t_start_utc = wall_clock()
        t_start = clock()
        deadline = t_start + REQUEST_TIMEOUT_NS
        try:
            sock = connect(self._address, timeout=_remaining_seconds(deadline, clock))
            phase = "send"
            _arm(sock, deadline, clock)
            sock.sendall(request.wire)
            phase = "receive"
            response = http.client.HTTPResponse(
                _DeadlineSocket(sock, deadline, clock), method="POST"
            )
            response.begin()
            status = response.status
            body = response.read()
            t_end = clock()
            return Exchange(
                t_start, t_end, t_start_utc, wall_clock(), http_status=status, body=body
            )
        except Exception as exc:
            error_class = transport_error_class(exc)
            if error_class is None and not isinstance(exc, OSError):
                # Ni HTTP ni red: excepción interna, fallo del aparato.
                raise
            t_end = clock()
            # Un `OSError` no atribuible conserva tiempos y `errno` para el diagnóstico,
            # pero es un fallo del aparato. Si la línea de estado llegó, se conserva:
            # el request sigue fallando porque el cuerpo no terminó.
            return Exchange(
                t_start,
                t_end,
                t_start_utc,
                wall_clock(),
                http_status=status,
                error_class=error_class or UNATTRIBUTABLE_ERROR,
                error_detail=_error_detail(exc),
                error_phase=phase,
            )
        finally:
            if sock is not None:
                with contextlib.suppress(OSError):
                    sock.close()


def _remaining_seconds(deadline_ns: int, clock: Clock) -> float:
    remaining = deadline_ns - clock()
    if remaining <= 0:
        raise TimeoutError("deadline")
    return remaining / 1e9


def _arm(sock: Any, deadline_ns: int, clock: Clock) -> None:
    sock.settimeout(_remaining_seconds(deadline_ns, clock))


class _DeadlineSocket:
    """`http.client.HTTPResponse` sólo usa `makefile("rb")` del socket."""

    def __init__(self, sock: Any, deadline_ns: int, clock: Clock):
        self._sock = sock
        self._deadline_ns = deadline_ns
        self._clock = clock

    def makefile(self, mode: str = "rb", *args: Any, **kwargs: Any) -> io.BufferedReader:
        return io.BufferedReader(_DeadlineReader(self._sock, self._deadline_ns, self._clock))


class _DeadlineReader(io.RawIOBase):
    """Cada lectura arma el socket con el tiempo que queda hasta el plazo total."""

    def __init__(self, sock: Any, deadline_ns: int, clock: Clock):
        super().__init__()
        self._sock = sock
        self._deadline_ns = deadline_ns
        self._clock = clock

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        _arm(self._sock, self._deadline_ns, self._clock)
        return self._sock.recv_into(buffer)


def _error_detail(exc: BaseException) -> str:
    # Nunca `str(exc)`: puede traer direcciones o la línea de estado del servidor.
    code = getattr(exc, "errno", None)
    name = errno.errorcode.get(code) if isinstance(code, int) else None
    return f"{type(exc).__name__}:{name}" if name else type(exc).__name__


# --- Registro de intentos --------------------------------------------------------------


class ItemState(StrEnum):
    PREPARED = "PREPARED"
    IN_FLIGHT = "IN_FLIGHT"
    COMPLETED = "COMPLETED"
    APPARATUS_FAILURE = "APPARATUS_FAILURE"
    NOT_SENT = "NOT_SENT"


class AttemptOutcome(StrEnum):
    #: HTTP 201 con el cuerpo contractual. Todavía no es un éxito de EX-10.
    HTTP_ACCEPTED = "HTTP_ACCEPTED"
    #: HTTP 201 con un cuerpo distinto del contrato: fallo válido del request.
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
    #: Cualquier otro status HTTP (4xx, 5xx…): fallo válido del request.
    HTTP_REJECTED = "HTTP_REJECTED"
    #: Error de transporte admitido por MD-02: fallo válido del request.
    TRANSPORT_FAILED = "TRANSPORT_FAILED"
    #: Fallo del generador o del aparato: invalida el run.
    APPARATUS_FAILURE = "APPARATUS_FAILURE"
    #: Ítem nunca enviado: pérdida de resultados, invalida el run.
    NOT_SENT = "NOT_SENT"


class FaultKind(StrEnum):
    BARRIER_BROKEN = "barrier_broken"
    THREAD_DIED = "thread_died"
    THREAD_HUNG = "thread_hung"
    INTERNAL_EXCEPTION = "internal_exception"
    RESULT_LOST = "result_lost"
    OPERATOR_INTERRUPTED = "operator_interrupted"
    TIMING_INCONSISTENT = "timing_inconsistent"
    UNATTRIBUTABLE_ERROR = "unattributable_error"


@dataclass(frozen=True, slots=True)
class ApparatusFault:
    kind: FaultKind
    vu: int | None = None
    request_key: str | None = None
    detail: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "vu": self.vu,
            "request_key": self.request_key,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    request_key: str
    request_id: str
    vu: int
    user_alias: str
    sequence_for_user: int
    state: ItemState
    send_count: int
    outcome: AttemptOutcome
    t_start_ns: int | None = None
    t_end_ns: int | None = None
    t_start_utc_ns: int | None = None
    t_end_utc_ns: int | None = None
    started_in_window: bool | None = None
    completed_in_window: bool | None = None
    duration_ns: int | None = None
    http_status: int | None = None
    minimal_response: Mapping[str, Any] | None = None
    error_class: str | None = None
    error_detail_sanitized: str | None = None
    error_phase: str | None = None

    @property
    def report_id(self) -> str | None:
        if self.outcome is not AttemptOutcome.HTTP_ACCEPTED or self.minimal_response is None:
            return None
        return self.minimal_response["id"]

    def to_json(self) -> dict[str, Any]:
        return {
            "request_key": self.request_key,
            "request_id": self.request_id,
            "vu": self.vu,
            "user_alias": self.user_alias,
            "sequence_for_user": self.sequence_for_user,
            "state": str(self.state),
            "send_count": self.send_count,
            "outcome": str(self.outcome),
            "t_start_ns": self.t_start_ns,
            "t_end_ns": self.t_end_ns,
            "t_start_utc_ns": self.t_start_utc_ns,
            "t_end_utc_ns": self.t_end_utc_ns,
            "started_in_window": self.started_in_window,
            "completed_in_window": self.completed_in_window,
            "duration_ns": self.duration_ns,
            "http_status": self.http_status,
            "minimal_response": (
                None if self.minimal_response is None else dict(self.minimal_response)
            ),
            "error_class": self.error_class,
            "error_detail_sanitized": self.error_detail_sanitized,
            "error_phase": self.error_phase,
        }


def window_flags(t0_ns: int, t_start_ns: int, t_end_ns: int) -> tuple[bool, bool]:
    """`(started_in_window, completed_in_window)` con `window_end = t0 + 60 s`.

    Empieza en la ventana si `t_start < window_end` y termina en ella si
    `t_end ≤ window_end` (G0, MD-01 y throughput).
    """
    window_end = t0_ns + WINDOW_NS
    return t_start_ns < window_end, t_end_ns <= window_end


def contract_body(body: bytes) -> dict[str, Any] | None:
    """El cuerpo 201 contractual de G0, o `None` si no lo es exactamente."""
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(value, dict) or set(value) != {"id", "status", "reward"}:
        return None
    reward = value["reward"]
    if (
        not _is_uuid(value["id"])
        or value["status"] != REPORT_STATUS_CREATED
        or not isinstance(reward, dict)
        or set(reward) != {"impact_points", "credits", "status"}
        or (reward["impact_points"], reward["credits"], reward["status"]) != CREATED_REWARD
        or type(reward["impact_points"]) is not int
        or type(reward["credits"]) is not int
    ):
        return None
    impact, credits, status = CREATED_REWARD
    return {
        "id": value["id"],
        "status": REPORT_STATUS_CREATED,
        "reward": {"impact_points": impact, "credits": credits, "status": status},
    }


def _body_digest(body: bytes) -> dict[str, Any]:
    keys = None
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        value = None
    if isinstance(value, dict):
        keys = sorted(key for key in value if _JSON_KEY_FORMAT.fullmatch(key))
    return {"body_sha256": sha256_bytes(body), "body_bytes": len(body), "json_keys": keys}


def _sanitize_detail(value: str | None) -> str | None:
    if value is None:
        return None
    return value if _DETAIL_FORMAT.fullmatch(value) else "[REDACTED]"


def attempt_record(item: PlanItem, exchange: Exchange, *, t0_ns: int) -> AttemptRecord:
    """Clasifica un envío completado. Función pura: no consulta la base."""
    started, completed = window_flags(t0_ns, exchange.t_start_ns, exchange.t_end_ns)
    base = {
        "request_key": item.request_key,
        "request_id": item.request_id,
        "vu": item.vu,
        "user_alias": item.user_alias,
        "sequence_for_user": item.sequence_for_user,
        "state": ItemState.COMPLETED,
        "send_count": 1,
        "t_start_ns": exchange.t_start_ns,
        "t_end_ns": exchange.t_end_ns,
        "t_start_utc_ns": exchange.t_start_utc_ns,
        "t_end_utc_ns": exchange.t_end_utc_ns,
        "started_in_window": started,
        "completed_in_window": completed,
        "duration_ns": exchange.t_end_ns - exchange.t_start_ns,
        "http_status": exchange.http_status,
    }
    if not t0_ns <= exchange.t_start_ns <= exchange.t_end_ns:
        return AttemptRecord(
            **base,
            outcome=AttemptOutcome.APPARATUS_FAILURE,
            error_class=str(FaultKind.TIMING_INCONSISTENT),
        )
    if exchange.error_class is None and exchange.http_status is None:
        return AttemptRecord(
            **base,
            outcome=AttemptOutcome.APPARATUS_FAILURE,
            error_class="exchange_inconsistent",
        )
    if exchange.error_class is not None:
        # Un status junto al error es sólo diagnóstico: el cuerpo no terminó de llegar.
        admitted = exchange.error_class in TRANSPORT_ERROR_CLASSES
        outcome = AttemptOutcome.TRANSPORT_FAILED if admitted else AttemptOutcome.APPARATUS_FAILURE
        return AttemptRecord(
            **base,
            outcome=outcome,
            error_class=exchange.error_class if admitted else UNATTRIBUTABLE_ERROR,
            error_detail_sanitized=_sanitize_detail(exchange.error_detail),
            error_phase=exchange.error_phase if exchange.error_phase in _PHASES else None,
        )
    status = exchange.http_status
    if status == 201:
        body = contract_body(exchange.body)
        if body is not None:
            return AttemptRecord(
                **base, outcome=AttemptOutcome.HTTP_ACCEPTED, minimal_response=body
            )
        return AttemptRecord(
            **base,
            outcome=AttemptOutcome.CONTRACT_VIOLATION,
            minimal_response=_body_digest(exchange.body),
            error_class="contract_violation",
            error_detail_sanitized="http_status=201",
        )
    # Cualquier respuesta HTTP final completa distinta de 201 es un fallo válido del
    # request (Decisión C de G0-A1): 1xx final, 2xx ≠ 201, 3xx, 4xx, 5xx y no estándar.
    error_class = HTTP_STATUS_CLASSES.get(status // 100, "http_nonstandard_status")
    return AttemptRecord(
        **base,
        outcome=AttemptOutcome.HTTP_REJECTED,
        minimal_response=_body_digest(exchange.body),
        error_class=error_class,
        error_detail_sanitized=f"http_status={status}",
    )


def _unfinished_record(
    item: PlanItem,
    *,
    state: ItemState,
    send_count: int,
    error_class: str | None = None,
    detail: str | None = None,
) -> AttemptRecord:
    unsent = state is ItemState.NOT_SENT
    return AttemptRecord(
        request_key=item.request_key,
        request_id=item.request_id,
        vu=item.vu,
        user_alias=item.user_alias,
        sequence_for_user=item.sequence_for_user,
        state=state,
        send_count=send_count,
        outcome=AttemptOutcome.NOT_SENT if unsent else AttemptOutcome.APPARATUS_FAILURE,
        error_class=error_class,
        error_detail_sanitized=_sanitize_detail(detail),
    )


# --- Generador -------------------------------------------------------------------------


class _ItemLedger:
    """Máquina de estados por ítem: `PREPARED → IN_FLIGHT → COMPLETED|APPARATUS_FAILURE`.

    Un ítem sólo sale de `PREPARED` una vez: es la garantía estructural de un único
    envío por ítem y de 0 reintentos.
    """

    def __init__(self, plan: LoadPlan):
        self._lock = threading.Lock()
        self._states = {item.request_key: ItemState.PREPARED for item in plan.items}
        self._sends = {item.request_key: 0 for item in plan.items}
        self._records: dict[str, AttemptRecord] = {}

    def begin(self, request_key: str) -> None:
        with self._lock:
            state = self._states.get(request_key)
            if state is not ItemState.PREPARED:
                raise Ex10ApparatusError(f"Envío no permitido para {request_key} en {state}.")
            self._states[request_key] = ItemState.IN_FLIGHT
            self._sends[request_key] += 1

    def finish(self, request_key: str, record: AttemptRecord) -> None:
        with self._lock:
            if self._states.get(request_key) is not ItemState.IN_FLIGHT:
                raise Ex10ApparatusError(f"Cierre no permitido para {request_key}.")
            self._states[request_key] = record.state
            self._records[request_key] = record

    def close(self) -> dict[str, tuple[ItemState, int, AttemptRecord | None]]:
        """Congela el ledger: lo no enviado queda `NOT_SENT` y lo que seguía en vuelo,
        `APPARATUS_FAILURE` (resultado perdido)."""
        with self._lock:
            final: dict[str, tuple[ItemState, int, AttemptRecord | None]] = {}
            for key, state in self._states.items():
                if state is ItemState.PREPARED:
                    state = ItemState.NOT_SENT
                elif state is ItemState.IN_FLIGHT:
                    state = ItemState.APPARATUS_FAILURE
                self._states[key] = state
                final[key] = (state, self._sends[key], self._records.get(key))
            return final


@dataclass(frozen=True, slots=True)
class GeneratorRun:
    plan_sha256: str
    t0_ns: int | None
    t0_utc_ns: int | None
    attempts: tuple[AttemptRecord, ...]
    faults: tuple[ApparatusFault, ...] = ()
    interrupted: bool = False

    @property
    def window_end_ns(self) -> int | None:
        return None if self.t0_ns is None else self.t0_ns + WINDOW_NS


class LoadGenerator:
    """10 VU, uno por usuario, con sus 5 requests en lazo cerrado sin pausa (MD-10 A).

    `t0` se toma una sola vez en la acción de la barrera, cuando han llegado los 10 VU
    y antes de liberarlos. Los 50 ítems se envían siempre, también después de
    `window_end`. Un error HTTP/red capturado cierra el ítem y el VU sigue con el
    siguiente; cualquier otra excepción es un fallo del aparato.
    """

    def __init__(
        self,
        plan: LoadPlan,
        prepared: Mapping[str, PreparedRequest],
        transport: Transport,
        *,
        clock: Clock = time.monotonic_ns,
        wall_clock: Clock = time.time_ns,
        barrier_timeout_s: float = BARRIER_TIMEOUT_SECONDS,
        join_timeout_s: float = JOIN_TIMEOUT_SECONDS,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ):
        reasons = validate_plan(plan)
        by_key = plan.by_key()
        if set(prepared) != set(by_key):
            reasons.append("prepared:request-keys")
        else:
            reasons.extend(
                f"prepared:item:{key}"
                for key, request in prepared.items()
                if request.item != by_key[key]
            )
        if reasons:
            raise Ex10PreparationError(reasons)
        self._plan = plan
        self._prepared = dict(prepared)
        self._transport = transport
        self._clock = clock
        self._wall_clock = wall_clock
        self._barrier_timeout_s = barrier_timeout_s
        self._join_timeout_s = join_timeout_s
        self._thread_factory = thread_factory
        self._lock = threading.Lock()
        self._faults: list[ApparatusFault] = []
        # Señal propia de fin de cada VU. No se usa `Thread.join`: en CPython 3.11, un
        # `KeyboardInterrupt` que corta el `join` deja marcado como terminado un hilo
        # que sigue vivo (`_wait_for_tstate_lock`) y su resultado se perdería.
        self._done = {vu: threading.Event() for vu in plan.vus}
        self._stop = threading.Event()
        self._t0: int | None = None
        self._t0_utc: int | None = None
        self._used = False

    def run(self) -> GeneratorRun:
        if self._used:
            raise Ex10ApparatusError("Un generador EX-10 se ejecuta una sola vez.")
        self._used = True
        ledger = _ItemLedger(self._plan)
        barrier = threading.Barrier(
            len(self._plan.vus), action=self._release, timeout=self._barrier_timeout_s
        )
        threads = [
            self._thread_factory(
                target=self._vu_main,
                args=(vu, ledger, barrier),
                name=f"ex10-vu-{vu:02d}",
                daemon=True,
            )
            for vu in self._plan.vus
        ]
        interrupted = False
        try:
            try:
                for thread in threads:
                    thread.start()
            except BaseException:
                barrier.abort()
                raise
            self._wait(threads, poll_threads=True, deadline=self._join_deadline())
        except KeyboardInterrupt:
            interrupted = True
            self._stop.set()
            barrier.abort()
            moment = "after_t0" if self._t0 is not None else "before_t0"
            self._fault(FaultKind.OPERATOR_INTERRUPTED, detail=moment)
            self._drain_after_interrupt(threads)

        # Nada puede enviarse ya: lo que siga en vuelo se da por perdido.
        self._stop.set()
        for vu, thread in zip(self._plan.vus, threads, strict=True):
            if self._done[vu].is_set() or (interrupted and thread.ident is None):
                continue
            if interrupted or thread.is_alive():
                self._fault(FaultKind.THREAD_HUNG, vu=vu)
            else:
                self._fault(FaultKind.THREAD_DIED, vu=vu, detail="no_exit_record")

        final = ledger.close()
        attempts = []
        for item in self._plan.items:
            state, sends, record = final[item.request_key]
            if record is None:
                if state is ItemState.APPARATUS_FAILURE:
                    self._fault(FaultKind.RESULT_LOST, vu=item.vu, request_key=item.request_key)
                record = _unfinished_record(
                    item,
                    state=state,
                    send_count=sends,
                    error_class=(
                        str(FaultKind.RESULT_LOST) if state is ItemState.APPARATUS_FAILURE else None
                    ),
                )
            attempts.append(replace(record, send_count=sends))
        return GeneratorRun(
            plan_sha256=self._plan.sha256,
            t0_ns=self._t0,
            t0_utc_ns=self._t0_utc,
            attempts=tuple(attempts),
            faults=tuple(self._faults),
            interrupted=interrupted,
        )

    def _release(self) -> None:
        # Acción de la barrera: se ejecuta una vez, con los 10 VU ya en la barrera y su lock
        # tomado. t0 se publica sólo cuando las dos lecturas del reloj se completaron; si la
        # acción falla antes, t0 queda ausente y la barrera rota es real.
        if self._t0 is not None:
            raise Ex10ApparatusError("t0 ya estaba fijado.")
        t0, t0_utc = self._clock(), self._wall_clock()
        self._t0, self._t0_utc = t0, t0_utc

    def _vu_main(self, vu: int, ledger: _ItemLedger, barrier: threading.Barrier) -> None:
        try:
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                # Con t0 ya publicado (bajo el lock de la barrera, que ordena esta lectura), el
                # error viene de un `abort` posterior a t0 que llegó antes de que este VU
                # saliera de `wait()`: CPython re-evalúa el estado al despertar. No se fabrica
                # un BARRIER_BROKEN pre-t0; la causa post-t0 la registra el hilo principal. El
                # VU no envía nada.
                if self._t0 is None:
                    self._fault(FaultKind.BARRIER_BROKEN, vu=vu)
                return
            for item in self._plan.for_vu(vu):
                if self._stop.is_set():
                    break
                self._send_one(item, ledger)
        except BaseException as exc:
            # El hilo muere: se registra y el run queda `NOT_DEMONSTRATED`.
            self._fault(FaultKind.THREAD_DIED, vu=vu, detail=type(exc).__name__)
        finally:
            self._done[vu].set()

    def _send_one(self, item: PlanItem, ledger: _ItemLedger) -> None:
        key = item.request_key
        ledger.begin(key)
        try:
            exchange = self._transport.send(
                self._prepared[key], clock=self._clock, wall_clock=self._wall_clock
            )
        except Exception as exc:
            detail = type(exc).__name__
            ledger.finish(
                key,
                _unfinished_record(
                    item,
                    state=ItemState.APPARATUS_FAILURE,
                    send_count=1,
                    error_class=str(FaultKind.INTERNAL_EXCEPTION),
                    detail=detail,
                ),
            )
            self._fault(FaultKind.INTERNAL_EXCEPTION, vu=item.vu, request_key=key, detail=detail)
            return
        record = attempt_record(item, exchange, t0_ns=self._t0)
        ledger.finish(key, record)
        if record.error_class == FaultKind.TIMING_INCONSISTENT:
            self._fault(FaultKind.TIMING_INCONSISTENT, vu=item.vu, request_key=key)
        elif record.error_class == UNATTRIBUTABLE_ERROR:
            self._fault(
                FaultKind.UNATTRIBUTABLE_ERROR,
                vu=item.vu,
                request_key=key,
                detail=record.error_detail_sanitized,
            )
        elif record.outcome is AttemptOutcome.APPARATUS_FAILURE:
            self._fault(FaultKind.INTERNAL_EXCEPTION, vu=item.vu, request_key=key)

    def _join_deadline(self) -> float:
        return time.monotonic() + self._join_timeout_s

    def _wait(
        self, threads: Sequence[threading.Thread], *, poll_threads: bool, deadline: float
    ) -> None:
        # Esperas cortas para que una interrupción del operador llegue a este hilo.
        # `poll_threads` detecta un hilo que terminó sin ejecutar `_vu_main`.
        for vu, thread in zip(self._plan.vus, threads, strict=True):
            if thread.ident is None:
                continue  # Nunca arrancó: no hay nada que esperar.
            done = self._done[vu]
            while not done.wait(0.1):
                if time.monotonic() >= deadline:
                    return
                if poll_threads and not thread.is_alive():
                    break

    def _drain_after_interrupt(self, threads: Sequence[threading.Thread]) -> None:
        # Cada VU termina su request en vuelo y no envía más. Tras la interrupción
        # `is_alive` ya no es fiable: sólo se esperan las señales de fin. Una nueva
        # interrupción no descarta los intentos ya registrados (G0: se conservan todos).
        deadline = self._join_deadline()
        while True:
            try:
                self._wait(threads, poll_threads=False, deadline=deadline)
                return
            except KeyboardInterrupt:
                continue

    def _fault(self, kind: FaultKind, **fields: Any) -> None:
        with self._lock:
            self._faults.append(ApparatusFault(kind, **fields))


def apparatus_invalidity(plan: LoadPlan, run: GeneratorRun) -> list[str]:
    """Condiciones de validez 3, 7 y 9 de G0 que dependen sólo del generador."""
    reasons: list[str] = []
    if plan.sha256 != PLAN_SHA256:
        reasons.append("plan-sha256")
    if run.plan_sha256 != plan.sha256:
        reasons.append("run-plan-sha256")
    if run.t0_ns is None:
        reasons.append("t0-missing")
    for fault in run.faults:
        subject = fault.request_key or (f"vu-{fault.vu:02d}" if fault.vu is not None else "run")
        reasons.append(f"apparatus:{fault.kind}:{subject}")
    if run.interrupted:
        reasons.append("operator-interrupted")
    if len(run.attempts) != TOTAL_REQUESTS:
        reasons.append(f"attempts:{len(run.attempts)}")
    if [attempt.request_key for attempt in run.attempts] != [i.request_key for i in plan.items]:
        reasons.append("attempts:request-keys")
    by_key = plan.by_key()
    for attempt in run.attempts:
        item = by_key.get(attempt.request_key)
        key = attempt.request_key
        if item is None or attempt.request_id != item.request_id:
            reasons.append(f"attempt-request-id:{key}")
        if attempt.send_count != 1:
            reasons.append(f"attempt-send-count:{key}:{attempt.send_count}")
        if attempt.outcome in {AttemptOutcome.APPARATUS_FAILURE, AttemptOutcome.NOT_SENT}:
            reasons.append(f"attempt-outcome:{key}:{attempt.outcome}")
        elif (
            run.t0_ns is None
            or attempt.t_start_ns is None
            or attempt.t_end_ns is None
            or not run.t0_ns <= attempt.t_start_ns <= attempt.t_end_ns
        ):
            reasons.append(f"attempt-timing:{key}")
        # Los derivados se recalculan: un registro releído no se acepta tal cual.
        elif (attempt.started_in_window, attempt.completed_in_window) != window_flags(
            run.t0_ns, attempt.t_start_ns, attempt.t_end_ns
        ) or attempt.duration_ns != attempt.t_end_ns - attempt.t_start_ns:
            reasons.append(f"attempt-derived-timing:{key}")
    for vu in plan.vus:
        sequence = [a for a in run.attempts if a.vu == vu]
        for previous, current in zip(sequence, sequence[1:], strict=False):
            if (
                previous.t_end_ns is not None
                and current.t_start_ns is not None
                and current.t_start_ns < previous.t_end_ns
            ):
                reasons.append(f"closed-loop:{current.request_key}")
    return list(dict.fromkeys(reasons))


# --- Éxito reconciliado ----------------------------------------------------------------


class SuccessStatus(StrEnum):
    #: 201 contractual + un único `Report` del usuario con el hash del plan + un único
    #: `REPORT_CREATED` +20/+20 `PENDIENTE` (DIC HT1-01, G0).
    RECONCILED_SUCCESS = "RECONCILED_SUCCESS"
    #: El hecho no ocurrió: fallo HTTP/transporte o postcondiciones persistidas falsas.
    NOT_SUCCESS = "NOT_SUCCESS"
    #: Hubo 201 contractual, pero falta evidencia persistida para decidirlo.
    RECONCILIATION_MISSING = "RECONCILIATION_MISSING"


@dataclass(frozen=True, slots=True)
class PersistedReportEvidence:
    """Lo que la reconciliación leerá de la base para un intento `HTTP_ACCEPTED`.

    `None` en un campo significa «no inspeccionado», que no es lo mismo que cero.
    """

    request_key: str
    report_id: str | None
    report_rows: int | None
    owner_alias: str | None
    media_file_hashes: tuple[str, ...] | None
    report_created_rewards: tuple[tuple[int, int, str], ...] | None
    report_created_audit_rows: int | None


@dataclass(frozen=True, slots=True)
class SuccessClassification:
    request_key: str
    status: SuccessStatus
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"request_key": self.request_key, "status": str(self.status), "reason": self.reason}


def classify_success(
    item: PlanItem, attempt: AttemptRecord, evidence: PersistedReportEvidence | None
) -> SuccessClassification:
    key = item.request_key
    if attempt.outcome is not AttemptOutcome.HTTP_ACCEPTED:
        return SuccessClassification(key, SuccessStatus.NOT_SUCCESS, str(attempt.outcome))
    if evidence is None:
        return SuccessClassification(key, SuccessStatus.RECONCILIATION_MISSING, "evidence-missing")
    fields = (
        evidence.report_id,
        evidence.report_rows,
        evidence.owner_alias,
        evidence.media_file_hashes,
        evidence.report_created_rewards,
    )
    if any(value is None for value in fields):
        return SuccessClassification(
            key, SuccessStatus.RECONCILIATION_MISSING, "evidence-incomplete"
        )
    if evidence.request_key != key or evidence.report_id != attempt.report_id:
        return SuccessClassification(
            key, SuccessStatus.RECONCILIATION_MISSING, "evidence-not-correlated"
        )
    if evidence.report_rows != 1:
        return SuccessClassification(
            key, SuccessStatus.NOT_SUCCESS, f"report-rows:{evidence.report_rows}"
        )
    if evidence.owner_alias != item.user_alias:
        return SuccessClassification(key, SuccessStatus.NOT_SUCCESS, "report-owner")
    if tuple(evidence.media_file_hashes) != (item.image_sha256,):
        return SuccessClassification(key, SuccessStatus.NOT_SUCCESS, "report-media-hash")
    if tuple(evidence.report_created_rewards) != (CREATED_REWARD,):
        return SuccessClassification(key, SuccessStatus.NOT_SUCCESS, "report-created-reward")
    return SuccessClassification(key, SuccessStatus.RECONCILED_SUCCESS)


def classify_successes(
    plan: LoadPlan,
    attempts: Sequence[AttemptRecord],
    persisted: Mapping[str, PersistedReportEvidence] | None,
) -> tuple[SuccessClassification, ...]:
    by_key = plan.by_key()
    return tuple(
        classify_success(
            by_key[attempt.request_key],
            attempt,
            None if persisted is None else persisted.get(attempt.request_key),
        )
        for attempt in attempts
        if attempt.request_key in by_key
    )


# --- HT1-01, HT1-02, HT1-03 y diagnóstico ----------------------------------------------


class CriterionStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUABLE = "NOT_EVALUABLE"
    #: HT1-02/HT1-03 con N = 0: no hay latencia que comparar con el umbral. G0: P50 y P99
    #: son `NOT_COMPUTABLE` y el criterio no se cumple; nunca es PASS ni un valor inventado.
    NOT_COMPUTABLE = NOT_COMPUTABLE


@dataclass(frozen=True, slots=True)
class ThroughputResult:
    status: CriterionStatus
    n_ok_window: int | None
    per_minute: int | None
    reconciliation_missing: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "n_ok_window": self.n_ok_window,
            "throughput_per_minute": self.per_minute,
            "denominator_minutes": WINDOW_MINUTES,
            "threshold_per_minute": THROUGHPUT_THRESHOLD_PER_MINUTE,
            "reconciliation_missing": list(self.reconciliation_missing),
        }


def evaluate_throughput(
    attempts: Sequence[AttemptRecord], successes: Sequence[SuccessClassification]
) -> ThroughputResult:
    """HT1-01: `N_ok_window = |{éxito ∧ t_start < window_end ∧ t_end ≤ window_end}|`."""
    missing = tuple(
        s.request_key for s in successes if s.status is SuccessStatus.RECONCILIATION_MISSING
    )
    if missing:
        return ThroughputResult(CriterionStatus.NOT_EVALUABLE, None, None, missing)
    reconciled = {s.request_key for s in successes if s.status is SuccessStatus.RECONCILED_SUCCESS}
    n_ok = sum(
        1
        for attempt in attempts
        if attempt.request_key in reconciled
        and attempt.started_in_window is True
        and attempt.completed_in_window is True
    )
    # La ventana es exactamente 1 minuto: el throughput por minuto es `N_ok_window`.
    per_minute = n_ok // WINDOW_MINUTES
    status = (
        CriterionStatus.PASS
        if per_minute >= THROUGHPUT_THRESHOLD_PER_MINUTE
        else CriterionStatus.FAIL
    )
    return ThroughputResult(status, n_ok, per_minute)


def nearest_rank(n: int, percentile: tuple[int, int]) -> int:
    """Rango base 1 `ceil(p × N)`, en aritmética entera y sin interpolación."""
    if n <= 0:
        raise ValueError("nearest-rank exige N > 0.")
    numerator, denominator = percentile
    return -(-numerator * n // denominator)


@dataclass(frozen=True, slots=True)
class PercentileResult:
    durations_ns: tuple[int, ...]
    p50_ns: int | None
    p99_ns: int | None
    p50_rank: int | None
    p99_rank: int | None
    ht1_02: CriterionStatus
    ht1_03: CriterionStatus

    @property
    def n(self) -> int:
        return len(self.durations_ns)

    def to_json(self) -> dict[str, Any]:
        return {
            "set": "all-http-201-in-or-out-of-window",
            "n": self.n,
            "durations_ns_sorted": list(self.durations_ns),
            "ht1_02": {
                "p50_ns": NOT_COMPUTABLE if self.p50_ns is None else self.p50_ns,
                "rank": self.p50_rank,
                "threshold_ns": P50_THRESHOLD_NS,
                "status": str(self.ht1_02),
            },
            "ht1_03": {
                "p99_ns": NOT_COMPUTABLE if self.p99_ns is None else self.p99_ns,
                "rank": self.p99_rank,
                "threshold_ns": P99_THRESHOLD_NS,
                "status": str(self.ht1_03),
            },
        }


def latency_set(attempts: Sequence[AttemptRecord]) -> tuple[int, ...]:
    """Duraciones de todos los HTTP 201 del run, dentro o fuera de la ventana.

    Entra todo 201 con el cuerpo leído por completo y `t_start`/`t_end` válidos,
    contractual (`HTTP_ACCEPTED`) o no (`CONTRACT_VIOLATION`). Un 201 truncado es
    `TRANSPORT_FAILED`: no tiene `t_end` tras el último byte y no entra.
    """
    return tuple(
        sorted(
            attempt.duration_ns
            for attempt in attempts
            if attempt.http_status == 201
            and attempt.outcome in {AttemptOutcome.HTTP_ACCEPTED, AttemptOutcome.CONTRACT_VIOLATION}
            and attempt.duration_ns is not None
        )
    )


def latency_percentiles(attempts: Sequence[AttemptRecord]) -> PercentileResult:
    durations = latency_set(attempts)
    if not durations:
        # N = 0: sin valor ni rango. No es una comparación numérica fallida.
        return PercentileResult(
            durations,
            None,
            None,
            None,
            None,
            CriterionStatus.NOT_COMPUTABLE,
            CriterionStatus.NOT_COMPUTABLE,
        )
    p50_rank = nearest_rank(len(durations), P50)
    p99_rank = nearest_rank(len(durations), P99)
    p50 = durations[p50_rank - 1]
    p99 = durations[p99_rank - 1]
    return PercentileResult(
        durations,
        p50,
        p99,
        p50_rank,
        p99_rank,
        CriterionStatus.PASS if p50 <= P50_THRESHOLD_NS else CriterionStatus.FAIL,
        CriterionStatus.PASS if p99 <= P99_THRESHOLD_NS else CriterionStatus.FAIL,
    )


def latency_histogram(durations_ns: Iterable[int]) -> list[dict[str, Any]]:
    """Histograma diagnóstico [a, b) en ms (TD-04). No interviene en la decisión."""
    bounds_ns = [bound * 1_000_000 for bound in HISTOGRAM_BOUNDS_MS]
    counts = [0] * len(bounds_ns)
    for duration in durations_ns:
        if duration < 0:
            raise ValueError("Duración negativa en el histograma.")
        counts[bisect.bisect_right(bounds_ns, duration) - 1] += 1
    uppers: list[int | None] = [*HISTOGRAM_BOUNDS_MS[1:], None]
    return [
        {"lower_ms": lower, "upper_ms": upper, "count": count}
        for lower, upper, count in zip(HISTOGRAM_BOUNDS_MS, uppers, counts, strict=True)
    ]


def failure_counts(attempts: Sequence[AttemptRecord]) -> dict[str, int]:
    """Fallos HTTP/transporte/aparato por clase, contados aparte del histograma.

    Un 201 con cuerpo no contractual cuenta aquí (`contract_violation`) y también en
    el histograma, que agrupa todos los 201. Un 201 contractual que no se reconcilia
    no aparece aquí: su motivo queda en la clasificación de éxitos.
    """
    return dict(
        sorted(
            Counter(
                attempt.error_class
                for attempt in attempts
                if attempt.outcome is not AttemptOutcome.HTTP_ACCEPTED and attempt.error_class
            ).items()
        )
    )


# --- HT1-04 y postcondición de auditoría -----------------------------------------------


class EvidenceStatus(StrEnum):
    PASS = "PASS"
    #: El hecho no ocurrió: la fase de decisión lo convierte en `FAIL`.
    FACT_FAILED = "FACT_FAILED"
    #: Falta evidencia, o un evento falta pero otra fuente prueba el hecho.
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    #: Configuración asíncrona inválida, o violación temporal con los indicadores
    #: estructurales correctos.
    INCONSISTENT = "INCONSISTENT"


#: Cómo se traduce cada estado de evidencia en la decisión del run.
EVIDENCE_DECISION = {
    EvidenceStatus.PASS: Decision.PASS,
    EvidenceStatus.FACT_FAILED: Decision.FAIL,
    EvidenceStatus.EVIDENCE_MISSING: Decision.NOT_DEMONSTRATED,
    EvidenceStatus.INCONSISTENT: Decision.NOT_DEMONSTRATED,
}


@dataclass(frozen=True, slots=True)
class Finding:
    status: EvidenceStatus
    code: str
    subject: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"status": str(self.status), "code": self.code, "subject": self.subject}


@dataclass(frozen=True, slots=True)
class EvidenceResult:
    status: EvidenceStatus
    findings: tuple[Finding, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {"status": str(self.status), "findings": [f.to_json() for f in self.findings]}


def _aggregate(findings: Iterable[Finding]) -> EvidenceResult:
    findings = tuple(findings)
    statuses = {finding.status for finding in findings}
    # La falta de evidencia prevalece sobre cualquier hecho fallido: «si falta un
    # evento pero una evidencia independiente prueba el hecho, el run es
    # NOT_DEMONSTRATED» (G0, HT1-04) y un FAIL vinculante exige un run completamente
    # evaluable (Decisión B).
    for status in (
        EvidenceStatus.EVIDENCE_MISSING,
        EvidenceStatus.INCONSISTENT,
        EvidenceStatus.FACT_FAILED,
    ):
        if status in statuses:
            return EvidenceResult(status, findings)
    return EvidenceResult(EvidenceStatus.PASS, findings)


@dataclass(frozen=True, slots=True)
class ProcessRef:
    hostname: str
    pid: int


@dataclass(frozen=True, slots=True)
class CeleryEventEvidence:
    """Un evento de `celery.jsonl` con los campos que G0 añade (`process`, …)."""

    event: str
    request_id: str | None
    task_id: str | None
    task_name: str | None
    process: ProcessRef | None
    enqueue_at_utc_ns: int | None = None
    start_at_utc_ns: int | None = None
    finish_at_utc_ns: int | None = None
    db_transaction_open: bool | None = None
    status: str | None = None
    celery_state: str | None = None


@dataclass(frozen=True, slots=True)
class AsyncFacts:
    """Hechos de un éxito que no vienen de `celery.jsonl`: base, `http.jsonl` y broker.

    `None` significa «no inspeccionado».
    """

    request_id: str
    report_created_at_utc_ns: int | None
    http_finished_at_utc_ns: int | None
    api_process: ProcessRef | None
    ai_classification_models: tuple[str, ...] | None
    final_status: str | None
    ai_classified_audit_rows: int | None
    task_in_broker: bool | None = None
    enqueue_failure_logged: bool | None = None


@dataclass(frozen=True, slots=True)
class AsyncConfigEvidence:
    """`CFG_ASYNC` de G0, observado en el API y en el worker."""

    api_task_always_eager: bool | None
    worker_task_always_eager: bool | None
    broker_url: str | None
    worker_nodes_before: int | None
    worker_max_concurrency_before: int | None
    worker_nodes_after: int | None
    worker_max_concurrency_after: int | None
    worker_fake_ai_enabled: bool | None


_EXPECTED_ASYNC_CONFIG = {
    "api_task_always_eager": False,
    "worker_task_always_eager": False,
    "broker_url": BROKER_URL,
    "worker_nodes_before": 1,
    "worker_max_concurrency_before": WORKER_CONCURRENCY,
    "worker_nodes_after": 1,
    "worker_max_concurrency_after": WORKER_CONCURRENCY,
    "worker_fake_ai_enabled": True,
}


@dataclass(frozen=True, slots=True)
class Ht104Evidence:
    config: AsyncConfigEvidence | None
    events: tuple[CeleryEventEvidence, ...]
    facts: Mapping[str, AsyncFacts]


@dataclass(frozen=True, slots=True)
class Ht104Result:
    result: EvidenceResult
    queue_latency_ns: Mapping[str, int] = field(default_factory=dict)
    task_duration_ns: Mapping[str, int] = field(default_factory=dict)
    #: Todas las violaciones temporales observadas, también las que no cambian el
    #: resultado porque el intento ya tiene un hecho fallido.
    temporal_violations: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def status(self) -> EvidenceStatus:
        return self.result.status

    def to_json(self) -> dict[str, Any]:
        return {
            **self.result.to_json(),
            # Se informan sin umbral (G0, HT1-04).
            "queue_latency_ns": dict(sorted(self.queue_latency_ns.items())),
            "task_duration_ns": dict(sorted(self.task_duration_ns.items())),
            "temporal_violations": {
                k: list(v) for k, v in sorted(self.temporal_violations.items())
            },
        }


def evaluate_async_config(config: AsyncConfigEvidence | None) -> list[Finding]:
    if config is None:
        return [Finding(EvidenceStatus.EVIDENCE_MISSING, "cfg-async:missing")]
    findings = []
    for name, expected in _EXPECTED_ASYNC_CONFIG.items():
        actual = getattr(config, name)
        if actual is None:
            findings.append(Finding(EvidenceStatus.EVIDENCE_MISSING, f"cfg-async:{name}:missing"))
        elif type(actual) is not type(expected) or actual != expected:
            findings.append(Finding(EvidenceStatus.INCONSISTENT, f"cfg-async:{name}"))
    return findings


def evaluate_ht1_04(
    success_request_ids: Sequence[str], evidence: Ht104Evidence | None
) -> Ht104Result:
    """Predicado estructural de HT1-04 (G0, MD-06) sobre evidencia ya recogida.

    Nunca compara el fin de la tarea con el fin del HTTP. `S` son los éxitos
    reconciliados, tardíos incluidos.
    """
    if evidence is None:
        return Ht104Result(
            EvidenceResult(
                EvidenceStatus.EVIDENCE_MISSING,
                (Finding(EvidenceStatus.EVIDENCE_MISSING, "ht1-04:evidence-missing"),),
            )
        )
    findings = evaluate_async_config(evidence.config)
    queue_latency: dict[str, int] = {}
    task_duration: dict[str, int] = {}
    temporal_violations: dict[str, tuple[str, ...]] = {}
    for request_id in success_request_ids:
        attempt_findings, latency, duration, temporal = _evaluate_async_attempt(
            request_id, evidence
        )
        findings.extend(attempt_findings)
        if latency is not None:
            queue_latency[request_id] = latency
        if duration is not None:
            task_duration[request_id] = duration
        if temporal:
            temporal_violations[request_id] = tuple(temporal)
    return Ht104Result(_aggregate(findings), queue_latency, task_duration, temporal_violations)


def _rows_found(rows: tuple[str, ...] | None) -> bool | None:
    return None if rows is None else bool(rows)


def _independent_proof(*sources: bool | None) -> bool | None:
    """¿Alguna fuente independiente prueba el hecho?

    `True` si alguna lo prueba; `None` si ninguna lo prueba pero alguna no se
    inspeccionó; `False` sólo si todas se inspeccionaron y ninguna lo prueba. Lo no
    inspeccionado nunca se toma como ausente.
    """
    if any(source is True for source in sources):
        return True
    if any(source is None for source in sources):
        return None
    return False


def _evaluate_async_attempt(
    request_id: str, evidence: Ht104Evidence
) -> tuple[list[Finding], int | None, int | None, list[str]]:
    structural: list[Finding] = []
    temporal: list[str] = []

    def add(status: EvidenceStatus, code: str) -> None:
        structural.append(Finding(status, code, request_id))

    def missing_event(event: str, proof: bool | None) -> None:
        # G0: evento ausente + evidencia independiente del hecho → NOT_DEMONSTRATED;
        # hecho no ocurrido → FAIL. Sin inspeccionar, no se puede concluir ninguno.
        if proof is True:
            add(EvidenceStatus.EVIDENCE_MISSING, f"{event}:missing")
        elif proof is None:
            add(EvidenceStatus.EVIDENCE_MISSING, f"{event}:missing-uninspected")
        else:
            add(EvidenceStatus.FACT_FAILED, f"{event}:not-processed")

    facts = evidence.facts.get(request_id)
    if facts is None:
        add(EvidenceStatus.EVIDENCE_MISSING, "facts:missing")
        return structural, None, None, temporal

    events = evidence.events
    classifications = facts.ai_classification_models
    queued = [e for e in events if e.event == "celery.queued" and e.request_id == request_id]
    later_by_request = [
        e for e in events if e.event != "celery.queued" and e.request_id == request_id
    ]
    task_id: str | None = None
    enqueue_at: int | None = None
    queue_latency: int | None = None
    task_duration: int | None = None

    # Un único celery.queued desde el API, fuera de una transacción y en orden.
    if len(queued) > 1:
        add(EvidenceStatus.FACT_FAILED, f"queued:count:{len(queued)}")
    elif not queued:
        proof = _independent_proof(
            bool(later_by_request), _rows_found(classifications), facts.task_in_broker
        )
        if proof is not True and facts.enqueue_failure_logged is True:
            add(EvidenceStatus.FACT_FAILED, "queued:enqueue-failed")
        else:
            missing_event("queued", proof)
    else:
        event = queued[0]
        if event.task_name != TASK_NAME:
            add(EvidenceStatus.FACT_FAILED, "queued:task-name")
        if event.task_id:
            task_id = event.task_id
        else:
            add(EvidenceStatus.EVIDENCE_MISSING, "queued:task-id-missing")
        if event.process is None or facts.api_process is None:
            add(EvidenceStatus.EVIDENCE_MISSING, "queued:process-missing")
        elif event.process != facts.api_process:
            add(EvidenceStatus.FACT_FAILED, "queued:process-not-api")
        if event.db_transaction_open is None:
            add(EvidenceStatus.EVIDENCE_MISSING, "queued:db-transaction-open-missing")
        elif event.db_transaction_open:
            add(EvidenceStatus.FACT_FAILED, "queued:db-transaction-open")
        enqueue_at = event.enqueue_at_utc_ns
        created_at = facts.report_created_at_utc_ns
        http_finished = facts.http_finished_at_utc_ns
        if created_at is None or enqueue_at is None or http_finished is None:
            add(EvidenceStatus.EVIDENCE_MISSING, "queued:timestamps-missing")
        elif not created_at <= enqueue_at <= http_finished:
            temporal.append("queued:temporal-order")

    if task_id is not None:
        started = [e for e in events if e.event == "celery.started" and e.task_id == task_id]
        finished = [e for e in events if e.event == "celery.finished" and e.task_id == task_id]
        failed = [e for e in events if e.event == "celery.failed" and e.task_id == task_id]

        # Un único celery.started con el mismo task_id, fuera del proceso del API.
        if len(started) > 1:
            add(EvidenceStatus.FACT_FAILED, f"started:count:{len(started)}")
        elif not started:
            missing_event(
                "started",
                _independent_proof(bool(finished or failed), _rows_found(classifications)),
            )
        else:
            event = started[0]
            if event.process is None or facts.api_process is None:
                add(EvidenceStatus.EVIDENCE_MISSING, "started:process-missing")
            elif event.process == facts.api_process:
                add(EvidenceStatus.FACT_FAILED, "started:process-is-api")
            if event.start_at_utc_ns is None or enqueue_at is None:
                add(EvidenceStatus.EVIDENCE_MISSING, "started:timestamp-missing")
            elif event.start_at_utc_ns < enqueue_at:
                temporal.append("started:before-enqueue")
            else:
                queue_latency = event.start_at_utc_ns - enqueue_at

        # Un único celery.finished con status=finished y celery_state=success.
        if failed:
            add(EvidenceStatus.FACT_FAILED, "finished:celery-failed")
        if len(finished) > 1:
            add(EvidenceStatus.FACT_FAILED, f"finished:count:{len(finished)}")
        elif not finished:
            # Con `celery.failed` el hecho ya está decidido: no falta ningún evento.
            if not failed:
                missing_event("finished", _rows_found(classifications))
        else:
            event = finished[0]
            if event.status is None or event.celery_state is None:
                add(EvidenceStatus.EVIDENCE_MISSING, "finished:state-missing")
            elif event.status != "finished" or event.celery_state != "success":
                add(EvidenceStatus.FACT_FAILED, "finished:state")
            if event.start_at_utc_ns is not None and event.finish_at_utc_ns is not None:
                task_duration = event.finish_at_utc_ns - event.start_at_utc_ns

    # Un único AiClassification del fake, estado CLASIFICADO y un único audit.
    if classifications is None:
        add(EvidenceStatus.EVIDENCE_MISSING, "classification:not-inspected")
    elif len(classifications) != 1:
        add(EvidenceStatus.FACT_FAILED, f"classification:count:{len(classifications)}")
    elif classifications[0] != AI_MODEL_NAME:
        add(EvidenceStatus.FACT_FAILED, "classification:model-name")
    if facts.final_status is None:
        add(EvidenceStatus.EVIDENCE_MISSING, "final-status:missing")
    elif facts.final_status != REPORT_STATUS_CLASSIFIED:
        add(EvidenceStatus.FACT_FAILED, "final-status")
    audit_rows = facts.ai_classified_audit_rows
    if audit_rows is None:
        add(EvidenceStatus.EVIDENCE_MISSING, "audit-ai-classified:missing")
    elif audit_rows != 1:
        add(EvidenceStatus.FACT_FAILED, f"audit-ai-classified:count:{audit_rows}")

    # Una violación temporal sólo deja el run sin demostrar si los indicadores
    # estructurales del intento son correctos; si no, el intento ya es un hecho fallido.
    if not any(f.status is EvidenceStatus.FACT_FAILED for f in structural):
        structural.extend(Finding(EvidenceStatus.INCONSISTENT, c, request_id) for c in temporal)
    return structural, queue_latency, task_duration, temporal


def evaluate_audit_postcondition(
    success_keys: Sequence[str], persisted: Mapping[str, PersistedReportEvidence] | None
) -> EvidenceResult:
    """Cada éxito tiene exactamente un `AuditLog report.created`."""
    findings = []
    for key in success_keys:
        evidence = None if persisted is None else persisted.get(key)
        rows = None if evidence is None else evidence.report_created_audit_rows
        if rows is None:
            findings.append(Finding(EvidenceStatus.EVIDENCE_MISSING, "audit-report-created", key))
        elif rows != 1:
            findings.append(
                Finding(EvidenceStatus.FACT_FAILED, f"audit-report-created:count:{rows}", key)
            )
    return _aggregate(findings)


# --- Decisión --------------------------------------------------------------------------

#: `NOT_DEMONSTRATED` deja el manifest `INVALID`; `PASS` y `FAIL`, `COMPLETED` (G0).
MANIFEST_STATUS = {
    Decision.PASS: RunStatus.COMPLETED,
    Decision.FAIL: RunStatus.COMPLETED,
    Decision.NOT_DEMONSTRATED: RunStatus.INVALID,
}


@dataclass(frozen=True, slots=True)
class DecisionResult:
    decision: Decision
    criteria: Mapping[str, str]
    invalidity_reasons: tuple[str, ...]
    not_demonstrable: tuple[str, ...]
    failed_criteria: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "decision": str(self.decision),
            "manifest_status": str(MANIFEST_STATUS[self.decision]),
            "criteria": dict(self.criteria),
            "invalidity_reasons": list(self.invalidity_reasons),
            "not_demonstrable": list(self.not_demonstrable),
            "failed_criteria": list(self.failed_criteria),
        }


def decide(
    *,
    invalidity_reasons: Sequence[str],
    throughput: ThroughputResult,
    percentiles: PercentileResult,
    ht1_04: EvidenceResult,
    audit: EvidenceResult,
) -> DecisionResult:
    """Regla de decisión de G0, sin acceso al stack. Precedencia (Decisión B):

    1. `NOT_DEMONSTRATED`: el aparato o el entorno son inválidos, o falta evidencia o
       hay una inconsistencia que impide demostrar algún criterio, aunque otro
       criterio ya haya fallado.
    2. `FAIL`: run válido y completamente evaluable con algún criterio fallido.
    3. `PASS`: run válido y completamente evaluable que cumple HT1-01…04 y la
       postcondición de auditoría.

    HT1-02/HT1-03 `NOT_COMPUTABLE` (N = 0) no es un fallo numérico ni invalida el aparato:
    no entra en `failed_criteria` ni en `not_demonstrable`. Con evidencia coherente N = 0
    implica HT1-01 FAIL, así que el run válido es FAIL por HT1-01. Nunca es PASS: si ningún
    otro criterio falla, las entradas son incoherentes y el run no demuestra nada.
    """
    criteria = {
        "HT1-01": str(throughput.status),
        "HT1-02": str(percentiles.ht1_02),
        "HT1-03": str(percentiles.ht1_03),
        "HT1-04": str(ht1_04.status),
        "audit-postcondition": str(audit.status),
    }
    not_demonstrable = []
    if throughput.status is CriterionStatus.NOT_EVALUABLE:
        not_demonstrable.append("HT1-01:NOT_EVALUABLE")
    for label, result in (("HT1-04", ht1_04), ("audit-postcondition", audit)):
        if EVIDENCE_DECISION[result.status] is Decision.NOT_DEMONSTRATED:
            not_demonstrable.append(f"{label}:{result.status}")
    failed = [
        label
        for label, is_failed in (
            ("HT1-01", throughput.status is CriterionStatus.FAIL),
            ("HT1-02", percentiles.ht1_02 is CriterionStatus.FAIL),
            ("HT1-03", percentiles.ht1_03 is CriterionStatus.FAIL),
            ("HT1-04", ht1_04.status is EvidenceStatus.FACT_FAILED),
            ("audit-postcondition", audit.status is EvidenceStatus.FACT_FAILED),
        )
        if is_failed
    ]
    not_computable = [
        label
        for label, status in (("HT1-02", percentiles.ht1_02), ("HT1-03", percentiles.ht1_03))
        if status is CriterionStatus.NOT_COMPUTABLE
    ]
    if invalidity_reasons or not_demonstrable:
        decision = Decision.NOT_DEMONSTRATED
    elif failed:
        decision = Decision.FAIL
    elif not_computable:
        not_demonstrable.extend(
            f"{label}:{CriterionStatus.NOT_COMPUTABLE}" for label in not_computable
        )
        decision = Decision.NOT_DEMONSTRATED
    else:
        decision = Decision.PASS
    return DecisionResult(
        decision, criteria, tuple(invalidity_reasons), tuple(not_demonstrable), tuple(failed)
    )


@dataclass(frozen=True, slots=True)
class Ex10Evaluation:
    plan: LoadPlan
    run: GeneratorRun
    successes: tuple[SuccessClassification, ...]
    throughput: ThroughputResult
    percentiles: PercentileResult
    ht1_04: Ht104Result
    audit: EvidenceResult
    result: DecisionResult

    @property
    def decision(self) -> Decision:
        return self.result.decision

    def calculation_payload(self) -> dict[str, Any]:
        """Contenido futuro de `ex10-ht1-calculation.json`. Aquí no se escribe."""
        attempts = self.run.attempts
        return {
            "schema_version": CALCULATION_SCHEMA_VERSION,
            **_identity_header(self.plan),
            "window": {
                "t0_ns": self.run.t0_ns,
                "t0_utc_ns": self.run.t0_utc_ns,
                "window_end_ns": self.run.window_end_ns,
                "window_ns": WINDOW_NS,
            },
            "attempts": {
                "total": len(attempts),
                "by_outcome": dict(sorted(Counter(str(a.outcome) for a in attempts).items())),
                "by_success_status": dict(
                    sorted(Counter(str(s.status) for s in self.successes).items())
                ),
                "failures_by_class": failure_counts(attempts),
            },
            "successes": [s.to_json() for s in self.successes],
            "ht1_01": self.throughput.to_json(),
            "latency": self.percentiles.to_json(),
            "histogram": {
                "diagnostic_only": True,
                "unit": "ms",
                "bins": latency_histogram(self.percentiles.durations_ns),
            },
            "ht1_04": self.ht1_04.to_json(),
            "audit_postcondition": self.audit.to_json(),
        }

    def decision_payload(self) -> dict[str, Any]:
        """Contenido futuro de `ex10-decision.json`. Aquí no se escribe."""
        return {
            "schema_version": DECISION_SCHEMA_VERSION,
            **_identity_header(self.plan),
            "decision_reference": DECISION_REFERENCE,
            **self.result.to_json(),
        }


def evaluate_run(
    plan: LoadPlan,
    run: GeneratorRun,
    *,
    persisted: Mapping[str, PersistedReportEvidence] | None = None,
    async_evidence: Ht104Evidence | None = None,
    environment_invalidity: Sequence[str] = (),
) -> Ex10Evaluation:
    """Compone la evaluación completa a partir de datos ya recogidos.

    `environment_invalidity` recibe las condiciones de validez que no dependen del
    generador (HEAD, contenedores, reset, fixture, custodia…), que calcularán fases
    posteriores. Sin `persisted` ningún 201 es un éxito y el run no demuestra nada.
    """
    invalidity = [*apparatus_invalidity(plan, run), *environment_invalidity]
    successes = classify_successes(plan, run.attempts, persisted)
    throughput = evaluate_throughput(run.attempts, successes)
    percentiles = latency_percentiles(run.attempts)
    if throughput.status is CriterionStatus.NOT_EVALUABLE:
        unknown = EvidenceResult(
            EvidenceStatus.EVIDENCE_MISSING,
            (Finding(EvidenceStatus.EVIDENCE_MISSING, "success-set-unknown"),),
        )
        ht1_04 = Ht104Result(unknown)
        audit = unknown
    else:
        by_key = plan.by_key()
        success_keys = [
            s.request_key for s in successes if s.status is SuccessStatus.RECONCILED_SUCCESS
        ]
        ht1_04 = evaluate_ht1_04([by_key[k].request_id for k in success_keys], async_evidence)
        audit = evaluate_audit_postcondition(success_keys, persisted)
    result = decide(
        invalidity_reasons=invalidity,
        throughput=throughput,
        percentiles=percentiles,
        ht1_04=ht1_04.result,
        audit=audit,
    )
    return Ex10Evaluation(plan, run, successes, throughput, percentiles, ht1_04, audit, result)


# --- Payloads serializables y control de contenido sensible ----------------------------


def _identity_header(plan: LoadPlan) -> dict[str, Any]:
    return {
        "protocol_id": PROTOCOL_ID,
        "methodology_version": METHODOLOGY_VERSION,
        "scenario_id": SCENARIO_ID,
        "dataset_id": DATASET_ID,
        "dataset_sha256": plan.dataset_sha256,
        "plan_sha256": plan.sha256,
    }


def raw_results_payload(plan: LoadPlan, run: GeneratorRun) -> dict[str, Any]:
    """Contenido futuro de `ex10-load-raw.json`: 50 registros de intento."""
    return {
        "schema_version": RAW_SCHEMA_VERSION,
        **_identity_header(plan),
        "t0_ns": run.t0_ns,
        "t0_utc_ns": run.t0_utc_ns,
        "window_end_ns": run.window_end_ns,
        "interrupted": run.interrupted,
        "attempts": [attempt.to_json() for attempt in run.attempts],
        "apparatus_faults": [fault.to_json() for fault in run.faults],
    }


_SENSITIVE_KEYS = frozenset({"latitude", "longitude", "lat", "lng", "base64", "location"})


def _mapping_keys(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _mapping_keys(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _mapping_keys(item)


def sensitive_findings(
    payload: Any, *, dataset: LoadDataset, secrets: Iterable[str] = ()
) -> list[str]:
    """Contenido que nunca puede persistirse: tokens, coordenadas e imágenes.

    Devuelve etiquetas, nunca los valores encontrados.
    """
    text = canonical_json_bytes(payload).decode("utf-8")
    findings = []
    if contains_secret(payload):
        findings.append("secret-pattern")
    if any(secret and secret in text for secret in secrets):
        findings.append("secret-value")
    # Sólo claves de diccionario: `"lat"` como nombre de un campo rechazado por el
    # servidor (`json_keys` de un 400) no es una coordenada y no se marca.
    for key in sorted(set(_mapping_keys(payload)) & _SENSITIVE_KEYS):
        findings.append(f"sensitive-key:{key}")
    if _PNG_SIGNATURE[:4].decode("latin-1") in text or "iVBORw0KGgo" in text:
        findings.append("png-content")
    for row in dataset.payload["requests"]:
        key = row["request_key"]
        if repr(float(row["latitude"])) in text or repr(float(row["longitude"])) in text:
            findings.append(f"coordinate:{key}")
        if row["image"]["base64"] in text:
            findings.append(f"image:{key}")
    return findings
