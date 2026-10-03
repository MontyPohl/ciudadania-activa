"""EX-10 fase 2B-2: preflight operacional. LOCAL TEST / NON-EXPERIMENTAL.

Dobles para Git, health, broker y worker; base de tests real donde el preflight lee.
Nunca se usa el stack S11, `var/s11-runs`, un proveedor ni un run: no hay carga, ni
`run_id`, ni POST a `/api/v1/reports/`, ni tareas publicadas. Los tests de proceso nuevo
crean y borran su propia base vacía `ciudadania_s11_fresh_*` en el Postgres de tests.
"""

import copy
import dataclasses
import fnmatch
import http.server
import io
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import psycopg
import pytest
from celery.app.task import Task
from celery.worker.control import Panel
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError, connection, transaction
from django.test.utils import CaptureQueriesContext
from psycopg import sql
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.admin_app.models import Category
from apps.ai_client_app.tasks import classify_report
from apps.experiments_app import ex10_fixture, ex10_load, runtime
from apps.experiments_app import ex10_preflight as pf
from apps.experiments_app import ex10_state as st
from apps.experiments_app import ex10_worker_probe as wp
from apps.experiments_app.canonical import canonical_json_bytes
from apps.experiments_app.isolation import initialize_object_root
from apps.experiments_app.management.commands import s11_ex10_preflight as command_module
from apps.reports_app.models import Report

User = get_user_model()
R = pf.CheckResult
SHA = "a" * 40
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
ENDPOINT = {"scheme": "redis", "host": "redis", "db": 11, "matches_protocol": True}
DECLARED_DB = "ciudadania_s11_unit"
CANARY = "canary-value-7f3a"


# --- Dobles ------------------------------------------------------------------------------------


COMPLETE = pf.SourceCompleteness(
    git_dir="directory",
    toplevel=True,
    shallow=False,
    sparse=False,
    hidden_index_entries=0,
    local_excludes=False,
    missing_top_level=0,
    missing_required=0,
)


class FakeRepo:
    def __init__(self, head=SHA, *, source=COMPLETE, dirty=0, ancestors=True, fail=None):
        self._head, self.source, self.dirty = head, source, dirty
        self.ancestors, self.fail, self.files = ancestors, fail, {}

    def _maybe_fail(self, name):
        if self.fail == name:
            raise pf.RepositoryUnavailable("git-command-failed")

    def completeness(self):
        self._maybe_fail("completeness")
        return self.source

    def head(self):
        self._maybe_fail("head")
        return self._head

    def dirty_entries(self):
        self._maybe_fail("dirty")
        return self.dirty

    def is_ancestor(self, sha):
        return self.ancestors if isinstance(self.ancestors, bool) else sha in self.ancestors

    def read(self, relative):
        if relative in self.files:
            return self.files[relative]
        from django.conf import settings

        return (Path(settings.BASE_DIR) / relative.removeprefix("backend/")).read_bytes()


class FakeBrokerRedis:
    """Un DB lógico de Redis: clave → (tipo, valor, pttl). `FLUSHDB` nunca debe llegar."""

    def __init__(self):
        self.data, self.down, self.calls = {}, False, []

    def put(self, key, kind, value, pttl=-1):
        self.data[key] = (kind, value, pttl)

    def _up(self, name):
        self.calls.append(name)
        if self.down:
            raise RedisConnectionError("fake redis down")

    def _len(self, key, kind):
        stored = self.data.get(key.decode() if isinstance(key, bytes) else key)
        return 0 if stored is None or stored[0] != kind else len(stored[1])

    def ping(self):
        self._up("ping")
        return True

    def llen(self, key):
        self._up("llen")
        return self._len(key, "list")

    def hlen(self, key):
        self._up("hlen")
        return self._len(key, "hash")

    def zcard(self, key):
        self._up("zcard")
        return self._len(key, "zset")

    def scan_iter(self, match=None, count=None):
        self._up("scan_iter")
        return iter([k.encode() for k in sorted(self.data) if fnmatch.fnmatchcase(k, match or "*")])

    def type(self, key):
        self._up("type")
        key = key.decode() if isinstance(key, bytes) else key
        return (self.data[key][0] if key in self.data else "none").encode()

    def pttl(self, key):
        self._up("pttl")
        key = key.decode() if isinstance(key, bytes) else key
        return self.data[key][2] if key in self.data else -2

    def lrange(self, key, start, end):
        self._up("lrange")
        key = key.decode() if isinstance(key, bytes) else key
        return list(self.data[key][1])[start : end + 1]

    def flushdb(self, *args, **kwargs):
        raise AssertionError("el preflight nunca debe vaciar el broker")


class FakeHealth:
    host = "backend"

    def __init__(self, status=200, body=None, error=None):
        self.status, self.error, self.calls = status, error, 0
        self.body = (
            body
            if body is not None
            else json.dumps(
                {"status": "ok", "version": "0.1.0", "db": True, "redis": True}
            ).encode()
        )

    def get(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.status, self.body


def worker_reply(nonce, node="celery@worker-1", **changes):
    payload = {
        "schema_version": wp.PROBE_SCHEMA_VERSION,
        "nonce": nonce,
        "node": node,
        "process": {"hostname": "worker-1", "pid": 4242},
        "pool": {"implementation": "celery.concurrency.prefork:TaskPool", "max_concurrency": 2},
        "task_always_eager": False,
        "broker": dict(ENDPOINT),
        "result_backend": dict(ENDPOINT),
        "fake_ai_enabled": True,
        "ai_adapter": ex10_load.AI_MODEL_NAME,
        "classify_task_registered": True,
        "versions": {"celery": "5.4.0", "kombu": "5.6.2"},
    }
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            payload[key] = {**payload[key], **value}
        else:
            payload[key] = value
    return {node: payload}


class FakeControl:
    """Sólo `probe`: si el preflight intentara purgar o publicar, no existe el método."""

    def __init__(self, *rounds, error=None):
        self.rounds, self.error, self.calls = rounds, error, []

    def probe(self, nonce, timeout):
        self.calls.append((nonce, timeout))
        if self.error is not None:
            raise self.error
        builder = self.rounds[len(self.calls) - 1] if self.rounds else None
        return builder(nonce) if builder else [worker_reply(nonce)]


def api_config(**changes):
    return dataclasses.replace(
        pf.ApiConfig(
            task_always_eager=False,
            broker=dict(ENDPOINT),
            broker_same_as_approved=True,
            result_backend=dict(ENDPOINT),
            result_same_as_approved=True,
            approved=dict(ENDPOINT),
            fake_ai_enabled=True,
            ai_adapter=ex10_load.AI_MODEL_NAME,
            classify_task_registered=True,
        ),
        **changes,
    )


READY_CHECKS = (
    "required_categories_active",
    "users_total",
    "citizen_profiles",
    "fixture_actors",
    "celery_queue_length",
    "residual_objects",
)


def state_report(**results):
    return st.StateReport(
        st.StateName.READY_STATE,
        tuple(
            st.StateCheck(name, 0, 0, results.get(name, st.CheckStatus.PASS))
            for name in READY_CHECKS
        ),
    )


def fake_fixture_result(specs):
    return ex10_fixture.FixtureResult(
        ex10_fixture.FixtureOutcome.PREPARED,
        tuple(
            ex10_fixture.ActorOutcome(
                spec.alias,
                str(spec.user_id),
                str(uuid.uuid5(uuid.NAMESPACE_URL, spec.alias)),
                ex10_fixture.ActorResult.ALREADY_MATCHED,
            )
            for spec in specs
        ),
        None,
    )


def fake_issue_tokens(result):
    return ex10_fixture.EphemeralTokens({a.alias: f"hdr.{a.alias}.sig" for a in result.actors})


def fake_verify_tokens(tokens, result, now):
    return tuple(ex10_fixture.TokenCheck(a.alias, True, True, True) for a in result.actors)


def raiser(exc):
    def raise_it(*args, **kwargs):
        raise exc

    return raise_it


def nonces():
    counter = iter(range(1, 1000))
    return lambda: f"{next(counter):032x}"


@pytest.fixture
def stack(settings, tmp_path):
    objects = tmp_path / "objects"
    objects.mkdir()
    (objects / st.OBJECT_ROOT_MARKER).write_text("S11 isolated synthetic objects only\n")
    for name in ("runs", "backup", "restore", "source"):
        (tmp_path / name).mkdir()
    settings.S11_ARTIFACT_ROOT = tmp_path / "runs"
    settings.S11_BACKUP_ROOT = tmp_path / "backup"
    settings.S11_RESTORE_ROOT = tmp_path / "restore"
    settings.S11_OBJECT_ROOT = objects
    settings.REDIS_URL = "redis://redis:6379/11"
    redis = FakeBrokerRedis()
    environment = st.ApprovedEnvironment(DECLARED_DB, objects, redis)
    repo, health, control = FakeRepo(), FakeHealth(), FakeControl()
    probes = pf.Probes(
        repository=lambda root: repo,
        approve_environment=lambda: environment,
        database_identity=lambda: {"database": True, "user": True, "host": True, "port": True},
        settings_module=lambda: "config.settings.test",
        api_config=api_config,
        throttle_config=lambda: pf.ThrottleConfig("5/hour", "report_create", True),
        health=health,
        inspect_ready_state=lambda env: state_report(),
        category_ids=lambda slugs: {
            slug: str(uuid.uuid5(uuid.NAMESPACE_URL, slug)) for slug in slugs
        },
        fixture_result=fake_fixture_result,
        issue_tokens=fake_issue_tokens,
        verify_tokens=fake_verify_tokens,
        worker_control=control,
        clock=lambda: NOW,
        nonce=nonces(),
    )
    options = pf.PreflightOptions(expected_git_sha=SHA, source_root=tmp_path / "source")
    return SimpleNamespace(
        probes=probes,
        options=options,
        redis=redis,
        repo=repo,
        health=health,
        control=control,
        environment=environment,
        root=tmp_path,
    )


def run(stack):
    return pf.run_preflight(stack.options, stack.probes)


def checks(report):
    return {check.check: check for check in report.checks}


def result_of(report, name):
    return checks(report)[name].result


def plan_order(report):
    """Nombres emitidos con `ready_state:*` plegado y sin los `<paso>_aborted`."""
    names = []
    for check in report.checks:
        name = "ready_state" if check.check.startswith("ready_state:") else check.check
        if not name.endswith("_aborted") and (not names or names[-1] != name):
            names.append(name)
    return names


REPOSITORY_DEPENDENTS = (
    "repository_head",
    "repository_clean",
    "repository_running_source",
    "protocol_decision_commits",
)
WORKER_CHECKS = (
    "broker_dynamic_before",
    "broker_classification_before",
    "worker_probe_1",
    "worker_probe_2",
    "worker_config",
    "worker_stability",
    "broker_dynamic_after",
    "broker_classification_after",
)
ISOLATION_DEPENDENTS = (
    "isolation_database_connected",
    "isolation_redis",
    "isolation_storage_roots",
    "health",
    "health_components",
    "ready_state",
    "plan_multipart_bodies",
    *WORKER_CHECKS,
)


# --- Preflight perfecto e invariantes ------------------------------------------------------------


def test_a_perfect_stack_is_ready_for_executor_integration(stack):
    report = run(stack)
    assert report.result is R.PASS, [c for c in report.checks if c.result is not R.PASS]
    assert (report.gate, report.exit_code) == (pf.Gate.READY_FOR_EXECUTOR_INTEGRATION, 0)
    assert not [c for c in report.checks if c.result is R.NOT_RUN]
    assert stack.health.calls == 1 and len(stack.control.calls) == pf.PROBE_ROUNDS
    payload = report.to_json()
    assert (payload["health_requests"], payload["control_probes"]) == (1, 2)
    assert payload["git_sha"] == SHA and payload["checked_at"] == "2026-09-28T12:00:00Z"


@pytest.mark.parametrize(
    "breakage",
    [
        None,
        lambda s: setattr(s.repo, "_head", "b" * 40),  # FAIL
        lambda s: setattr(s.health, "error", pf.HealthUnavailable()),  # INCOMPLETE
    ],
    ids=["pass", "fail", "incomplete"],
)
def test_invariants_hold_for_every_outcome(stack, breakage):
    if breakage:
        breakage(stack)
    payload = run(stack).to_json()
    assert payload["ready_for_execution"] is False
    assert payload["run_id"] is None
    assert payload["evidence_written"] is False
    assert (payload["report_posts"], payload["provider_calls"], payload["task_publishes"]) == (
        0,
        0,
        0,
    )
    assert (payload["schema_version"], payload["protocol_version"]) == (
        "s11c3-ex10-preflight-v1",
        "s11-ex10-v1",
    )
    assert (payload["methodology_version"], payload["scenario_id"]) == ("S11A-v1.1", "EX-10")


def test_output_is_canonical_json_with_the_required_check_fields(stack):
    raw = run(stack).to_bytes()
    assert raw.endswith(b"\n") and raw.count(b"\n") == 1
    payload = json.loads(raw)
    assert canonical_json_bytes(payload) == raw
    assert all(
        set(check) == {"check", "result", "expected", "observed_sanitized", "detail_code"}
        for check in payload["checks"]
    )


def test_incomplete_takes_precedence_over_fail(stack):
    stack.repo._head = "b" * 40  # FAIL
    stack.health.error = pf.HealthUnavailable()  # INCOMPLETE
    report = run(stack)
    assert (report.result, report.gate, report.exit_code) == (R.INCOMPLETE, pf.Gate.INCOMPLETE, 2)


# --- NOT_RUN y agregación ---------------------------------------------------------------------


def test_isolation_incomplete_blocks_the_probes_and_the_result_is_incomplete(stack):
    stack.probes.approve_environment = raiser(RedisConnectionError("fake redis down"))
    report = run(stack)
    assert (report.result, report.exit_code) == (R.INCOMPLETE, 2)
    for name in ISOLATION_DEPENDENTS:
        assert checks(report)[name].detail_code == "blocked-by:isolation_guards", name
    assert (stack.health.calls, stack.control.calls) == (0, [])


def test_an_unreachable_approved_redis_blocks_the_broker_and_the_probes(stack):
    stack.redis.down = True
    report = run(stack)
    assert result_of(report, "isolation_redis") is R.INCOMPLETE
    for name in WORKER_CHECKS:
        assert checks(report)[name].detail_code == "blocked-by:isolation_redis", name
    assert result_of(report, "health") is R.PASS  # otra rama: sí corre
    assert (report.result, stack.control.calls) == (R.INCOMPLETE, [])


def test_health_fail_blocks_its_components_and_the_result_is_fail(stack):
    stack.health.status = 503
    report = run(stack)
    assert result_of(report, "health") is R.FAIL
    assert checks(report)["health_components"].detail_code == "blocked-by:health"
    assert (report.result, report.exit_code) == (R.FAIL, 1)
    assert result_of(report, "worker_stability") is R.PASS  # sin relación con el health


def test_no_worker_blocks_the_config_and_the_result_is_incomplete(stack):
    stack.probes.worker_control = FakeControl(lambda n: [], lambda n: [])
    report = run(stack)
    by_name = checks(report)
    assert (by_name["worker_probe_1"].result, by_name["worker_probe_2"].result) == (
        R.INCOMPLETE,
        R.INCOMPLETE,
    )
    for name in ("worker_config", "worker_stability"):
        assert by_name[name].detail_code == "blocked-by:worker_probe_1", name
    assert by_name["broker_dynamic_after"].result is R.PASS
    assert report.result is R.INCOMPLETE


def replaced(report, name, **changes):
    return tuple(
        dataclasses.replace(check, **changes) if check.check == name else check
        for check in report.checks
    )


@pytest.mark.parametrize(
    ("name", "detail", "failed"),
    [
        ("throttle_report_create", None, None),
        ("throttle_report_create", "no-code", None),
        ("health", "blocked-by:isolation_guards", None),  # el prerrequisito pasó
        ("health", "blocked-by:ghost_check", None),  # el prerrequisito no existe
        ("worker_probe_1", "blocked-by:dataset_sha256", "dataset_sha256"),  # otra rama
        ("worker_probe_1", "blocked-by:health_aborted", "health_aborted"),  # otro paso
    ],
    ids=["no-detail", "no-blocker", "blocker-passed", "blocker-absent", "unrelated", "abort"],
)
def test_a_not_run_without_a_declared_blocking_cause_is_incomplete(stack, name, detail, failed):
    report = run(stack)
    assert report.result is R.PASS
    altered = replaced(report, name, result=R.NOT_RUN, detail_code=detail)
    if failed in checks(report):
        altered = replaced(dataclasses.replace(report, checks=altered), failed, result=R.FAIL)
    elif failed:
        altered += (pf.Check(failed, R.FAIL),)
    assert pf.unjustified_not_run(altered) == (name,)
    assert pf.aggregate(altered) is R.INCOMPLETE
    rebuilt = dataclasses.replace(report, checks=altered)
    assert (rebuilt.result, rebuilt.gate, rebuilt.exit_code) == (
        R.INCOMPLETE,
        pf.Gate.INCOMPLETE,
        2,
    )


def test_the_runner_flags_an_unexpected_not_run(stack, monkeypatch):
    monkeypatch.setattr(pf._Runner, "throttle", lambda self: self.skip("throttle_report_create"))
    report = run(stack)
    assert checks(report)["throttle_report_create"].detail_code == "blocked-by:unresolved"
    coverage = checks(report)["check_coverage"]
    assert (coverage.result, coverage.detail_code) == (R.INCOMPLETE, "unexpected-not-run")
    assert coverage.observed_sanitized["unjustified_not_run"] == ["throttle_report_create"]
    assert (report.result, report.exit_code) == (R.INCOMPLETE, 2)


def test_a_required_check_that_is_never_emitted_is_incomplete(stack, monkeypatch):
    monkeypatch.setattr(pf._Runner, "throttle", lambda self: None)
    report = run(stack)
    coverage = checks(report)["check_coverage"]
    assert (coverage.result, coverage.detail_code) == (R.INCOMPLETE, "missing-required-check")
    assert coverage.observed_sanitized["missing_required"] == ["throttle_report_create"]
    assert report.result is R.INCOMPLETE


def test_not_run_never_counts_as_pass():
    blocked = pf.Check("health", R.NOT_RUN, detail_code="blocked-by:isolation_guards")
    assert pf.aggregate(()) is R.INCOMPLETE
    assert pf.aggregate((blocked,)) is R.INCOMPLETE  # su causa no está
    assert pf.aggregate((pf.Check("isolation_guards", R.FAIL), blocked)) is R.FAIL
    assert pf.aggregate((pf.Check("isolation_guards", R.INCOMPLETE), blocked)) is R.INCOMPLETE
    assert pf.aggregate((pf.Check("isolation_guards", R.PASS), blocked)) is R.INCOMPLETE


def test_not_run_is_serialized_exactly_and_never_as_the_global_result(stack):
    stack.probes.approve_environment = raiser(st.Ex10StateError("DATABASE_NOT_APPROVED"))
    raw = run(stack).to_bytes()
    payload = json.loads(raw)
    health = next(check for check in payload["checks"] if check["check"] == "health")
    assert health == {
        "check": "health",
        "result": "NOT_RUN",
        "expected": None,
        "observed_sanitized": None,
        "detail_code": "blocked-by:isolation_guards",
    }
    assert b'"result":"NOT_RUN"' in raw
    assert (payload["result"], payload["gate"]) == ("FAIL", "NOT_READY")


ORDER_BREAKAGES = {
    "pass": None,
    "source-partial": lambda s: setattr(
        s.repo, "source", dataclasses.replace(COMPLETE, sparse=True)
    ),
    "dataset-rejected": lambda s: setattr(
        s.probes, "load_dataset", raiser(ex10_load.Ex10DatasetError(["dataset-sha256"]))
    ),
    "plan-rejected": lambda s: setattr(
        s.probes, "build_plan", raiser(ex10_load.Ex10PlanError(["plan-shape"]))
    ),
    "isolation-fail": lambda s: setattr(
        s.probes, "approve_environment", raiser(st.Ex10StateError("DATABASE_NOT_APPROVED"))
    ),
    "health-fail": lambda s: setattr(s.health, "status", 503),
    "no-worker": lambda s: setattr(
        s.probes, "worker_control", FakeControl(lambda n: [], lambda n: [])
    ),
    "unapproved-broker": lambda s: setattr(
        s.probes, "api_config", lambda: api_config(broker_same_as_approved=False)
    ),
}


@pytest.mark.parametrize("breakage", list(ORDER_BREAKAGES), ids=list(ORDER_BREAKAGES))
def test_checks_follow_the_frozen_plan_in_a_deterministic_order(stack, breakage):
    if ORDER_BREAKAGES[breakage]:
        ORDER_BREAKAGES[breakage](stack)
    first = run(stack)
    stack.probes.nonce = nonces()
    if ORDER_BREAKAGES[breakage]:
        ORDER_BREAKAGES[breakage](stack)  # dobles con estado, como el control, desde cero
    second = run(stack)
    assert plan_order(first) == list(pf.REQUIRED_CHECKS)
    assert [(c.check, c.result, c.detail_code) for c in first.checks] == [
        (c.check, c.result, c.detail_code) for c in second.checks
    ]
    assert first.to_bytes() == second.to_bytes()


@pytest.mark.parametrize(
    ("breakage", "independent"),
    [
        (
            "isolation-fail",
            (
                *pf.REQUIRED_CHECKS[:5],
                "protocol_identity",
                "dataset_sha256",
                "plan_shape",
                "plan_sha256",
                "plan_actors",
                "isolation_non_production",
                "api_broker",
                "throttle_report_create",
            ),
        ),
        (
            "dataset-rejected",
            ("isolation_guards", "health_components", "worker_stability", "api_broker"),
        ),
        ("plan-rejected", ("plan_actors", "token_issuance_capability", "worker_stability")),
        ("health-fail", ("ready_state:fixture_actors", "plan_multipart_bodies", *WORKER_CHECKS)),
        ("source-partial", ("protocol_identity", "health_components", "worker_stability")),
        ("no-worker", ("broker_dynamic_after", "broker_classification_after", "health")),
    ],
    ids=["isolation", "dataset", "plan", "health", "source-root", "worker"],
)
def test_independent_checks_are_not_blocked_by_an_unrelated_failure(stack, breakage, independent):
    ORDER_BREAKAGES[breakage](stack)
    report = run(stack)
    assert [name for name in independent if result_of(report, name) is R.NOT_RUN] == []
    assert pf.unjustified_not_run(report.checks) == ()
    assert result_of(report, "check_coverage") is R.PASS
    for check in report.checks:
        if check.result is R.NOT_RUN:
            blocker = check.detail_code.removeprefix("blocked-by:")
            assert blocker in pf.allowed_blockers(check.check), check.check
            assert result_of(report, blocker) in (R.FAIL, R.INCOMPLETE), check.check


def test_isolation_fail_blocks_health_and_probes_and_the_result_is_fail(stack):
    stack.probes.approve_environment = raiser(st.Ex10StateError("DATABASE_NOT_APPROVED"))
    report = run(stack)
    assert (report.result, report.exit_code) == (R.FAIL, 1)
    by_name = checks(report)
    assert by_name["isolation_guards"].detail_code == "database-not-approved"
    for name in (*ISOLATION_DEPENDENTS, "token_issuance_capability"):
        assert by_name[name].result is R.NOT_RUN, name
        assert by_name[name].detail_code == "blocked-by:isolation_guards", name
    # Ni un GET ni un probe contra un entorno no aprobado.
    assert (stack.health.calls, stack.control.calls) == (0, [])
    assert report.to_json()["health_requests"] == 0
    assert result_of(report, "no_side_effects") is R.PASS
    assert result_of(report, "check_coverage") is R.PASS


# --- Repositorio, protocolo, dataset y plan ------------------------------------------------


def test_wrong_head_is_not_ready(stack):
    stack.repo._head = "b" * 40
    report = run(stack)
    check = checks(report)["repository_head"]
    assert (check.result, check.detail_code) == (R.FAIL, "head-mismatch")
    assert report.exit_code == 1


def test_a_dirty_tree_is_not_ready(stack):
    stack.repo.dirty = 2
    check = checks(run(stack))["repository_clean"]
    assert (check.result, check.observed_sanitized) == (R.FAIL, {"dirty_entries": 2})


def test_a_partial_source_root_cannot_prove_head_or_a_clean_tree(stack):
    stack.repo.source = dataclasses.replace(COMPLETE, missing_top_level=1)  # `/s11-source`
    report = run(stack)
    check = checks(report)["repository_source_complete"]
    assert (check.result, check.detail_code) == (R.INCOMPLETE, "partial-source-root")
    for name in REPOSITORY_DEPENDENTS:
        assert checks(report)[name].detail_code == "blocked-by:repository_source_complete", name
    assert (report.result, report.git_sha) == (R.INCOMPLETE, None)


def test_git_unavailable_is_incomplete(stack):
    stack.repo.fail = "head"
    report = run(stack)
    assert result_of(report, "repository_head") is R.INCOMPLETE
    assert result_of(report, "repository_clean") is R.PASS  # independiente de HEAD


def test_running_code_must_match_the_source_root(stack):
    stack.repo.files["backend/apps/experiments_app/ex10_state.py"] = b"otro codigo"
    check = checks(run(stack))["repository_running_source"]
    assert (check.result, check.observed_sanitized["mismatched"]) == (R.FAIL, 1)


EXECUTOR_RUNTIME_SOURCES = (
    "backend/apps/experiments_app/ex10_executor.py",
    "backend/apps/experiments_app/ex10_reconcile.py",
    "backend/apps/experiments_app/management/commands/s11_ex10.py",
)


def running_sources(report):
    return {
        s["source"]: s
        for s in checks(report)["repository_running_source"].observed_sanitized["sources"]
    }


def test_the_executor_runtime_sources_are_matched_against_the_checkout(stack):
    # (1) Las 3 fuentes runtime del executor, presentes e idénticas: PASS.
    report = run(stack)
    check = checks(report)["repository_running_source"]
    assert check.result is R.PASS and check.detail_code is None
    assert check.observed_sanitized["compared"] == len(pf.RUNNING_SOURCES) == 10
    sources = running_sources(report)
    for relative in EXECUTOR_RUNTIME_SOURCES:
        assert sources[relative] == {"source": relative, "match": True, "detail_code": None}


@pytest.mark.parametrize("relative", EXECUTOR_RUNTIME_SOURCES)
def test_an_executor_runtime_source_that_differs_fails(stack, relative):
    # (2, 3, 4) Cualquier byte distinto en el executor, la reconciliación o el comando: FAIL.
    stack.repo.files[relative] = b"otra revision\n"
    report = run(stack)
    check = checks(report)["repository_running_source"]
    assert (check.result, check.detail_code) == (R.FAIL, "running-source-differs")
    assert check.observed_sanitized["mismatched"] == 1
    assert running_sources(report)[relative] == {
        "source": relative,
        "match": False,
        "detail_code": "bytes-differ",
    }
    assert report.result is R.FAIL


def test_a_missing_runtime_executor_source_fails(stack, settings, tmp_path):
    # (5) El contenedor no tiene `ex10_executor.py`: nada que comparar, FAIL.
    running = Path(settings.BASE_DIR)
    runtime = tmp_path / "runtime"
    for relative in pf.RUNNING_SOURCES:
        stack.repo.files[relative] = (running / relative.removeprefix("backend/")).read_bytes()
        if relative != EXECUTOR_RUNTIME_SOURCES[0]:
            target = runtime / relative.removeprefix("backend/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(stack.repo.files[relative])
    settings.BASE_DIR = runtime
    report = run(stack)
    check = checks(report)["repository_running_source"]
    assert (check.result, check.observed_sanitized["mismatched"]) == (R.FAIL, 1)
    assert running_sources(report)[EXECUTOR_RUNTIME_SOURCES[0]]["detail_code"] == "runtime-missing"


def test_a_missing_checked_out_executor_source_is_reported(stack):
    # (6) Sin el blob del checkout nunca hay coincidencia (ruta falsa del repositorio).
    stack.repo.files[EXECUTOR_RUNTIME_SOURCES[1]] = None
    report = run(stack)
    assert result_of(report, "repository_running_source") is R.FAIL
    assert running_sources(report)[EXECUTOR_RUNTIME_SOURCES[1]]["detail_code"] == "source-missing"


def test_running_sources_are_runtime_only_unique_and_deterministic(stack):
    # (7) Ni tests ni soporte de tests; (8) sin duplicados; (9) orden fijo y reproducible.
    assert isinstance(pf.RUNNING_SOURCES, tuple)
    assert not [s for s in pf.RUNNING_SOURCES if "/tests/" in s or not s.endswith(".py")]
    for test_file in (
        "backend/apps/experiments_app/tests/test_ex10_executor.py",
        "backend/apps/experiments_app/tests/ex10_executor_support.py",
    ):
        assert test_file not in pf.RUNNING_SOURCES
    assert len(set(pf.RUNNING_SOURCES)) == len(pf.RUNNING_SOURCES)
    assert set(EXECUTOR_RUNTIME_SOURCES) <= set(pf.RUNNING_SOURCES)
    first, second = run(stack), run(stack)
    listed = [
        s["source"]
        for s in checks(first)["repository_running_source"].observed_sanitized["sources"]
    ]
    assert listed == list(pf.RUNNING_SOURCES)
    assert canonical_json_bytes(checks(first)["repository_running_source"].to_json()) == (
        canonical_json_bytes(checks(second)["repository_running_source"].to_json())
    )


def test_decision_commits_must_be_in_history(stack):
    stack.repo.ancestors = {pf.G0_DECISION_COMMIT}
    check = checks(run(stack))["protocol_decision_commits"]
    assert check.result is R.FAIL and check.observed_sanitized["g0_a1_in_history"] is False


def test_the_g0_a2_decision_commit_must_be_in_history(stack):
    # G0-A2 forma parte de la identidad de los runs futuros: su commit debe estar en la historia.
    stack.repo.ancestors = {pf.G0_DECISION_COMMIT, pf.G0_A1_DECISION_COMMIT}
    check = checks(run(stack))["protocol_decision_commits"]
    assert check.result is R.FAIL and check.detail_code == "decision-commit-not-in-history"
    assert check.observed_sanitized == {
        "g0_in_history": True,
        "g0_a1_in_history": True,
        "g0_a2_in_history": False,
    }
    stack.repo.ancestors = {
        pf.G0_DECISION_COMMIT,
        pf.G0_A1_DECISION_COMMIT,
        pf.G0_A2_DECISION_COMMIT,
    }
    assert checks(run(stack))["protocol_decision_commits"].result is R.PASS


def test_the_frozen_identity_includes_g0_a2():
    assert pf.G0_A2_DECISION_COMMIT == "2bab6cabe4108e1eddbb319c3f859ed75c914004"
    assert pf.FROZEN_IDENTITY["decision_addendum_a2_commit"] == pf.G0_A2_DECISION_COMMIT
    assert "decision_addendum_a2_commit" in pf.EXPECTED_IDENTITY_KEYS
    assert len(pf.EXPECTED_IDENTITY_KEYS) == 13
    assert pf.G0_A2_DECISION_COMMIT == ex10_load.G0_A2_DECISION_COMMIT
    assert set(ex10_load.protocol_identity()) == pf.EXPECTED_IDENTITY_KEYS


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (lambda i: i.update(decision_commit="0" * 40), "identity-decision-commit"),
        (
            lambda i: i.update(decision_addendum_commit="0" * 40),
            "identity-decision-addendum-commit",
        ),
        (lambda i: i.update(extra_field=None), "identity-fields"),
        (lambda i: i.pop("decision_addendum_commit"), "identity-fields"),
    ],
    ids=["g0", "g0-a1", "extra-field", "missing-field"],
)
def test_protocol_identity_is_checked_against_g0(stack, mutate, detail):
    identity = copy.deepcopy(ex10_load.protocol_identity())
    mutate(identity)
    stack.probes.protocol_identity = lambda: identity
    check = checks(run(stack))["protocol_identity"]
    assert (check.result, check.detail_code) == (R.FAIL, detail)


def test_altered_dataset_is_rejected(stack):
    dataset = ex10_load.load_dataset()
    stack.probes.load_dataset = lambda: ex10_load.LoadDataset("0" * 64, dataset.payload)
    report = run(stack)
    assert result_of(report, "dataset_sha256") is R.FAIL
    assert result_of(report, "plan_shape") is R.NOT_RUN


def test_a_plan_that_is_not_ten_by_five_is_rejected(stack):
    real = ex10_load.build_plan(ex10_load.load_dataset())
    short = ex10_load.LoadPlan(real.dataset_sha256, real.items[:49])
    stack.probes.build_plan = lambda dataset: short
    check = checks(run(stack))["plan_shape"]
    assert (check.result, check.observed_sanitized["items"]) == (R.FAIL, 49)


def test_plan_sha_and_actors_are_the_canonical_ones(stack):
    by_name = checks(run(stack))
    assert by_name["plan_sha256"].expected == ex10_load.PLAN_SHA256
    assert by_name["plan_actors"].observed_sanitized == {"actors": 10, "deterministic": True}
    assert by_name["plan_multipart_bodies"].observed_sanitized == {"prepared": 50}


# --- Aislamiento ------------------------------------------------------------------------------


@pytest.fixture
def isolated(settings, request):
    base = Path.cwd() / ".s11-test-artifacts" / f"ex10-preflight-{request.node.name}"
    shutil.rmtree(base, ignore_errors=True)
    evidence = {name: base / "evidence" / name for name in ("runs", "backup", "restore")}
    for path in evidence.values():
        path.mkdir(parents=True)
    settings.S11_EXPERIMENTS_ENABLED = True
    settings.S11_ENVIRONMENT = "isolated-experiment"
    settings.S11_DATABASE_URL = f"postgis://ciudadania@db:5432/{DECLARED_DB}"
    settings.REDIS_URL = "redis://redis:6379/11"
    settings.S11_OBJECT_ROOT = base / "objects"
    settings.S11_ARTIFACT_ROOT = evidence["runs"]
    settings.S11_BACKUP_ROOT = evidence["backup"]
    settings.S11_RESTORE_ROOT = evidence["restore"]
    initialize_object_root(runtime.isolation_config())
    yield SimpleNamespace(base=base, objects=base / "objects", evidence=evidence)
    shutil.rmtree(base)


def real_approval(redis):
    return lambda: st.approve_environment(redis_factory=lambda url: redis)


def test_real_isolation_guards_approve_the_isolated_environment(stack, isolated, monkeypatch):
    monkeypatch.setattr(st, "_connected_database_name", lambda: DECLARED_DB)
    stack.probes.approve_environment = real_approval(stack.redis)
    report = run(stack)
    for name in ("isolation_guards", "isolation_redis", "isolation_storage_roots"):
        assert result_of(report, name) is R.PASS, name


@pytest.mark.django_db
def test_the_really_connected_database_must_be_the_declared_one(stack, isolated):
    # Sin simulación: la base de tests no es `ciudadania_s11_unit`.
    stack.probes.database_identity = pf.real_database_identity
    check = checks(run(stack))["isolation_database_connected"]
    assert (check.result, check.observed_sanitized["database"]) == (R.FAIL, False)


def test_a_redis_db_other_than_11_is_rejected(stack, isolated, settings, monkeypatch):
    monkeypatch.setattr(st, "_connected_database_name", lambda: DECLARED_DB)
    settings.REDIS_URL = "redis://redis:6379/0"
    stack.probes.approve_environment = real_approval(stack.redis)
    check = checks(run(stack))["isolation_guards"]
    assert (check.result, check.detail_code) == (R.FAIL, "isolation-rejected")


def test_storage_overlapping_evidence_is_rejected(stack, isolated, settings, monkeypatch):
    monkeypatch.setattr(st, "_connected_database_name", lambda: DECLARED_DB)
    settings.S11_OBJECT_ROOT = isolated.evidence["runs"] / "objects"
    stack.probes.approve_environment = real_approval(stack.redis)
    check = checks(run(stack))["isolation_guards"]
    assert (check.result, check.detail_code) == (R.FAIL, "storage-overlaps-evidence")


def test_symlinked_roots_are_rejected(stack, settings):
    real = stack.root / "real-runs"
    real.mkdir()
    link = stack.root / "linked-runs"
    link.symlink_to(real, target_is_directory=True)
    settings.S11_ARTIFACT_ROOT = link
    check = checks(run(stack))["isolation_storage_roots"]
    assert (check.result, check.detail_code) == (R.FAIL, "storage-symlink")


def test_production_settings_are_rejected(stack):
    stack.probes.settings_module = lambda: "config.settings.prod"
    check = checks(run(stack))["isolation_non_production"]
    assert (check.result, check.detail_code) == (R.FAIL, "production-settings")


def test_unreachable_database_identity_is_incomplete(stack):
    def down():
        raise OperationalError(f"could not connect password={CANARY}")

    stack.probes.database_identity = down
    report = run(stack)
    assert result_of(report, "isolation_database_connected") is R.INCOMPLETE
    assert CANARY not in report.to_bytes().decode()


# --- Health -----------------------------------------------------------------------------------


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _answer(self):
        server = self.server
        server.requests.append((self.command, self.path))
        if server.delay:
            threading.Event().wait(server.delay)
        if server.redirect:
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:9/elsewhere")
            self.end_headers()
            return
        self.send_response(server.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(server.body)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _answer


@pytest.fixture
def health_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    server.daemon_threads = False  # `server_close()` espera a cada handler: ningún hilo vivo
    server.requests, server.delay, server.redirect, server.status = [], 0, False, 200
    server.body = json.dumps(
        {"status": "ok", "version": "0.1.0", "db": True, "redis": True}
    ).encode()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.url = f"http://127.0.0.1:{server.server_address[1]}/api/v1/health/"
    yield server
    server.shutdown()
    server.server_close()
    thread.join(10)
    assert not thread.is_alive()


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, None, ((R.PASS, None), (R.PASS, None))),
        (500, None, ((R.FAIL, "health-http-status"), (R.NOT_RUN, "blocked-by:health"))),
        (
            200,
            b"<html>not json</html>",
            ((R.PASS, None), (R.INCOMPLETE, "health-body-invalid")),
        ),
        (200, b'{"status": "ok"}', ((R.PASS, None), (R.INCOMPLETE, "health-body-invalid"))),
        (
            200,
            b'{"status": "degraded", "db": true, "redis": false}',
            ((R.PASS, None), (R.FAIL, "health-degraded")),
        ),
    ],
    ids=["healthy", "http-500", "not-json", "missing-fields", "degraded"],
)
def test_health_is_one_real_get(stack, health_server, monkeypatch, status, body, expected):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")  # un proxy nunca se usa
    health_server.status = status
    if body is not None:
        health_server.body = body
    stack.probes.health = pf.HttpHealthClient(health_server.url, timeout=5)
    report = run(stack)
    by_name = checks(report)
    assert (
        tuple(
            (by_name[name].result, by_name[name].detail_code)
            for name in ("health", "health_components")
        )
        == expected
    )
    assert health_server.requests == [("GET", "/api/v1/health/")]
    assert report.to_json()["health_requests"] == 1


def test_health_timeout_is_incomplete(stack, health_server):
    health_server.delay = 2
    stack.probes.health = pf.HttpHealthClient(health_server.url, timeout=0.3)
    by_name = checks(run(stack))
    assert (by_name["health"].result, by_name["health"].detail_code) == (
        R.INCOMPLETE,
        "health-unreachable",
    )
    assert by_name["health_components"].detail_code == "blocked-by:health"
    assert [method for method, _ in health_server.requests] == ["GET"]


def test_health_redirects_are_not_followed(stack, health_server):
    health_server.redirect = True
    stack.probes.health = pf.HttpHealthClient(health_server.url, timeout=5)
    check = checks(run(stack))["health"]
    assert (check.result, check.observed_sanitized) == (R.FAIL, {"http_status": 302})
    assert len(health_server.requests) == 1


def test_the_health_client_only_does_get():
    client = pf.HttpHealthClient()
    assert client.url == "http://backend:8000/api/v1/health/"
    assert [name for name in vars(pf.HttpHealthClient) if not name.startswith("_")] == [
        "host",
        "get",
    ]


# --- READY_STATE --------------------------------------------------------------------------------


@pytest.fixture
def ready_stack(stack, isolated, monkeypatch, db):
    """Stack con la base de tests real: catálogos, los diez actores y lecturas de verdad."""
    monkeypatch.setattr(st, "_connected_database_name", lambda: DECLARED_DB)
    call_command("seed_catalogs", verbosity=0)
    ex10_fixture.prepare_fixture()
    stack.probes.approve_environment = real_approval(stack.redis)
    stack.probes.inspect_ready_state = st.inspect_ready_state
    stack.probes.category_ids = pf.real_category_ids
    stack.probes.fixture_result = pf.real_fixture_result
    stack.probes.issue_tokens = ex10_fixture.issue_tokens
    stack.probes.verify_tokens = ex10_fixture.verify_tokens
    stack.probes.clock = lambda: datetime.now(UTC)
    stack.probes.throttle_config = pf.real_throttle_config
    return stack


def test_a_real_ready_state_passes_with_real_tokens_and_bodies(ready_stack):
    report = run(ready_stack)
    assert report.result is R.PASS, [c for c in report.checks if c.result is not R.PASS]
    by_name = checks(report)
    assert by_name["ready_state:fixture_actors"].observed_sanitized == 10
    assert by_name["token_issuance_capability"].observed_sanitized["verified"] == 10
    assert by_name["throttle_report_create"].result is R.PASS


def test_zero_state_without_actors_is_not_ready(ready_stack):
    User.objects.all().delete()
    report = run(ready_stack)
    assert result_of(report, "ready_state:users_total") is R.FAIL
    assert result_of(report, "token_issuance_capability") is R.NOT_RUN
    assert report.exit_code == 1


def test_a_missing_actor_or_drift_is_not_ready(ready_stack):
    specs = ex10_fixture.actor_specs()
    User.objects.filter(pk=specs[0].user_id).delete()
    assert result_of(run(ready_stack), "ready_state:fixture_actors") is R.FAIL
    ex10_fixture.prepare_fixture()
    User.objects.filter(pk=specs[1].user_id).update(full_name="Otra persona")
    check = checks(run(ready_stack))["ready_state:fixture_actors"]
    assert (check.result, check.detail_code) == (R.FAIL, "missing=0 drift=1")


def test_residual_dynamic_state_is_not_ready(ready_stack):
    Report.objects.create(
        citizen=User.objects.first(),
        category=Category.objects.get(slug="baches"),
        description="LOCAL-TEST",
        location="POINT(-57.5 -25.3)",
    )
    assert result_of(run(ready_stack), "ready_state:reports") is R.FAIL


def test_unavailable_redis_during_ready_state_is_incomplete(ready_stack):
    ready_stack.redis.down = True
    report = run(ready_stack)
    assert result_of(report, "isolation_redis") is R.INCOMPLETE
    assert result_of(report, "ready_state:celery_queue_length") is R.INCOMPLETE
    assert report.result is R.INCOMPLETE


def test_unavailable_database_during_ready_state_is_incomplete(ready_stack, monkeypatch):
    def unavailable():
        raise OperationalError(f"could not connect password={CANARY}")

    monkeypatch.setattr(Report.objects, "count", unavailable)
    report = run(ready_stack)
    check = checks(report)["ready_state:reports"]
    assert (check.result, check.detail_code) == (R.INCOMPLETE, "database-unavailable")
    assert CANARY not in report.to_bytes().decode()


# --- Tokens en memoria -------------------------------------------------------------------------


def test_token_issuance_is_read_only(ready_stack):
    specs = ex10_fixture.actor_specs()
    result = pf.real_fixture_result(specs)
    before = list(User.objects.order_by("pk").values_list("pk", "last_login"))
    with CaptureQueriesContext(connection) as queries:
        tokens = ex10_fixture.issue_tokens(result)
        verified = ex10_fixture.verify_tokens(tokens, result)
    assert all(query["sql"].lstrip().upper().startswith("SELECT") for query in queries)
    assert len(tokens) == 10 and all(check.ok for check in verified)
    tokens.release()
    assert tokens.released
    assert list(User.objects.order_by("pk").values_list("pk", "last_login")) == before


def test_tokens_never_reach_json_repr_or_logs(ready_stack, caplog, monkeypatch):
    issued = []

    def capture(result):
        tokens = ex10_fixture.issue_tokens(result)
        issued.extend(tokens.mapping().values())
        return tokens

    ready_stack.probes.issue_tokens = capture
    with caplog.at_level("DEBUG"):
        report = run(ready_stack)
    assert result_of(report, "token_issuance_capability") is R.PASS and len(issued) == 10
    texts = [report.to_bytes().decode(), repr(report), caplog.text]
    assert not [i for i, token in enumerate(issued) for text in texts if token in text]
    assert "Bearer" not in texts[0] and "Authorization" not in texts[0]


def test_a_token_issuance_side_effect_is_blocked_and_incomplete(ready_stack, monkeypatch):
    original = ex10_fixture.AccessToken.for_user.__func__

    def persisting(cls, user):
        User.objects.filter(pk=user.pk).update(last_login=NOW)  # efecto persistente
        return original(cls, user)

    monkeypatch.setattr(ex10_fixture.AccessToken, "for_user", classmethod(persisting))
    # La escritura bloqueada marca la transacción del test como rota (en producción el
    # preflight corre en autocommit): el savepoint la deja consultable después.
    with transaction.atomic():
        report = run(ready_stack)
    check = checks(report)["token_issuance_capability"]
    assert (check.result, check.detail_code) == (R.INCOMPLETE, "token-issuance-has-side-effects")
    assert not User.objects.filter(last_login__isnull=False).exists()  # bloqueado antes
    assert result_of(report, "no_side_effects") is R.PASS  # contenido por su check


# --- Throttle ---------------------------------------------------------------------------------


def test_the_real_throttle_is_report_create_five_per_hour():
    config = pf.real_throttle_config()
    assert (config.rate, config.view_scope, config.scoped_throttle) == (
        "5/hour",
        "report_create",
        True,
    )


@pytest.mark.parametrize(
    ("config", "detail"),
    [
        (pf.ThrottleConfig("5/hour", "report_create", True), None),
        (pf.ThrottleConfig("5/hour", "reports_mine", True), "scope-mismatch"),
        (pf.ThrottleConfig("5/hour", "report_create", False), "scope-mismatch"),
        (pf.ThrottleConfig("5/minute", "report_create", True), "rate-mismatch"),
        (pf.ThrottleConfig("5/h", "report_create", True), "rate-mismatch"),
        (pf.ThrottleConfig("five/hour", "report_create", True), "rate-unparseable"),
        (pf.ThrottleConfig(None, "report_create", True), "rate-missing"),
    ],
    ids=["ok", "other-scope", "no-scoped-class", "per-minute", "abbreviated", "garbage", "missing"],
)
def test_throttle_must_be_exactly_five_per_hour(stack, config, detail):
    stack.probes.throttle_config = lambda: config
    check = checks(run(stack))["throttle_report_create"]
    assert check.detail_code == detail
    assert check.result is (R.PASS if detail is None else R.FAIL)


# --- Configuración del API --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "check", "detail"),
    [
        ({"task_always_eager": True}, "api_task_always_eager", "api-eager-enabled"),
        ({"broker_same_as_approved": False}, "api_broker", "api-broker-not-approved"),
        (
            {"broker": {**ENDPOINT, "db": 0}},
            "api_broker",
            "api-broker-not-approved",
        ),
        (
            {"result_same_as_approved": False},
            "api_result_backend",
            "api-result-backend-not-approved",
        ),
        ({"fake_ai_enabled": False}, "api_fake_ai", "api-fake-ai"),
        ({"ai_adapter": "not-s11-fake"}, "api_ai_adapter", "api-ai-adapter"),
        (
            {"classify_task_registered": False},
            "api_classify_task_registered",
            "api-classify-task-registered",
        ),
    ],
    ids=["eager", "broker-url", "broker-db", "result-backend", "fake", "adapter", "task"],
)
def test_api_config_mismatches_are_not_ready(stack, changes, check, detail):
    stack.probes.api_config = lambda: api_config(**changes)
    observed = checks(run(stack))[check]
    assert (observed.result, observed.detail_code) == (R.FAIL, detail)


def test_the_real_api_config_of_the_test_settings_is_eager_and_not_approved():
    config = pf.real_api_config()
    assert config.task_always_eager is True  # settings.test: el preflight lo rechazaría
    assert config.classify_task_registered is True
    assert "://" not in json.dumps(dataclasses.asdict(config))


def test_an_unapproved_broker_blocks_every_worker_probe(stack):
    stack.probes.api_config = lambda: api_config(broker_same_as_approved=False)
    report = run(stack)
    assert result_of(report, "worker_probe_1") is R.NOT_RUN
    assert stack.control.calls == []


# --- Worker -------------------------------------------------------------------------------------


def test_exactly_one_stable_worker_passes(stack):
    by_name = checks(run(stack))
    assert by_name["worker_probe_1"].observed_sanitized["nodes"] == 1
    for name in ("worker_probe_2", "worker_config", "worker_stability"):
        assert by_name[name].result is R.PASS, name
    assert len({nonce for nonce, _ in stack.control.calls}) == 2  # un nonce por probe


def test_no_worker_is_incomplete(stack):
    stack.probes.worker_control = FakeControl(lambda n: [], lambda n: [])
    report = run(stack)
    check = checks(report)["worker_probe_1"]
    assert (check.result, check.detail_code) == (R.INCOMPLETE, "no-worker-replied")
    assert report.result is R.INCOMPLETE


def test_two_workers_are_not_ready(stack):
    two = lambda n: [worker_reply(n), worker_reply(n, node="celery@worker-2")]  # noqa: E731
    stack.probes.worker_control = FakeControl(two, two)
    check = checks(run(stack))["worker_probe_1"]
    assert (check.result, check.detail_code) == (R.FAIL, "multiple-workers")


def test_probe_timeout_is_incomplete_and_never_retried(stack):
    control = FakeControl(error=pf.ControlUnavailable())
    stack.probes.worker_control = control
    report = run(stack)
    assert result_of(report, "worker_probe_1") is R.INCOMPLETE
    assert len(control.calls) == pf.PROBE_ROUNDS  # ni un tercero oculto


@pytest.mark.parametrize(
    ("changes", "detail"),
    [
        ({"pool": {"max_concurrency": 3}}, "worker-concurrency"),
        ({"task_always_eager": True}, "worker-eager"),
        ({"broker": {"db": 0}}, "worker-broker"),
        ({"broker": {"matches_protocol": False}}, "worker-broker"),
        ({"result_backend": {"db": 5}}, "worker-result-backend"),
        ({"fake_ai_enabled": False}, "worker-fake"),
        ({"ai_adapter": "not-s11-fake"}, "worker-adapter"),
        ({"classify_task_registered": False}, "worker-task-not-registered"),
    ],
    ids=["concurrency", "eager", "broker-db", "broker-url", "result", "fake", "adapter", "task"],
)
def test_worker_config_mismatches_are_not_ready(stack, changes, detail):
    reply = lambda n: [worker_reply(n, **changes)]  # noqa: E731
    stack.probes.worker_control = FakeControl(reply, reply)
    check = checks(run(stack))["worker_config"]
    assert (check.result, check.detail_code) == (R.FAIL, detail)


def test_a_worker_that_changes_between_probes_is_not_stable(stack):
    stack.probes.worker_control = FakeControl(
        lambda n: [worker_reply(n)], lambda n: [worker_reply(n, process={"pid": 4243})]
    )
    check = checks(run(stack))["worker_stability"]
    assert (check.result, check.detail_code) == (R.FAIL, "worker-not-stable")


@pytest.mark.parametrize(
    ("reply", "detail"),
    [
        (
            lambda n: {"celery@w": {**worker_reply(n, node="celery@w")["celery@w"], "env": {}}},
            "worker-probe-unexpected-fields",
        ),
        (
            lambda n: worker_reply(n, process={"hostname": "Bearer-abc"}),
            "worker-probe-possible-secret",
        ),
        (lambda n: worker_reply(n, ai_adapter="token-leak"), "worker-probe-possible-secret"),
        (
            lambda n: worker_reply(n, pool={"implementation": "redis://u:p@h/1"}),
            "worker-probe-field-format",
        ),
        (
            lambda n: {k: {**v, "nonce": "f" * 32} for k, v in worker_reply(n).items()},
            "worker-probe-nonce-mismatch",
        ),
        (
            lambda n: {"celery@w": {"error": "KeyError('s11_ex10_worker_probe')"}},
            "worker-probe-not-registered",
        ),
        (
            lambda n: {
                "celery@w": {
                    "schema_version": wp.PROBE_SCHEMA_VERSION,
                    "nonce": n,
                    "error": "probe-failed",
                }
            },
            "worker-probe-internal-error",
        ),
    ],
    ids=[
        "extra-field",
        "bearer",
        "token",
        "url",
        "foreign-nonce",
        "not-registered",
        "internal-error",
    ],
)
def test_worker_replies_outside_the_whitelist_are_rejected(stack, reply, detail):
    stack.probes.worker_control = FakeControl(lambda n: [reply(n)], lambda n: [reply(n)])
    report = run(stack)
    check = checks(report)["worker_probe_1"]
    assert (check.result, check.detail_code) == (R.FAIL, detail)
    raw = report.to_bytes().decode()
    assert "Bearer-abc" not in raw and "redis://u" not in raw and "KeyError" not in raw


# --- Broker y control plane ---------------------------------------------------------------------


def test_an_empty_broker_with_bindings_and_control_plane_passes(stack):
    redis = stack.redis
    redis.put("_kombu.binding.celery", "set", {"celery\x06\x16\x06\x16celery"})
    redis.put("_kombu.binding.celery.pidbox", "set", {"x"})
    redis.put("_kombu.binding.reply.celery.pidbox", "set", {"y"})
    redis.put("_kombu.binding.celeryev", "set", {"z"})
    redis.put("unacked_mutex", "string", b"lock", pttl=250_000)
    reply = json.dumps({"body": "e30=", "headers": {"ticket": "t", "clock": 1}}).encode()
    redis.put("0f0e.reply.celery.pidbox", "list", [reply])
    redis.put("celeryev.4d2c", "list", [json.dumps({"headers": {}}).encode()])
    by_name = checks(run(stack))
    for name in ("broker_dynamic_before", "broker_classification_before", "broker_dynamic_after"):
        assert by_name[name].result is R.PASS, name
    observed = by_name["broker_classification_after"].observed_sanitized
    assert observed["static_bindings"] == 4
    assert observed["control_plane"] == {"unacked_mutex": 1, "pidbox": 1, "celeryev": 1}
    assert "0f0e" not in json.dumps(observed)  # sólo clases y cantidades


@pytest.mark.parametrize(
    ("key", "kind", "value"),
    [
        ("celery", "list", [b"m"]),
        ("celery\x06\x169", "list", [b"m"]),
        ("unacked", "hash", {"t": b"m"}),
        ("unacked_index", "zset", {"t": 1.0}),
        ("celery-task-meta-0f0", "string", b"{}"),
        ("celery-taskset-meta-0f0.j", "list", [b"m"]),
        ("chord-unlock-0f0", "string", b"1"),
    ],
    ids=["queue", "priority", "unacked", "unacked-index", "result", "group-join", "chord"],
)
def test_dynamic_broker_state_is_not_ready(stack, key, kind, value):
    stack.redis.put(key, kind, value)
    check = checks(run(stack))["broker_dynamic_before"]
    assert (check.result, check.observed_sanitized["dynamic"]) == (R.FAIL, 1)


def test_actor_throttle_keys_are_dynamic_state(stack):
    stack.redis.put(
        f"{st.throttle_redis_pattern()[:-1]}{ex10_fixture.actor_specs()[0].user_id}",
        "string",
        b"[]",
    )
    assert result_of(run(stack), "broker_dynamic_before") is R.FAIL


def test_a_task_message_in_the_control_plane_is_not_ready(stack):
    task = json.dumps({"headers": {"task": "ai.classify_report", "id": "x"}}).encode()
    stack.redis.put("0f0e.reply.celery.pidbox", "list", [task])
    check = checks(run(stack))["broker_dynamic_before"]
    assert (check.result, check.observed_sanitized["task_messages"]) == (R.FAIL, 1)


@pytest.mark.parametrize(
    ("key", "kind", "value", "pttl", "detail"),
    [
        ("something-else", "string", b"x", -1, "unknown-broker-keys"),
        ("unacked_mutex", "string", b"lock", -1, "mutex-without-ttl"),
        ("abc.celery.pidbox", "hash", {"x": 1}, -1, "control-plane-type"),
        ("abc.celery.pidbox", "list", [b"not-json"], -1, "control-plane-message"),
        ("abc.celery.pidbox", "list", [b"{}"] * 11, -1, "control-plane-backlog"),
    ],
    ids=["unknown", "mutex-without-ttl", "wrong-type", "unparseable", "backlog"],
)
def test_unclassifiable_broker_keys_are_incomplete(stack, key, kind, value, pttl, detail):
    stack.redis.put(key, kind, value, pttl=pttl)
    report = run(stack)
    check = checks(report)["broker_classification_before"]
    assert (check.result, check.detail_code) == (R.INCOMPLETE, detail)
    assert report.result is R.INCOMPLETE


def test_the_broker_is_never_flushed_or_purged(stack):
    run(stack)  # FakeBrokerRedis.flushdb lanza si alguien lo llamara
    assert "flushdb" not in stack.redis.calls
    assert set(stack.redis.calls) <= {
        "ping",
        "llen",
        "hlen",
        "zcard",
        "scan_iter",
        "type",
        "pttl",
        "lrange",
    }
    with pytest.raises(pf.PreflightViolation, match="broker-write"):
        pf.ReadOnlyRedis(object()).flushdb()
    assert not hasattr(pf.ReadOnlyRedis, "delete") and not hasattr(pf.ReadOnlyRedis, "purge")


# --- Guarda: sin carga ni evidencia --------------------------------------------------------------


@pytest.fixture
def guard(tmp_path):
    protected = tmp_path / "evidence"
    protected.mkdir()
    return pf.SideEffectGuard(protected_roots=(protected,), allowed_hosts=frozenset({"redis"}))


@pytest.mark.django_db
def test_the_guard_blocks_database_writes_and_model_saves(guard):
    category = Category.objects.get(slug="baches")
    with guard:
        with pytest.raises(pf.PreflightViolation, match="model-write"):
            Report.objects.create(category=category, description="x", location="POINT(0 0)")
        with pytest.raises(pf.PreflightViolation, match="database-write"):
            with connection.cursor() as cursor:
                cursor.execute("UPDATE categories SET name = name")
        assert Category.objects.filter(slug="baches").exists()  # las lecturas siguen
    assert Report.objects.count() == 0
    assert [kind for kind, _ in guard.violations] == ["model-write", "database-write"]


@pytest.mark.django_db
def test_the_guard_blocks_every_task_publication(guard):
    report_id = str(uuid.uuid4())
    with guard:
        with pytest.raises(pf.PreflightViolation, match="task-publish"):
            classify_report.delay(report_id)
        with pytest.raises(pf.PreflightViolation, match="task-publish"):
            classify_report.apply_async(args=[report_id])
        from config import celery_app

        with pytest.raises(pf.PreflightViolation, match="task-publish"):
            celery_app.send_task(ex10_load.TASK_NAME, args=[report_id])
    assert Task.apply_async.__name__ == "apply_async"  # restaurado al salir


def test_the_guard_blocks_writes_to_evidence_and_foreign_processes(guard, tmp_path):
    target = tmp_path / "evidence" / "manifest.json"
    with guard:
        with pytest.raises(pf.PreflightViolation, match="filesystem-write"):
            target.write_text("{}")
        with pytest.raises(pf.PreflightViolation, match="filesystem-write"):
            (tmp_path / "evidence" / "run").mkdir()
        with pytest.raises(pf.PreflightViolation, match="subprocess"):
            subprocess.run(["true"], check=False)
        with pytest.raises(pf.PreflightViolation, match="network"):
            socket.getaddrinfo("api.openai.com", 443)
        (tmp_path / "outside.txt").write_text("ok")  # fuera de las raíces protegidas
        assert (tmp_path / "evidence").is_dir()  # leer está permitido
    assert not target.exists() and not (tmp_path / "evidence" / "run").exists()
    target.write_text("{}")  # sin guarda activa, nada se bloquea
    target.unlink()


def test_the_guard_allows_approved_hosts_and_git(guard):
    with guard:
        guard._on_audit("socket.getaddrinfo", ("redis", 6379, 0, 0, 0))
        guard._on_audit("subprocess.Popen", ("git", ["git", "status"], None, None))
    assert guard.violations == []


def test_a_blocked_effect_makes_the_preflight_fail(stack):
    class WritingHealth(FakeHealth):
        def get(self):
            Path(stack.root / "runs" / "leak.json").write_text("{}")
            return super().get()

    stack.probes.health = WritingHealth()
    report = run(stack)
    check = checks(report)["no_side_effects"]
    assert check.result is R.FAIL
    assert check.observed_sanitized["blocked_by_kind"] == {"filesystem-write": 1}
    assert not (stack.root / "runs" / "leak.json").exists()
    assert report.to_json()["evidence_written"] is False
    # El paso abortado queda FAIL y lo que no llegó a emitir, NOT_RUN por ese aborto.
    assert checks(report)["health_aborted"].detail_code == "guard-filesystem-write"
    for name in ("health", "health_components"):
        assert checks(report)[name].detail_code == "blocked-by:health_aborted", name
    assert result_of(report, "check_coverage") is R.PASS
    assert (report.result, report.exit_code) == (R.FAIL, 1)


def test_a_full_preflight_writes_nothing_to_evidence_or_objects(ready_stack, isolated):
    before = sorted(p.relative_to(isolated.base) for p in isolated.base.rglob("*"))
    report = run(ready_stack)
    assert report.result is R.PASS
    assert sorted(p.relative_to(isolated.base) for p in isolated.base.rglob("*")) == before
    assert Report.objects.count() == 0


# --- Guarda: el destino PostgreSQL aprobado ------------------------------------------------------


@pytest.mark.parametrize(
    ("effective", "declared", "endpoint"),
    [
        ({"HOST": "db", "PORT": 5432}, "postgis://u@db:5432/ciudadania_s11_x", ("db", 5432)),
        ({"HOST": "db", "PORT": "5432"}, "postgis://u:p@db:5432/ciudadania_s11_x", ("db", 5432)),
        ({"HOST": "db", "PORT": ""}, "postgis://u@db/ciudadania_s11_x", ("db", 5432)),
        ({"HOST": "127.0.0.1", "PORT": 5432}, "postgis://u@127.0.0.1:5432/x", ("127.0.0.1", 5432)),
        ({"HOST": "::1", "PORT": 5432}, "postgis://u@[::1]:5432/x", ("::1", 5432)),
        ({"HOST": "db.", "PORT": 5432}, "postgis://u@db.:5432/x", ("db.", 5432)),
    ],
    ids=["hostname", "port-as-text", "default-port", "ipv4", "ipv6", "trailing-dot"],
)
def test_the_approved_database_endpoint_is_the_declared_s11_one(effective, declared, endpoint):
    assert pf._approved_database_endpoint(effective, declared) == endpoint


@pytest.mark.parametrize(
    ("effective", "declared"),
    [
        ({"HOST": "localhost", "PORT": 5432}, "postgis://u@db:5432/x"),
        ({"HOST": "redis", "PORT": 5432}, "postgis://u@db:5432/x"),
        ({"HOST": "db.evil", "PORT": 5432}, "postgis://u@db:5432/x"),
        ({"HOST": "evil-db", "PORT": 5432}, "postgis://u@db:5432/x"),
        ({"HOST": "db-evil", "PORT": 5432}, "postgis://u@db:5432/x"),
        ({"HOST": "db.example.invalid", "PORT": 5432}, "postgis://u@db:5432/x"),
        ({"HOST": "DB", "PORT": 5432}, "postgis://u@DB:5432/x"),
        ({"HOST": "db", "PORT": 5432}, "postgis://u@db.:5432/x"),
        ({"HOST": "[::1]", "PORT": 5432}, "postgis://u@[::1]:5432/x"),
        ({"HOST": "db", "PORT": 6543}, "postgis://u@db:5432/x"),
        ({"HOST": "db", "PORT": 5432}, "postgis://u@db:6543/x"),
        ({"HOST": "db", "PORT": "x"}, "postgis://u@db:5432/x"),
        ({"HOST": "db", "PORT": 5432}, "postgis://u@db:abc/x"),
        ({"HOST": "db", "PORT": 5432}, "postgis://u@[::1/x"),
        ({"HOST": "db", "PORT": 5432}, ""),
        ({"HOST": "", "PORT": 5432}, "postgis://u@/x"),
        ({"HOST": None, "PORT": 5432}, "postgis://u@db:5432/x"),
        ({"HOST": "/var/run/postgresql", "PORT": 5432}, "postgis://u@/x"),
        ({}, "postgis://u@db:5432/x"),
    ],
    ids=[
        "localhost",
        "redis-host",
        "suffix-domain",
        "prefix",
        "suffix",
        "other-domain",
        "uppercase",
        "trailing-dot",
        "bracketed-ipv6",
        "effective-port",
        "declared-port",
        "invalid-effective-port",
        "invalid-declared-port",
        "malformed-url",
        "no-declared-url",
        "no-host",
        "none-host",
        "unix-socket",
        "no-settings",
    ],
)
def test_no_database_endpoint_is_approved_outside_the_declared_one(effective, declared):
    assert pf._approved_database_endpoint(effective, declared) is None


@pytest.fixture
def database_guard(tmp_path):
    return pf.SideEffectGuard(
        protected_roots=(tmp_path,),
        allowed_hosts=frozenset({"redis", "backend"}),
        database_endpoint=("db", 5432),
    )


def test_the_guard_resolves_the_approved_database_destination(database_guard):
    with database_guard:
        for host, port in (("db", "5432"), ("db", b"5432"), ("db", 5432), (b"db", "5432")):
            database_guard._on_audit("socket.getaddrinfo", (host, port, 0, 1, 6))
    assert database_guard.violations == []


@pytest.mark.parametrize(
    ("host", "port"),
    [
        ("example.com", "443"),
        ("example.com", "5432"),
        ("localhost", "5432"),
        ("127.0.0.1", "5432"),
        ("db", "6379"),
        ("db", "5433"),
        ("db", None),
        ("db", "postgresql"),
        ("worker", "5432"),
        ("ciudadania-s11-db-1", "5432"),
        ("db.evil", "5432"),
        ("evil-db", "5432"),
        ("db-evil", "5432"),
        ("db.example.invalid", "5432"),
        ("DB", "5432"),
        ("db.", "5432"),
        ("10.0.0.5", "5432"),
        ("172.18.0.2", "5432"),
        ("::1", "5432"),
        ("fe80::1", "5432"),
        ("[::1]", "5432"),
        ("", "5432"),
        (None, "5432"),
    ],
)
def test_every_other_destination_stays_blocked(database_guard, host, port):
    with database_guard:
        with pytest.raises(pf.PreflightViolation, match="network"):
            socket.getaddrinfo(host, port)
    assert [kind for kind, _ in database_guard.violations] == ["network"]


@pytest.mark.parametrize(
    ("endpoint", "blocked"),
    [
        (("127.0.0.1", 5432), ("127.0.0.2", "0x7f000001", "localhost", "::1")),
        (("::1", 5432), ("[::1]", "0:0:0:0:0:0:0:1", "::2", "127.0.0.1")),
    ],
    ids=["ipv4", "ipv6"],
)
def test_an_approved_ip_literal_is_matched_exactly(tmp_path, endpoint, blocked):
    guard = pf.SideEffectGuard(
        protected_roots=(tmp_path,), allowed_hosts=frozenset(), database_endpoint=endpoint
    )
    with guard:
        guard._on_audit("socket.getaddrinfo", (endpoint[0], "5432", 0, 1, 6))
        with pytest.raises(pf.PreflightViolation, match="network"):
            socket.getaddrinfo(endpoint[0], "5433")
        for host in blocked:
            with pytest.raises(pf.PreflightViolation, match="network"):
                socket.getaddrinfo(host, "5432")
    assert [kind for kind, _ in guard.violations] == ["network"] * (len(blocked) + 1)


def test_without_an_approved_database_no_database_host_resolves(guard):
    assert guard.database_endpoint is None
    with guard:
        with pytest.raises(pf.PreflightViolation, match="network"):
            socket.getaddrinfo("db", "5432")


def test_a_resolved_database_address_is_never_approved(tmp_path, test_database):
    host, port = connection.settings_dict["HOST"], int(connection.settings_dict["PORT"] or 5432)
    guard = pf.SideEffectGuard(
        protected_roots=(tmp_path,), allowed_hosts=frozenset(), database_endpoint=(host, port)
    )
    with guard:
        answers = socket.getaddrinfo(host, str(port), type=socket.SOCK_STREAM)
        addresses = sorted({answer[4][0] for answer in answers})
        for address in addresses:
            with pytest.raises(pf.PreflightViolation, match="network"):
                socket.getaddrinfo(address, str(port))
    assert addresses and guard.violations == [("network", None)] * len(addresses)
    assert (guard.allowed_hosts, guard.database_endpoint) == (frozenset(), (host, port))


def test_the_preflight_guard_approves_the_declared_database_only(stack, settings):
    params = connection.settings_dict
    host, port = params["HOST"], int(params["PORT"] or 5432)
    settings.S11_DATABASE_URL = f"postgis://ciudadania@{host}:{port}/{DECLARED_DB}"
    assert pf._side_effect_guard(stack.options, stack.probes).database_endpoint == (host, port)
    settings.S11_DATABASE_URL = f"postgis://ciudadania@elsewhere-db:{port}/{DECLARED_DB}"
    assert pf._side_effect_guard(stack.options, stack.probes).database_endpoint is None


# --- Primera conexión en un proceso nuevo --------------------------------------------------------

#: Un proceso nuevo: Django sin conexión abierta y la guarda real del preflight. Escribe sus
#: hechos en un JSON (argv[2]) al terminar, ya sin guarda activa.
FRESH_PROCESS = r"""
import json, os, socket, sys
from pathlib import Path

import django

django.setup()
from django.conf import settings
from django.db import DatabaseError, connection

from apps.experiments_app import ex10_preflight as pf

mode, facts_path = sys.argv[1], Path(sys.argv[2])
database = settings.DATABASES["default"]
database["NAME"] = os.environ["EX10_FRESH_DB_NAME"]
if mode == "unresolvable":
    database["HOST"] = "ex10-unresolvable-db.invalid"
host, port = database["HOST"], int(database["PORT"] or 5432)
declared = os.environ.get("EX10_FRESH_DECLARED_DB", database["NAME"])
netloc = f"[{host}]" if ":" in host else host
settings.S11_DATABASE_URL = f"postgis://{database['USER']}@{netloc}:{port}/{declared}"
facts = {"connected_before": connection.connection is not None, "db_resolutions": 0}


def watch(event, args):
    # Cada resolución del destino PostgreSQL con la guarda ya activa, bloqueada o no.
    if event == "socket.getaddrinfo" and pf._ACTIVE.get() is not None:
        facts["db_resolutions"] += args[0] == host and pf._port_number(args[1]) == port


sys.addaudithook(watch)
if mode == "cli-unresolvable":
    # `db` detenido: el DNS de Docker responde EAI_AGAIN. La guarda decide antes, como en C.
    real_getaddrinfo = socket.getaddrinfo

    def unavailable(name, *args, **kwargs):
        if name == host:
            sys.audit("socket.getaddrinfo", name, args[0] if args else None, 0, 0, 0)
            raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
        return real_getaddrinfo(name, *args, **kwargs)

    socket.getaddrinfo = unavailable

if mode.startswith("cli"):
    from django.core.management import execute_from_command_line

    from apps.experiments_app.management.commands import s11_ex10_preflight as command

    class SilentControl:
        def probe(self, nonce, timeout):
            return []

    probes = pf.Probes(
        health=pf.HttpHealthClient(os.environ["EX10_FRESH_HEALTH_URL"], timeout=5),
        worker_control=SilentControl(),
    )
    command.Command.probes = lambda self: probes
    run_preflight, side_effect_guard = pf.run_preflight, pf._side_effect_guard

    def recording(options, probes=None):
        facts["connected_before"] = connection.connection is not None
        return run_preflight(options, probes)

    def capturing(options, probes):
        guard = side_effect_guard(options, probes)
        facts["endpoint_approved"] = guard.database_endpoint is not None
        facts["host_allowed_by_name"] = host in guard.allowed_hosts
        return guard

    pf.run_preflight, pf._side_effect_guard = recording, capturing
    source = os.environ["EX10_FRESH_SOURCE"]
    argv = ["manage.py", "s11_ex10_preflight", "--expected-git-sha", "0" * 40]
    try:
        execute_from_command_line([*argv, "--source-root", source])
        facts["exit"] = 0
    except SystemExit as exc:
        facts["exit"] = exc.code
else:
    options = pf.PreflightOptions("0" * 40, Path(os.environ["EX10_FRESH_SOURCE"]))
    probes = pf.Probes()
    if mode == "guard-2b2":
        # El cableado congelado de la 2B-2: sólo los hosts de Redis y health.
        guard = pf.SideEffectGuard(
            protected_roots=pf._protected_roots(options), allowed_hosts=pf._allowed_hosts(probes)
        )
    else:
        guard = pf._side_effect_guard(options, probes)
    facts["endpoint_approved"] = guard.database_endpoint is not None
    facts["host_allowed_by_name"] = host in guard.allowed_hosts
    try:
        with guard:
            facts["identity"] = pf.real_database_identity()
    except pf.PreflightViolation as exc:
        facts["error"] = f"guard-{exc.kind}"
    except DatabaseError as exc:
        facts["error"] = type(exc).__name__
    facts["violations"] = [kind for kind, _ in guard.violations]
facts["connected_after"] = connection.connection is not None
facts_path.write_text(json.dumps(facts))
raise SystemExit(facts.get("exit") or 0)
"""
#: Redis en un host distinto del de la base: si lo compartieran (`localhost` en CI), la regla
#: de Redis, por nombre, ya dejaría resolver la base y el control de la 2B-2 no probaría nada.
GUARD_ONLY_REDIS = {"REDIS_URL": "redis://redis:6379/11"}


def fresh_process(mode, tmp_path, database, **env):
    from django.conf import settings

    facts, source = tmp_path / f"facts-{mode}.json", tmp_path / "source"
    source.mkdir(exist_ok=True)
    completed = subprocess.run(
        [sys.executable, "-c", FRESH_PROCESS, mode, str(facts)],
        cwd=settings.BASE_DIR,
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings.test",
            "EX10_FRESH_DB_NAME": database,
            "EX10_FRESH_SOURCE": str(source),
            **env,
        },
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert facts.is_file(), completed.stderr[-3000:]
    return completed, json.loads(facts.read_text())


@pytest.fixture
def test_database(django_db_setup):
    """La base de tests, sin abrirla aquí. Con una IP literal psycopg no resuelve nada."""
    try:
        ipaddress.ip_address(connection.settings_dict["HOST"])
    except ValueError:
        return connection.settings_dict["NAME"]
    pytest.skip("con una IP literal no hay primera resolución que probar")


@pytest.fixture
def s11_database(test_database):
    """Una base `ciudadania_s11_*` propia y vacía: el aislamiento exige ese prefijo."""
    params = connection.settings_dict
    name = f"ciudadania_s11_fresh_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(
        host=params["HOST"],
        port=params["PORT"] or 5432,
        user=params["USER"],
        password=params["PASSWORD"],
        dbname="postgres",
        autocommit=True,
    ) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            yield name
        finally:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def fresh_roots(request):
    base = Path.cwd() / ".s11-test-artifacts" / f"ex10-preflight-fresh-{request.node.name}"
    shutil.rmtree(base, ignore_errors=True)
    roots = {name: base / name for name in ("objects", "runs", "backup", "restore")}
    for path in roots.values():
        path.mkdir(parents=True)
    (roots["objects"] / st.OBJECT_ROOT_MARKER).write_text("S11 isolated synthetic objects only\n")
    redis = urlsplit(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    yield {
        "S11_EXPERIMENTS_ENABLED": "true",
        "S11_ENVIRONMENT": "isolated-experiment",
        "REDIS_URL": f"redis://{redis.hostname}:{redis.port or 6379}/11",
        "S11_OBJECT_ROOT": str(roots["objects"]),
        "S11_ARTIFACT_ROOT": str(roots["runs"]),
        "S11_BACKUP_ROOT": str(roots["backup"]),
        "S11_RESTORE_ROOT": str(roots["restore"]),
    }
    shutil.rmtree(base)


def test_a_fresh_process_opens_its_first_connection_through_the_real_guard(tmp_path, test_database):
    completed, facts = fresh_process("guard", tmp_path, test_database, **GUARD_ONLY_REDIS)
    assert completed.returncode == 0, completed.stderr[-3000:]
    assert facts["connected_before"] is False
    assert (facts["endpoint_approved"], facts["host_allowed_by_name"]) == (True, False)
    assert facts["db_resolutions"] >= 1  # psycopg resolvió el host ya con la guarda activa
    assert (facts.get("error"), facts["violations"]) == (None, [])
    assert facts["identity"] == {"database": True, "user": True, "host": True, "port": True}
    assert facts["connected_after"] is True


def test_the_frozen_2b2_guard_blocks_that_same_first_connection(tmp_path, test_database):
    # Control: el mismo proceso nuevo con la allowlist de la 2B-2. La condición es real aquí
    # y la regresión anterior falla con ella.
    completed, facts = fresh_process("guard-2b2", tmp_path, test_database, **GUARD_ONLY_REDIS)
    assert completed.returncode == 0, completed.stderr[-3000:]
    assert facts["connected_before"] is False
    assert (facts["endpoint_approved"], facts["host_allowed_by_name"]) == (False, False)
    assert facts["db_resolutions"] >= 1
    assert (facts["error"], facts["violations"]) == ("guard-network", ["network"])
    assert facts["connected_after"] is False


def test_approving_the_resolution_does_not_approve_another_database(tmp_path, test_database):
    completed, facts = fresh_process(
        "guard",
        tmp_path,
        test_database,
        EX10_FRESH_DECLARED_DB="ciudadania_s11_elsewhere",
        **GUARD_ONLY_REDIS,
    )
    assert (facts["endpoint_approved"], facts["violations"]) == (True, [])
    # La base realmente conectada sigue contrastándose: aquí no es la declarada.
    assert facts["identity"] == {"database": False, "user": True, "host": True, "port": True}


def test_an_approved_host_that_does_not_resolve_is_unavailable_not_blocked(tmp_path, test_database):
    completed, facts = fresh_process("unresolvable", tmp_path, test_database, **GUARD_ONLY_REDIS)
    assert completed.returncode == 0, completed.stderr[-3000:]
    assert facts["endpoint_approved"] is True
    assert facts["db_resolutions"] >= 1
    assert (facts["error"], facts["violations"]) == ("OperationalError", [])


def json_values(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from json_values(value)
    elif isinstance(node, list):
        for value in node:
            yield from json_values(value)
    else:
        yield node


#: `endpoint_facts` tal cual, o como lo publican `api_broker` y `api_result_backend`.
REDIS_ENDPOINT_SHAPES = (
    {"scheme", "host", "db", "matches_protocol"},
    {"scheme", "host", "db", "matches_protocol", "same_as_approved"},
)


def without_redis_endpoint_hosts(node):
    """El informe sin el `host` de los hechos de endpoint Redis (`endpoint_facts`), que se
    publican a propósito. Si Redis y la base comparten host (`localhost` en CI), ese valor no
    es una fuga del destino PostgreSQL aprobado; cualquier otra aparición, sí."""
    if isinstance(node, dict):
        if set(node) in REDIS_ENDPOINT_SHAPES and str(node["scheme"]).startswith("redis"):
            return {key: value for key, value in node.items() if key != "host"}
        return {key: without_redis_endpoint_hosts(value) for key, value in node.items()}
    if isinstance(node, list):
        return [without_redis_endpoint_hosts(value) for value in node]
    return node


def test_only_redis_endpoint_hosts_are_excluded_from_the_leak_scan():
    # La excepción es exacta: el mismo host fuera de un endpoint Redis sigue contando.
    redis = {"scheme": "redis", "host": "localhost", "db": 11, "matches_protocol": True}
    report = {
        "api_broker": {**redis, "same_as_approved": True},
        "approved": redis,
        "isolation_database_connected": {"host": "localhost"},
        "other": [{"scheme": "postgis", "host": "localhost", "db": 1, "matches_protocol": True}],
        "extra": {**redis, "port": 5432},
    }
    assert [v for v in json_values(without_redis_endpoint_hosts(report)) if v == "localhost"] == [
        "localhost",
        "localhost",
        "localhost",
    ]


@pytest.mark.parametrize("mode", ["cli", "cli-unresolvable"])
def test_the_cli_in_a_fresh_process_reaches_its_database_through_the_guard(
    tmp_path, s11_database, fresh_roots, health_server, mode
):
    completed, facts = fresh_process(
        mode, tmp_path, s11_database, EX10_FRESH_HEALTH_URL=health_server.url, **fresh_roots
    )
    report = json.loads(completed.stdout)
    by_name = {check["check"]: check for check in report["checks"]}
    assert facts["connected_before"] is False
    # Con Redis en el mismo host que la base (CI) la regla de Redis también lo cubriría.
    assert facts["endpoint_approved"] is True
    assert facts["db_resolutions"] >= 1
    assert [name for name in by_name if name.endswith("_aborted")] == []
    assert [c["check"] for c in report["checks"] if "guard-" in (c["detail_code"] or "")] == []
    assert by_name["isolation_guards"]["result"] == "PASS"
    connected = by_name["isolation_database_connected"]
    if mode == "cli":
        assert (connected["result"], connected["observed_sanitized"]["database"]) == ("PASS", True)
    else:
        assert (connected["result"], connected["detail_code"]) == (
            "INCOMPLETE",
            "database-unavailable",
        )
    assert by_name["no_side_effects"]["result"] == "PASS"
    assert (report["health_requests"], len(health_server.requests)) == (1, 1)
    # Sin checkout Git ni worker: INCOMPLETE, nunca un FAIL de la guarda.
    assert (report["result"], completed.returncode, facts["exit"]) == ("INCOMPLETE", 2, 2)
    # El destino aprobado no llega al output: ni host, ni direcciones, ni puerto, ni DSN.
    host, port = connection.settings_dict["HOST"], int(connection.settings_dict["PORT"] or 5432)
    addresses = {answer[4][0] for answer in socket.getaddrinfo(host, port)}
    values = list(json_values(without_redis_endpoint_hosts(report)))
    strings = [value for value in values if isinstance(value, str)]
    assert [value for value in strings if value in {host, str(port), *addresses}] == []
    assert port not in [value for value in values if type(value) is int]
    for forbidden in (s11_database, "postgis", f"@{host}", *addresses):
        assert forbidden not in completed.stdout


# --- Git real ------------------------------------------------------------------------------------


def git(root, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def tree_state(root):
    """Huella de todo el checkout, `.git` incluido: contenido y mtime de cada archivo."""
    state = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            stat = path.stat()
            state[str(path.relative_to(root))] = (path.read_bytes(), stat.st_mtime_ns)
    return state


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """Checkout completo sintético: el código en ejecución y tres raíces de primer nivel."""
    from django.conf import settings

    root = tmp_path / "checkout"
    running = Path(settings.BASE_DIR)
    for relative in pf.RUNNING_SOURCES:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((running / relative.removeprefix("backend/")).read_bytes())
    for relative, text in (("frontend/package.json", "{}\n"), ("infra/compose.yml", "{}\n")):
        (root / relative).parent.mkdir(parents=True)
        (root / relative).write_text(text)
    (root / ".gitignore").write_text("__pycache__/\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "one")
    first = git(root, "rev-parse", "HEAD")
    (root / "infra" / "compose.yml").write_text("services: {}\n")
    git(root, "commit", "-q", "-am", "two")
    # Los commits de G0 de este historial sintético.
    monkeypatch.setattr(pf, "G0_DECISION_COMMIT", first)
    monkeypatch.setattr(pf, "G0_A1_DECISION_COMMIT", first)
    monkeypatch.setattr(pf, "G0_A2_DECISION_COMMIT", first)
    return SimpleNamespace(root=root, first=first, head=git(root, "rev-parse", "HEAD"))


def on_checkout(stack, root, expected=None):
    stack.probes.repository = pf.GitRepository
    stack.options = dataclasses.replace(
        stack.options,
        source_root=root,
        expected_git_sha=expected or git(root, "rev-parse", "HEAD"),
    )
    return run(stack)


def test_a_complete_clean_checkout_passes(stack, checkout):
    report = on_checkout(stack, checkout.root)
    assert report.result is R.PASS, [c for c in report.checks if c.result is not R.PASS]
    source = checks(report)["repository_source_complete"]
    assert source.observed_sanitized == {"git_dir": "directory", **pf.COMPLETE_SOURCE}
    for name in REPOSITORY_DEPENDENTS:
        assert result_of(report, name) is R.PASS, name
    assert report.git_sha == checkout.head


def _untracked(root):
    (root / "infra" / "nuevo").mkdir()
    (root / "infra" / "nuevo" / "suelto.txt").write_text("u\n")


@pytest.mark.parametrize(
    "change",
    [
        lambda root: (root / "frontend" / "package.json").write_text('{"x": 1}\n'),
        _untracked,
        lambda root: (root / "infra" / "compose.yml").unlink(),  # un archivo, no una raíz
    ],
    ids=["tracked-modified", "untracked", "tracked-deleted"],
)
def test_a_complete_checkout_with_changes_is_not_ready(stack, checkout, change):
    change(checkout.root)
    report = on_checkout(stack, checkout.root)
    assert result_of(report, "repository_source_complete") is R.PASS
    check = checks(report)["repository_clean"]
    assert (check.result, check.detail_code) == (R.FAIL, "working-tree-dirty")
    assert check.observed_sanitized["dirty_entries"] >= 1
    assert (report.result, report.exit_code) == (R.FAIL, 1)


def record_git(monkeypatch):
    calls = []
    real_run = subprocess.run

    def recording(argv, **kwargs):
        calls.append((tuple(argv), dict(kwargs.get("env") or {})))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(pf.subprocess, "run", recording)
    return calls


def exported(tmp_path, checkout):
    target = tmp_path / "export"
    shutil.copytree(checkout.root, target, ignore=shutil.ignore_patterns(".git"))
    return target


@pytest.mark.parametrize(
    "source",
    [
        exported,  # árbol completo sin `.git`, como una copia o `/app`
        lambda tmp_path, checkout: checkout.root / "backend",  # subdirectorio del repositorio
        lambda tmp_path, checkout: tmp_path,  # fuera de todo repositorio
    ],
    ids=["no-git", "subdirectory", "outside-repo"],
)
def test_a_root_without_its_own_git_is_incomplete_and_git_never_runs(
    stack, checkout, tmp_path, monkeypatch, source
):
    root = source(tmp_path, checkout)
    calls = record_git(monkeypatch)
    report = on_checkout(stack, root, expected=checkout.head)
    check = checks(report)["repository_source_complete"]
    assert (check.result, check.detail_code) == (R.INCOMPLETE, "source-root-not-git")
    for name in REPOSITORY_DEPENDENTS:
        assert checks(report)[name].detail_code == "blocked-by:repository_source_complete"
    assert calls == []  # ni HEAD ni limpieza se infieren; tampoco del repositorio padre
    assert (report.result, report.git_sha) == (R.INCOMPLETE, None)


def test_a_linked_worktree_is_a_complete_checkout(stack, checkout, tmp_path):
    worktree = tmp_path / "worktree"
    git(checkout.root, "worktree", "add", "-q", "--detach", str(worktree), "HEAD")
    report = on_checkout(stack, worktree)
    source = checks(report)["repository_source_complete"]
    assert (source.result, source.observed_sanitized["git_dir"]) == (R.PASS, "gitfile")
    assert report.result is R.PASS


@pytest.mark.parametrize(
    "breakage",
    [
        lambda root, tmp: (root / ".git").write_text(f"gitdir: {tmp / 'no-such-gitdir'}\n"),
        lambda root, tmp: (root / ".git").write_text("esto no es un gitfile\n"),
        lambda root, tmp: (root / ".git").symlink_to(tmp / "no-such-target"),
    ],
    ids=["missing-gitdir", "garbage", "dangling-symlink"],
)
def test_an_unresolvable_gitfile_is_incomplete(stack, checkout, tmp_path, breakage):
    root = exported(tmp_path, checkout)
    breakage(root, tmp_path)
    check = checks(on_checkout(stack, root, expected=checkout.head))["repository_source_complete"]
    assert (check.result, check.detail_code) == (R.INCOMPLETE, "broken-gitfile")


def test_a_worktree_whose_admin_directory_is_gone_is_incomplete(stack, checkout, tmp_path):
    worktree = tmp_path / "worktree"
    git(checkout.root, "worktree", "add", "-q", "--detach", str(worktree), "HEAD")
    shutil.rmtree(checkout.root / ".git" / "worktrees" / "worktree")
    check = checks(on_checkout(stack, worktree, expected=checkout.head))[
        "repository_source_complete"
    ]
    assert (check.result, check.detail_code) == (R.INCOMPLETE, "broken-gitfile")


def _sparse(root):
    git(root, "sparse-checkout", "set", "backend", "infra")


def _sparse_enabled(root):
    # Todavía no oculta nada, pero el mecanismo de vista parcial está activo.
    git(root, "sparse-checkout", "set", "--no-cone", "/*")


def _skip_worktree(root):
    git(root, "update-index", "--skip-worktree", "frontend/package.json")
    (root / "frontend" / "package.json").write_text('{"oculto": true}\n')


def _assume_unchanged(root):
    git(root, "update-index", "--assume-unchanged", "frontend/package.json")
    (root / "frontend" / "package.json").write_text('{"oculto": true}\n')


def _local_exclude(root):
    (root / ".git" / "info").mkdir(exist_ok=True)
    (root / ".git" / "info" / "exclude").write_text("# comentario\nescondido/\n")
    (root / "escondido").mkdir()
    (root / "escondido" / "nuevo.py").write_text("x = 1\n")


@pytest.mark.parametrize(
    ("breakage", "fact", "hides_changes"),
    [
        (lambda root: shutil.rmtree(root / "frontend"), "missing_top_level", False),
        (
            lambda root: (root / pf.RUNNING_SOURCES[0]).unlink(),
            "missing_required",
            False,
        ),
        (_sparse, "sparse", True),
        (_sparse_enabled, "sparse", False),
        (_skip_worktree, "hidden_index_entries", True),
        (_assume_unchanged, "hidden_index_entries", True),
        (_local_exclude, "local_excludes", True),
    ],
    ids=[
        "missing-top-level",
        "missing-required",
        "sparse",
        "sparse-enabled",
        "skip-worktree",
        "assume-unchanged",
        "local-exclude",
    ],
)
def test_a_partial_view_with_git_is_incomplete_and_cleanliness_is_not_inferred(
    stack, checkout, breakage, fact, hides_changes
):
    breakage(checkout.root)
    if hides_changes:  # control: un `git status` corriente no ve nada
        assert git(checkout.root, "status", "--porcelain", "--untracked-files=all") == ""
    report = on_checkout(stack, checkout.root)
    check = checks(report)["repository_source_complete"]
    assert (check.result, check.detail_code) == (R.INCOMPLETE, "partial-source-root")
    assert check.observed_sanitized[fact] not in (0, False)
    assert checks(report)["repository_clean"].detail_code == "blocked-by:repository_source_complete"
    assert (report.result, report.git_sha) == (R.INCOMPLETE, None)


def test_a_shallow_clone_or_a_foreign_work_tree_is_incomplete(stack, checkout, tmp_path):
    shallow = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth", "1", f"file://{checkout.root}", str(shallow))
    check = checks(on_checkout(stack, shallow))["repository_source_complete"]
    assert (check.result, check.detail_code) == (R.INCOMPLETE, "shallow-source-root")
    git(checkout.root, "config", "core.worktree", str(checkout.root / "infra"))
    check = checks(on_checkout(stack, checkout.root, expected=checkout.head))[
        "repository_source_complete"
    ]
    assert (check.result, check.detail_code) == (R.INCOMPLETE, "source-root-not-toplevel")


def test_a_complete_checkout_matches_every_executor_runtime_source(stack, checkout):
    # (10) El preflight con checkout completo sigue pasando, ahora también por el executor.
    report = on_checkout(stack, checkout.root)
    assert report.result is R.PASS
    sources = running_sources(report)
    assert all(source["match"] for source in sources.values())
    assert set(EXECUTOR_RUNTIME_SOURCES) <= set(sources)


@pytest.mark.parametrize("relative", EXECUTOR_RUNTIME_SOURCES)
def test_a_checkout_without_an_executor_source_is_incomplete(stack, checkout, relative):
    # (6) El checkout calificado no tiene la fuente: vista parcial, INCOMPLETE canónico.
    git(checkout.root, "rm", "-q", relative)
    git(checkout.root, "commit", "-q", "-m", "without executor source")
    report = on_checkout(stack, checkout.root)
    check = checks(report)["repository_source_complete"]
    assert (check.result, check.detail_code) == (R.INCOMPLETE, "partial-source-root")
    assert check.observed_sanitized["missing_required"] == 1
    assert report.result is R.INCOMPLETE


def test_running_code_that_differs_from_a_clean_checkout_fails(stack, checkout):
    changed = checkout.root / "backend" / "apps" / "experiments_app" / "ex10_state.py"
    changed.write_bytes(changed.read_bytes() + b"# otra revision\n")
    git(checkout.root, "commit", "-q", "-am", "three")
    report = on_checkout(stack, checkout.root)
    assert result_of(report, "repository_clean") is R.PASS
    check = checks(report)["repository_running_source"]
    assert (check.result, check.observed_sanitized["mismatched"]) == (R.FAIL, 1)
    assert (report.result, report.exit_code) == (R.FAIL, 1)


def test_git_takes_no_optional_locks_and_changes_no_index_ref_or_file(stack, checkout, monkeypatch):
    root = checkout.root
    # Índice «racy»: mtime nuevo con el mismo contenido. Un `git status` corriente lo
    # refrescaría reescribiendo `.git/index`.
    stamp = (root / "frontend" / "package.json").stat().st_mtime_ns + 5_000_000_000
    os.utime(root / "frontend" / "package.json", ns=(stamp, stamp))
    before = tree_state(root)
    calls = record_git(monkeypatch)
    repo = pf.GitRepository(root)
    assert repo.completeness().problem is None
    assert (repo.head(), repo.dirty_entries()) == (checkout.head, 0)
    assert repo.is_ancestor(checkout.first) and not repo.is_ancestor("0" * 40)
    assert on_checkout(stack, root, expected=checkout.head).result is R.PASS
    assert tree_state(root) == before  # índice, refs, config, logs y árbol intactos
    assert calls
    for argv, env in calls:
        assert argv[:2] == ("git", "--no-optional-locks"), argv
        assert (env["GIT_OPTIONAL_LOCKS"], env["GIT_CONFIG_GLOBAL"]) == ("0", "/dev/null")
        assert not {"commit", "stash", "reset", "clean", "checkout", "add"} & set(argv)
    monkeypatch.undo()
    git(root, "status", "--porcelain")  # control negativo: sin esas opciones sí escribe
    assert tree_state(root)[".git/index"] != before[".git/index"]


# --- CLI y códigos de salida ----------------------------------------------------------------------


def call(stack, monkeypatch, *args):
    monkeypatch.setattr(command_module.Command, "probes", lambda self: stack.probes)
    out, err = io.StringIO(), io.StringIO()
    argv = args or ("--expected-git-sha", SHA, "--source-root", str(stack.options.source_root))
    try:
        call_command("s11_ex10_preflight", *argv, stdout=out, stderr=err)
        code = 0
    except SystemExit as exc:
        code = exc.code
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize(
    ("breakage", "code", "gate"),
    [
        (None, 0, "READY_FOR_EXECUTOR_INTEGRATION"),
        (lambda s: setattr(s.repo, "dirty", 1), 1, "NOT_READY"),
        (lambda s: setattr(s.health, "error", pf.HealthUnavailable()), 2, "INCOMPLETE"),
    ],
    ids=["exit-0", "exit-1", "exit-2"],
)
def test_cli_prints_one_json_and_exits_with_the_gate(stack, monkeypatch, breakage, code, gate):
    if breakage:
        breakage(stack)
    exit_code, out, err = call(stack, monkeypatch)
    payload = json.loads(out)
    assert (exit_code, payload["gate"], err) == (code, gate, "")
    assert out.count("\n") == 1


@pytest.mark.parametrize(
    "argv",
    [
        ("--source-root", "/nope"),
        ("--expected-git-sha", "not-a-sha", "--source-root", "/"),
        ("--expected-git-sha", "a" * 40, "--source-root", "/no/such/root"),
        ("--expected-git-sha", "a" * 40, "--source-root", "/", "--probe-timeout", "999"),
        ("--expected-git-sha", "a" * 40, "--source-root", "/", "--git-clean"),
    ],
    ids=["missing-sha", "bad-sha", "missing-root", "timeout", "no-git-clean-flag"],
)
def test_cli_usage_and_configuration_errors_exit_3(stack, monkeypatch, argv):
    monkeypatch.setattr(command_module.Command, "probes", lambda self: stack.probes)
    with pytest.raises(CommandError) as error:
        call_command("s11_ex10_preflight", *argv, stdout=io.StringIO(), stderr=io.StringIO())
    assert error.value.returncode == 3
    assert "/no/such/root" not in str(error.value) and "999" not in str(error.value)


def test_cli_from_the_terminal_exits_3_with_a_sanitized_stderr():
    completed = subprocess.run(
        [sys.executable, "manage.py", "s11_ex10_preflight", f"--bogus={CANARY}"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 3
    assert completed.stdout == ""
    assert completed.stderr.strip() == "S11_EX10_PREFLIGHT_USAGE: invalid-arguments"


def test_cli_internal_errors_exit_3_without_the_original_message(stack, monkeypatch):
    def broken():
        raise RuntimeError(f"secret-path=/home/x token={CANARY}")

    stack.probes.protocol_identity = broken
    monkeypatch.setattr(command_module.Command, "probes", lambda self: stack.probes)
    with pytest.raises(CommandError) as error:
        call_command(
            "s11_ex10_preflight",
            "--expected-git-sha",
            SHA,
            "--source-root",
            str(stack.options.source_root),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
    assert error.value.returncode == 3
    assert str(error.value) == "S11_EX10_PREFLIGHT_INTERNAL_ERROR: RuntimeError"


# --- Seguridad del output -----------------------------------------------------------------------


def test_output_holds_no_dsn_env_body_image_or_coordinates(ready_stack, monkeypatch, settings):
    monkeypatch.setenv("S11_CANARY_ENV", CANARY)
    raw = run(ready_stack).to_bytes().decode()
    dataset = ex10_load.load_dataset()
    forbidden = [
        CANARY,
        "postgis://",
        "redis://",
        "Content-Disposition",
        "multipart/form-data",
        "s11ex10",  # prefijo de los boundaries
        "iVBORw0KGgo",
        "Authorization",
        "Bearer",
        "eyJ",
        str(settings.S11_ARTIFACT_ROOT),
        str(ready_stack.root),
    ]
    for row in dataset.payload["requests"]:
        forbidden += [repr(row["latitude"]), repr(row["longitude"])]
    assert [index for index, value in enumerate(forbidden) if value in raw] == []


def test_observed_values_are_validated_before_they_are_emitted():
    with pytest.raises(ValueError):
        pf.Check("x", R.PASS, None, {"url": "redis://u:p@redis:6379/11"})
    with pytest.raises(ValueError):
        pf.Check("x", R.PASS, None, object())


# --- Concurrencia y registro del probe ---------------------------------------------------------


def test_a_second_preflight_in_the_same_process_is_rejected(stack):
    entered, release = threading.Event(), threading.Event()

    def blocking(nonce):
        entered.set()
        release.wait(10)
        return [worker_reply(nonce)]

    stack.probes.worker_control = FakeControl(blocking, lambda n: [worker_reply(n)])
    outcome = {}

    def first():
        outcome["first"] = run(stack)

    thread = threading.Thread(target=first, daemon=True)
    thread.start()
    assert entered.wait(10)
    with pytest.raises(pf.PreflightBusy):
        run(stack)
    release.set()
    thread.join(30)
    assert not thread.is_alive()
    assert outcome["first"].result is R.PASS


def test_register_is_idempotent_and_refuses_a_foreign_command():
    wp.register()
    wp.register()
    assert Panel.data[wp.PROBE_COMMAND] is wp.worker_probe
    assert list(Panel.data).count(wp.PROBE_COMMAND) == 1
    original = Panel.data.pop(wp.PROBE_COMMAND)
    try:
        Panel.data[wp.PROBE_COMMAND] = lambda state, **kwargs: None
        with pytest.raises(RuntimeError):
            wp.register()
    finally:
        Panel.data[wp.PROBE_COMMAND] = original


def test_importing_twice_does_not_duplicate_the_command():
    import importlib

    from apps.experiments_app import tasks

    wp.register()
    registered, meta = Panel.data[wp.PROBE_COMMAND], Panel.meta[wp.PROBE_COMMAND]
    importlib.reload(tasks)
    importlib.reload(tasks)
    assert list(Panel.data).count(wp.PROBE_COMMAND) == 1
    # El registro se salta si ya es el probe: ni función ni metadatos cambian.
    assert Panel.data[wp.PROBE_COMMAND] is registered
    assert Panel.meta[wp.PROBE_COMMAND] is meta


# --- Probe del worker (lado worker) --------------------------------------------------------------


def fake_state(**conf):
    config = SimpleNamespace(
        task_always_eager=False,
        broker_url="redis://redis:6379/11",
        result_backend="redis://redis:6379/11",
        **conf,
    )
    pool = SimpleNamespace(
        info={"implementation": "celery.concurrency.prefork:TaskPool", "max-concurrency": 2}
    )
    app = SimpleNamespace(conf=config, tasks={ex10_load.TASK_NAME: object()})
    return SimpleNamespace(app=app, hostname="celery@worker-1", consumer=SimpleNamespace(pool=pool))


def test_the_worker_payload_is_exactly_the_whitelist():
    nonce = "0" * 31 + "1"
    payload = wp.probe_payload(fake_state(), nonce)
    assert set(payload) == wp.RESPONSE_FIELDS
    facts = wp.validate_reply({"celery@worker-1": payload}, nonce)
    assert facts["pool"]["max_concurrency"] == 2 and facts["broker"] == ENDPOINT
    assert "://" not in json.dumps(payload)


def test_the_worker_payload_never_raises_nor_reflects_the_input():
    payload = wp.probe_payload(SimpleNamespace(), f"not-a-nonce-{CANARY}")
    assert payload == {
        "schema_version": wp.PROBE_SCHEMA_VERSION,
        "nonce": None,
        "error": "probe-failed",
    }


def test_endpoint_facts_never_carry_credentials():
    facts = wp.endpoint_facts(f"redis://user:{CANARY}@redis:6379/11")
    assert facts == {"scheme": "redis", "host": "redis", "db": 11, "matches_protocol": False}
    assert wp.endpoint_facts("redis://redis:6379/11")["matches_protocol"] is True
    assert wp.endpoint_facts("redis://redis:6379/0")["matches_protocol"] is False
    assert wp.endpoint_facts("redis://redis:notaport/11")["scheme"] == "invalid"
    assert wp.endpoint_facts(None)["matches_protocol"] is False


def test_effective_adapter_is_read_without_building_a_provider(monkeypatch):
    from apps.ai_client_app import tasks as ai_tasks
    from apps.experiments_app.ai_runtime import get_experimental_runtime_adapter

    monkeypatch.setattr(ai_tasks, "get_runtime_adapter", get_experimental_runtime_adapter)
    assert wp.effective_ai_adapter() == "s11-deterministic-fake-v1"
    monkeypatch.setattr(ai_tasks, "get_runtime_adapter", lambda: pytest.fail("provider built"))
    assert wp.effective_ai_adapter() == "not-s11-fake"


def test_token_lifetime_boundary_is_g0(stack):
    def short(tokens, result, now):
        return tuple(ex10_fixture.TokenCheck(a.alias, True, True, False) for a in result.actors)

    stack.probes.verify_tokens = short
    check = checks(run(stack))["token_issuance_capability"]
    assert (check.result, check.observed_sanitized["remaining_ok"]) == (R.FAIL, 0)
    assert ex10_fixture.MIN_TOKEN_REMAINING == timedelta(minutes=10)
