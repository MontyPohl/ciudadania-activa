import json
import os
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.management.base import CommandError

from apps.experiments_app.harness import (
    FullStackHarness,
    HarnessError,
    _backend_url_from_compose_port,
    _wait_for_backend,
    assert_execute_command_contract,
    build_plan,
    expected_execute_command,
    verify_frontend_tooling,
    verify_playwright_output_dir,
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
    assert payload["schema_version"] == "s11-harness-log-v2"
    assert payload["recorded_at"].endswith("Z")
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
    (frontend / "playwright.s11.config.ts").write_text("export default {};\n", encoding="utf-8")
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
    monkeypatch.setattr(
        "apps.experiments_app.harness.shutil.which",
        lambda command: command if command == "node" else None,
    )

    with pytest.raises(HarnessError, match="node y npm"):
        verify_frontend_tooling(tmp_path)


def test_real_harness_preflight_blocks_before_creating_run(tmp_path, monkeypatch):
    plan = build_plan(
        scenario_id="EX-02",
        execution_nonce="test-host-preflight",
        expected_git_sha="a" * 40,
    )
    monkeypatch.setattr("apps.experiments_app.harness.verify_repository", lambda *args: None)
    monkeypatch.setattr(
        "apps.experiments_app.harness.verify_frontend_tooling",
        lambda *args: (_ for _ in ()).throw(HarnessError("npm ausente")),
    )

    with pytest.raises(HarnessError, match="npm ausente"):
        FullStackHarness(tmp_path).execute(plan, environment=_environment())

    assert not (tmp_path / "var" / "s11-runs").exists()


# --------------------------------------------------------------------------- #
# Regresión A2: el separador npm y la llegada real de los argumentos a Playwright
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parents[4]
FRONTEND_ROOT = REPO_ROOT / "frontend"
PLAYWRIGHT_VERSION = re.compile(r"^Version\s+\d+\.\d+\.\d+")


def _forwarded_arguments(command):
    """Lo que realmente recibe Playwright: todo lo posterior al separador npm."""
    return tuple(command[command.index("--") + 1 :])


def test_execute_command_delivers_scenario_arguments_to_playwright_not_npm():
    """A2: `npm exec playwright test …` deja que npm consuma `--config`/`--grep`."""
    for number in range(2, 9):
        scenario = f"EX-{number:02d}"
        plan = build_plan(
            scenario_id=scenario,
            execution_nonce="test-npm-separator",
            expected_git_sha="a" * 40,
        )

        # Estructura semántica, no una cadena accidental.
        assert plan.execute[:3] == ("npm", "exec", "playwright")
        assert "--" in plan.execute, "falta el separador npm"
        separator_index = plan.execute.index("--")
        assert separator_index == 3, "el separador debe seguir inmediatamente al paquete"

        # Todo lo que npm podría interceptar queda antes del separador, y ahí
        # sólo hay tokens posicionales: nada destinado a Playwright.
        forwarded = _forwarded_arguments(plan.execute)
        assert forwarded[0] == "test"
        assert "--config=playwright.s11.config.ts" in forwarded
        assert f"--grep=@{scenario}" in forwarded


def test_execute_command_contract_rejects_the_historical_a2_defect():
    defective = (
        "npm",
        "exec",
        "playwright",
        "test",
        "--config=playwright.s11.config.ts",
        "--grep=@EX-02",
    )
    with pytest.raises(HarnessError, match="npm exec playwright -- test"):
        assert_execute_command_contract(defective, "EX-02")


def test_execute_command_contract_rejects_default_config_and_missing_grep():
    with pytest.raises(HarnessError, match="playwright.s11.config.ts"):
        assert_execute_command_contract(
            ("npm", "exec", "playwright", "--", "test", "--grep=@EX-02"), "EX-02"
        )
    with pytest.raises(HarnessError, match="grep=@EX-02"):
        assert_execute_command_contract(
            ("npm", "exec", "playwright", "--", "test", "--config=playwright.s11.config.ts"),
            "EX-02",
        )


def test_execute_command_contract_rejects_flags_before_the_separator():
    """Un flag del lado de npm sería interceptado por npm, nunca por Playwright."""
    with pytest.raises(HarnessError, match="npm exec playwright -- test"):
        assert_execute_command_contract(
            (
                "npm",
                "exec",
                "--config=playwright.s11.config.ts",
                "playwright",
                "--",
                "test",
                "--grep=@EX-02",
            ),
            "EX-02",
        )


def test_harness_precheck_aborts_a_plan_that_would_run_another_suite(tmp_path):
    """Un plan defectuoso se detiene antes de crear run, manifest o evidencia."""
    plan = build_plan(
        scenario_id="EX-02",
        execution_nonce="test-precheck-contract",
        expected_git_sha="a" * 40,
    )
    defective = replace(
        plan,
        execute=(
            "npm",
            "exec",
            "playwright",
            "test",
            "--config=playwright.s11.config.ts",
            "--grep=@EX-02",
        ),
    )
    calls = []

    def runner(command, *, cwd, env):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=_successful_stdout(command), stderr="")

    with pytest.raises(HarnessError, match="npm exec playwright -- test"):
        FullStackHarness(
            tmp_path,
            runner=runner,
            backend_probe=lambda backend_url: None,
        ).execute(defective, environment=_environment())

    assert calls == [], "el precheck no debe ejecutar ninguna fase"
    assert not (tmp_path / "var" / "s11-runs").exists()


@pytest.mark.skipif(
    shutil.which("npm") is None
    or not (FRONTEND_ROOT / "node_modules" / ".bin" / "playwright").is_file(),
    reason="Requiere npm y la CLI local de Playwright (npm ci).",
)
def test_npm_only_forwards_arguments_placed_after_the_separator():
    """Verificación a nivel de proceso; no ejecuta el suite ni toca el backend."""

    def npm_exec(*arguments):
        return subprocess.run(
            ("npm", "exec", "playwright", *arguments),
            cwd=FRONTEND_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        ).stdout.strip()

    with_separator = npm_exec("--", "--version")
    without_separator = npm_exec("--version")

    assert PLAYWRIGHT_VERSION.match(
        with_separator
    ), f"con separador debe responder Playwright, respondió {with_separator!r}"
    # Sin esto la aserción negativa pasaría de forma vacua si npm no imprimiera nada.
    assert without_separator, "npm no respondió nada; la aserción negativa sería vacua"
    assert not PLAYWRIGHT_VERSION.match(without_separator), (
        "sin separador npm intercepta el argumento y responde su propia versión; "
        f"respondió {without_separator!r}"
    )


# --------------------------------------------------------------------------- #
# Regresión A3: escritura del output directory de Playwright
# --------------------------------------------------------------------------- #


def test_output_directory_preflight_creates_a_missing_directory(tmp_path):
    (tmp_path / "frontend").mkdir()

    output_dir = verify_playwright_output_dir(tmp_path)

    assert output_dir == tmp_path / "frontend" / "test-results"
    assert output_dir.is_dir()
    assert list(output_dir.iterdir()) == [], "la sonda de escritura debe retirarse"


def test_output_directory_preflight_accepts_a_writable_directory_with_last_run(tmp_path):
    output_dir = tmp_path / "frontend" / "test-results"
    output_dir.mkdir(parents=True)
    (output_dir / ".last-run.json").write_text("{}\n", encoding="utf-8")

    assert verify_playwright_output_dir(tmp_path) == output_dir


def test_output_directory_preflight_blocks_a_foreign_owner(tmp_path, monkeypatch):
    """A1: un directorio heredado de otro UID bloquea con motivo explícito."""
    (tmp_path / "frontend" / "test-results").mkdir(parents=True)
    # `harness.os` es el módulo `os` real: hay que fijar el UID antes de doblarlo.
    running_uid = os.getuid()
    monkeypatch.setattr("apps.experiments_app.harness.os.getuid", lambda: running_uid + 1)

    with pytest.raises(HarnessError, match="pertenece al UID"):
        verify_playwright_output_dir(tmp_path)


def test_output_directory_preflight_blocks_a_foreign_owned_last_run(tmp_path):
    """Reproduce A1: el `.last-run.json` heredado de root bloqueaba `playwright test`."""
    if os.geteuid() != 0:
        pytest.skip("Reasignar el dueño de un artefacto requiere privilegios.")
    output_dir = tmp_path / "frontend" / "test-results"
    output_dir.mkdir(parents=True)
    last_run = output_dir / ".last-run.json"
    last_run.write_text("{}\n", encoding="utf-8")
    os.chown(last_run, os.geteuid() + 4242, -1)

    with pytest.raises(HarnessError, match=r"entradas de otro UID.*\.last-run\.json"):
        verify_playwright_output_dir(tmp_path)


def test_output_directory_preflight_rejects_a_non_directory(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "test-results").write_text("no soy un directorio\n", encoding="utf-8")

    with pytest.raises(HarnessError, match="no es un directorio"):
        verify_playwright_output_dir(tmp_path)


def test_frontend_preflight_requires_the_s11_playwright_config(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    playwright_cli = frontend / "node_modules" / ".bin" / "playwright"
    playwright_cli.parent.mkdir(parents=True)
    playwright_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    (frontend / "package-lock.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("apps.experiments_app.harness.shutil.which", lambda command: command)
    monkeypatch.setattr(
        "apps.experiments_app.harness.subprocess.run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout={"node": "v22.23.2\n", "npm": "10.9.8\n"}.get(command[0], "Version 1.62.1\n"),
            stderr="",
        ),
    )

    with pytest.raises(HarnessError, match="playwright.s11.config.ts"):
        verify_frontend_tooling(tmp_path)


def test_frontend_preflight_detects_npm_intercepting_the_version_argument(tmp_path, monkeypatch):
    """Si npm respondiera su propia versión, los argumentos no llegan a Playwright."""
    frontend = tmp_path / "frontend"
    playwright_cli = frontend / "node_modules" / ".bin" / "playwright"
    playwright_cli.parent.mkdir(parents=True)
    playwright_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    (frontend / "package-lock.json").write_text("{}\n", encoding="utf-8")
    (frontend / "playwright.s11.config.ts").write_text("export default {};\n", encoding="utf-8")
    monkeypatch.setattr("apps.experiments_app.harness.shutil.which", lambda command: command)
    monkeypatch.setattr(
        "apps.experiments_app.harness.subprocess.run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            # npm devuelve «10.9.8» en lugar de «Version 1.62.1».
            stdout={"node": "v22.23.2\n"}.get(command[0], "10.9.8\n"),
            stderr="",
        ),
    )

    with pytest.raises(HarnessError, match="npm podría"):
        verify_frontend_tooling(tmp_path)


# --------------------------------------------------------------------------- #
# El contrato es exacto: un argumento extra puede anular config o filtro
# --------------------------------------------------------------------------- #


def test_execute_command_contract_rejects_a_second_config_that_would_win():
    """Playwright conserva el último `--config`: la pertenencia no basta."""
    with pytest.raises(HarnessError, match="no coincide exactamente"):
        assert_execute_command_contract(
            (
                *expected_execute_command("EX-02"),
                "--config=playwright.config.ts",
            ),
            "EX-02",
        )


@pytest.mark.parametrize(
    "extra",
    ["--grep-invert=@EX-02", "--grep=@EX", "e2e", "--project=chromium"],
)
def test_execute_command_contract_rejects_any_additional_argument(extra):
    with pytest.raises(HarnessError, match="no coincide exactamente"):
        assert_execute_command_contract((*expected_execute_command("EX-02"), extra), "EX-02")


def test_expected_execute_command_is_pinned_independently_of_the_harness():
    """`build_plan` valida contra esta misma función: el literal se fija aquí.

    Sin esta prueba, quitar el separador de `expected_execute_command` dejaría
    el auto-chequeo de `build_plan` tautológico y la regresión A2 pasaría verde.
    """
    assert expected_execute_command("EX-02") == (
        "npm",
        "exec",
        "playwright",
        "--",
        "test",
        "--config=playwright.s11.config.ts",
        "--grep=@EX-02",
    )


def test_build_plan_uses_exactly_the_contracted_command():
    for number in range(2, 9):
        scenario = f"EX-{number:02d}"
        plan = build_plan(
            scenario_id=scenario,
            execution_nonce="test-exact-command",
            expected_git_sha="a" * 40,
        )
        assert plan.execute == expected_execute_command(scenario)


def test_execute_phase_runs_from_the_frontend_directory(tmp_path):
    """El `--config` es relativo: sólo resuelve con cwd=frontend."""
    seen = {}

    def runner(command, *, cwd, env):
        if "playwright" in command:
            seen["cwd"] = cwd
        return SimpleNamespace(returncode=0, stdout=_successful_stdout(command), stderr="")

    plan = build_plan(
        scenario_id="EX-02",
        execution_nonce="test-execute-cwd",
        expected_git_sha="a" * 40,
    )
    FullStackHarness(
        tmp_path,
        runner=runner,
        backend_probe=lambda backend_url: None,
    ).execute(plan, environment=_environment())

    assert seen["cwd"] == tmp_path.resolve() / "frontend"


# --------------------------------------------------------------------------- #
# Los literales del harness deben corresponder a artefactos reales
# --------------------------------------------------------------------------- #


def _frontend_source_root():
    """El repo montado, o `/s11-source` cuando la suite corre en el contenedor."""
    if (REPO_ROOT / "frontend").is_dir():
        return REPO_ROOT / "frontend"
    return Path("/s11-source/frontend")


def test_contracted_config_names_the_real_s11_configuration():
    config = _frontend_source_root() / "playwright.s11.config.ts"
    assert config.is_file(), "el harness apunta a una configuración inexistente"
    source = config.read_text(encoding="utf-8")

    # La suite S11 debe seguir siendo la experimental, no la ordinaria.
    assert 'testDir: "./e2e-s11"' in source
    # `_S11_PLAYWRIGHT_OUTPUT_DIR` supone el valor por defecto de Playwright:
    # si la configuración declarase `outputDir`, el preflight validaría otra ruta.
    assert "outputDir" not in source
    # Las trazas siguen apagadas por política S11 (pueden contener JWT/cookies).
    for disabled in ('trace: "off"', 'screenshot: "off"', 'video: "off"'):
        assert disabled in source


def test_contracted_grep_matches_the_real_scenario_tags():
    spec = _frontend_source_root() / "e2e-s11" / "s11-fullstack.spec.ts"
    assert spec.is_file()
    source = spec.read_text(encoding="utf-8")

    for number in range(2, 9):
        scenario = f"EX-{number:02d}"
        grep = expected_execute_command(scenario)[-1]
        tag = grep.removeprefix("--grep=")
        assert f'test("{tag} ' in source, f"ningún test lleva la etiqueta {tag}"


# --------------------------------------------------------------------------- #
# El preflight debe seguir cableado al chequeo de output directory
# --------------------------------------------------------------------------- #


def _stub_frontend(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    playwright_cli = frontend / "node_modules" / ".bin" / "playwright"
    playwright_cli.parent.mkdir(parents=True)
    playwright_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    (frontend / "package-lock.json").write_text("{}\n", encoding="utf-8")
    (frontend / "playwright.s11.config.ts").write_text("export default {};\n", encoding="utf-8")
    monkeypatch.setattr("apps.experiments_app.harness.shutil.which", lambda command: command)

    def run(command, **kwargs):
        if "playwright" in command:
            stdout = "Version 1.62.1\n"
        else:
            stdout = {"node": "v22.23.2\n", "npm": "10.9.8\n"}[command[0]]
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("apps.experiments_app.harness.subprocess.run", run)
    return frontend


def test_frontend_preflight_is_wired_to_the_output_directory_check(tmp_path, monkeypatch):
    """Si se desconectara `verify_playwright_output_dir`, esto dejaría de fallar."""
    frontend = _stub_frontend(tmp_path, monkeypatch)
    (frontend / "test-results").write_text("no soy un directorio\n", encoding="utf-8")

    with pytest.raises(HarnessError, match="no es un directorio"):
        verify_frontend_tooling(tmp_path)


def test_frontend_preflight_creates_the_output_directory_when_absent(tmp_path, monkeypatch):
    frontend = _stub_frontend(tmp_path, monkeypatch)

    verify_frontend_tooling(tmp_path)

    assert (frontend / "test-results").is_dir()


def test_output_directory_preflight_blocks_a_foreign_owned_nested_entry(tmp_path):
    """Playwright borra el árbol completo: una entrada anidada ajena aborta el run."""
    if os.geteuid() != 0:
        pytest.skip("Reasignar el dueño de una entrada anidada requiere privilegios.")
    nested = tmp_path / "frontend" / "test-results" / ".playwright-artifacts-0"
    nested.mkdir(parents=True)
    (nested / "trace.zip").write_text("x\n", encoding="utf-8")
    os.chown(nested, os.geteuid() + 4242, -1)

    with pytest.raises(HarnessError, match="entradas de otro UID"):
        verify_playwright_output_dir(tmp_path)


def test_output_directory_preflight_rejects_a_dangling_symlink(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "test-results").symlink_to(tmp_path / "inexistente")

    with pytest.raises(HarnessError, match="enlace simbólico roto"):
        verify_playwright_output_dir(tmp_path)
