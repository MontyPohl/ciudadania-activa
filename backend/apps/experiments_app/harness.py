from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Protocol

from .canonical import canonical_json_bytes
from .constants import FULLSTACK_SCENARIOS
from .redaction import redact

_RUN_ID = re.compile(r"^s11-[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_COMPOSE_PORT = re.compile(r"^127\.0\.0\.1:(?P<port>[0-9]{1,5})$")
_BACKEND_CONTAINER_PORT = 8000
_BACKEND_ALLOCATION = "docker-compose-dynamic"
_MINIMUM_NODE_VERSION = (20, 9, 0)
#: `npm exec <pkg> <args>` interpreta como propios los `--flags` que siguen al
#: paquete. Sólo lo posterior a este separador llega al binario invocado.
_NPM_SEPARATOR = "--"
_S11_PLAYWRIGHT_CONFIG = "playwright.s11.config.ts"
#: `playwright.s11.config.ts` no declara `outputDir`, por lo que Playwright usa
#: su valor por defecto relativo al directorio de la configuración.
_S11_PLAYWRIGHT_OUTPUT_DIR = "test-results"
_S11_WRITE_PROBE = ".s11-preflight-write-probe"
#: `playwright --version` responde «Version X.Y.Z»; `npm --version` responde
#: «X.Y.Z». Distinguirlos detecta que npm interceptó el argumento.
_PLAYWRIGHT_VERSION = re.compile(r"^Version\s+\d+\.\d+\.\d+")


class HarnessError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ScenarioPlan:
    scenario_id: str
    expected_git_sha: str
    start: tuple[tuple[str, ...], ...]
    discover_backend: tuple[str, ...]
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
    started_at: str
    returncode: int
    duration_ms: float
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class HarnessResult:
    run_id: str
    scenario_id: str
    backend_url: str
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
    execute_command = expected_execute_command(scenario_id)
    assert_execute_command_contract(execute_command, scenario_id)
    return ScenarioPlan(
        scenario_id=scenario_id,
        expected_git_sha=expected_git_sha,
        start=((*compose, "up", "-d", "db", "redis", "backend", "worker"),),
        discover_backend=(*compose, "port", "backend", str(_BACKEND_CONTAINER_PORT)),
        setup=(
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
                "--backend-url",
                "{backend_url}",
                "--backend-discovered-at",
                "{backend_discovered_at}",
            ),
        ),
        execute=execute_command,
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

    def __init__(
        self,
        repo_root: Path,
        runner: CommandRunner | None = None,
        backend_probe: Callable[[str], None] | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.runner = runner or _run
        self.backend_probe = backend_probe or _wait_for_backend

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
        # Precheck: un plan que no entregue la configuración S11 a Playwright
        # ejecutaría otra suite, así que aborta antes de crear run ni evidencia.
        assert_execute_command_contract(plan.execute, plan.scenario_id)
        if self.runner is _run:
            verify_repository(self.repo_root, plan.expected_git_sha)
            verify_frontend_tooling(self.repo_root)
        self._prepare_control_directories()

        runtime_environment = {**environment, "S11_SCENARIO_ID": plan.scenario_id}
        results: list[PhaseResult] = []
        failure: HarnessError | None = None
        unexpected_failure: Exception | None = None
        try:
            self._phases(results, "start", plan.start, runtime_environment)
            self._discover_backend(results, plan, runtime_environment)
            self._probe_backend(results, runtime_environment)
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
        except Exception as exc:
            unexpected_failure = exc
            failure = HarnessError(
                f"Excepción de infraestructura no controlada: {type(exc).__name__}: {exc}"
            )
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

        if unexpected_failure is not None:
            raise unexpected_failure.with_traceback(unexpected_failure.__traceback__)
        if failure is not None:
            raise HarnessError(f"Harness falló y lo registró sin ocultarlo: {failure}") from failure
        run_id = runtime_environment.get("S11_RUN_ID", "")
        if not _valid_run_id(run_id):
            raise HarnessError("El harness terminó sin producir un run_id válido.")
        return HarnessResult(
            run_id=run_id,
            scenario_id=plan.scenario_id,
            backend_url=runtime_environment["S11_BACKEND_URL"],
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
            started_at = _utc_timestamp()
            started = perf_counter()
            try:
                completed = self.runner(
                    command,
                    cwd=self.repo_root / cwd if cwd else self.repo_root,
                    env=self._render_environment(environment),
                )
            except OSError as exc:
                results.append(
                    PhaseResult(
                        phase=phase,
                        command=command,
                        started_at=started_at,
                        returncode=127,
                        duration_ms=round((perf_counter() - started) * 1000, 3),
                        stdout="",
                        stderr=f"{type(exc).__name__}: {exc}",
                    )
                )
                raise HarnessError(
                    f"{phase} no pudo iniciar {command[0]}: {type(exc).__name__}: {exc}"
                ) from exc
            result = PhaseResult(
                phase=phase,
                command=command,
                started_at=started_at,
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

    def _discover_backend(
        self,
        results: list[PhaseResult],
        plan: ScenarioPlan,
        environment: dict[str, str],
    ) -> None:
        started_at = _utc_timestamp()
        started = perf_counter()
        completed = self.runner(
            plan.discover_backend,
            cwd=self.repo_root,
            env=self._render_environment(environment),
        )
        results.append(
            PhaseResult(
                phase="discover_backend",
                command=plan.discover_backend,
                started_at=started_at,
                returncode=completed.returncode,
                duration_ms=round((perf_counter() - started) * 1000, 3),
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        )
        if completed.returncode != 0:
            raise HarnessError("Docker Compose no pudo informar el puerto publicado del backend.")
        backend_url, host_port = _backend_url_from_compose_port(completed.stdout)
        environment.update(
            {
                "S11_BACKEND_URL": backend_url,
                "S11_BACKEND_HOST": "127.0.0.1",
                "S11_BACKEND_HOST_PORT": str(host_port),
                "S11_BACKEND_CONTAINER_PORT": str(_BACKEND_CONTAINER_PORT),
                "S11_BACKEND_PORT_ALLOCATION": _BACKEND_ALLOCATION,
                "S11_BACKEND_DISCOVERED_AT": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        )

    def _probe_backend(
        self,
        results: list[PhaseResult],
        environment: dict[str, str],
    ) -> None:
        backend_url = environment["S11_BACKEND_URL"]
        started_at = _utc_timestamp()
        started = perf_counter()
        try:
            self.backend_probe(backend_url)
        except Exception as exc:
            results.append(
                PhaseResult(
                    phase="backend_ready",
                    command=("http-healthcheck", f"{backend_url}/api/v1/health/"),
                    started_at=started_at,
                    returncode=1,
                    duration_ms=round((perf_counter() - started) * 1000, 3),
                    stdout="",
                    stderr=str(exc),
                )
            )
            raise HarnessError(
                "El backend S11 no quedó disponible en el puerto descubierto."
            ) from exc
        results.append(
            PhaseResult(
                phase="backend_ready",
                command=("http-healthcheck", f"{backend_url}/api/v1/health/"),
                started_at=started_at,
                returncode=0,
                duration_ms=round((perf_counter() - started) * 1000, 3),
                stdout="ready",
                stderr="",
            )
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
                "schema_version": "s11-harness-log-v2",
                "run_id": environment["S11_RUN_ID"],
                "scenario_id": plan.scenario_id,
                "recorded_at": _utc_timestamp(),
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
        values = {
            "run_id": environment.get("S11_RUN_ID", "{run_id}"),
            "backend_url": environment.get("S11_BACKEND_URL", "{backend_url}"),
            "backend_discovered_at": environment.get(
                "S11_BACKEND_DISCOVERED_AT", "{backend_discovered_at}"
            ),
        }
        return tuple(part.format(**values) for part in command)

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


def _backend_url_from_compose_port(stdout: str) -> tuple[str, int]:
    value = stdout.strip()
    match = _COMPOSE_PORT.fullmatch(value)
    if match is None:
        raise HarnessError(
            "Docker Compose devolvió un puerto inválido; se exige una publicación IPv4 loopback."
        )
    port = int(match.group("port"))
    if not 1 <= port <= 65535:
        raise HarnessError("Docker Compose devolvió un puerto fuera del rango TCP válido.")
    return f"http://127.0.0.1:{port}", port


def _wait_for_backend(backend_url: str, *, timeout_seconds: float = 60.0) -> None:
    endpoint = f"{backend_url}/api/v1/health/"
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(endpoint, timeout=2) as response:  # noqa: S310
                try:
                    payload = json.loads(response.read())
                except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
                    last_error = RuntimeError(f"El health check no devolvió JSON válido: {exc}")
                    time.sleep(0.25)
                    continue
                if (
                    response.status == 200
                    and isinstance(payload, dict)
                    and payload.get("status") == "ok"
                    and payload.get("db") is True
                    and payload.get("redis") is True
                ):
                    return
                last_error = RuntimeError(
                    f"Health no disponible: HTTP {response.status}, payload={payload!r}"
                )
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise HarnessError(f"Timeout esperando {endpoint}: {last_error}")


def _valid_run_id(value: str) -> bool:
    return _RUN_ID.fullmatch(value) is not None


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def expected_execute_command(scenario_id: str) -> tuple[str, ...]:
    """Única forma admitida de invocar Playwright para un escenario S11."""
    return (
        "npm",
        "exec",
        "playwright",
        # Sin este separador npm consume `--config` y `--grep` como flags
        # propios y lanza `playwright test` con la configuración por defecto,
        # es decir la suite ordinaria `e2e/` en vez del escenario S11.
        _NPM_SEPARATOR,
        "test",
        f"--config={_S11_PLAYWRIGHT_CONFIG}",
        f"--grep=@{scenario_id}",
    )


def assert_execute_command_contract(command: tuple[str, ...], scenario_id: str) -> None:
    """Exige que Playwright —y no npm— reciba la configuración S11 y el grep.

    `npm exec playwright test --config=… --grep=…` deja que npm consuma ambos
    flags y ejecuta la suite ordinaria `e2e/`. La forma válida interpone el
    separador: `npm exec playwright -- test --config=… --grep=…`.

    La comparación es **exacta** a propósito. Bastaría con añadir un segundo
    `--config` para que Playwright —que conserva el último— volviera a la suite
    ordinaria pese a contener los tokens esperados, así que no se admite ningún
    argumento extra. Cambiar la forma de invocación exige actualizar
    `expected_execute_command`, que es el único lugar donde se define.
    """
    expected = expected_execute_command(scenario_id)
    actual = tuple(command)
    if actual == expected:
        return

    prefix_length = 5
    if actual[:prefix_length] != expected[:prefix_length]:
        raise HarnessError(
            "El comando Playwright debe construirse como "
            "'npm exec playwright -- test …'; sin el separador npm consume los "
            f"argumentos del escenario. Recibido: {' '.join(actual)}"
        )
    forwarded = actual[prefix_length - 1 :]
    if f"--config={_S11_PLAYWRIGHT_CONFIG}" not in forwarded:
        raise HarnessError(
            f"El comando Playwright no selecciona {_S11_PLAYWRIGHT_CONFIG} después "
            "del separador npm; se ejecutaría la configuración por defecto."
        )
    if f"--grep=@{scenario_id}" not in forwarded:
        raise HarnessError(
            f"El comando Playwright no reenvía --grep=@{scenario_id} después del "
            "separador npm; no quedaría restringido al escenario."
        )
    raise HarnessError(
        "El comando Playwright no coincide exactamente con el contrato S11; un "
        "argumento adicional puede anular la configuración o el filtro —Playwright "
        f"conserva el último `--config`—. Esperado: {' '.join(expected)} | "
        f"Recibido: {' '.join(actual)}"
    )


def verify_playwright_output_dir(repo_root: Path) -> Path:
    """Comprueba que Playwright podrá escribir sus artefactos del run S11.

    Un directorio heredado de otro UID —por ejemplo creado por un contenedor
    que corrió como root— hace fallar `playwright test` al reescribir
    `.last-run.json`. El preflight lo bloquea con un motivo explícito en vez de
    repararlo por su cuenta: cambiar dueño o permisos excede al proceso
    experimental.
    """
    output_dir = repo_root / "frontend" / _S11_PLAYWRIGHT_OUTPUT_DIR
    expected_uid = os.getuid()
    if output_dir.is_symlink() and not output_dir.exists():
        raise HarnessError(
            f"El directorio de resultados S11 {output_dir} es un enlace simbólico roto."
        )
    if output_dir.exists():
        if not output_dir.is_dir():
            raise HarnessError(
                f"El directorio de resultados S11 {output_dir} existe y no es un directorio."
            )
        owner_uid = output_dir.stat().st_uid
        if owner_uid != expected_uid:
            raise HarnessError(
                f"El directorio de resultados S11 {output_dir} pertenece al UID "
                f"{owner_uid} y Playwright correrá como UID {expected_uid}. "
                "Requiere intervención administrativa fuera del run; el preflight "
                "no altera dueños ni permisos del repositorio."
            )
        if not os.access(output_dir, os.W_OK | os.X_OK):
            raise HarnessError(
                f"El directorio de resultados S11 {output_dir} no es escribible "
                f"por el UID {expected_uid}."
            )
        # Playwright borra el directorio completo antes de empezar y propaga
        # cualquier error que no sea EBUSY, así que una entrada anidada de otro
        # dueño —incluido un `.last-run.json` heredado— aborta el run aunque la
        # raíz sea escribible.
        foreign = _foreign_owned_entries(output_dir, expected_uid)
        if foreign:
            listed = ", ".join(str(path.relative_to(output_dir)) for path in foreign[:5])
            raise HarnessError(
                f"El directorio de resultados S11 {output_dir} contiene entradas de "
                f"otro UID que Playwright no podrá retirar al limpiarlo: {listed}. "
                f"Playwright correrá como UID {expected_uid}. Requiere intervención "
                "administrativa fuera del run; el preflight no altera dueños ni "
                "permisos del repositorio."
            )
    else:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HarnessError(
                f"El directorio de resultados S11 {output_dir} no pudo crearse: "
                f"{type(exc).__name__}."
            ) from exc

    probe = output_dir / _S11_WRITE_PROBE
    try:
        probe.write_text("s11-preflight\n", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        with suppress(OSError):
            probe.unlink(missing_ok=True)
        raise HarnessError(
            f"El preflight no pudo escribir y retirar {probe}: {type(exc).__name__}."
        ) from exc
    return output_dir


def _foreign_owned_entries(output_dir: Path, expected_uid: int) -> list[Path]:
    """Entradas anidadas cuyo dueño impediría que Playwright limpie el directorio."""
    foreign: list[Path] = []
    for parent, directories, files in os.walk(output_dir, followlinks=False):
        for name in (*directories, *files):
            path = Path(parent) / name
            try:
                if path.lstat().st_uid != expected_uid:
                    foreign.append(path)
            except OSError:  # desapareció mientras se recorría
                continue
    return foreign


def verify_frontend_tooling(repo_root: Path) -> None:
    frontend_root = repo_root / "frontend"
    if shutil.which("node") is None or shutil.which("npm") is None:
        raise HarnessError("El preflight frontend exige node y npm disponibles en PATH.")

    checks = (
        (("node", "--version"), "Node"),
        (("npm", "--version"), "npm"),
    )
    outputs: dict[str, str] = {}
    for command, label in checks:
        try:
            completed = subprocess.run(
                command,
                cwd=frontend_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HarnessError(
                f"El preflight frontend no pudo verificar {label}: {type(exc).__name__}."
            ) from exc
        if completed.returncode != 0:
            raise HarnessError(f"El preflight frontend rechazó {label}.")
        outputs[label] = completed.stdout.strip()

    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", outputs["Node"])
    if match is None or tuple(int(part) for part in match.groups()) < _MINIMUM_NODE_VERSION:
        minimum = ".".join(str(part) for part in _MINIMUM_NODE_VERSION)
        raise HarnessError(f"El preflight frontend exige Node >= {minimum}.")
    if re.fullmatch(r"\d+\.\d+\.\d+", outputs["npm"]) is None:
        raise HarnessError("El preflight frontend no pudo identificar la versión de npm.")

    package_lock = frontend_root / "package-lock.json"
    playwright_cli = frontend_root / "node_modules" / ".bin" / "playwright"
    if not package_lock.is_file():
        raise HarnessError("El preflight frontend exige frontend/package-lock.json.")
    if not playwright_cli.is_file():
        raise HarnessError("El preflight frontend exige npm ci y la CLI local de Playwright.")

    s11_config = frontend_root / _S11_PLAYWRIGHT_CONFIG
    if not s11_config.is_file():
        raise HarnessError(f"El preflight frontend exige frontend/{_S11_PLAYWRIGHT_CONFIG}.")

    command = ("npm", "exec", "playwright", _NPM_SEPARATOR, "--version")
    try:
        completed = subprocess.run(
            command,
            cwd=frontend_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HarnessError(
            f"El preflight frontend no pudo verificar Playwright: {type(exc).__name__}."
        ) from exc
    if completed.returncode != 0:
        raise HarnessError("El preflight frontend rechazó Playwright.")
    # Si npm interceptara el argumento respondería su propia versión («X.Y.Z»)
    # en lugar de la de Playwright («Version X.Y.Z»).
    if _PLAYWRIGHT_VERSION.match(completed.stdout.strip()) is None:
        raise HarnessError(
            "El preflight frontend no obtuvo la versión de Playwright; npm podría "
            "estar interceptando los argumentos posteriores al paquete."
        )

    verify_playwright_output_dir(repo_root)


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
