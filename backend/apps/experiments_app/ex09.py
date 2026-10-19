"""Ejecutor de EX-09 (CA-4 contra el proveedor real) bajo la sub-etapa S11-C3.

Aplica la decisión metodológica S11-C3 del 23/09/2026 (`docs/PROGRESO_SPRINTS.md`,
D1–D4) sobre el protocolo prerregistrado S8-E2-R2, que sigue siendo la única fuente de
modelo, dataset, prompt, timeout, pacing, reintentos, scoring y umbral
(`apps.ai_client_app.evaluation.recovery.R2`). Este módulo no redefine ninguno de esos
valores: los lee de `R2`, verifica los bytes reales y los fija en la identidad del run.

EX-09 es **una sola corrida** con un único `run_id` y dos lotes: `lot-01` (casos 1–14)
y `lot-02` (casos 15–28), cada uno en su propio proceso efímero, con su propia clave
temporal y su propia ventana RPD. Entre lotes el run queda abierto —sin manifest final,
sin `SHA256SUMS` y sin sello— y sólo se finaliza al terminar el segundo.

Tres propiedades se hacen estructurales en lugar de dejarlas a la disciplina de quien
ejecute:

- **Ningún lote se puntúa por separado.** El resumen de un lote no contiene CA-4 y la
  combinación sólo existe cuando los dos lotes son válidos.
- **Nada se reintenta.** Un lote con cualquier artefacto de ejecución ya no se puede
  volver a lanzar; el run se finaliza como `NOT_DEMONSTRATED`.
- **La decisión se deriva de la evidencia custodiada.** `finalize_run` vuelve a leer y
  verificar todo lo persistido y sólo entonces decide `PASS`, `FAIL` o
  `NOT_DEMONSTRATED`. No existe ningún otro estado.

La clave del proveedor llega únicamente por la variable de entorno
`S11_EX09_PROVIDER_KEY` del proceso del lote. Nunca se persiste: sólo su huella
(los 16 primeros hexadecimales de su SHA-256, calculados en memoria).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from apps.ai_client_app.evaluation.dataset import EvaluationDataset
from apps.ai_client_app.evaluation.execution import (
    GEMINI_ADAPTER_NAME,
    load_verified_dataset,
    run_recovery_batch,
    verify_prompt,
)
from apps.ai_client_app.evaluation.recovery import (
    BATCH_SIZE,
    R2,
    RESULT_FAIL,
    RESULT_NOT_DEMONSTRATED,
    RESULT_PASS,
    TOTAL_CASES,
    BatchEvaluation,
    RecoveryProtocolError,
    combine_batches,
    combined_to_json,
    expected_case_ids,
    minimum_concordant_cases,
)
from apps.ai_client_app.evaluation.runner import CaseDiagnostic
from apps.ai_client_app.evaluation.sanitization import sanitize_text
from apps.ai_client_app.evaluation.scoring import CA4_MIN_CONCORDANCE_PCT, CaseResult

from .canonical import canonical_json_bytes, sha256_bytes
from .constants import DEFAULT_SEED, ArtifactKind, Classification, RunStatus
from .custody_policy import PUBLICATION_POLICY_VERSION
from .evidence import EvidencePolicyError, EvidenceStore, validate_finalized_state
from .manifest import ArtifactReference, RunManifest, create_manifest, finalize_manifest
from .redaction import contains_secret

SCENARIO_ID = "EX-09"
PROTOCOL = R2
DATASET_ID = R2.dataset_version
DECISION_REFERENCE = "S11-C3 (docs/PROGRESO_SPRINTS.md, 23/09/2026)"

OFFICIAL_MODE = "OFFICIAL"
LOCAL_TEST_MODE = "LOCAL TEST / NON-EXPERIMENTAL"
_EXPERIMENT_IDS = {
    OFFICIAL_MODE: "S11C3-EX09",
    LOCAL_TEST_MODE: "S11C3-EX09-LOCAL-TEST-NON-EXPERIMENTAL",
}

LOTS: Mapping[str, int] = {"lot-01": 1, "lot-02": 2}

#: Regla operativa **propia de S11-C3** (decisión del 23/09/2026, D3.5–D3.6). No es
#: una propiedad del protocolo R2 ni del prerregistro S8-E2-R2 ni de las fuentes S11-A:
#: acota la duración de la identidad experimental. El lote 2 debe iniciarse, además,
#: en la primera ventana RPD disponible posterior al lote 1.
OPERATIONAL_LOT_02_LIMIT = timedelta(days=7)
OPERATIONAL_RULE_SOURCE = "S11-C3 operational decision; not a property of R2 or S8-E2-R2"

#: Cuota diaria mínima por lote: un lote completo, sin margen para reintentos.
MINIMUM_QUOTA = BATCH_SIZE

PROVIDER_KEY_ENV = "S11_EX09_PROVIDER_KEY"
FINGERPRINT_LENGTH = 16
OPERATOR = "Monty Emmanuel Pohl Amarilla"
ENVIRONMENT_NAME = "s11c3-ex09-ephemeral-container"
PROVIDER = "gemini-interactions-api"

#: Valores prerregistrados de S8-E2-R2 (README de `evaluation`, tabla R2). Se comparan
#: con la instancia congelada `R2` para detectar una edición accidental del protocolo.
PREREGISTERED_R2 = {
    "version": "s8-e2-r2",
    "model_name": "gemini-3.6-flash",
    "dataset_version": "s8-evaluation-v1",
    "dataset_sha256": "3bc54d591dc104aeabeecb2f5cc7c7a38e70ba042e7f045321ab33ae9b5efad4",
    "prompt_version": "s8-classification-v1",
    "prompt_sha256": "f3c803984806a3b934db0417f6cc3ce59a49cdde1e30b4c86b862c2e11c665ca",
    "request_timeout_seconds": 60,
    "inter_request_delay_seconds": 20,
    "status_in_progress": "R2_IN_PROGRESS",
}

#: Credenciales y endpoints productivos que el contenedor efímero debe tener vacíos.
FORBIDDEN_ENVIRONMENT = (
    "GEMINI_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "BREVO_API_KEY",
    "DOCUMENT_HMAC_SECRET",
)
#: Interruptores que harían intervenir otra ruta de IA distinta del ejecutor.
DISABLED_FLAGS = ("AI_CLASSIFICATION_ENABLED", "S11_FAKE_AI_ENABLED")

OPERATOR_EVENTS = ("quota_check", "credential_removed", "access_revoked", "cleanup")
QUOTA_OK = "OK"
QUOTA_INSUFFICIENT = "INSUFFICIENT"
CHECK_PASS = "PASS"
CHECK_FAIL = "FAIL"
CHECK_NOT_EVALUATED = "NOT_EVALUATED"

_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_FINGERPRINT = re.compile(rf"^[0-9a-f]{{{FINGERPRINT_LENGTH}}}$")
_QUOTA_TYPE = re.compile(r"^ex09-(lot-0[12])-quota-check-(\d{2})$")
_TRUE_VALUES = {"1", "true", "yes", "on"}


class Ex09Error(RuntimeError):
    """El ejecutor no puede continuar. Nunca contiene la clave del proveedor."""


class Ex09Refusal(Ex09Error):
    """El paso se rechazó antes de cualquier llamada al proveedor.

    `terminal=True` significa que el run ya no puede continuar y sólo admite su
    finalización como `NOT_DEMONSTRATED`; `False`, que falta una condición que el
    operador puede satisfacer sin consumir cuota.
    """

    def __init__(self, reasons: Sequence[str], *, terminal: bool):
        self.reasons = tuple(reasons)
        self.terminal = terminal
        kind = "terminal" if terminal else "pendiente"
        super().__init__(f"EX-09 rechazado ({kind}): {list(self.reasons)}")


# --- Identidad, partición, correlación e invariancia ---------------------------------


def experiment_id(evidence_mode: str) -> str:
    if evidence_mode not in _EXPERIMENT_IDS:
        raise Ex09Error(f"Modo de evidencia desconocido: {evidence_mode!r}.")
    return _EXPERIMENT_IDS[evidence_mode]


def provider_sdk_attempts() -> int:
    """Intentos HTTP por petición que el adaptador real configura en el SDK.

    Se lee del adaptador y no se copia: `1` significa cero reintentos internos.
    """
    from apps.ai_client_app.adapters import gemini

    return gemini._SDK_ATTEMPTS_SIN_REINTENTOS


def check_partition(case_ids: Sequence[str], lots: Mapping[str, Sequence[str]]) -> list[str]:
    """Errores de la partición `lots` sobre `case_ids`, en el orden del dataset."""
    errors: list[str] = []
    if len(case_ids) != TOTAL_CASES:
        errors.append(f"dataset-size:{len(case_ids)}")
    if set(lots) != set(LOTS):
        errors.append(f"lot-ids:{sorted(lots)}")
        return errors
    expected = {
        lot_id: tuple(case_ids[(n - 1) * BATCH_SIZE : n * BATCH_SIZE]) for lot_id, n in LOTS.items()
    }
    joined = [case_id for lot_id in LOTS for case_id in lots[lot_id]]
    for lot_id in LOTS:
        if len(lots[lot_id]) != BATCH_SIZE:
            errors.append(f"lot-size:{lot_id}:{len(lots[lot_id])}")
        if tuple(lots[lot_id]) != expected[lot_id]:
            errors.append(f"lot-cases:{lot_id}")
    if len(set(joined)) != len(joined):
        errors.append("lot-overlap")
    if set(joined) != set(case_ids):
        errors.append("lot-gap")
    return errors


def dataset_partition(dataset: EvaluationDataset) -> dict[str, tuple[str, ...]]:
    try:
        return {lot_id: expected_case_ids(dataset, n) for lot_id, n in LOTS.items()}
    except RecoveryProtocolError as exc:
        raise Ex09Error(str(exc)) from exc


def validate_partition(dataset: EvaluationDataset) -> list[str]:
    case_ids = tuple(case.id for case in dataset.cases)
    try:
        lots = dataset_partition(dataset)
    except Ex09Error:
        return [f"dataset-size:{len(case_ids)}"]
    return check_partition(case_ids, lots)


def validate_correlation(
    dataset: EvaluationDataset, batches: Mapping[str, BatchEvaluation]
) -> list[str]:
    """Exactamente 28 casos, 28 respuestas y una correlación inequívoca por caso."""
    errors: list[str] = []
    case_ids = [case.id for case in dataset.cases]
    if len(case_ids) != TOTAL_CASES:
        return [f"dataset-size:{len(case_ids)}"]
    lots = dataset_partition(dataset)
    for lot_id in LOTS:
        batch = batches.get(lot_id)
        if batch is None:
            errors.append(f"missing-batch:{lot_id}")
            continue
        if batch.batch_number != LOTS[lot_id]:
            errors.append(f"batch-number:{lot_id}")
        for label, ids in (
            ("batch_case_ids", batch.batch_case_ids),
            ("results", tuple(result.id for result in batch.results)),
            ("diagnostics", tuple(diagnostic.id for diagnostic in batch.diagnostics)),
        ):
            if tuple(ids) != lots[lot_id]:
                errors.append(f"correlation:{lot_id}:{label}")
    responses = [result.id for batch in batches.values() for result in batch.results]
    if len(responses) != TOTAL_CASES:
        errors.append(f"responses:{len(responses)}")
    duplicated = sorted({case_id for case_id in responses if responses.count(case_id) > 1})
    if duplicated:
        errors.append(f"duplicated:{','.join(duplicated)}")
    missing = sorted(set(case_ids) - set(responses))
    if missing:
        errors.append(f"missing:{','.join(missing)}")
    unexpected = sorted(set(responses) - set(case_ids))
    if unexpected:
        errors.append(f"unexpected:{','.join(unexpected)}")
    return errors


def build_identity(
    *,
    git_sha: str,
    git_clean: bool,
    evidence_mode: str,
    dataset_path: Path | None = None,
) -> tuple[dict[str, Any], EvaluationDataset]:
    """Identidad invariante del run, construida desde los bytes y el template reales.

    Todo lo que debe coincidir entre los dos lotes vive aquí; los timestamps no, porque
    son lo único que cambia legítimamente entre ellos.
    """
    try:
        dataset, dataset_sha256 = load_verified_dataset(
            protocol=PROTOCOL, dataset_path=dataset_path
        )
        prompt_sha256 = verify_prompt(protocol=PROTOCOL)
    except RecoveryProtocolError as exc:
        raise Ex09Error(sanitize_text(str(exc))) from exc
    partition_errors = validate_partition(dataset)
    if partition_errors:
        raise Ex09Error(f"La partición de lotes no es la prerregistrada: {partition_errors}")
    lots = dataset_partition(dataset)
    identity = {
        "schema_version": "s11c3-ex09-identity-v1",
        "scenario_id": SCENARIO_ID,
        "experiment_id": experiment_id(evidence_mode),
        "evidence_mode": evidence_mode,
        "decision": DECISION_REFERENCE,
        "operator": OPERATOR,
        "git_sha": git_sha,
        "git_clean": git_clean,
        "protocol_version": PROTOCOL.version,
        "protocol_status_in_progress": PROTOCOL.status_in_progress,
        "provider": PROVIDER,
        "adapter_name": GEMINI_ADAPTER_NAME,
        "model_name": PROTOCOL.model_name,
        "store": False,
        "dataset_id": DATASET_ID,
        "dataset_sha256": dataset_sha256,
        "total_cases": len(dataset.cases),
        "prompt_version": PROTOCOL.prompt_version,
        "prompt_sha256": prompt_sha256,
        "request_timeout_seconds": PROTOCOL.request_timeout_seconds,
        "inter_request_delay_seconds": PROTOCOL.inter_request_delay_seconds,
        "retries": {
            "sdk_attempts": provider_sdk_attempts(),
            "script": 0,
            "celery": 0,
            "selective_reruns": 0,
        },
        "scoring": {
            "metric": "ca4_concordance_pct",
            "fields": ["category_slug", "institution_slug", "priority"],
            "threshold_pct": str(CA4_MIN_CONCORDANCE_PCT),
            "minimum_concordant_cases": minimum_concordant_cases(TOTAL_CASES),
            "lot_scored_separately": False,
        },
        "lots": {
            lot_id: {"batch_number": LOTS[lot_id], "case_ids": list(case_ids)}
            for lot_id, case_ids in lots.items()
        },
        "operational_rules": {
            "lot_02_window": "first-available-rpd-window-after-lot-01",
            "lot_02_max_days_from_lot_01_start": OPERATIONAL_LOT_02_LIMIT.days,
            "source": OPERATIONAL_RULE_SOURCE,
            "exceeded_result": RESULT_NOT_DEMONSTRATED,
        },
        "minimum_quota_per_lot": MINIMUM_QUOTA,
        "credential": {
            "delivery": f"process environment variable {PROVIDER_KEY_ENV}",
            "persisted": False,
            "fingerprint": f"sha256-prefix-{FINGERPRINT_LENGTH}-hex",
            "one_key_per_lot": True,
        },
        "custody_policy": PUBLICATION_POLICY_VERSION,
    }
    return identity, dataset


def identity_sha256(identity: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(identity))


def compare_identity(expected: Mapping[str, Any], observed: Mapping[str, Any]) -> list[str]:
    """Campos de la identidad que divergen entre lo fijado y lo observado."""
    return sorted(
        key
        for key in set(expected) | set(observed)
        if canonical_json_bytes(expected.get(key)) != canonical_json_bytes(observed.get(key))
    )


def check_protocol_frozen() -> list[str]:
    return sorted(
        name for name, value in PREREGISTERED_R2.items() if getattr(PROTOCOL, name) != value
    )


# --- Credencial, entorno y reloj -------------------------------------------------------


def credential_fingerprint(key: str) -> str:
    """Huella no reversible para correlacionar alta y revocación. Nunca la clave."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def read_credential(environ: Mapping[str, str]) -> str | None:
    value = environ.get(PROVIDER_KEY_ENV, "")
    return value if value.strip() else None


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


def filesystem_type(path: Path, *, mounts_path: Path = Path("/proc/self/mounts")) -> str | None:
    """Tipo del sistema de archivos que contiene `path`, según la tabla de montajes."""
    try:
        lines = mounts_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    target = Path(path).resolve()
    best: tuple[int, str] | None = None
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mount_point = Path(parts[1].replace("\\040", " "))
        if target == mount_point or mount_point in target.parents:
            depth = len(mount_point.parts)
            if best is None or depth >= best[0]:
                best = (depth, parts[2])
    return best[1] if best else None


def lot_02_deadline(lot_01_started_at: datetime) -> datetime:
    return _aware(lot_01_started_at) + OPERATIONAL_LOT_02_LIMIT


def within_operational_limit(lot_01_started_at: datetime, lot_02_started_at: datetime) -> bool:
    return _aware(lot_02_started_at) <= lot_02_deadline(lot_01_started_at)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise Ex09Error("Los timestamps de EX-09 deben incluir zona horaria.")
    return value.astimezone(UTC)


def _utc(value: datetime) -> str:
    return _aware(value).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))


# --- Preflight estático ----------------------------------------------------------------


def run_preflight(
    *,
    lot_id: str,
    git_sha: str,
    git_clean: bool,
    environ: Mapping[str, str],
    repo_root: Path,
    evidence_root: Path,
    evidence_mode: str,
    dataset_path: Path | None = None,
    mounts_path: Path = Path("/proc/self/mounts"),
    require_credential: bool = True,
) -> dict[str, Any]:
    """Comprobaciones deterministas previas a cualquier llamada. No usa la red.

    `require_credential=False` sólo para el preflight previo del operador, que corre
    sin la clave: la credencial queda `NOT_EVALUATED` y la exige el preflight que
    `execute_lot` custodia dentro del proceso del lote.
    """
    checks: list[dict[str, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "detail": detail, "result": CHECK_PASS if ok else CHECK_FAIL})

    check("lot", lot_id in LOTS, f"{lot_id} ∈ {list(LOTS)}")
    check(
        "git",
        bool(_SHA1.fullmatch(git_sha)) and git_clean is True,
        "commit verificado por el lanzador en el host y working tree limpio",
    )
    identity = None
    try:
        identity, _dataset = build_identity(
            git_sha=git_sha,
            git_clean=git_clean,
            evidence_mode=evidence_mode,
            dataset_path=dataset_path,
        )
    except Ex09Error as exc:
        check("dataset-prompt-partition", False, str(exc))
    else:
        check(
            "dataset",
            identity["dataset_sha256"] == PREREGISTERED_R2["dataset_sha256"]
            and identity["total_cases"] == TOTAL_CASES,
            f"{DATASET_ID}, {identity['total_cases']} casos, sha256 {identity['dataset_sha256']}",
        )
        check(
            "prompt",
            identity["prompt_sha256"] == PREREGISTERED_R2["prompt_sha256"],
            f"{identity['prompt_version']}, sha256 {identity['prompt_sha256']}",
        )
        check("batch-partition", True, "lot-01 = casos 1–14; lot-02 = casos 15–28; 14 + 14 = 28")
    drift = check_protocol_frozen()
    check("protocol-r2", not drift, f"campos divergentes: {drift}" if drift else "R2 congelado")
    attempts = provider_sdk_attempts()
    check("retries", attempts == 1, f"SDK attempts={attempts}; script 0; Celery no interviene")
    key = read_credential(environ)
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
                "detail": f"sin clave en el preflight previo; run-lot exige {PROVIDER_KEY_ENV}",
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
        "evidence-root",
        fs_type not in {None, "tmpfs", "ramfs"},
        f"sistema de archivos {fs_type}",
    )
    return {
        "schema_version": "s11c3-ex09-preflight-v1",
        "scenario_id": SCENARIO_ID,
        "lot_id": lot_id,
        "evidence_mode": evidence_mode,
        "result": (
            CHECK_PASS
            if all(c["result"] in (CHECK_PASS, CHECK_NOT_EVALUATED) for c in checks)
            else CHECK_FAIL
        ),
        "checks": checks,
        "identity_sha256": identity_sha256(identity) if identity else None,
        "credential_fingerprint": fingerprint,
        "provider_calls": 0,
    }


# --- Estado custodiado del run ---------------------------------------------------------


def _type(lot_id: str, name: str) -> str:
    return f"ex09-{lot_id}-{name}"


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

    def lot_records(self, lot_id: str) -> list[dict[str, Any]]:
        prefix = f"ex09-{lot_id}-"
        return [r for r in self.records if r["artifact_type"].startswith(prefix)]

    def quota_checks(self, lot_id: str) -> list[dict[str, Any]]:
        checks = []
        for record in self.records:
            match = _QUOTA_TYPE.fullmatch(record["artifact_type"])
            if match and match.group(1) == lot_id:
                checks.append(self.payloads.get(record["artifact_type"]) or {})
        return checks

    def lot_executed(self, lot_id: str) -> bool:
        return self.has(_type(lot_id, "credential-provisioned"))

    @property
    def initial_manifest(self) -> dict[str, Any] | None:
        return self.payloads.get("run-manifest-initial")

    @property
    def finalized(self) -> bool:
        return self.has("run-manifest-final") or (self.run_root / ".s11-finalized").exists()


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
            payloads[record["artifact_type"]] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable.append(record["artifact_type"])
    return RunState(
        run_id=run_id,
        run_root=run_root,
        records=records,
        payloads=payloads,
        unreadable=unreadable,
    )


def batch_from_payload(payload: Mapping[str, Any]) -> BatchEvaluation:
    """Reconstruye la evidencia de un lote tal como `evaluate_batch` la produjo."""
    data = dict(payload)
    data["batch_case_ids"] = tuple(data["batch_case_ids"])
    data["results"] = tuple(CaseResult(**item) for item in data["results"])
    data["diagnostics"] = tuple(CaseDiagnostic(**item) for item in data["diagnostics"])
    return BatchEvaluation(**data)


def _batch_payload(state: RunState, lot_id: str) -> Mapping[str, Any] | None:
    return state.payload(_type(lot_id, "batch"))


def _load_batch(state: RunState, lot_id: str) -> BatchEvaluation | None:
    payload = _batch_payload(state, lot_id)
    if payload is None:
        return None
    try:
        return batch_from_payload(payload)
    except (KeyError, TypeError, ValueError):
        return None


def _references(records: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "artifact_id": record["artifact_id"],
            "relative_path": record["relative_path"],
            "sha256": record["sha256"],
        }
        for record in records
    ]


# --- Persistencia ----------------------------------------------------------------------


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
    *, event: str, lot_id: str, run_id: str, recorded_at: datetime, result: str, **fields: Any
) -> dict[str, Any]:
    return {
        "schema_version": "s11c3-ex09-event-v1",
        "event": event,
        "lot_id": lot_id,
        "run_id": run_id,
        "recorded_at": _utc(recorded_at),
        "operator": OPERATOR,
        "attestation": "operator",
        "result": result,
        **fields,
    }


@contextlib.contextmanager
def capture_stderr():
    """Captura stderr y los avisos de logging del lote para persistirlos redactados."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setLevel(logging.WARNING)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        with contextlib.redirect_stderr(buffer):
            yield buffer
    finally:
        root.removeHandler(handler)


def redact_stderr(text: str, *, secret: str | None) -> bytes:
    lines = []
    for line in text.splitlines():
        if secret and secret in line:
            line = line.replace(secret, "[REDACTADO]")
        lines.append(sanitize_text(line))
    content = "\n".join(lines) + ("\n" if lines else "")
    if contains_secret(content) or (secret and secret in content):
        raise EvidencePolicyError("El stderr conserva un patrón de secreto tras redacción.")
    return content.encode("utf-8")


# --- Inicio del run --------------------------------------------------------------------


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
    """Asigna el `run_id` UUIDv5 y registra el manifest inicial al iniciar el lote 1."""
    if not git_clean or not _SHA1.fullmatch(git_sha):
        raise Ex09Error("EX-09 exige un commit verificado y un working tree limpio.")
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
    if store.records(manifest.run_id):
        raise Ex09Error("El run_id ya tiene evidencia indexada; se exige un nonce nuevo.")
    record = store.persist_json(
        run_id=manifest.run_id,
        scenario_id=SCENARIO_ID,
        artifact_type="run-manifest-initial",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="run-manifest.initial.json",
        payload=manifest.to_dict(),
        source="s11_ex09.start",
    )
    return {
        "run_id": manifest.run_id,
        "scenario_id": SCENARIO_ID,
        "evidence_mode": evidence_mode,
        "identity_sha256": identity_sha256(identity),
        "manifest_sha256": record.sha256,
        "status": manifest.status,
    }


# --- Registros del operador (D4) -------------------------------------------------------


def record_event(
    store: EvidenceStore,
    *,
    run_id: str,
    lot_id: str,
    event: str,
    recorded_at: datetime,
    environ: Mapping[str, str],
    observed_remaining: int | None = None,
    rpd_window: str | None = None,
    first_available_window: bool = False,
    credential_fingerprint_value: str | None = None,
    container_absent: bool = False,
) -> dict[str, Any]:
    """Registra una atestación del operador, autenticada por la custodia."""
    if event not in OPERATOR_EVENTS:
        raise Ex09Error(f"Evento desconocido: {event!r}.")
    if lot_id not in LOTS:
        raise Ex09Error(f"Lote desconocido: {lot_id!r}.")
    with store.mutation():
        state = load_state(store, run_id)
        _require_open_run(state)
        if event == "quota_check":
            payload, artifact_type = _quota_check(
                state,
                lot_id=lot_id,
                recorded_at=recorded_at,
                observed_remaining=observed_remaining,
                rpd_window=rpd_window,
                first_available_window=first_available_window,
            )
        else:
            payload, artifact_type = _lifecycle_event(
                state,
                lot_id=lot_id,
                event=event,
                recorded_at=recorded_at,
                environ=environ,
                credential_fingerprint_value=credential_fingerprint_value,
                container_absent=container_absent,
            )
        record = _persist(store, run_id, artifact_type, payload, source=f"s11_ex09.{event}")
    return {"artifact_type": artifact_type, "sha256": record.sha256, **payload}


def _quota_check(
    state: RunState,
    *,
    lot_id: str,
    recorded_at: datetime,
    observed_remaining: int | None,
    rpd_window: str | None,
    first_available_window: bool,
) -> tuple[dict[str, Any], str]:
    if state.lot_executed(lot_id):
        raise Ex09Refusal([f"quota-check-after-lot-start:{lot_id}"], terminal=False)
    if type(observed_remaining) is not int or observed_remaining < 0:
        raise Ex09Error("La cuota observada debe ser un entero no negativo.")
    try:
        window = date.fromisoformat(rpd_window or "")
    except ValueError as exc:
        raise Ex09Error("La ventana RPD debe ser una fecha YYYY-MM-DD.") from exc
    if abs((window - _aware(recorded_at).date()).days) > 1:
        raise Ex09Error("La ventana RPD no corresponde a la fecha UTC del registro.")
    if lot_id == "lot-02" and not state.has(_type("lot-01", "manifest")):
        raise Ex09Refusal(["lot-01-not-finished"], terminal=False)
    sequence = len(state.quota_checks(lot_id)) + 1
    if sequence > 99:
        raise Ex09Error("Demasiadas comprobaciones de cuota para un lote.")
    payload = _event_payload(
        event="quota_check",
        lot_id=lot_id,
        run_id=state.run_id,
        recorded_at=recorded_at,
        result=QUOTA_OK if observed_remaining >= MINIMUM_QUOTA else QUOTA_INSUFFICIENT,
        observed_remaining=observed_remaining,
        minimum_required=MINIMUM_QUOTA,
        rpd_window=window.isoformat(),
        first_available_window=bool(first_available_window),
        source="provider console, read-only; no provider call",
    )
    return payload, _type(lot_id, f"quota-check-{sequence:02d}")


def _lifecycle_event(
    state: RunState,
    *,
    lot_id: str,
    event: str,
    recorded_at: datetime,
    environ: Mapping[str, str],
    credential_fingerprint_value: str | None,
    container_absent: bool,
) -> tuple[dict[str, Any], str]:
    artifact_type = _type(lot_id, event.replace("_", "-"))
    if state.has(artifact_type):
        raise Ex09Refusal([f"already-recorded:{artifact_type}"], terminal=False)
    provisioned = state.payload(_type(lot_id, "credential-provisioned"))
    if provisioned is None:
        raise Ex09Refusal([f"lot-not-executed:{lot_id}"], terminal=False)
    fingerprint = provisioned["credential_fingerprint"]
    fields: dict[str, Any] = {"credential_fingerprint": fingerprint}
    if event == "credential_removed":
        if read_credential(environ) is not None:
            raise Ex09Refusal([f"{PROVIDER_KEY_ENV}-still-present"], terminal=False)
        fields["executor_process_ended"] = True
        fields["environment_variable_absent"] = True
    elif event == "access_revoked":
        if not state.has(_type(lot_id, "credential-removed")):
            raise Ex09Refusal([f"credential-not-removed:{lot_id}"], terminal=False)
        if (
            not _FINGERPRINT.fullmatch(credential_fingerprint_value or "")
            or credential_fingerprint_value != fingerprint
        ):
            raise Ex09Refusal([f"fingerprint-mismatch:{lot_id}"], terminal=False)
        fields["revoked_in"] = "provider console"
    elif event == "cleanup":
        if not state.has(_type(lot_id, "credential-removed")):
            raise Ex09Refusal([f"credential-not-removed:{lot_id}"], terminal=False)
        if not container_absent:
            raise Ex09Refusal([f"container-present:{lot_id}"], terminal=False)
        fields["ephemeral_container_absent"] = True
    payload = _event_payload(
        event=event,
        lot_id=lot_id,
        run_id=state.run_id,
        recorded_at=recorded_at,
        result=CHECK_PASS,
        **fields,
    )
    return payload, artifact_type


def _require_open_run(state: RunState) -> None:
    if state.initial_manifest is None:
        raise Ex09Error("El run no tiene manifest inicial de EX-09.")
    if state.initial_manifest.get("scenario_id") != SCENARIO_ID:
        raise Ex09Error("El run no pertenece a EX-09.")
    if state.finalized:
        raise Ex09Error("El run ya está finalizado y es inmutable.")


# --- Condiciones de cada lote ----------------------------------------------------------


def _accepted_quota_check(state: RunState, lot_id: str) -> dict[str, Any] | None:
    checks = state.quota_checks(lot_id)
    return checks[-1] if checks else None


def _lot_valid(state: RunState, lot_id: str) -> bool:
    batch = _load_batch(state, lot_id)
    return (
        batch is not None
        and state.has(_type(lot_id, "manifest"))
        and len(batch.results) == BATCH_SIZE
        and all(result.error_type is None for result in batch.results)
    )


def _lot_started_at(state: RunState, lot_id: str) -> datetime | None:
    manifest = state.payload(_type(lot_id, "manifest"))
    provisioned = state.payload(_type(lot_id, "credential-provisioned"))
    source = manifest or provisioned
    if not source:
        return None
    return _parse_utc(source.get("started_at") or source["recorded_at"])


def _window_errors(state: RunState, lot_id: str) -> list[str]:
    """Ventana RPD del lote: cuota ≥14 y, para el lote 2, la primera disponible."""
    errors: list[str] = []
    accepted = _accepted_quota_check(state, lot_id)
    if accepted is None:
        return [f"quota-check-missing:{lot_id}"]
    if accepted.get("result") != QUOTA_OK:
        errors.append(f"quota-insufficient:{lot_id}")
    if lot_id == "lot-02":
        first = _accepted_quota_check(state, "lot-01")
        if first is None or accepted.get("rpd_window", "") <= first.get("rpd_window", ""):
            errors.append("rpd-window-not-after-lot-01")
        if accepted.get("first_available_window") is not True:
            errors.append("rpd-window-not-attested-first-available")
        lot_01_window = first.get("rpd_window", "") if first else ""
        earlier_ok = [
            check
            for check in state.quota_checks(lot_id)[:-1]
            if check.get("result") == QUOTA_OK
            and lot_01_window < check.get("rpd_window", "") < accepted.get("rpd_window", "")
        ]
        if earlier_ok:
            errors.append("rpd-window-not-first-available")
        lot_01_manifest = state.payload(_type("lot-01", "manifest"))
        if lot_01_manifest and _parse_utc(accepted["recorded_at"]) < _parse_utc(
            lot_01_manifest["finished_at"]
        ):
            errors.append("quota-check-before-lot-01-finished")
    return errors


def lot_admission(
    state: RunState, lot_id: str, *, now: datetime, identity: Mapping[str, Any]
) -> tuple[list[str], list[str]]:
    """Condiciones previas al lote. Devuelve `(pendientes, terminales)`."""
    pending: list[str] = []
    terminal: list[str] = []
    attempted = state.lot_executed(lot_id) or any(
        not _QUOTA_TYPE.fullmatch(record["artifact_type"]) for record in state.lot_records(lot_id)
    )
    if attempted or (lot_id == "lot-02" and state.has("ex09-continuity")):
        terminal.append(f"lot-already-attempted:{lot_id}")
    divergent = compare_identity(state.initial_manifest.get("configuration", {}), identity)
    if divergent:
        terminal.append(f"identity-divergent:{','.join(divergent)}")
    if state.unreadable:
        terminal.append(f"unreadable:{','.join(state.unreadable)}")
    if lot_id == "lot-02":
        if not state.lot_executed("lot-01") or not state.has(_type("lot-01", "manifest")):
            pending.append("lot-01-not-finished")
        else:
            if not _lot_valid(state, "lot-01"):
                terminal.append("lot-01-invalid")
            for event in ("credential-removed", "access-revoked", "cleanup"):
                if not state.has(_type("lot-01", event)):
                    pending.append(f"lot-01-{event}-missing")
            started = _lot_started_at(state, "lot-01")
            if started is not None and _aware(now) > lot_02_deadline(started):
                terminal.append("lot-02-operational-limit-exceeded")
    # Esperar otra ventana o registrar la comprobación no cuesta cuota; saltarse la
    # primera ventana disponible posterior al lote 1, en cambio, ya no se corrige.
    for error in _window_errors(state, lot_id):
        (terminal if error == "rpd-window-not-first-available" else pending).append(error)
    return pending, terminal


# --- Ejecución de un lote --------------------------------------------------------------


BatchRunner = Callable[..., BatchEvaluation]


def execute_lot(
    store: EvidenceStore,
    *,
    run_id: str,
    lot_id: str,
    git_sha: str,
    git_clean: bool,
    environ: Mapping[str, str],
    repo_root: Path,
    clock: Callable[[], datetime],
    runner: BatchRunner = run_recovery_batch,
    evidence_mode: str = OFFICIAL_MODE,
    dataset_path: Path | None = None,
    mounts_path: Path = Path("/proc/self/mounts"),
) -> dict[str, Any]:
    """Ejecuta un lote una sola vez y deja su evidencia custodiada.

    Todas las comprobaciones que no cuestan cuota ocurren antes de la primera llamada.
    El resumen devuelto no contiene CA-4: un lote nunca se puntúa por separado.
    """
    if lot_id not in LOTS:
        raise Ex09Error(f"Lote desconocido: {lot_id!r}.")
    key = read_credential(environ)
    if key is None:
        raise Ex09Refusal([f"{PROVIDER_KEY_ENV}-missing"], terminal=False)
    fingerprint = credential_fingerprint(key)
    try:
        identity, dataset = build_identity(
            git_sha=git_sha,
            git_clean=git_clean,
            evidence_mode=evidence_mode,
            dataset_path=dataset_path,
        )
    except Ex09Error as exc:
        raise Ex09Refusal([f"identity:{exc}"], terminal=False) from exc

    with store.mutation():
        state = load_state(store, run_id)
        _require_open_run(state)
        custody_errors = store.verify(run_id)
        pending, terminal = lot_admission(state, lot_id, now=clock(), identity=identity)
        if custody_errors:
            terminal.append(f"custody:{','.join(custody_errors)}")
        if lot_id == "lot-02":
            first = state.payload(_type("lot-01", "credential-provisioned"))
            if first and first.get("credential_fingerprint") == fingerprint:
                pending.append("credential-reused-from-lot-01")
        if terminal:
            if lot_id == "lot-02" and not any(
                reason.startswith("lot-already-attempted") for reason in terminal
            ):
                # D3.4: cualquier diferencia deja el run en NOT_DEMONSTRATED, y eso
                # queda custodiado antes de rechazar el lote.
                _persist(
                    store,
                    run_id,
                    "ex09-continuity",
                    _continuity_payload(
                        store, state, identity=identity, now=clock(), admission=terminal
                    ),
                    source="s11_ex09.run-lot",
                )
            raise Ex09Refusal(terminal + pending, terminal=True)
        if pending:
            raise Ex09Refusal(pending, terminal=False)

        lot_artifacts = list(state.lot_records(lot_id))
        provisioned = _persist(
            store,
            run_id,
            _type(lot_id, "credential-provisioned"),
            _event_payload(
                event="credential_provisioned",
                lot_id=lot_id,
                run_id=run_id,
                recorded_at=clock(),
                result=CHECK_PASS,
                credential_fingerprint=fingerprint,
                delivery=f"process environment variable {PROVIDER_KEY_ENV}",
            ),
            source="s11_ex09.run-lot",
            secret=key,
        )
        preflight = run_preflight(
            lot_id=lot_id,
            git_sha=git_sha,
            git_clean=git_clean,
            environ=environ,
            repo_root=repo_root,
            evidence_root=store.root,
            evidence_mode=evidence_mode,
            dataset_path=dataset_path,
            mounts_path=mounts_path,
        )
        preflight_record = _persist(
            store,
            run_id,
            _type(lot_id, "preflight"),
            {**preflight, "run_id": run_id, "recorded_at": _utc(clock())},
            source="s11_ex09.run-lot",
            secret=key,
        )
        lot_artifacts += [asdict(provisioned), asdict(preflight_record)]
        if preflight["result"] != CHECK_PASS:
            raise Ex09Refusal([f"preflight-failed:{lot_id}"], terminal=True)

        if lot_id == "lot-02":
            continuity = _continuity_payload(store, state, identity=identity, now=clock())
            continuity_record = _persist(
                store,
                run_id,
                "ex09-continuity",
                continuity,
                source="s11_ex09.run-lot",
            )
            lot_artifacts.append(asdict(continuity_record))
            if continuity["result"] != CHECK_PASS:
                raise Ex09Refusal(["continuity-failed"], terminal=True)

        started_at = clock()
        lot_01_started = _lot_started_at(state, "lot-01")
        if lot_id == "lot-02" and not within_operational_limit(lot_01_started, started_at):
            raise Ex09Refusal(["lot-02-operational-limit-exceeded"], terminal=True)
        execution_error = None
        batch = None
        with capture_stderr() as captured:
            try:
                batch = runner(
                    protocol=PROTOCOL,
                    batch_number=LOTS[lot_id],
                    api_key=key,
                    dataset_path=dataset_path,
                )
            except Exception as exc:
                # Se custodia y el run queda inválido; nunca se reintenta.
                execution_error = f"{type(exc).__name__}: {sanitize_text(str(exc))}"
        finished_at = clock()

        stderr_record = store.persist_bytes(
            run_id=run_id,
            scenario_id=SCENARIO_ID,
            artifact_type=_type(lot_id, "stderr"),
            kind=ArtifactKind.LOG,
            classification=Classification.INTERNAL,
            filename=f"{_type(lot_id, 'stderr')}.txt",
            content=redact_stderr(captured.getvalue(), secret=key),
            mime_type="text/plain",
            source="s11_ex09.run-lot",
            transformation="evaluation-sanitization+secret-guard",
        )
        lot_artifacts.append(asdict(stderr_record))
        if batch is not None:
            batch_record = _persist(
                store,
                run_id,
                _type(lot_id, "batch"),
                asdict(batch),
                kind=ArtifactKind.RAW,
                classification=Classification.EXPERIMENTAL,
                source="s11_ex09.run-lot",
                secret=key,
            )
            lot_artifacts.append(asdict(batch_record))
        errors_count = (
            sum(1 for result in batch.results if result.error_type is not None)
            if batch is not None
            else None
        )
        lot_manifest = {
            "schema_version": "s11c3-ex09-lot-manifest-v1",
            "run_id": run_id,
            "scenario_id": SCENARIO_ID,
            "lot_id": lot_id,
            "batch_number": LOTS[lot_id],
            "evidence_mode": evidence_mode,
            "identity_sha256": identity_sha256(identity),
            "started_at": _utc(started_at),
            "finished_at": _utc(finished_at),
            "case_ids": list(identity["lots"][lot_id]["case_ids"]),
            "attempted_cases": len(batch.results) if batch is not None else 0,
            "provider_errors": errors_count,
            "execution_error": execution_error,
            "credential_fingerprint": fingerprint,
            "artifacts": _references(lot_artifacts),
            "scored": False,
        }
        manifest_record = _persist(
            store, run_id, _type(lot_id, "manifest"), lot_manifest, source="s11_ex09.run-lot"
        )
        combined_sha256 = None
        if lot_id == "lot-02" and batch is not None and execution_error is None:
            state = load_state(store, run_id)
            if _lot_valid(state, "lot-01") and _lot_valid(state, "lot-02"):
                combined_sha256 = _persist_combined(store, state, dataset)
    return {
        "run_id": run_id,
        "lot_id": lot_id,
        "evidence_mode": evidence_mode,
        "started_at": lot_manifest["started_at"],
        "finished_at": lot_manifest["finished_at"],
        "attempted_cases": lot_manifest["attempted_cases"],
        "provider_errors": errors_count,
        "execution_error": execution_error,
        "credential_fingerprint": fingerprint,
        "lot_manifest_sha256": manifest_record.sha256,
        "combined_persisted": combined_sha256 is not None,
        "scored": False,
    }


def _continuity_payload(
    store: EvidenceStore,
    state: RunState,
    *,
    identity: Mapping[str, Any],
    now: datetime,
    admission: Sequence[str] = (),
) -> dict[str, Any]:
    """Continuidad del lote 1 al lote 2, verificada antes de cualquier llamada."""
    checks = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "detail": detail, "result": CHECK_PASS if ok else CHECK_FAIL})

    if admission:
        check("admission", False, f"{list(admission)}")

    configuration = state.initial_manifest.get("configuration", {})
    divergent = compare_identity(configuration, identity)
    check("identity", not divergent, f"divergentes: {divergent}" if divergent else "idéntica")
    lot_manifest = state.payload(_type("lot-01", "manifest")) or {}
    indexed = {record["artifact_id"]: record["sha256"] for record in state.records}
    references = lot_manifest.get("artifacts", [])
    hashes_ok = bool(references) and all(
        indexed.get(ref["artifact_id"]) == ref["sha256"] for ref in references
    )
    check("lot-01-artifacts", hashes_ok, f"{len(references)} artefactos del lote 1 verificados")
    check(
        "lot-01-identity",
        lot_manifest.get("identity_sha256") == identity_sha256(identity),
        "identity_sha256 del lote 1 igual al del lote 2",
    )
    custody_errors = store.verify(state.run_id)
    check("custody", not custody_errors, f"{custody_errors}" if custody_errors else "íntegra")
    started = _lot_started_at(state, "lot-01")
    deadline = lot_02_deadline(started) if started else None
    check(
        "operational-limit",
        deadline is not None and _aware(now) <= deadline,
        f"lote 2 antes de {_utc(deadline) if deadline else 'desconocido'} "
        f"({OPERATIONAL_RULE_SOURCE})",
    )
    window = _window_errors(state, "lot-02")
    check("rpd-window", not window, f"{window}" if window else "ventana posterior, cuota ≥14")
    return {
        "schema_version": "s11c3-ex09-continuity-v1",
        "run_id": state.run_id,
        "lot_id": "lot-02",
        "recorded_at": _utc(now),
        "identity_sha256": identity_sha256(identity),
        "checks": checks,
        "result": CHECK_PASS if all(c["result"] == CHECK_PASS for c in checks) else CHECK_FAIL,
    }


def _combine(state: RunState, dataset: EvaluationDataset) -> tuple[Any | None, list[str]]:
    batches = {lot_id: _load_batch(state, lot_id) for lot_id in LOTS}
    present = {lot_id: batch for lot_id, batch in batches.items() if batch is not None}
    errors = validate_correlation(dataset, present)
    if errors:
        return None, errors
    try:
        return combine_batches(dataset, present.values(), protocol=PROTOCOL), []
    except RecoveryProtocolError as exc:
        return None, [f"combine:{sanitize_text(str(exc))}"]


def _combined_payload(state: RunState, combined) -> dict[str, Any]:
    return {
        "schema_version": "s11c3-ex09-combined-v1",
        "run_id": state.run_id,
        "scenario_id": SCENARIO_ID,
        "lot_manifests": {
            lot_id: (state.record(_type(lot_id, "manifest")) or {}).get("sha256") for lot_id in LOTS
        },
        "batches": {
            lot_id: (state.record(_type(lot_id, "batch")) or {}).get("sha256") for lot_id in LOTS
        },
        "combined": json.loads(combined_to_json(combined)),
    }


def _persist_combined(store: EvidenceStore, state: RunState, dataset: EvaluationDataset) -> str:
    combined, errors = _combine(state, dataset)
    if combined is None:
        raise Ex09Error(f"No se pudo combinar la corrida: {errors}")
    record = _persist(
        store,
        state.run_id,
        "ex09-combined",
        _combined_payload(state, combined),
        kind=ArtifactKind.DERIVED,
        classification=Classification.EXPERIMENTAL,
        source="s11_ex09.run-lot",
    )
    return record.sha256


# --- Evaluación y finalización ---------------------------------------------------------


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
    now: datetime,
) -> Evaluation:
    """Decide sobre la evidencia custodiada. Sólo `PASS`, `FAIL` o `NOT_DEMONSTRATED`."""
    invalidating: list[str] = []
    pending: list[str] = []
    custody_errors = store.verify(state.run_id)
    invalidating += [f"custody:{error}" for error in custody_errors]
    invalidating += [f"unreadable:{artifact}" for artifact in state.unreadable]
    divergent = compare_identity(state.initial_manifest.get("configuration", {}), identity)
    invalidating += [f"identity-divergent:{name}" for name in divergent]

    lot_01_started = _lot_started_at(state, "lot-01")
    for lot_id in LOTS:
        if not state.lot_executed(lot_id):
            continue
        invalidating += _window_errors(state, lot_id)
        preflight = state.payload(_type(lot_id, "preflight"))
        if not preflight or preflight.get("result") != CHECK_PASS:
            invalidating.append(f"preflight-failed:{lot_id}")
        manifest = state.payload(_type(lot_id, "manifest"))
        if manifest is None:
            invalidating.append(f"lot-manifest-missing:{lot_id}")
        else:
            if manifest.get("execution_error"):
                invalidating.append(f"execution-error:{lot_id}")
            indexed = {record["artifact_id"]: record["sha256"] for record in state.records}
            if any(
                indexed.get(ref["artifact_id"]) != ref["sha256"] for ref in manifest["artifacts"]
            ):
                invalidating.append(f"lot-artifact-hash:{lot_id}")
            if manifest.get("identity_sha256") != identity_sha256(identity):
                invalidating.append(f"lot-identity:{lot_id}")
        if not state.has(_type(lot_id, "stderr")):
            invalidating.append(f"stderr-missing:{lot_id}")
        batch = _load_batch(state, lot_id)
        if batch is None:
            invalidating.append(f"batch-missing:{lot_id}")
        else:
            errors = sum(1 for result in batch.results if result.error_type is not None)
            if errors:
                invalidating.append(f"provider-errors:{lot_id}:{errors}")
        for event in ("credential-removed", "access-revoked", "cleanup"):
            if not state.has(_type(lot_id, event)):
                pending.append(f"{event}-missing:{lot_id}")

    continuity = state.payload("ex09-continuity")
    if continuity is not None and continuity.get("result") != CHECK_PASS:
        invalidating.append("continuity-failed")
    lot_01_done = state.lot_executed("lot-01")
    lot_02_done = state.lot_executed("lot-02")
    # Un lote pendiente sólo es «pendiente» si el run todavía puede continuar; si ya
    # hay una causa de invalidez, su ausencia es otra causa más.
    progress = invalidating if invalidating else pending
    if not lot_01_done:
        progress.append("lot-01-not-executed")
    if lot_01_done and not lot_02_done:
        if lot_01_started is not None and _aware(now) > lot_02_deadline(lot_01_started):
            invalidating.append("lot-02-not-started-within-s11c3-operational-limit")
        else:
            progress.append("lot-02-not-executed")
    if lot_02_done:
        if continuity is None:
            invalidating.append("continuity-missing")
        lot_02_started = _lot_started_at(state, "lot-02")
        if (
            lot_01_started is None
            or lot_02_started is None
            or not within_operational_limit(lot_01_started, lot_02_started)
        ):
            invalidating.append("lot-02-operational-limit-exceeded")
        fingerprints = {
            (state.payload(_type(lot_id, "credential-provisioned")) or {}).get(
                "credential_fingerprint"
            )
            for lot_id in LOTS
        }
        if len(fingerprints) != len(LOTS) or None in fingerprints:
            invalidating.append("credential-not-distinct-per-lot")

    ca4 = None
    if lot_01_done and lot_02_done and not invalidating:
        combined, errors = _combine(state, dataset)
        invalidating += errors
        persisted = state.payload("ex09-combined")
        if combined is not None:
            if persisted is None:
                invalidating.append("combined-missing")
            elif canonical_json_bytes(persisted) != canonical_json_bytes(
                _combined_payload(state, combined)
            ):
                invalidating.append("combined-divergent")
            else:
                ca4 = {
                    "ca4_result": combined.ca4_result,
                    "run_valid_for_ca4": combined.run_valid_for_ca4,
                    "ca4_concordant_cases": combined.report.ca4_concordant_cases,
                    "total_cases": combined.report.total_cases,
                    "ca4_concordance_pct": str(combined.report.ca4_concordance_pct),
                    "minimum_concordant_cases": combined.minimum_concordant_cases,
                    "threshold_pct": str(CA4_MIN_CONCORDANCE_PCT),
                }

    if invalidating or pending or ca4 is None:
        decision = RESULT_NOT_DEMONSTRATED
    elif ca4["ca4_result"] in (RESULT_PASS, RESULT_FAIL) and ca4["run_valid_for_ca4"]:
        decision = ca4["ca4_result"]
    else:
        decision = RESULT_NOT_DEMONSTRATED
    return Evaluation(
        decision=decision,
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

    Un run que todavía puede continuar —o al que le faltan atestaciones del
    operador— no se finaliza salvo que el operador declare `abandon_reason`, en cuyo
    caso el resultado es `NOT_DEMONSTRATED`.
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
        evaluation = evaluate_run(store, state, dataset=dataset, identity=identity, now=now)
        reasons = list(evaluation.invalidating)
        if evaluation.pending and not abandon_reason:
            raise Ex09Refusal(evaluation.pending, terminal=False)
        decision = evaluation.decision
        if abandon_reason:
            reasons.append(f"abandoned:{sanitize_text(abandon_reason)}")
            reasons += list(evaluation.pending)
            decision = RESULT_NOT_DEMONSTRATED
        if decision not in (RESULT_PASS, RESULT_FAIL, RESULT_NOT_DEMONSTRATED):
            raise Ex09Error(f"Decisión fuera del contrato: {decision!r}.")
        if decision == RESULT_NOT_DEMONSTRATED and not reasons:
            reasons.append("run-not-demonstrable")

        decision_payload = {
            "schema_version": "s11c3-ex09-decision-v1",
            "run_id": run_id,
            "scenario_id": SCENARIO_ID,
            "evidence_mode": evidence_mode,
            "decided_at": _utc(now),
            "decision": decision,
            "reasons": reasons if decision == RESULT_NOT_DEMONSTRATED else [],
            "ca4": evaluation.ca4 if decision in (RESULT_PASS, RESULT_FAIL) else None,
            "operational_rule": {
                "lot_02_max_days_from_lot_01_start": OPERATIONAL_LOT_02_LIMIT.days,
                "source": OPERATIONAL_RULE_SOURCE,
            },
            "allowed_decisions": [RESULT_PASS, RESULT_FAIL, RESULT_NOT_DEMONSTRATED],
        }
        existing = state.payload("ex09-decision")
        if existing is not None:
            # Una finalización interrumpida ya custodió su decisión: se reutiliza sólo
            # si la evidencia vuelve a producir exactamente la misma.
            if (existing["decision"], existing["reasons"]) != (
                decision_payload["decision"],
                decision_payload["reasons"],
            ):
                raise Ex09Error("La decisión custodiada no coincide con la recalculada.")
            decision_payload = existing
            decision_sha256 = state.record("ex09-decision")["sha256"]
        else:
            decision_sha256 = _persist(
                store,
                run_id,
                "ex09-decision",
                decision_payload,
                kind=ArtifactKind.DERIVED,
                classification=Classification.EXPERIMENTAL,
                source="s11_ex09.finalize",
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
            source="s11_ex09.finalize",
        )
        verification = store.verify(run_id)
        # Un fallo de custodia previo ya es causa registrada de NOT_DEMONSTRATED y no
        # desaparece al sellar; cualquier fallo nuevo, en cambio, es un error.
        if verification and not (
            decision == RESULT_NOT_DEMONSTRATED and set(verification) <= known_custody_errors
        ):
            raise Ex09Error(f"Falló la validación final de la custodia: {verification}")
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
    lot_01_started = _lot_started_at(state, "lot-01")
    return {
        "run_id": run_id,
        "finalized": state.finalized,
        "custody_errors": store.verify(run_id),
        "lots": {
            lot_id: {
                "executed": state.lot_executed(lot_id),
                "finished": state.has(_type(lot_id, "manifest")),
                "quota_checks": len(state.quota_checks(lot_id)),
                "credential_removed": state.has(_type(lot_id, "credential-removed")),
                "access_revoked": state.has(_type(lot_id, "access-revoked")),
                "cleanup": state.has(_type(lot_id, "cleanup")),
            }
            for lot_id in LOTS
        },
        "lot_02_deadline": _utc(lot_02_deadline(lot_01_started)) if lot_01_started else None,
        "lot_02_deadline_source": OPERATIONAL_RULE_SOURCE,
        "checked_at": _utc(now),
    }
