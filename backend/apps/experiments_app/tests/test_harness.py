import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.management.base import CommandError

from apps.experiments_app.harness import (
    FullStackHarness,
    HarnessError,
    _backend_url_from_compose_port,
    _wait_for_backend,
    build_plan,
    verify_frontend_tooling,
    verify_repository,
)
from apps.experiments_app.management.commands.s11_prepare_run import (
    backend_runtime_configuration,
)

RUN_ID = "s11-00000000-0000-5000-8000-000000000001"
ASSIGNED_PORT = 49152
BACKEND_URL = f"http://127.0.0.1:{ASSIGNED_PORT}"


def _environment():
    return {
        "S11_EXPERIMENTS_ENABLED": "true",
        "S11_ENVIRONMENT": "isolated-experiment",
        "S11_CUSTODY_APPROVED": "true",
        "S11_DOCUMENT_HMAC_SECRET": "synthetic-test-secret",
    }


def _is_port_discovery(command):
    return command[-3:] == ("port", "backend", "8000")


def _successful_stdout(command):
    if _is_port_discovery(command):
        return f"127.0.0.1:{ASSIGNED_PORT}\n"
    if "s11_prepare_run" in command:
        return f'{{"run_id": "{RUN_ID}"}}'
    return ""


def test_plan_has_start_discovery_setup_and_lifecycle_for_each_scenario():
    for number in range(2, 9):
        scenario = f"EX-{number:02d}"
        plan = build_plan(
            scenario_id=scenario,
            execution_nonce="test-plan",
            expected_git_sha="a" * 40,
        )
        assert plan.status == "READY"
        assert plan.start and plan.setup and plan.execute and plan.collect
        assert plan.validate and plan.cleanup
        assert plan.discover_backend[-3:] == ("port", "backend", "8000")
        assert any("create" in command for command in plan.setup)
        assert any("reset" in command for command in plan.setup)
        prepare = next(command for command in plan.setup if "s11_prepare_run" in command)
        assert "--backend-url" in prepare
        assert "{backend_url}" in prepare
        assert "--backend-discovered-at" in prepare
        assert f"--grep=@{scenario}" in plan.execute
        assert "--concurrency=2" not in " ".join(plan.execute)


def test_harness_requires_explicit_custody_gate(tmp_path):
    plan = build_plan(
        scenario_id="EX-02",
        execution_nonce="test-gate",
        expected_git_sha="a" * 40,
    )
    harness = FullStackHarness(tmp_path, runner=lambda *args, **kwargs: None)
    with pytest.raises(HarnessError, match="S11_CUSTODY_APPROVED"):
        harness.execute(
            plan,
            environment={
                "S11_EXPERIMENTS_ENABLED": "true",
                "S11_ENVIRONMENT": "isolated-experiment",
                "S11_DOCUMENT_HMAC_SECRET": "synthetic-test-secret",
            },
        )


def test_harness_always_runs_cleanup_and_surfaces_failure(tmp_path):
    calls = []

    def runner(command, *, cwd, env):
        calls.append(command)
        return SimpleNamespace(
            returncode=1 if "playwright" in command else 0,
            stdout=_successful_stdout(command),
            stderr="synthetic failure" if "playwright" in command else "",
        )

    plan = build_plan(
        scenario_id="EX-03",
        execution_nonce="test-failure",
        expected_git_sha="a" * 40,
    )
    with pytest.raises(HarnessError, match="sin ocultarlo"):
        FullStackHarness(
            tmp_path,
            runner=runner,
            backend_probe=lambda backend_url: None,
        ).execute(plan, environment=_environment())
    assert any("s11_environment" in command and "reset" in command for command in calls)
    assert any("s11_finalize_run" in command and "FAILED" in command for command in calls)
    assert any(
        "s11_record_harness_log" in command and RUN_ID in " ".join(command) for command in calls
    )


def test_harness_records_missing_playwright_runtime_as_failed(tmp_path):
    calls = []

    def runner(command, *, cwd, env):
        calls.append(command)
        if "playwright" in command:
            raise FileNotFoundError(2, "No such file or directory", command[0])
        return SimpleNamespace(returncode=0, stdout=_successful_stdout(command), stderr="")

    plan = build_plan(
        scenario_id="EX-02",
        execution_nonce="test-missing-runtime",
        expected_git_sha="a" * 40,
    )
    with pytest.raises(HarnessError, match="FileNotFoundError"):
        FullStackHarness(
            tmp_path,
            runner=runner,
            backend_probe=lambda backend_url: None,
        ).execute(plan, environment=_environment())

    log_path = tmp_path / "var" / "s11-runs" / "control" / "harness" / f"{RUN_ID}-EX-02.json"
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    failed_phase = next(phase for phase in payload["phases"] if phase["phase"] == "execute")

    assert payload["run_id"] == RUN_ID
    assert payload["status"] == "FAILED"
    assert "FileNotFoundError" in payload["failure"]
    assert failed_phase["command"][0] == "npm"
    assert failed_phase["returncode"] == 127
    assert "FileNotFoundError" in failed_phase["stderr"]
    assert failed_phase["started_at"].endswith("Z")
    assert any("s11_environment" in command and "reset" in command for command in calls)
    assert any("s11_finalize_run" in command and "FAILED" in command for command in calls)


def test_runtime_url_is_discovered_once_and_propagated_consistently(tmp_path):
    calls = []
    probed_urls = []

    def runner(command, *, cwd, env):
        calls.append((command, env.copy()))
        return SimpleNamespace(returncode=0, stdout=_successful_stdout(command), stderr="")

    plan = build_plan(
        scenario_id="EX-08",
        execution_nonce="test-success",
        expected_git_sha="a" * 40,
    )
    result = FullStackHarness(
        tmp_path,
        runner=runner,
        backend_probe=probed_urls.append,
    ).execute(plan, environment=_environment())

    assert result.run_id == RUN_ID
    assert result.status == "COMPLETED"
    assert result.backend_url == BACKEND_URL
    assert probed_urls == [BACKEND_URL]
    assert (tmp_path / "var" / "s11-runs" / "control" / "scopes").is_dir()
    assert all(phase.duration_ms >= 0 for phase in result.phases)

    prepare_command, prepare_environment = next(
        (command, env) for command, env in calls if "s11_prepare_run" in command
    )
    playwright_environment = next(env for command, env in calls if "playwright" in command)
    backend_url_argument = prepare_command[prepare_command.index("--backend-url") + 1]
    discovered_at_argument = prepare_command[prepare_command.index("--backend-discovered-at") + 1]
    propagated_urls = {
        result.backend_url,
        probed_urls[0],
        backend_url_argument,
        prepare_environment["S11_BACKEND_URL"],
        playwright_environment["S11_BACKEND_URL"],
    }
    assert propagated_urls == {BACKEND_URL}
    assert prepare_environment["S11_BACKEND_HOST_PORT"] == str(ASSIGNED_PORT)
    assert prepare_environment["S11_BACKEND_CONTAINER_PORT"] == "8000"
    assert prepare_environment["S11_BACKEND_PORT_ALLOCATION"] == "docker-compose-dynamic"
    assert discovered_at_argument == prepare_environment["S11_BACKEND_DISCOVERED_AT"]
    assert discovered_at_argument.endswith("Z")
    assert playwright_environment["S11_RUN_ID"] == RUN_ID


def test_port_discovery_failure_is_explicit_and_has_no_fallback(tmp_path):
    calls = []

    def runner(command, *, cwd, env):
        calls.append(command)
        if _is_port_discovery(command):
            return SimpleNamespace(returncode=1, stdout="", stderr="no published port")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    plan = build_plan(
        scenario_id="EX-02",
        execution_nonce="test-discovery-failure",
        expected_git_sha="a" * 40,
    )
    with pytest.raises(HarnessError, match="no pudo informar"):
        FullStackHarness(
            tmp_path,
            runner=runner,
            backend_probe=lambda backend_url: None,
        ).execute(plan, environment=_environment())
    assert not any("s11_prepare_run" in command for command in calls)
    assert not any("playwright" in command for command in calls)
    assert any(command[-3:] == ("stop", "backend", "worker") for command in calls)


@pytest.mark.parametrize(
    "value",
    ["", "0.0.0.0:49152", "localhost:49152", "127.0.0.1:0", "127.0.0.1:65536"],
)
def test_port_discovery_rejects_non_loopback_or_invalid_values(value):
    with pytest.raises(HarnessError):
        _backend_url_from_compose_port(value)


class _HealthResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.payload


def test_backend_probe_retries_degraded_health_until_dependencies_are_ready(monkeypatch):
    responses = iter(
        [
            _HealthResponse(b'{"status":"degraded","db":false,"redis":true}'),
            _HealthResponse(b'{"status":"ok","db":true,"redis":true}'),
        ]
    )
    calls = []

    def urlopen(endpoint, timeout):
        calls.append((endpoint, timeout))
        return next(responses)

    monkeypatch.setattr("apps.experiments_app.harness.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("apps.experiments_app.harness.time.sleep", lambda seconds: None)

    _wait_for_backend(BACKEND_URL, timeout_seconds=1)

    assert calls == [
        (f"{BACKEND_URL}/api/v1/health/", 2),
        (f"{BACKEND_URL}/api/v1/health/", 2),
    ]


def test_backend_probe_times_out_when_health_remains_degraded(monkeypatch):
    monotonic_values = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(
        "apps.experiments_app.harness.urllib.request.urlopen",
        lambda endpoint, timeout: _HealthResponse(
            b'{"status":"degraded","db":false,"redis":false}'
        ),
    )
    monkeypatch.setattr(
        "apps.experiments_app.harness.time.monotonic", lambda: next(monotonic_values)
    )
    monkeypatch.setattr("apps.experiments_app.harness.time.sleep", lambda seconds: None)

    with pytest.raises(HarnessError, match="Timeout"):
        _wait_for_backend(BACKEND_URL, timeout_seconds=1)


def test_backend_runtime_configuration_is_manifest_ready():
    discovered_at = "2026-09-21T12:00:00Z"
    assert backend_runtime_configuration(BACKEND_URL, discovered_at) == {
        "host": "127.0.0.1",
        "host_port": ASSIGNED_PORT,
        "container_port": 8000,
        "url": BACKEND_URL,
        "allocation": "docker-compose-dynamic",
        "discovered_at": discovered_at,
        "compose_services": ["backend", "db", "redis", "worker"],
    }


@pytest.mark.parametrize(
    ("backend_url", "discovered_at"),
    [
        ("http://127.0.0.1:0", "2026-09-21T12:00:00Z"),
        ("http://0.0.0.0:49152", "2026-09-21T12:00:00Z"),
        ("http://127.0.0.1:49152/path", "2026-09-21T12:00:00Z"),
        (BACKEND_URL, "2026-09-21T09:00:00-03:00"),
        (BACKEND_URL, "not-a-timestamp"),
    ],
)
def test_backend_runtime_configuration_fails_closed(backend_url, discovered_at):
    with pytest.raises(CommandError):
        backend_runtime_configuration(backend_url, discovered_at)


def test_s11_plan_does_not_hardcode_host_port_8000():
    plan = build_plan(
        scenario_id="EX-02",
        execution_nonce="test-no-fixed-host-port",
        expected_git_sha="a" * 40,
    )
    assert "http://127.0.0.1:8000" not in repr(plan)


def test_s11_compose_overlay_requests_dynamic_loopback_port():
    repo_root = Path(__file__).resolve().parents[4]
    source_root = repo_root if (repo_root / "infra").is_dir() else Path("/s11-source")
    overlay = (source_root / "infra/docker-compose.s11.yml").read_text(encoding="utf-8")
    assert 'published: "0"' in overlay
    assert "host_ip: 127.0.0.1" in overlay
    assert '"8000:8000"' not in overlay


def test_repository_gate_requires_expected_clean_commit(tmp_path, monkeypatch):
    answers = iter(["a" * 40, ""])
    monkeypatch.setattr("apps.experiments_app.harness._git", lambda repo_root, *args: next(answers))
    verify_repository(tmp_path, "a" * 40)

    dirty_answers = iter(["a" * 40, " M tracked.py"])
    monkeypatch.setattr(
        "apps.experiments_app.harness._git", lambda repo_root, *args: next(dirty_answers)
    )
    with pytest.raises(HarnessError, match="working tree"):
        verify_repository(tmp_path, "a" * 40)


def test_frontend_preflight_verifies_node_npm_and_local_playwright(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    playwright_cli = frontend / "node_modules" / ".bin" / "playwright"
    playwright_cli.parent.mkdir(parents=True)
    playwright_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    (frontend / "package-lock.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("apps.experiments_app.harness.shutil.which", lambda command: command)

    calls = []

    def run(command, **kwargs):
        calls.append(command)
        stdout = {
            "node": "v22.23.2\n",
            "npm": "10.9.8\n",
        }[command[0]]
        if "playwright" in command:
            stdout = "Version 1.62.1\n"
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("apps.experiments_app.harness.subprocess.run", run)

    verify_frontend_tooling(tmp_path)

    assert calls == [
        ("node", "--version"),
        ("npm", "--version"),
        ("npm", "exec", "playwright", "--", "--version"),
    ]


def test_frontend_preflight_rejects_missing_npm_before_start(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    playwright_cli = frontend / "node_modules" / ".bin" / "playwright"
    playwright_cli.parent.mkdir(parents=True)
    playwright_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    (frontend / "package-lock.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        "apps.experiments_app.harness.shutil.which",
        lambda command: command if command == "node" else None,
    )

    with pytest.raises(HarnessError, match="node y npm"):
        verify_frontend_tooling(tmp_path)
