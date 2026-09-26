"""Ejecutor de EX-09, instancia OpenAI. LOCAL TEST / NON-EXPERIMENTAL.

Ninguna prueba contacta al proveedor: los sockets quedan bloqueados, el cliente y el
adaptador son dobles, y toda la custodia vive en `.s11-test-artifacts/` con
`evidence_mode = "LOCAL TEST / NON-EXPERIMENTAL"`. La clave tiene forma de clave pero
es inventada. Nada de esto es evidencia de EX-09.
"""

import json
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
