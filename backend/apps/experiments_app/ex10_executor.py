"""Executor oficial de EX-10 (fase 3A): ciclo de vida, emisión, drain y decisión.

Orquesta un run de `s11-ex10-v1` sobre la custodia S11 existente (`EvidenceStore`,
`create_manifest`, `finalize_with_json_artifact`) y sobre el aparato de la fase 1
(`ex10_load`): no redefine ningún parámetro de G0 ni duplica el sistema de evidencia.

Cada mutación irreversible es un paso aparte, con su checkpoint custodiado:

- `start`: preflight operacional en proceso (mismo HEAD, recién hecho), fuentes del
  executor, proveedor fake y la regla del primer run válido; después `run_id` UUIDv5 y
  manifest inicial. Ninguna petición HTTP al API.
- `execute`: READY_STATE y `CFG_ASYNC` previos, el marcador de emisión (punto de no
  retorno), tokens y cuerpos precodificados, el marcador de armado (lo último antes de la
  barrera) y, sólo entonces, la barrera, los 50 intentos y el drain. Nunca dos veces: no
  existe un reenvío ni una emisión parcial.
- `drain`: sólo para recuperar un drain interrumpido. Lee, no envía nada.
- `reconcile`: sólo lectura y repetible; no persiste nada.
- `finalize`: la reconciliación definitiva, la decisión (una única vez) y el sello.

El estado del run no es una columna: se deriva de los artefactos indexados (`Phase`), como
en EX-09. `RunStatus` no cambia de significado: el manifest queda `PREPARED` hasta el sello
y termina `COMPLETED` (PASS/FAIL) o `INVALID` (NOT_DEMONSTRATED), según `MANIFEST_STATUS`.

`store.mutation()` es un `flock` sobre toda la raíz de evidencia, el mismo que toman web y
worker en cada evento técnico: nunca se sostiene durante la emisión ni el drain, sólo en
cada checkpoint.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import signal
import socket
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from django.conf import settings

from . import ex10_fixture, ex10_load, ex10_preflight, ex10_reconcile, ex10_worker_probe
from .canonical import canonical_json_bytes, sha256_bytes
from .constants import ArtifactKind, Classification, Decision
from .evidence import EvidenceStore, validate_finalized_state
from .ex10_load import (
    ApparatusFault,
    AttemptOutcome,
    AttemptRecord,
    FaultKind,
    GeneratorRun,
    ItemState,
    LoadDataset,
    LoadPlan,
)
from .ex10_reconcile import ConfigObservation, Stream
from .manifest import ArtifactReference, RunManifest, create_manifest, finalize_manifest
from .redaction import redact
from .runtime import valid_run_id

SCENARIO_ID = ex10_load.SCENARIO_ID
OFFICIAL_MODE = "OFFICIAL"
LOCAL_TEST_MODE = "LOCAL_TEST"
EXPERIMENT_IDS = {
    OFFICIAL_MODE: "S11C3-EX10",
    LOCAL_TEST_MODE: "S11C3-EX10-LOCAL-TEST-NON-EXPERIMENTAL",
}
ENVIRONMENT_NAME = "isolated-experiment"
EXECUTOR_VERSION = "s11c3-ex10-executor-v1"

ART_MANIFEST_INITIAL = "run-manifest-initial"
ART_MANIFEST_FINAL = "run-manifest-final"
ART_PREFLIGHT = "ex10-preflight"
ART_START = "ex10-start"
ART_PLAN = "ex10-plan"
ART_EMISSION = "ex10-emission-started"
ART_EMISSION_ABORTED = "ex10-emission-aborted"
ART_ARMED = "ex10-emission-armed"
ART_RAW = "ex10-load-raw"
ART_RAW_REJECTED = "ex10-load-raw-rejected"
ART_DRAIN = "ex10-drain"
ART_RECONCILIATION = "ex10-reconciliation"
ART_CALCULATION = "ex10-ht1-calculation"
ART_DECISION = "ex10-decision"
STREAMS = {"http": "http-events", "celery": "celery-events"}

#: Política operacional de frescura del preflight, no derivada de la vida de los JWT: los
#: tokens del run se emiten después, al emitir. El launcher corre el preflight justo antes
#: de START; el límite sólo defiende de un informe viejo, un operador en pausa o un cambio
#: operacional no revalidado. Inclusivo: una antigüedad de 15 min exactos se acepta.
PREFLIGHT_MAX_AGE = timedelta(minutes=15)
#: Un `checked_at` posterior al reloj del executor no se tolera: el preflight de START corre
#: en este mismo proceso y el de la emisión se lee de su registro custodiado.
PREFLIGHT_FUTURE_TOLERANCE = timedelta(0)
DRAIN_POLL_NS = 1_000_000_000
DRAIN_EXTENSION_NS = ex10_load.DRAIN_LIMIT_SECONDS * 1_000_000_000
WORKER_PROBE_TIMEOUT_SECONDS = 3.0
#: Código que ejecuta un run: el que ya califica el preflight (`RUNNING_SOURCES`, que incluye
#: el propio executor) y la preparación que reutiliza en sólo lectura. START exige que sea
#: byte a byte el del checkout que el preflight dio por bueno.
EXECUTOR_SOURCES = (
    *ex10_preflight.RUNNING_SOURCES,
    "backend/apps/experiments_app/ex10_prepare.py",
)
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_NONCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
#: Un abandono se declara con un código, nunca con texto libre: no entra prosa en la evidencia.
_REASON = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")


class Ex10ExecutorError(RuntimeError):
    """El executor no puede continuar. Nunca contiene tokens, URLs ni valores."""


class Ex10Refusal(Ex10ExecutorError):
    """Un paso rechazado antes de mutar nada. `terminal`: no tiene sentido reintentarlo."""

    def __init__(self, reasons: Iterable[str], *, terminal: bool):
        self.reasons = tuple(dict.fromkeys(reasons))
        self.terminal = terminal
        super().__init__(f"EX-10 rechazó el paso: {list(self.reasons)}")


# --- Estado del run ------------------------------------------------------------------------


class Phase(StrEnum):
    """Fase derivada de los artefactos. Las cuatro últimas son terminales."""

    #: Manifest inicial sin el registro de START (un START interrumpido): sólo abandono.
    CREATED = "CREATED"
    #: START completo: se puede emitir o abandonar.
    PREPARED = "PREPARED"
    #: Marcador de emisión sin armar: preparando tokens y cuerpos. Nada se envió todavía.
    STARTED = "STARTED"
    #: Armado sin resultado: la barrera pudo liberarse (t0) o el proceso murió emitiendo.
    EMITTING = "EMITTING"
    #: La emisión se detuvo antes de la barrera: no hubo t0 ni ningún envío.
    STOPPED_PRE_T0 = "STOPPED_PRE_T0"
    #: Resultado crudo custodiado; falta el drain.
    DRAINING = "DRAINING"
    #: Drain custodiado: se puede reconciliar y finalizar.
    RECONCILING = "RECONCILING"
    #: Decisión custodiada sin sello: una finalización interrumpida.
    DECIDED = "DECIDED"
    ABANDONED_PRE_T0 = "ABANDONED_PRE_T0"
    NOT_DEMONSTRATED = "NOT_DEMONSTRATED"
    FINALIZED_PASS = "FINALIZED_PASS"
    FINALIZED_FAIL = "FINALIZED_FAIL"


TERMINAL_PHASES = frozenset(
    {Phase.ABANDONED_PRE_T0, Phase.NOT_DEMONSTRATED, Phase.FINALIZED_PASS, Phase.FINALIZED_FAIL}
)
#: Transiciones permitidas por paso. Cualquier otra se rechaza sin tocar nada.
ALLOWED = {
    "execute": frozenset({Phase.PREPARED}),
    "drain": frozenset({Phase.DRAINING}),
    "reconcile": frozenset({Phase.RECONCILING, Phase.DECIDED}),
    "finalize": frozenset({Phase.RECONCILING, Phase.DECIDED}),
    #: Sólo con la declaración del operador de que el executor ya no existe.
    "finalize-crashed": frozenset({Phase.STARTED, Phase.EMITTING, Phase.DRAINING}),
    #: STARTED es abandonable aunque el executor siga vivo: armar exige el run abierto bajo
    #: el lock, así que tras el sello ya no puede emitir.
    "abandon": frozenset({Phase.CREATED, Phase.PREPARED, Phase.STARTED, Phase.STOPPED_PRE_T0}),
}


@dataclass
class RunState:
    run_id: str
    run_root: Path
    records: list[dict[str, Any]]
    payloads: dict[str, Any]
    unreadable: list[str] = field(default_factory=list)

    def has(self, artifact_type: str) -> bool:
        return any(record["artifact_type"] == artifact_type for record in self.records)

    def payload(self, artifact_type: str) -> Any:
        return self.payloads.get(artifact_type)

    def record(self, artifact_type: str) -> dict[str, Any] | None:
        for record in self.records:
            if record["artifact_type"] == artifact_type:
                return record
        return None

    @property
    def initial_manifest(self) -> dict[str, Any] | None:
        return self.payloads.get(ART_MANIFEST_INITIAL)

    @property
    def finalized(self) -> bool:
        return self.has(ART_MANIFEST_FINAL) or (self.run_root / ".s11-finalized").exists()


def load_state(store: EvidenceStore, run_id: str) -> RunState:
    run_root = store.initialize_run(run_id)
    records = store.records(run_id)
    payloads: dict[str, Any] = {}
    unreadable: list[str] = []
    for record in records:
        if record.get("mime_type") != "application/json":
            continue
        path = (run_root / record["relative_path"]).resolve()
        try:
            if run_root not in path.parents or path.is_symlink():
                raise OSError("ruta insegura")
            payloads[record["artifact_type"]] = _json(path.read_bytes())
        except (OSError, ValueError):
            unreadable.append(record["artifact_type"])
    return RunState(run_id, run_root, records, payloads, unreadable)


def _json(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"))


def raw_pre_t0(state: RunState) -> bool:
    """El resultado crudo prueba que no hubo t0 ni ningún envío: nada se midió."""
    raw = state.payload(ART_RAW)
    if not isinstance(raw, Mapping) or raw.get("t0_ns") is not None:
        return False
    attempts = raw.get("attempts")
    return isinstance(attempts, list) and all(
        isinstance(a, Mapping) and a.get("send_count") == 0 for a in attempts
    )


def phase_of(state: RunState) -> Phase:
    decision = state.payload(ART_DECISION)
    if state.finalized:
        lifecycle = decision.get("lifecycle") if isinstance(decision, Mapping) else None
        # Un run sellado sin una decisión EX-10 legible no demuestra nada.
        return Phase(lifecycle) if lifecycle in TERMINAL_PHASES else Phase.NOT_DEMONSTRATED
    if decision is not None:
        return Phase.DECIDED
    if state.has(ART_DRAIN):
        return Phase.RECONCILING
    if state.has(ART_RAW) or state.has(ART_RAW_REJECTED):
        return Phase.DRAINING
    if state.has(ART_EMISSION_ABORTED):
        return Phase.STOPPED_PRE_T0
    if state.has(ART_ARMED):
        return Phase.EMITTING
    if state.has(ART_EMISSION):
        return Phase.STARTED
    if state.has(ART_START):
        return Phase.PREPARED
    return Phase.CREATED


def _require_open_run(state: RunState) -> None:
    manifest = state.initial_manifest
    if manifest is None:
        raise Ex10ExecutorError("El run no tiene manifest inicial de EX-10.")
    if manifest.get("scenario_id") != SCENARIO_ID:
        raise Ex10ExecutorError("El run no pertenece a EX-10.")
    if state.unreadable:
        raise Ex10Refusal(
            [f"artifact-unreadable:{name}" for name in state.unreadable], terminal=True
        )
    if state.finalized:
        raise Ex10Refusal(["run-finalized"], terminal=True)


def _require_phase(state: RunState, step: str) -> Phase:
    phase = phase_of(state)
    if phase not in ALLOWED[step]:
        # Una emisión ya intentada no se repite nunca: no hay reenvío ni emisión parcial. Y un
        # run que no está en PREPARED ya no llegará a estarlo.
        terminal = step == "execute"
        raise Ex10Refusal([f"phase:{phase}:{step}-not-allowed"], terminal=terminal)
    return phase


def _evidence_mode(manifest: Mapping[str, Any]) -> str | None:
    for mode, experiment_id in EXPERIMENT_IDS.items():
        if manifest.get("experiment_id") == experiment_id:
            return mode
    return None


# --- Primer run válido: una sola decisión PASS/FAIL oficial ----------------------------------


@dataclass(frozen=True, slots=True)
class PriorRun:
    run_id: str
    evidence_mode: str | None
    finalized: bool
    decision: str | None
    problems: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "evidence_mode": self.evidence_mode,
            "finalized": self.finalized,
            "decision": self.decision,
            "problems": list(self.problems),
        }


def prior_ex10_runs(store: EvidenceStore) -> list[PriorRun]:
    """Todos los runs de EX-10 de la raíz, verificados. Nunca se ignora uno ilegible."""
    runs_dir = store.root / "runs"
    if not runs_dir.is_dir():
        return []
    found = []
    for child in sorted(runs_dir.iterdir()):
        # Cualquier rastro de EX-10 cuenta: borrar `metadata/` no esconde un run previo.
        if not any(os.path.lexists(child / kind.value / SCENARIO_ID) for kind in ArtifactKind):
            continue
        if child.is_symlink() or not child.is_dir() or not valid_run_id(child.name):
            found.append(PriorRun(child.name, None, False, None, ("run-directory-invalid",)))
            continue
        problems: list[str] = []
        try:
            state = load_state(store, child.name)
        except (OSError, ValueError):
            found.append(PriorRun(child.name, None, False, None, ("records-unreadable",)))
            continue
        manifest = state.initial_manifest
        mode = None if manifest is None else _evidence_mode(manifest)
        if manifest is None or manifest.get("scenario_id") != SCENARIO_ID or mode is None:
            problems.append("initial-manifest")
        problems.extend(f"custody:{error}" for error in store.verify(child.name))
        problems.extend(f"unreadable:{name}" for name in state.unreadable)
        decision_payload = state.payload(ART_DECISION)
        decision = (
            decision_payload.get("decision") if isinstance(decision_payload, Mapping) else None
        )
        if state.finalized and decision not in {d.value for d in Decision}:
            problems.append("finalized-without-ex10-decision")
        found.append(PriorRun(child.name, mode, state.finalized, decision, tuple(problems)))
    return found


def binding_refusal(
    prior: Sequence[PriorRun], *, evidence_mode: str, acknowledged: Iterable[str]
) -> tuple[list[str], bool]:
    """Motivos para no abrir un run nuevo y si son definitivos (primer run válido).

    Un run oficial `PASS` o `FAIL` es el resultado de EX-10: ninguno oficial más. Un run
    abierto (de cualquier modo) impide otro. Tras runs oficiales `NOT_DEMONSTRATED`, un
    nuevo intento sólo si el operador los reconoce uno a uno: nunca automáticamente.
    """
    acknowledged = set(acknowledged)
    reasons: list[str] = []
    terminal = False
    for run in prior:
        if run.problems:
            reasons.append(f"prior-run-unverifiable:{run.run_id}")
        elif not run.finalized:
            reasons.append(f"active-run:{run.run_id}")
        elif run.evidence_mode == OFFICIAL_MODE and evidence_mode == OFFICIAL_MODE:
            if run.decision in {Decision.PASS, Decision.FAIL}:
                reasons.append(f"binding-run-exists:{run.run_id}")
                terminal = True
            elif run.run_id not in acknowledged:
                reasons.append(f"not-demonstrated-unacknowledged:{run.run_id}")
    known = {
        run.run_id
        for run in prior
        if run.evidence_mode == OFFICIAL_MODE and run.decision == Decision.NOT_DEMONSTRATED
    }
    reasons.extend(f"acknowledged-unknown:{run_id}" for run_id in sorted(acknowledged - known))
    return reasons, terminal


# --- Precondiciones --------------------------------------------------------------------------


def provider_problems() -> list[str]:
    """EX-10 usa el fake determinista: ningún proveedor real puede ser el efectivo."""
    problems = []
    if getattr(settings, "S11_FAKE_AI_ENABLED", False) is not True:
        problems.append("fake-ai-disabled")
    if ex10_worker_probe.effective_ai_adapter() != ex10_load.AI_MODEL_NAME:
        problems.append("ai-adapter-not-s11-fake")
    return problems


def executor_source_problems(source_root: Path) -> list[str]:
    """El código que corre es, byte a byte, el del checkout (limpio) del preflight."""
    repository = ex10_preflight.GitRepository(source_root)
    running = Path(settings.BASE_DIR)
    problems = []
    for relative in EXECUTOR_SOURCES:
        checked_out = repository.read(relative)
        try:
            actual = (running / relative.removeprefix("backend/")).read_bytes()
        except OSError:
            actual = None
        if checked_out is None or actual is None:
            problems.append(f"executor-source-missing:{relative}")
        elif checked_out != actual:
            problems.append(f"executor-source-mismatch:{relative}")
    return problems


def executor_fingerprint() -> str:
    running = Path(settings.BASE_DIR)
    digest = {}
    for relative in EXECUTOR_SOURCES:
        try:
            digest[relative] = sha256_bytes(
                (running / relative.removeprefix("backend/")).read_bytes()
            )
        except OSError:
            digest[relative] = None
    return sha256_bytes(canonical_json_bytes(digest))


def _ns(delta: timedelta) -> int:
    return delta // timedelta(microseconds=1) * 1_000


def preflight_age_problem(checked_at_ns: int | None, now_ns: int | None) -> str | None:
    """La frescura del preflight, igual en START y al emitir: `0 ≤ edad ≤ PREFLIGHT_MAX_AGE`."""
    if checked_at_ns is None or now_ns is None:
        return "checked-at"
    if checked_at_ns - now_ns > _ns(PREFLIGHT_FUTURE_TOLERANCE):
        return "checked-at-future"
    if now_ns - checked_at_ns > _ns(PREFLIGHT_MAX_AGE):
        return "stale"
    return None


def preflight_problems(report: Any, *, git_sha: str, now: datetime) -> list[str]:
    """El preflight operacional debe ser un PASS completo del mismo HEAD y reciente."""
    if not isinstance(report, Mapping):
        return ["preflight:not-a-report"]
    problems = []
    expected = {
        "schema_version": ex10_preflight.SCHEMA_VERSION,
        "scenario_id": SCENARIO_ID,
        "protocol_version": ex10_load.PROTOCOL_ID,
        "methodology_version": ex10_load.METHODOLOGY_VERSION,
        "result": "PASS",
        "gate": "READY_FOR_EXECUTOR_INTEGRATION",
        "ready_for_execution": False,
        "run_id": None,
        "evidence_written": False,
        "report_posts": 0,
        "provider_calls": 0,
        "task_publishes": 0,
        "health_requests": 1,
        "control_probes": 2,
        "git_sha": git_sha,
        "expected_git_sha": git_sha,
    }
    for key, value in expected.items():
        if report.get(key) != value or type(report.get(key)) is not type(value):
            problems.append(f"preflight:{key}")
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        problems.append("preflight:checks")
    else:
        names = {c.get("check") for c in checks if isinstance(c, Mapping)}
        if [c for c in checks if not isinstance(c, Mapping) or c.get("result") != "PASS"]:
            problems.append("preflight:check-not-pass")
        missing = set(ex10_preflight.REQUIRED_CHECKS) - {"ready_state"} - names
        if missing or not any(str(n).startswith("ready_state:") for n in names):
            problems.append("preflight:missing-required-check")
    age = preflight_age_problem(
        ex10_reconcile.utc_ns(report.get("checked_at")), ex10_reconcile.datetime_ns(now)
    )
    if age:
        problems.append(f"preflight:{age}")
    return problems


def run_identity(
    *, git_sha: str, evidence_mode: str, plan: LoadPlan, fingerprint: str
) -> dict[str, Any]:
    return {
        "schema_version": "s11c3-ex10-identity-v1",
        "protocol": ex10_load.protocol_identity(),
        "evidence_mode": evidence_mode,
        "experiment_id": EXPERIMENT_IDS[evidence_mode],
        "git_sha": git_sha,
        "plan_sha256": plan.sha256,
        "executor_version": EXECUTOR_VERSION,
        "executor_fingerprint": fingerprint,
        "preflight_max_age_seconds": int(PREFLIGHT_MAX_AGE.total_seconds()),
        "drain": {
            "poll_interval_ns": DRAIN_POLL_NS,
            "extension_ns": DRAIN_EXTENSION_NS,
            "basis": "max(window_end, last_attempt_terminal)",
        },
    }


# --- Probes ----------------------------------------------------------------------------------


def observe_config(*, control: Any | None = None, nonce: str | None = None) -> ConfigObservation:
    """`CFG_ASYNC`: el API de este mismo proceso y el worker por el control plane.

    Un probe de inspección, como los del preflight (MD-08): nunca publica una tarea.
    """
    from config import celery_app

    api = ex10_preflight.real_api_config()
    control = control or ex10_preflight.CeleryWorkerControl()
    nonce = nonce or secrets.token_hex(16)
    facts: list[dict[str, Any]] | None
    try:
        replies = control.probe(nonce, WORKER_PROBE_TIMEOUT_SECONDS)
        facts = [ex10_worker_probe.validate_reply(reply, nonce) for reply in replies]
    except (ex10_preflight.ControlUnavailable, ex10_worker_probe.ReplyRejected):
        facts = None
    single = facts[0] if facts is not None and len(facts) == 1 else None
    return ConfigObservation(
        api_task_always_eager=bool(api.task_always_eager),
        broker_url=ex10_reconcile.broker_label(celery_app.conf.broker_url),
        worker_nodes=None if facts is None else len(facts),
        worker_max_concurrency=None if single is None else single["pool"]["max_concurrency"],
        worker_task_always_eager=None if single is None else single["task_always_eager"],
        worker_fake_ai_enabled=None if single is None else single["fake_ai_enabled"],
        worker_ai_adapter=None if single is None else single["ai_adapter"],
    )


def inspect_ready_state(dataset: LoadDataset) -> dict[str, Any]:
    from . import ex10_prepare, ex10_state

    environment = ex10_prepare.readonly_environment()
    try:
        return ex10_state.inspect_ready_state(environment, dataset=dataset).to_json()
    finally:
        ex10_prepare._close(environment.redis)


def broker_dynamic() -> int:
    """Claves dinámicas y mensajes de tarea en el broker (0 tras un drain completo)."""
    from . import ex10_prepare

    environment = ex10_prepare.readonly_environment()
    try:
        snapshot = ex10_preflight.classify_broker(environment.redis)
    finally:
        ex10_prepare._close(environment.redis)
    return snapshot.dynamic + snapshot.task_messages + len(snapshot.unclassifiable)


def resolve_backend() -> tuple[str, int]:
    """La IP del servicio `backend` antes de la barrera: resolver dentro de la medición no."""
    host, _, port = ex10_load.HOST_HEADER.partition(":")
    answers = socket.getaddrinfo(host, int(port), family=socket.AF_INET, type=socket.SOCK_STREAM)
    return answers[0][4][0], int(port)


def read_boot_id() -> str | None:
    """Identifica el arranque del kernel: sólo en él son comparables dos relojes monotónicos."""
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        return None
    return value if re.fullmatch(r"[0-9a-f-]{36}", value) else None


def default_preflight(options: ex10_preflight.PreflightOptions) -> Mapping[str, Any]:
    return ex10_preflight.run_preflight(options).to_json()


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class ExecutorProbes:
    """Todo lo que el executor observa o usa; los tests sustituyen cada pieza."""

    preflight: Callable[[ex10_preflight.PreflightOptions], Mapping[str, Any]] = default_preflight
    provider: Callable[[], list[str]] = provider_problems
    sources: Callable[[Path], list[str]] = executor_source_problems
    fingerprint: Callable[[], str] = executor_fingerprint
    config: Callable[[], ConfigObservation] = observe_config
    ready_state: Callable[[LoadDataset], Mapping[str, Any]] = inspect_ready_state
    fixture_result: Callable[..., Any] = ex10_preflight.real_fixture_result
    issue_tokens: Callable[..., Any] = ex10_fixture.issue_tokens
    verify_tokens: Callable[..., Any] = ex10_fixture.verify_tokens
    category_ids: Callable[[set[str]], Mapping[str, str]] = ex10_preflight.real_category_ids
    resolve_backend: Callable[[], tuple[str, int]] = resolve_backend
    transport: Callable[[tuple[str, int]], ex10_load.Transport] = ex10_load.StdlibHttpTransport
    generator: Callable[..., ex10_load.LoadGenerator] = ex10_load.LoadGenerator
    database: Callable[[], ex10_reconcile.DatabaseSnapshot] = ex10_reconcile.snapshot_database
    broker_dynamic: Callable[[], int] = broker_dynamic
    now: Callable[[], datetime] = _now
    monotonic_ns: Callable[[], int] = time.monotonic_ns
    wall_ns: Callable[[], int] = time.time_ns
    sleep: Callable[[float], None] = time.sleep
    boot_id: Callable[[], str | None] = read_boot_id
    dataset: Callable[[], LoadDataset] = ex10_load.load_dataset


# --- Persistencia --------------------------------------------------------------------------


_KINDS = {
    ART_PREFLIGHT: (ArtifactKind.METADATA, Classification.INTERNAL),
    ART_START: (ArtifactKind.METADATA, Classification.INTERNAL),
    ART_PLAN: (ArtifactKind.METADATA, Classification.INTERNAL),
    ART_EMISSION: (ArtifactKind.METADATA, Classification.INTERNAL),
    ART_EMISSION_ABORTED: (ArtifactKind.METADATA, Classification.INTERNAL),
    ART_ARMED: (ArtifactKind.METADATA, Classification.INTERNAL),
    ART_RAW: (ArtifactKind.RAW, Classification.EXPERIMENTAL),
    ART_RAW_REJECTED: (ArtifactKind.METADATA, Classification.INTERNAL),
    ART_DRAIN: (ArtifactKind.METADATA, Classification.INTERNAL),
    ART_RECONCILIATION: (ArtifactKind.PROCESSED, Classification.EXPERIMENTAL),
    ART_CALCULATION: (ArtifactKind.DERIVED, Classification.EXPERIMENTAL),
    ART_DECISION: (ArtifactKind.DERIVED, Classification.EXPERIMENTAL),
}


def _persist(
    store: EvidenceStore,
    run_id: str,
    artifact_type: str,
    payload: Any,
    *,
    source: str,
    dataset: LoadDataset,
    secret_values: Sequence[str] = (),
):
    # Cada payload se revisa antes de escribirse: ni tokens, ni coordenadas, ni imágenes.
    findings = ex10_load.sensitive_findings(payload, dataset=dataset, secrets=secret_values)
    if findings:
        raise Ex10ExecutorError(f"Contenido sensible en {artifact_type}: {findings}")
    kind, classification = _KINDS[artifact_type]
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


@contextmanager
def _deferred_interrupts() -> Iterator[None]:
    """Un checkpoint no se corta a medias: SIGINT, SIGTERM y SIGHUP se entregan al acabar."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    received: list[int] = []
    numbers = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = {number: signal.getsignal(number) for number in numbers}
    for number in numbers:
        signal.signal(number, lambda signum, frame: received.append(signum))
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, signal.SIG_DFL if handler is None else handler)
    if received:
        raise KeyboardInterrupt


@contextmanager
def _checkpoint(store: EvidenceStore) -> Iterator[None]:
    """Un checkpoint custodiado: el lock de custodia y, dentro, ninguna interrupción."""
    with _deferred_interrupts(), store.mutation():
        yield


def _persist_once(
    store: EvidenceStore,
    state: RunState,
    artifact_type: str,
    payload: Any,
    *,
    source: str,
    dataset: LoadDataset,
) -> str:
    """Persiste un artefacto de la finalización o, si una finalización interrumpida ya lo
    custodió, exige que sea idéntico byte a byte al recalculado. Nunca lo reemplaza."""
    record = state.record(artifact_type)
    if record is None:
        return _persist(
            store, state.run_id, artifact_type, payload, source=source, dataset=dataset
        ).sha256
    if record["sha256"] != sha256_bytes(canonical_json_bytes(redact(payload))):
        raise Ex10ExecutorError(f"{artifact_type} custodiado difiere del recalculado.")
    return record["sha256"]


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _event(schema: str, *, run_id: str, recorded_at: datetime, **fields: Any) -> dict[str, Any]:
    return {
        "schema_version": schema,
        "scenario_id": SCENARIO_ID,
        "run_id": run_id,
        "recorded_at": _utc(recorded_at),
        **fields,
    }


# --- START -----------------------------------------------------------------------------------


def start_run(
    store: EvidenceStore,
    *,
    git_sha: str,
    source_root: Path,
    nonce: str,
    evidence_mode: str,
    timezone: str,
    acknowledged_not_demonstrated: Iterable[str] = (),
    probes: ExecutorProbes | None = None,
) -> dict[str, Any]:
    """Preflight en proceso, precondiciones y la regla del primer run; luego el run_id.

    Nada se escribe si algo falla. El preflight corre antes de abrir la evidencia: su guarda
    protege la raíz de evidencia y lo prohíbe escribir.
    """
    probes = probes or ExecutorProbes()
    if evidence_mode not in EXPERIMENT_IDS:
        raise Ex10Refusal(["evidence-mode"], terminal=True)
    if not _SHA1.fullmatch(git_sha) or not _NONCE.fullmatch(nonce):
        raise Ex10Refusal(["git-sha-or-nonce-format"], terminal=True)
    dataset = probes.dataset()
    plan = ex10_load.build_plan(dataset)
    problems = list(probes.provider())
    problems.extend(
        f"protocol:{reason}"
        for reason in ex10_load.verify_protocol_identity(ex10_load.protocol_identity())
    )
    if plan.sha256 != ex10_load.PLAN_SHA256:
        problems.append("plan-sha256")
    report = probes.preflight(ex10_preflight.PreflightOptions(git_sha, source_root))
    now = probes.now()
    problems.extend(preflight_problems(report, git_sha=git_sha, now=now))
    # Se comprueba antes de escribir nada: un rechazo aquí no deja un run a medio crear.
    if isinstance(report, Mapping) and ex10_load.sensitive_findings(report, dataset=dataset):
        problems.append("preflight:sensitive-content")
    problems.extend(probes.sources(source_root))
    if problems:
        raise Ex10Refusal(problems, terminal=False)
    preflight_sha = sha256_bytes(canonical_json_bytes(report))
    with _checkpoint(store):
        prior = prior_ex10_runs(store)
        reasons, terminal = binding_refusal(
            prior, evidence_mode=evidence_mode, acknowledged=acknowledged_not_demonstrated
        )
        if reasons:
            raise Ex10Refusal(reasons, terminal=terminal)
        identity = run_identity(
            git_sha=git_sha,
            evidence_mode=evidence_mode,
            plan=plan,
            fingerprint=probes.fingerprint(),
        )
        manifest = create_manifest(
            repo_root=Path("."),
            started_at=now,
            environment=ENVIRONMENT_NAME,
            timezone=timezone,
            seed=ex10_load.DATASET_SEED,
            dataset_id=ex10_load.DATASET_ID,
            dataset_sha256=dataset.sha256,
            scenario_id=SCENARIO_ID,
            experiment_id=EXPERIMENT_IDS[evidence_mode],
            execution_nonce=nonce,
            configuration=identity,
            expected_git_sha=git_sha,
            supplied_git_sha=git_sha,
            supplied_git_clean=True,
        )
        run_id = manifest.run_id
        if store.records(run_id):
            raise Ex10Refusal(["run-id-reused"], terminal=True)
        initial = store.persist_json(
            run_id=run_id,
            scenario_id=SCENARIO_ID,
            artifact_type=ART_MANIFEST_INITIAL,
            kind=ArtifactKind.METADATA,
            classification=Classification.INTERNAL,
            filename="run-manifest.initial.json",
            payload=manifest.to_dict(),
            source="s11_ex10.start",
        )
        _persist(store, run_id, ART_PREFLIGHT, report, source="s11_ex10.start", dataset=dataset)
        _persist(store, run_id, ART_PLAN, plan.to_json(), source="s11_ex10.start", dataset=dataset)
        start = _event(
            "s11c3-ex10-start-v1",
            run_id=run_id,
            recorded_at=now,
            evidence_mode=evidence_mode,
            git_sha=git_sha,
            plan_sha256=plan.sha256,
            dataset_sha256=dataset.sha256,
            preflight={
                "sha256": preflight_sha,
                "checked_at": report["checked_at"],
                "result": report["result"],
                "gate": report["gate"],
                "checks": len(report["checks"]),
            },
            executor_sources={"files": len(EXECUTOR_SOURCES), "match": True},
            executor_fingerprint=identity["executor_fingerprint"],
            prior_ex10_runs=[run.to_json() for run in prior],
            acknowledged_not_demonstrated=sorted(set(acknowledged_not_demonstrated)),
        )
        record = _persist(store, run_id, ART_START, start, source="s11_ex10.start", dataset=dataset)
    return {
        "run_id": run_id,
        "scenario_id": SCENARIO_ID,
        "evidence_mode": evidence_mode,
        "phase": str(Phase.PREPARED),
        "manifest_sha256": initial.sha256,
        "start_sha256": record.sha256,
        "preflight_sha256": preflight_sha,
    }


# --- EXECUTE: emisión y drain ------------------------------------------------------------------


def run_exists(store: EvidenceStore, run_id: str) -> bool:
    """Sin crear nada: `load_state` inicializa el directorio de cualquier `run_id` válido."""
    path = store.root / "runs" / run_id
    return valid_run_id(run_id) and path.is_dir() and not path.is_symlink()


def _open_state(store: EvidenceStore, run_id: str, git_sha: str) -> RunState:
    if not run_exists(store, run_id):
        raise Ex10Refusal(["run-not-found"], terminal=True)
    state = load_state(store, run_id)
    _require_open_run(state)
    if state.initial_manifest.get("git_sha") != git_sha:
        raise Ex10Refusal(["git-sha-differs-from-start"], terminal=True)
    return state


def _pre_emission_problems(
    state: RunState, probes: ExecutorProbes, dataset: LoadDataset
) -> tuple[list[str], ConfigObservation, Mapping[str, Any]]:
    problems = list(probes.provider())
    start = state.payload(ART_START) or {}
    if probes.fingerprint() != start.get("executor_fingerprint"):
        problems.append("executor-code-changed-since-start")
    checked_at = ex10_reconcile.utc_ns((start.get("preflight") or {}).get("checked_at"))
    if preflight_age_problem(checked_at, ex10_reconcile.datetime_ns(probes.now())):
        problems.append("preflight-stale")
    ready = probes.ready_state(dataset)
    if (
        not isinstance(ready, Mapping)
        or ready.get("state") != "READY_STATE"
        or ready.get("overall") != "PASS"
    ):
        problems.append("ready-state")
    config = probes.config()
    config_evidence, config_problems = ex10_reconcile.async_config(config, config)
    problems.extend(config_problems)
    if config_evidence is not None:
        problems.extend(f.code for f in ex10_load.evaluate_async_config(config_evidence) if f.code)
    return problems, config, ready


def execute_run(
    store: EvidenceStore,
    *,
    run_id: str,
    git_sha: str,
    probes: ExecutorProbes | None = None,
) -> dict[str, Any]:
    """Emite los 50 intentos y drena, con un checkpoint custodiado entre cada etapa.

    Antes del marcador de emisión nada es irreversible y un rechazo no gasta el run. Tras él
    no hay vuelta atrás: la emisión no se repite, ni entera ni en parte.
    """
    probes = probes or ExecutorProbes()
    dataset = probes.dataset()
    plan = ex10_load.build_plan(dataset)
    state = _open_state(store, run_id, git_sha)
    _require_phase(state, "execute")
    problems, config_before, ready = _pre_emission_problems(state, probes, dataset)
    if problems:
        raise Ex10Refusal(problems, terminal=False)
    boot_id = probes.boot_id()
    try:
        with _checkpoint(store):
            state = _open_state(store, run_id, git_sha)
            _require_phase(state, "execute")
            marker = _event(
                "s11c3-ex10-emission-v1",
                run_id=run_id,
                recorded_at=probes.now(),
                boot_id=boot_id,
                config_before=config_before.to_json(),
                ready_state={"state": ready.get("state"), "overall": ready.get("overall")},
            )
            _persist(
                store, run_id, ART_EMISSION, marker, source="s11_ex10.execute", dataset=dataset
            )
    except KeyboardInterrupt:
        # Diferida hasta custodiar el marcador: no hubo barrera, es una detención antes de t0.
        return _stop_pre_t0(store, run_id, git_sha, ["operator-interrupted"], probes, dataset)

    # Punto de no retorno. Hasta la barrera, un fallo detiene la emisión sin t0 ni envíos.
    secret_values: tuple[str, ...] = ()
    prepared: dict[str, ex10_load.PreparedRequest] = {}
    try:
        try:
            specs = ex10_fixture.actor_specs(dataset)
            result = probes.fixture_result(specs)
            tokens = probes.issue_tokens(result)
            try:
                checks = probes.verify_tokens(tokens, result, now=probes.now())
                if len(checks) != ex10_load.VIRTUAL_USERS or not all(c.ok for c in checks):
                    raise _PreT0Stop("tokens-not-verified")
                mapping = tokens.mapping()
                secret_values = tuple(mapping.values())
                category_ids = probes.category_ids({item.category_slug for item in plan.items})
                prepared = ex10_load.prepare_requests(
                    dataset, plan, run_id=run_id, tokens=mapping, category_ids=category_ids
                )
            finally:
                # Los tokens sólo viven en memoria y sólo hasta tener los cuerpos codificados.
                mapping = None
                tokens.release()
            transport = probes.transport(probes.resolve_backend())
            generator = probes.generator(plan, prepared, transport)
            _arm_emission(store, run_id, git_sha, probes, dataset)
        except _PreT0Stop as stop:
            return _stop_pre_t0(store, run_id, git_sha, [stop.code], probes, dataset)
        except KeyboardInterrupt:
            return _stop_pre_t0(store, run_id, git_sha, ["operator-interrupted"], probes, dataset)
        except Ex10Refusal:
            # El run se cerró desde fuera (un abandono): no hay nada que registrar ni emitir.
            raise
        except Exception as exc:  # noqa: BLE001 - sólo el tipo, nunca el mensaje
            return _stop_pre_t0(
                store, run_id, git_sha, [f"pre-barrier:{type(exc).__name__}"], probes, dataset
            )
        run = generator.run()
        # El generador guarda su copia de las peticiones (con el JWT): se suelta ya.
        generator = transport = None
    finally:
        prepared.clear()
    raw = ex10_load.raw_results_payload(plan, run)
    with _checkpoint(store):
        state = _open_state(store, run_id, git_sha)
        if phase_of(state) is not Phase.EMITTING:
            raise Ex10ExecutorError("El run cambió de fase durante la emisión.")
        findings = ex10_load.sensitive_findings(raw, dataset=dataset, secrets=secret_values)
        if findings:
            rejected = _event(
                "s11c3-ex10-raw-rejected-v1",
                run_id=run_id,
                recorded_at=probes.now(),
                findings=findings,
                attempts=len(run.attempts),
            )
            _persist(
                store,
                run_id,
                ART_RAW_REJECTED,
                rejected,
                source="s11_ex10.execute",
                dataset=dataset,
            )
        else:
            _persist(
                store,
                run_id,
                ART_RAW,
                raw,
                source="s11_ex10.execute",
                dataset=dataset,
                secret_values=secret_values,
            )
    secret_values = ()
    drain = run_drain_step(store, run_id=run_id, git_sha=git_sha, probes=probes, run=run, plan=plan)
    return {
        "run_id": run_id,
        "phase": str(Phase.RECONCILING),
        "t0_published": run.t0_ns is not None,
        "interrupted": run.interrupted,
        "attempts": len(run.attempts),
        "sent": sum(1 for a in run.attempts if a.send_count),
        "apparatus_faults": len(run.faults),
        "raw_rejected": bool(findings),
        "drain": {key: drain[key] for key in ("outcome", "polls", "uninspected_polls")},
    }


def _arm_emission(
    store: EvidenceStore, run_id: str, git_sha: str, probes: ExecutorProbes, dataset: LoadDataset
) -> None:
    """El último checkpoint antes de la barrera: separa «nada enviado» de «quizá emitido».

    Se re-comprueba la fase bajo el lock: un run abandonado mientras se preparaban los tokens
    ya no se arma y, por tanto, no emite.
    """
    with _checkpoint(store):
        state = _open_state(store, run_id, git_sha)
        if phase_of(state) is not Phase.STARTED:
            raise Ex10Refusal([f"phase:{phase_of(state)}:arm-not-allowed"], terminal=True)
        payload = _event(
            "s11c3-ex10-emission-armed-v1",
            run_id=run_id,
            recorded_at=probes.now(),
            requests_prepared=ex10_load.TOTAL_REQUESTS,
            tokens_verified=ex10_load.VIRTUAL_USERS,
        )
        _persist(store, run_id, ART_ARMED, payload, source="s11_ex10.execute", dataset=dataset)


class _PreT0Stop(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _stop_pre_t0(
    store: EvidenceStore,
    run_id: str,
    git_sha: str,
    reasons: list[str],
    probes: ExecutorProbes,
    dataset: LoadDataset,
) -> dict[str, Any]:
    # Sólo se llama antes de `generator.run()`: incluso armado, nada se envió todavía.
    with _checkpoint(store):
        state = _open_state(store, run_id, git_sha)
        if phase_of(state) not in {Phase.STARTED, Phase.EMITTING}:
            raise Ex10ExecutorError("El run cambió de fase antes de la barrera.")
        payload = _event(
            "s11c3-ex10-emission-aborted-v1",
            run_id=run_id,
            recorded_at=probes.now(),
            pre_t0=True,
            t0_published=False,
            requests_sent=0,
            reasons=reasons,
        )
        _persist(
            store, run_id, ART_EMISSION_ABORTED, payload, source="s11_ex10.execute", dataset=dataset
        )
    return {"run_id": run_id, "phase": str(Phase.STOPPED_PRE_T0), "reasons": reasons}


# --- Drain ---------------------------------------------------------------------------------------


def drain_deadline(run: GeneratorRun) -> tuple[int, str] | None:
    """`max(window_end, fin del último intento) + 300 s` en el reloj monotónico del run."""
    if run.t0_ns is None:
        return None
    window_end = run.t0_ns + ex10_load.WINDOW_NS
    ends = [a.t_end_ns for a in run.attempts if a.t_end_ns is not None]
    last = max(ends) if ends else None
    if last is not None and last > window_end:
        return last + DRAIN_EXTENSION_NS, "last_attempt"
    return window_end + DRAIN_EXTENSION_NS, "window_end"


@dataclass(frozen=True, slots=True)
class DrainObservation:
    inspected: bool
    http_missing: tuple[str, ...] = ()
    queued_not_terminal: tuple[str, ...] = ()
    accepted_not_queued: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def settled(self) -> bool:
        return self.inspected and not (
            self.http_missing or self.queued_not_terminal or self.accepted_not_queued
        )


def observe_drain(
    plan: LoadPlan,
    run: GeneratorRun,
    *,
    http: Stream,
    celery: Stream,
    database_ok: bool,
) -> DrainObservation:
    """Sólo lectura: telemetría de los requests respondidos y de toda tarea encolada."""
    problems = [*http.problems, *celery.problems]
    if not database_ok:
        problems.append("database:unavailable")
    finished = ex10_reconcile.http_finished_request_ids(http)
    tasks = ex10_reconcile.terminal_request_ids(celery)
    if finished is None or tasks is None or not database_ok:
        return DrainObservation(False, problems=tuple(problems))
    queued, terminal = tasks
    by_id = {item.request_id: item.request_key for item in plan.items}
    responded = ex10_reconcile.responded_request_ids(plan, run.attempts)
    accepted = {
        plan.by_key()[a.request_key].request_id
        for a in run.attempts
        if a.outcome is AttemptOutcome.HTTP_ACCEPTED
    }
    return DrainObservation(
        True,
        http_missing=tuple(sorted(by_id.get(r, "unknown") for r in responded - finished)),
        queued_not_terminal=tuple(sorted(by_id.get(r, "unknown") for r in queued - terminal)),
        accepted_not_queued=tuple(sorted(by_id.get(r, "unknown") for r in accepted - queued)),
        problems=tuple(problems),
    )


def _read_stream(state: RunState, name: str, run_id: str) -> Stream:
    path = state.run_root / "logs" / SCENARIO_ID / f"{name}.jsonl"
    if not os.path.lexists(path):
        return ex10_reconcile.parse_stream(None, name=name, run_id=run_id)
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("stream inseguro")
        raw = path.read_bytes()
    except OSError:
        return ex10_reconcile.parse_stream(None, name=name, run_id=run_id, readable=False)
    return ex10_reconcile.parse_stream(raw, name=name, run_id=run_id)


def run_drain(
    plan: LoadPlan,
    run: GeneratorRun,
    *,
    observe: Callable[[], DrainObservation],
    probes: ExecutorProbes,
    same_boot: bool,
    t0_utc_ns: int | None,
) -> dict[str, Any]:
    """Sondea cada 1 s exacto hasta que todo esté asentado o venza el plazo. No envía nada.

    El sondeo es de liveness y completitud: no interviene en P50, P99 ni throughput. Sin
    backoff ni intervalo dinámico. Si el reloj monotónico del run ya no es comparable (otro
    arranque del kernel), el plazo se lleva al reloj de pared con el mismo desplazamiento.
    """
    deadline = drain_deadline(run)
    if deadline is None:
        return {
            "outcome": "NO_T0",
            "polls": 0,
            "uninspected_polls": 0,
            "deadline": None,
            "pending_at_end": None,
            "final_poll_inspected": None,
        }
    deadline_ns, basis = deadline
    deadline_utc_ns = None if t0_utc_ns is None else t0_utc_ns + (deadline_ns - run.t0_ns)
    if same_boot:
        now = probes.monotonic_ns
        target = deadline_ns
    else:
        now = probes.wall_ns
        target = deadline_utc_ns if deadline_utc_ns is not None else probes.wall_ns()
    started = now()
    polls = uninspected = 0
    observation = DrainObservation(False)
    while True:
        observation = observe()
        polls += 1
        uninspected += not observation.inspected
        if observation.settled:
            outcome = "COMPLETE"
            break
        current = now()
        if current >= target:
            outcome = "TIMEOUT"
            break
        # Siguiente sondeo en `started + polls × 1 s`, nunca antes del plazo más un sondeo.
        next_poll = min(started + polls * DRAIN_POLL_NS, target)
        probes.sleep(max(0, next_poll - current) / 1e9)
    return {
        "outcome": outcome,
        "polls": polls,
        "uninspected_polls": uninspected,
        "deadline": {
            "basis": basis,
            "deadline_ns": deadline_ns,
            "deadline_utc_ns": deadline_utc_ns,
            "extension_ns": DRAIN_EXTENSION_NS,
            "poll_interval_ns": DRAIN_POLL_NS,
            "clock": "monotonic" if same_boot else "wall",
        },
        "final_poll_inspected": observation.inspected,
        "pending_at_end": {
            "http_finished_missing": list(observation.http_missing),
            "queued_not_terminal": list(observation.queued_not_terminal),
            "accepted_not_queued": list(observation.accepted_not_queued),
            "problems": list(observation.problems),
        },
    }


def run_drain_step(
    store: EvidenceStore,
    *,
    run_id: str,
    git_sha: str,
    probes: ExecutorProbes,
    run: GeneratorRun | None = None,
    plan: LoadPlan | None = None,
) -> dict[str, Any]:
    """Drain y `CFG_ASYNC` posterior; se custodia `ex10-drain`. Sin lock mientras sondea."""
    dataset = probes.dataset()
    plan = plan or ex10_load.build_plan(dataset)
    state = _open_state(store, run_id, git_sha)
    if phase_of(state) is not Phase.DRAINING:
        raise Ex10Refusal([f"phase:{phase_of(state)}:drain-not-allowed"], terminal=False)
    if run is None:
        run = generator_run_from_raw(state.payload(ART_RAW)) if state.has(ART_RAW) else None
    emission = state.payload(ART_EMISSION) or {}
    boot_id = probes.boot_id()
    same_boot = boot_id is not None and boot_id == emission.get("boot_id")

    def observe() -> DrainObservation:
        http = _read_stream(state, "http", run_id)
        celery = _read_stream(state, "celery", run_id)
        try:
            probes.database()
            database_ok = True
        except Exception:  # noqa: BLE001 - cualquier fallo es «no inspeccionado», nunca un crash
            database_ok = False
        return observe_drain(plan, run, http=http, celery=celery, database_ok=database_ok)

    if run is None:
        result = {
            "outcome": "RAW_MISSING",
            "polls": 0,
            "uninspected_polls": 0,
            "deadline": None,
            "pending_at_end": None,
            "final_poll_inspected": None,
        }
    else:
        result = run_drain(
            plan, run, observe=observe, probes=probes, same_boot=same_boot, t0_utc_ns=run.t0_utc_ns
        )
    try:
        config_after: dict[str, Any] | None = probes.config().to_json()
    except Exception:  # noqa: BLE001 - sin CFG_ASYNC posterior el run no demuestra HT1-04
        config_after = None
    payload = _event(
        "s11c3-ex10-drain-v1",
        run_id=run_id,
        recorded_at=probes.now(),
        boot_id=boot_id,
        same_boot=same_boot,
        requests_sent_during_drain=0,
        config_after=config_after,
        **result,
    )
    with _checkpoint(store):
        state = _open_state(store, run_id, git_sha)
        if phase_of(state) is not Phase.DRAINING:
            raise Ex10ExecutorError("El run cambió de fase durante el drain.")
        _persist(store, run_id, ART_DRAIN, payload, source="s11_ex10.drain", dataset=dataset)
    return payload


# --- Reconstrucción del resultado crudo -------------------------------------------------------


_ATTEMPT_FIELDS = frozenset(
    {
        "request_key",
        "request_id",
        "vu",
        "user_alias",
        "sequence_for_user",
        "state",
        "send_count",
        "outcome",
        "t_start_ns",
        "t_end_ns",
        "t_start_utc_ns",
        "t_end_utc_ns",
        "started_in_window",
        "completed_in_window",
        "duration_ns",
        "http_status",
        "minimal_response",
        "error_class",
        "error_detail_sanitized",
        "error_phase",
    }
)


def generator_run_from_raw(payload: Any) -> GeneratorRun:
    """El `GeneratorRun` custodiado en `ex10-load-raw`, tal cual. Nada se recalcula aquí:
    `apparatus_invalidity` rechaza cualquier derivado incoherente al evaluarlo."""
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != ex10_load.RAW_SCHEMA_VERSION
    ):
        raise Ex10ExecutorError("ex10-load-raw no tiene el esquema esperado.")
    attempts = []
    for item in payload["attempts"]:
        if not isinstance(item, Mapping) or set(item) != _ATTEMPT_FIELDS:
            raise Ex10ExecutorError("Registro de intento con campos inesperados.")
        attempts.append(
            AttemptRecord(
                **{
                    **item,
                    "state": ItemState(item["state"]),
                    "outcome": AttemptOutcome(item["outcome"]),
                }
            )
        )
    faults = tuple(
        ApparatusFault(FaultKind(f["kind"]), f["vu"], f["request_key"], f["detail"])
        for f in payload["apparatus_faults"]
    )
    return GeneratorRun(
        plan_sha256=payload["plan_sha256"],
        t0_ns=payload["t0_ns"],
        t0_utc_ns=payload["t0_utc_ns"],
        attempts=tuple(attempts),
        faults=faults,
        interrupted=bool(payload["interrupted"]),
    )


# --- RECONCILE y FINALIZE -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Assessment:
    """La evaluación completa de un run ya drenado, sin persistir nada."""

    evaluation: ex10_load.Ex10Evaluation
    reconciliation: ex10_reconcile.Reconciliation
    reconciliation_payload: dict[str, Any]
    streams: Mapping[str, Stream]
    invalidity: tuple[str, ...]


def _drain_invalidity(drain: Any) -> list[str]:
    if not isinstance(drain, Mapping):
        return ["drain:missing"]
    reasons = []
    outcome = drain.get("outcome")
    if outcome == "TIMEOUT":
        pending = drain.get("pending_at_end") or {}
        # Sólo lo que seguía en curso invalida: un 201 que nunca se encoló no se espera;
        # es un hecho que HT1-04 decide.
        if pending.get("http_finished_missing"):
            reasons.append("drain:timeout:http-finished-missing")
        if pending.get("queued_not_terminal"):
            reasons.append("drain:timeout:task-not-terminal")
        if not drain.get("final_poll_inspected"):
            reasons.append("drain:timeout:final-poll-uninspected")
    elif outcome not in {"COMPLETE", "NO_T0"}:
        reasons.append(f"drain:{outcome}")
    return reasons


def assess(
    state: RunState, *, probes: ExecutorProbes, dataset: LoadDataset, custody_errors: Sequence[str]
) -> Assessment:
    plan = ex10_load.build_plan(dataset)
    run = generator_run_from_raw(state.payload(ART_RAW))
    streams = {name: _read_stream(state, name, state.run_id) for name in STREAMS}
    # Un fallo de inspección, del tipo que sea, es evidencia no inspeccionada (NOT_DEMONSTRATED)
    # y deja el run cerrable: nunca un crash de la finalización. Sólo se guarda el tipo.
    probe_errors: list[str] = []
    try:
        database = probes.database()
    except Exception as exc:  # noqa: BLE001
        database = None
        probe_errors.append(f"probe:database:{type(exc).__name__}")
    try:
        dynamic: int | None = probes.broker_dynamic()
    except Exception as exc:  # noqa: BLE001
        dynamic = None
        probe_errors.append(f"probe:broker:{type(exc).__name__}")
    emission = state.payload(ART_EMISSION) or {}
    drain = state.payload(ART_DRAIN)
    before = ConfigObservation.from_json(emission.get("config_before"))
    after = ConfigObservation.from_json(
        drain.get("config_after") if isinstance(drain, Mapping) else None
    )
    reconciliation = ex10_reconcile.reconcile(
        plan,
        run,
        database=database,
        http=streams["http"],
        celery=streams["celery"],
        config_before=before,
        config_after=after,
        broker_dynamic=dynamic,
    )
    invalidity = [
        *reconciliation.environment_invalidity,
        *probe_errors,
        *_drain_invalidity(drain),
        *(f"custody:{error}" for error in custody_errors),
    ]
    if probes.fingerprint() != (state.payload(ART_START) or {}).get("executor_fingerprint"):
        invalidity.append("executor-code-changed-since-start")
    evaluation = ex10_load.evaluate_run(
        plan,
        run,
        persisted=reconciliation.persisted,
        async_evidence=reconciliation.async_evidence,
        environment_invalidity=tuple(dict.fromkeys(invalidity)),
    )
    payload = ex10_reconcile.reconciliation_payload(
        plan,
        run,
        reconciliation,
        run_id=state.run_id,
        successes=evaluation.successes,
        http=streams["http"],
        celery=streams["celery"],
        config_before=before,
        config_after=after,
        database=database,
    )
    return Assessment(
        evaluation, reconciliation, payload, streams, tuple(dict.fromkeys(invalidity))
    )


def reconcile_run(
    store: EvidenceStore, *, run_id: str, git_sha: str, probes: ExecutorProbes | None = None
) -> dict[str, Any]:
    """Sólo lectura y repetible: la decisión que resultaría, sin custodiarla."""
    probes = probes or ExecutorProbes()
    dataset = probes.dataset()
    state = _open_state(store, run_id, git_sha)
    _require_phase(state, "reconcile")
    if not state.has(ART_RAW):
        raise Ex10Refusal(["raw-missing"], terminal=False)
    assessment = assess(state, probes=probes, dataset=dataset, custody_errors=store.verify(run_id))
    result = assessment.evaluation.result
    return {
        "run_id": run_id,
        "provisional": True,
        "decision": str(result.decision),
        "criteria": dict(result.criteria),
        "invalidity_reasons": list(result.invalidity_reasons),
        "not_demonstrable": list(result.not_demonstrable),
        "failed_criteria": list(result.failed_criteria),
    }


def _ht1_01_view(evaluation: ex10_load.Ex10Evaluation) -> dict[str, Any]:
    throughput = evaluation.throughput
    return {
        "numerator": throughput.n_ok_window,
        "denominator_seconds": ex10_load.WINDOW_NS // 1_000_000_000,
        "throughput_per_minute": throughput.per_minute,
        "threshold_per_minute": ex10_load.THROUGHPUT_THRESHOLD_PER_MINUTE,
        "result": str(throughput.status),
    }


def _lifecycle(decision: Decision, *, abandoned: bool) -> Phase:
    if abandoned:
        return Phase.ABANDONED_PRE_T0
    return {
        Decision.PASS: Phase.FINALIZED_PASS,
        Decision.FAIL: Phase.FINALIZED_FAIL,
        Decision.NOT_DEMONSTRATED: Phase.NOT_DEMONSTRATED,
    }[decision]


def finalize_run(
    store: EvidenceStore,
    *,
    run_id: str,
    git_sha: str,
    abandon_reason: str | None = None,
    declare_executor_gone: bool = False,
    probes: ExecutorProbes | None = None,
) -> dict[str, Any]:
    """Decide una única vez, custodia la decisión y sella el run con el manifest final.

    Todo ocurre bajo un solo `store.mutation()`: recuperación del journal, lectura de los
    streams (congelados mientras dure el lock), su registro, la decisión y el sello. Una
    finalización interrumpida se retoma y sólo reutiliza una decisión idéntica.
    """
    probes = probes or ExecutorProbes()
    dataset = probes.dataset()
    plan = ex10_load.build_plan(dataset)
    with _checkpoint(store):
        if not run_exists(store, run_id):
            raise Ex10Refusal(["run-not-found"], terminal=True)
        store.recover_finalization(run_id)
        state = _open_state(store, run_id, git_sha)
        phase = phase_of(state)
        abandoned = abandon_reason is not None
        extra: list[str] = []
        if abandoned:
            if not _REASON.fullmatch(abandon_reason):
                raise Ex10Refusal(["abandon-reason-format"], terminal=False)
            reason = abandon_reason
            # Sin t0 no se demostró nada, pero tampoco se midió nada: es un abandono limpio.
            if phase not in ALLOWED["abandon"] and not (
                phase in {Phase.DRAINING, Phase.RECONCILING} and raw_pre_t0(state)
            ):
                raise Ex10Refusal([f"phase:{phase}:abandon-after-t0-not-allowed"], terminal=False)
            extra.append(f"abandoned-before-t0:{reason}")
        elif phase in ALLOWED["finalize-crashed"]:
            if not declare_executor_gone:
                raise Ex10Refusal([f"phase:{phase}:executor-may-still-run"], terminal=False)
            extra.append(
                {
                    Phase.STARTED: "emission-interrupted:before-arming",
                    Phase.EMITTING: "emission-interrupted:raw-missing",
                    Phase.DRAINING: "drain:missing",
                }[phase]
            )
        elif phase not in ALLOWED["finalize"]:
            raise Ex10Refusal([f"phase:{phase}:finalize-not-allowed"], terminal=False)

        custody_errors = store.verify(run_id)
        assessment = None
        if state.has(ART_RAW) and not abandoned:
            assessment = assess(
                state, probes=probes, dataset=dataset, custody_errors=custody_errors
            )
        if assessment is not None and not extra:
            decision = assessment.evaluation.decision
            reasons = list(assessment.evaluation.result.invalidity_reasons) + list(
                assessment.evaluation.result.not_demonstrable
            )
        else:
            decision = Decision.NOT_DEMONSTRATED
            reasons = [*extra, *(f"custody:{e}" for e in custody_errors)]
            if state.has(ART_RAW_REJECTED):
                reasons.append("raw-rejected:sensitive-content")
            if assessment is not None:
                reasons += list(assessment.evaluation.result.invalidity_reasons)
        if decision is Decision.NOT_DEMONSTRATED and not reasons:
            reasons = ["run-not-demonstrable"]
        lifecycle = _lifecycle(decision, abandoned=abandoned)
        mode = _evidence_mode(state.initial_manifest)

        # Streams: se registran con los mismos bytes que leyó la reconciliación. Con el lock
        # tomado, web y worker no pueden añadir eventos; tras el sello, ya no admiten ninguno.
        for name, artifact_type in STREAMS.items():
            path = state.run_root / "logs" / SCENARIO_ID / f"{name}.jsonl"
            record = state.record(artifact_type)
            if record is not None:
                registered = record["sha256"]
            elif path.is_file():
                registered = store.register_existing(
                    run_id=run_id,
                    scenario_id=SCENARIO_ID,
                    artifact_type=artifact_type,
                    kind=ArtifactKind.LOG,
                    classification=Classification.INTERNAL,
                    filename=f"{name}.jsonl",
                    source="s11-instrumentation",
                    transformation="automatic-redaction+append-only-jsonl",
                ).sha256
            else:
                registered = None
            if assessment is not None and registered != assessment.streams[name].sha256:
                raise Ex10ExecutorError(f"El stream {name} difiere del que se reconcilió.")

        decision_payload = {
            **(
                assessment.evaluation.decision_payload()
                if assessment is not None and not extra
                else {
                    "schema_version": ex10_load.DECISION_SCHEMA_VERSION,
                    "protocol_id": ex10_load.PROTOCOL_ID,
                    "methodology_version": ex10_load.METHODOLOGY_VERSION,
                    "scenario_id": SCENARIO_ID,
                    "dataset_id": ex10_load.DATASET_ID,
                    "dataset_sha256": dataset.sha256,
                    "plan_sha256": plan.sha256,
                    "decision_reference": ex10_load.DECISION_REFERENCE,
                    "decision": str(decision),
                    "manifest_status": str(ex10_load.MANIFEST_STATUS[decision]),
                    "criteria": None,
                    "invalidity_reasons": reasons,
                    "not_demonstrable": [],
                    "failed_criteria": [],
                }
            ),
            "run_id": run_id,
            "evidence_mode": mode,
            "lifecycle": str(lifecycle),
            "binding": mode == OFFICIAL_MODE and decision in {Decision.PASS, Decision.FAIL},
            "ht1_01": None if assessment is None or extra else _ht1_01_view(assessment.evaluation),
            "reasons": reasons if decision is Decision.NOT_DEMONSTRATED else [],
            "allowed_decisions": [str(d) for d in Decision],
        }
        # Una finalización interrumpida sólo se retoma con artefactos idénticos: la decisión se
        # toma una única vez y nunca se reescribe.
        if assessment is not None and not extra:
            _persist_once(
                store,
                state,
                ART_RECONCILIATION,
                assessment.reconciliation_payload,
                source="s11_ex10.finalize",
                dataset=dataset,
            )
            _persist_once(
                store,
                state,
                ART_CALCULATION,
                assessment.evaluation.calculation_payload(),
                source="s11_ex10.finalize",
                dataset=dataset,
            )
        decision_sha256 = _persist_once(
            store,
            state,
            ART_DECISION,
            decision_payload,
            source="s11_ex10.finalize",
            dataset=dataset,
        )

        records = store.records(run_id)
        initial = dict(state.initial_manifest)
        initial["artifact_references"] = tuple(
            ArtifactReference(**reference) for reference in initial.get("artifact_references", [])
        )
        manifest = RunManifest(**initial)
        finished_at = probes.now()
        status = ex10_load.MANIFEST_STATUS[decision]
        invalidity = "; ".join(reasons)[:2000] if decision is Decision.NOT_DEMONSTRATED else None
        final = finalize_manifest(
            manifest,
            finished_at=finished_at,
            status=status,
            artifacts=[
                ArtifactReference(r["artifact_id"], r["relative_path"], r["sha256"])
                for r in records
            ],
            invalidity_reason=invalidity,
        )
        final_record = store.finalize_with_json_artifact(
            finished_at=finished_at,
            run_id=run_id,
            scenario_id=SCENARIO_ID,
            artifact_type=ART_MANIFEST_FINAL,
            kind=ArtifactKind.METADATA,
            classification=Classification.INTERNAL,
            filename="run-manifest.final.json",
            payload=final.to_dict(),
            source="s11_ex10.finalize",
        )
        verification = store.verify(run_id)
        if verification and not (
            decision is Decision.NOT_DEMONSTRATED and set(verification) <= set(custody_errors)
        ):
            raise Ex10ExecutorError(f"Falló la validación final de la custodia: {verification}")
        validate_finalized_state(
            state.run_root, store.records(run_id), custody_hmac_key=store.custody_hmac_key
        )
    return {
        "run_id": run_id,
        "decision": decision_payload["decision"],
        "lifecycle": decision_payload["lifecycle"],
        "binding": decision_payload["binding"],
        "reasons": decision_payload["reasons"],
        "decision_sha256": decision_sha256,
        "manifest_status": final.status,
        "manifest_sha256": final_record.sha256,
        "custody_errors": verification,
    }


def run_status(store: EvidenceStore, run_id: str) -> dict[str, Any]:
    """Resumen de sólo lectura: fase y artefactos, sin métricas. No crea nada."""
    if not run_exists(store, run_id):
        return {"run_id": run_id, "exists": False}
    state = load_state(store, run_id)
    decision = state.payload(ART_DECISION)
    return {
        "run_id": run_id,
        "exists": True,
        "scenario_id": (state.initial_manifest or {}).get("scenario_id"),
        "phase": str(phase_of(state)) if state.initial_manifest else None,
        "finalized": state.finalized,
        "artifacts": sorted(record["artifact_type"] for record in state.records),
        "decision": decision.get("decision")
        if isinstance(decision, Mapping) and state.finalized
        else None,
        "custody_errors": store.verify(run_id),
    }
