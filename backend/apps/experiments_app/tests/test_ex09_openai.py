"""Ejecutor de EX-09, instancia OpenAI. LOCAL TEST / NON-EXPERIMENTAL.

Ninguna prueba contacta al proveedor: los sockets quedan bloqueados, el cliente y el
adaptador son dobles, y toda la custodia vive en `.s11-test-artifacts/` con
`evidence_mode = "LOCAL TEST / NON-EXPERIMENTAL"`. La clave tiene forma de clave pero
es inventada. Nada de esto es evidencia de EX-09.
"""

import hashlib
import inspect
import json
import re
import shutil
import socket
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import pytest

from apps.ai_client_app.adapters.openai import ProviderCall
from apps.ai_client_app.evaluation.dataset import load_dataset
from apps.ai_client_app.evaluation.openai_session import run_openai_session
from apps.ai_client_app.evaluation.scoring import compare_case, failed_case
from apps.experiments_app import ex09_openai, ex09_openai_launcher
from apps.experiments_app.custody_policy import publication_public_key_from_private
from apps.experiments_app.evidence import EvidencePolicyError, EvidenceStore

MODE = ex09_openai.LOCAL_TEST_MODE
GIT_SHA = "a" * 40
KEY = "sk-proj-LOCALtestNOTaREALkeyEX09xxxxxxxxxxxx"
START = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
_CUSTODY_KEY = b"synthetic-custody-test-key-32-bytes"
_PUBLICATION_KEY = publication_public_key_from_private(bytes(range(32)))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("LOCAL TEST / NON-EXPERIMENTAL: la red está prohibida.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture(scope="module")
def dataset():
    return load_dataset()


def _gold_text(case, *, wrong=False, priorities=()):
    priority = case.expected.priority
    if wrong:
        priority = next(p for p in priorities if p != priority)
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


class Clock:
    def __init__(self, start=START):
        self.now = start

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


class FakeClient:
    def __init__(self, *, api_key, timeout_seconds):
        assert timeout_seconds == 60


class FakeAdapter:
    def __init__(self, dataset, *, wrong=(), errors=None):
        self.by_description = {case.description: case for case in dataset.cases}
        self.priorities = dataset.catalog.priorities
        self.wrong = set(wrong)
        self.errors = dict(errors or {})
        self.calls = []

    def __call__(self, *, client, model, reasoning_effort):
        assert (model, reasoning_effort) == ("gpt-6-luna", "low")
        return self

    def classify_with_metadata(self, request):
        case = self.by_description[request.description]
        self.calls.append(case.id)
        if case.id in self.errors:
            raise self.errors[case.id]
        return ProviderCall(
            text=_gold_text(case, wrong=case.id in self.wrong, priorities=self.priorities),
            response_id=f"resp_{case.id}",
            request_id=f"req_{case.id}",
            model="gpt-6-luna",
        )


def _monotonic():
    state = {"now": 0}

    def tick():
        state["now"] += 1_000_000_000
        return state["now"]

    return tick


class Context:
    def __init__(self, root, dataset):
        self.dataset = dataset
        self.repo_root = root / "repo"
        self.repo_root.mkdir(parents=True)
        self.mounts = root / "mounts"
        self.mounts.write_text("/dev/local / ext4 rw 0 0\n", encoding="utf-8")
        self.store = EvidenceStore(
            root / "evidence",
            custody_hmac_key=_CUSTODY_KEY,
            publication_verification_key=_PUBLICATION_KEY,
        )
        self.clock = Clock()
        self.adapter = FakeAdapter(dataset)
        self.run_id = None

    def runner(self):
        return partial(
            run_openai_session,
            client_factory=FakeClient,
            adapter_factory=self.adapter,
            clock=self.clock,
            monotonic_ns=_monotonic(),
        )

    def preflight(self, environ=None, **kwargs):
        return ex09_openai.run_preflight(
            git_sha=kwargs.pop("git_sha", GIT_SHA),
            git_clean=kwargs.pop("git_clean", True),
            environ=environ or {},
            repo_root=self.repo_root,
            evidence_root=self.store.root,
            evidence_mode=MODE,
            mounts_path=self.mounts,
            **kwargs,
        )

    def start(self):
        result = ex09_openai.start_run(
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

    def record(self, event, **kwargs):
        return ex09_openai.record_event(
            self.store,
            run_id=self.run_id,
            event=event,
            recorded_at=self.clock(),
            environ=kwargs.pop("environ", {}),
            **kwargs,
        )

    def limits(self, **overrides):
        values = {
            "tier": "Tier 1",
            "rpm": 500,
            "tpm": 500000,
            "rpd": None,
            "credit_available": True,
            "spend_limit_reached": False,
        }
        return self.record("limits_check", **{**values, **overrides})

    def run(self, *, environ=None, runner=None):
        return ex09_openai.execute_session(
            self.store,
            run_id=self.run_id,
            git_sha=GIT_SHA,
            git_clean=True,
            environ=environ if environ is not None else {ex09_openai.PROVIDER_KEY_ENV: KEY},
            repo_root=self.repo_root,
            clock=self.clock,
            runner=runner or self.runner(),
            evidence_mode=MODE,
            mounts_path=self.mounts,
        )

    def close(self):
        self.record("credential_removed")
        self.record(
            "access_revoked",
            credential_fingerprint_value=ex09_openai.credential_fingerprint(KEY),
        )
        self.record("cleanup", container_absent=True)

    def complete(self):
        self.start()
        self.limits()
        self.run()
        self.close()

    def finalize(self, **kwargs):
        return ex09_openai.finalize_run(
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
        return ex09_openai.load_state(self.store, self.run_id).payload(artifact_type)


@pytest.fixture
def ctx(request, dataset):
    root = Path.cwd() / ".s11-test-artifacts" / f"ex09-openai-{request.node.name}"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    yield Context(root, dataset)
    shutil.rmtree(root, ignore_errors=True)


def _identity():
    identity, _ = ex09_openai.build_identity(git_sha=GIT_SHA, git_clean=True, evidence_mode=MODE)
    return identity


def _valid_session(dataset, *, wrong=()):
    adapter = FakeAdapter(dataset, wrong=wrong)
    session = run_openai_session(
        protocol=ex09_openai.PROTOCOL,
        api_key=KEY,
        client_factory=FakeClient,
        adapter_factory=adapter,
        clock=Clock(),
        monotonic_ns=_monotonic(),
    )
    return json.loads(json.dumps(asdict(session), default=str))


# --- Identidad, protocolo y preflight local --------------------------------------------


def test_identity_binds_every_parameter_that_can_change_the_result():
    identity = _identity()
    assert identity["methodology_version"] == "S11A-v1.1"
    assert identity["protocol_version"] == "s11-ex09-openai-v1"
    assert (identity["provider"], identity["api"], identity["model_name"]) == (
        "openai",
        "responses",
        "gpt-6-luna",
    )
    assert (identity["reasoning_effort"], identity["image_detail"], identity["store"]) == (
        "low",
        "auto",
        False,
    )
    assert identity["request_timeout_seconds"] == 60
    assert identity["retries"] == {
        "sdk_max_retries": 0,
        "script": 0,
        "celery": 0,
        "selective_reruns": 0,
    }
    assert identity["sdk"] == {"package": "openai", "version": "3.19.2"}
    assert identity["dataset_sha256"] == ex09_openai.PREREGISTERED["dataset_sha256"]
    assert identity["prompt_sha256"] == ex09_openai.PREREGISTERED["prompt_sha256"]
    assert identity["schema_sha256"] == ex09_openai.PREREGISTERED["schema_sha256"]
    assert identity["partition"] == {
        "sessions": 1,
        "cases": "1-28",
        "concurrency": 1,
        "inter_request_delay_seconds": 0,
    }
    assert identity["provider_limits"]["status"] == "PARAMETER_TO_VERIFY_AT_PREFLIGHT"
    assert identity["provider_limits"]["minimum_requests"] == 28
    serialized = json.dumps(identity)
    for gemini_rule in ("lot-01", "rpd", "quota", "gemini"):
        assert gemini_rule not in serialized.lower()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_name", "gpt-6-sol"),
        ("provider", "gemini"),
        ("reasoning_effort", "medium"),
        ("request_timeout_seconds", 20),
        ("sdk_max_retries", 2),
        ("store", True),
        ("dataset_sha256", "0" * 64),
        ("prompt_sha256", "0" * 64),
        ("schema_sha256", "0" * 64),
        ("image_detail", "high"),
        ("inter_request_delay_seconds", 20),
    ],
)
def test_protocol_drift_is_detected_field_by_field(field, value):
    drifted = replace(ex09_openai.PROTOCOL, **{field: value})
    assert ex09_openai.check_protocol_frozen(drifted) == [field]
    assert ex09_openai.check_protocol_frozen() == []


def test_local_preflight_is_ready_only_for_the_provider_preflight(ctx):
    report = ctx.preflight()
    checks = {check["check"]: check["result"] for check in report["checks"]}

    assert report["result"] == "PASS"
    assert report["gate"] == "READY_FOR_PROVIDER_PREFLIGHT"
    assert report["ready_for_execution"] is False
    assert report["provider_calls"] == 0
    for name in (
        "git",
        "methodology",
        "protocol-frozen",
        "dataset",
        "cases",
        "prompt",
        "schema",
        "sdk-version",
        "request-shape",
        "image-absence",
        "provider-model",
        "reasoning-effort",
        "image-detail",
        "store",
        "timeout",
        "retries",
        "environment-boundary",
        "evidence-root",
    ):
        assert checks[name] == "PASS", name
    assert checks["credential"] == "NOT_EVALUATED"
    assert checks["provider-limits"] == "NOT_EVALUATED"
    assert set(report["provider_limits"].values()) >= {"PARAMETER_TO_VERIFY_AT_PREFLIGHT"}
    assert "READY_FOR_EXECUTION" not in json.dumps(report)


@pytest.mark.parametrize(
    ("environ", "setup", "failing"),
    [
        ({"OPENAI_API_KEY": "real-looking"}, None, "environment-boundary"),
        ({"GEMINI_API_KEY": "x"}, None, "environment-boundary"),
        ({"AI_CLASSIFICATION_ENABLED": "true"}, None, "environment-boundary"),
        ({}, "dotenv", "environment-boundary"),
        ({}, "tmpfs", "evidence-root"),
        ({}, "dirty", "git"),
    ],
)
def test_local_preflight_is_not_ready_when_isolation_breaks(ctx, environ, setup, failing):
    kwargs = {}
    if setup == "dotenv":
        (ctx.repo_root / ".env").write_text("OPENAI_API_KEY=x\n", encoding="utf-8")
    if setup == "tmpfs":
        ctx.mounts.write_text(f"tmpfs {ctx.store.root.parent} tmpfs rw 0 0\n", encoding="utf-8")
    if setup == "dirty":
        kwargs["git_clean"] = False
    report = ctx.preflight(environ, **kwargs)
    checks = {check["check"]: check["result"] for check in report["checks"]}

    assert report["gate"] == "NOT_READY"
    assert checks[failing] == "FAIL"
    assert "real-looking" not in json.dumps(report)


def test_local_preflight_rejects_a_different_installed_sdk(ctx, monkeypatch):
    from apps.ai_client_app.evaluation import openai_session

    monkeypatch.setattr(openai_session, "installed_sdk_version", lambda package="openai": "3.20.0")
    report = ctx.preflight()
    assert report["gate"] == "NOT_READY"
    assert any(
        check["check"] == "inputs" and check["result"] == "FAIL" for check in report["checks"]
    )


# --- Configuración, correlación, imagen y CA-4 -----------------------------------------


def test_a_valid_session_has_no_errors(dataset):
    assert ex09_openai.session_errors(_valid_session(dataset), identity=_identity()) == []


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("model_requested", "gpt-6-sol", "parameter-divergence:model_requested"),
        ("provider", "gemini", "parameter-divergence:provider"),
        ("reasoning_effort", "medium", "parameter-divergence:reasoning_effort"),
        ("request_timeout_seconds", 20, "parameter-divergence:request_timeout_seconds"),
        ("sdk_max_retries", 2, "parameter-divergence:sdk_max_retries"),
        ("store", True, "parameter-divergence:store"),
        ("dataset_sha256", "0" * 64, "parameter-divergence:dataset_sha256"),
        ("prompt_sha256", "0" * 64, "parameter-divergence:prompt_sha256"),
        ("schema_sha256", "0" * 64, "parameter-divergence:schema_sha256"),
        ("sdk_version", "3.20.0", "parameter-divergence:sdk_version"),
    ],
)
def test_configuration_divergence_invalidates_the_session(dataset, field, value, error):
    session = {**_valid_session(dataset), field: value}
    assert error in ex09_openai.session_errors(session, identity=_identity())


def _correlation_case(session, mutate):
    session = json.loads(json.dumps(session))
    mutate(session)
    return ex09_openai.session_errors(session, identity=_identity())


def test_27_of_28_responses_is_not_demonstrated(dataset):
    def drop_last(session):
        session["cases"].pop()
        session["results"].pop()

    errors = _correlation_case(_valid_session(dataset), drop_last)
    assert "responses:27/28" in errors
    assert any(error.startswith("missing:") for error in errors)


def test_a_duplicate_case_is_not_demonstrated(dataset):
    def duplicate(session):
        session["cases"][1] = {**session["cases"][0], "sequence": 2}
        session["results"][1] = session["results"][0]

    errors = _correlation_case(_valid_session(dataset), duplicate)
    assert any(error.startswith("duplicate:") for error in errors)
    assert any(error.startswith("missing:") for error in errors)
    assert "provider-response-id-duplicated" in errors


def test_an_extra_or_unexpected_response_is_not_demonstrated(dataset):
    def extra(session):
        session["cases"].append({**session["cases"][0], "case_id": "S8-EVAL-099", "sequence": 29})
        session["results"].append({**session["results"][0], "id": "S8-EVAL-099"})

    errors = _correlation_case(_valid_session(dataset), extra)
    assert "responses:29/28" in errors
    assert "unexpected:S8-EVAL-099" in errors
    assert "out-of-range" in errors


def test_an_out_of_order_session_is_not_demonstrated(dataset):
    def swap(session):
        cases, results = session["cases"], session["results"]
        cases[0], cases[1] = cases[1], cases[0]
        results[0], results[1] = results[1], results[0]

    errors = _correlation_case(_valid_session(dataset), swap)
    assert "out-of-order" in errors or "out-of-range" in errors


def test_an_uncorrelated_or_divergent_model_response_is_not_demonstrated(dataset):
    def break_correlation(session):
        session["cases"][4]["provider_request_id"] = None
        session["cases"][5]["model"] = "gpt-6-luna-2026-11-01"

    errors = _correlation_case(_valid_session(dataset), break_correlation)
    assert f"uncorrelated:{dataset.cases[4].id}" in errors
    assert "model-divergent-between-cases" in errors


def test_an_image_in_the_session_is_not_demonstrated(dataset):
    def image(session):
        session["images_sent"] = 1
        session["aborted_reason"] = f"image-present:{dataset.cases[0].id}"
        session["cases"][0]["image_bytes_sent"] = 4

    errors = _correlation_case(_valid_session(dataset), image)
    assert "images-sent:1" in errors
    assert f"aborted:image-present:{dataset.cases[0].id}" in errors


@pytest.mark.parametrize(("wrong", "decision", "concordant"), [(4, "PASS", 24), (5, "FAIL", 23)])
def test_ca4_threshold_is_24_of_28(dataset, wrong, decision, concordant):
    results = []
    for index, case in enumerate(dataset.cases):
        parsed_text = _gold_text(case, wrong=index < wrong, priorities=dataset.catalog.priorities)
        from apps.ai_client_app.parser import parse_classification_response

        results.append(compare_case(case, parse_classification_response(parsed_text)))
    result, ca4 = ex09_openai.decide_ca4(results, dataset=dataset)
    assert result == decision
    assert ca4["ca4_concordant_cases"] == concordant
    assert ca4["total_cases"] == 28
    assert ca4["minimum_concordant_cases"] == 24


def test_28_invalid_answers_are_not_demonstrated_never_fail(dataset):
    results = [failed_case(case, "APITimeoutError") for case in dataset.cases]
    assert ex09_openai.decide_ca4(results, dataset=dataset) == ("NOT_DEMONSTRATED", None)


def test_27_answers_are_not_demonstrated(dataset):
    results = [failed_case(case, "x") for case in dataset.cases][:27]
    assert ex09_openai.decide_ca4(results, dataset=dataset)[0] == "NOT_DEMONSTRATED"


# --- Ciclo de vida custodiado ----------------------------------------------------------


def test_complete_run_is_pass_recorded_under_s11a_v1_1(ctx):
    started = ctx.start()
    assert started["methodology_version"] == "S11A-v1.1"
    ctx.limits()
    summary = ctx.run()
    assert summary["attempted_cases"] == 28
    assert summary["scored"] is False
    assert "ca4" not in json.dumps(summary).lower()
    ctx.close()
    result = ctx.finalize()

    assert result["decision"] == "PASS"
    assert result["manifest_status"] == "COMPLETED"
    assert result["custody_verification"] == "PASS"
    decision = ctx.payload("ex09-openai-decision")
    assert decision["ca4"]["ca4_concordant_cases"] == 28
    assert decision["methodology_version"] == "S11A-v1.1"
    [final_path] = ctx.run_root.rglob("run-manifest.final.json")
    final = json.loads(final_path.read_text())
    assert final["methodology_version"] == "S11A-v1.1"
    assert final["configuration"]["protocol_version"] == "s11-ex09-openai-v1"
    manifest = ctx.payload("ex09-openai-session-manifest")
    for field in (
        "scenario_id",
        "protocol_version",
        "methodology_version",
        "provider",
        "model",
        "sdk_version",
        "run_id",
        "commit_sha",
        "git_clean",
        "dataset_sha256",
        "prompt_sha256",
        "schema_sha256",
        "reasoning_effort",
        "image_detail",
        "timeout_seconds",
        "retry_policy",
        "store",
        "credential_fingerprint",
        "start_timestamp",
        "end_timestamp",
    ):
        assert manifest.get(field) is not None, field
    case = ctx.payload("ex09-openai-session")["cases"][0]
    for field in (
        "case_id",
        "provider_response_id",
        "provider_request_id",
        "model",
        "timestamp_start",
        "timestamp_end",
        "duration_ms",
        "validity",
        "category_slug",
        "institution_slug",
        "priority",
        "error_class",
    ):
        assert field in case, field


def test_complete_run_with_five_wrong_answers_is_fail(ctx, dataset):
    ctx.adapter = FakeAdapter(dataset, wrong={case.id for case in dataset.cases[:5]})
    ctx.complete()
    result = ctx.finalize()
    assert result["decision"] == "FAIL"
    assert result["manifest_status"] == "COMPLETED"


def test_a_provider_error_makes_the_run_not_demonstrated_never_fail(ctx, dataset):
    ctx.adapter = FakeAdapter(dataset, errors={dataset.cases[7].id: RuntimeError("boom")})
    ctx.complete()
    result = ctx.finalize()
    assert result["decision"] == "NOT_DEMONSTRATED"
    assert result["manifest_status"] == "INVALID"
    assert any(reason.startswith(f"invalid:{dataset.cases[7].id}") for reason in result["reasons"])


def test_the_session_waits_for_an_ok_limits_check_without_calling(ctx):
    ctx.start()
    with pytest.raises(ex09_openai.Ex09OpenAiRefusal) as missing:
        ctx.run()
    assert missing.value.terminal is False
    assert "limits-check-missing" in missing.value.reasons

    ctx.limits(rpm=27)
    with pytest.raises(ex09_openai.Ex09OpenAiRefusal, match="limits-insufficient"):
        ctx.run()
    ctx.limits(rpd=10)
    with pytest.raises(ex09_openai.Ex09OpenAiRefusal, match="limits-insufficient"):
        ctx.run()
    ctx.limits(credit_available=False)
    with pytest.raises(ex09_openai.Ex09OpenAiRefusal, match="limits-insufficient"):
        ctx.run()
    assert ctx.adapter.calls == []
    assert not ex09_openai.session_executed(ex09_openai.load_state(ctx.store, ctx.run_id))


def test_limits_values_are_never_invented(ctx):
    ctx.start()
    with pytest.raises(ex09_openai.Ex09OpenAiError):
        ctx.limits(rpm=None)
    with pytest.raises(ex09_openai.Ex09OpenAiError):
        ctx.limits(credit_available=None)


def test_a_session_is_never_executed_twice(ctx):
    ctx.start()
    ctx.limits()
    ctx.run()
    with pytest.raises(ex09_openai.Ex09OpenAiRefusal) as caught:
        ctx.run()
    assert caught.value.terminal is True
    assert ctx.adapter.calls.count(ctx.dataset.cases[0].id) == 1


def test_finalize_refuses_a_continuable_run_unless_abandoned(ctx):
    ctx.start()
    ctx.limits()
    with pytest.raises(ex09_openai.Ex09OpenAiRefusal):
        ctx.finalize()
    result = ctx.finalize(abandon_reason="local test")
    assert result["decision"] == "NOT_DEMONSTRATED"
    assert "session-not-executed" in result["reasons"]


def test_tampered_session_evidence_invalidates_the_run(ctx):
    ctx.complete()
    session_path = next(ctx.run_root.rglob("ex09-openai-session.json"))
    data = json.loads(session_path.read_text())
    data["cases"][0]["priority"] = "tampered"
    session_path.write_text(json.dumps(data))
    result = ctx.finalize()
    assert result["decision"] == "NOT_DEMONSTRATED"


def test_a_historical_version_cannot_open_an_ex09_run(ctx, monkeypatch):
    from apps.experiments_app import manifest

    monkeypatch.setattr(manifest, "METHODOLOGY_VERSION", "S11A-v1.0")
    with pytest.raises(Exception, match="S11A-v1.1|históricos"):
        ctx.start()


# --- Secretos ----------------------------------------------------------------------------


def test_no_artifact_contains_the_key_or_authorization_material(ctx):
    ctx.complete()
    ctx.finalize()
    for path in ctx.run_root.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        assert KEY not in content, path
        assert "sk-" not in content, path
        assert "Authorization" not in content and "Bearer" not in content, path
        assert "OPENAI_API_KEY" not in content, path


def test_credential_removed_refuses_while_the_key_is_still_present(ctx):
    ctx.start()
    ctx.limits()
    ctx.run()
    with pytest.raises(ex09_openai.Ex09OpenAiRefusal, match="still-present"):
        ctx.record("credential_removed", environ={ex09_openai.PROVIDER_KEY_ENV: KEY})


def test_access_revoked_requires_the_matching_fingerprint(ctx):
    ctx.start()
    ctx.limits()
    ctx.run()
    ctx.record("credential_removed")
    with pytest.raises(ex09_openai.Ex09OpenAiRefusal, match="fingerprint-mismatch"):
        ctx.record("access_revoked", credential_fingerprint_value="0" * 16)


def test_missing_key_is_refused_without_persisting_anything(ctx):
    ctx.start()
    ctx.limits()
    before = ctx.store.records(ctx.run_id)
    with pytest.raises(ex09_openai.Ex09OpenAiRefusal, match="missing"):
        ctx.run(environ={})
    assert ctx.store.records(ctx.run_id) == before


def test_persist_refuses_payloads_containing_the_key(ctx):
    ctx.start()
    with pytest.raises(EvidencePolicyError):
        ex09_openai._persist(
            ctx.store, ctx.run_id, "ex09-openai-probe", {"note": KEY}, source="t", secret=KEY
        )


# --- Lanzador, compose y comando -------------------------------------------------------


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
    monkeypatch.setattr(ex09_openai_launcher, "verify_repository", lambda root, sha: None)
    return {
        "PATH": "/usr/bin",
        "S11_CUSTODY_HMAC_KEY": "k" * 32,
        "S11_PUBLICATION_APPROVAL_PUBLIC_KEY": "0" * 64,
        "OPENAI_API_KEY": "must-not-pass",
    }


def test_launcher_passes_the_key_only_to_run_and_by_name(launcher_env):
    processes = FakeProcesses()
    environ = {**launcher_env, ex09_openai_launcher.PROVIDER_KEY_ENV: KEY}
    ex09_openai_launcher.launch(
        "run",
        ["--run-id", "s11-x"],
        repo_root=Path("."),
        expected_git_sha=GIT_SHA,
        environ=environ,
        runner=processes,
    )
    command, kwargs = next(call for call in processes.calls if "run" in call[0])
    assert KEY not in " ".join(command)
    assert ("-e", ex09_openai_launcher.PROVIDER_KEY_ENV) == command[9:11]
    assert command[-3:] == ("--git-sha", GIT_SHA, "--git-clean")
    assert "s11_ex09_openai" in command
    assert kwargs["env"][ex09_openai_launcher.PROVIDER_KEY_ENV] == KEY
    assert "OPENAI_API_KEY" not in kwargs["env"]


def test_launcher_refuses_other_actions_while_the_key_is_exported(launcher_env):
    environ = {**launcher_env, ex09_openai_launcher.PROVIDER_KEY_ENV: KEY}
    for action in ("preflight", "start", "finalize"):
        with pytest.raises(ex09_openai_launcher.LauncherError, match="sólo run"):
            ex09_openai_launcher.launch(
                action,
                [],
                repo_root=Path("."),
                expected_git_sha=GIT_SHA,
                environ=environ,
                runner=FakeProcesses(),
            )


def test_launcher_verifies_container_absence_for_cleanup(launcher_env):
    processes = FakeProcesses()
    ex09_openai_launcher.launch(
        "record",
        ["--run-id", "s11-x", "--event", "cleanup"],
        repo_root=Path("."),
        expected_git_sha=GIT_SHA,
        environ=launcher_env,
        runner=processes,
    )
    assert processes.calls[-1][0][-1] == "--container-absent"
    with pytest.raises(ex09_openai_launcher.LauncherError, match="contenedor"):
        ex09_openai_launcher.launch(
            "record",
            ["--run-id", "s11-x", "--event", "cleanup"],
            repo_root=Path("."),
            expected_git_sha=GIT_SHA,
            environ=launcher_env,
            runner=FakeProcesses(containers="abc123\n"),
        )


def test_ephemeral_compose_has_no_env_file_ports_or_credentials():
    candidates = [
        Path(__file__).resolve().parents[4] / "infra" / "docker-compose.s11-ex09-openai.yml",
        Path("/s11-source/infra/docker-compose.s11-ex09-openai.yml"),
    ]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        pytest.skip("infra/ no está montado en este contenedor de pruebas")
    compose = path.read_text(encoding="utf-8").split("services:", 1)[1]
    assert "env_file" not in compose
    assert "../backend:/app:ro" in compose
    assert "S11_EX09_PROVIDER_KEY" not in compose
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "AI_PROVIDER"):
        assert f'{name}: ""' in compose
    assert 'AI_CLASSIFICATION_ENABLED: "false"' in compose
    assert 'S11_FAKE_AI_ENABLED: "false"' in compose
    assert "ports" not in compose
    assert "depends_on" not in compose


def _clear_productive_env(monkeypatch):
    for name in (
        *ex09_openai.FORBIDDEN_ENVIRONMENT,
        *ex09_openai.DISABLED_FLAGS,
        ex09_openai.PROVIDER_KEY_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


def test_command_preflight_prints_json_and_creates_no_run(monkeypatch, capsys):
    from django.core.management import call_command

    _clear_productive_env(monkeypatch)
    call_command("s11_ex09_openai", "preflight", "--git-sha", GIT_SHA, "--git-clean")
    report = json.loads(capsys.readouterr().out)
    assert report["provider_calls"] == 0
    assert report["evidence_mode"] == "OFFICIAL"
    assert report["ready_for_execution"] is False
    assert "run_id" not in report
    checks = {check["check"]: check["result"] for check in report["checks"]}
    for name in ("methodology", "protocol-frozen", "dataset", "prompt", "schema", "sdk-version"):
        assert checks[name] == "PASS", name


def test_command_refuses_the_key_outside_run(monkeypatch):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    monkeypatch.setenv(ex09_openai.PROVIDER_KEY_ENV, KEY)
    with pytest.raises(CommandError, match="sólo puede existir en el proceso de run"):
        call_command("s11_ex09_openai", "preflight", "--git-sha", GIT_SHA, "--git-clean")


# --- Preflight del proveedor (sin run_id) ----------------------------------------------

KEY_ENV = ex09_openai.PROVIDER_KEY_ENV
#: Lo más favorable que el operador podría atestar. Aun así, TPM no queda demostrado.
FAVORABLE = {
    "tier": "Tier 1",
    "rpm": 500,
    "tpm": 500_000,
    "rpd_absent": True,
    "credit_status": "sufficient",
    "spend_limit": "absent",
    "retention_status": "standard",
}


def _provider_preflight(ctx, environ=None, **attestation):
    return ex09_openai.run_provider_preflight(
        git_sha=GIT_SHA,
        git_clean=True,
        environ=environ or {},
        repo_root=ctx.repo_root,
        evidence_root=ctx.store.root,
        evidence_mode=MODE,
        attestation=ex09_openai.ProviderAttestation(**attestation),
        recorded_at=START,
        mounts_path=ctx.mounts,
    )


def _tree(root):
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "dir"
        )
        for path in root.rglob("*")
    }


def test_provider_preflight_creates_no_run_id_manifest_or_evidence(ctx):
    before = _tree(ctx.store.root)
    report = _provider_preflight(ctx, {KEY_ENV: KEY}, **FAVORABLE)

    assert report["run_id"] is None
    assert report["persisted"] is False
    assert report["local_preflight"]["gate"] == "READY_FOR_PROVIDER_PREFLIGHT"
    assert _tree(ctx.store.root) == before
    assert not list(ctx.store.root.rglob("run-manifest*.json"))
    runs = ctx.store.root / "runs"
    assert not runs.exists() or not any(runs.iterdir())


def test_provider_preflight_keeps_only_the_credential_fingerprint(ctx):
    report = _provider_preflight(ctx, {KEY_ENV: KEY}, **FAVORABLE)
    credential = report["parameters"]["credential"]

    assert credential["status"] == "PASS"
    assert credential["credential_available"] is True
    assert credential["credential_fingerprint"] == ex09_openai.credential_fingerprint(KEY)
    assert re.fullmatch(r"[0-9a-f]{16}", credential["credential_fingerprint"])
    assert credential["validity"] == "NOT_DEMONSTRATED"
    serialized = json.dumps(report)
    for forbidden in (KEY, "Bearer", "Authorization", "OPENAI_API_KEY="):
        assert forbidden not in serialized


def test_provider_preflight_without_a_credential_is_not_demonstrated(ctx):
    report = _provider_preflight(ctx, {}, **FAVORABLE)
    credential = report["parameters"]["credential"]

    assert credential["credential_available"] is False
    assert credential["credential_fingerprint"] is None
    assert credential["status"] == "NOT_DEMONSTRATED"
    assert report["provider_preflight"] == "NOT_DEMONSTRATED"
    assert report["ready_for_execution"] is False
    assert report["run_id"] is None


def test_unattested_values_stay_unknown_and_never_pass(ctx):
    report = _provider_preflight(ctx, {KEY_ENV: KEY})
    parameters = report["parameters"]

    for name in ("credit", "rpm", "rpd", "tpm", "spend_limit", "retention"):
        assert parameters[name]["status"] == "UNKNOWN", name
    assert parameters["rpd"]["limit"] == "unknown"
    assert parameters["tpm"]["limit"] == "unknown"
    assert report["tier"] == "UNKNOWN"
    assert report["failed"] == []
    assert set(report["pending"]) == {
        "credit",
        "rpm",
        "rpd",
        "tpm",
        "spend_limit",
        "retention",
    }
    assert report["provider_preflight"] == "NOT_DEMONSTRATED"
    assert report["ready_for_execution"] is False
    assert report["gate"] == "NOT_READY_FOR_EXECUTION"


@pytest.mark.parametrize(("rpm", "status"), [(27, "FAIL"), (28, "PASS")])
def test_rpm_must_admit_the_28_requests(ctx, rpm, status):
    report = _provider_preflight(ctx, {KEY_ENV: KEY}, **{**FAVORABLE, "rpm": rpm})

    assert report["parameters"]["rpm"]["status"] == status
    assert report["ready_for_execution"] is False
    if status == "FAIL":
        assert report["provider_preflight"] == "FAIL"
        assert "rpm" in report["failed"]


def test_an_absent_rpd_is_recorded_as_absent_never_as_a_number(ctx):
    report = _provider_preflight(ctx, {KEY_ENV: KEY}, **FAVORABLE)
    rpd = report["parameters"]["rpd"]

    assert rpd == {"status": "ABSENT", "limit": "absent", "value": None, "minimum": 28}
    assert "rpd" not in report["failed"] + report["pending"]
    silent = _provider_preflight(ctx, {KEY_ENV: KEY}, **{**FAVORABLE, "rpd_absent": False})
    assert silent["parameters"]["rpd"]["status"] == "UNKNOWN"
    assert "rpd" in silent["pending"]


@pytest.mark.parametrize(("rpd", "status"), [(27, "FAIL"), (28, "PASS")])
def test_an_applicable_rpd_must_admit_the_28_requests(ctx, rpd, status):
    attestation = {**FAVORABLE, "rpd_absent": False, "rpd": rpd}
    report = _provider_preflight(ctx, {KEY_ENV: KEY}, **attestation)

    assert report["parameters"]["rpd"]["status"] == status
    assert report["parameters"]["rpd"]["limit"] == "present"
    assert report["ready_for_execution"] is False
    assert ("rpd" in report["failed"]) is (status == "FAIL")


#: Conteos exactos inyectados sólo para ejercitar la regla. Son ficticios: bajo
#: `s11-ex09-openai-v1` no existe un mecanismo legítimo para obtenerlos antes del run.
EXACT_INPUT = [1_000] * 27 + [1_500]


def _decide(tpm, input_tokens=None, **overrides):
    return ex09_openai.evaluate_provider_preflight(
        local={"gate": ex09_openai.READY_FOR_PROVIDER_PREFLIGHT},
        attestation=ex09_openai.ProviderAttestation(**{**FAVORABLE, "tpm": tpm, **overrides}),
        credential_available=True,
        credential_fingerprint_value=ex09_openai.credential_fingerprint(KEY),
        input_payload=None,
        input_tokens=input_tokens,
    )


def test_an_unknown_tpm_limit_stays_unknown(ctx):
    report = _provider_preflight(ctx, {KEY_ENV: KEY}, **{**FAVORABLE, "tpm": None})
    tpm = report["parameters"]["tpm"]

    assert tpm["status"] == "UNKNOWN"
    assert tpm["limit"] == "unknown"
    assert report["ready_for_execution"] is False
    # Ni con tokens exactos: sin límite atestado no hay comparación posible.
    assert _decide(None, EXACT_INPUT)["parameters"]["tpm"]["status"] == "UNKNOWN"


def test_a_known_tpm_without_exact_input_tokens_is_not_demonstrated(ctx):
    before = _tree(ctx.store.root)
    report = _provider_preflight(ctx, {KEY_ENV: KEY}, **FAVORABLE)
    tpm = report["parameters"]["tpm"]

    assert report["tpm_policy"] == "TPM_STRICT_DEMONSTRATED_MAXIMUM"
    assert tpm["policy"] == "TPM_STRICT_DEMONSTRATED_MAXIMUM"
    assert tpm["limit"] == "present"
    assert tpm["value"] == 500_000
    assert tpm["status"] == "NOT_DEMONSTRATED"
    assert tpm["input_tokens"]["status"] == "UNAVAILABLE"
    assert "/v1/responses/input_tokens" in tpm["input_tokens"]["reason"]
    assert tpm["demonstrable_minimum_tokens"] is None
    # Todo lo demás atestado a favor: TPM es lo único pendiente y no hay run.
    assert report["failed"] == []
    assert report["pending"] == ["tpm"]
    assert report["provider_preflight"] == "NOT_DEMONSTRATED"
    assert report["ready_for_execution"] is False
    assert report["run_id"] is None
    assert _tree(ctx.store.root) == before


def test_a_single_request_above_the_tpm_limit_is_fail():
    report = _decide(1_499, EXACT_INPUT)

    assert report["parameters"]["tpm"]["status"] == "FAIL"
    assert report["parameters"]["tpm"]["demonstrable_minimum_tokens"] == 1_500
    assert "tpm" in report["failed"]
    assert report["provider_preflight"] == "FAIL"
    # La entrada total (28 500) supera el límite, pero la sesión puede repartirse en
    # varios minutos: eso no demuestra un fallo.
    spread = _decide(1_500, EXACT_INPUT)["parameters"]["tpm"]
    assert spread["status"] == "COMPATIBLE_MINIMUM_ONLY"


def test_a_tpm_above_the_demonstrable_minimum_is_compatible_minimum_only():
    tpm = _decide(500_000, EXACT_INPUT)["parameters"]["tpm"]

    assert tpm["status"] == "COMPATIBLE_MINIMUM_ONLY"
    assert tpm["input_tokens"] == {"status": "EXACT", "total": 28_500, "max_request": 1_500}
    assert tpm["session_maximum_tokens"] is None


def test_minimum_compatibility_never_passes_the_gate():
    report = _decide(500_000, EXACT_INPUT)

    assert report["parameters"]["tpm"]["status"] == "COMPATIBLE_MINIMUM_ONLY"
    assert report["failed"] == []
    assert report["pending"] == ["tpm"]
    assert report["provider_preflight"] == "NOT_DEMONSTRATED"
    assert report["ready_for_execution"] is False
    assert report["gate"] == "NOT_READY_FOR_EXECUTION"


def test_output_and_reasoning_are_unbounded_under_the_frozen_protocol():
    assert ex09_openai.PROTOCOL_MAX_OUTPUT_TOKENS is None
    tpm = _decide(500_000, EXACT_INPUT)["parameters"]["tpm"]

    assert tpm["output_bound"] == {
        "max_output_tokens": None,
        "status": "UNBOUNDED",
        "covers": "visible output and reasoning tokens",
    }


def test_the_frozen_protocol_sends_no_max_output_tokens(dataset):
    assert "max_output_tokens" not in ex09_openai.PROTOCOL.fields()
    assert "max_output_tokens" not in ex09_openai.PREREGISTERED
    assert "max_output_tokens" in ex09_openai._FORBIDDEN_REQUEST_FIELDS
    captured = list(ex09_openai._captured_requests(dataset))
    assert len(captured) == 28
    for case, _request, params, failure in captured:
        assert failure is None, case.id
        assert "max_output_tokens" not in params, case.id


def test_the_strict_policy_passes_only_a_bounded_session_maximum():
    # Un protocolo hipotético con tope de salida, no s11-ex09-openai-v1.
    bounded = ex09_openai.evaluate_tpm(
        tpm_limit=500_000, input_tokens=EXACT_INPUT, max_output_tokens=10_000
    )
    assert bounded["session_maximum_tokens"] == 28_500 + 28 * 10_000
    assert bounded["status"] == "PASS"
    above = ex09_openai.evaluate_tpm(
        tpm_limit=300_000, input_tokens=EXACT_INPUT, max_output_tokens=10_000
    )
    assert above["status"] == "NOT_DEMONSTRATED"


@pytest.mark.parametrize(
    ("input_tokens", "max_output_tokens"),
    [
        (EXACT_INPUT[:27], None),
        ([0] + EXACT_INPUT[1:], None),
        ([True] + EXACT_INPUT[1:], None),
        (EXACT_INPUT, 0),
    ],
)
def test_malformed_token_counts_are_rejected(input_tokens, max_output_tokens):
    with pytest.raises(ex09_openai.Ex09OpenAiError):
        ex09_openai.evaluate_tpm(
            tpm_limit=500_000, input_tokens=input_tokens, max_output_tokens=max_output_tokens
        )


@pytest.mark.parametrize(("payload_bytes", "tpm"), [(10**12, 1), (1, 10**9)])
def test_bytes_are_never_converted_to_tokens(ctx, monkeypatch, payload_bytes, tpm):
    parameters = set(inspect.signature(ex09_openai.evaluate_tpm).parameters)
    assert parameters == {"tpm_limit", "input_tokens", "max_output_tokens", "total_requests"}
    monkeypatch.setattr(
        ex09_openai,
        "session_input_measure",
        lambda dataset: {
            "requests": 28,
            "input_utf8_bytes_total": payload_bytes,
            "input_utf8_bytes_max_request": payload_bytes,
            "unit": "UTF-8 bytes, not tokens",
            "used_in_tpm_evaluation": False,
        },
    )
    tpm_report = _provider_preflight(ctx, {KEY_ENV: KEY}, **{**FAVORABLE, "tpm": tpm})[
        "parameters"
    ]["tpm"]

    # Ni un volumen absurdo produce FAIL ni uno mínimo produce PASS.
    assert tpm_report["status"] == "NOT_DEMONSTRATED"
    assert tpm_report["input_payload"]["input_utf8_bytes_total"] == payload_bytes
    assert tpm_report["input_payload"]["used_in_tpm_evaluation"] is False


@pytest.mark.parametrize(
    ("field", "value", "status", "outcome"),
    [
        ("credit_status", "unknown", "UNKNOWN", "NOT_DEMONSTRATED"),
        ("credit_status", "insufficient", "FAIL", "FAIL"),
        ("spend_limit", "unknown", "UNKNOWN", "NOT_DEMONSTRATED"),
        ("spend_limit", "reached", "FAIL", "FAIL"),
        ("spend_limit", "compatible", "PASS", "NOT_DEMONSTRATED"),
        ("retention_status", "unknown", "UNKNOWN", "NOT_DEMONSTRATED"),
        ("retention_status", "extended", "BLOCKED", "FAIL"),
        ("retention_status", "zero-data-retention", "PASS", "NOT_DEMONSTRATED"),
    ],
)
def test_credit_spend_and_retention_gate_the_preflight(ctx, field, value, status, outcome):
    report = _provider_preflight(ctx, {KEY_ENV: KEY}, **{**FAVORABLE, field: value})
    name = {"credit_status": "credit", "spend_limit": "spend_limit"}.get(field, "retention")

    assert report["parameters"][name]["status"] == status
    assert report["provider_preflight"] == outcome
    assert report["ready_for_execution"] is False


def test_the_model_is_configured_but_its_availability_is_not_demonstrated(ctx):
    report = _provider_preflight(ctx, {KEY_ENV: KEY}, **FAVORABLE)

    assert report["model"]["configured_model"] == "gpt-6-luna"
    assert report["model"]["availability"] == "NOT_DEMONSTRATED"
    assert report["provider_config"]["configured_model"] == "gpt-6-luna"
    assert report["provider_config"]["reasoning_effort"] == "low"
    assert report["provider_config"]["store"] is False
    assert report["provider_config"]["request_timeout_seconds"] == 60
    assert report["provider_config"]["sdk_max_retries"] == 0
    assert report["sdk_version"] == "3.19.2"
    assert "model" not in report["parameters"]


def test_provider_preflight_sends_no_classification_request(ctx, monkeypatch):
    from openai.resources.responses import Responses
    from openai.resources.responses.input_tokens import InputTokens

    def forbidden(*args, **kwargs):
        raise AssertionError("El preflight del proveedor no puede llamar al proveedor.")

    monkeypatch.setattr(Responses, "create", forbidden)
    monkeypatch.setattr(InputTokens, "count", forbidden)
    monkeypatch.setattr(ex09_openai, "run_openai_session", forbidden)
    report = _provider_preflight(ctx, {KEY_ENV: KEY}, **FAVORABLE)

    assert report["provider_calls"] == 0
    assert report["classification_requests"] == 0
    assert ctx.adapter.calls == []


@pytest.mark.parametrize(
    "attestation",
    [
        {"rpd": 100, "rpd_absent": True},
        {"rpm": -1},
        {"rpm": True},
        {"tpm": "500000"},
        {"tier": "  "},
        {"credit_status": "yes"},
        {"spend_limit": "none"},
        {"retention_status": "zdr"},
    ],
)
def test_malformed_attestations_are_rejected(attestation):
    with pytest.raises(ex09_openai.Ex09OpenAiError):
        ex09_openai.ProviderAttestation(**attestation)


def test_provider_preflight_leaves_existing_evidence_untouched(ctx):
    ctx.complete()
    ctx.finalize()
    before = _tree(ctx.store.root)

    _provider_preflight(ctx, {KEY_ENV: KEY}, **FAVORABLE)

    assert _tree(ctx.store.root) == before


#: Blobs de `73e5037`. La preparación Gemini es histórica y no se modifica.
_GEMINI_EXECUTOR_SHA256 = {
    "apps/experiments_app/ex09.py": (
        "be227f2d94d649fbcb41727471bcd3dff2794195d8914b480cf3109c98628be3"
    ),
    "apps/experiments_app/ex09_launcher.py": (
        "6f17bda8c6b4f88a35344d02ebb3adcae4c30ee02447cdc24da459e65e15c16d"
    ),
    "apps/experiments_app/management/commands/s11_ex09.py": (
        "5480dc86cd42cd5c01d8f8303d6559d044cea0e62fe0f3b07efd7c7204c7ed57"
    ),
    "scripts/s11_ex09.py": "2ac48f1f1e0217dc6ad6f34a418ab596c4447735d1711dcddb5bcbf2814f9647",
}


def test_the_gemini_executor_is_unchanged():
    backend = Path(__file__).resolve().parents[3]
    for relative, expected in _GEMINI_EXECUTOR_SHA256.items():
        digest = hashlib.sha256((backend / relative).read_bytes()).hexdigest()
        assert digest == expected, relative
    from apps.experiments_app import ex09_launcher

    assert "provider-preflight" not in ex09_launcher.ACTIONS


def test_provider_preflight_neither_opens_nor_admits_a_run(ctx):
    from apps.experiments_app.runtime import valid_run_id

    report = _provider_preflight(ctx, {KEY_ENV: KEY}, **FAVORABLE)
    started = ctx.start()

    assert valid_run_id(started["run_id"])
    assert started["methodology_version"] == "S11A-v1.1"
    assert started["identity_sha256"] == report["identity_sha256"]
    # El run conserva su contrato: exige su propio limits_check custodiado.
    with pytest.raises(ex09_openai.Ex09OpenAiRefusal) as missing:
        ctx.run()
    assert missing.value.terminal is False
    assert "limits-check-missing" in missing.value.reasons
    assert ctx.adapter.calls == []

    ctx.limits()
    summary = ctx.run()
    assert summary["attempted_cases"] == 28


def test_launcher_passes_the_key_to_provider_preflight_only_by_name(launcher_env):
    processes = FakeProcesses()
    environ = {**launcher_env, KEY_ENV: KEY}
    ex09_openai_launcher.launch(
        "provider-preflight",
        ["--rpm", "500"],
        repo_root=Path("."),
        expected_git_sha=GIT_SHA,
        environ=environ,
        runner=processes,
    )
    command, kwargs = next(call for call in processes.calls if "provider-preflight" in call[0])
    assert KEY not in " ".join(command)
    assert ("-e", KEY_ENV) == command[9:11]
    assert command[-3:] == ("--git-sha", GIT_SHA, "--git-clean")
    assert kwargs["env"][KEY_ENV] == KEY
    assert "OPENAI_API_KEY" not in kwargs["env"]


def test_launcher_runs_provider_preflight_without_a_key(launcher_env):
    processes = FakeProcesses()
    ex09_openai_launcher.launch(
        "provider-preflight",
        [],
        repo_root=Path("."),
        expected_git_sha=GIT_SHA,
        environ=launcher_env,
        runner=processes,
    )
    command, kwargs = processes.calls[-1]
    assert "provider-preflight" in command
    assert "-e" not in command
    assert KEY_ENV not in kwargs["env"]


def test_command_provider_preflight_accepts_the_key_and_creates_no_run(monkeypatch, capsys):
    from django.core.management import call_command

    _clear_productive_env(monkeypatch)
    monkeypatch.setenv(KEY_ENV, KEY)
    call_command(
        "s11_ex09_openai",
        "provider-preflight",
        "--tier",
        "Tier 1",
        "--rpm",
        "500",
        "--rpd-absent",
        "--credit-status",
        "sufficient",
        "--git-sha",
        GIT_SHA,
        "--git-clean",
    )
    output = capsys.readouterr().out
    assert KEY not in output
    report = json.loads(output)
    assert report["evidence_mode"] == "OFFICIAL"
    assert report["run_id"] is None
    assert report["provider_calls"] == 0
    assert report["parameters"]["credential"]["credential_available"] is True
    assert report["parameters"]["rpm"]["status"] == "PASS"
    assert report["parameters"]["rpd"]["status"] == "ABSENT"
    assert report["parameters"]["tpm"]["status"] == "UNKNOWN"
    assert report["ready_for_execution"] is False


def test_command_provider_preflight_without_a_key_is_not_demonstrated(monkeypatch, capsys):
    from django.core.management import call_command

    _clear_productive_env(monkeypatch)
    call_command("s11_ex09_openai", "provider-preflight", "--git-sha", GIT_SHA, "--git-clean")
    report = json.loads(capsys.readouterr().out)
    credential = report["parameters"]["credential"]
    assert credential["credential_available"] is False
    assert credential["status"] == "NOT_DEMONSTRATED"
    assert "credential" in report["pending"]
    assert report["run_id"] is None
    assert report["ready_for_execution"] is False
