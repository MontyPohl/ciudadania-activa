from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from time import perf_counter
from typing import Protocol

from .canonical import canonical_json_bytes
from .constants import FULLSTACK_SCENARIOS
from .redaction import redact

_RUN_ID = re.compile(r"^s11-[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class HarnessError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ScenarioPlan:
    scenario_id: str
    expected_git_sha: str
    setup: tuple[tuple[str, ...], ...]
    execute: tuple[str, ...]
    collect: tuple[tuple[str, ...], ...]
    validate: tuple[tuple[str, ...], ...]
    cleanup: tuple[tuple[str, ...], ...]
    record: tuple[tuple[str, ...], ...]
    custody: tuple[tuple[str, ...], ...]
    finalize_completed: tuple[tuple[str, ...], ...]
    finalize_failed: tuple[tuple[str, ...], ...]
    shutdown: tuple[tuple[str, ...], ...]
    harness_log_host_path: str
    status: str = "READY"


@dataclass(frozen=True, slots=True)
class PhaseResult:
    phase: str
    command: tuple[str, ...]
    returncode: int
    duration_ms: float
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class HarnessResult:
    run_id: str
    scenario_id: str
    status: str
    phases: tuple[PhaseResult, ...]


class CommandRunner(Protocol):
    def __call__(self, command: tuple[str, ...], *, cwd: Path, env: dict[str, str]): ...


def build_plan(
    *,
    scenario_id: str,
    execution_nonce: str,
    expected_git_sha: str,
    dataset_path: Path = Path("/app/apps/experiments_app/data/s11-functional-v1.json"),
) -> ScenarioPlan:
    if scenario_id not in FULLSTACK_SCENARIOS:
        raise HarnessError("El harness full-stack S11-B admite EX-02…EX-08.")
    if not execution_nonce.strip():
        raise HarnessError("execution_nonce explícito es obligatorio.")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_git_sha):
        raise HarnessError("expected_git_sha debe ser un SHA-1 completo.")
    compose = (
        "docker",
        "compose",
        "-p",
        "ciudadania-s11",
        "-f",
        "infra/docker-compose.yml",
        "-f",
        "infra/docker-compose.s11.yml",
    )
    backend_exec = (*compose, "exec", "-T", "backend", "python", "manage.py")
    scope_container = "/s11-evidence/control/scopes/{run_id}-" + scenario_id + ".json"
    export_container = "/s11-evidence/runs/{run_id}/raw/" + scenario_id + "/source-export.json"
    harness_log_container = "/s11-evidence/control/harness/{run_id}-" + scenario_id + ".json"
    harness_log_host = "var/s11-runs/control/harness/{run_id}-" + scenario_id + ".json"
    finalize = (*backend_exec, "s11_finalize_run", "--run-id", "{run_id}", "--status")
    return ScenarioPlan(
        scenario_id=scenario_id,
        expected_git_sha=expected_git_sha,
        setup=(
            (*compose, "up", "-d", "db", "redis", "backend", "worker"),
            (*backend_exec, "s11_environment", "create"),
            (*backend_exec, "s11_environment", "reset"),
            (
                *backend_exec,
                "s11_prepare_run",
                "--dataset",
                str(dataset_path),
                "--scenario",
                scenario_id,
                "--experiment-id",
                scenario_id,
                "--nonce",
                execution_nonce,
                "--git-sha",
                expected_git_sha,
                "--git-clean",
            ),
        ),
        execute=(
            "npm",
            "exec",
            "playwright",
            "test",
            "--config=playwright.s11.config.ts",
            f"--grep=@{scenario_id}",
        ),
        collect=((*backend_exec, "s11_export_evidence", "--scope", scope_container),),
        validate=((*backend_exec, "s11_check_traceability", export_container, "--complete"),),
        cleanup=((*backend_exec, "s11_environment", "reset"),),
        record=((*backend_exec, "s11_record_harness_log", "--input", harness_log_container),),
        custody=(
            (
                *backend_exec,
                "s11_validate_run",
                "--run-id",
                "{run_id}",
                "--scenario",
                scenario_id,
            ),
        ),
        finalize_completed=((*finalize, "COMPLETED"),),
        finalize_failed=((*finalize, "FAILED"),),
        shutdown=((*compose, "stop", "backend", "worker"),),
        harness_log_host_path=harness_log_host,
    )


class FullStackHarness:
    """Orquestador setup→execute→collect→validate→cleanup sin ocultar fallos."""

    def __init__(self, repo_root: Path, runner: CommandRunner | None = None) -> None:
        self.repo_root = repo_root.resolve()
        self.runner = runner or _run

    def execute(self, plan: ScenarioPlan, *, environment: dict[str, str]) -> HarnessResult:
        required = {
            "S11_EXPERIMENTS_ENABLED": "true",
            "S11_ENVIRONMENT": "isolated-experiment",
            "S11_CUSTODY_APPROVED": "true",
        }
        for key, expected in required.items():
            if environment.get(key, "").lower() != expected:
                raise HarnessError(f"Falta el gate explícito {key}={expected}.")
        if not environment.get("S11_DOCUMENT_HMAC_SECRET", "").strip():
            raise HarnessError("Falta el secreto HMAC efímero S11; no se usa un valor versionado.")
        if self.runner is _run:
            verify_repository(self.repo_root, plan.expected_git_sha)
        self._prepare_control_directories()

        runtime_environment = {**environment, "S11_SCENARIO_ID": plan.scenario_id}
        results: list[PhaseResult] = []
        failure: HarnessError | None = None
        try:
            self._phases(results, "setup", plan.setup, runtime_environment)
            self._phases(
                results,
                "execute",
                (plan.execute,),
                runtime_environment,
                cwd="frontend",
            )
            self._phases(results, "collect", plan.collect, runtime_environment)
            self._phases(results, "validate", plan.validate, runtime_environment)
        except HarnessError as exc:
            failure = exc
        finally:
            if runtime_environment.get("S11_RUN_ID"):
                if not any(
                    result.phase == "collect" and result.returncode == 0 for result in results
                ):
                    try:
                        self._phases(
                            results,
                            "collect_failure",
                            plan.collect,
                            runtime_environment,
                        )
                    except HarnessError:
                        # El fallo primario conserva prioridad; el intento y su código
                        # quedan en el log del harness para diagnóstico.
                        pass
                try:
                    self._phases(results, "cleanup", plan.cleanup, runtime_environment)
                except HarnessError as exc:
                    failure = failure or exc
                try:
                    self._persist_harness_log(plan, results, runtime_environment, failure)
                    self._phases(results, "record", plan.record, runtime_environment)
                except HarnessError as exc:
                    failure = failure or exc
                if failure is None:
                    try:
                        self._phases(results, "custody", plan.custody, runtime_environment)
                    except HarnessError as exc:
                        failure = exc
                final_commands = (
                    plan.finalize_failed if failure is not None else plan.finalize_completed
                )
                try:
                    self._phases(results, "finalize", final_commands, runtime_environment)
                except HarnessError as exc:
                    failure = failure or exc
            try:
                self._phases(results, "shutdown", plan.shutdown, runtime_environment)
            except HarnessError as exc:
                failure = failure or exc

        if failure is not None:
            raise HarnessError(f"Harness falló y lo registró sin ocultarlo: {failure}") from failure
        run_id = runtime_environment.get("S11_RUN_ID", "")
        if not _valid_run_id(run_id):
            raise HarnessError("El harness terminó sin producir un run_id válido.")
        return HarnessResult(
            run_id=run_id,
            scenario_id=plan.scenario_id,
            status="COMPLETED",
            phases=tuple(
                replace(
                    result,
                    stdout=redact(result.stdout),
                    stderr=redact(result.stderr),
                )
                for result in results
            ),
        )

    def _phases(
        self,
        results: list[PhaseResult],
        phase: str,
        commands: tuple[tuple[str, ...], ...],
        environment: dict[str, str],
        cwd: str | None = None,
    ) -> None:
        for command_template in commands:
            command = self._render(command_template, environment)
            started = perf_counter()
            completed = self.runner(
                command,
                cwd=self.repo_root / cwd if cwd else self.repo_root,
                env=self._render_environment(environment),
            )
            result = PhaseResult(
                phase=phase,
                command=command,
                returncode=completed.returncode,
                duration_ms=round((perf_counter() - started) * 1000, 3),
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
            results.append(result)
            if completed.returncode != 0:
                raise HarnessError(f"{phase} terminó con código {completed.returncode}.")
            if "s11_prepare_run" in command:
                run_id = self._parse_run_id(completed.stdout)
                environment["S11_RUN_ID"] = run_id
                environment["S11_SCOPE_HOST_PATH"] = (
                    f"../var/s11-runs/control/scopes/{run_id}-{environment['S11_SCENARIO_ID']}.json"
                )

    def _persist_harness_log(
        self,
        plan: ScenarioPlan,
        results: list[PhaseResult],
        environment: dict[str, str],
        failure: HarnessError | None,
    ) -> None:
        relative = self._render((plan.harness_log_host_path,), environment)[0]
        target = (self.repo_root / relative).resolve()
        custody_root = (self.repo_root / "var" / "s11-runs").resolve()
        if custody_root not in target.parents:
            raise HarnessError("El log del harness quedó fuera de la custodia S11.")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = redact(
            {
                "schema_version": "s11-harness-log-v1",
                "run_id": environment["S11_RUN_ID"],
                "scenario_id": plan.scenario_id,
                "status": "FAILED" if failure else "COMPLETED",
                "failure": str(failure) if failure else None,
                "phases": [asdict(result) for result in results],
            }
        )
        try:
            with target.open("xb") as destination:
                destination.write(canonical_json_bytes(payload))
        except OSError as exc:
            raise HarnessError("No se pudo preparar el log redactado del harness.") from exc

    def _prepare_control_directories(self) -> None:
        custody_root = (self.repo_root / "var" / "s11-runs").resolve()
        if self.runner is _run and (
            custody_root == Path("/tmp") or Path("/tmp") in custody_root.parents
        ):
            raise HarnessError("La custodia del harness no puede residir en /tmp.")
        for relative in ("control/harness", "control/scopes"):
            (custody_root / relative).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _parse_run_id(stdout: str) -> str:
        try:
            payload = json.loads(stdout.strip().splitlines()[-1])
            run_id = payload["run_id"]
        except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise HarnessError("s11_prepare_run no devolvió un run_id auditable.") from exc
        if not _valid_run_id(run_id):
            raise HarnessError("s11_prepare_run devolvió un run_id inválido.")
        return run_id

    @staticmethod
    def _render(command: tuple[str, ...], environment: dict[str, str]) -> tuple[str, ...]:
        run_id = environment.get("S11_RUN_ID", "{run_id}")
        return tuple(part.format(run_id=run_id) for part in command)

    @staticmethod
    def _render_environment(environment: dict[str, str]) -> dict[str, str]:
        run_id = environment.get("S11_RUN_ID", "")
        return {key: value.format(run_id=run_id) for key, value in environment.items()}


def _run(command: tuple[str, ...], *, cwd: Path, env: dict[str, str]):
    return subprocess.run(
        command,
        cwd=cwd,
        env={**os.environ, **env},
        check=False,
        capture_output=True,
        text=True,
    )


def _valid_run_id(value: str) -> bool:
    return _RUN_ID.fullmatch(value) is not None


def verify_repository(repo_root: Path, expected_git_sha: str) -> None:
    head = _git(repo_root, "rev-parse", "HEAD")
    if head != expected_git_sha:
        raise HarnessError(f"HEAD {head} no coincide con {expected_git_sha}.")
    if _git(repo_root, "status", "--porcelain"):
        raise HarnessError("El harness exige un working tree limpio.")


def _git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise HarnessError("No se pudo verificar Git desde el host.")
    return completed.stdout.strip()
