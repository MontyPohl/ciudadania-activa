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

import fcntl
import json
import os
import re
import secrets
import select
import signal
import socket
import stat
import threading
import time
import unicodedata
import uuid
from base64 import b64decode
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings

from . import ex10_bridge_protocol as wire
from . import ex10_deployment_envelope as deployment_envelope
from . import ex10_fixture, ex10_load, ex10_preflight, ex10_reconcile, ex10_worker_probe
from . import ex10_open_evidence as open_evidence
from . import ex10_runtime_profile as profile_codec
from . import ex10_session_v2 as session_codec
from .canonical import canonical_json_bytes, sha256_bytes, sha256_file
from .constants import ArtifactKind, Classification, Decision
from .custody_policy import (
    CustodyPolicyError,
    PublicationApproval,
    validate_custody_finalization_signature,
    validate_custody_index_signature,
    validate_custody_record_signature,
    validate_public_approval,
)
from .evidence import (
    _APPEND_LOCK,
    _FINALIZED_MARKER,
    EvidencePolicyError,
    EvidenceStore,
    validate_finalized_state,
)
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
from .ex10_prior_custody import PriorRun, binding_refusal
from .ex10_reconcile import ConfigObservation, Stream
from .manifest import (
    ArtifactReference,
    RunManifest,
    create_manifest,
    finalize_manifest,
    make_run_id,
)
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
    #: Marcador de emisión sin armar: preparando tokens y cuerpos, o detenido ahí por el
    #: checkpoint LOCAL_TEST `rehearsal-started`. Nada se envió todavía.
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
    #: Sólo LOCAL_TEST: el marcador de emisión y nada más (ensayo del lifecycle antes de t0).
    "rehearsal-started": frozenset({Phase.PREPARED}),
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
    unreadable: list[str] = dataclass_field(default_factory=list)

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
    """El estado de un run para los pasos que mutan: inicializa el directorio (`mkdir`/`chmod`).

    `status` nunca lo usa: lee con `load_existing_state_readonly`.
    """
    run_root = store.initialize_run(run_id)
    records = store.records(run_id)
    payloads, unreadable = _read_payloads(run_root, records)
    return RunState(run_id, run_root, records, payloads, unreadable)


def _read_payloads(
    run_root: Path, records: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], list[str]]:
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
    return payloads, unreadable


# --- Lectura estrictamente read-only (status) ------------------------------------------------

#: Un mutador que retiene el lock de custodia más que esto deja la lectura en `status-read-race`.
STATUS_LOCK_ATTEMPTS = 40
STATUS_LOCK_PAUSE = 0.05
_READ_RACE = "status-read-race"
_INDEX = Path(ArtifactKind.METADATA.value) / "artifacts.jsonl"


@contextmanager
def _shared_custody_lock(store: EvidenceStore) -> Iterator[None]:
    """El lock de custodia existente en modo compartido, sin crearlo ni tocar su metadata.

    Se abre en sólo lectura (sin `O_CREAT`, sin seguir symlinks): el `flock` compartido excluye
    a los mutadores, que lo toman exclusivo, sin escribir nada. Si no existe, la raíz nunca tuvo
    una mutación; la comparación del índice antes y después cubre ese caso. Un mutador que lo
    retiene más allá del plazo deja la lectura en `status-read-race`.
    """
    try:
        fd = os.open(store.root / ".s11-custody.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        yield
        return
    try:
        for _ in range(STATUS_LOCK_ATTEMPTS):
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(STATUS_LOCK_PAUSE)
        else:
            raise Ex10Refusal([_READ_RACE], terminal=False)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _index_fingerprint(run_root: Path) -> tuple:
    """Identidad y metadata del índice, su firma y el sello: detecta una escritura concurrente."""
    facts = []
    for path in (run_root / _INDEX, (run_root / _INDEX).with_suffix(".jsonl.hmac"),
                 run_root / ".s11-finalized"):  # fmt: skip
        try:
            info = os.lstat(path)
        except (FileNotFoundError, NotADirectoryError):
            facts.append(None)
            continue
        facts.append((info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    return tuple(facts)


def _existing_run_root(store: EvidenceStore, run_id: str) -> Path | None:
    """El directorio real de un run existente o `None`: ni `runs/` ni el run pueden ser symlinks."""
    runs = store.root / "runs"
    if runs.is_symlink() or not run_exists(store, run_id):
        return None
    return runs / run_id


def _read_existing_state_under_caller_lock(store: EvidenceStore, run_id: str) -> RunState | None:
    """El estado de un run existente tal cual está en disco; `None` si no existe.

    No toma ningún lock: el caller garantiza la consistencia con el lock de custodia que ya
    tiene (exclusivo en START, compartido en `status` y `load_existing_state_readonly`). Volver
    a pedirlo aquí con otro descriptor bloquearía al propio START.

    Nunca `initialize_run`, `mkdir`, `chmod` ni escrituras. El run, `runs/`, `metadata/` y el
    índice deben ser reales: un symlink no se sigue (`OSError`). Un índice corrupto propaga su
    `ValueError`, como `load_state`.
    """
    run_root = _existing_run_root(store, run_id)
    if run_root is None:
        return None
    index = run_root / _INDEX
    if index.parent.is_symlink() or index.is_symlink():
        raise OSError("índice de custodia a través de un symlink")
    records = (
        [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
        if index.exists()
        else []
    )
    payloads, unreadable = _read_payloads(run_root, records)
    return RunState(run_id, run_root, records, payloads, unreadable)


def load_existing_state_readonly(store: EvidenceStore, run_id: str) -> RunState | None:
    """El estado de un run existente sin crear, reparar ni normalizar nada; `None` si no existe.

    Lee bajo el lock de custodia compartido (`_shared_custody_lock`). Si el índice, su firma o
    el sello cambian mientras se lee, la lectura se rechaza (`status-read-race`) en lugar de
    componer un estado que nunca existió. Bajo el lock exclusivo de un paso que muta se usa
    `_read_existing_state_under_caller_lock`.
    """
    with _shared_custody_lock(store):
        run_root = _existing_run_root(store, run_id)
        if run_root is None:
            return None
        before = _index_fingerprint(run_root)
        state = _read_existing_state_under_caller_lock(store, run_id)
        if state is None or _index_fingerprint(run_root) != before:
            raise Ex10Refusal([_READ_RACE], terminal=False)
        return state


def verify_readonly(store: EvidenceStore, state: RunState) -> list[str]:
    """Las comprobaciones de `EvidenceStore.verify` sin `initialize_run`, con sus mismas primitivas
    puras de custodia y los mismos códigos de error: firma del índice, SHA-256 y política de cada
    artefacto, y el sello del run finalizado."""
    run_root, errors = state.run_root, []
    index = run_root / _INDEX
    signature = index.with_suffix(".jsonl.hmac")
    try:
        if index.parent.is_symlink() or index.is_symlink() or signature.is_symlink():
            raise OSError("firma del índice a través de un symlink")
        validate_custody_index_signature(
            index.read_bytes(),
            signature.read_text(encoding="ascii").strip(),
            custody_hmac_key=store.custody_hmac_key,
        )
    except (CustodyPolicyError, OSError):
        errors.append("index-signature")
    for record in state.records:
        path = run_root / record["relative_path"]
        # Un artefacto que es un symlink, o que resuelve fuera del run, no está custodiado.
        if (
            path.is_symlink()
            or run_root.resolve() not in path.resolve().parents
            or not path.is_file()
        ):
            errors.append(f"missing:{record['artifact_id']}")
        elif sha256_file(path) != record["sha256"]:
            errors.append(f"sha256:{record['artifact_id']}")
        try:
            validate_custody_record_signature(record, custody_hmac_key=store.custody_hmac_key)
            approval_payload = record.get("publication_approval")
            if approval_payload is not None and not isinstance(approval_payload, dict):
                raise CustodyPolicyError("La aprobación almacenada no es un objeto.")
            approval = (
                PublicationApproval(**approval_payload) if approval_payload is not None else None
            )
            validate_public_approval(
                Classification(record["classification"]),
                approval,
                expected_artifact_id=record["artifact_id"],
                expected_content_sha256=record["sha256"],
                verification_key=store.publication_verification_key,
            )
        except (CustodyPolicyError, TypeError, ValueError):
            errors.append(f"policy:{record['artifact_id']}")
    try:
        validate_finalized_state(run_root, state.records, custody_hmac_key=store.custody_hmac_key)
    except EvidencePolicyError:
        errors.append("finalized-state")
    return errors


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
        terminal = step in {"execute", "rehearsal-started"}
        raise Ex10Refusal([f"phase:{phase}:{step}-not-allowed"], terminal=terminal)
    return phase


def _evidence_mode(manifest: Mapping[str, Any]) -> str | None:
    for mode, experiment_id in EXPERIMENT_IDS.items():
        if manifest.get("experiment_id") == experiment_id:
            return mode
    return None


# --- Primer run válido: una sola decisión PASS/FAIL oficial ----------------------------------


def _prior_ex10_runs_under_caller_lock(store: EvidenceStore) -> list[PriorRun]:
    """Todos los runs de EX-10 de la raíz, verificados. Nunca se ignora uno ilegible.

    Estrictamente de sólo lectura (ni `initialize_run`, ni `mkdir`, ni `chmod`, ni el lock) y
    sin lock propio: START la llama con el lock exclusivo de custodia ya tomado, y
    `prior_ex10_runs` con el compartido. Un rastro que no se puede verificar (índice corrupto o
    ausente, firma inválida, artefacto faltante, symlinks) queda como run con problemas, que
    bloquea un START nuevo: nunca se repara ni desaparece.
    """
    runs_dir = store.root / "runs"
    if not os.path.lexists(runs_dir):
        return []
    if runs_dir.is_symlink() or not runs_dir.is_dir():
        return [PriorRun("runs", None, False, None, ("runs-directory-invalid",))]
    found = []
    for child in sorted(runs_dir.iterdir()):
        real_directory = child.is_dir() and not child.is_symlink()
        # Una ruta con nombre de run que no es un directorio real no se interpreta: bloquea.
        if not real_directory and valid_run_id(child.name):
            found.append(PriorRun(child.name, None, False, None, ("run-directory-invalid",)))
            continue
        # Cualquier rastro de EX-10 cuenta: borrar `metadata/` no esconde un run previo.
        if not any(os.path.lexists(child / kind.value / SCENARIO_ID) for kind in ArtifactKind):
            continue
        if not real_directory or not valid_run_id(child.name):
            found.append(PriorRun(child.name, None, False, None, ("run-directory-invalid",)))
            continue
        problems: list[str] = []
        try:
            state = _read_existing_state_under_caller_lock(store, child.name)
        except (OSError, ValueError):
            state = None
        if state is None:
            found.append(PriorRun(child.name, None, False, None, ("records-unreadable",)))
            continue
        manifest = state.initial_manifest
        mode = None if manifest is None else _evidence_mode(manifest)
        if manifest is None or manifest.get("scenario_id") != SCENARIO_ID or mode is None:
            problems.append("initial-manifest")
        problems.extend(f"custody:{error}" for error in verify_readonly(store, state))
        problems.extend(f"unreadable:{name}" for name in state.unreadable)
        decision_payload = state.payload(ART_DECISION)
        decision = (
            decision_payload.get("decision") if isinstance(decision_payload, Mapping) else None
        )
        if state.finalized and decision not in {d.value for d in Decision}:
            problems.append("finalized-without-ex10-decision")
        found.append(PriorRun(child.name, mode, state.finalized, decision, tuple(problems)))
    return found


def _prior_scan_fingerprint(store: EvidenceStore) -> tuple:
    runs_dir = store.root / "runs"
    if runs_dir.is_symlink() or not runs_dir.is_dir():
        return (os.path.lexists(runs_dir),)
    return tuple((child.name, _index_fingerprint(child)) for child in sorted(runs_dir.iterdir()))


def prior_ex10_runs(store: EvidenceStore) -> list[PriorRun]:
    """Los runs previos de EX-10, fuera de START: estrictamente de sólo lectura y coherente.

    Un único lock de custodia compartido para toda la exploración (no uno por run) y la
    comparación de los índices antes y después: un cambio concurrente es `status-read-race`.
    """
    with _shared_custody_lock(store):
        before = _prior_scan_fingerprint(store)
        found = _prior_ex10_runs_under_caller_lock(store)
        if _prior_scan_fingerprint(store) != before:
            raise Ex10Refusal([_READ_RACE], terminal=False)
        return found


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


def propose_run(*, started_at: datetime, git_sha: str, nonce: str) -> dict[str, Any]:
    """Pure v2 proposal: the original six UUID5 fields and their exact digest.

    No probes, manifest, filesystem, clock, key, permit or authority are consumed.
    Callers freeze started_at before reserve and use it again in initial artifacts.
    """
    if (
        type(started_at) is not datetime
        or started_at.tzinfo is None
        or type(git_sha) is not str
        or not _SHA1.fullmatch(git_sha)
        or type(nonce) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", nonce)
    ):
        raise Ex10Refusal(["run-proposal-invalid"], terminal=True)
    value = {
        "started_at": _utc(started_at),
        "git_sha": git_sha,
        "dataset_id": ex10_load.DATASET_ID,
        "scenario_id": SCENARIO_ID,
        "seed": ex10_load.DATASET_SEED,
        "execution_nonce": nonce,
        "run_id": make_run_id(
            started_at=started_at,
            git_sha=git_sha,
            dataset_id=ex10_load.DATASET_ID,
            scenario_id=SCENARIO_ID,
            seed=ex10_load.DATASET_SEED,
            execution_nonce=nonce,
        ),
    }
    return {**value, "identity_digest": sha256_bytes(canonical_json_bytes(value))}


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
    """Semántica histórica (runs sin G0-A2): claves dinámicas y mensajes de tarea del broker.

    Aplica al estado post-run la regla de broker vacío del `READY_STATE`; un run correcto deja
    residuos esperados (resultados, throttle), así que esta lectura sólo decide la evidencia de
    runs cuya identidad no registra G0-A2. Los nuevos usan `post_run_broker`.
    """
    from . import ex10_prepare

    environment = ex10_prepare.readonly_environment()
    try:
        snapshot = ex10_preflight.classify_broker(environment.redis)
    finally:
        ex10_prepare._close(environment.redis)
    return snapshot.dynamic + snapshot.task_messages + len(snapshot.unclassifiable)


_TASK_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def post_run_broker(plan: LoadPlan) -> ex10_reconcile.PostRunBroker:
    """G0-A2: el broker tras el run, con el cliente de sólo lectura y sin leer valores.

    Clasifica cada clave por nombre exacto: colas y entregas (`broker_keys`), resultados de
    Celery por su prefijo con el task ID como sufijo, throttle por el prefijo de la cache y
    el resto como desconocidas. El throttle esperado y la caché adjudicada se derivan con la
    API de cache real, nunca escritos a mano.
    """
    from fnmatch import fnmatchcase

    from django.core.cache import cache

    from apps.map_app.cache import GENERATION_KEY

    from . import ex10_prepare, ex10_state

    environment = ex10_prepare.readonly_environment()
    redis = environment.redis
    try:
        snapshot = ex10_preflight.classify_broker(redis)
        keys = ex10_state.broker_keys()
        result_prefixes = tuple(p[:-1] for p in keys.result_patterns or () if p.endswith("*"))
        throttle_prefix = cache.make_key("throttle_")
        mutex, pidbox, events = keys.worker_ephemeral
        queues = set(keys.queues)
        queue_keys = unacked = unacked_index = malformed = 0
        result_ids: list[str] = []
        throttle: list[str] = []
        unknown: list[str] = []
        for raw in redis.scan_iter(match="*", count=500):
            key = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            prefix = next((p for p in result_prefixes if key.startswith(p)), None)
            if key in queues:
                queue_keys += 1
            elif key == keys.unacked:
                unacked += 1
            elif key == keys.unacked_index:
                unacked_index += 1
            elif prefix is not None:
                task_id = key[len(prefix) :]
                if _TASK_ID.fullmatch(task_id):
                    result_ids.append(task_id)
                else:
                    malformed += 1
            elif key.startswith(throttle_prefix):
                throttle.append(key)
            elif (
                fnmatchcase(key, keys.static_bindings)
                or key == mutex
                or fnmatchcase(key, pidbox)
                or fnmatchcase(key, events)
            ):
                continue
            else:
                unknown.append(key)
    finally:
        ex10_prepare._close(redis)
    expected_throttle = {
        cache.make_key(ex10_state.throttle_key(str(uuid.UUID(item.dataset_user_id))))
        for item in plan.items
    }
    return ex10_reconcile.PostRunBroker(
        queue_keys=queue_keys,
        unacked=unacked,
        unacked_index=unacked_index,
        task_messages=snapshot.task_messages,
        unclassifiable=tuple(snapshot.unclassifiable),
        result_task_ids=tuple(sorted(result_ids)),
        malformed_result_keys=malformed,
        throttle_keys=tuple(sorted(throttle)),
        expected_throttle_keys=tuple(sorted(expected_throttle)),
        unknown_keys=tuple(sorted(unknown)),
        expected_cache_key=cache.make_key(GENERATION_KEY),
    )


def follows_g0_a2(initial_manifest: Mapping[str, Any] | None) -> bool:
    """Si la identidad custodiada del run registra G0-A2. La de un run anterior, no (G0-A2)."""
    configuration = (initial_manifest or {}).get("configuration")
    protocol = configuration.get("protocol") if isinstance(configuration, Mapping) else None
    return (
        isinstance(protocol, Mapping)
        and protocol.get("decision_addendum_a2_commit") == ex10_load.G0_A2_DECISION_COMMIT
    )


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
    post_run_broker: Callable[[LoadPlan], ex10_reconcile.PostRunBroker] = post_run_broker
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


"""Executor-side v2 bridge. Fixed providers are installed by the approved host launcher."""


class ExecutorBridgeError(Ex10ExecutorError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _bridge_require(condition, code):
    if not condition:
        raise ExecutorBridgeError(code)


def _fixed_provider(provider, **arguments):
    try:
        return provider(**arguments)
    except Exception:
        raise ExecutorBridgeError("CONTEXT_OBSERVATION_UNAVAILABLE") from None


START_STEPS = (
    ("HOST_CHALLENGE", "HOST_TO_EXECUTOR"),
    ("PROPOSE_OPEN", "EXECUTOR_TO_HOST"),
    ("AUTHORIZE_REQUEST", "HOST_TO_EXECUTOR"),
    ("RPC_REQUEST", "EXECUTOR_TO_HOST"),
    ("RPC_RESPONSE", "HOST_TO_EXECUTOR"),
    ("OPEN_EVIDENCE", "EXECUTOR_TO_HOST"),
    ("AUTHORIZE_REQUEST", "HOST_TO_EXECUTOR"),
    ("RPC_REQUEST", "EXECUTOR_TO_HOST"),
    ("RPC_RESPONSE", "HOST_TO_EXECUTOR"),
)
SINGLE_STEPS = (
    ("HOST_CHALLENGE", "HOST_TO_EXECUTOR"),
    ("RPC_PROPOSAL", "EXECUTOR_TO_HOST"),
    ("AUTHORIZE_REQUEST", "HOST_TO_EXECUTOR"),
    ("RPC_REQUEST", "EXECUTOR_TO_HOST"),
    ("RPC_RESPONSE", "HOST_TO_EXECUTOR"),
)


class ExecutorOperationalContext:
    """One constructor-fixed action, actual pipe, independent pins and holder PoP.

    Providers are mandatory trust/OS integration boundaries installed by the
    approved launcher. None proves their fixed qualification; booleans cannot.
    A request never chooses a provider, root, action, key, target or entrypoint.
    START preparation has a separate supervisor budget; the exchange's deadline
    begins once and never resets. Other actions never produce a new START PF.
    """

    def __init__(
        self,
        *,
        action,
        channel,
        bridge_pin,
        witness_pin,
        holder_key,
        expected_challenge,
        request_payload,
        evidence_root,
        host_evidence_root,
        staging_name,
        authority_revision,
        host_root_binding_sha256,
        expected_reservation=None,
        native_admitter,
        current_guard,
        common_prior_reader,
        common_prior_verifier,
        commit_verifier,
        terminal_preparer,
        clock,
    ):
        _bridge_require(
            action in ("start", "execute", "drain", "reconcile", "finalize"), "ACTION_INVALID"
        )
        _bridge_require(
            type(channel) is ExecutorBridgeChannel and channel.role == "EXECUTOR",
            "PRIVATE_PIPE_REQUIRED",
        )
        _bridge_require(
            channel.exchange == ("START" if action == "start" else "SINGLE_RPC"), "PROTOCOL_INVALID"
        )
        _bridge_require(
            type(bridge_pin) is wire.PublicKeyPin
            and type(witness_pin) is wire.PublicKeyPin
            and isinstance(holder_key, Ed25519PrivateKey),
            "INDEPENDENT_PIN_REQUIRED",
        )
        for provider in (
            native_admitter,
            current_guard,
            common_prior_reader,
            common_prior_verifier,
            commit_verifier,
            terminal_preparer,
            clock,
        ):
            _bridge_require(callable(provider), "FIXED_PROVIDER_REQUIRED")
        profile_codec._absolute(str(evidence_root))
        profile_codec._absolute(str(host_evidence_root))
        expected_keys = {
            "exchange_id",
            "authority_id",
            "authority_epoch",
            "authorization_reference",
            "bridge_key_id",
            "bridge_key_epoch",
            "container_identity_digest",
            "deployment_verification_sha256",
            "session_id",
            "session_header_sha256",
            "runtime_profile_sha256",
            "source_revision",
            "project",
        }
        _bridge_require(
            type(expected_challenge) is dict
            and set(expected_challenge) == expected_keys
            and expected_challenge["exchange_id"] == channel.exchange_id
            and expected_challenge["bridge_key_id"] == bridge_pin.key_id
            and expected_challenge["bridge_key_epoch"] == bridge_pin.key_epoch,
            "CONTEXT_BINDING_INVALID",
        )
        _bridge_require(
            action != "start"
            or type(staging_name) is str
            and re.fullmatch(r"application-preflight\.[0-9a-f-]{36}\.json", staging_name),
            "STAGING_INVALID",
        )
        _bridge_require(
            type(authority_revision) is int and 0 <= authority_revision < wire.SAFE_INTEGER_MAX,
            "REVISION_INVALID",
        )
        _bridge_require(
            type(host_root_binding_sha256) is str
            and re.fullmatch(r"[0-9a-f]{64}", host_root_binding_sha256),
            "ROOT_BINDING_INVALID",
        )
        if action == "start":
            _bridge_require(
                expected_reservation is None
                and request_payload["expected_head"]["revision"] == authority_revision,
                "CONTEXT_BINDING_INVALID",
            )
            _bridge_require(
                str(uuid.UUID(staging_name[len("application-preflight.") : -5]))
                == staging_name[len("application-preflight.") : -5],
                "STAGING_INVALID",
            )
        else:
            _bridge_require(type(expected_reservation) is dict, "RESERVATION_REQUIRED")
            expected_reservation = wire.qualify_reservation(
                canonical_json_bytes(expected_reservation)
            ).to_dict()
            _bridge_require(
                expected_reservation["state"] == "COMMITTED"
                and request_payload
                == {
                    "reservation_id": expected_reservation["reservation_id"],
                    "original_request_id": expected_reservation["request_id"],
                },
                "RESERVATION_REQUIRED",
            )
            _bridge_require(
                all(
                    expected_reservation[k] == expected_challenge[k]
                    for k in (
                        "authority_id",
                        "authority_epoch",
                        "session_id",
                        "session_header_sha256",
                        "runtime_profile_sha256",
                        "source_revision",
                        "project",
                    )
                ),
                "CONTEXT_BINDING_INVALID",
            )
        self.config = canonical_json_bytes(
            {
                "action": action,
                "expected_challenge": expected_challenge,
                "payload": request_payload,
                "evidence_root": str(evidence_root),
                "host_evidence_root": str(host_evidence_root),
                "staging_name": staging_name,
                "authority_revision": authority_revision,
                "host_root_binding_sha256": host_root_binding_sha256,
                "expected_reservation": expected_reservation,
            }
        )
        self.channel, self.pin, self.witness, self.holder = (
            channel,
            bridge_pin,
            witness_pin,
            holder_key,
        )
        (
            self.admit,
            self.guard,
            self.prior_reader,
            self.prior_verifier,
            self.verify_commit,
            self.prepare_terminal,
            self.clock,
        ) = (
            native_admitter,
            current_guard,
            common_prior_reader,
            common_prior_verifier,
            commit_verifier,
            terminal_preparer,
            clock,
        )
        self.owner = (os.getpid(), threading.get_ident())
        self.prepared = self.consumed = self.finished = False
        self.preflight = self.proposal = self.permit = self.reservation = self.reserve_request = (
            None
        )
        self.common = None
        self.scientific_finished = False
        self.step_authorized = False
        self.terminal_prepared = False
        self.commit_dispatched = False
        self.reserve_receipt = None
        self.host_challenge = None
        self.store = None
        self._reservation_intent = None
        retained_intent = None
        exchange_reader = channel._exchange_context_reader
        original_config = self.config
        original_providers = self.admit, self.guard, self.clock
        retained_single = None

        def read_closed_single():
            _bridge_require(
                self._closed_single_reader is read_closed_single
                and self._retain_closed_single is retain_closed_single
                and self.channel is channel
                and self.config == original_config
                and all(
                    actual is expected
                    for actual, expected in zip(
                        (self.admit, self.guard, self.clock), original_providers, strict=False
                    )
                ),
                "CONTEXT_BINDING_INVALID",
            )
            exchange = exchange_reader()
            if retained_single is not None:
                store, proposal, store_facts, original_exchange = retained_single
                _bridge_require(
                    self.store is store
                    and canonical_json_bytes(self.proposal) == proposal
                    and exchange == original_exchange
                    and type(channel.position) is int
                    and channel.position == 5
                    and channel.steps is SINGLE_STEPS
                    and channel.closed is True
                    and channel.read_fd is None
                    and channel.write_fd is None
                    and channel.error_fd is None
                    and str(store.root) == store_facts[0]
                    and type(store.custody_hmac_key) is type(store_facts[1])
                    and store.custody_hmac_key == store_facts[1]
                    and type(store.publication_verification_key) is type(store_facts[2])
                    and store.publication_verification_key == store_facts[2],
                    "CONTEXT_BINDING_INVALID",
                )
            return retained_single

        def retain_closed_single(store):
            nonlocal retained_single
            _bridge_require(read_closed_single() is None, "SECOND_OPERATION_DENIED")
            exchange = exchange_reader()
            _bridge_require(
                exchange[0] == "EXECUTOR"
                and exchange[1] == "SINGLE_RPC"
                and channel.steps is SINGLE_STEPS
                and type(channel.position) is int
                and channel.position == 5
                and channel.closed is True
                and channel.read_fd is None
                and channel.write_fd is None
                and channel.error_fd is None
                and type(exchange[4]) in (int, float)
                and 0 < exchange[4] <= 120
                and type(exchange[5]) in (int, float)
                and time.monotonic() < exchange[5]
                and self.store is store,
                "ACTION_NOT_COMPLETED",
            )
            retained_single = (
                store,
                canonical_json_bytes(self.proposal),
                (str(store.root), store.custody_hmac_key, store.publication_verification_key),
                exchange,
            )
            read_closed_single()

        self._closed_single_reader = read_closed_single
        self._retain_closed_single = retain_closed_single

        def read_intent():
            _bridge_require(
                self._reservation_intent == retained_intent
                and self._intent_reader is read_intent
                and self._retain_reservation_intent is retain_intent
                and self._closed_single_reader is read_closed_single
                and self._retain_closed_single is retain_closed_single,
                "RESERVATION_MISMATCH",
            )
            return retained_intent

        def retain_intent(canonical_record):
            nonlocal retained_intent
            read_intent()
            _bridge_require(retained_intent is None, "SECOND_OPERATION_DENIED")
            record = wire.qualify_reservation(canonical_record).to_dict()
            retained_intent = canonical_json_bytes(
                {k: record[k] for k in (*wire.RESERVATION_INTENT_FIELDS, "reservation_digest")}
            )
            self._reservation_intent = retained_intent

        self._intent_reader = read_intent
        self._retain_reservation_intent = retain_intent
        if expected_reservation is not None:
            self.proposal = expected_reservation["run_identity"]
            self.reservation = expected_reservation
            _bridge_require(
                expected_reservation["operator_authorization_reference"]
                == expected_challenge["authorization_reference"],
                "CONTEXT_BINDING_INVALID",
            )
            self._retain_reservation_intent(canonical_json_bytes(expected_reservation))
        self._admit()
        self._check_reservation_intent(reader=read_intent)

    def _check_reservation_intent(self, *, reader):
        retained = reader()
        if retained is None:
            return
        _bridge_require(type(self.reservation) is dict, "RESERVATION_MISMATCH")
        record = wire.qualify_reservation(canonical_json_bytes(self.reservation)).to_dict()
        _bridge_require(
            canonical_json_bytes(
                {k: record[k] for k in (*wire.RESERVATION_INTENT_FIELDS, "reservation_digest")}
            )
            == retained,
            "RESERVATION_MISMATCH",
        )

    def _cfg(self):
        return json.loads(self.config)

    @property
    def action(self):
        return self._cfg()["action"]

    def _now(self):
        value = _fixed_provider(self.clock)
        _bridge_require(
            type(value) is datetime
            and value.tzinfo is not None
            and value.utcoffset() == UTC.utcoffset(value),
            "CLOCK_INVALID",
        )
        return value

    def _admit(self):
        _bridge_require(
            self.owner == (os.getpid(), threading.get_ident()), "PROCESS_IDENTITY_CHANGED"
        )
        _bridge_require(
            _fixed_provider(self.admit, context=self, action=self.action) is None,
            "PROCESS_ADMISSION_REQUIRED",
        )

    def check(self, *, run_id=None, phase=None):
        reader = self._intent_reader
        reader()
        self._admit()
        _bridge_require(not self.scientific_finished, "SECOND_OPERATION_DENIED")
        _bridge_require(
            _fixed_provider(
                self.guard, context=self, action=self.action, run_id=run_id, phase=phase
            )
            is None,
            "OWNERSHIP_REQUIRED",
        )
        if not self.finished:
            self.channel._check()
        if self.proposal is not None and run_id is not None:
            _bridge_require(self.proposal["run_id"] == run_id, "RUN_ID_MISMATCH")
        # Providers receive this context. Compare only after every provider and
        # channel guard, against bytes retained before those callbacks.
        self._check_reservation_intent(reader=reader)

    def _root(self, store):
        cfg = self._cfg()
        _bridge_require(str(store.root) == cfg["evidence_root"], "ROOT_BINDING_INVALID")
        fd, lease = profile_codec._walk(cfg["evidence_root"], "directory")
        try:
            info = os.fstat(fd)
            _bridge_require(
                info.st_uid == os.geteuid()
                and info.st_gid == os.getegid()
                and stat.S_IMODE(info.st_mode) == 0o700,
                "ROOT_BINDING_INVALID",
            )
            return fd, lease
        except BaseException:
            os.close(fd)
            raise

    def _create_private(self, store, name, raw):
        root_fd, lease = self._root(store)
        fd = None
        try:
            fd = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd
            )
            offset = 0
            while offset < len(raw):
                written = os.write(fd, raw[offset:])
                _bridge_require(written > 0, "WRITE_UNKNOWN")
                offset += written
            os.fsync(fd)
            os.fsync(root_fd)
            check, current = profile_codec._walk(str(store.root), "directory")
            try:
                _bridge_require(
                    current == lease and os.fstat(check).st_ino == os.fstat(root_fd).st_ino,
                    "ROOT_BINDING_INVALID",
                )
            finally:
                os.close(check)
        except BaseException:
            # Partial/new files remain as evidence; no unlink/recreate or replay.
            raise ExecutorBridgeError("WRITE_UNKNOWN") from None
        finally:
            if fd is not None:
                os.close(fd)
            os.close(root_fd)

    def prepare_start(self, store, *, probes, git_sha, source_root, nonce):
        reader = self._intent_reader
        self._check_reservation_intent(reader=reader)
        _bridge_require(self.action == "start" and not self.prepared, "SECOND_OPERATION_DENIED")
        self.check()
        root_fd, _ = self._root(store)
        os.close(root_fd)
        _bridge_require(
            git_sha == self._cfg()["expected_challenge"]["source_revision"],
            "SOURCE_REVISION_CHANGED",
        )
        proposal, raw = _prepare_native_start(
            probes=probes, git_sha=git_sha, source_root=source_root, nonce=nonce
        )
        self._check_reservation_intent(reader=reader)
        self._create_private(store, self._cfg()["staging_name"], raw)
        self._check_reservation_intent(reader=reader)
        self.store = store
        self.preflight, self.proposal = raw, proposal
        self.prepared = True
        return proposal

    def _send(self, kind, body):
        self.channel.send(
            canonical_json_bytes(
                {
                    "schema_version": "s11-ex10-b4-frame-v1",
                    "exchange_id": self.channel.exchange_id,
                    "sequence": self.channel.position + 1,
                    "kind": kind,
                    "direction": "EXECUTOR_TO_HOST",
                    "body": body,
                }
            )
        )

    def _sign(self, kind, value):
        value = dict(value, signature="0" * 128)
        message = wire._parse(kind, canonical_json_bytes(value), maximum=wire.MAX_REQUEST_BYTES)
        value["signature"] = self.holder.sign(wire.signing_preimage(message)).hex()
        return wire._parse(kind, canonical_json_bytes(value), maximum=wire.MAX_REQUEST_BYTES)

    def _challenge(self):
        body = self.channel.receive().to_dict()["body"]
        expected = {
            **self._cfg()["expected_challenge"],
            "schema_version": "s11-ex10-b4-host-challenge-v1",
            "challenge": body["challenge"],
        }
        result = wire.verify_host_challenge(
            canonical_json_bytes(body),
            expected_context=wire.ReceiptContext(canonical_json_bytes(expected), self._now()),
            bridge_pin=self.pin,
        )
        self.host_challenge = result
        self.check()
        return result

    def _body(self, operation, payload):
        fixed = self._cfg()["expected_challenge"]
        now = self._now()
        return {
            "schema_version": "s11-ex10-b4-bridge-request-v1",
            "request_id": str(uuid.uuid4()),
            "nonce": self.proposal["execution_nonce"]
            if operation == "reserve-start"
            else secrets.token_hex(32),
            "exchange_id": fixed["exchange_id"],
            "session_id": fixed["session_id"],
            "session_header_sha256": fixed["session_header_sha256"],
            "runtime_profile_sha256": fixed["runtime_profile_sha256"],
            "authority_id": fixed["authority_id"],
            "authority_epoch": fixed["authority_epoch"],
            "authority_revision": self._cfg()["authority_revision"],
            "source_revision": fixed["source_revision"],
            "project": fixed["project"],
            "issued_at": _utc(now),
            "expires_at": _utc(now + timedelta(seconds=120)),
            "authorization_reference": fixed["authorization_reference"],
            "operation": operation,
            "payload": payload,
        }

    def _request(self, body):
        capability = wire.parse_capability_bytes(
            canonical_json_bytes(self.channel.receive().to_dict()["body"])
        )
        value = {
            **body,
            "capability": capability.to_dict(),
            "request_digest": "0" * 64,
            "sender_signature": "0" * 128,
        }
        parsed = wire.parse_request_bytes(canonical_json_bytes(value))
        value["request_digest"] = wire.request_digest(parsed)
        parsed = wire.parse_request_bytes(canonical_json_bytes(value))
        value["sender_signature"] = self.holder.sign(
            wire.DOMAINS["request"]
            + canonical_json_bytes({k: v for k, v in value.items() if k != "sender_signature"})
        ).hex()
        cfg = self._cfg()["expected_challenge"]
        context = wire.RequestContext(
            request_id=body["request_id"],
            nonce=body["nonce"],
            exchange_id=body["exchange_id"],
            operation=body["operation"],
            session_id=cfg["session_id"],
            session_header_sha256=cfg["session_header_sha256"],
            runtime_profile_sha256=cfg["runtime_profile_sha256"],
            authority_id=cfg["authority_id"],
            authority_epoch=cfg["authority_epoch"],
            authority_revision=body["authority_revision"],
            source_revision=cfg["source_revision"],
            project=cfg["project"],
            authorization_reference=canonical_json_bytes(cfg["authorization_reference"]),
            deployment_verification_sha256=cfg["deployment_verification_sha256"],
            container_identity_digest=cfg["container_identity_digest"],
            expected_run_id=self.proposal["run_id"],
            reservation_id=(
                self.reservation["reservation_id"]
                if body["operation"] == "inspect-reservation"
                else body["payload"].get("reservation_id")
            ),
            reservation_digest=(
                self.reservation["reservation_digest"]
                if body["operation"] == "inspect-reservation"
                else body["payload"].get("reservation_digest")
            ),
            fencing_token=(
                self.reservation["fencing_token"]
                if body["operation"] == "inspect-reservation"
                else body["payload"].get("fencing_token")
            ),
            holder_public_key_hex=self.holder.public_key().public_bytes_raw().hex(),
            expected_payload=canonical_json_bytes(body["payload"]),
            now=self._now(),
        )
        authenticated = wire.verify_request(
            canonical_json_bytes(value),
            expected_context=context,
            bridge_pin=self.pin,
            holder_public_key=self.holder.public_key(),
        )
        mutating = body["operation"] in {"reserve-start", "commit-open", "record-uncertain"}
        if mutating:
            if body["operation"] == "commit-open":
                self.commit_dispatched = True
            self.channel.mark_dispatched()
        try:
            self._send("RPC_REQUEST", value)
            response = wire.verify_response(
                canonical_json_bytes(self.channel.receive().to_dict()["body"]),
                authenticated_request=authenticated,
                bridge_pin=self.pin,
                now=self._now(),
            )
        except Exception:
            if mutating:
                raise ExecutorBridgeError("UNCERTAIN_PUBLICATION") from None
            raise
        status = response.to_dict()["status"]
        _bridge_require(status != "UNCERTAIN", "UNCERTAIN_PUBLICATION")
        _bridge_require(status == "OK", "HOST_REFUSED")
        return authenticated, response

    def reserve(self, store):
        retain_intent, reader = self._retain_reservation_intent, self._intent_reader
        reader()
        _bridge_require(
            self.prepared and self.action == "start" and self.permit is None, "START_NOT_PREPARED"
        )
        challenge = self._challenge()
        payload = {
            **self._cfg()["payload"],
            "run_identity": self.proposal,
            "preflight_reference": {
                "path": self._cfg()["host_evidence_root"] + "/" + self._cfg()["staging_name"],
                "sha256": sha256_bytes(self.preflight),
                "schema_version": ex10_preflight.SCHEMA_VERSION,
            },
        }
        body = self._body("reserve-start", payload)
        proposal = self._sign(
            "open_proposal",
            {
                "schema_version": "s11-ex10-b4-proposal-v1",
                "exchange_id": self.channel.exchange_id,
                "host_challenge_digest": challenge.sha256,
                "holder_public_key_hex": self.holder.public_key().public_bytes_raw().hex(),
                "reserve_request_body": body,
            },
        )
        self._send("PROPOSE_OPEN", proposal.to_dict())
        self.reserve_request, response = self._request(body)
        result = response.to_dict()["result"]
        canonical_record = wire.qualify_reservation(
            canonical_json_bytes(result["reservation"])
        ).canonical
        record = wire.qualify_reservation(canonical_record).to_dict()
        permit = wire.parse_open_one_bytes(canonical_json_bytes(result["open_permit"])).to_dict()
        fixed = self._cfg()["expected_challenge"]
        base = payload["expected_head"]
        expected = {
            "namespace": "S11C3-EX10",
            "authority_id": fixed["authority_id"],
            "authority_epoch": fixed["authority_epoch"],
            "authorization_reference": fixed["authorization_reference"],
            "base_authority_revision": base["revision"],
            "expected_open_revision": base["revision"] + 1,
            "bridge_key_id": self.pin.key_id,
            "bridge_key_epoch": self.pin.key_epoch,
            "container_identity_digest": fixed["container_identity_digest"],
            "deployment_verification_sha256": fixed["deployment_verification_sha256"],
            "exchange_id": fixed["exchange_id"],
            "holder_public_key_hex": self.holder.public_key().public_bytes_raw().hex(),
            "attempt_nonce": self.proposal["execution_nonce"],
            "acknowledgment_digest": sha256_bytes(canonical_json_bytes(payload["acknowledgments"])),
            "preflight_reference": payload["preflight_reference"],
            "reserve_request_id": body["request_id"],
            "reserve_request_digest": self.reserve_request.request.to_dict()["request_digest"],
            "run_identity": self.proposal,
            **{
                k: fixed[k]
                for k in (
                    "project",
                    "runtime_profile_sha256",
                    "session_header_sha256",
                    "session_id",
                    "source_revision",
                )
            },
            **{
                k: record[k]
                for k in (
                    "reservation_id",
                    "reservation_digest",
                    "reservation_receipt_reference",
                    "fencing_token",
                )
            },
        }
        # Grant/receipt must be authenticated with the separately fixed witness pin.
        receipt = result["reservation_receipt"]
        # The host signature authenticates the response; independently bind every
        # reservation intent to this fixed action before accepting witness material.
        bindings = {
            **{
                k: fixed[k]
                for k in (
                    "authority_id",
                    "authority_epoch",
                    "session_id",
                    "session_header_sha256",
                    "runtime_profile_sha256",
                    "source_revision",
                    "project",
                )
            },
            "namespace": "S11C3-EX10",
            "run_identity": self.proposal,
            "run_id": self.proposal["run_id"],
            "nonce": body["nonce"],
            "request_id": body["request_id"],
            "request_digest": self.reserve_request.request.to_dict()["request_digest"],
            "acknowledgment_digest": expected["acknowledgment_digest"],
            "previous_authority_revision": base["revision"],
            "expected_next_revision": base["revision"] + 1,
            "previous_ledger_digest": base["ledger_digest"],
            "previous_witness_digest": base["witness_digest"],
            "deployment_verification_reference": payload["deployment_verification_reference"],
            # The authenticated host response and separately pinned witness
            # bind this existing intent field. Operator authorization remains
            # constructor-fixed; the complete process policy is retained once.
            "coordination_policy_reference": record["coordination_policy_reference"],
            "host_root_binding_sha256": self._cfg()["host_root_binding_sha256"],
            "operator_authorization_reference": fixed["authorization_reference"],
        }
        _bridge_require(all(record[k] == v for k, v in bindings.items()), "RESERVATION_MISMATCH")
        projection = {k: v for k, v in receipt.items() if k not in {"signature", "issued_at"}}
        for key in (
            "authority_id",
            "authority_epoch",
            "reservation_id",
            "reservation_digest",
            "fencing_token",
        ):
            _bridge_require(receipt[key] == record[key], "RESERVATION_MISMATCH")
        _bridge_require(
            receipt["namespace"] == "S11C3-EX10"
            and receipt["base_authority_revision"] == base["revision"]
            and receipt["base_authority_witness_digest"] == base["witness_digest"]
            and receipt["operation_id"] == body["request_id"]
            and receipt["open_witness_digest"] is None
            and receipt["terminal_witness_digest"] is None,
            "RESERVATION_MISMATCH",
        )
        _bridge_require(
            receipt["key_id"] == self.witness.key_id
            and receipt["key_epoch"] == self.witness.key_epoch,
            "WITNESS_PIN_INVALID",
        )
        wire.verify_reservation_receipt(
            canonical_json_bytes(receipt),
            expected_context=wire.ReceiptContext(canonical_json_bytes(projection), self._now()),
            witness_pin=self.witness,
        )
        receipt_raw = canonical_json_bytes(receipt)
        _bridge_require(
            sha256_bytes(receipt_raw)
            == record["reservation_receipt_reference"]["sha256"]
            == permit["reservation_receipt_reference"]["sha256"],
            "RESERVATION_RECEIPT_MISMATCH",
        )
        self.reserve_receipt = receipt_raw
        _bridge_require(
            record["run_identity"] == self.proposal
            and record["state"] == "PENDING"
            and record["request_id"] == body["request_id"]
            and record["nonce"] == self.proposal["execution_nonce"]
            and record["reservation_id"] == permit["reservation_id"]
            and record["fencing_token"] == permit["fencing_token"]
            and receipt["reservation_id"] == record["reservation_id"]
            and receipt["reservation_digest"] == record["reservation_digest"]
            and receipt["state"] == "PENDING"
            and receipt["transition"] == "RESERVE",
            "RESERVATION_MISMATCH",
        )
        self.permit = wire.verify_open_one(
            canonical_json_bytes(permit),
            expected_context=wire.PermitContext(
                canonical_json_bytes(expected),
                wire._time(record["freshness_deadline"]),
                self._now(),
            ),
            bridge_pin=self.pin,
            holder_public_key=self.holder.public_key(),
        )
        self.reservation = record
        common = _fixed_provider(self.prior_reader, context=self)
        _bridge_require(
            type(common) is tuple and all(type(p) is PriorRun for p in common),
            "COMMON_HISTORY_INVALID",
        )
        _bridge_require(
            _fixed_provider(
                self.prior_verifier, context=self, common=common, reserve_response=response
            )
            is None,
            "COMMON_HISTORY_INVALID",
        )
        self.common = common
        retain_intent(canonical_record)
        self.check(run_id=self.proposal["run_id"])
        self._check_reservation_intent(reader=reader)
        return self.permit

    def consume_delivery(self, store):
        reader = self._intent_reader
        self._check_reservation_intent(reader=reader)
        _bridge_require(self.permit is not None and not self.consumed, "SECOND_OPEN_DENIED")
        self.check(run_id=self.proposal["run_id"])
        permit = self.permit.to_dict()
        _bridge_require(
            wire._time(permit["issued_at"]) <= self._now() < wire._time(permit["expires_at"]),
            "OPEN_ONE_EXPIRED",
        )
        current = self._now()
        problems = preflight_problems(
            json.loads(self.preflight), git_sha=self.proposal["git_sha"], now=current
        )
        _bridge_require(not problems, "PREFLIGHT_EXPIRED_OR_CHANGED")
        self._check_reservation_intent(reader=reader)
        self._create_private(
            store,
            "delivery-claim." + permit["permit_nonce"] + ".json",
            canonical_json_bytes(
                {
                    "reservation_id": permit["reservation_id"],
                    "fencing_token": permit["fencing_token"],
                    "permit_digest": permit["permit_digest"],
                    "permit_nonce": permit["permit_nonce"],
                    "run_identity": self.proposal,
                }
            ),
        )
        self._check_reservation_intent(reader=reader)
        self.consumed = True

    def commit_open(self, store, records):
        try:
            return self._commit_open(store, records)
        except ExecutorBridgeError as error:
            if self.commit_dispatched and error.code != "HOST_REFUSED":
                raise ExecutorBridgeError("UNCERTAIN_PUBLICATION") from None
            raise
        except Exception:
            if self.commit_dispatched:
                raise ExecutorBridgeError("UNCERTAIN_PUBLICATION") from None
            raise

    def _commit_open(self, store, records):
        reader = self._intent_reader
        original_intent = reader()
        _bridge_require(self.consumed and not self.finished, "START_NOT_OPENED")
        self.check(run_id=self.proposal["run_id"])
        refs = {}
        cfg = self._cfg()
        for reference_field, record in zip(open_evidence.REFERENCE_FIELDS, records, strict=True):
            path = str(store.root / "runs" / self.proposal["run_id"] / record.relative_path)
            raw, _ = deployment_envelope._file(
                path, owner=(os.geteuid(), os.getegid()), private=True
            )
            _bridge_require(sha256_bytes(raw) == record.sha256, "ARTIFACT_CHANGED")
            if reference_field == "preflight_reference":
                _bridge_require(raw == self.preflight, "PREFLIGHT_CHANGED")
            refs[reference_field] = {
                "path": cfg["host_evidence_root"]
                + "/runs/"
                + self.proposal["run_id"]
                + "/"
                + record.relative_path,
                "sha256": record.sha256,
                "schema_version": open_evidence.ARTIFACT_SCHEMAS[reference_field],
            }
        permit = self.permit.to_dict()
        value = {
            "schema_version": open_evidence.SCHEMA_VERSION,
            "reserve_request_id": self.reserve_request.request.to_dict()["request_id"],
            "open_permit_digest": permit["permit_digest"],
            "reservation_id": permit["reservation_id"],
            "fencing_token": permit["fencing_token"],
            "run_id": self.proposal["run_id"],
            "identity_digest": self.proposal["identity_digest"],
            "runtime_profile_sha256": permit["runtime_profile_sha256"],
            "session_header_sha256": permit["session_header_sha256"],
            **refs,
            "opened_at": _utc(self._now()),
            "receipt_digest": "0" * 64,
            "executor_signature": "0" * 128,
        }
        value["receipt_digest"] = open_evidence.receipt_digest(
            open_evidence.parse_open_evidence_bytes(canonical_json_bytes(value))
        )
        value["executor_signature"] = self.holder.sign(
            canonical_json_bytes({k: v for k, v in value.items() if k != "executor_signature"})
        ).hex()
        checked = open_evidence.parse_open_evidence_bytes(canonical_json_bytes(value))
        payload = {
            "reservation_id": permit["reservation_id"],
            "reservation_digest": permit["reservation_digest"],
            "fencing_token": permit["fencing_token"],
            "run_identity": self.proposal,
            "reserve_request_id": value["reserve_request_id"],
            "open_permit_digest": permit["permit_digest"],
            "reservation_receipt_reference": permit["reservation_receipt_reference"],
            "expected_head": cfg["payload"]["expected_head"],
            "open_evidence": checked.to_dict(),
        }
        body = self._body("commit-open", payload)
        frame = self._sign(
            "open_evidence_frame",
            {
                "schema_version": "s11-ex10-b4-evidence-frame-v1",
                "exchange_id": self.channel.exchange_id,
                "open_permit_digest": permit["permit_digest"],
                "commit_request_body": body,
            },
        )
        self._send("OPEN_EVIDENCE", frame.to_dict())
        request, response = self._request(body)
        _bridge_require(
            request.request.to_dict()["request_id"] != value["reserve_request_id"]
            and request.capability.to_dict()["capability_id"]
            != self.reserve_request.capability.to_dict()["capability_id"],
            "SECOND_CAPABILITY_REQUIRED",
        )
        result = response.to_dict()["result"]
        _bridge_require(
            result["opened"] is True
            and result["open_evidence"] == checked.to_dict()
            and result["reservation"]["state"] == "COMMITTED"
            and result["reservation"]["reservation_id"] == permit["reservation_id"]
            and result["open_witness"]["revision"] == permit["expected_open_revision"],
            "OPEN_COMMIT_INVALID",
        )
        committed = wire.qualify_reservation(canonical_json_bytes(result["reservation"])).to_dict()
        _bridge_require(
            all(
                committed[k] == json.loads(original_intent)[k]
                for k in (*wire.RESERVATION_INTENT_FIELDS, "reservation_digest")
            ),
            "COMMITTED_INTENT_CHANGED",
        )
        _bridge_require(
            committed["record_sequence"] == self.reservation["record_sequence"] + 1
            and committed["previous_reservation_record_digest"]
            == self.reservation["record_digest"],
            "COMMITTED_CHAIN_INVALID",
        )
        _bridge_require(
            committed["open_witness_reference"]
            == result["open_witness"]
            == response.to_dict()["witness_head"]
            and committed["evidence_receipt_reference"]["sha256"]
            == sha256_bytes(checked.canonical),
            "OPEN_COMMIT_INVALID",
        )
        # The closed response carries references, not the full B3/B4 receipts.
        # A fixed host qualifier verifies their actual bytes, pins, chain/floors,
        # complete current history and this exact request/result; never self-trust
        # an embedded summary or invent another wire operation.
        _bridge_require(
            _fixed_provider(
                self.verify_commit, context=self, authenticated_request=request, response=response
            )
            is None,
            "OPEN_COMMIT_UNQUALIFIED",
        )
        self.reservation = committed
        self._check_reservation_intent(reader=reader)
        self.channel.finish()
        self._check_reservation_intent(reader=reader)
        self.finished = True
        return response

    def authorize_step(self, store, *, action, run_id, git_sha, phase):
        """One read-only five-frame inspection before any scientific side effect."""
        reader = self._intent_reader
        self._check_reservation_intent(reader=reader)
        _bridge_require(
            action == self.action
            and action != "start"
            and not self.step_authorized
            and not self.finished,
            "SECOND_OPERATION_DENIED",
        )
        _bridge_require(
            self.proposal["run_id"] == run_id and self.proposal["git_sha"] == git_sha,
            "RUN_ID_MISMATCH",
        )
        fd, _ = self._root(store)
        os.close(fd)
        self.check(run_id=run_id, phase=phase)
        challenge = self._challenge()
        body = self._body("inspect-reservation", self._cfg()["payload"])
        proposal = self._sign(
            "single_rpc_proposal",
            {
                "schema_version": "s11-ex10-b4-rpc-proposal-v1",
                "exchange_id": self.channel.exchange_id,
                "host_challenge_digest": challenge.sha256,
                "holder_public_key_hex": self.holder.public_key().public_bytes_raw().hex(),
                "request_body": body,
            },
        )
        self._send("RPC_PROPOSAL", proposal.to_dict())
        _, response = self._request(body)
        record = wire.qualify_reservation(
            canonical_json_bytes(response.to_dict()["result"]["reservation"])
        ).to_dict()
        _bridge_require(
            record == self._cfg()["expected_reservation"] and record["state"] == "COMMITTED",
            "OWNERSHIP_REQUIRED",
        )
        # This fixed provider independently verifies the complete current B3/floor,
        # process/deployment and authority state. A wire summary cannot replace it.
        _bridge_require(
            _fixed_provider(
                self.prior_verifier, context=self, common=None, reserve_response=response
            )
            is None,
            "AUTHORITY_REQUIRED",
        )
        self._check_reservation_intent(reader=reader)
        self.store = store
        self.channel.finish()
        self._retain_closed_single(store)
        self._check_reservation_intent(reader=reader)
        self.finished = True
        self.check(run_id=run_id, phase=phase)
        self._check_reservation_intent(reader=reader)
        self.step_authorized = True

    def persist_terminal_refusal(self, refusal, *, declare_executor_gone):
        """Retain the genuine negative with its original closed channel and seal.

        No scientific completion, B2 update, terminal publication or retry is
        granted here. The host independently qualifies this original output.
        """
        from . import ex10_prior_custody as prior

        reader, store, channel, config = (
            self._intent_reader,
            self.store,
            self.channel,
            self.config,
        )
        self._check_reservation_intent(reader=reader)
        closed_reader = self._closed_single_reader
        retained = closed_reader()
        _bridge_require(retained is not None, "ACTION_NOT_COMPLETED")
        original_store, proposal_bytes, store_facts, original_exchange = retained
        _bridge_require(store is original_store, "CONTEXT_BINDING_INVALID")
        _bridge_require(
            type(refusal) is Ex10Refusal
            and refusal.terminal is True
            and refusal.reasons == ("run-finalized",)
            and declare_executor_gone is True
            and self.action == "finalize"
            and self.finished is True
            and self.step_authorized is True
            and self.scientific_finished is False
            and self.terminal_prepared is False
            and store is not None
            and type(channel) is ExecutorBridgeChannel
            and channel.role == "EXECUTOR"
            and channel.exchange == "SINGLE_RPC"
            and channel.steps is SINGLE_STEPS
            and type(channel.position) is int
            and channel.position == 5
            and channel.owner == os.getpid()
            and channel.closed is True
            and channel.read_fd is None
            and channel.write_fd is None
            and channel.error_fd is None,
            "ACTION_NOT_COMPLETED",
        )
        run_id, exchange_id = self.proposal["run_id"], channel.exchange_id
        channel_facts = {
            name: getattr(channel, name)
            for name in (
                "role",
                "exchange",
                "exchange_id",
                "position",
                "closed",
                "read_fd",
                "write_fd",
                "error_fd",
                "owner",
                "deadline",
                "budget",
            )
        }
        steps, process_guard = channel.steps, channel.guard
        providers = self.admit, self.guard, self.clock
        store_root, key, publication_key = store_facts
        deadline = original_exchange[5]

        def binding():
            _bridge_require(
                closed_reader() is retained and time.monotonic() < deadline,
                "CONTEXT_BINDING_INVALID",
            )
            _bridge_require(
                self.store is store
                and self.channel is channel
                and self.config == config
                and canonical_json_bytes(self.proposal) == proposal_bytes
                and self.finished is True
                and self.step_authorized is True
                and self.scientific_finished is False
                and self.terminal_prepared is False
                and channel.steps is steps
                and channel.guard is process_guard
                and all(
                    actual is original
                    for actual, original in zip(
                        (self.admit, self.guard, self.clock), providers, strict=False
                    )
                )
                and all(
                    type(getattr(channel, name)) is type(value) and getattr(channel, name) == value
                    for name, value in channel_facts.items()
                )
                and str(store.root) == store_root
                and type(store.custody_hmac_key) is type(key)
                and store.custody_hmac_key == key
                and type(store.publication_verification_key) is type(publication_key)
                and store.publication_verification_key == publication_key,
                "CONTEXT_BINDING_INVALID",
            )

        def current():
            binding()
            self._check_reservation_intent(reader=reader)
            binding()
            self.check(run_id=run_id, phase="SCIENTIFIC_REFUSAL")
            binding()
            self._check_reservation_intent(reader=reader)
            binding()

        def retained_current():
            binding()
            self._check_reservation_intent(reader=reader)
            _bridge_require(time.monotonic() < deadline, "CONTEXT_BINDING_INVALID")
            binding()

        current()
        root_fd, lease = self._root(store)
        try:
            components = _native_refusal_path_identities(lease)
            with _native_refusal_custody_lock(root_fd, retained_current, deadline) as lock:
                lock_stamp, locked_current = lock
                sealed = prior._PriorFDReader(root_fd, locked_current)
                actual = prior._prior_run(sealed, run_id, key, publication_key)
                _bridge_require(
                    actual.run_id == run_id
                    and actual.evidence_mode == OFFICIAL_MODE
                    and actual.finalized is True
                    and actual.problems == (),
                    "ACTION_NOT_COMPLETED",
                )
                frozen = sealed.freeze()
                _bridge_require(not frozen[1], "RESULT_INVALID")
                sealed.verify(frozen)
                # Requalify the complete signed authority immediately before
                # the only new private byte write. Replaying that full proof
                # for every local stat can itself exhaust its freshness window.
                current()
                raw = canonical_json_bytes(
                    {
                        "refused": True,
                        "terminal": refusal.terminal,
                        "reasons": list(refusal.reasons),
                    }
                )
                _bridge_require(raw == _native_terminal_refusal_bytes(), "RESULT_INVALID")
                self._create_private(store, "executor-result." + exchange_id + ".json", raw)
                retained_current()
                sealed.verify(frozen)
        finally:
            os.close(root_fd)
        # Shared custody unlock and all retained FDs finish before passive
        # comparison of the ORIGINAL authenticated seal. The external proof
        # was requalified immediately before the private result write.
        retained_current()
        _bridge_require(_native_refusal_path_identities(lease) == components, "RESULT_INVALID")
        for parts, expected in frozen[0]:
            path = Path(store_root).joinpath(*parts)
            try:
                observed = prior._PriorFDReader._stamp(os.lstat(path))
            except FileNotFoundError:
                observed = None
            _bridge_require(observed == expected, "RESULT_INVALID")
        _bridge_require(
            prior._PriorFDReader._stamp(os.lstat(Path(store_root) / ".s11-custody.lock"))
            == lock_stamp,
            "RESULT_INVALID",
        )
        binding()

    def persist_public_result(self, store, result):
        """Existing public JSON as one bounded private operational output."""
        _bridge_require(self.finished and self.scientific_finished, "ACTION_NOT_COMPLETED")
        reader = self._intent_reader
        self._check_reservation_intent(reader=reader)
        self._admit()
        _bridge_require(
            _fixed_provider(
                self.guard,
                context=self,
                action=self.action,
                run_id=result["run_id"],
                phase="PUBLIC_RESULT",
            )
            is None,
            "OWNERSHIP_REQUIRED",
        )
        self._check_reservation_intent(reader=reader)
        raw = canonical_json_bytes(result)
        _bridge_require(len(raw) <= wire.MAX_REQUEST_BYTES, "RESULT_LIMIT")
        self._create_private(store, "executor-result." + self.channel.exchange_id + ".json", raw)

    def prepare_sealed_terminal(self, store, *, run_id, result):
        """Prepare sealed private output; only the host can publish terminal B3.

        The host must qualify this seal/result after the worker exits, publish
        its terminal receipt and retire the slot before returning public JSON.
        This latch attests preparation only, never that publication succeeded.
        """
        reader = self._intent_reader
        self._check_reservation_intent(reader=reader)
        _bridge_require(
            self.action == "finalize" and self.step_authorized and not self.terminal_prepared,
            "TERMINAL_PUBLICATION_DENIED",
        )
        self.check(run_id=run_id, phase="SEALED")
        state = load_existing_state_readonly(store, run_id)
        _bridge_require(state is not None and state.finalized, "CUSTODY_SEAL_REQUIRED")
        validate_finalized_state(
            state.run_root, state.records, custody_hmac_key=store.custody_hmac_key
        )
        _bridge_require(
            _fixed_provider(
                self.prepare_terminal, context=self, store=store, run_id=run_id, result=result
            )
            is None,
            "TERMINAL_PREPARATION_UNKNOWN",
        )
        self._check_reservation_intent(reader=reader)
        self.terminal_prepared = True


# Exact approved B3 read-only column/projection/witness rules; no host imports.
_EXECUTOR_B3_COLUMNS = {
    "artifact_references": {
        "introduced_revision": {"not_null": True, "type": "int64_nonnegative"},
        "kind": {
            "not_null": True,
            "type": "enum",
            "values": [
                "EVIDENCE",
                "SESSION",
                "PROFILE",
                "SOURCE_ANCHOR",
                "APPROVAL",
                "COVERAGE",
                "BOOTSTRAP",
                "GOVERNANCE",
                "SNAPSHOT",
            ],
        },
        "path": {"not_null": True, "type": "locator"},
        "reference_id": {"not_null": True, "type": "sha256"},
        "schema_version": {"not_null": True, "type": "identifier"},
        "sha256": {"not_null": True, "type": "sha256"},
    },
    "authority_events": {
        "event_kind": {
            "not_null": True,
            "type": "enum",
            "values": [
                "GENESIS",
                "IMPORT_RUN",
                "RUN_OPENED",
                "RUN_STATE",
                "RUN_TERMINAL",
                "RUN_UNCERTAIN",
                "AUTHORITY_UNCERTAIN",
                "MANUAL_RESOLUTION",
                "COVERAGE_EXTENSION",
            ],
        },
        "event_sequence": {"not_null": True, "type": "positive_int64"},
        "evidence_reference_id": {"not_null": True, "type": "sha256"},
        "occurred_at": {"not_null": True, "type": "utc"},
        "reason_code": {"not_null": False, "type": "identifier"},
        "resolves_event_sequence": {"not_null": False, "type": "positive_int64"},
        "revision": {"not_null": True, "type": "int64_nonnegative"},
        "run_id": {"not_null": False, "type": "run_id"},
        "run_version": {"not_null": False, "type": "int64_nonnegative"},
    },
    "bootstrap_findings": {
        "finding_reference_id": {"not_null": True, "type": "sha256"},
        "is_primary_evidence": {"not_null": True, "type": "bool01"},
        "run_id": {"not_null": True, "type": "run_id"},
        "run_version": {"not_null": True, "type": "int64_nonnegative"},
        "source_id": {"not_null": True, "type": "identifier"},
    },
    "bootstrap_manifest": {
        "baseline_head": {"not_null": True, "type": "git_sha1"},
        "baseline_tree": {"not_null": True, "type": "git_sha1"},
        "certified_at": {"not_null": True, "type": "utc"},
        "completeness": {"not_null": True, "type": "enum", "values": ["COMPLETE"]},
        "core_digest": {"not_null": True, "type": "sha256"},
        "coverage_certificate_reference_id": {"not_null": True, "type": "sha256"},
        "manifest_reference_id": {"not_null": True, "type": "sha256"},
        "operator_approval_reference_id": {"not_null": True, "type": "sha256"},
        "run_count": {"not_null": True, "type": "int64_nonnegative"},
        "scope_reference_id": {"not_null": True, "type": "sha256"},
        "singleton": {"not_null": True, "type": "positive_int64"},
        "source_count": {"not_null": True, "type": "positive_int64"},
    },
    "bootstrap_sources": {
        "discovered_run_count": {"not_null": True, "type": "int64_nonnegative"},
        "has_active_source": {"not_null": True, "type": "bool01"},
        "location": {"not_null": True, "type": "locator"},
        "observation_reference_id": {"not_null": True, "type": "sha256"},
        "observed_at": {"not_null": True, "type": "utc"},
        "scope_reference_id": {"not_null": True, "type": "sha256"},
        "snapshot_reference_id": {"not_null": False, "type": "sha256"},
        "source_id": {"not_null": True, "type": "identifier"},
        "source_kind": {
            "not_null": True,
            "type": "enum",
            "values": [
                "EVIDENCE_ROOT",
                "SESSION_ROOT",
                "SOURCE_ANCHOR",
                "CUSTODY",
                "GOVERNANCE",
                "WRITER_REGISTRY",
            ],
        },
        "status": {
            "not_null": True,
            "type": "enum",
            "values": ["SCANNED", "ABSENT_CERTIFIED", "UNREADABLE", "BLOCKED"],
        },
    },
    "history_revisions": {
        "event_count": {"not_null": True, "type": "positive_int64"},
        "first_event_sequence": {"not_null": True, "type": "positive_int64"},
        "last_event_sequence": {"not_null": True, "type": "positive_int64"},
        "ledger_digest": {"not_null": True, "type": "sha256"},
        "operation_id": {"not_null": True, "type": "uuid"},
        "previous_ledger_digest": {"not_null": False, "type": "sha256"},
        "recorded_at": {"not_null": True, "type": "utc"},
        "revision": {"not_null": True, "type": "int64_nonnegative"},
    },
    "metadata": {
        "authority_id": {"not_null": True, "type": "uuid"},
        "bootstrap_reference_id": {"not_null": True, "type": "sha256"},
        "descriptor_sha256": {"not_null": True, "type": "sha256"},
        "epoch": {"not_null": True, "type": "positive_int64"},
        "g0_a3_approval_reference_id": {"not_null": True, "type": "sha256"},
        "namespace": {"not_null": True, "type": "enum", "values": ["S11C3-EX10"]},
        "schema_version": {
            "not_null": True,
            "type": "enum",
            "values": ["s11-ex10-historical-authority-ledger-v1"],
        },
        "singleton": {"not_null": True, "type": "positive_int64"},
        "source_baseline_head": {"not_null": True, "type": "git_sha1"},
        "source_baseline_tree": {"not_null": True, "type": "git_sha1"},
    },
    "official_runs": {
        "ack_required": {"not_null": True, "type": "bool01"},
        "binding": {"not_null": True, "type": "bool01"},
        "created_at": {"not_null": True, "type": "utc"},
        "evidence_authority_reference_id": {"not_null": True, "type": "sha256"},
        "finalized_at": {"not_null": False, "type": "utc"},
        "introduced_revision": {"not_null": True, "type": "int64_nonnegative"},
        "lifecycle_state": {
            "not_null": True,
            "type": "enum",
            "values": ["OPEN", "ACTIVE", "UNCERTAIN", "TERMINAL"],
        },
        "methodology": {"not_null": True, "type": "identifier"},
        "mode": {"not_null": True, "type": "enum", "values": ["OFFICIAL"]},
        "opened_at": {"not_null": True, "type": "utc"},
        "phase": {
            "not_null": True,
            "type": "enum",
            "values": ["OPENED", "EXECUTING", "DRAINED", "RECONCILED", "UNCERTAIN", "FINALIZED"],
        },
        "previous_run_version": {"not_null": False, "type": "int64_nonnegative"},
        "profile_reference_id": {"not_null": False, "type": "sha256"},
        "protocol": {"not_null": True, "type": "identifier"},
        "provenance_model": {
            "not_null": True,
            "type": "enum",
            "values": ["HISTORICAL_V1", "PROFILE_V2"],
        },
        "result": {
            "not_null": False,
            "type": "enum",
            "values": ["PASS", "FAIL", "NOT_DEMONSTRATED"],
        },
        "run_id": {"not_null": True, "type": "run_id"},
        "run_version": {"not_null": True, "type": "int64_nonnegative"},
        "session_reference_id": {"not_null": False, "type": "sha256"},
        "source_revision": {"not_null": True, "type": "git_sha1"},
        "validity_classification": {
            "not_null": True,
            "type": "enum",
            "values": ["PENDING", "UNCERTAIN", "VALID_BINDING", "NON_BINDING_ND"],
        },
    },
    "publication_intents": {
        "base_ledger_digest": {"not_null": False, "type": "sha256"},
        "base_revision": {"not_null": False, "type": "int64_nonnegative"},
        "candidate_ledger_digest": {"not_null": True, "type": "sha256"},
        "candidate_payload_path": {"not_null": True, "type": "locator"},
        "candidate_payload_sha256": {"not_null": True, "type": "sha256"},
        "candidate_revision": {"not_null": True, "type": "int64_nonnegative"},
        "created_at": {"not_null": True, "type": "utc"},
        "operation_id": {"not_null": True, "type": "uuid"},
        "serialization_capability_digest": {"not_null": True, "type": "sha256"},
    },
    "publication_resolutions": {
        "committed_revision": {"not_null": False, "type": "int64_nonnegative"},
        "disposition": {
            "not_null": True,
            "type": "enum",
            "values": ["COMMITTED", "MANUAL_CANCELLED"],
        },
        "fresh_witness_observation_digest": {"not_null": True, "type": "sha256"},
        "operation_id": {"not_null": True, "type": "uuid"},
        "operator_approval_path": {"not_null": False, "type": "locator"},
        "operator_approval_schema": {"not_null": False, "type": "identifier"},
        "operator_approval_sha256": {"not_null": False, "type": "sha256"},
        "receipt_digest": {"not_null": False, "type": "sha256"},
        "resolved_at": {"not_null": True, "type": "utc"},
    },
    "witness_checkpoints": {
        "authority_epoch": {"not_null": True, "type": "positive_int64"},
        "authority_id": {"not_null": True, "type": "uuid"},
        "created_at": {"not_null": True, "type": "utc"},
        "genesis_baseline_head": {"not_null": False, "type": "git_sha1"},
        "genesis_baseline_tree": {"not_null": False, "type": "git_sha1"},
        "genesis_bootstrap_sha256": {"not_null": False, "type": "sha256"},
        "genesis_g0_a3_approval_path": {"not_null": False, "type": "locator"},
        "genesis_g0_a3_approval_schema": {"not_null": False, "type": "identifier"},
        "genesis_g0_a3_approval_sha256": {"not_null": False, "type": "sha256"},
        "key_epoch": {"not_null": True, "type": "positive_int64"},
        "key_fingerprint": {"not_null": True, "type": "sha256"},
        "key_id": {"not_null": True, "type": "identifier"},
        "ledger_digest": {"not_null": True, "type": "sha256"},
        "operation_id": {"not_null": True, "type": "uuid"},
        "previous_witness_digest": {"not_null": False, "type": "sha256"},
        "receipt_schema_version": {
            "not_null": True,
            "type": "enum",
            "values": ["s11-ex10-authority-witness-receipt-v1"],
        },
        "revision": {"not_null": True, "type": "int64_nonnegative"},
        "signature": {"not_null": True, "type": "signature_ed25519"},
        "witness_digest": {"not_null": True, "type": "sha256"},
    },
}

_EXECUTOR_B3_LOGICAL_FIELDS = [
    "schema_version",
    "metadata",
    "revision",
    "artifact_references",
    "official_runs",
    "history_revisions",
    "authority_events",
    "bootstrap_manifest",
    "bootstrap_sources",
    "bootstrap_findings",
]

_EXECUTOR_B3_ORDERS = {
    "artifact_references": ["reference_id"],
    "authority_events": ["event_sequence"],
    "bootstrap_findings": ["source_id", "run_id"],
    "bootstrap_sources": ["source_id"],
    "history_revisions": ["revision"],
    "official_runs": ["run_id", "run_version"],
}

_EXECUTOR_B3_WITNESS_SCHEMAS = {
    "current_head": {
        "additionalProperties": False,
        "properties": {
            "authority_epoch": {"maximum": 9007199254740991, "minimum": 1, "type": "integer"},
            "authority_id": {
                "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                "type": "string",
            },
            "challenge": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
            "expires_at": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
            "issued_at": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
            "key_epoch": {"maximum": 9007199254740991, "minimum": 1, "type": "integer"},
            "key_fingerprint": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
            "key_id": {"minLength": 1, "type": "string"},
            "ledger_digest": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
            "namespace": {"const": "S11C3-EX10"},
            "revision": {"maximum": 9007199254740991, "minimum": 0, "type": "integer"},
            "signature": {"pattern": "^[0-9a-f]{128}$", "type": "string"},
            "witness_digest": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
            "witness_schema_version": {"const": "s11-ex10-authority-witness-head-v1"},
        },
        "required": [
            "witness_schema_version",
            "authority_id",
            "authority_epoch",
            "namespace",
            "challenge",
            "revision",
            "ledger_digest",
            "witness_digest",
            "issued_at",
            "expires_at",
            "key_id",
            "key_epoch",
            "key_fingerprint",
            "signature",
        ],
        "type": "object",
    },
    "receipt": {
        "additionalProperties": False,
        "allOf": [
            {
                "else": {
                    "properties": {
                        "genesis": {"type": "null"},
                        "previous_witness_digest": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                    }
                },
                "if": {"properties": {"revision": {"const": 0}}},
                "then": {
                    "properties": {
                        "genesis": {
                            "additionalProperties": False,
                            "properties": {
                                "bootstrap_manifest_sha256": {
                                    "pattern": "^[0-9a-f]{64}$",
                                    "type": "string",
                                },
                                "g0_a3_approval_reference": {
                                    "additionalProperties": False,
                                    "properties": {
                                        "path": {"minLength": 1, "type": "string"},
                                        "schema_version": {"minLength": 1, "type": "string"},
                                        "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                                    },
                                    "required": ["path", "sha256", "schema_version"],
                                    "type": "object",
                                },
                                "source_baseline_head": {
                                    "pattern": "^[0-9a-f]{40}$",
                                    "type": "string",
                                },
                                "source_baseline_tree": {
                                    "pattern": "^[0-9a-f]{40}$",
                                    "type": "string",
                                },
                            },
                            "required": [
                                "bootstrap_manifest_sha256",
                                "g0_a3_approval_reference",
                                "source_baseline_head",
                                "source_baseline_tree",
                            ],
                            "type": "object",
                        },
                        "previous_witness_digest": {"type": "null"},
                    }
                },
            }
        ],
        "properties": {
            "authority_epoch": {"maximum": 9007199254740991, "minimum": 1, "type": "integer"},
            "authority_id": {
                "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                "type": "string",
            },
            "created_at": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
            "genesis": {
                "anyOf": [
                    {
                        "additionalProperties": False,
                        "properties": {
                            "bootstrap_manifest_sha256": {
                                "pattern": "^[0-9a-f]{64}$",
                                "type": "string",
                            },
                            "g0_a3_approval_reference": {
                                "additionalProperties": False,
                                "properties": {
                                    "path": {"minLength": 1, "type": "string"},
                                    "schema_version": {"minLength": 1, "type": "string"},
                                    "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                                },
                                "required": ["path", "sha256", "schema_version"],
                                "type": "object",
                            },
                            "source_baseline_head": {"pattern": "^[0-9a-f]{40}$", "type": "string"},
                            "source_baseline_tree": {"pattern": "^[0-9a-f]{40}$", "type": "string"},
                        },
                        "required": [
                            "bootstrap_manifest_sha256",
                            "g0_a3_approval_reference",
                            "source_baseline_head",
                            "source_baseline_tree",
                        ],
                        "type": "object",
                    },
                    {"type": "null"},
                ]
            },
            "key_epoch": {"maximum": 9007199254740991, "minimum": 1, "type": "integer"},
            "key_fingerprint": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
            "key_id": {"pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$", "type": "string"},
            "ledger_digest": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
            "namespace": {"const": "S11C3-EX10"},
            "operation_id": {
                "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                "type": "string",
            },
            "previous_witness_digest": {
                "anyOf": [{"pattern": "^[0-9a-f]{64}$", "type": "string"}, {"type": "null"}]
            },
            "revision": {"maximum": 9007199254740991, "minimum": 0, "type": "integer"},
            "signature": {"pattern": "^[0-9a-f]{128}$", "type": "string"},
            "witness_schema_version": {"const": "s11-ex10-authority-witness-receipt-v1"},
        },
        "required": [
            "witness_schema_version",
            "authority_id",
            "authority_epoch",
            "namespace",
            "operation_id",
            "revision",
            "ledger_digest",
            "previous_witness_digest",
            "created_at",
            "key_id",
            "key_epoch",
            "key_fingerprint",
            "genesis",
            "signature",
        ],
        "type": "object",
    },
}

_EXECUTOR_B3_TABLE_CONSTRAINTS = {
    "artifact_references": {
        "columns": {},
        "foreign_keys": [
            {
                "columns": ["introduced_revision"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["revision"],
                "references_table": "history_revisions",
            }
        ],
        "primary_key": ["reference_id"],
        "unique": [["path", "sha256", "schema_version", "kind"]],
    },
    "authority_events": {
        "columns": {},
        "foreign_keys": [
            {
                "columns": ["revision"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["revision"],
                "references_table": "history_revisions",
            },
            {
                "columns": ["run_id", "run_version"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["run_id", "run_version"],
                "references_table": "official_runs",
            },
            {
                "columns": ["evidence_reference_id"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["reference_id"],
                "references_table": "artifact_references",
            },
            {
                "columns": ["resolves_event_sequence"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["event_sequence"],
                "references_table": "authority_events",
            },
        ],
        "primary_key": ["event_sequence"],
        "unique": [],
    },
    "bootstrap_findings": {
        "columns": {},
        "foreign_keys": [
            {
                "columns": ["source_id"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["source_id"],
                "references_table": "bootstrap_sources",
            },
            {
                "columns": ["run_id", "run_version"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["run_id", "run_version"],
                "references_table": "official_runs",
            },
            {
                "columns": ["finding_reference_id"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["reference_id"],
                "references_table": "artifact_references",
            },
        ],
        "primary_key": ["source_id", "run_id"],
        "unique": [["source_id", "finding_reference_id"]],
    },
    "bootstrap_manifest": {
        "columns": {},
        "foreign_keys": [
            {
                "columns": ["manifest_reference_id"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["reference_id"],
                "references_table": "artifact_references",
            },
            {
                "columns": ["scope_reference_id"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["reference_id"],
                "references_table": "artifact_references",
            },
            {
                "columns": ["coverage_certificate_reference_id"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["reference_id"],
                "references_table": "artifact_references",
            },
            {
                "columns": ["operator_approval_reference_id"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["reference_id"],
                "references_table": "artifact_references",
            },
        ],
        "primary_key": ["singleton"],
        "unique": [["manifest_reference_id"]],
    },
    "bootstrap_sources": {
        "columns": {},
        "foreign_keys": [
            {
                "columns": ["scope_reference_id"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["reference_id"],
                "references_table": "artifact_references",
            },
            {
                "columns": ["snapshot_reference_id"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["reference_id"],
                "references_table": "artifact_references",
            },
            {
                "columns": ["observation_reference_id"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["reference_id"],
                "references_table": "artifact_references",
            },
        ],
        "primary_key": ["source_id"],
        "unique": [["source_kind", "location"]],
    },
    "history_revisions": {
        "columns": {},
        "foreign_keys": [],
        "primary_key": ["revision"],
        "unique": [["operation_id"]],
    },
    "metadata": {
        "columns": {},
        "foreign_keys": [
            {
                "columns": ["bootstrap_reference_id"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["reference_id"],
                "references_table": "artifact_references",
            },
            {
                "columns": ["g0_a3_approval_reference_id"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["reference_id"],
                "references_table": "artifact_references",
            },
        ],
        "primary_key": ["singleton"],
        "unique": [["authority_id", "epoch"]],
    },
    "official_runs": {
        "columns": {},
        "foreign_keys": [
            {
                "columns": ["introduced_revision"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["revision"],
                "references_table": "history_revisions",
            },
            {
                "columns": ["evidence_authority_reference_id"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["reference_id"],
                "references_table": "artifact_references",
            },
            {
                "columns": ["profile_reference_id"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["reference_id"],
                "references_table": "artifact_references",
            },
            {
                "columns": ["session_reference_id"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["reference_id"],
                "references_table": "artifact_references",
            },
            {
                "columns": ["run_id", "previous_run_version"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["run_id", "run_version"],
                "references_table": "official_runs",
            },
        ],
        "primary_key": ["run_id", "run_version"],
        "unique": [["run_id", "introduced_revision"]],
    },
    "publication_intents": {
        "columns": {},
        "foreign_keys": [],
        "primary_key": ["operation_id"],
        "unique": [["candidate_revision"]],
    },
    "publication_resolutions": {
        "columns": {},
        "foreign_keys": [
            {
                "columns": ["operation_id"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["operation_id"],
                "references_table": "publication_intents",
            },
            {
                "columns": ["committed_revision"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["revision"],
                "references_table": "history_revisions",
            },
        ],
        "primary_key": ["operation_id"],
        "unique": [],
    },
    "witness_checkpoints": {
        "columns": {},
        "foreign_keys": [
            {
                "columns": ["revision"],
                "deferrable": "INITIALLY_DEFERRED",
                "on_delete": "RESTRICT",
                "on_update": "RESTRICT",
                "references_columns": ["revision"],
                "references_table": "history_revisions",
            }
        ],
        "primary_key": ["revision"],
        "unique": [["operation_id"], ["witness_digest"]],
    },
}
_EXECUTOR_B3_TRANSITIONS = {
    "ACTIVE": ["ACTIVE", "UNCERTAIN", "TERMINAL"],
    "OPEN": ["ACTIVE", "UNCERTAIN", "TERMINAL"],
    "TERMINAL": [],
    "UNCERTAIN": ["UNCERTAIN", "ACTIVE", "TERMINAL"],
    "manual_requirements": "Every exit from UNCERTAIN requires separate operator approval, "
    "external "
    "witness freshness and evidence establishing the next state; "
    "OPEN->TERMINAL also requires complete authoritative terminal evidence. "
    "Terminal immutable even by manual resolution.",
}
_EXECUTOR_B3_PUBLIC_SCHEMAS = {
    "authority_descriptor": {
        "additionalProperties": False,
        "properties": {
            "authority_epoch": {"maximum": 9007199254740991, "minimum": 1, "type": "integer"},
            "authority_id": {
                "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                "type": "string",
            },
            "authority_schema_version": {"const": "s11-ex10-historical-authority-ledger-v1"},
            "bootstrap_manifest_reference": {
                "additionalProperties": False,
                "properties": {
                    "path": {"minLength": 1, "type": "string"},
                    "schema_version": {"minLength": 1, "type": "string"},
                    "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                },
                "required": ["path", "sha256", "schema_version"],
                "type": "object",
            },
            "g0_a3_approval_reference": {
                "additionalProperties": False,
                "properties": {
                    "path": {"minLength": 1, "type": "string"},
                    "schema_version": {"minLength": 1, "type": "string"},
                    "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                },
                "required": ["path", "sha256", "schema_version"],
                "type": "object",
            },
            "ledger_locator": {"minLength": 1, "type": "string"},
            "minimum_revision": {"maximum": 9007199254740991, "minimum": 0, "type": "integer"},
            "namespace": {"const": "S11C3-EX10"},
            "revision_policy": {"const": "IDENTITY_MINIMUM_AND_FRESH_CURRENT_HEAD"},
            "schema_version": {"const": "s11-ex10-historical-authority-descriptor-v1"},
            "source_baseline_head": {"pattern": "^[0-9a-f]{40}$", "type": "string"},
            "source_baseline_tree": {"pattern": "^[0-9a-f]{40}$", "type": "string"},
            "trust_root_reference": {
                "additionalProperties": False,
                "properties": {
                    "path": {"minLength": 1, "type": "string"},
                    "schema_version": {"minLength": 1, "type": "string"},
                    "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                },
                "required": ["path", "sha256", "schema_version"],
                "type": "object",
            },
            "witness_head_reference": {
                "additionalProperties": False,
                "properties": {
                    "authority_epoch": {
                        "maximum": 9007199254740991,
                        "minimum": 1,
                        "type": "integer",
                    },
                    "authority_id": {
                        "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                        "type": "string",
                    },
                    "endpoint": {"pattern": "^https://", "type": "string"},
                    "namespace": {"const": "S11C3-EX10"},
                    "schema_version": {"const": "s11-ex10-authority-witness-head-v1"},
                    "transport_identity_reference": {
                        "additionalProperties": False,
                        "properties": {
                            "path": {"minLength": 1, "type": "string"},
                            "schema_version": {"minLength": 1, "type": "string"},
                            "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                        },
                        "required": ["path", "sha256", "schema_version"],
                        "type": "object",
                    },
                },
                "required": [
                    "schema_version",
                    "endpoint",
                    "transport_identity_reference",
                    "authority_id",
                    "authority_epoch",
                    "namespace",
                ],
                "type": "object",
            },
        },
        "required": [
            "schema_version",
            "authority_id",
            "authority_epoch",
            "namespace",
            "authority_schema_version",
            "bootstrap_manifest_reference",
            "g0_a3_approval_reference",
            "source_baseline_head",
            "source_baseline_tree",
            "ledger_locator",
            "trust_root_reference",
            "minimum_revision",
            "revision_policy",
            "witness_head_reference",
        ],
        "type": "object",
    },
    "external_reference": {
        "additionalProperties": False,
        "properties": {
            "path": {"minLength": 1, "type": "string"},
            "schema_version": {"minLength": 1, "type": "string"},
            "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
        },
        "required": ["path", "sha256", "schema_version"],
        "type": "object",
    },
    "trust_root": {
        "additionalProperties": False,
        "properties": {
            "activation_revision": {"maximum": 9007199254740991, "minimum": 0, "type": "integer"},
            "algorithm": {"const": "Ed25519"},
            "approval_reference": {
                "additionalProperties": False,
                "properties": {
                    "path": {"minLength": 1, "type": "string"},
                    "schema_version": {"minLength": 1, "type": "string"},
                    "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                },
                "required": ["path", "sha256", "schema_version"],
                "type": "object",
            },
            "authority_epoch": {"maximum": 9007199254740991, "minimum": 1, "type": "integer"},
            "authority_id": {
                "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                "type": "string",
            },
            "fingerprint": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
            "key_epoch": {"maximum": 9007199254740991, "minimum": 1, "type": "integer"},
            "key_id": {"minLength": 1, "type": "string"},
            "key_transition_reference": {
                "anyOf": [
                    {
                        "additionalProperties": False,
                        "properties": {
                            "path": {"minLength": 1, "type": "string"},
                            "schema_version": {"minLength": 1, "type": "string"},
                            "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                        },
                        "required": ["path", "sha256", "schema_version"],
                        "type": "object",
                    },
                    {"type": "null"},
                ]
            },
            "namespace": {"const": "S11C3-EX10"},
            "public_key_hex": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
            "retirement_revision": {
                "anyOf": [
                    {"maximum": 9007199254740991, "minimum": 0, "type": "integer"},
                    {"type": "null"},
                ]
            },
            "schema_version": {"const": "s11-ex10-authority-trust-root-v1"},
            "transport_identity_reference": {
                "additionalProperties": False,
                "properties": {
                    "path": {"minLength": 1, "type": "string"},
                    "schema_version": {"minLength": 1, "type": "string"},
                    "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                },
                "required": ["path", "sha256", "schema_version"],
                "type": "object",
            },
        },
        "required": [
            "schema_version",
            "authority_id",
            "authority_epoch",
            "namespace",
            "key_id",
            "key_epoch",
            "algorithm",
            "public_key_hex",
            "fingerprint",
            "approval_reference",
            "key_transition_reference",
            "activation_revision",
            "retirement_revision",
            "transport_identity_reference",
        ],
        "type": "object",
    },
}


class ExecutorHostProofs:
    """Read existing host-qualified INTERNAL material through the evidence mount.

    The host still owns full B3/GOV/coverage/floor qualification and action
    serialization. This client verifies complete copied projection bytes and
    independent signed heads, never an unauthenticated DTO or a true flag.
    Paths are fixed roles under the independently selected exchange namespace;
    references to host paths are compared only and never opened by the backend.
    """

    def __init__(self, *, root, exchange_id, b3_pins, genesis_digest, genesis_binding, clock):
        profile_codec._absolute(str(root))
        _bridge_require(str(uuid.UUID(exchange_id)) == exchange_id, "PROTOCOL_INVALID")
        _bridge_require(type(b3_pins) is list and b3_pins, "INDEPENDENT_PIN_REQUIRED")
        self.root = str(root)
        self.exchange_id = exchange_id
        self.clock = clock
        for pin in b3_pins:
            self._validate_schema(pin, _EXECUTOR_B3_PUBLIC_SCHEMAS["trust_root"])
            _bridge_require(
                sha256_bytes(bytes.fromhex(pin["public_key_hex"])) == pin["fingerprint"]
                and (
                    pin["retirement_revision"] is None
                    or pin["retirement_revision"] >= pin["activation_revision"]
                ),
                "INDEPENDENT_PIN_REQUIRED",
            )
        _bridge_require(
            len({(p["key_id"], p["key_epoch"]) for p in b3_pins}) == len(b3_pins),
            "INDEPENDENT_PIN_REQUIRED",
        )
        self.pins = canonical_json_bytes(b3_pins)
        self.observed_revision = None
        self.coordination_sequence = None
        self.current_observation = None
        self.genesis_digest = genesis_digest
        self.genesis_binding = canonical_json_bytes(genesis_binding)
        self.owner = (os.getpid(), threading.get_ident())

    def read(self, role):
        roles = {
            "base-material",
            "base-head",
            "base-coordination",
            "base-receipt",
            "commit-material",
            "commit-head",
            "commit-coordination",
            "commit-receipt",
            "action-material",
            "action-head",
            "action-coordination",
            "action-receipt",
            "emission-material",
            "emission-head",
            "emission-coordination",
            "emission-receipt",
            "profile",
            "header",
            "envelope",
            "reservation",
            "process",
        }
        _bridge_require(
            role in roles and self.owner == (os.getpid(), threading.get_ident()),
            "HOST_PROOF_INVALID",
        )
        path = self.root + "/.s11-operational/" + self.exchange_id + "/" + role + ".json"
        directories = (
            self.root,
            self.root + "/.s11-operational",
            self.root + "/.s11-operational/" + self.exchange_id,
        )
        leases = []
        for directory in directories:
            dfd, lease = profile_codec._walk(directory, "directory")
            try:
                info = os.fstat(dfd)
                _bridge_require(
                    (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
                    == (os.geteuid(), os.getegid(), 0o700),
                    "HOST_PROOF_INVALID",
                )
                leases.append(lease)
            finally:
                os.close(dfd)
        fd, lease = profile_codec._walk(path, "file")
        try:
            first = os.fstat(fd)
            _bridge_require(
                first.st_nlink == 1
                and (first.st_uid, first.st_gid, stat.S_IMODE(first.st_mode))
                == (os.geteuid(), os.getegid(), 0o600)
                and 0 < first.st_size <= 16 * 1024 * 1024,
                "HOST_PROOF_INVALID",
            )
            raw = bytearray()
            while len(raw) <= first.st_size:
                chunk = os.read(fd, min(65536, first.st_size + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            check, current = profile_codec._walk(path, "file")
            try:

                def stamp(i):
                    return (
                        i.st_dev,
                        i.st_ino,
                        i.st_size,
                        i.st_mtime_ns,
                        i.st_ctime_ns,
                        i.st_uid,
                        i.st_gid,
                        i.st_mode,
                        i.st_nlink,
                    )

                _bridge_require(
                    stamp(first) == stamp(os.fstat(fd)) == stamp(os.fstat(check))
                    and lease == current
                    and len(raw) == first.st_size,
                    "HOST_PROOF_CHANGED",
                )
            finally:
                os.close(check)
            for directory, before in zip(directories, leases, strict=True):
                check, after = profile_codec._walk(directory, "directory")
                try:
                    info = os.fstat(check)
                    _bridge_require(
                        before == after
                        and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
                        == (os.geteuid(), os.getegid(), 0o700),
                        "HOST_PROOF_CHANGED",
                    )
                finally:
                    os.close(check)
            data = bytes(raw)
            return open_evidence.parse_canonical_bytes(data, maximum=16 * 1024 * 1024), data
        except ExecutorBridgeError:
            raise
        except Exception:
            raise ExecutorBridgeError("HOST_PROOF_INVALID") from None
        finally:
            os.close(fd)

    @staticmethod
    def _canonical_value(value):
        if type(value) is str:
            ExecutorHostProofs._require(
                unicodedata.normalize("NFC", value) == value, "NONCANONICAL"
            )
            ExecutorHostProofs._require(
                not any(55296 <= ord(c) <= 57343 for c in value), "NONCANONICAL"
            )
        elif type(value) is dict:
            ExecutorHostProofs._require(all(type(key) is str for key in value), "SCHEMA")
            for key, item in value.items():
                ExecutorHostProofs._canonical_value(key)
                ExecutorHostProofs._canonical_value(item)
        elif type(value) in (list, tuple):
            for item in value:
                ExecutorHostProofs._canonical_value(item)
        elif type(value) is int:
            ExecutorHostProofs._require(abs(value) <= wire.SAFE_INTEGER_MAX, "SCHEMA")
        else:
            ExecutorHostProofs._require(value is None or type(value) is bool, "SCHEMA")

    @staticmethod
    def _validate_schema(value, schema):
        """Validate the closed, bounded subset used by the approved B3 schemas."""
        ExecutorHostProofs._canonical_value(value)
        if "anyOf" in schema:
            for alternative in schema["anyOf"]:
                try:
                    ExecutorHostProofs._validate_schema(value, alternative)
                    return
                except ExecutorBridgeError:
                    pass
            raise ExecutorBridgeError("HOST_HISTORY_INVALID")
        if "const" in schema:
            ExecutorHostProofs._require(
                type(value) is type(schema["const"]) and value == schema["const"], "SCHEMA"
            )
        if "enum" in schema:
            ExecutorHostProofs._require(value in schema["enum"] and type(value) is str, "SCHEMA")
        kind = schema.get("type")
        expected = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
        if kind == "null":
            ExecutorHostProofs._require(value is None, "SCHEMA")
        elif kind:
            ExecutorHostProofs._require(type(value) is expected[kind], "SCHEMA")
        if kind == "object" or (kind is None and "properties" in schema):
            props = schema["properties"]
            ExecutorHostProofs._require(set(schema.get("required", [])) <= value.keys(), "SCHEMA")
            if schema.get("additionalProperties") is False:
                ExecutorHostProofs._require(value.keys() <= props.keys(), "SCHEMA")
            for key, item in value.items():
                if key in props:
                    ExecutorHostProofs._validate_schema(item, props[key])
        elif kind == "array":
            for item in value:
                ExecutorHostProofs._validate_schema(item, schema["items"])
        elif kind == "string":
            ExecutorHostProofs._require(len(value) >= schema.get("minLength", 0), "SCHEMA")
            if "pattern" in schema:
                if schema["pattern"] == "^https://":
                    ExecutorHostProofs._require(
                        value.startswith("https://")
                        and (not any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in value)),
                        "SCHEMA",
                    )
                else:
                    ExecutorHostProofs._require(
                        re.fullmatch(schema["pattern"], value) is not None, "SCHEMA"
                    )
        elif kind == "integer":
            ExecutorHostProofs._require(
                schema.get("minimum", -wire.SAFE_INTEGER_MAX)
                <= value
                <= schema.get("maximum", wire.SAFE_INTEGER_MAX),
                "SCHEMA",
            )
        for rule in schema.get("allOf", []):
            try:
                ExecutorHostProofs._validate_schema(value, rule["if"])
                branch = "then"
            except ExecutorBridgeError:
                branch = "else"
            ExecutorHostProofs._validate_schema(value, rule[branch])

    @staticmethod
    def _require(condition, code):
        _bridge_require(condition, "HOST_HISTORY_INVALID")

    @staticmethod
    def _digest(value):
        return sha256_bytes(canonical_json_bytes(value))

    @staticmethod
    def _logical_digest(state, revision):
        return ExecutorHostProofs._digest(ExecutorHostProofs._projection(state, revision))

    @staticmethod
    def _validate_row(name, row):
        ExecutorHostProofs._require(
            name in _EXECUTOR_B3_TABLE_CONSTRAINTS
            and type(row) is dict
            and (row.keys() == _EXECUTOR_B3_COLUMNS[name].keys()),
            "SCHEMA",
        )
        for key, field in _EXECUTOR_B3_COLUMNS[name].items():
            ExecutorHostProofs._scalar(row[key], field)
        if name in ("metadata", "bootstrap_manifest"):
            ExecutorHostProofs._require(row["singleton"] == 1, "SCHEMA")
        if name == "artifact_references":
            ExecutorHostProofs._require(
                row["reference_id"]
                == ExecutorHostProofs._digest(
                    {k: row[k] for k in ("path", "sha256", "schema_version", "kind")}
                ),
                "SCHEMA",
            )
        elif name == "history_revisions":
            ExecutorHostProofs._require(
                (row["revision"] == 0) == (row["previous_ledger_digest"] is None), "SCHEMA"
            )
            ExecutorHostProofs._require(
                row["last_event_sequence"] >= row["first_event_sequence"]
                and row["event_count"]
                == row["last_event_sequence"] - row["first_event_sequence"] + 1,
                "SCHEMA",
            )
        elif name == "official_runs":
            version, prev = (row["run_version"], row["previous_run_version"])
            ExecutorHostProofs._require(
                version == 0 and prev is None or (version > 0 and prev == version - 1),
                "ILLEGAL_TRANSITION",
            )
            ExecutorHostProofs._require(
                row["created_at"] <= row["opened_at"]
                and (row["finalized_at"] is None or row["finalized_at"] >= row["opened_at"]),
                "SCHEMA",
            )
            state, result = (row["lifecycle_state"], row["result"])
            terminal = state == "TERMINAL"
            ExecutorHostProofs._require(
                terminal
                == (
                    row["phase"] == "FINALIZED"
                    and result is not None
                    and (row["finalized_at"] is not None)
                ),
                "ILLEGAL_TRANSITION",
            )
            if terminal:
                nd = result == "NOT_DEMONSTRATED"
                ExecutorHostProofs._require(
                    row["binding"] is (not nd)
                    and row["ack_required"] is nd
                    and (
                        row["validity_classification"]
                        == ("NON_BINDING_ND" if nd else "VALID_BINDING")
                    ),
                    "CONTRADICTORY_RUN",
                )
            else:
                ExecutorHostProofs._require(
                    result is None
                    and row["finalized_at"] is None
                    and (not row["binding"])
                    and (not row["ack_required"]),
                    "ILLEGAL_TRANSITION",
                )
                expected = {
                    "OPEN": ("OPENED",),
                    "ACTIVE": ("EXECUTING", "DRAINED", "RECONCILED"),
                    "UNCERTAIN": ("UNCERTAIN",),
                }
                ExecutorHostProofs._require(
                    row["phase"] in expected[state]
                    and row["validity_classification"]
                    == ("UNCERTAIN" if state == "UNCERTAIN" else "PENDING"),
                    "ILLEGAL_TRANSITION",
                )
            if row["provenance_model"] == "PROFILE_V2":
                ExecutorHostProofs._require(
                    row["profile_reference_id"] is not None
                    and row["session_reference_id"] is not None,
                    "SCHEMA",
                )
            else:
                ExecutorHostProofs._require(row["profile_reference_id"] is None, "SCHEMA")
        elif name == "authority_events":
            has_run = row["run_id"] is not None
            ExecutorHostProofs._require(has_run == (row["run_version"] is not None), "SCHEMA")
            if row["event_kind"].startswith("RUN_") or row["event_kind"] == "IMPORT_RUN":
                ExecutorHostProofs._require(has_run, "SCHEMA")
            elif row["event_kind"] != "MANUAL_RESOLUTION":
                ExecutorHostProofs._require(not has_run, "SCHEMA")
            ExecutorHostProofs._require(
                (row["event_kind"] == "MANUAL_RESOLUTION")
                == (row["resolves_event_sequence"] is not None),
                "SCHEMA",
            )
        elif name == "witness_checkpoints":
            is_genesis = row["revision"] == 0
            ExecutorHostProofs._require(
                is_genesis == (row["previous_witness_digest"] is None), "SCHEMA"
            )
            ExecutorHostProofs._require(
                all((row[k] is not None) == is_genesis for k in row if k.startswith("genesis_")),
                "SCHEMA",
            )
        elif name == "bootstrap_sources":
            ExecutorHostProofs._require(
                row["status"] in ("SCANNED", "ABSENT_CERTIFIED"), "INCOMPLETE_BOOTSTRAP"
            )
            ExecutorHostProofs._require(
                (row["status"] == "SCANNED") == (row["snapshot_reference_id"] is not None), "SCHEMA"
            )
            if row["status"] == "ABSENT_CERTIFIED":
                ExecutorHostProofs._require(row["discovered_run_count"] == 0, "SCHEMA")
        elif name == "bootstrap_findings":
            ExecutorHostProofs._require(row["run_version"] == 0, "SCHEMA")
        elif name == "publication_intents":
            zero = row["candidate_revision"] == 0
            ExecutorHostProofs._require(
                zero == (row["base_revision"] is None)
                and zero == (row["base_ledger_digest"] is None),
                "SCHEMA",
            )
            ExecutorHostProofs._require(
                zero or row["candidate_revision"] == row["base_revision"] + 1, "SCHEMA"
            )
        elif name == "publication_resolutions":
            committed = row["disposition"] == "COMMITTED"
            ExecutorHostProofs._require(
                committed == (row["committed_revision"] is not None)
                and committed == (row["receipt_digest"] is not None),
                "SCHEMA",
            )
            approval = [
                row[k]
                for k in (
                    "operator_approval_path",
                    "operator_approval_sha256",
                    "operator_approval_schema",
                )
            ]
            ExecutorHostProofs._require(
                all(x is None for x in approval) or all(x is not None for x in approval), "SCHEMA"
            )
            ExecutorHostProofs._require(committed or all(x is not None for x in approval), "SCHEMA")

    @staticmethod
    def _validate_state(state, *, check_digests=True, check_publication=True):
        ExecutorHostProofs._require(
            type(state) is dict and state.keys() == _EXECUTOR_B3_TABLE_CONSTRAINTS.keys(), "SCHEMA"
        )
        indexes = {}
        for name, rows in state.items():
            ExecutorHostProofs._require(type(rows) is list, "SCHEMA")
            table = _EXECUTOR_B3_TABLE_CONSTRAINTS[name]
            for row in rows:
                ExecutorHostProofs._validate_row(name, row)
            for keys in [table["primary_key"], *table["unique"]]:
                values = [
                    tuple(row[k] for k in keys)
                    for row in rows
                    if all(row[k] is not None for k in keys)
                ]
                ExecutorHostProofs._require(
                    len(values) == len(set(values)),
                    "CONTRADICTORY_RUN" if name == "official_runs" else "HISTORY_INVALID",
                )
            indexes[name] = {tuple(row[k] for k in table["primary_key"]): row for row in rows}
        for name, rows in state.items():
            for foreign in _EXECUTOR_B3_TABLE_CONSTRAINTS[name]["foreign_keys"]:
                targets = {
                    tuple(row[k] for k in foreign["references_columns"])
                    for row in state[foreign["references_table"]]
                }
                for row in rows:
                    value = tuple(row[k] for k in foreign["columns"])
                    ExecutorHostProofs._require(
                        any(x is None for x in value) or value in targets, "HISTORY_INVALID"
                    )
        ExecutorHostProofs._require(
            len(state["metadata"]) == len(state["bootstrap_manifest"]) == 1, "INCOMPLETE_BOOTSTRAP"
        )
        metadata, bootstrap = (state["metadata"][0], state["bootstrap_manifest"][0])
        revisions = sorted(state["history_revisions"], key=lambda row: row["revision"])
        ExecutorHostProofs._require(
            revisions and [r["revision"] for r in revisions] == list(range(len(revisions))),
            "REVISION_GAP",
        )
        head = len(revisions) - 1
        ExecutorHostProofs._require(
            all(
                r["introduced_revision"] <= head
                for t in ("official_runs", "artifact_references")
                for r in state[t]
            ),
            "REVISION_GAP",
        )
        events = sorted(state["authority_events"], key=lambda row: row["event_sequence"])
        ExecutorHostProofs._require(
            [e["event_sequence"] for e in events] == list(range(1, len(events) + 1)), "REVISION_GAP"
        )
        ExecutorHostProofs._require(
            sum(e["event_kind"] == "GENESIS" for e in events) == 1, "INCOMPLETE_BOOTSTRAP"
        )
        next_event = 1
        for revision in revisions:
            selected = [e for e in events if e["revision"] == revision["revision"]]
            ExecutorHostProofs._require(
                selected
                and revision["first_event_sequence"] == next_event
                and (len(selected) == revision["event_count"])
                and (selected[-1]["event_sequence"] == revision["last_event_sequence"]),
                "REVISION_GAP",
            )
            next_event = revision["last_event_sequence"] + 1
            ExecutorHostProofs._require(
                all(e["revision"] == 0 for e in selected if e["event_kind"] == "GENESIS"), "SCHEMA"
            )
        ExecutorHostProofs._require(all(e["revision"] <= head for e in events), "REVISION_GAP")
        resolved_targets = [
            e["resolves_event_sequence"] for e in events if e["resolves_event_sequence"] is not None
        ]
        ExecutorHostProofs._require(
            len(resolved_targets) == len(set(resolved_targets)), "ILLEGAL_TRANSITION"
        )
        for event in events:
            if event["run_id"] is not None:
                run = indexes["official_runs"][event["run_id"], event["run_version"]]
                ExecutorHostProofs._require(
                    run["introduced_revision"] == event["revision"], "CONTRADICTORY_RUN"
                )
            if event["resolves_event_sequence"] is not None:
                previous = indexes["authority_events"][event["resolves_event_sequence"],]
                ExecutorHostProofs._require(
                    previous["event_sequence"] < event["event_sequence"]
                    and (
                        previous["event_kind"] in ("RUN_UNCERTAIN", "AUTHORITY_UNCERTAIN")
                        or (
                            previous["event_kind"] == "IMPORT_RUN"
                            and previous["run_id"] is not None
                            and (
                                indexes["official_runs"][
                                    previous["run_id"], previous["run_version"]
                                ]["lifecycle_state"]
                                == "UNCERTAIN"
                            )
                        )
                    ),
                    "ILLEGAL_TRANSITION",
                )
                ExecutorHostProofs._require(
                    previous["run_id"] == event["run_id"], "ILLEGAL_TRANSITION"
                )
                ExecutorHostProofs._require(
                    indexes["artifact_references"][event["evidence_reference_id"],]["kind"]
                    == "APPROVAL",
                    "TRUST_UNAPPROVED",
                )
        latest = {}
        immutable = (
            "source_revision",
            "protocol",
            "methodology",
            "mode",
            "provenance_model",
            "created_at",
            "opened_at",
            "profile_reference_id",
            "session_reference_id",
        )
        for run in sorted(state["official_runs"], key=lambda r: (r["run_id"], r["run_version"])):
            previous = latest.get(run["run_id"])
            ExecutorHostProofs._require(
                run["run_version"] == (0 if previous is None else previous["run_version"] + 1),
                "ILLEGAL_TRANSITION",
            )
            if previous:
                ExecutorHostProofs._require(
                    all(previous[k] == run[k] for k in immutable), "CONTRADICTORY_RUN"
                )
                ExecutorHostProofs._require(
                    run["introduced_revision"] > previous["introduced_revision"]
                    and run["lifecycle_state"]
                    in _EXECUTOR_B3_TRANSITIONS[previous["lifecycle_state"]],
                    "ILLEGAL_TRANSITION",
                )
                if (
                    previous["lifecycle_state"] == "UNCERTAIN"
                    and run["lifecycle_state"] != "UNCERTAIN"
                ):
                    ExecutorHostProofs._require(
                        any(
                            e["event_kind"] == "MANUAL_RESOLUTION"
                            and e["run_id"] == run["run_id"]
                            and (e["revision"] == run["introduced_revision"])
                            and (
                                indexes["authority_events"][e["resolves_event_sequence"],][
                                    "run_version"
                                ]
                                == previous["run_version"]
                            )
                            for e in events
                        ),
                        "ILLEGAL_TRANSITION",
                    )
            latest[run["run_id"]] = run
        ExecutorHostProofs._require(
            sum(r["binding"] for r in latest.values()) <= 1, "CONTRADICTORY_RUN"
        )
        ExecutorHostProofs._require(
            metadata["bootstrap_reference_id"] == bootstrap["manifest_reference_id"]
            and metadata["source_baseline_head"] == bootstrap["baseline_head"]
            and (metadata["source_baseline_tree"] == bootstrap["baseline_tree"]),
            "INCOMPLETE_BOOTSTRAP",
        )
        ExecutorHostProofs._require(
            bootstrap["source_count"] == len(state["bootstrap_sources"]), "INCOMPLETE_BOOTSTRAP"
        )
        imported = [r for r in state["official_runs"] if r["introduced_revision"] == 0]
        ExecutorHostProofs._require(bootstrap["run_count"] == len(imported), "INCOMPLETE_BOOTSTRAP")
        for run in imported:
            findings = [f for f in state["bootstrap_findings"] if f["run_id"] == run["run_id"]]
            ExecutorHostProofs._require(
                findings and any(f["is_primary_evidence"] for f in findings), "INCOMPLETE_BOOTSTRAP"
            )
        for source in state["bootstrap_sources"]:
            ExecutorHostProofs._require(
                source["scope_reference_id"] == bootstrap["scope_reference_id"],
                "INCOMPLETE_BOOTSTRAP",
            )
            ExecutorHostProofs._require(
                source["discovered_run_count"]
                == sum(f["source_id"] == source["source_id"] for f in state["bootstrap_findings"]),
                "INCOMPLETE_BOOTSTRAP",
            )
        checkpoints = sorted(state["witness_checkpoints"], key=lambda r: r["revision"])
        ExecutorHostProofs._require(
            [r["revision"] for r in checkpoints] == list(range(head + 1)), "HISTORY_INVALID"
        )
        for cp, revision in zip(checkpoints, revisions, strict=True):
            ExecutorHostProofs._require(
                cp["authority_id"] == metadata["authority_id"]
                and cp["authority_epoch"] == metadata["epoch"]
                and (cp["operation_id"] == revision["operation_id"])
                and (cp["ledger_digest"] == revision["ledger_digest"]),
                "FORK_DETECTED",
            )
        for resolution in state["publication_resolutions"]:
            intent = indexes["publication_intents"][resolution["operation_id"],]
            if resolution["disposition"] == "COMMITTED":
                ExecutorHostProofs._require(
                    resolution["committed_revision"] == intent["candidate_revision"],
                    "FORK_DETECTED",
                )
                cp = checkpoints[resolution["committed_revision"]]
                ExecutorHostProofs._require(
                    cp["operation_id"] == intent["operation_id"]
                    and cp["ledger_digest"] == intent["candidate_ledger_digest"]
                    and (cp["witness_digest"] == resolution["receipt_digest"]),
                    "FORK_DETECTED",
                )
        for intent in state["publication_intents"]:
            ExecutorHostProofs._require(
                intent["candidate_payload_sha256"] == intent["candidate_ledger_digest"]
                and intent["candidate_revision"] <= head + 1,
                "DIGEST_MISMATCH",
            )
            if intent["base_revision"] is not None:
                ExecutorHostProofs._require(
                    intent["base_revision"] <= head
                    and revisions[intent["base_revision"]]["ledger_digest"]
                    == intent["base_ledger_digest"],
                    "FORK_DETECTED",
                )
        if check_publication:
            committed = [
                r["committed_revision"]
                for r in state["publication_resolutions"]
                if r["disposition"] == "COMMITTED"
            ]
            ExecutorHostProofs._require(
                sorted(committed) == list(range(head + 1)), "UNCERTAIN_PUBLICATION"
            )
            cancelled = [
                r
                for r in state["publication_resolutions"]
                if r["disposition"] == "MANUAL_CANCELLED"
            ]
            ExecutorHostProofs._require(
                not cancelled
                or all(
                    indexes["publication_intents"][r["operation_id"],]["candidate_revision"]
                    == head + 1
                    for r in cancelled
                ),
                "ILLEGAL_TRANSITION",
            )
        if check_digests:
            previous = None
            for revision in revisions:
                ExecutorHostProofs._require(
                    revision["previous_ledger_digest"] == previous, "FORK_DETECTED"
                )
                previous = ExecutorHostProofs._logical_digest(state, revision["revision"])
                ExecutorHostProofs._require(
                    previous == revision["ledger_digest"], "DIGEST_MISMATCH"
                )
        return latest

    @staticmethod
    def _reference_row(state, ref_id):
        rows = [row for row in state["artifact_references"] if row["reference_id"] == ref_id]
        ExecutorHostProofs._require(len(rows) == 1, "HISTORY_INVALID")
        return {k: rows[0][k] for k in ("path", "sha256", "schema_version")}

    @staticmethod
    def _reconcile_bootstrap(state, core, sealed, descriptor):
        """Bind the sealed approved core to exact immutable genesis facts."""
        metadata = state["metadata"][0]
        bootstrap = state["bootstrap_manifest"][0]
        ExecutorHostProofs._require(
            metadata["authority_id"] == core["authority_id"] == descriptor["authority_id"]
            and metadata["epoch"] == core["authority_epoch"] == descriptor["authority_epoch"],
            "REFERENCE_MISMATCH",
        )
        ExecutorHostProofs._require(
            metadata["namespace"] == core["namespace"] == descriptor["namespace"]
            and metadata["schema_version"]
            == descriptor["authority_schema_version"]
            == "s11-ex10-historical-authority-ledger-v1"
            and (metadata["descriptor_sha256"] == ExecutorHostProofs._digest(descriptor)),
            "REFERENCE_MISMATCH",
        )
        for name in ("head", "tree"):
            ExecutorHostProofs._require(
                metadata["source_baseline_" + name]
                == core["baseline_" + name]
                == bootstrap["baseline_" + name]
                == descriptor["source_baseline_" + name],
                "REFERENCE_MISMATCH",
            )
        ExecutorHostProofs._require(
            ExecutorHostProofs._reference_row(state, metadata["g0_a3_approval_reference_id"])
            == descriptor["g0_a3_approval_reference"],
            "REFERENCE_MISMATCH",
        )
        ExecutorHostProofs._require(
            bootstrap["core_digest"] == ExecutorHostProofs._digest(core)
            and bootstrap["source_count"] == core["source_count"]
            and (bootstrap["run_count"] == core["run_count"])
            and (bootstrap["certified_at"] == sealed["approved_at"]),
            "INCOMPLETE_BOOTSTRAP",
        )
        for field, reference in (
            ("scope_reference_id", core["scope_reference"]),
            ("coverage_certificate_reference_id", core["coverage_certificate_reference"]),
            ("operator_approval_reference_id", sealed["operator_approval_reference"]),
        ):
            ExecutorHostProofs._require(
                ExecutorHostProofs._reference_row(state, bootstrap[field]) == reference,
                "INCOMPLETE_BOOTSTRAP",
            )
        ExecutorHostProofs._require(
            ExecutorHostProofs._reference_row(state, bootstrap["manifest_reference_id"])
            == descriptor["bootstrap_manifest_reference"]
            and ExecutorHostProofs._digest(sealed)
            == descriptor["bootstrap_manifest_reference"]["sha256"],
            "INCOMPLETE_BOOTSTRAP",
        )
        sources = {s["source_id"]: s for s in state["bootstrap_sources"]}
        ExecutorHostProofs._require(
            set(sources) == {s["source_id"] for s in core["sources"]}, "INCOMPLETE_BOOTSTRAP"
        )
        for observed in core["sources"]:
            row = sources[observed["source_id"]]
            ExecutorHostProofs._require(
                row["location"] == observed["location"]
                and row["source_kind"] == observed["source_kind"]
                and (row["status"] == observed["status"])
                and (row["has_active_source"] == observed["active_source"])
                and (row["observed_at"] == core["observed_at"]),
                "INCOMPLETE_BOOTSTRAP",
            )
            ExecutorHostProofs._require(
                ExecutorHostProofs._reference_row(state, row["observation_reference_id"])
                == observed["observation_reference"],
                "INCOMPLETE_BOOTSTRAP",
            )
            if observed["snapshot_reference"] is None:
                ExecutorHostProofs._require(
                    row["snapshot_reference_id"] is None, "INCOMPLETE_BOOTSTRAP"
                )
            else:
                ExecutorHostProofs._require(
                    ExecutorHostProofs._reference_row(state, row["snapshot_reference_id"])
                    == observed["snapshot_reference"],
                    "INCOMPLETE_BOOTSTRAP",
                )
            actual_ids = sorted(
                f["run_id"]
                for f in state["bootstrap_findings"]
                if f["source_id"] == row["source_id"]
            )
            ExecutorHostProofs._require(
                actual_ids == observed["run_ids"]
                and row["discovered_run_count"] == len(actual_ids),
                "INCOMPLETE_BOOTSTRAP",
            )
        imported = {r["run_id"]: r for r in state["official_runs"] if r["introduced_revision"] == 0}
        ExecutorHostProofs._require(
            set(imported) == {r["run_id"] for r in core["run_findings"]}, "INCOMPLETE_BOOTSTRAP"
        )
        for finding in core["run_findings"]:
            proof = finding["normalized_facts_reference"]
            ExecutorHostProofs._require(
                ExecutorHostProofs._digest(imported[finding["run_id"]]) == proof["sha256"],
                "CONTRADICTORY_RUN",
            )
            matches = [f for f in state["bootstrap_findings"] if f["run_id"] == finding["run_id"]]
            ExecutorHostProofs._require(
                sorted(f["source_id"] for f in matches) == finding["source_ids"],
                "INCOMPLETE_BOOTSTRAP",
            )
            ExecutorHostProofs._require(
                matches and any(f["is_primary_evidence"] for f in matches), "INCOMPLETE_BOOTSTRAP"
            )
            for row in matches:
                ExecutorHostProofs._require(
                    ExecutorHostProofs._reference_row(state, row["finding_reference_id"]) == proof,
                    "INCOMPLETE_BOOTSTRAP",
                )

    @staticmethod
    def _scalar(value, field):
        if value is None:
            _bridge_require(not field["not_null"], "HOST_HISTORY_INVALID")
            return
        kind = field["type"]
        if kind in ("int64_nonnegative", "positive_int64"):
            _bridge_require(
                type(value) is int
                and (1 if kind == "positive_int64" else 0) <= value <= wire.SAFE_INTEGER_MAX,
                "HOST_HISTORY_INVALID",
            )
        elif kind == "bool01":
            _bridge_require(type(value) is bool, "HOST_HISTORY_INVALID")
        else:
            _bridge_require(type(value) is str and "\x00" not in value, "HOST_HISTORY_INVALID")
            patterns = {
                "sha256": r"[0-9a-f]{64}",
                "git_sha1": r"[0-9a-f]{40}",
                "uuid": r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                "run_id": r"s11-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                "identifier": r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}",
                "signature_ed25519": r"[0-9a-f]{128}",
            }
            if kind in patterns:
                _bridge_require(re.fullmatch(patterns[kind], value), "HOST_HISTORY_INVALID")
            elif kind == "utc":
                wire._time(value)
            elif kind == "enum":
                _bridge_require(value in field["values"], "HOST_HISTORY_INVALID")
            elif kind == "locator":
                # Locators are data only. No historical path or URI is resolved.
                if value.startswith("/"):
                    profile_codec._absolute(value)
                else:
                    _bridge_require(
                        value.startswith("https://")
                        and len(value) <= 4096
                        and not any(c in value for c in "@?#\r\n"),
                        "HOST_HISTORY_INVALID",
                    )
            else:
                raise ExecutorBridgeError("HOST_HISTORY_INVALID")

    @staticmethod
    def _projection(state, revision):
        result = {"schema_version": "s11-ex10-authority-logical-state-v1", "revision": revision}
        for name in _EXECUTOR_B3_LOGICAL_FIELDS:
            if name in ("schema_version", "revision"):
                continue
            if name in ("metadata", "bootstrap_manifest"):
                _bridge_require(len(state[name]) == 1, "HOST_HISTORY_INVALID")
                result[name] = state[name][0]
                continue
            rows = state[name]
            if name in ("official_runs", "artifact_references"):
                rows = [r for r in rows if r["introduced_revision"] <= revision]
            elif name in ("authority_events", "history_revisions"):
                rows = [r for r in rows if r["revision"] <= revision]
            rows = sorted(rows, key=lambda row: tuple(row[k] for k in _EXECUTOR_B3_ORDERS[name]))
            result[name] = [
                {
                    k: v
                    for k, v in row.items()
                    if not (name == "history_revisions" and k == "ledger_digest")
                }
                for row in rows
            ]
        return result

    @staticmethod
    def _receipt(cp):
        genesis = None
        if cp["revision"] == 0:
            genesis = {
                "bootstrap_manifest_sha256": cp["genesis_bootstrap_sha256"],
                "g0_a3_approval_reference": {
                    "path": cp["genesis_g0_a3_approval_path"],
                    "sha256": cp["genesis_g0_a3_approval_sha256"],
                    "schema_version": cp["genesis_g0_a3_approval_schema"],
                },
                "source_baseline_head": cp["genesis_baseline_head"],
                "source_baseline_tree": cp["genesis_baseline_tree"],
            }
        return {
            "witness_schema_version": cp["receipt_schema_version"],
            "namespace": "S11C3-EX10",
            "genesis": genesis,
            **{
                k: cp[k]
                for k in (
                    "authority_id",
                    "authority_epoch",
                    "operation_id",
                    "revision",
                    "ledger_digest",
                    "previous_witness_digest",
                    "created_at",
                    "key_id",
                    "key_epoch",
                    "key_fingerprint",
                    "signature",
                )
            },
        }

    def _signed_b3(self, value, context):
        cfg = context._cfg()["expected_challenge"]
        kind = value.get("witness_schema_version")
        schemas = {
            "s11-ex10-authority-witness-receipt-v1": "receipt",
            "s11-ex10-authority-witness-head-v1": "current_head",
        }
        _bridge_require(kind in schemas, "HOST_HISTORY_INVALID")
        self._validate_schema(value, _EXECUTOR_B3_WITNESS_SCHEMAS[schemas[kind]])
        pin = [
            p
            for p in json.loads(self.pins)
            if (p["key_id"], p["key_epoch"]) == (value["key_id"], value["key_epoch"])
        ]
        _bridge_require(len(pin) == 1, "INDEPENDENT_PIN_REQUIRED")
        pin = pin[0]
        for key in ("authority_id", "authority_epoch"):
            _bridge_require(value[key] == pin[key] == cfg[key], "HOST_HISTORY_INVALID")
        _bridge_require(
            value["namespace"] == pin["namespace"] == "S11C3-EX10"
            and value["key_fingerprint"] == pin["fingerprint"]
            and pin["activation_revision"] <= value["revision"]
            and (
                pin["retirement_revision"] is None
                or value["revision"] <= pin["retirement_revision"]
            ),
            "HOST_HISTORY_INVALID",
        )
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        domain = (
            b"S11-EX10-B3-WITNESS-RECEIPT-V1\n"
            if schemas[kind] == "receipt"
            else b"S11-EX10-B3-WITNESS-HEAD-V1\n"
        )
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(pin["public_key_hex"])).verify(
                bytes.fromhex(value["signature"]),
                domain + canonical_json_bytes({k: v for k, v in value.items() if k != "signature"}),
            )
        except Exception:
            raise ExecutorBridgeError("HOST_HISTORY_INVALID") from None
        return sha256_bytes(canonical_json_bytes(value))

    def history(self, prefix, context, *, minimum_issued_at=None):
        try:
            return self._history(prefix, context, minimum_issued_at=minimum_issued_at)
        except ExecutorBridgeError:
            raise
        except Exception:
            raise ExecutorBridgeError("HOST_HISTORY_INVALID") from None

    def _history(self, prefix, context, *, minimum_issued_at=None):
        material, _ = self.read(prefix + "-material")
        _bridge_require(
            set(material)
            == {
                "descriptor",
                "descriptor_reference",
                "state",
                "bootstrap_core",
                "bootstrap_manifest",
            },
            "HOST_HISTORY_INVALID",
        )
        state = material["state"]
        _bridge_require(
            type(state) is dict and set(state) == set(_EXECUTOR_B3_COLUMNS), "HOST_HISTORY_INVALID"
        )
        for name, fields in _EXECUTOR_B3_COLUMNS.items():
            _bridge_require(type(state[name]) is list, "HOST_HISTORY_INVALID")
            for row in state[name]:
                _bridge_require(
                    type(row) is dict and set(row) == set(fields), "HOST_HISTORY_INVALID"
                )
                for key, field in fields.items():
                    self._scalar(row[key], field)
        cfg = context._cfg()["expected_challenge"]
        _bridge_require(
            len(state["metadata"]) == 1
            and state["metadata"][0]["authority_id"] == cfg["authority_id"]
            and state["metadata"][0]["epoch"] == cfg["authority_epoch"],
            "HOST_HISTORY_INVALID",
        )
        revisions = sorted(state["history_revisions"], key=lambda row: row["revision"])
        receipts = sorted(state["witness_checkpoints"], key=lambda row: row["revision"])
        _bridge_require(revisions and len(receipts) == len(revisions), "HOST_HISTORY_INVALID")
        self._validate_state(state)
        descriptor = material["descriptor"]
        self._validate_schema(descriptor, _EXECUTOR_B3_PUBLIC_SCHEMAS["authority_descriptor"])
        reference = material["descriptor_reference"]
        self._validate_schema(reference, _EXECUTOR_B3_PUBLIC_SCHEMAS["external_reference"])
        _bridge_require(
            reference["sha256"]
            == self._digest(descriptor)
            == state["metadata"][0]["descriptor_sha256"]
            and reference["schema_version"] == descriptor["schema_version"],
            "HOST_HISTORY_INVALID",
        )
        pins = json.loads(self.pins)
        root_pin = min(pins, key=lambda p: p["key_epoch"])
        _bridge_require(
            root_pin["activation_revision"] == 0
            and root_pin["key_epoch"] == 1
            and self._digest(root_pin) == descriptor["trust_root_reference"]["sha256"]
            and root_pin["schema_version"] == descriptor["trust_root_reference"]["schema_version"]
            and all(
                (p["authority_id"], p["authority_epoch"], p["namespace"])
                == (
                    descriptor["authority_id"],
                    descriptor["authority_epoch"],
                    descriptor["namespace"],
                )
                for p in pins
            ),
            "HOST_HISTORY_INVALID",
        )
        self._reconcile_bootstrap(
            state, material["bootstrap_core"], material["bootstrap_manifest"], descriptor
        )
        _bridge_require(
            canonical_json_bytes(
                {
                    "bootstrap_manifest_sha256": descriptor["bootstrap_manifest_reference"][
                        "sha256"
                    ],
                    "g0_a3_approval_reference": descriptor["g0_a3_approval_reference"],
                    "source_baseline_head": descriptor["source_baseline_head"],
                    "source_baseline_tree": descriptor["source_baseline_tree"],
                }
            )
            == self.genesis_binding,
            "HOST_HISTORY_INVALID",
        )
        previous_ledger = previous_witness = previous_epoch = None
        operations = set()
        for revision, (row, cp) in enumerate(zip(revisions, receipts, strict=True)):
            ledger = sha256_bytes(canonical_json_bytes(self._projection(state, revision)))
            receipt = self._receipt(cp)
            digest = self._signed_b3(receipt, context)
            _bridge_require(receipt["operation_id"] not in operations, "HOST_HISTORY_INVALID")
            operations.add(receipt["operation_id"])
            pin = next(
                p
                for p in json.loads(self.pins)
                if (p["key_id"], p["key_epoch"]) == (receipt["key_id"], receipt["key_epoch"])
            )
            if previous_epoch is not None and pin["key_epoch"] != previous_epoch:
                _bridge_require(
                    pin["key_epoch"] == previous_epoch + 1
                    and pin["activation_revision"] == revision
                    and pin["key_transition_reference"] is not None,
                    "HOST_HISTORY_INVALID",
                )
            previous_epoch = pin["key_epoch"]
            _bridge_require(
                row["revision"] == cp["revision"] == revision
                and row["ledger_digest"] == cp["ledger_digest"] == ledger
                and row["previous_ledger_digest"] == previous_ledger
                and cp["previous_witness_digest"] == previous_witness
                and cp["witness_digest"] == digest,
                "HOST_HISTORY_INVALID",
            )
            if revision == 0:
                _bridge_require(
                    digest == self.genesis_digest
                    and canonical_json_bytes(receipt["genesis"]) == self.genesis_binding,
                    "HOST_HISTORY_INVALID",
                )
            previous_ledger, previous_witness = ledger, digest
        head, _ = self.read(prefix + "-head")
        self._signed_b3(head, context)
        now = context._now()
        issued, expires = wire._time(head["issued_at"]), wire._time(head["expires_at"])
        _bridge_require(
            head["revision"] == len(revisions) - 1
            and head["revision"]
            >= max(
                descriptor["minimum_revision"],
                context._cfg()["authority_revision"],
                self.observed_revision if self.observed_revision is not None else 0,
            )
            and (head["key_id"], head["key_epoch"])
            == (receipts[-1]["key_id"], receipts[-1]["key_epoch"])
            and head["ledger_digest"] == previous_ledger
            and head["witness_digest"] == previous_witness
            and context.host_challenge is not None
            and head["challenge"] == context.host_challenge.to_dict()["challenge"]
            and issued <= now < expires <= issued + timedelta(seconds=30)
            and (minimum_issued_at is None or issued >= minimum_issued_at),
            "HOST_HISTORY_STALE",
        )
        self.observed_revision = head["revision"]
        return state, head

    @staticmethod
    def _write_allowed(state):
        resolutions = {r["operation_id"]: r for r in state["publication_resolutions"]}
        resolved = {
            e["resolves_event_sequence"]
            for e in state["authority_events"]
            if e["event_kind"] == "MANUAL_RESOLUTION"
        }
        _bridge_require(
            not any(i["operation_id"] not in resolutions for i in state["publication_intents"])
            and not any(r["disposition"] == "MANUAL_CANCELLED" for r in resolutions.values())
            and not any(
                e["event_kind"] == "AUTHORITY_UNCERTAIN" and e["event_sequence"] not in resolved
                for e in state["authority_events"]
            ),
            "AUTHORITY_UNCERTAIN",
        )

    def common_prior(self, *, context):
        state, head = self.history("base", context)
        self._write_allowed(state)
        base = context._cfg()["payload"]["expected_head"]
        _bridge_require(
            all(
                head[k] == base[k]
                for k in (
                    "authority_id",
                    "authority_epoch",
                    "revision",
                    "ledger_digest",
                    "witness_digest",
                )
            ),
            "HOST_HISTORY_INVALID",
        )
        latest = {}
        for row in sorted(state["official_runs"], key=lambda r: (r["run_id"], r["run_version"])):
            old = latest.get(row["run_id"])
            _bridge_require(
                row["run_version"] == (0 if old is None else old["run_version"] + 1)
                and row["previous_run_version"] == (None if old is None else old["run_version"]),
                "HOST_HISTORY_INVALID",
            )
            latest[row["run_id"]] = row
        return tuple(
            PriorRun(
                run_id,
                OFFICIAL_MODE,
                row["lifecycle_state"] == "TERMINAL",
                row["result"],
                ("authority-uncertain",) if row["lifecycle_state"] == "UNCERTAIN" else (),
            )
            for run_id, row in sorted(latest.items())
        )

    def _response_head(self, response, context):
        _bridge_require(type(response) is wire.Response, "HOST_HISTORY_INVALID")
        value = response.to_dict()
        head = value["witness_head"]
        cfg = context._cfg()["expected_challenge"]
        _bridge_require(
            type(head) is dict
            and head["authority_id"] == cfg["authority_id"]
            and head["authority_epoch"] == cfg["authority_epoch"]
            and head["revision"] >= context._cfg()["authority_revision"],
            "HOST_HISTORY_INVALID",
        )
        self.observed_revision = max(
            head["revision"], self.observed_revision if self.observed_revision is not None else 0
        )
        return head

    def _head_checkpoint(self, head, state):
        checkpoints = [
            cp for cp in state["witness_checkpoints"] if cp["revision"] == head["revision"]
        ]
        _bridge_require(
            len(checkpoints) == 1
            and all(
                checkpoints[0][key] == head[key]
                for key in (
                    "authority_id",
                    "authority_epoch",
                    "revision",
                    "ledger_digest",
                    "witness_digest",
                )
            ),
            "HOST_HISTORY_INVALID",
        )

    def verify_common(self, *, context, common=None, reserve_response=None, **unused):
        response_head = (
            None if reserve_response is None else self._response_head(reserve_response, context)
        )
        if context.action != "start":
            _bridge_require(common is None and response_head is not None, "COMMON_HISTORY_INVALID")
            state, head = self.history("action", context)
            self._head_checkpoint(response_head, state)
            self._write_allowed(state)
            _bridge_require(
                head["revision"] >= context.reservation["expected_next_revision"]
                and any(r["run_id"] == context.proposal["run_id"] for r in state["official_runs"]),
                "AUTHORITY_REQUIRED",
            )
            receipt = self._committed_receipt(
                "action", context, state=state, record=context.reservation
            )
            coordination = self.coordination(
                "action", context, record=context.reservation, minimum_sequence=receipt["sequence"]
            )
            self._heads_current(head, coordination, context)
            return
        actual = self.common_prior(context=context)
        if response_head is not None:
            state, _ = self.history("base", context)
            self._head_checkpoint(response_head, state)
        _bridge_require(common is None or common == actual, "COMMON_HISTORY_INVALID")

    def verify_commit(self, *, context, authenticated_request, response):
        result = response.to_dict()["result"]
        record = result["reservation"]
        state, head = self.history("commit", context)
        _bridge_require(
            head["revision"] == record["expected_next_revision"]
            and head["witness_digest"] == result["open_witness"]["witness_digest"]
            and head["ledger_digest"] == result["open_witness"]["ledger_digest"]
            and any(
                row["run_id"] == record["run_id"]
                and row["run_version"] == 0
                and row["lifecycle_state"] == "OPEN"
                for row in state["official_runs"]
            ),
            "OPEN_COMMIT_UNQUALIFIED",
        )
        self._committed_receipt("commit", context, state=state, record=record)
        receipt, raw = self.read("commit-receipt")
        _bridge_require(
            sha256_bytes(raw) == record["reservation_receipt_reference"]["sha256"]
            and receipt["state"] == "COMMITTED"
            and receipt["transition"] == "COMMIT_OPEN"
            and receipt["reservation_id"] == record["reservation_id"]
            and receipt["reservation_digest"] == record["reservation_digest"],
            "OPEN_COMMIT_UNQUALIFIED",
        )
        _bridge_require(
            context.reserve_receipt is not None
            and sha256_bytes(context.reserve_receipt)
            == context.reservation["reservation_receipt_reference"]["sha256"]
            == context.permit.to_dict()["reservation_receipt_reference"]["sha256"],
            "OPEN_COMMIT_UNQUALIFIED",
        )
        previous = json.loads(context.reserve_receipt)
        expected = {
            "schema_version": "s11-ex10-b4-reservation-receipt-v1",
            "namespace": "S11C3-EX10",
            "authority_id": record["authority_id"],
            "authority_epoch": record["authority_epoch"],
            "reservation_id": record["reservation_id"],
            "reservation_digest": record["reservation_digest"],
            "state": "COMMITTED",
            "transition": "COMMIT_OPEN",
            "fencing_token": record["fencing_token"],
            "sequence": previous["sequence"] + 1,
            "previous_receipt_digest": sha256_bytes(context.reserve_receipt),
            "base_authority_revision": record["previous_authority_revision"],
            "base_authority_witness_digest": record["previous_witness_digest"],
            "open_witness_digest": result["open_witness"]["witness_digest"],
            "terminal_witness_digest": None,
            "operation_id": authenticated_request.request.to_dict()["request_id"],
            "key_id": context.witness.key_id,
            "key_epoch": context.witness.key_epoch,
        }
        wire.verify_reservation_receipt(
            raw,
            expected_context=wire.ReceiptContext(canonical_json_bytes(expected), context._now()),
            witness_pin=context.witness,
        )
        self._write_allowed(state)
        coordination = self.coordination(
            "commit", context, record=record, minimum_sequence=receipt["sequence"]
        )
        self._heads_current(head, coordination, context)

    def _committed_receipt(self, prefix, context, *, state, record):
        # Non-START's independently selected record is also matched exactly to
        # the authenticated inspect result before scientific authorization.
        receipt, raw = self.read(prefix + "-receipt")
        _bridge_require(
            sha256_bytes(raw) == record["reservation_receipt_reference"]["sha256"]
            and record["state"] == "COMMITTED"
            and record["terminal_witness_reference"] is None,
            "OWNERSHIP_REQUIRED",
        )
        checkpoint = [
            cp
            for cp in state["witness_checkpoints"]
            if cp["revision"] == record["expected_next_revision"]
        ]
        _bridge_require(
            len(checkpoint) == 1 and receipt["transition"] in ("COMMIT_OPEN", "ADOPT_OPEN_MANUAL"),
            "OWNERSHIP_REQUIRED",
        )
        cp = checkpoint[0]
        opening = record["open_witness_reference"]
        base = [
            row
            for row in state["witness_checkpoints"]
            if row["revision"] == record["previous_authority_revision"]
        ]
        _bridge_require(
            all(
                cp[key] == opening[key]
                for key in (
                    "authority_id",
                    "authority_epoch",
                    "revision",
                    "ledger_digest",
                    "witness_digest",
                )
            )
            and opening["receipt_reference"]["sha256"]
            == sha256_bytes(canonical_json_bytes(self._receipt(cp)))
            and opening["receipt_reference"]["schema_version"] == cp["receipt_schema_version"]
            and len(base) == 1
            and base[0]["ledger_digest"] == record["previous_ledger_digest"]
            and base[0]["witness_digest"] == record["previous_witness_digest"],
            "OWNERSHIP_REQUIRED",
        )
        operation = (
            checkpoint[0]["operation_id"]
            if receipt["transition"] == "COMMIT_OPEN"
            else receipt["operation_id"]
        )
        expected = {
            "schema_version": "s11-ex10-b4-reservation-receipt-v1",
            "namespace": "S11C3-EX10",
            "authority_id": record["authority_id"],
            "authority_epoch": record["authority_epoch"],
            "reservation_id": record["reservation_id"],
            "reservation_digest": record["reservation_digest"],
            "fencing_token": record["fencing_token"],
            "state": "COMMITTED",
            "base_authority_revision": record["previous_authority_revision"],
            "base_authority_witness_digest": record["previous_witness_digest"],
            "open_witness_digest": record["open_witness_reference"]["witness_digest"],
            "terminal_witness_digest": None,
            "transition": receipt["transition"],
            "operation_id": operation,
            "sequence": receipt["sequence"],
            "previous_receipt_digest": receipt["previous_receipt_digest"],
            "key_id": context.witness.key_id,
            "key_epoch": context.witness.key_epoch,
        }
        wire.verify_reservation_receipt(
            raw,
            expected_context=wire.ReceiptContext(canonical_json_bytes(expected), context._now()),
            witness_pin=context.witness,
        )
        return receipt

    def coordination(self, prefix, context, *, record, minimum_sequence):
        head, raw = self.read(prefix + "-coordination")
        cfg = context._cfg()["expected_challenge"]
        projection = {
            "schema_version": "s11-ex10-b4-coordination-head-v1",
            "namespace": "S11C3-EX10",
            "authority_id": cfg["authority_id"],
            "authority_epoch": cfg["authority_epoch"],
            "active_reservation_id": record["reservation_id"],
            "receipt_digest": record["reservation_receipt_reference"]["sha256"],
            "challenge": context.host_challenge.to_dict()["challenge"],
            "key_id": context.witness.key_id,
            "key_epoch": context.witness.key_epoch,
        }
        wire.verify_coordination_head(
            raw,
            expected_context=wire.CoordinationHeadContext(
                canonical_json_bytes(projection),
                max(
                    minimum_sequence,
                    self.coordination_sequence if self.coordination_sequence is not None else 0,
                ),
                record["fencing_token"],
                context._now(),
            ),
            witness_pin=context.witness,
        )
        _bridge_require(head["fencing_token"] == record["fencing_token"], "OWNERSHIP_REQUIRED")
        self.coordination_sequence = head["sequence"]
        return head

    def current(self, *, context, action, run_id=None, phase=None):
        self.current_observation = None
        if context.host_challenge is None or context.reservation is None:
            return
        if action == "start" and not context.finished:
            return  # reserve/commit methods require their exact authenticated proofs.
        prefix = "commit" if action == "start" else ("emission" if phase == "BARRIER" else "action")
        minimum = None
        if prefix == "emission":
            state = load_existing_state_readonly(context.store, run_id)
            armed = state.payload(ART_ARMED) if state is not None else None
            _bridge_require(isinstance(armed, dict), "OWNERSHIP_REQUIRED")
            minimum = wire._time(armed["recorded_at"])
        state, head = self.history(prefix, context, minimum_issued_at=minimum)
        self._write_allowed(state)
        receipt = self._committed_receipt(prefix, context, state=state, record=context.reservation)
        coordination = self.coordination(
            prefix, context, record=context.reservation, minimum_sequence=receipt["sequence"]
        )
        rows = [r for r in state["official_runs"] if r["run_id"] == context.proposal["run_id"]]
        _bridge_require(
            rows
            and max(rows, key=lambda r: r["run_version"])["lifecycle_state"] in ("OPEN", "ACTIVE"),
            "OWNERSHIP_REQUIRED",
        )

        self._heads_current(head, coordination, context)
        self.current_observation = canonical_json_bytes(
            {
                "context_config_sha256": sha256_bytes(context.config),
                "host_challenge_sha256": context.host_challenge.sha256,
                "head": head,
                "coordination": coordination,
            }
        )

    def check_current_freshness(self, *, context, envelope=None):
        _bridge_require(
            self.owner == (os.getpid(), threading.get_ident())
            and self.current_observation is not None,
            "OWNERSHIP_REQUIRED",
        )
        observation = json.loads(self.current_observation)
        _bridge_require(
            observation["context_config_sha256"] == sha256_bytes(context.config)
            and context.host_challenge is not None
            and observation["host_challenge_sha256"] == context.host_challenge.sha256,
            "OWNERSHIP_REQUIRED",
        )
        # Sample once after parsing all retained authenticated objects. B5 and
        # both signed heads must be valid at this same final admission instant.
        now = context._now()
        self._heads_current(observation["head"], observation["coordination"], context, now=now)
        if envelope is not None:
            _bridge_require(
                wire._time(envelope["checked_at"]) <= now < wire._time(envelope["expires_at"]),
                "DEPLOYMENT_REQUIRED",
            )

    @staticmethod
    def _heads_current(head, coordination, context, *, now=None):
        # Recheck the authenticated objects after all later qualification I/O.
        # A second exchange or PF cannot repair an expired observation.
        now = context._now() if now is None else now
        for value in (head, coordination):
            _bridge_require(
                wire._time(value["issued_at"]) <= now < wire._time(value["expires_at"]),
                "HOST_HISTORY_STALE",
            )


class ExecutorBridgeChannel:
    """One bounded exchange on retained private POSIX pipes, never stdout text RPC.

    Owns duplicated FDs. All reads/writes share one monotonic deadline and a
    fixed role/sequence. Bounded diagnostics are discarded, never logged.
    Signature/context verification happens in the typed broker above this layer.
    """

    def __init__(
        self,
        *,
        read_fd,
        write_fd,
        error_fd=None,
        exchange,
        exchange_id,
        role="EXECUTOR",
        budget=120,
        process_guard=None,
    ):
        _bridge_require(
            exchange in ("START", "SINGLE_RPC") and role in ("HOST", "EXECUTOR"), "PROTOCOL_INVALID"
        )
        _bridge_require(type(budget) in (int, float) and 0 < budget <= 120, "PROTOCOL_INVALID")
        _bridge_require(
            type(exchange_id) is str and str(uuid.UUID(exchange_id)) == exchange_id,
            "PROTOCOL_INVALID",
        )
        self.exchange, self.exchange_id, self.role = (exchange, exchange_id, role)
        self.steps = START_STEPS if exchange == "START" else SINGLE_STEPS
        self.position = 0
        self.deadline = None
        self.budget = budget
        self.owner = os.getpid()
        self.dispatched = False
        self.closed = False
        self.guard = process_guard
        self.error_count = 0
        self.read_fd = self.write_fd = self.error_fd = None
        self.identities = {}
        original_exchange = (self.role, self.exchange, self.exchange_id, self.owner, self.budget)
        original_steps, original_guard = self.steps, self.guard
        retained_deadline = None

        def read_exchange_context():
            _bridge_require(
                self._exchange_context_reader is read_exchange_context
                and self._retain_exchange_deadline is retain_exchange_deadline
                and self.steps is original_steps
                and self.guard is original_guard
                and self.owner == os.getpid()
                and all(
                    type(actual) is type(expected) and actual == expected
                    for actual, expected in zip(
                        (self.role, self.exchange, self.exchange_id, self.owner, self.budget),
                        original_exchange,
                        strict=False,
                    )
                )
                and type(self.deadline) is type(retained_deadline)
                and self.deadline == retained_deadline,
                "CONTEXT_BINDING_INVALID",
            )
            return original_exchange + (retained_deadline,)

        def retain_exchange_deadline(value):
            nonlocal retained_deadline
            read_exchange_context()
            _bridge_require(
                retained_deadline is None
                and type(value) in (int, float)
                and time.monotonic() < value <= time.monotonic() + original_exchange[4],
                "EXCHANGE_EXPIRED",
            )
            retained_deadline = value
            self.deadline = value

        self._exchange_context_reader = read_exchange_context
        self._retain_exchange_deadline = retain_exchange_deadline
        try:
            for name, fd in (("read_fd", read_fd), ("write_fd", write_fd), ("error_fd", error_fd)):
                if fd is None:
                    _bridge_require(name == "error_fd", "PROTOCOL_INVALID")
                    continue
                _bridge_require(type(fd) is int and fd >= 0, "PROTOCOL_INVALID")
                info = os.fstat(fd)
                access = os.O_WRONLY if name == "write_fd" else os.O_RDONLY
                _bridge_require(
                    stat.S_ISFIFO(info.st_mode)
                    and info.st_uid == os.geteuid()
                    and (
                        os.readlink("/proc/self/fd/" + str(fd)) == "pipe:[" + str(info.st_ino) + "]"
                    )
                    and (fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == access),
                    "PRIVATE_PIPE_REQUIRED",
                )
                duplicate = os.dup(fd)
                setattr(self, name, duplicate)
                os.set_inheritable(duplicate, False)
                os.set_blocking(duplicate, False)
                self.identities[duplicate] = (info.st_dev, info.st_ino)
            _bridge_require(
                len(set(self.identities.values())) == len(self.identities), "PRIVATE_PIPE_REQUIRED"
            )
            self._check()
        except (KeyboardInterrupt, SystemExit):
            self.close()
            raise
        except Exception:
            self.close()
            raise ExecutorBridgeError("PRIVATE_PIPE_REQUIRED") from None

    def _fail(self, code):
        raise ExecutorBridgeError("UNCERTAIN_PUBLICATION" if self.dispatched else code) from None

    def _check(self):
        reader = self._exchange_context_reader
        reader()
        if self.closed or self.owner != os.getpid():
            self._fail("PROTOCOL_INVALID")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self._fail("EXCHANGE_EXPIRED")
        try:
            for fd, expected in self.identities.items():
                info = os.fstat(fd)
                _bridge_require(
                    (info.st_dev, info.st_ino) == expected
                    and stat.S_ISFIFO(info.st_mode)
                    and (not os.get_inheritable(fd))
                    and (info.st_uid == os.geteuid())
                    and (
                        os.readlink("/proc/self/fd/" + str(fd)) == "pipe:[" + str(info.st_ino) + "]"
                    )
                    and (
                        fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE
                        == (os.O_WRONLY if fd == self.write_fd else os.O_RDONLY)
                    ),
                    "PRIVATE_PIPE_REQUIRED",
                )
            if self.guard is not None:
                _bridge_require(self.guard() is None, "PROCESS_IDENTITY_CHANGED")
            reader()
        except Exception:
            self._fail("PROCESS_IDENTITY_CHANGED")

    def _activate(self):
        self._check()
        if self.deadline is None:
            self._retain_exchange_deadline(time.monotonic() + self.budget)

    def mark_dispatched(self):
        self._check()
        self.dispatched = True

    def _wait(self, *, writing=False):
        self._activate()
        self._check()
        read = [] if writing else [self.read_fd]
        if self.error_fd is not None:
            read.append(self.error_fd)
        try:
            ready, writable, _ = select.select(
                read,
                [self.write_fd] if writing else [],
                [],
                max(0, self.deadline - time.monotonic()),
            )
            if self.error_fd is not None and self.error_fd in ready:
                chunk = os.read(self.error_fd, min(4097, 4097 - self.error_count))
                self.error_count += len(chunk)
                if self.error_count > 4096:
                    self._fail("DIAGNOSTIC_LIMIT")
                if not chunk:
                    self.identities.pop(self.error_fd)
                    os.close(self.error_fd)
                    self.error_fd = None
            self._check()
            return bool(writable) if writing else self.read_fd in ready
        except ExecutorBridgeError:
            raise
        except Exception:
            self._fail("TRANSPORT_UNAVAILABLE")

    def _read_exact(self, size):
        _bridge_require(type(size) is int and 0 < size <= wire.MAX_RESPONSE_BYTES, "FRAME_LIMIT")
        chunks = bytearray()
        while len(chunks) < size:
            if not self._wait():
                continue
            try:
                chunk = os.read(self.read_fd, size - len(chunks))
            except BlockingIOError:
                continue
            except OSError:
                self._fail("TRANSPORT_UNAVAILABLE")
            if not chunk:
                self._fail("FRAME_INCOMPLETE")
            chunks.extend(chunk)
        self._check()
        return bytes(chunks)

    def _write_exact(self, data):
        view = memoryview(data)
        while view:
            if not self._wait(writing=True):
                continue
            try:
                written = os.write(self.write_fd, view)
            except BlockingIOError:
                continue
            except OSError:
                self._fail("TRANSPORT_UNAVAILABLE")
            if written <= 0:
                self._fail("TRANSPORT_UNAVAILABLE")
            view = view[written:]
        self._check()

    def _frame(self, raw, *, direction):
        self._check()
        if self.position >= len(self.steps):
            self._fail("SECOND_OPERATION_DENIED")
        try:
            parser = (
                wire.parse_handshake_frame_bytes
                if self.exchange == "START"
                else wire.parse_single_rpc_frame_bytes
            )
            message = parser(raw)
            value = message.to_dict()
            _bridge_require(
                (value["sequence"], value["kind"], value["direction"], value["exchange_id"])
                == (self.position + 1, *self.steps[self.position], self.exchange_id)
                and value["direction"] == direction,
                "FRAME_ORDER_INVALID",
            )
        except Exception:
            self._fail("FRAME_ORDER_INVALID")
        return message

    def send(self, raw):
        direction = "HOST_TO_EXECUTOR" if self.role == "HOST" else "EXECUTOR_TO_HOST"
        message = self._frame(raw, direction=direction)
        self._write_exact(wire.encode_frame(message))
        self.position += 1

    def receive(self):
        direction = "EXECUTOR_TO_HOST" if self.role == "HOST" else "HOST_TO_EXECUTOR"
        self._check()
        prefix = self._read_exact(4)
        length = int.from_bytes(prefix, "big")
        maximum = (
            wire.MAX_REQUEST_BYTES if direction == "EXECUTOR_TO_HOST" else wire.MAX_RESPONSE_BYTES
        )
        if not 0 < length <= maximum:
            self._fail("FRAME_LIMIT")
        message = self._frame(self._read_exact(length), direction=direction)
        self.position += 1
        return message

    def finish(self):
        self._check()
        if self.position != len(self.steps):
            self._fail("FRAME_INCOMPLETE")
        self.identities.pop(self.write_fd)
        fd, self.write_fd = (self.write_fd, None)
        try:
            os.close(fd)
        except OSError:
            self._fail("TRANSPORT_UNAVAILABLE")
        while True:
            if not self._wait():
                continue
            try:
                extra = os.read(self.read_fd, 1)
            except BlockingIOError:
                continue
            except OSError:
                self._fail("TRANSPORT_UNAVAILABLE")
            if extra:
                self._fail("EXTRA_STDOUT")
            break
        self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        for name in ("read_fd", "write_fd", "error_fd"):
            fd = getattr(self, name, None)
            if fd is not None:
                setattr(self, name, None)
                try:
                    os.close(fd)
                except OSError:
                    pass
        self.identities.clear()

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *args):
        self.close()


# The sole OS bootstrap is public constructor data, selected by the approved
# launcher before create. No frame, provider name, private key or path selects it.
_NATIVE_BOOTSTRAP_KEY = b"S11_EX10_NATIVE_BOOTSTRAP"
_NATIVE_ENTRY = "/app/manage.py"
_NATIVE_PYTHON = "/usr/local/bin/python"


def _native_initial_bootstrap():
    try:
        with open("/proc/1/environ", "rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        _bridge_require(len(raw) <= 1024 * 1024, "NATIVE_BOOTSTRAP_REQUIRED")
        selected = [
            entry.split(b"=", 1)[1]
            for entry in raw.split(b"\0")
            if entry.startswith(_NATIVE_BOOTSTRAP_KEY + b"=")
        ]
        _bridge_require(
            len(selected) == 1 and 0 < len(selected[0]) <= 32768, "NATIVE_BOOTSTRAP_REQUIRED"
        )
        value = open_evidence.parse_canonical_bytes(selected[0], maximum=32768)
        _bridge_require(
            type(value) is dict
            and set(value)
            == {
                "action",
                "exchange_id",
                "profile_sha256",
                "header_sha256",
                "bridge_pin",
                "witness_pin",
                "b3_pins",
                "genesis_digest",
                "genesis_binding",
                "authority_revision",
                "request_payload",
                "expected_reservation",
                "host_root_binding_sha256",
                "source_hashes",
                "staging_name",
                "command_options",
                "authorization_reference",
            },
            "NATIVE_BOOTSTRAP_REQUIRED",
        )
        _bridge_require(
            value["action"] in ("start", "execute", "drain", "reconcile", "finalize")
            and str(uuid.UUID(value["exchange_id"])) == value["exchange_id"],
            "NATIVE_BOOTSTRAP_REQUIRED",
        )
        for name in (
            "profile_sha256",
            "header_sha256",
            "genesis_digest",
            "host_root_binding_sha256",
        ):
            _bridge_require(
                type(value[name]) is str and re.fullmatch(r"[0-9a-f]{64}", value[name]),
                "NATIVE_BOOTSTRAP_REQUIRED",
            )
        _bridge_require(
            type(value["authority_revision"]) is int
            and 0 <= value["authority_revision"] <= wire.SAFE_INTEGER_MAX,
            "NATIVE_BOOTSTRAP_REQUIRED",
        )
        if value["action"] == "start":
            name = value["staging_name"]
            _bridge_require(
                type(name) is str
                and re.fullmatch(r"application-preflight\.[0-9a-f-]{36}\.json", name)
                and str(uuid.UUID(name[len("application-preflight.") : -5]))
                == name[len("application-preflight.") : -5],
                "NATIVE_BOOTSTRAP_REQUIRED",
            )
        else:
            _bridge_require(value["staging_name"] is None, "NATIVE_BOOTSTRAP_REQUIRED")
        for name in ("bridge_pin", "witness_pin"):
            pin = value[name]
            _bridge_require(
                type(pin) is dict
                and set(pin) == {"key_id", "key_epoch", "public_key_hex"}
                and type(pin["key_id"]) is str
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", pin["key_id"])
                and type(pin["key_epoch"]) is int
                and 1 <= pin["key_epoch"] <= wire.SAFE_INTEGER_MAX
                and type(pin["public_key_hex"]) is str
                and re.fullmatch(r"[0-9a-f]{64}", pin["public_key_hex"]),
                "NATIVE_BOOTSTRAP_REQUIRED",
            )
        _native_initial_payload(value)
        _native_command_arguments(value)
        return value, selected[0]
    except ExecutorBridgeError:
        raise
    except Exception:
        raise ExecutorBridgeError("NATIVE_BOOTSTRAP_REQUIRED") from None


def _native_mount_observation():
    """Independent full CID from all three actual read-only Docker metadata mounts."""
    try:
        with open("/proc/self/mountinfo", encoding="ascii") as stream:
            raw = stream.read(1024 * 1024 + 1)
        _bridge_require(len(raw) <= 1024 * 1024, "PROCESS_ADMISSION_REQUIRED")
        mounts = {}
        for line in raw.splitlines():
            fields = line.split()
            _bridge_require(len(fields) >= 10 and "-" in fields, "PROCESS_ADMISSION_REQUIRED")

            def decode(text):
                return re.sub("\\\\([0-7]{3})", lambda m: chr(int(m[1], 8)), text)

            target, root = decode(fields[4]), decode(fields[3])
            _bridge_require(target not in mounts, "PROCESS_ADMISSION_REQUIRED")
            mounts[target] = (root, frozenset(fields[5].split(",")))
        ids = []
        for target, leaf in (
            ("/etc/hostname", "hostname"),
            ("/etc/hosts", "hosts"),
            ("/etc/resolv.conf", "resolv.conf"),
        ):
            root, options = mounts[target]
            match = re.fullmatch(r"/docker/containers/([0-9a-f]{64})/" + re.escape(leaf), root)
            _bridge_require(match is not None and "ro" in options, "PROCESS_ADMISSION_REQUIRED")
            ids.append(match[1])
        _bridge_require(len(set(ids)) == 1 and "ro" in mounts["/"][1], "PROCESS_ADMISSION_REQUIRED")
        return ids[0], mounts
    except ExecutorBridgeError:
        raise
    except Exception:
        raise ExecutorBridgeError("PROCESS_ADMISSION_REQUIRED") from None


def _native_command_arguments(cfg):
    """One approved action, compiled to existing arguments; no arbitrary argv/env."""
    action, options = cfg["action"], cfg["command_options"]
    fields = {"expected_git_sha"}
    if action == "start":
        fields |= {"source_root", "nonce", "acknowledge_not_demonstrated"}
    else:
        fields |= {"run_id"}
        if action == "finalize":
            fields |= {"abandon_reason", "declare_executor_gone"}
    _bridge_require(type(options) is dict and set(options) == fields, "NATIVE_BOOTSTRAP_REQUIRED")
    _bridge_require(
        type(options["expected_git_sha"]) is str and _SHA1.fullmatch(options["expected_git_sha"]),
        "SOURCE_REVISION_CHANGED",
    )
    arguments = [
        action,
        "--operational-context-stdin",
        "--expected-git-sha",
        options["expected_git_sha"],
    ]
    if action == "start":
        _bridge_require(
            options["source_root"] == "/s11-full-source"
            and type(options["nonce"]) is str
            and re.fullmatch(r"[0-9a-f]{64}", options["nonce"])
            and type(options["acknowledge_not_demonstrated"]) is list
            and all(
                type(run) is str and valid_run_id(run)
                for run in options["acknowledge_not_demonstrated"]
            ),
            "NATIVE_BOOTSTRAP_REQUIRED",
        )
        arguments += [
            "--source-root",
            "/s11-full-source",
            "--nonce",
            options["nonce"],
            "--evidence-mode",
            OFFICIAL_MODE,
            "--confirm-new-run",
        ]
        for run in options["acknowledge_not_demonstrated"]:
            arguments += ["--acknowledge-not-demonstrated", run]
    else:
        _bridge_require(
            type(options["run_id"]) is str and valid_run_id(options["run_id"]), "RUN_ID_MISMATCH"
        )
        arguments += ["--run-id", options["run_id"]]
        if action == "execute":
            arguments.append("--confirm-emission")
        if action == "finalize":
            _bridge_require(
                options["abandon_reason"] is None
                or type(options["abandon_reason"]) is str
                and _REASON.fullmatch(options["abandon_reason"]),
                "NATIVE_BOOTSTRAP_REQUIRED",
            )
            _bridge_require(
                type(options["declare_executor_gone"]) is bool, "NATIVE_BOOTSTRAP_REQUIRED"
            )
            if options["abandon_reason"] is not None:
                arguments += ["--abandon-reason", options["abandon_reason"]]
            if options["declare_executor_gone"]:
                arguments.append("--declare-executor-gone")
    return arguments


def _native_initial_payload(cfg):
    payload = cfg["request_payload"]
    required = (
        {
            "acknowledgments",
            "deployment_verification_reference",
            "descriptor_reference",
            "expected_head",
            "profile_reference",
            "session_reference",
        }
        if cfg["action"] == "start"
        else {"reservation_id", "original_request_id"}
    )
    _bridge_require(type(payload) is dict and set(payload) == required, "NATIVE_BOOTSTRAP_REQUIRED")
    return payload


def _native_process_identity(*, cfg, profile, container_id, initial, envelope):
    """Fixed observer preimages, independent of any published process DTO.

    The final B5 is absent from initial Env; its exact bytes enter only the
    deployment commitment, subsequently authenticated by HOST_CHALLENGE.
    """
    p = profile.to_dict()
    identity = sha256_bytes(
        canonical_json_bytes(
            {
                "container_id": container_id,
                "runtime_profile_sha256": cfg["profile_sha256"],
                "session_header_sha256": cfg["header_sha256"],
                "project": p["project"],
                "source_revision": p["source_revision"],
                "source_hashes": cfg["source_hashes"],
                "native_bootstrap_sha256": sha256_bytes(initial),
            }
        )
    )
    deployment = sha256_bytes(
        canonical_json_bytes(
            {
                "container_identity_digest": identity,
                "operational_envelope_sha256": sha256_bytes(envelope),
            }
        )
    )
    return {
        "container_id": container_id,
        "identity_digest": identity,
        "deployment_verification_sha256": deployment,
    }


def _native_prepared_payload(*, cfg, profile, envelope):
    payload = _native_initial_payload(cfg)
    if cfg["action"] != "start":
        return dict(payload)
    host_root = profile.to_dict()["host_roots"]["evidence"]["path"]
    return {
        **payload,
        "operational_envelope_reference": {
            "path": host_root + "/.s11-operational/" + cfg["exchange_id"] + "/envelope.json",
            "sha256": sha256_bytes(envelope),
            "schema_version": deployment_envelope.SCHEMA_VERSION,
        },
    }


def _native_wait_prepared(*, cfg, deadline, guard, alive):
    paths = [
        Path("/s11-evidence/.s11-operational") / cfg["exchange_id"] / (role + ".json")
        for role in ("process", "envelope")
    ]
    if cfg["action"] == "start":
        paths.insert(0, Path("/s11-evidence") / cfg["staging_name"])
    while True:
        guard()
        _bridge_require(alive() and time.monotonic() < deadline, "START_NOT_PREPARED")
        if all(path.exists() for path in paths):
            return
        time.sleep(0.05)


_NATIVE_EMISSION_ROLES = (
    "emission-material",
    "emission-head",
    "emission-coordination",
    "emission-receipt",
)


def _native_wait_emission(*, cfg, proof, deadline, guard):
    """Wait for one exclusive host publication; existence never grants permission.

    The deadline is the bootstrap's original absolute monotonic deadline.
    Once any role appears, its canonical bytes and private identity are retained;
    malformed, replaced or removed roles fail rather than being repaired.
    The caller still performs full B3/B4/GOV/B5 verification exactly once.
    """
    _bridge_require(
        cfg["action"] == "execute" and cfg["exchange_id"] == proof.exchange_id,
        "CONTEXT_BINDING_INVALID",
    )
    directory = proof.root + "/.s11-operational/" + proof.exchange_id
    retained, identities, directory_lease = {}, {}, None

    def stamp(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_uid,
            info.st_gid,
            info.st_mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    def recheck():
        # Keep the first complete publication's directory and file leases
        # through full qualification and the final OS/B5 reads.
        fd = None
        try:
            fd, lease = profile_codec._walk(directory, "directory")
            _bridge_require(lease == directory_lease, "HOST_PROOF_CHANGED")
            for role in _NATIVE_EMISSION_ROLES:
                before = os.stat(role + ".json", dir_fd=fd, follow_symlinks=False)
                _bridge_require(stamp(before) == identities[role], "HOST_PROOF_CHANGED")
                _, raw = proof.read(role)
                after = os.stat(role + ".json", dir_fd=fd, follow_symlinks=False)
                _bridge_require(
                    raw == retained[role] and stamp(after) == identities[role],
                    "HOST_PROOF_CHANGED",
                )
            check, current = profile_codec._walk(directory, "directory")
            try:
                _bridge_require(current == directory_lease, "HOST_PROOF_CHANGED")
            finally:
                os.close(check)
        except ExecutorBridgeError:
            raise
        except Exception:
            raise ExecutorBridgeError("HOST_PROOF_CHANGED") from None
        finally:
            if fd is not None:
                os.close(fd)
        _bridge_require(time.monotonic() < deadline, "EMISSION_NOT_PREPARED")

    while True:
        guard()
        _bridge_require(time.monotonic() < deadline, "EMISSION_NOT_PREPARED")
        fd = None
        try:
            fd, lease = profile_codec._walk(directory, "directory")
            info = os.fstat(fd)
            _bridge_require(
                (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
                == (os.geteuid(), os.getegid(), 0o700),
                "HOST_PROOF_INVALID",
            )
            if directory_lease is None:
                directory_lease = lease
            _bridge_require(lease == directory_lease, "HOST_PROOF_CHANGED")
            for role in _NATIVE_EMISSION_ROLES:
                try:
                    observed = os.stat(role + ".json", dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    _bridge_require(role not in retained, "HOST_PROOF_CHANGED")
                    continue
                _bridge_require(
                    stat.S_ISREG(observed.st_mode)
                    and observed.st_nlink == 1
                    and (observed.st_uid, observed.st_gid, stat.S_IMODE(observed.st_mode))
                    == (os.geteuid(), os.getegid(), 0o600)
                    and 0 < observed.st_size <= 16 * 1024 * 1024,
                    "HOST_PROOF_INVALID",
                )
                _, raw = proof.read(role)
                current = os.stat(role + ".json", dir_fd=fd, follow_symlinks=False)
                _bridge_require(stamp(current) == stamp(observed), "HOST_PROOF_CHANGED")
                if role in retained:
                    _bridge_require(
                        raw == retained[role] and stamp(current) == identities[role],
                        "HOST_PROOF_CHANGED",
                    )
                else:
                    retained[role], identities[role] = raw, stamp(current)
            check, current = profile_codec._walk(directory, "directory")
            try:
                _bridge_require(current == directory_lease, "HOST_PROOF_CHANGED")
            finally:
                os.close(check)
        except ExecutorBridgeError:
            raise
        except Exception:
            raise ExecutorBridgeError("HOST_PROOF_INVALID") from None
        finally:
            if fd is not None:
                os.close(fd)
        guard()
        _bridge_require(time.monotonic() < deadline, "EMISSION_NOT_PREPARED")
        if len(retained) == len(_NATIVE_EMISSION_ROLES):
            return recheck
        time.sleep(0.05)


def _native_dev_null_mask(mask, mounts):
    target = "/s11-full-source/.env"
    _bridge_require(
        mask
        == {
            "relative_path": ".env",
            "kind": "dev_null_file",
            "source_path": "/dev/null",
            "read_only": True,
        }
        and target in mounts
        and mounts[target][0] == "/null"
        and "ro" in mounts[target][1],
        "ROOT_BINDING_INVALID",
    )
    # mountinfo.root is filesystem-relative for this approved character device.
    # Host observer still proves bind source=/dev/null; backend proves actual
    # no-alias target FD, character type, major/minor1:3 and readonly mount.
    try:
        fd, _ = profile_codec._walk(target, "dev_null")
        try:
            info = os.fstat(fd)
            _bridge_require(
                stat.S_ISCHR(info.st_mode) and info.st_rdev == os.makedev(1, 3),
                "ROOT_BINDING_INVALID",
            )
        finally:
            os.close(fd)
    except ExecutorBridgeError:
        raise
    except Exception:
        raise ExecutorBridgeError("ROOT_BINDING_INVALID") from None


class ExecutorNativeBootstrap:
    """Fixed OS loader; host retains approval, Docker, B3/GOV/floor and action lease.

    Initial public pins never come from a proof or frame. Profile/header copies
    are hash-bound to initial approved Docker Env. Process/B5 copies are
    provisional until the independent bridge pin verifies their challenge.
    No historical path, socket, host ledger, sidecar or key is read here.
    """

    def __init__(self):
        preparation_started = time.monotonic()
        cfg, self.initial = _native_initial_bootstrap()
        _native_command_arguments(cfg)
        self.cfg = cfg
        self.preparation_deadline = preparation_started + 900
        self.owner = (os.getpid(), threading.get_ident())
        self.cid, self.mounts = _native_mount_observation()
        self.proof = ExecutorHostProofs(
            root="/s11-evidence",
            exchange_id=cfg["exchange_id"],
            b3_pins=cfg["b3_pins"],
            genesis_digest=cfg["genesis_digest"],
            genesis_binding=cfg["genesis_binding"],
            clock=_now,
        )
        profile, pbytes = self.proof.read("profile")
        self.profile = profile_codec.parse_runtime_profile_bytes(pbytes)
        header, hbytes = self.proof.read("header")
        session_codec._validate_header(header)
        _bridge_require(
            session_codec.canonical_session_header_bytes(header) == hbytes
            and sha256_bytes(pbytes) == cfg["profile_sha256"]
            and sha256_bytes(hbytes) == cfg["header_sha256"],
            "CONTEXT_BINDING_INVALID",
        )
        session_codec._profile_binding(header, self.profile)
        self.header, self.profile_bytes, self.header_bytes = header, pbytes, hbytes
        _bridge_require(
            header["expected_git_sha"] == cfg["command_options"]["expected_git_sha"],
            "SOURCE_REVISION_CHANGED",
        )
        self.process_bytes = self.envelope_bytes = self.context = None
        self.preflight = self.proposal = None
        self.emission_admitted = False
        self.channel = None
        self.check_os()

    def check_os(self):
        _bridge_require(
            self.owner == (os.getpid(), threading.get_ident()) and os.getpid() != 1,
            "PROCESS_ADMISSION_REQUIRED",
        )
        _, initial = _native_initial_bootstrap()
        cid, mounts = _native_mount_observation()
        _bridge_require(
            initial == self.initial and cid == self.cid and mounts == self.mounts,
            "PROCESS_IDENTITY_CHANGED",
        )
        with open("/proc/1/cmdline", "rb") as stream:
            parent = stream.read(4097)
        _bridge_require(
            parent.rstrip(b"\0").split(b"\0")
            == [b"/usr/local/bin/python", b"-B", b"/app/manage.py", b"s11_ex10", b"bridge"],
            "PROCESS_ADMISSION_REQUIRED",
        )
        value = self.profile.to_dict()
        owner = value["expected_owner"]
        _bridge_require(
            (os.geteuid(), os.getegid()) == (owner["uid"], owner["gid"]), "ROOT_BINDING_INVALID"
        )
        required = {"/s11-full-source": (value["source_host_root"], True)}
        for role, target in value["fixed_targets"].items():
            required[target] = (
                value["host_roots"][role]["path"],
                False if role == "evidence" else None,
            )
            fd, _ = profile_codec._walk(target, "directory")
            try:
                info = os.fstat(fd)
                _bridge_require(
                    (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
                    == (owner["uid"], owner["gid"], 0o700),
                    "ROOT_BINDING_INVALID",
                )
            finally:
                os.close(fd)
        for mask in value["masks"]:
            if mask["kind"] == "dev_null_file":
                _native_dev_null_mask(mask, mounts)
            else:
                required["/s11-full-source/" + mask["relative_path"]] = (mask["source_path"], True)
        for target, (root, readonly) in required.items():
            _bridge_require(
                target in mounts
                and mounts[target][0] == root
                and (readonly is None or ("ro" in mounts[target][1]) == readonly),
                "ROOT_BINDING_INVALID",
            )
        vector = self.cfg["source_hashes"]
        _bridge_require(
            type(vector) is dict and set(vector) == set(EXECUTOR_SOURCES), "SOURCE_REVISION_CHANGED"
        )
        for relative, expected in vector.items():
            _bridge_require(
                type(expected) is str and re.fullmatch(r"[0-9a-f]{64}", expected),
                "SOURCE_REVISION_CHANGED",
            )
            full = "/s11-full-source/" + relative
            fd, lease = profile_codec._walk(full, "file")
            try:
                before = os.fstat(fd)
                _bridge_require(
                    (before.st_uid, before.st_gid) == (owner["uid"], owner["gid"])
                    and before.st_nlink == 1
                    and 0 < before.st_size <= 16 * 1024 * 1024,
                    "SOURCE_REVISION_CHANGED",
                )
                data = bytearray()
                while len(data) <= before.st_size:
                    chunk = os.read(fd, min(65536, before.st_size + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                check, after_lease = profile_codec._walk(full, "file")
                try:

                    def stamp(i):
                        return (
                            i.st_dev,
                            i.st_ino,
                            i.st_size,
                            i.st_mtime_ns,
                            i.st_ctime_ns,
                            i.st_mode,
                        )

                    _bridge_require(
                        lease == after_lease
                        and stamp(before) == stamp(os.fstat(fd)) == stamp(os.fstat(check))
                        and sha256_bytes(bytes(data)) == expected,
                        "SOURCE_REVISION_CHANGED",
                    )
                finally:
                    os.close(check)
            finally:
                os.close(fd)
            actual = Path(settings.BASE_DIR) / relative.removeprefix("backend/")
            _bridge_require(sha256_file(actual) == expected, "SOURCE_REVISION_CHANGED")
        _, pbytes = self.proof.read("profile")
        _, hbytes = self.proof.read("header")
        _bridge_require(
            pbytes == self.profile_bytes and hbytes == self.header_bytes, "CONTEXT_BINDING_INVALID"
        )
        if self.process_bytes is not None:
            _, raw = self.proof.read("process")
            _, envelope = self.proof.read("envelope")
            _bridge_require(
                raw == self.process_bytes and envelope == self.envelope_bytes,
                "PROCESS_IDENTITY_CHANGED",
            )
            self._envelope(envelope)

    def _envelope(self, raw):
        fresh = self.cfg["action"] in ("start", "execute") and not self.emission_admitted
        if self.context is not None and self.context.scientific_finished:
            fresh = False
        return _native_envelope_bindings(
            raw,
            profile=self.profile,
            header=self.header,
            cfg=self.cfg,
            preflight=self.preflight,
            now=_now(),
            fresh_required=fresh,
        )

    def current(self, *, context, action, run_id=None, phase=None):
        boundary = action == "execute" and phase == "BARRIER"
        if boundary:
            _bridge_require(
                type(context) is ExecutorOperationalContext
                and context is self.context
                and context.finished
                and context.step_authorized
                and context.host_challenge is not None
                and context.reservation is not None
                and context.proposal["run_id"] == run_id,
                "OWNERSHIP_REQUIRED",
            )
            prepared = _native_wait_emission(
                cfg=self.cfg,
                proof=self.proof,
                deadline=self.preparation_deadline,
                guard=self.check_os,
            )
        self.proof.current(context=context, action=action, run_id=run_id, phase=phase)
        if boundary:
            # Full qualification and OS/B5 reads may do I/O. Retain the
            # exact exclusive publication until the existing final clock
            # admission. No extra frame, permission, RPC or application PF.
            prepared()
            self.check_os()
            # The genuine current-proof read may consume time. Recheck the
            # frozen B5 immediately before crossing T0; no second PF or RPC.
            envelope = self._envelope(self.envelope_bytes)
            prepared()
            self.proof.check_current_freshness(context=context, envelope=envelope)
            _bridge_require(time.monotonic() < self.preparation_deadline, "EMISSION_NOT_PREPARED")
            self.emission_admitted = True

    def admit(self, *, context, action):
        _bridge_require(
            context is self.context or self.context is None, "PROCESS_ADMISSION_REQUIRED"
        )
        _bridge_require(action == self.cfg["action"], "ACTION_INVALID")
        self.check_os()

    def prepare_terminal(self, *, context, store, run_id, result):
        self.admit(context=context, action="finalize")
        _bridge_require(
            context.step_authorized and result["run_id"] == run_id, "TERMINAL_PUBLICATION_DENIED"
        )
        # Host validates the sealed run and publishes B3/retirement after the
        # worker exits; this callback only prepares the existing private result.
        state = load_existing_state_readonly(store, run_id)
        _bridge_require(state is not None and state.finalized, "CUSTODY_SEAL_REQUIRED")
        validate_finalized_state(
            state.run_root, state.records, custody_hmac_key=store.custody_hmac_key
        )

    def build(self, *, options, store, probes):
        expected_options = self.cfg["command_options"]
        _bridge_require(
            options["action"] == self.cfg["action"]
            and all(options.get(key) == value for key, value in expected_options.items())
            and str(store.root) == "/s11-evidence",
            "CONTEXT_BINDING_INVALID",
        )
        if self.cfg["action"] == "start":
            _bridge_require(
                not self.preflight and self.cfg["expected_reservation"] is None,
                "SECOND_OPERATION_DENIED",
            )
            self.check_os()
            proposal, raw = _prepare_native_start(
                probes=probes,
                git_sha=expected_options["expected_git_sha"],
                source_root=Path("/s11-full-source"),
                nonce=expected_options["nonce"],
            )
            # Root-bound writer without constructing/challenging authority yet.
            _native_private_artifact(store, self.cfg["staging_name"], raw)
            self.preflight, self.proposal = raw, proposal
        # Publication is host preparation, not an RPC retry or PF refresh.
        _native_wait_prepared(
            cfg=self.cfg,
            deadline=self.preparation_deadline,
            guard=self.check_os,
            alive=lambda: True,
        )
        process, self.process_bytes = self.proof.read("process")
        _, self.envelope_bytes = self.proof.read("envelope")
        _bridge_require(
            type(process) is dict
            and set(process)
            == {"container_id", "identity_digest", "deployment_verification_sha256"}
            and process["container_id"] == self.cid
            and all(type(v) is str and re.fullmatch(r"[0-9a-f]{64}", v) for v in process.values()),
            "PROCESS_IDENTITY_CHANGED",
        )
        self._envelope(self.envelope_bytes)
        _bridge_require(
            process
            == _native_process_identity(
                cfg=self.cfg,
                profile=self.profile,
                container_id=self.cid,
                initial=self.initial,
                envelope=self.envelope_bytes,
            ),
            "PROCESS_IDENTITY_CHANGED",
        )
        payload = _native_prepared_payload(
            cfg=self.cfg, profile=self.profile, envelope=self.envelope_bytes
        )
        channel = ExecutorBridgeChannel(
            read_fd=0,
            write_fd=1,
            exchange="START" if self.cfg["action"] == "start" else "SINGLE_RPC",
            exchange_id=self.cfg["exchange_id"],
            process_guard=self.check_os,
        )
        # Transfer sole ownership; scientific output can never keep Docker RPC alive.
        _native_transfer_stdio(channel)
        self.channel = channel
        fixed = {
            "exchange_id": self.cfg["exchange_id"],
            "authority_id": self.cfg["b3_pins"][0]["authority_id"],
            "authority_epoch": self.cfg["b3_pins"][0]["authority_epoch"],
            "authorization_reference": self.cfg["authorization_reference"],
            "bridge_key_id": self.cfg["bridge_pin"]["key_id"],
            "bridge_key_epoch": self.cfg["bridge_pin"]["key_epoch"],
            "container_identity_digest": process["identity_digest"],
            "deployment_verification_sha256": process["deployment_verification_sha256"],
            "session_id": self.header["session_id"],
            "session_header_sha256": self.cfg["header_sha256"],
            "runtime_profile_sha256": self.cfg["profile_sha256"],
            "source_revision": self.profile.to_dict()["source_revision"],
            "project": self.profile.to_dict()["project"],
        }

        def pin(name):
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            value = self.cfg[name]
            _bridge_require(
                type(value) is dict
                and set(value) == {"key_id", "key_epoch", "public_key_hex"}
                and type(value["key_id"]) is str
                and type(value["key_epoch"]) is int
                and value["key_epoch"] >= 1
                and type(value["public_key_hex"]) is str
                and re.fullmatch(r"[0-9a-f]{64}", value["public_key_hex"]),
                "INDEPENDENT_PIN_REQUIRED",
            )
            return wire.PublicKeyPin(
                value["key_id"],
                value["key_epoch"],
                Ed25519PublicKey.from_public_bytes(bytes.fromhex(value["public_key_hex"])),
            )

        result = ExecutorOperationalContext(
            action=self.cfg["action"],
            channel=channel,
            bridge_pin=pin("bridge_pin"),
            witness_pin=pin("witness_pin"),
            holder_key=Ed25519PrivateKey.generate(),
            expected_challenge=fixed,
            request_payload=payload,
            evidence_root="/s11-evidence",
            host_evidence_root=self.profile.to_dict()["host_roots"]["evidence"]["path"],
            staging_name=self.cfg["staging_name"],
            authority_revision=self.cfg["authority_revision"],
            host_root_binding_sha256=self.cfg["host_root_binding_sha256"],
            expected_reservation=self.cfg["expected_reservation"],
            native_admitter=self.admit,
            current_guard=self.current,
            common_prior_reader=self.proof.common_prior,
            common_prior_verifier=self.proof.verify_common,
            commit_verifier=self.proof.verify_commit,
            terminal_preparer=self.prepare_terminal,
            clock=_now,
        )
        self.context = result
        if self.preflight is not None:
            result.preflight, result.proposal, result.store, result.prepared = (
                self.preflight,
                self.proposal,
                store,
                True,
            )
        _bridge_require(time.monotonic() < self.preparation_deadline, "START_NOT_PREPARED")
        return result


def _native_envelope_bindings(raw, *, profile, header, cfg, preflight, now, fresh_required):
    envelope = deployment_envelope.parse_envelope_bytes(raw).to_dict()
    p = profile.to_dict()
    _bridge_require(
        envelope["result"] == "PASS"
        and wire._time(envelope["checked_at"]) <= now
        and (not fresh_required or now < wire._time(envelope["expires_at"]))
        and envelope["expected_git_sha"] == p["source_revision"]
        and envelope["runtime_profile_sha256"] == cfg["profile_sha256"]
        and envelope["session_header_sha256"] == cfg["header_sha256"]
        and envelope["session_id"] == header["session_id"]
        and envelope["project"] == p["project"],
        "DEPLOYMENT_REQUIRED",
    )
    for name in (
        "host_roots",
        "fixed_targets",
        "compose_files",
        "override_path",
        "override_sha256",
        "deployment_verification_reference",
    ):
        _bridge_require(envelope[name] == p[name], "DEPLOYMENT_REQUIRED")
    # B5's qualified current authority view and B1's stable descriptor have
    # different roles. Signed current B3/coordination proofs bind the former;
    # the initial genesis/pin lineage and complete history bind the descriptor.
    _bridge_require(
        envelope["running_sources"]
        == {
            name: digest
            for name, digest in cfg["source_hashes"].items()
            if name in ex10_preflight.RUNNING_SOURCES
        },
        "DEPLOYMENT_REQUIRED",
    )
    if preflight is not None:
        _bridge_require(
            envelope["application_preflight_sha256"] == sha256_bytes(preflight), "PREFLIGHT_CHANGED"
        )
    return envelope


def _prepare_native_start(*, probes, git_sha, source_root, nonce):
    problems = list(probes.provider()) + list(probes.sources(source_root))
    report = probes.preflight(ex10_preflight.PreflightOptions(git_sha, source_root))
    now = probes.now()
    problems.extend(preflight_problems(report, git_sha=git_sha, now=now))
    if problems:
        raise Ex10Refusal(problems, terminal=False)
    proposal = propose_run(started_at=now, git_sha=git_sha, nonce=nonce)
    _bridge_require(
        not ex10_load.sensitive_findings(report, dataset=probes.dataset()), "PREFLIGHT_SENSITIVE"
    )
    return proposal, canonical_json_bytes(report)


def _native_private_artifact(store, name, raw):
    _bridge_require(
        str(store.root) == "/s11-evidence"
        and type(name) is str
        and re.fullmatch(r"application-preflight\.[0-9a-f-]{36}\.json", name),
        "STAGING_INVALID",
    )
    _bridge_require(
        str(uuid.UUID(name[len("application-preflight.") : -5]))
        == name[len("application-preflight.") : -5],
        "STAGING_INVALID",
    )
    root, lease = profile_codec._walk("/s11-evidence", "directory")
    fd = None
    try:
        info = os.fstat(root)
        _bridge_require(
            (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
            == (os.geteuid(), os.getegid(), 0o700),
            "ROOT_BINDING_INVALID",
        )
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root)
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            _bridge_require(written > 0, "WRITE_UNKNOWN")
            offset += written
        os.fsync(fd)
        os.fsync(root)
        check, current = profile_codec._walk("/s11-evidence", "directory")
        try:
            now = os.fstat(check)
            _bridge_require(
                current == lease
                and (now.st_dev, now.st_ino) == (info.st_dev, info.st_ino)
                and (now.st_uid, now.st_gid, stat.S_IMODE(now.st_mode))
                == (os.geteuid(), os.getegid(), 0o700),
                "ROOT_BINDING_INVALID",
            )
        finally:
            os.close(check)
    except Exception:
        raise ExecutorBridgeError("WRITE_UNKNOWN") from None
    finally:
        if fd is not None:
            os.close(fd)
        os.close(root)


def _native_transfer_stdio(channel):
    """Transfer the original stdio endpoints to the retained private channel."""
    import sys

    _bridge_require(
        type(channel) is ExecutorBridgeChannel
        and channel.role == "EXECUTOR"
        and channel.owner == os.getpid(),
        "PRIVATE_PIPE_REQUIRED",
    )
    for original, retained in ((0, channel.read_fd), (1, channel.write_fd)):
        info, held = os.fstat(original), os.fstat(retained)
        _bridge_require(
            (info.st_dev, info.st_ino) == (held.st_dev, held.st_ino), "PRIVATE_PIPE_REQUIRED"
        )
    sys.stdin.close()
    sys.stdout.close()
    # CPython's standard FileIO wrappers use closefd=False. Explicitly release
    # the OS originals so only the retained channel owns the pipe endpoints.
    for original in (0, 1):
        try:
            os.close(original)
        except OSError as error:
            import errno

            _bridge_require(error.errno == errno.EBADF, "PRIVATE_PIPE_REQUIRED")


def _native_terminal_refusal_bytes():
    # Existing public SDK payload, unchanged; no run/schema/authority fields.
    return canonical_json_bytes({"refused": True, "terminal": True, "reasons": ["run-finalized"]})


@contextmanager
def _native_refusal_custody_lock(root_fd, current, deadline):
    """Own an existing pinned regular shared lock within the original deadline."""
    from contextlib import ExitStack

    from . import ex10_prior_custody as prior

    stamp = prior._PriorFDReader._stamp
    name = ".s11-custody.lock"
    owner = os.geteuid(), os.getegid()
    current()
    with ExitStack() as stack:
        metadata = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd)
        stack.callback(os.close, metadata)
        pinned = os.fstat(metadata)
        _bridge_require(
            stat.S_ISREG(pinned.st_mode)
            and (pinned.st_uid, pinned.st_gid, stat.S_IMODE(pinned.st_mode), pinned.st_nlink)
            == (*owner, 0o600, 1),
            "RESULT_INVALID",
        )
        original = stamp(pinned)
        payload = None

        def verify_original():
            _bridge_require(
                time.monotonic() < deadline
                and stamp(os.fstat(metadata)) == original
                and stamp(os.stat(name, dir_fd=root_fd, follow_symlinks=False)) == original
                and (payload is None or stamp(os.fstat(payload)) == original),
                "RESULT_INVALID",
            )

        def locked_current():
            verify_original()
            current()
            verify_original()

        # Same approved Linux own-proc/FD boundary as the confined custody
        # reader. Open data only through the already pinned regular inode.
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        proc = os.open("/proc", flags)
        stack.callback(os.close, proc)
        info = os.fstat(proc)
        _bridge_require(info.st_uid == 0 and not info.st_mode & 0o022, "RESULT_INVALID")
        process = os.open(str(os.getpid()), flags, dir_fd=proc)
        stack.callback(os.close, process)
        descriptors = os.open("fd", flags, dir_fd=process)
        stack.callback(os.close, descriptors)
        for fd in (process, descriptors):
            info = os.fstat(fd)
            _bridge_require(
                (info.st_uid, info.st_gid) == owner and not info.st_mode & 0o022,
                "RESULT_INVALID",
            )
        locked_current()
        payload = os.open(
            str(metadata), os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=descriptors
        )
        stack.callback(os.close, payload)
        locked_current()
        for _ in range(STATUS_LOCK_ATTEMPTS):
            locked_current()
            try:
                fcntl.flock(payload, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                _bridge_require(remaining > 0, "EXCHANGE_EXPIRED")
                time.sleep(min(STATUS_LOCK_PAUSE, remaining))
        else:
            raise ExecutorBridgeError("RESULT_INVALID")
        try:
            locked_current()
            yield original, locked_current
            locked_current()
        finally:
            fcntl.flock(payload, fcntl.LOCK_UN)
        locked_current()
    # All close callbacks precede the last provider and original name check.
    current()
    _bridge_require(
        time.monotonic() < deadline
        and stamp(os.stat(name, dir_fd=root_fd, follow_symlinks=False)) == original,
        "RESULT_INVALID",
    )


def _native_refusal_path_identities(lease):
    result = []
    for path, device, inode in lease:
        info = os.lstat(path)
        _bridge_require(
            stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == (device, inode),
            "ROOT_BINDING_INVALID",
        )
        result.append((path, device, inode, info.st_mode, info.st_uid, info.st_gid))
    return tuple(result)


def _native_qualify_terminal_refusal(cfg, original_mounts):
    """Transport scientific2 only from the pinned private regular output inode."""
    from . import ex10_prior_custody as prior

    _native_command_arguments(cfg)
    options = cfg["command_options"]
    exchange_id = cfg["exchange_id"]
    _bridge_require(
        type(exchange_id) is str
        and str(uuid.UUID(exchange_id)) == exchange_id
        and cfg["action"] == "finalize"
        and options["declare_executor_gone"] is True
        and options["abandon_reason"] is None
        and _native_mount_observation() == original_mounts,
        "RESULT_INVALID",
    )
    expected = _native_terminal_refusal_bytes()
    root = Path("/s11-evidence")
    name = "executor-result." + exchange_id + ".json"
    fd, lease = profile_codec._walk(str(root), "directory")
    try:
        components = _native_refusal_path_identities(lease)
        root_info = os.fstat(fd)
        _bridge_require(
            (root_info.st_uid, root_info.st_gid, stat.S_IMODE(root_info.st_mode))
            == (os.geteuid(), os.getegid(), 0o700),
            "ROOT_BINDING_INVALID",
        )
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        _bridge_require(
            stat.S_ISREG(info.st_mode)
            and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink)
            == (os.geteuid(), os.getegid(), 0o600, 1)
            and info.st_size == len(expected),
            "RESULT_INVALID",
        )
        stamp = prior._PriorFDReader._stamp
        root_stamp, file_stamp = stamp(root_info), stamp(info)

        def current():
            _bridge_require(
                stamp(os.fstat(fd)) == root_stamp
                and stamp(os.stat(name, dir_fd=fd, follow_symlinks=False)) == file_stamp,
                "RESULT_INVALID",
            )

        # Existing confined reader pins O_PATH/type/inode before opening data
        # through its qualified own regular FD; never data-open a mutable name.
        reader = prior._PriorFDReader(fd, current)
        reader._fact((name,), info)
        raw, _ = reader.read(name)
        _bridge_require(raw == expected, "RESULT_INVALID")
        reader.verify(reader.freeze())
        current()
    finally:
        os.close(fd)
    _bridge_require(_native_mount_observation() == original_mounts, "ROOT_BINDING_INVALID")
    _bridge_require(
        _native_refusal_path_identities(lease) == components
        and stamp(os.lstat(root)) == root_stamp
        and stamp(os.lstat(root / name)) == file_stamp,
        "RESULT_INVALID",
    )


def _native_supervise(cfg):
    """PID1 retains the one-off and reaps its unique orphaned scientific worker."""
    _bridge_require(os.getpid() == 1, "PROCESS_ADMISSION_REQUIRED")
    original_mounts = _native_mount_observation()
    result = Path("/s11-evidence/executor-result." + cfg["exchange_id"] + ".json")
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            pid = 0
        if pid:
            code = os.waitstatus_to_exitcode(status)
            if code == 2:
                try:
                    _native_qualify_terminal_refusal(cfg, original_mounts)
                except Exception:
                    return 3
                return 2
            if code != 0:
                return 3
            if result.exists():
                return 0
        time.sleep(0.05)


def _native_share_relay_deadline(outer, inner):
    _bridge_require(
        type(outer) is ExecutorBridgeChannel and type(inner) is ExecutorBridgeChannel,
        "PROTOCOL_INVALID",
    )
    outer_reader = outer._exchange_context_reader
    inner_reader = inner._exchange_context_reader
    inner_retainer = inner._retain_exchange_deadline
    outer_before, inner_before = outer_reader(), inner_reader()
    _bridge_require(
        type(outer) is ExecutorBridgeChannel
        and type(inner) is ExecutorBridgeChannel
        and outer.role == "EXECUTOR"
        and inner.role == "HOST"
        and outer.exchange_id == inner.exchange_id
        and outer.exchange == inner.exchange
        and outer.budget == inner.budget
        and outer.position == inner.position == 0
        and outer_before[-1] is None
        and inner_before[-1] is None,
        "PROTOCOL_INVALID",
    )
    outer._activate()
    original_outer = outer_reader()
    _bridge_require(original_outer[:-1] == outer_before[:-1], "CONTEXT_BINDING_INVALID")
    inner._check()
    _bridge_require(
        outer_reader() == original_outer and inner_reader() == inner_before,
        "CONTEXT_BINDING_INVALID",
    )
    # Transfer the exact first-leg absolute deadline. No second activation,
    # recomputation, deadline renewal or fresh baseline after callbacks.
    inner_retainer(original_outer[-1])
    inner._check()
    _bridge_require(
        outer_reader() == original_outer
        and inner_reader() == inner_before[:-1] + (original_outer[-1],),
        "CONTEXT_BINDING_INVALID",
    )


def run_native_bridge():
    """Fixed W06 Docker exec bridge: one worker, bounded exact relay, then exit.

    The worker owns scientific work and remains in this one-off under PID1.
    The short relay owns Docker stdout only until the closed control exchange;
    no scientific bytes, result JSON, key or authority DTO use its stdout.
    """
    import subprocess

    cfg, initial = _native_initial_bootstrap()
    arguments = _native_command_arguments(cfg)
    if os.getpid() == 1:
        return _native_supervise(cfg)
    admission = ExecutorNativeBootstrap()
    _bridge_require(admission.initial == initial, "PROCESS_IDENTITY_CHANGED")
    process = subprocess.Popen(
        [_NATIVE_PYTHON, "-B", _NATIVE_ENTRY, "s11_ex10", *arguments],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        shell=False,
        close_fds=True,
        start_new_session=True,
        bufsize=0,
    )
    exchange = "START" if cfg["action"] == "start" else "SINGLE_RPC"
    inner = outer = None
    try:
        # Both workers and relay await the same preparation phase. The 120s
        # frame deadline starts only after PF plus complete process/B5 publication.
        _native_wait_prepared(
            cfg=cfg,
            deadline=admission.preparation_deadline,
            guard=admission.check_os,
            alive=lambda: process.poll() is None,
        )
        inner = ExecutorBridgeChannel(
            read_fd=process.stdout.fileno(),
            write_fd=process.stdin.fileno(),
            role="HOST",
            exchange=exchange,
            exchange_id=cfg["exchange_id"],
            process_guard=admission.check_os,
        )
        process.stdin.close()
        process.stdout.close()
        outer = ExecutorBridgeChannel(
            read_fd=0,
            write_fd=1,
            exchange=exchange,
            exchange_id=cfg["exchange_id"],
            process_guard=admission.check_os,
        )
        _native_transfer_stdio(outer)
        _native_share_relay_deadline(outer, inner)
        _bridge_require(time.monotonic() < admission.preparation_deadline, "START_NOT_PREPARED")
        for kind, direction in outer.steps:
            if direction == "HOST_TO_EXECUTOR":
                message = outer.receive()
                inner.send(message.canonical)
            else:
                message = inner.receive()
                if kind == "RPC_REQUEST" and message.to_dict()["body"]["operation"] in {
                    "reserve-start",
                    "commit-open",
                    "record-uncertain",
                }:
                    outer.mark_dispatched()
                    inner.mark_dispatched()
                outer.send(message.canonical)
        # Closing Docker stdin/output and child stdin/output are distinct. Neither
        # original Popen endpoint nor this relay inherits the worker's science FDs.
        outer.finish()
        inner.finish()
        return 0  # Do not wait for the worker's scientific action here.
    finally:
        for channel in (outer, inner):
            if channel is not None:
                channel.close()
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()
        # No respawn, terminate, rollback or success claim on unknown worker state.


def _require_operational_context(mode, context, *, action):
    if mode == OFFICIAL_MODE:
        if type(context) is not ExecutorOperationalContext:
            raise Ex10Refusal(["operational-context-required"], terminal=True)
        if context.action != action:
            raise Ex10Refusal(["operational-context-action"], terminal=True)
        context.check()
    elif context is not None:
        raise Ex10Refusal(["local-test-official-context"], terminal=True)


def _step_context(store, *, run_id, git_sha, action, context):
    # Bind the safe root before even the read-only state scan.
    if type(context) is ExecutorOperationalContext:
        root_fd, _ = context._root(store)
        os.close(root_fd)
    # Read-only admission before load_state, dataset/DB/probes or any normalization.
    try:
        state = load_existing_state_readonly(store, run_id)
    except Ex10Refusal as exc:
        if action == "reconcile" and exc.reasons == (_READ_RACE,):
            raise Ex10Refusal([_RECONCILE_RACE], terminal=False) from None
        raise
    if state is None:
        raise Ex10Refusal(["run-not-found"], terminal=True)
    mode = _evidence_mode(state.initial_manifest or {})
    if mode not in EXPERIMENT_IDS and action != "reconcile":
        raise Ex10Refusal(["initial-manifest"], terminal=True)
    if (
        action == "drain"
        and type(context) is ExecutorOperationalContext
        and context.action == "execute"
        and context.step_authorized
    ):
        context.check(run_id=run_id, phase=str(phase_of(state)))
    else:
        _require_operational_context(mode, context, action=action)
        if context is not None:
            context.authorize_step(
                store, action=action, run_id=run_id, git_sha=git_sha, phase=str(phase_of(state))
            )
    return state


def verified_prior_ex10_runs(store, operational_context, *, local=None):
    """Complete verified common view plus anomaly detection on only this safe root."""
    _bridge_require(
        type(operational_context) is ExecutorOperationalContext
        and operational_context.common is not None,
        "COMMON_HISTORY_REQUIRED",
    )
    common = operational_context.common
    local = _prior_ex10_runs_under_caller_lock(store) if local is None else local
    common_by_id = {run.run_id: run for run in common}
    _bridge_require(len(common_by_id) == len(common), "COMMON_HISTORY_DUPLICATE")
    # Common B3 rows are exclusively OFFICIAL. Closed LOCAL_TEST evidence is
    # a verified diagnostic on this root, never a substitute for common facts.
    reasons, terminal = binding_refusal(local, evidence_mode=LOCAL_TEST_MODE, acknowledged=())
    if reasons:
        raise Ex10Refusal(reasons, terminal=terminal)
    diagnostics = []
    for run in local:
        if run.evidence_mode == OFFICIAL_MODE:
            _bridge_require(
                run.run_id in common_by_id and run == common_by_id[run.run_id],
                "LOCAL_COMMON_HISTORY_MISMATCH",
            )
        else:
            _bridge_require(
                run.evidence_mode == LOCAL_TEST_MODE and run.run_id not in common_by_id,
                "LOCAL_COMMON_HISTORY_MISMATCH",
            )
            diagnostics.append(run)
    _bridge_require(
        _fixed_provider(
            operational_context.prior_verifier,
            context=operational_context,
            common=common,
            local=tuple(local),
            reserve_response=None,
        )
        is None,
        "COMMON_HISTORY_INVALID",
    )
    return [*common, *diagnostics]


def _context_checkpoint(context, run_id, phase):
    if context is not None:
        context.check(run_id=run_id, phase=phase)


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
    operational_context: ExecutorOperationalContext | None = None,
) -> dict[str, Any]:
    """Preflight en proceso, precondiciones y la regla del primer run; luego el run_id.

    Nada se escribe si algo falla. El preflight corre antes de abrir la evidencia: su guarda
    protege la raíz de evidencia y lo prohíbe escribir.
    """
    _require_operational_context(evidence_mode, operational_context, action="start")
    if operational_context is not None:
        root_fd, _ = operational_context._root(store)
        os.close(root_fd)
    probes = probes or ExecutorProbes()
    if evidence_mode not in EXPERIMENT_IDS:
        raise Ex10Refusal(["evidence-mode"], terminal=True)
    if not _SHA1.fullmatch(git_sha) or not _NONCE.fullmatch(nonce):
        raise Ex10Refusal(["git-sha-or-nonce-format"], terminal=True)
    if operational_context is not None:
        if not operational_context.prepared:
            operational_context.prepare_start(
                store, probes=probes, git_sha=git_sha, source_root=source_root, nonce=nonce
            )
        _bridge_require(
            operational_context.proposal["execution_nonce"] == nonce
            and operational_context.proposal["git_sha"] == git_sha,
            "RUN_ID_MISMATCH",
        )
    dataset = probes.dataset()
    plan = ex10_load.build_plan(dataset)
    problems = list(probes.provider())
    problems.extend(
        f"protocol:{reason}"
        for reason in ex10_load.verify_protocol_identity(ex10_load.protocol_identity())
    )
    if plan.sha256 != ex10_load.PLAN_SHA256:
        problems.append("plan-sha256")
    if operational_context is None:
        report = probes.preflight(ex10_preflight.PreflightOptions(git_sha, source_root))
        now = probes.now()
    else:
        report = json.loads(operational_context.preflight)
        now = wire._time(operational_context.proposal["started_at"])
    problems.extend(preflight_problems(report, git_sha=git_sha, now=now))
    # Se comprueba antes de escribir nada: un rechazo aquí no deja un run a medio crear.
    if isinstance(report, Mapping) and ex10_load.sensitive_findings(report, dataset=dataset):
        problems.append("preflight:sensitive-content")
    problems.extend(probes.sources(source_root))
    if problems:
        raise Ex10Refusal(problems, terminal=False)
    preflight_sha = sha256_bytes(canonical_json_bytes(report))
    if operational_context is not None:
        operational_context.reserve(store)
    with _checkpoint(store):
        # Ya bajo el lock exclusivo: la exploración de sólo lectura no pide otro lock.
        prior = _prior_ex10_runs_under_caller_lock(store)
        if operational_context is not None:
            prior = verified_prior_ex10_runs(store, operational_context, local=prior)
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
        # Cualquier rastro del mismo run_id, sin crear ni normalizar su directorio.
        if os.path.lexists(store.root / "runs" / run_id):
            raise Ex10Refusal(["run-id-reused"], terminal=True)
        if operational_context is not None:
            _bridge_require(run_id == operational_context.proposal["run_id"], "RUN_ID_MISMATCH")
            manifest = replace(manifest, started_at=operational_context.proposal["started_at"])
            operational_context.consume_delivery(store)
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
        pf_record = _persist(
            store, run_id, ART_PREFLIGHT, report, source="s11_ex10.start", dataset=dataset
        )
        plan_record = _persist(
            store, run_id, ART_PLAN, plan.to_json(), source="s11_ex10.start", dataset=dataset
        )
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
    if operational_context is not None:
        operational_context.commit_open(store, (initial, record, pf_record, plan_record))
        operational_context.scientific_finished = True
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
    operational_context: ExecutorOperationalContext | None = None,
) -> dict[str, Any]:
    """Emite los 50 intentos y drena, con un checkpoint custodiado entre cada etapa.

    Antes del marcador de emisión nada es irreversible y un rechazo no gasta el run. Tras él
    no hay vuelta atrás: la emisión no se repite, ni entera ni en parte.
    """
    _step_context(
        store, run_id=run_id, git_sha=git_sha, action="execute", context=operational_context
    )
    probes = probes or ExecutorProbes()
    dataset = probes.dataset()
    plan = ex10_load.build_plan(dataset)
    try:
        _checkpoint_started(
            store,
            run_id=run_id,
            git_sha=git_sha,
            probes=probes,
            dataset=dataset,
            step="execute",
            source="s11_ex10.execute",
            operational_context=operational_context,
        )
    except _InterruptedAtStarted:
        # Diferida hasta custodiar el marcador: no hubo barrera, es una detención antes de t0.
        return _stop_pre_t0(
            store,
            run_id,
            git_sha,
            ["operator-interrupted"],
            probes,
            dataset,
            operational_context=operational_context,
        )

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
            _arm_emission(
                store, run_id, git_sha, probes, dataset, operational_context=operational_context
            )
        except _PreT0Stop as stop:
            return _stop_pre_t0(
                store,
                run_id,
                git_sha,
                [stop.code],
                probes,
                dataset,
                operational_context=operational_context,
            )
        except KeyboardInterrupt:
            return _stop_pre_t0(
                store,
                run_id,
                git_sha,
                ["operator-interrupted"],
                probes,
                dataset,
                operational_context=operational_context,
            )
        except Ex10Refusal:
            # El run se cerró desde fuera (un abandono): no hay nada que registrar ni emitir.
            raise
        except ExecutorBridgeError:
            raise
        except Exception as exc:  # noqa: BLE001 - sólo el tipo, nunca el mensaje
            return _stop_pre_t0(
                store,
                run_id,
                git_sha,
                [f"pre-barrier:{type(exc).__name__}"],
                probes,
                dataset,
                operational_context=operational_context,
            )
        _context_checkpoint(operational_context, run_id, "BARRIER")
        run = generator.run()
        # El generador guarda su copia de las peticiones (con el JWT): se suelta ya.
        generator = transport = None
    finally:
        prepared.clear()
    raw = ex10_load.raw_results_payload(plan, run)
    with _checkpoint(store):
        _context_checkpoint(operational_context, run_id, "RAW")
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
    drain = run_drain_step(
        store,
        run_id=run_id,
        git_sha=git_sha,
        probes=probes,
        run=run,
        plan=plan,
        operational_context=operational_context,
    )
    if operational_context is not None:
        operational_context.scientific_finished = True
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


class _InterruptedAtStarted(Exception):
    """Una interrupción diferida que se entregó al cerrar el checkpoint de STARTED."""


def _checkpoint_started(
    store: EvidenceStore,
    *,
    run_id: str,
    git_sha: str,
    probes: ExecutorProbes,
    dataset: LoadDataset,
    step: str,
    source: str,
    require_mode: str | None = None,
    operational_context: ExecutorOperationalContext | None = None,
) -> None:
    """PREPARED → STARTED: las comprobaciones previas a la emisión y el marcador custodiado.

    La única ruta que crea el marcador de emisión. La usan `execute` (que después prepara
    tokens y cuerpos, arma y emite) y el checkpoint LOCAL_TEST `rehearsal-started` (que se
    detiene aquí). Con `require_mode`, el modo del manifest se exige fuera y dentro del lock.
    Una interrupción durante el checkpoint se entrega al cerrarlo, con el marcador ya
    custodiado, como `_InterruptedAtStarted`.
    """
    _context_checkpoint(operational_context, run_id, "PREPARED")
    state = _open_state(store, run_id, git_sha)
    _require_mode(state, require_mode, step)
    _require_phase(state, step)
    problems, config_before, ready = _pre_emission_problems(state, probes, dataset)
    if problems:
        raise Ex10Refusal(problems, terminal=False)
    boot_id = probes.boot_id()
    try:
        with _checkpoint(store):
            _context_checkpoint(operational_context, run_id, "STARTED")
            state = _open_state(store, run_id, git_sha)
            _require_mode(state, require_mode, step)
            _require_phase(state, step)
            marker = _event(
                "s11c3-ex10-emission-v1",
                run_id=run_id,
                recorded_at=probes.now(),
                boot_id=boot_id,
                config_before=config_before.to_json(),
                ready_state={"state": ready.get("state"), "overall": ready.get("overall")},
            )
            _persist(store, run_id, ART_EMISSION, marker, source=source, dataset=dataset)
    except KeyboardInterrupt:
        raise _InterruptedAtStarted from None


def _require_mode(state: RunState, mode: str | None, step: str) -> None:
    if mode is not None and _evidence_mode(state.initial_manifest) != mode:
        raise Ex10Refusal([f"evidence-mode:{step}-requires-{mode}"], terminal=True)


def rehearsal_started_run(
    store: EvidenceStore,
    *,
    run_id: str,
    git_sha: str,
    probes: ExecutorProbes | None = None,
) -> dict[str, Any]:
    """LOCAL_TEST: PREPARED → STARTED y se detiene. Un ensayo del lifecycle, nunca de carga.

    El modo sale del manifest custodiado: un run OFFICIAL se rechaza de forma definitiva y
    sin escribir. Además de las comprobaciones de `execute`, el preflight custodiado en START
    debe seguir siendo un PASS completo, reciente y del mismo HEAD, y la custodia del run
    debe verificar.
    Crea el marcador de emisión por la misma ruta que `execute` y vuelve: no emite ni
    verifica tokens, no prepara cuerpos, no crea transporte ni generador, no arma la emisión
    y no abre la barrera. Desde STARTED sólo queda el abandono antes de t0.
    """
    probes = probes or ExecutorProbes()
    dataset = probes.dataset()
    state = _open_state(store, run_id, git_sha)
    _require_mode(state, LOCAL_TEST_MODE, "rehearsal-started")
    _require_phase(state, "rehearsal-started")
    problems = preflight_problems(state.payload(ART_PREFLIGHT), git_sha=git_sha, now=probes.now())
    problems.extend(f"custody:{error}" for error in store.verify(run_id))
    if problems:
        raise Ex10Refusal(problems, terminal=False)
    try:
        _checkpoint_started(
            store,
            run_id=run_id,
            git_sha=git_sha,
            probes=probes,
            dataset=dataset,
            step="rehearsal-started",
            source="s11_ex10.rehearsal-started",
            require_mode=LOCAL_TEST_MODE,
        )
    except _InterruptedAtStarted:
        # El marcador ya está custodiado: el run queda en STARTED, que es el objetivo.
        raise KeyboardInterrupt from None
    return {
        "run_id": run_id,
        "evidence_mode": LOCAL_TEST_MODE,
        "phase": str(Phase.STARTED),
        "emission_armed": False,
        "t0_published": False,
        "tokens_issued": 0,
        "requests_prepared": 0,
        "requests_sent": 0,
    }


def _arm_emission(
    store: EvidenceStore,
    run_id: str,
    git_sha: str,
    probes: ExecutorProbes,
    dataset: LoadDataset,
    *,
    operational_context: ExecutorOperationalContext | None = None,
) -> None:
    """El último checkpoint antes de la barrera: separa «nada enviado» de «quizá emitido».

    Se re-comprueba la fase bajo el lock: un run abandonado mientras se preparaban los tokens
    ya no se arma y, por tanto, no emite.
    """
    with _checkpoint(store):
        _context_checkpoint(operational_context, run_id, "ARMED")
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
    *,
    operational_context: ExecutorOperationalContext | None = None,
) -> dict[str, Any]:
    # Sólo se llama antes de `generator.run()`: incluso armado, nada se envió todavía.
    with _checkpoint(store):
        _context_checkpoint(operational_context, run_id, "ABORT_PRE_T0")
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
    if operational_context is not None:
        operational_context.scientific_finished = True
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
    operational_context: ExecutorOperationalContext | None = None,
) -> dict[str, Any]:
    """Drain y `CFG_ASYNC` posterior; se custodia `ex10-drain`. Sin lock mientras sondea."""
    _step_context(
        store, run_id=run_id, git_sha=git_sha, action="drain", context=operational_context
    )
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
        _context_checkpoint(operational_context, run_id, "DRAINING")
        state = _open_state(store, run_id, git_sha)
        if phase_of(state) is not Phase.DRAINING:
            raise Ex10ExecutorError("El run cambió de fase durante el drain.")
        _persist(store, run_id, ART_DRAIN, payload, source="s11_ex10.drain", dataset=dataset)
    if operational_context is not None and operational_context.action == "drain":
        operational_context.scientific_finished = True
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
    # La semántica del broker la fija la identidad custodiada del run, no el HEAD (G0-A2).
    g0_a2 = follows_g0_a2(state.initial_manifest)
    dynamic: int | None = None
    residue: ex10_reconcile.PostRunBroker | None = None
    try:
        if g0_a2:
            residue = probes.post_run_broker(plan)
        else:
            dynamic = probes.broker_dynamic()
    except Exception as exc:  # noqa: BLE001
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
        post_run_broker=residue,
        g0_a2=g0_a2,
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


_RECONCILE_RACE = "reconcile-read-race"


def _reconcile_fingerprint(run_root: Path) -> tuple:
    """Lo que lee la reconciliación: índice, firma, sello y los streams del run."""
    facts = list(_index_fingerprint(run_root))
    for name in STREAMS:
        path = run_root / "logs" / SCENARIO_ID / f"{name}.jsonl"
        try:
            info = os.lstat(path)
        except (FileNotFoundError, NotADirectoryError):
            facts.append(None)
            continue
        facts.append((info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    return tuple(facts)


def reconcile_run(
    store: EvidenceStore,
    *,
    run_id: str,
    git_sha: str,
    probes: ExecutorProbes | None = None,
    operational_context: ExecutorOperationalContext | None = None,
) -> dict[str, Any]:
    """Sólo lectura y repetible: la decisión que resultaría, sin custodiarla.

    Estrictamente de sólo lectura: nunca `load_state`, `initialize_run`, `records()` ni
    `verify()` (que normalizan permisos), ni ninguna escritura. Lee el run bajo el lock de
    custodia compartido (breve, sólo la lectura del estado) y verifica la custodia con
    `verify_readonly`. La base se lee en una transacción `READ ONLY` y el broker sin tocarlo.
    Si el índice, su firma, el sello o los streams cambian mientras se reconcilia, el
    resultado se descarta (`reconcile-read-race`) en vez de mezclar dos estados.
    """
    _step_context(
        store, run_id=run_id, git_sha=git_sha, action="reconcile", context=operational_context
    )
    probes = probes or ExecutorProbes()
    dataset = probes.dataset()
    run_root = _existing_run_root(store, run_id)
    if run_root is None:
        raise Ex10Refusal(["run-not-found"], terminal=True)
    before = _reconcile_fingerprint(run_root)
    try:
        state = load_existing_state_readonly(store, run_id)
    except Ex10Refusal as exc:
        if exc.reasons == (_READ_RACE,):
            raise Ex10Refusal([_RECONCILE_RACE], terminal=False) from None
        raise
    if state is None:
        raise Ex10Refusal([_RECONCILE_RACE], terminal=False)
    _require_open_run(state)
    if state.initial_manifest.get("git_sha") != git_sha:
        raise Ex10Refusal(["git-sha-differs-from-start"], terminal=True)
    _require_phase(state, "reconcile")
    if not state.has(ART_RAW):
        raise Ex10Refusal(["raw-missing"], terminal=False)
    custody_errors = verify_readonly(store, state)
    assessment = assess(state, probes=probes, dataset=dataset, custody_errors=custody_errors)
    if _reconcile_fingerprint(run_root) != before:
        raise Ex10Refusal([_RECONCILE_RACE], terminal=False)
    result = assessment.evaluation.result
    if operational_context is not None:
        operational_context.scientific_finished = True
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


def _finalize_recovery_plan_under_caller_lock(store: EvidenceStore, run_id: str):
    """Prevalidate the frozen recovery contract before FINAL can normalize custody.

    Use the same HMAC, base64 and final-seal primitives and fields consumed by
    EvidenceStore recovery. This helper performs no recovery or initialization.
    The caller holds the existing custody lock.
    """
    store._identifier(run_id, "run_id")
    parent = store.root / "finalization-transactions"
    journal = parent / f"{run_id}.json"
    parent_fd = journal_fd = None
    try:
        try:
            parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            # A broken parent link is invalid custody, not journal absence.
            if parent.is_symlink():
                raise EvidencePolicyError("El journal usa un directorio enlazado.") from None
            try:
                os.lstat(parent)
            except FileNotFoundError:
                return None
            raise EvidencePolicyError(
                "El directorio del journal cambió durante la lectura."
            ) from None
        try:
            journal_fd = os.open(
                journal.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            parent_info = os.fstat(parent_fd)
            current_parent = os.lstat(parent)
            if (parent_info.st_dev, parent_info.st_ino) != (
                current_parent.st_dev,
                current_parent.st_ino,
            ):
                raise EvidencePolicyError(
                    "El directorio del journal cambió de identidad."
                ) from None
            try:
                os.stat(journal.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return None
            raise EvidencePolicyError(
                "El journal apareció durante la lectura de ausencia."
            ) from None
        info = os.fstat(journal_fd)
        if not stat.S_ISREG(info.st_mode):
            raise EvidencePolicyError("El journal no es un archivo regular.")
        with os.fdopen(journal_fd, "r", encoding="utf-8") as source:
            journal_fd = None
            envelope = json.load(source)
        payload = envelope["payload"]
        signature = envelope["signature"]
        if not isinstance(payload, dict) or not isinstance(signature, str):
            raise EvidencePolicyError("El journal de finalización no es un objeto firmado.")
        validate_custody_finalization_signature(
            canonical_json_bytes(payload),
            signature,
            custody_hmac_key=store.custody_hmac_key,
        )
        state = payload["state"]
        if payload["run_id"] != run_id:
            raise EvidencePolicyError("El journal pertenece a otro run.")
        if payload["seal_relative_path"] != _FINALIZED_MARKER:
            raise EvidencePolicyError("El journal contiene un sello de cierre desconocido.")
        run_root = store.root / "runs" / run_id
        if run_root.parent.is_symlink() or run_root.is_symlink():
            raise EvidencePolicyError("El journal usa una raíz de run enlazada.")
        # initialize_run and rollback touch these paths even when the final
        # target lives in a different ArtifactKind directory.
        directories = [store.root, run_root.parent, run_root]
        directories.extend(run_root / kind.value for kind in ArtifactKind)
        for directory in directories:
            try:
                directory_info = os.lstat(directory)
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(directory_info.st_mode):
                raise EvidencePolicyError("La recuperación usa un directorio inválido.")
        index = run_root / ArtifactKind.METADATA.value / "artifacts.jsonl"
        for destination in (
            run_root / ".s11-evidence-root",
            index,
            index.with_suffix(index.suffix + ".hmac"),
            run_root / "SHA256SUMS",
        ):
            try:
                destination_info = os.lstat(destination)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(destination_info.st_mode):
                raise EvidencePolicyError("La recuperación usa un destino inválido.")
        plan = {"journal": journal, "state": state, "run_root": run_root}
        if state == "PREPARED":
            target = (run_root / payload["target_relative_path"]).resolve()
            if run_root not in target.parents:
                raise EvidencePolicyError("El journal contiene una ruta insegura.")
            seal = run_root / _FINALIZED_MARKER
            if seal.is_symlink() or (seal.exists() and not seal.is_file()):
                raise EvidencePolicyError("El sello de cierre no es un archivo regular.")
            plan["target"] = target
            plan["original_index"] = b64decode(payload["original_index_b64"])
            for key in ("original_index_signature", "original_checksums"):
                encoded = payload[key + "_b64"]
                plan[key] = None if encoded is None else b64decode(encoded)
        elif state == "COMMITTED":
            index = run_root / ArtifactKind.METADATA.value / "artifacts.jsonl"
            if index.parent.is_symlink() or index.is_symlink():
                raise EvidencePolicyError("El journal usa un índice enlazado.")
            records = (
                [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
                if index.exists()
                else []
            )
            validate_finalized_state(run_root, records, custody_hmac_key=store.custody_hmac_key)
        else:
            raise EvidencePolicyError("El journal tiene un estado desconocido.")
        parent_info = os.fstat(parent_fd)
        current_parent = os.lstat(parent)
        current_journal = os.lstat(journal)
        if (parent_info.st_dev, parent_info.st_ino) != (
            current_parent.st_dev,
            current_parent.st_ino,
        ) or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
            current_journal.st_dev,
            current_journal.st_ino,
            current_journal.st_size,
            current_journal.st_mtime_ns,
            current_journal.st_ctime_ns,
        ):
            raise EvidencePolicyError("El journal cambió durante su lectura.")
        return plan
    except (CustodyPolicyError, KeyError, OSError, TypeError, ValueError) as exc:
        raise EvidencePolicyError("El journal de finalización no es recuperable.") from exc
    finally:
        if journal_fd is not None:
            os.close(journal_fd)
        if parent_fd is not None:
            os.close(parent_fd)


@contextmanager
def _finalize_existing_checkpoint(store: EvidenceStore):
    """Hold the existing custody descriptor before deciding whether FINAL may mutate.

    Preserve frozen EvidenceStore bytes. Its nested mutators reuse the held fd
    only after FINAL admission. Rejection here does not create, chmod or repair.
    The caller consumes the normalization callback before leaving this context.
    """
    with _deferred_interrupts(), _APPEND_LOCK:
        root_fd = lock_fd = None
        owns_lock = False
        acquired = False
        try:
            root_fd = os.open(
                store.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            )
            root_info = os.fstat(root_fd)
            if store._mutation_depth:
                lock_fd = store._mutation_lock_file.fileno()
            else:
                lock_fd = os.open(
                    ".s11-custody.lock",
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                    dir_fd=root_fd,
                )
                owns_lock = True
            lock_info = os.fstat(lock_fd)
            if not stat.S_ISREG(lock_info.st_mode):
                raise EvidencePolicyError("El lock de custodia no es un archivo regular.")
            if owns_lock:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                acquired = True
            current_root = os.lstat(store.root)
            current_lock = os.stat(".s11-custody.lock", dir_fd=root_fd, follow_symlinks=False)
            if (root_info.st_dev, root_info.st_ino) != (
                current_root.st_dev,
                current_root.st_ino,
            ) or (lock_info.st_dev, lock_info.st_ino) != (current_lock.st_dev, current_lock.st_ino):
                raise EvidencePolicyError("El lock de custodia cambió de identidad.")
            if owns_lock:
                store._mutation_lock_file = os.fdopen(lock_fd, "rb")
                lock_fd = None
            held_file = store._mutation_lock_file
            held_fd = held_file.fileno()
            store._mutation_depth += 1
            try:

                def normalize_admitted():
                    # Admission guards may perform IO. The named root and lock
                    # must still select our original exclusive descriptor.
                    named_root = os.lstat(store.root)
                    retained_root = os.fstat(root_fd)
                    retained_lock = os.fstat(held_fd)
                    named_lock = os.stat(".s11-custody.lock", dir_fd=root_fd, follow_symlinks=False)
                    if (
                        store._mutation_lock_file is not held_file
                        or held_file.closed
                        or (root_info.st_dev, root_info.st_ino)
                        != (named_root.st_dev, named_root.st_ino)
                        or (root_info.st_dev, root_info.st_ino)
                        != (retained_root.st_dev, retained_root.st_ino)
                        or (lock_info.st_dev, lock_info.st_ino)
                        != (named_lock.st_dev, named_lock.st_ino)
                        or (lock_info.st_dev, lock_info.st_ino)
                        != (retained_lock.st_dev, retained_lock.st_ino)
                    ):
                        raise EvidencePolicyError(
                            "La admisión perdió el lock de custodia original."
                        )
                    os.fchmod(root_fd, 0o700)
                    os.fchmod(held_fd, 0o600)

                yield normalize_admitted
            finally:
                store._mutation_depth -= 1
                if owns_lock:
                    try:
                        if not held_file.closed:
                            fcntl.flock(held_file.fileno(), fcntl.LOCK_UN)
                    finally:
                        held_file.close()
                        store._mutation_lock_file = None
        finally:
            if owns_lock and lock_fd is not None:
                try:
                    if acquired:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)
            if root_fd is not None:
                os.close(root_fd)


def _finalize_admission_under_caller_lock(store: EvidenceStore, run_id: str):
    state = _read_existing_state_under_caller_lock(store, run_id)
    if state is None:
        raise Ex10Refusal(["run-not-found"], terminal=True)
    recovery_plan = _finalize_recovery_plan_under_caller_lock(store, run_id)
    if state.finalized and recovery_plan is None:
        _require_open_run(state)


def finalize_run(
    store: EvidenceStore,
    *,
    run_id: str,
    git_sha: str,
    abandon_reason: str | None = None,
    declare_executor_gone: bool = False,
    probes: ExecutorProbes | None = None,
    operational_context: ExecutorOperationalContext | None = None,
) -> dict[str, Any]:
    """Decide una única vez, custodia la decisión y sella el run con el manifest final.

    Todo ocurre bajo un único lock de custodia existente: recuperación del journal, lectura de los
    streams (congelados mientras dure el lock), su registro, la decisión y el sello. Una
    finalización interrumpida se retoma y sólo reutiliza una decisión idéntica.
    """
    _step_context(
        store, run_id=run_id, git_sha=git_sha, action="finalize", context=operational_context
    )
    # SINGLE5 has completed before this authentic scientific refusal. The
    # coherent shared view keeps an already-sealed negative free of probes.
    with _shared_custody_lock(store):
        _finalize_admission_under_caller_lock(store, run_id)
    probes = probes or ExecutorProbes()
    dataset = probes.dataset()
    plan = ex10_load.build_plan(dataset)
    with _finalize_existing_checkpoint(store) as normalize_admitted:
        _finalize_admission_under_caller_lock(store, run_id)
        _context_checkpoint(operational_context, run_id, "FINALIZING")
        # The guard may perform provider IO. Recheck custody after that IO,
        # before normalizing or invoking the frozen recovery implementation.
        _finalize_admission_under_caller_lock(store, run_id)
        normalize_admitted()
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
    result = {
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
    if operational_context is not None:
        operational_context.prepare_sealed_terminal(store, run_id=run_id, result=result)
        operational_context.scientific_finished = True
    return result


def run_status(store: EvidenceStore, run_id: str) -> dict[str, Any]:
    """Resumen estrictamente de sólo lectura: fase y artefactos, sin métricas.

    No crea, repara ni normaliza nada (ni `mkdir` ni `chmod`): ni el run, ni `runs/`, ni la raíz,
    ni el lock. Lee bajo el lock de custodia compartido y verifica la custodia con
    `verify_readonly`.
    """
    with _shared_custody_lock(store):
        run_root = _existing_run_root(store, run_id)
        if run_root is None:
            return {"run_id": run_id, "exists": False}
        before = _index_fingerprint(run_root)
        state = _read_existing_state_under_caller_lock(store, run_id)
        if state is None:
            raise Ex10Refusal([_READ_RACE], terminal=False)
        decision = state.payload(ART_DECISION)
        status = {
            "run_id": run_id,
            "exists": True,
            "scenario_id": (state.initial_manifest or {}).get("scenario_id"),
            "phase": str(phase_of(state)) if state.initial_manifest else None,
            "finalized": state.finalized,
            "artifacts": sorted(record["artifact_type"] for record in state.records),
            "decision": decision.get("decision")
            if isinstance(decision, Mapping) and state.finalized
            else None,
            "custody_errors": verify_readonly(store, state),
        }
        # Sin lock (raíz que nunca mutó), la verificación y el sello también deben corresponder
        # al índice leído.
        if _index_fingerprint(run_root) != before:
            raise Ex10Refusal([_READ_RACE], terminal=False)
    return status
