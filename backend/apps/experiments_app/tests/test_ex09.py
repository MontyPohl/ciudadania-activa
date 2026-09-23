"""Ejecutor EX-09 bajo S11-C3. LOCAL TEST / NON-EXPERIMENTAL.

Ninguna prueba contacta al proveedor ni consume cuota: los sockets quedan bloqueados,
el cliente y el adaptador del SDK son dobles, y toda la custodia vive en
`.s11-test-artifacts/` con `evidence_mode = "LOCAL TEST / NON-EXPERIMENTAL"`.
La clave usada tiene forma de clave pero es inventada.
"""

import json
import shutil
import socket
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import pytest

from apps.ai_client_app.adapters import gemini
from apps.ai_client_app.evaluation.dataset import DATASET_PATH, load_dataset
from apps.ai_client_app.evaluation.execution import run_recovery_batch
from apps.ai_client_app.evaluation.recovery import R2
from apps.ai_client_app.exceptions import TransientAiProviderError
from apps.experiments_app import ex09, ex09_launcher
from apps.experiments_app.custody_policy import publication_public_key_from_private
from apps.experiments_app.evidence import EvidencePolicyError, EvidenceStore

MODE = ex09.LOCAL_TEST_MODE
GIT_SHA = "a" * 40
KEY_LOT_01 = "AIzaSyLOCALtestNOTaREALkeyLOT01xxxxxxxx"
KEY_LOT_02 = "AIzaSyLOCALtestNOTaREALkeyLOT02xxxxxxxx"
START = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
_CUSTODY_KEY = b"synthetic-custody-test-key-32-bytes"
_PUBLICATION_KEY = publication_public_key_from_private(bytes(range(32)))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Cualquier intento de red o de construir el cliente real hace fallar la prueba."""

    def blocked(*args, **kwargs):
        raise AssertionError("LOCAL TEST / NON-EXPERIMENTAL: la red está prohibida.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(gemini, "build_client", blocked)


@pytest.fixture(scope="module")
def dataset():
    return load_dataset()


class Clock:
    def __init__(self, start=START):
        self.now = start

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


class FakeClient:
    def __init__(self, *, api_key, timeout_seconds):
        self.timeout_seconds = timeout_seconds


class FakeAdapter:
    """Responde la etiqueta de oro, salvo los casos marcados como erróneos o caídos."""

    def __init__(self, dataset, *, wrong=(), timeouts=(), noise=None):
        self._cases = {case.description: case for case in dataset.cases}
        self._priorities = dataset.catalog.priorities
        self.wrong = set(wrong)
        self.timeouts = set(timeouts)
        self.noise = noise
        self.calls = []

    def __call__(self, *, client, model):
        assert client.timeout_seconds == R2.request_timeout_seconds
        assert model == R2.model_name
        return self

    def classify(self, request):
        case = self._cases[request.description]
        self.calls.append(case.id)
        if self.noise:
            sys.stderr.write(self.noise + "\n")
        if case.id in self.timeouts:
            raise TransientAiProviderError("El proveedor de IA no respondió a tiempo.")
        priority = case.expected.priority
        if case.id in self.wrong:
            priority = next(p for p in self._priorities if p != priority)
        return json.dumps(
            {
                "category_slug": case.expected.category_slug,
                "institution_slug": case.expected.institution_slug,
                "priority": priority,
                "confidence": 0.9,
                "inconsistency": case.expected.inconsistency,
                "justification": "LOCAL TEST / NON-EXPERIMENTAL.",
            }
        )


class Context:
    def __init__(self, root, dataset):
        self.root = root
        self.repo_root = root / "repo"
        self.repo_root.mkdir(parents=True)
        self.mounts = root / "mounts"
        self.mounts.write_text("/dev/local / ext4 rw 0 0\n", encoding="utf-8")
        self.store = EvidenceStore(
            root / "evidence",
            custody_hmac_key=_CUSTODY_KEY,
            publication_verification_key=_PUBLICATION_KEY,
            rolling_checksums=False,
        )
        self.clock = Clock()
        self.adapter = FakeAdapter(dataset)
        self.run_id = None

    def runner(self):
        return partial(
            run_recovery_batch,
            client_factory=FakeClient,
            adapter_factory=self.adapter,
            sleep=lambda seconds: None,
        )

    def start(self):
        result = ex09.start_run(
            self.store,
            git_sha=GIT_SHA,
            git_clean=True,
            nonce="local-test-non-experimental",
            evidence_mode=MODE,
            started_at=self.clock(),
            timezone="America/Asuncion",
        )
        self.run_id = result["run_id"]
        return result

    def record(self, lot_id, event, **kwargs):
        return ex09.record_event(
            self.store,
            run_id=self.run_id,
            lot_id=lot_id,
            event=event,
            recorded_at=self.clock(),
            environ=kwargs.pop("environ", {}),
            **kwargs,
        )

    def quota(self, lot_id, *, remaining=20, window=None, first=False):
        window = window or self.clock.now.date().isoformat()
        return self.record(
            lot_id,
            "quota_check",
            observed_remaining=remaining,
            rpd_window=window,
            first_available_window=first,
        )

    def run_lot(self, lot_id, key, *, git_sha=GIT_SHA, environ=None, runner=None):
        return ex09.execute_lot(
            self.store,
            run_id=self.run_id,
            lot_id=lot_id,
            git_sha=git_sha,
            git_clean=True,
            environ=environ if environ is not None else {ex09.PROVIDER_KEY_ENV: key},
            repo_root=self.repo_root,
            clock=self.clock,
            runner=runner or self.runner(),
            evidence_mode=MODE,
            mounts_path=self.mounts,
        )

    def close_lot(self, lot_id, key):
        self.record(lot_id, "credential_removed")
        self.record(
            lot_id,
            "access_revoked",
            credential_fingerprint_value=ex09.credential_fingerprint(key),
        )
        self.record(lot_id, "cleanup", container_absent=True)

    def lot_01(self):
        self.start()
        self.quota("lot-01")
        self.run_lot("lot-01", KEY_LOT_01)
        self.close_lot("lot-01", KEY_LOT_01)

    def lot_02(self):
        self.clock.advance(days=1)
        self.quota("lot-02", first=True)
        self.run_lot("lot-02", KEY_LOT_02)
        self.close_lot("lot-02", KEY_LOT_02)

    def finalize(self, **kwargs):
        return ex09.finalize_run(
            self.store,
            run_id=self.run_id,
            git_sha=GIT_SHA,
            git_clean=True,
            clock=self.clock,
            evidence_mode=MODE,
            **kwargs,
        )

    @property
    def run_root(self):
        return self.store.root / "runs" / self.run_id

    def payload(self, artifact_type):
        return ex09.load_state(self.store, self.run_id).payload(artifact_type)


@pytest.fixture
def ctx(request, dataset):
    root = Path.cwd() / ".s11-test-artifacts" / f"ex09-{request.node.name}"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    yield Context(root, dataset)
    shutil.rmtree(root, ignore_errors=True)


def _all_bytes(root):
    return b"".join(path.read_bytes() for path in root.rglob("*") if path.is_file())


# --- Dataset, prompt, partición, identidad ---------------------------------------------


def test_identity_is_bound_to_real_dataset_prompt_and_frozen_r2(dataset):
    identity, loaded = ex09.build_identity(git_sha=GIT_SHA, git_clean=True, evidence_mode=MODE)

    assert len(loaded.cases) == 28
    assert identity["dataset_id"] == "s8-evaluation-v1"
    assert identity["dataset_sha256"] == (
        "3bc54d591dc104aeabeecb2f5cc7c7a38e70ba042e7f045321ab33ae9b5efad4"
    )
    assert identity["prompt_sha256"] == R2.prompt_sha256
    assert identity["model_name"] == "gemini-3.6-flash"
    assert identity["request_timeout_seconds"] == 60
    assert identity["inter_request_delay_seconds"] == 20
    assert identity["retries"] == {
        "sdk_attempts": 1,
        "script": 0,
        "celery": 0,
        "selective_reruns": 0,
    }
    assert identity["scoring"]["minimum_concordant_cases"] == 24
    assert identity["scoring"]["lot_scored_separately"] is False
    assert identity["evidence_mode"] == "LOCAL TEST / NON-EXPERIMENTAL"
    assert ex09.check_protocol_frozen() == []


def test_identity_rejects_a_dataset_with_other_bytes(ctx):
    altered = ctx.root / "dataset.json"
    altered.write_bytes(DATASET_PATH.read_bytes() + b"\n")

    with pytest.raises(ex09.Ex09Error, match="prerregistrado"):
        ex09.build_identity(
            git_sha=GIT_SHA, git_clean=True, evidence_mode=MODE, dataset_path=altered
        )


def test_prompt_divergence_blocks_identity(monkeypatch):
    monkeypatch.setattr(
        "apps.ai_client_app.evaluation.execution.load_prompt_template",
        lambda: "otro prompt",
    )
    with pytest.raises(ex09.Ex09Error, match="prompt"):
        ex09.build_identity(git_sha=GIT_SHA, git_clean=True, evidence_mode=MODE)


def test_batch_partition_is_1_14_and_15_28_without_gaps_or_overlap(dataset):
    lots = ex09.dataset_partition(dataset)
    ids = [case.id for case in dataset.cases]

    assert lots["lot-01"] == tuple(ids[:14])
    assert lots["lot-02"] == tuple(ids[14:])
    assert len(lots["lot-01"]) + len(lots["lot-02"]) == 28
    assert ex09.validate_partition(dataset) == []


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda ids: {"lot-01": ids[:13], "lot-02": ids[14:]}, "lot-gap"),
        (lambda ids: {"lot-01": ids[:14], "lot-02": ids[13:27]}, "lot-overlap"),
        (lambda ids: {"lot-01": ids[14:], "lot-02": ids[:14]}, "lot-cases:lot-01"),
        (lambda ids: {"lot-01": ids[:15], "lot-02": ids[15:]}, "lot-size:lot-01:15"),
    ],
)
def test_partition_check_detects_tampering(dataset, mutate, error):
    ids = tuple(case.id for case in dataset.cases)
    assert error in ex09.check_partition(ids, mutate(ids))


@pytest.mark.parametrize(
    "field",
    [
        "git_sha",
        "dataset_sha256",
        "prompt_sha256",
        "model_name",
        "request_timeout_seconds",
        "inter_request_delay_seconds",
        "retries",
        "lots",
        "scoring",
        "protocol_version",
    ],
)
def test_configuration_invariance_detects_each_divergent_field(field):
    identity, _ = ex09.build_identity(git_sha=GIT_SHA, git_clean=True, evidence_mode=MODE)
    observed = {**identity, field: "divergente"}
    assert ex09.compare_identity(identity, observed) == [field]
    assert ex09.compare_identity(identity, dict(identity)) == []


# --- Correlación -----------------------------------------------------------------------


def _batches(dataset):
    adapter = FakeAdapter(dataset)
    runner = partial(
        run_recovery_batch,
        client_factory=FakeClient,
        adapter_factory=adapter,
        sleep=lambda seconds: None,
    )
    return {
        lot_id: runner(protocol=R2, batch_number=number, api_key=KEY_LOT_01)
        for lot_id, number in ex09.LOTS.items()
    }


def test_correlation_accepts_exactly_28_unique_responses(dataset):
    assert ex09.validate_correlation(dataset, _batches(dataset)) == []


def test_correlation_detects_missing_duplicate_and_unexpected_cases(dataset):
    batches = _batches(dataset)
    lot_02 = batches["lot-02"]
    duplicated = replace(lot_02, results=(lot_02.results[0], *lot_02.results[:-1]))
    missing = replace(lot_02, results=lot_02.results[:-1])
    unexpected = replace(
        lot_02, results=(*lot_02.results[:-1], replace(lot_02.results[-1], id="S8-999"))
    )

    dup_errors = ex09.validate_correlation(dataset, {**batches, "lot-02": duplicated})
    assert any(error.startswith("duplicated:") for error in dup_errors)
    missing_errors = ex09.validate_correlation(dataset, {**batches, "lot-02": missing})
    assert "responses:27" in missing_errors
    assert any(error.startswith("missing:") for error in missing_errors)
    unexpected_errors = ex09.validate_correlation(dataset, {**batches, "lot-02": unexpected})
    assert "unexpected:S8-999" in unexpected_errors
    assert "missing-batch:lot-02" in ex09.validate_correlation(
        dataset, {"lot-01": batches["lot-01"]}
    )


# --- Regla temporal S11-C3 -------------------------------------------------------------


def test_seven_day_rule_is_an_s11c3_operational_limit():
    assert ex09.OPERATIONAL_LOT_02_LIMIT == timedelta(days=7)
    assert "S11-C3" in ex09.OPERATIONAL_RULE_SOURCE
    assert "not a property of R2" in ex09.OPERATIONAL_RULE_SOURCE
    assert ex09.within_operational_limit(START, START + timedelta(days=7))
    assert not ex09.within_operational_limit(START, START + timedelta(days=7, seconds=1))


# --- Credencial y entorno --------------------------------------------------------------


def test_fingerprint_is_16_hex_and_never_the_key():
    fingerprint = ex09.credential_fingerprint(KEY_LOT_01)
    assert len(fingerprint) == 16
    assert int(fingerprint, 16) >= 0
    assert fingerprint not in KEY_LOT_01
    assert fingerprint != ex09.credential_fingerprint(KEY_LOT_02)


def test_preflight_is_static_and_reports_missing_credential_and_productive_env(ctx):
    (ctx.repo_root / ".env").write_text("GEMINI_API_KEY=\n", encoding="utf-8")
    report = ex09.run_preflight(
        lot_id="lot-01",
        git_sha=GIT_SHA,
        git_clean=True,
        environ={"SUPABASE_SERVICE_ROLE_KEY": "x", "S11_FAKE_AI_ENABLED": "true"},
        repo_root=ctx.repo_root,
        evidence_root=ctx.store.root,
        evidence_mode=MODE,
        mounts_path=ctx.mounts,
    )
    results = {check["check"]: check for check in report["checks"]}

    assert report["result"] == "FAIL"
    assert report["provider_calls"] == 0
    assert results["credential"]["result"] == "FAIL"
    assert results["environment-boundary"]["result"] == "FAIL"
    assert "repo-dotenv-visible" in results["environment-boundary"]["detail"]
    assert "forbidden-env:SUPABASE_SERVICE_ROLE_KEY" in results["environment-boundary"]["detail"]
    assert "enabled-flag:S11_FAKE_AI_ENABLED" in results["environment-boundary"]["detail"]
    for name in ("dataset", "prompt", "batch-partition", "protocol-r2", "retries"):
        assert results[name]["result"] == "PASS"


def test_preflight_passes_with_key_present_and_never_contains_it(ctx):
    report = ex09.run_preflight(
        lot_id="lot-01",
        git_sha=GIT_SHA,
        git_clean=True,
        environ={ex09.PROVIDER_KEY_ENV: KEY_LOT_01},
        repo_root=ctx.repo_root,
        evidence_root=ctx.store.root,
        evidence_mode=MODE,
        mounts_path=ctx.mounts,
    )
    assert report["result"] == "PASS"
    assert KEY_LOT_01 not in json.dumps(report)
    assert report["credential_fingerprint"] == ex09.credential_fingerprint(KEY_LOT_01)


def test_operator_preflight_runs_without_the_key(ctx):
    report = ex09.run_preflight(
        lot_id="lot-01",
        git_sha=GIT_SHA,
        git_clean=True,
        environ={},
        repo_root=ctx.repo_root,
        evidence_root=ctx.store.root,
        evidence_mode=MODE,
        mounts_path=ctx.mounts,
        require_credential=False,
    )
    credential = next(check for check in report["checks"] if check["check"] == "credential")
    assert credential["result"] == "NOT_EVALUATED"
    assert report["result"] == "PASS"


def test_preflight_rejects_tmpfs_evidence_root(ctx):
    ctx.mounts.write_text(f"tmpfs {ctx.store.root} tmpfs rw 0 0\n", encoding="utf-8")
    report = ex09.run_preflight(
        lot_id="lot-01",
        git_sha=GIT_SHA,
        git_clean=True,
        environ={ex09.PROVIDER_KEY_ENV: KEY_LOT_01},
        repo_root=ctx.repo_root,
        evidence_root=ctx.store.root,
        evidence_mode=MODE,
        mounts_path=ctx.mounts,
    )
    assert report["result"] == "FAIL"


# --- Corrida completa y estados --------------------------------------------------------


def test_complete_run_passes_and_is_sealed_only_at_the_end(ctx):
    ctx.lot_01()
    assert not (ctx.run_root / "SHA256SUMS").exists()
    assert not (ctx.run_root / ".s11-finalized").exists()
    assert ctx.payload("ex09-combined") is None
    ctx.lot_02()

    result = ctx.finalize()

    assert result["decision"] == "PASS"
    assert result["manifest_status"] == "COMPLETED"
    assert result["custody_verification"] == "PASS"
    decision = ctx.payload("ex09-decision")
    assert decision["ca4"]["ca4_concordant_cases"] == 28
    assert decision["evidence_mode"] == "LOCAL TEST / NON-EXPERIMENTAL"
    assert decision["allowed_decisions"] == ["PASS", "FAIL", "NOT_DEMONSTRATED"]
    assert (ctx.run_root / "SHA256SUMS").is_file()
    assert (ctx.run_root / ".s11-finalized").is_file()
    assert ctx.store.verify(ctx.run_id) == []
    assert ctx.adapter.calls == [case.id for case in load_dataset().cases]
    manifest = ctx.payload("run-manifest-final")
    assert manifest["configuration"]["experiment_id"] == ex09.experiment_id(MODE)


def test_lot_01_is_never_scored_separately(ctx):
    ctx.start()
    ctx.quota("lot-01")
    summary = ctx.run_lot("lot-01", KEY_LOT_01)

    assert summary["scored"] is False
    assert not any("ca4" in key or "pct" in key for key in summary)
    lot_manifest = ctx.payload("ex09-lot-01-manifest")
    assert lot_manifest["scored"] is False
    batch = ctx.payload("ex09-lot-01-batch")
    assert "ca4_concordance_pct" not in batch
    assert ctx.payload("ex09-combined") is None


@pytest.mark.parametrize(("wrong", "decision"), [(4, "PASS"), (5, "FAIL")])
def test_threshold_is_24_of_28(ctx, dataset, wrong, decision):
    ctx.adapter.wrong = {case.id for case in dataset.cases[-wrong:]}
    ctx.lot_01()
    ctx.lot_02()

    result = ctx.finalize()

    assert result["decision"] == decision
    assert result["manifest_status"] == "COMPLETED"
    assert ctx.payload("ex09-decision")["ca4"]["ca4_concordant_cases"] == 28 - wrong


def test_timeout_makes_the_run_not_demonstrated_and_blocks_lot_02(ctx, dataset):
    ctx.adapter.timeouts = {dataset.cases[3].id}
    ctx.lot_01()
    ctx.clock.advance(days=1)
    ctx.quota("lot-02", first=True)

    with pytest.raises(ex09.Ex09Refusal) as refusal:
        ctx.run_lot("lot-02", KEY_LOT_02)
    assert refusal.value.terminal
    assert "lot-01-invalid" in refusal.value.reasons

    result = ctx.finalize()
    assert result["decision"] == "NOT_DEMONSTRATED"
    assert result["manifest_status"] == "INVALID"
    assert "provider-errors:lot-01:1" in result["reasons"]
    assert ctx.payload("ex09-decision")["ca4"] is None
    assert ctx.payload("ex09-combined") is None


def test_exceeding_seven_days_is_not_demonstrated_never_fail(ctx):
    ctx.lot_01()
    ctx.clock.advance(days=7, hours=1)
    ctx.quota("lot-02", first=True)
    calls = len(ctx.adapter.calls)

    with pytest.raises(ex09.Ex09Refusal) as refusal:
        ctx.run_lot("lot-02", KEY_LOT_02)
    assert refusal.value.terminal
    assert "lot-02-operational-limit-exceeded" in refusal.value.reasons
    assert len(ctx.adapter.calls) == calls

    result = ctx.finalize()
    assert result["decision"] == "NOT_DEMONSTRATED"
    assert "lot-02-not-started-within-s11c3-operational-limit" in result["reasons"]
    assert result["decision"] != "FAIL"


def test_lot_02_waits_for_operator_records_without_calling_the_provider(ctx):
    ctx.start()
    ctx.quota("lot-01")
    ctx.run_lot("lot-01", KEY_LOT_01)
    calls = len(ctx.adapter.calls)
    ctx.clock.advance(days=1)

    with pytest.raises(ex09.Ex09Refusal) as refusal:
        ctx.run_lot("lot-02", KEY_LOT_02)
    assert not refusal.value.terminal
    assert "lot-01-credential-removed-missing" in refusal.value.reasons
    assert "quota-check-missing:lot-02" in refusal.value.reasons

    ctx.close_lot("lot-01", KEY_LOT_01)
    ctx.quota("lot-02", remaining=5, first=True)
    with pytest.raises(ex09.Ex09Refusal) as insufficient:
        ctx.run_lot("lot-02", KEY_LOT_02)
    assert "quota-insufficient:lot-02" in insufficient.value.reasons
    assert len(ctx.adapter.calls) == calls


def test_lot_02_waits_for_a_later_rpd_window(ctx):
    ctx.lot_01()
    ctx.quota("lot-02", first=True)
    calls = len(ctx.adapter.calls)
    with pytest.raises(ex09.Ex09Refusal) as same_window:
        ctx.run_lot("lot-02", KEY_LOT_02)
    assert not same_window.value.terminal
    assert "rpd-window-not-after-lot-01" in same_window.value.reasons
    assert len(ctx.adapter.calls) == calls

    ctx.clock.advance(days=1)
    ctx.quota("lot-02", first=True)
    ctx.run_lot("lot-02", KEY_LOT_02)
    ctx.close_lot("lot-02", KEY_LOT_02)
    assert ctx.finalize()["decision"] == "PASS"


def test_lot_02_rejects_the_lot_01_key_before_calling(ctx):
    ctx.lot_01()
    ctx.clock.advance(days=1)
    ctx.quota("lot-02", first=True)
    calls = len(ctx.adapter.calls)

    with pytest.raises(ex09.Ex09Refusal) as refusal:
        ctx.run_lot("lot-02", KEY_LOT_01)
    assert not refusal.value.terminal
    assert "credential-reused-from-lot-01" in refusal.value.reasons
    assert len(ctx.adapter.calls) == calls


def test_continuity_divergence_is_custodied_and_prevents_every_call(ctx):
    ctx.lot_01()
    ctx.clock.advance(days=1)
    ctx.quota("lot-02", first=True)
    calls = len(ctx.adapter.calls)

    with pytest.raises(ex09.Ex09Refusal) as refusal:
        ctx.run_lot("lot-02", KEY_LOT_02, git_sha="b" * 40)
    assert refusal.value.terminal
    assert any(reason.startswith("identity-divergent:") for reason in refusal.value.reasons)
    assert len(ctx.adapter.calls) == calls
    assert ctx.payload("ex09-continuity")["result"] == "FAIL"

    result = ctx.finalize()
    assert result["decision"] == "NOT_DEMONSTRATED"
    assert "continuity-failed" in result["reasons"]


def test_a_lot_is_never_executed_twice(ctx):
    ctx.lot_01()
    with pytest.raises(ex09.Ex09Refusal) as refusal:
        ctx.run_lot("lot-01", KEY_LOT_02)
    assert refusal.value.terminal
    assert "lot-already-attempted:lot-01" in refusal.value.reasons


def test_tampered_lot_01_evidence_invalidates_the_run(ctx):
    ctx.lot_01()
    batch = ctx.run_root / "raw" / "EX-09" / "ex09-lot-01-batch.json"
    batch.chmod(0o600)
    tampered = batch.read_bytes().replace(b'"priority_match":true', b'"priority_match":0', 1)
    batch.write_bytes(tampered)
    ctx.clock.advance(days=1)
    ctx.quota("lot-02", first=True)

    with pytest.raises(ex09.Ex09Refusal) as refusal:
        ctx.run_lot("lot-02", KEY_LOT_02)
    assert refusal.value.terminal
    assert any(reason.startswith("custody:") for reason in refusal.value.reasons)
    result = ctx.finalize()
    assert result["decision"] == "NOT_DEMONSTRATED"
    assert result["manifest_status"] == "INVALID"
    assert result["custody_verification"] == "FAIL"
    assert any(reason.startswith("custody:sha256:") for reason in result["reasons"])


def test_finalize_refuses_a_continuable_run_unless_abandoned(ctx):
    ctx.lot_01()
    with pytest.raises(ex09.Ex09Refusal) as refusal:
        ctx.finalize()
    assert not refusal.value.terminal
    assert "lot-02-not-executed" in refusal.value.reasons

    result = ctx.finalize(abandon_reason="operador: continuidad no demostrable")
    assert result["decision"] == "NOT_DEMONSTRATED"
    assert any(reason.startswith("abandoned:") for reason in result["reasons"])
    with pytest.raises(ex09.Ex09Error, match="finalizado"):
        ctx.quota("lot-02", first=True)


def test_rpd_window_must_be_the_first_available_one(ctx):
    ctx.lot_01()
    ctx.clock.advance(days=1)
    ctx.quota("lot-02", first=False)
    ctx.clock.advance(days=1)
    ctx.quota("lot-02", first=True)

    with pytest.raises(ex09.Ex09Refusal) as refusal:
        ctx.run_lot("lot-02", KEY_LOT_02)
    assert refusal.value.terminal
    assert "rpd-window-not-first-available" in refusal.value.reasons
    assert ctx.finalize()["decision"] == "NOT_DEMONSTRATED"


# --- Secretos --------------------------------------------------------------------------


def test_key_never_reaches_custody_and_stderr_is_redacted(ctx):
    ctx.adapter.noise = f"debug https://x.test/v1?key={KEY_LOT_01} Authorization: Bearer abc"
    ctx.lot_01()
    ctx.adapter.noise = f"api_key={KEY_LOT_02}"
    ctx.lot_02()
    ctx.finalize()

    content = _all_bytes(ctx.run_root)
    assert KEY_LOT_01.encode() not in content
    assert KEY_LOT_02.encode() not in content
    stderr = (ctx.run_root / "logs" / "EX-09" / "ex09-lot-01-stderr.txt").read_text()
    assert "[REDACTADO]" in stderr
    assert "Bearer abc" not in stderr
    removed = ctx.payload("ex09-lot-01-credential-removed")
    assert removed["credential_fingerprint"] == ex09.credential_fingerprint(KEY_LOT_01)
    assert removed["environment_variable_absent"] is True


def test_credential_removed_refuses_while_the_key_is_still_in_the_environment(ctx):
    ctx.start()
    ctx.quota("lot-01")
    ctx.run_lot("lot-01", KEY_LOT_01)
    with pytest.raises(ex09.Ex09Refusal):
        ctx.record("lot-01", "credential_removed", environ={ex09.PROVIDER_KEY_ENV: KEY_LOT_01})
    ctx.record("lot-01", "credential_removed")
    with pytest.raises(ex09.Ex09Refusal) as mismatch:
        ctx.record("lot-01", "access_revoked", credential_fingerprint_value="0" * 16)
    assert "fingerprint-mismatch:lot-01" in mismatch.value.reasons


def test_missing_key_is_refused_without_persisting_anything(ctx):
    ctx.start()
    ctx.quota("lot-01")
    before = ctx.store.records(ctx.run_id)
    with pytest.raises(ex09.Ex09Refusal) as refusal:
        ctx.run_lot("lot-01", KEY_LOT_01, environ={})
    assert not refusal.value.terminal
    assert ctx.store.records(ctx.run_id) == before


# --- Custodia sin SHA256SUMS entre lotes -----------------------------------------------


def test_rolling_checksums_disabled_recovers_a_failed_finalization(ctx, monkeypatch):
    ctx.lot_01()
    ctx.lot_02()

    def crash(*args, **kwargs):
        raise RuntimeError("fallo sintético LOCAL TEST")

    monkeypatch.setattr(EvidenceStore, "_write_finalized_seal", crash)
    with pytest.raises(RuntimeError, match="LOCAL TEST"):
        ctx.finalize()
    assert not (ctx.run_root / "SHA256SUMS").exists()
    assert not (ctx.run_root / ".s11-finalized").exists()
    monkeypatch.undo()
    assert ctx.store.verify(ctx.run_id) == []

    retry = ctx.finalize()
    assert retry["decision"] == "PASS"
    assert (ctx.run_root / "SHA256SUMS").is_file()


def test_default_evidence_store_keeps_rolling_checksums(ctx):
    store = EvidenceStore(
        ctx.root / "default",
        custody_hmac_key=_CUSTODY_KEY,
        publication_verification_key=_PUBLICATION_KEY,
    )
    assert store.rolling_checksums is True


def test_persist_refuses_payloads_containing_the_key(ctx):
    ctx.start()
    with pytest.raises(EvidencePolicyError):
        ex09._persist(
            ctx.store,
            ctx.run_id,
            "ex09-lot-01-probe",
            {"note": KEY_LOT_01},
            source="local-test",
            secret=KEY_LOT_01,
        )


# --- Lanzador de host ------------------------------------------------------------------


class FakeProcesses:
    def __init__(self, *, containers=""):
        self.calls = []
        self.containers = containers

    def __call__(self, command, **kwargs):
        self.calls.append((tuple(command), kwargs))
        stdout = self.containers if command[-3:] == ("ps", "-a", "-q") else ""
        return type("Completed", (), {"returncode": 0, "stdout": stdout})()


@pytest.fixture
def launcher_env(monkeypatch):
    monkeypatch.setattr(ex09_launcher, "verify_repository", lambda root, sha: None)
    return {
        "PATH": "/usr/bin",
        "S11_CUSTODY_HMAC_KEY": "k" * 32,
        "S11_PUBLICATION_APPROVAL_PUBLIC_KEY": "0" * 64,
        "GEMINI_API_KEY": "must-not-pass",
    }


def test_launcher_passes_the_key_only_to_run_lot_and_by_name(launcher_env):
    processes = FakeProcesses()
    environ = {**launcher_env, ex09_launcher.PROVIDER_KEY_ENV: KEY_LOT_01}

    ex09_launcher.launch(
        "run-lot",
        ["--run-id", "s11-x", "--lot", "lot-01"],
        repo_root=Path("."),
        expected_git_sha=GIT_SHA,
        environ=environ,
        runner=processes,
    )
    command, kwargs = next(call for call in processes.calls if "run" in call[0])
    assert KEY_LOT_01 not in " ".join(command)
    assert ("-e", ex09_launcher.PROVIDER_KEY_ENV) == command[9:11]
    assert command[-2:] == (GIT_SHA, "--git-clean")
    assert kwargs["env"][ex09_launcher.PROVIDER_KEY_ENV] == KEY_LOT_01
    assert "GEMINI_API_KEY" not in kwargs["env"]


def test_launcher_refuses_other_actions_while_the_key_is_exported(launcher_env):
    environ = {**launcher_env, ex09_launcher.PROVIDER_KEY_ENV: KEY_LOT_01}
    with pytest.raises(ex09_launcher.LauncherError, match="sólo run-lot"):
        ex09_launcher.launch(
            "finalize",
            ["--run-id", "s11-x"],
            repo_root=Path("."),
            expected_git_sha=GIT_SHA,
            environ=environ,
            runner=FakeProcesses(),
        )


def test_launcher_verifies_container_absence_for_cleanup(launcher_env):
    processes = FakeProcesses()
    ex09_launcher.launch(
        "record",
        ["--run-id", "s11-x", "--lot", "lot-01", "--event", "cleanup"],
        repo_root=Path("."),
        expected_git_sha=GIT_SHA,
        environ=launcher_env,
        runner=processes,
    )
    assert processes.calls[-1][0][-1] == "--container-absent"

    with pytest.raises(ex09_launcher.LauncherError, match="contenedor"):
        ex09_launcher.launch(
            "record",
            ["--run-id", "s11-x", "--lot", "lot-01", "--event", "cleanup"],
            repo_root=Path("."),
            expected_git_sha=GIT_SHA,
            environ=launcher_env,
            runner=FakeProcesses(containers="abc123\n"),
        )
    with pytest.raises(ex09_launcher.LauncherError, match="lanzador"):
        ex09_launcher.launch(
            "record",
            ["--event", "cleanup", "--container-absent"],
            repo_root=Path("."),
            expected_git_sha=GIT_SHA,
            environ=launcher_env,
            runner=FakeProcesses(),
        )


def test_ephemeral_compose_has_no_env_file_and_no_credentials():
    candidates = [
        Path(__file__).resolve().parents[4] / "infra" / "docker-compose.s11-ex09.yml",
        Path("/s11-source/infra/docker-compose.s11-ex09.yml"),
    ]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        pytest.skip("infra/ no está montado en este contenedor de pruebas")
    compose = path.read_text(encoding="utf-8").split("services:", 1)[1]
    assert "env_file" not in compose
    assert "../backend:/app:ro" in compose
    assert "S11_EX09_PROVIDER_KEY" not in compose
    assert 'GEMINI_API_KEY: ""' in compose
    assert 'S11_FAKE_AI_ENABLED: "false"' in compose
    assert "ports" not in compose
    assert "depends_on" not in compose


# --- Comando de gestión ----------------------------------------------------------------


def _clear_productive_env(monkeypatch):
    for name in (*ex09.FORBIDDEN_ENVIRONMENT, *ex09.DISABLED_FLAGS, ex09.PROVIDER_KEY_ENV):
        monkeypatch.delenv(name, raising=False)


def test_command_preflight_prints_json_and_creates_no_evidence(monkeypatch, capsys):
    from django.core.management import call_command

    _clear_productive_env(monkeypatch)
    call_command("s11_ex09", "preflight", "--lot", "lot-01", "--git-sha", GIT_SHA, "--git-clean")
    report = json.loads(capsys.readouterr().out)

    assert report["provider_calls"] == 0
    assert report["evidence_mode"] == "OFFICIAL"
    checks = {check["check"]: check["result"] for check in report["checks"]}
    assert checks["credential"] == "NOT_EVALUATED"
    for name in ("git", "dataset", "prompt", "batch-partition", "protocol-r2", "retries"):
        assert checks[name] == "PASS"
    assert "run_id" not in report


def test_command_refuses_the_key_outside_run_lot(monkeypatch):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    monkeypatch.setenv(ex09.PROVIDER_KEY_ENV, KEY_LOT_01)
    with pytest.raises(CommandError, match="sólo puede existir en el proceso de run-lot"):
        call_command(
            "s11_ex09", "preflight", "--lot", "lot-01", "--git-sha", GIT_SHA, "--git-clean"
        )
