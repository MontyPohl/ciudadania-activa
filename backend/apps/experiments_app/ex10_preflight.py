"""Preflight operacional de EX-10 (fase 2B-2). Operacional, no experimental.

Comprueba que un stack EX-10 ya preparado cumple el contrato técnico antes de integrar el
executor: repositorio, identidad del protocolo, dataset y plan, aislamiento, configuración
del API, throttle, health, READY_STATE, tokens en memoria, worker y broker.

Nunca ejecuta carga, nunca crea `run_id`, nunca escribe evidencia y nunca declara EX-10
preparado: `ready_for_execution` es siempre `false`. Su único HTTP es un `GET` al health y
su único tráfico Celery, dos probes de control. Todo corre dentro de `SideEffectGuard`,
que bloquea escrituras en la base, publicación de tareas, escrituras en las raíces de
evidencia y de objetos, red fuera de los hosts aprobados (Redis, health y el destino
PostgreSQL S11 declarado) y subprocesos que no sean `git`.

Resultado global: `PASS`, `FAIL` o `INCOMPLETE`, con precedencia de `INCOMPLETE` (una
comprobación obligatoria que no pudo demostrarse). `NOT_RUN` es sólo un estado interno de
un check: no corrió porque un prerrequisito declarado en `CHECK_PLAN` quedó `FAIL` o
`INCOMPLETE`, y cita esa causa (`blocked-by:<check>`). Así justificado no cuenta, porque su
causa ya impide `PASS`; sin esa causa el resultado es `INCOMPLETE` (`unexpected-not-run`).

`--source-root` tiene que ser un checkout Git completo, legible y de sólo lectura: con una
vista parcial (como el `/s11-source` del overlay S11) ni HEAD ni la limpieza del árbol se
infieren, y el resultado es `INCOMPLETE` (`partial-source-root`).
"""

from __future__ import annotations

import functools
import http.client
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

import celery
import django
import kombu
import psycopg
import redis
import rest_framework
import rest_framework_simplejwt
from celery.app.base import Celery
from celery.app.task import Task
from django.conf import settings
from django.db import DatabaseError, connection, connections
from django.db.models.signals import m2m_changed, pre_delete, pre_save
from django.urls import Resolver404, resolve
from django.utils import timezone
from kombu import Producer
from kombu.exceptions import KombuError
from redis import Redis
from redis.exceptions import RedisError

from . import ex10_fixture, ex10_load, ex10_state, ex10_worker_probe, runtime
from .canonical import canonical_json_bytes

SCHEMA_VERSION = "s11c3-ex10-preflight-v1"
#: Servicio backend del overlay S11, sin credenciales: el único HTTP del preflight.
HEALTH_URL = f"http://{ex10_load.HOST_HEADER}/api/v1/health/"
HEALTH_TIMEOUT_SECONDS = 5.0
PROBE_TIMEOUT_SECONDS = 3.0
#: G0: dos probes previstos, nunca un tercero oculto.
PROBE_ROUNDS = 2
MAX_HEALTH_BODY_BYTES = 64 * 1024
MAX_BROKER_KEYS = 10_000
MAX_CONTROL_MESSAGES = 10
REDIS_TIMEOUT_SECONDS = 2.0

#: Referencias congeladas de G0, G0-A1 y G0-A2, independientes de `ex10_load`.
G0_DECISION_COMMIT = "1693d0ae562bfe777c496b616c9f8c8d5fa8a591"
G0_A1_DECISION_COMMIT = "db7fe6649213a8d26f4ee60d36a797ee54f80322"
G0_A2_DECISION_COMMIT = "2bab6cabe4108e1eddbb319c3f859ed75c914004"
EXPECTED_IDENTITY_KEYS = frozenset(
    {
        "protocol_id",
        "methodology_version",
        "scenario_id",
        "decision_reference",
        "decision_commit",
        "decision_addendum_commit",
        "decision_addendum_a2_commit",
        "dataset",
        "load",
        "request",
        "system_under_test",
        "thresholds",
        "drain_limit_seconds",
    }
)
FROZEN_IDENTITY = {
    "protocol_id": "s11-ex10-v1",
    "methodology_version": "S11A-v1.1",
    "scenario_id": "EX-10",
    "decision_commit": G0_DECISION_COMMIT,
    "decision_addendum_commit": G0_A1_DECISION_COMMIT,
    "decision_addendum_a2_commit": G0_A2_DECISION_COMMIT,
    "dataset": {
        "id": "s11-load-v1",
        "seed": 20260921,
        "sha256": "c6883b59dc861fb07fae730594c929fdd572a9ed71aee6d8b85c87f71ede0acb",
    },
}
#: Código que debe coincidir byte a byte entre el proceso y `--source-root`. Incluye el
#: executor oficial (runtime, sin sus tests): el preflight del HEAD que ejecutará EX-10
#: demuestra también que ese aparato es el del checkout calificado. `local_storage.py` guarda
#: las imágenes de los reportes en la raíz aislada durante la emisión (backend).
RUNNING_SOURCES = (
    "backend/apps/experiments_app/ex10_load.py",
    "backend/apps/experiments_app/ex10_fixture.py",
    "backend/apps/experiments_app/ex10_state.py",
    "backend/apps/experiments_app/ex10_preflight.py",
    "backend/apps/experiments_app/ex10_worker_probe.py",
    "backend/apps/experiments_app/tasks.py",
    "backend/apps/experiments_app/management/commands/s11_ex10_preflight.py",
    "backend/apps/experiments_app/ex10_executor.py",
    "backend/apps/experiments_app/ex10_prior_custody.py",
    "backend/apps/experiments_app/ex10_reconcile.py",
    "backend/apps/experiments_app/management/commands/s11_ex10.py",
    # Shared modules actually imported by the executor/closed v2 consumer.
    "backend/apps/experiments_app/ex10_bridge_protocol.py",
    "backend/apps/experiments_app/ex10_open_evidence.py",
    "backend/apps/experiments_app/ex10_runtime_profile.py",
    "backend/apps/experiments_app/ex10_session_v2.py",
    "backend/apps/experiments_app/ex10_deployment_envelope.py",
    "backend/apps/experiments_app/canonical.py",
    "backend/apps/experiments_app/constants.py",
    "backend/apps/experiments_app/manifest.py",
    "backend/apps/experiments_app/evidence.py",
    "backend/apps/experiments_app/custody_policy.py",
    "backend/apps/experiments_app/redaction.py",
    "backend/apps/experiments_app/runtime.py",
    "backend/apps/experiments_app/custody.py",
    "backend/apps/experiments_app/isolation.py",
    "backend/apps/experiments_app/local_storage.py",
)
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_RATE = re.compile(r"^[0-9]{1,6}/[a-z]{1,10}$")
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9_.:@=+,/ -]{0,160}$")
_RUN_LOCK = threading.Lock()


class CheckResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCOMPLETE = "INCOMPLETE"
    #: Sólo por check, nunca global: bloqueado por un prerrequisito FAIL o INCOMPLETE.
    NOT_RUN = "NOT_RUN"


class Gate(StrEnum):
    READY_FOR_EXECUTOR_INTEGRATION = "READY_FOR_EXECUTOR_INTEGRATION"
    NOT_READY = "NOT_READY"
    INCOMPLETE = "INCOMPLETE"


EXIT_PASS, EXIT_FAIL, EXIT_INCOMPLETE, EXIT_USAGE = 0, 1, 2, 3
_GATES = {
    CheckResult.PASS: (Gate.READY_FOR_EXECUTOR_INTEGRATION, EXIT_PASS),
    CheckResult.FAIL: (Gate.NOT_READY, EXIT_FAIL),
    CheckResult.INCOMPLETE: (Gate.INCOMPLETE, EXIT_INCOMPLETE),
}
_BLOCKING = frozenset({CheckResult.FAIL, CheckResult.INCOMPLETE})
_BLOCKED_BY = "blocked-by:"

_WORKER_PREREQUISITES = ("isolation_guards", "isolation_redis", "api_broker")
#: Plan congelado: cada paso del runner, sus checks obligatorios en orden de emisión y los
#: prerrequisitos de cada uno. Un check corre sólo si todos sus prerrequisitos pasaron; si
#: no, queda NOT_RUN citando la causa raíz. Los `ready_state:<check>` los emite el paso
#: `ready_state` (o un único `ready_state` si no pudo leerse).
CHECK_PLAN: tuple[tuple[str, tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    (
        "repository",
        (
            ("repository_source_complete", ()),
            ("repository_head", ("repository_source_complete",)),
            ("repository_clean", ("repository_source_complete",)),
            ("repository_running_source", ("repository_source_complete",)),
            ("protocol_decision_commits", ("repository_source_complete",)),
        ),
    ),
    ("protocol", (("protocol_identity", ()), ("runtime_versions", ()))),
    (
        "dataset_and_plan",
        (
            ("dataset_sha256", ()),
            ("plan_shape", ("dataset_sha256",)),
            ("plan_sha256", ("plan_shape",)),
            ("plan_actors", ("dataset_sha256",)),
        ),
    ),
    (
        "isolation",
        (
            ("isolation_guards", ()),
            ("isolation_database_connected", ("isolation_guards",)),
            ("isolation_redis", ("isolation_guards",)),
            ("isolation_storage_roots", ("isolation_guards",)),
            ("isolation_non_production", ()),
        ),
    ),
    (
        "api_config",
        (
            ("api_task_always_eager", ()),
            ("api_broker", ()),
            ("api_result_backend", ()),
            ("api_fake_ai", ()),
            ("api_ai_adapter", ()),
            ("api_classify_task_registered", ()),
        ),
    ),
    ("throttle", (("throttle_report_create", ()),)),
    # Sin aislamiento aprobado no hay GET: no se sabe a qué stack se hablaría.
    ("health", (("health", ("isolation_guards",)), ("health_components", ("health",)))),
    ("ready_state", (("ready_state", ("isolation_guards",)),)),
    ("multipart_bodies", (("plan_multipart_bodies", ("isolation_guards", "plan_sha256")),)),
    ("tokens", (("token_issuance_capability", ("ready_state:fixture_actors", "plan_actors")),)),
    (
        "worker_and_broker",
        (
            ("broker_dynamic_before", _WORKER_PREREQUISITES),
            ("broker_classification_before", _WORKER_PREREQUISITES),
            ("worker_probe_1", _WORKER_PREREQUISITES),
            ("worker_probe_2", _WORKER_PREREQUISITES),
            ("worker_config", ("worker_probe_1",)),
            ("worker_stability", ("worker_probe_1", "worker_probe_2")),
            ("broker_dynamic_after", _WORKER_PREREQUISITES),
            ("broker_classification_after", _WORKER_PREREQUISITES),
        ),
    ),
    ("side_effects", (("no_side_effects", ()),)),
    ("coverage", (("check_coverage", ()),)),
)
PREREQUISITES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {name: prerequisites for _, entries in CHECK_PLAN for name, prerequisites in entries}
)
REQUIRED_CHECKS = tuple(PREREQUISITES)
_STEP_OF = MappingProxyType({name: step for step, entries in CHECK_PLAN for name, _ in entries})


def _family(name: str) -> str:
    return "ready_state" if name.startswith("ready_state:") else name


def _declared(name: str) -> tuple[str, ...]:
    return ("ready_state",) if name.startswith("ready_state:") else PREREQUISITES.get(name, ())


@functools.cache
def allowed_blockers(name: str) -> frozenset[str]:
    """Causas que pueden justificar que `name` no corra: sus prerrequisitos, transitivos,
    y el aborto por la guarda de cualquier paso implicado."""
    seen: set[str] = set()
    pending = [name]
    while pending:
        for prerequisite in _declared(pending.pop()):
            if prerequisite not in seen:
                seen.add(prerequisite)
                pending.append(prerequisite)
    steps = {_STEP_OF.get(_family(item)) for item in seen | {name}} - {None}
    return frozenset(seen | {f"{step}_aborted" for step in steps})


def unjustified_not_run(checks: tuple[Check, ...]) -> tuple[str, ...]:
    """Los NOT_RUN sin una causa declarada que haya quedado FAIL o INCOMPLETE."""
    results = {check.check: check.result for check in checks}
    unjustified = []
    for check in checks:
        if check.result is not CheckResult.NOT_RUN:
            continue
        detail = check.detail_code or ""
        blocker = detail.removeprefix(_BLOCKED_BY) if detail.startswith(_BLOCKED_BY) else None
        if (
            blocker is None
            or results.get(blocker) not in _BLOCKING
            or blocker not in allowed_blockers(check.check)
        ):
            unjustified.append(check.check)
    return tuple(unjustified)


class PreflightError(Exception):
    """Error de uso o de configuración del propio preflight (exit 3). Sólo códigos."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class PreflightBusy(PreflightError):
    def __init__(self):
        super().__init__("preflight-already-running")


class PreflightViolation(Exception):
    """El preflight intentó un efecto prohibido; la guarda lo bloqueó antes de ocurrir."""

    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(kind)


def _sanitized(value: Any) -> Any:
    """Sólo `None`, booleanos, enteros y textos cortos sin URL: nunca un valor opaco."""
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, str):
        if not _SAFE_TEXT.fullmatch(value) or "://" in value:
            raise ValueError("observed value is not sanitized")
        return value
    if isinstance(value, list | tuple):
        return [_sanitized(item) for item in value]
    if isinstance(value, Mapping):
        return {_sanitized(str(key)): _sanitized(item) for key, item in value.items()}
    raise ValueError("observed value is not sanitized")


@dataclass(frozen=True, slots=True)
class Check:
    check: str
    result: CheckResult
    expected: Any = None
    observed_sanitized: Any = None
    detail_code: str | None = None

    def __post_init__(self):
        for value in (self.check, self.expected, self.observed_sanitized, self.detail_code):
            _sanitized(value)

    def to_json(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "result": str(self.result),
            "expected": _sanitized(self.expected),
            "observed_sanitized": _sanitized(self.observed_sanitized),
            "detail_code": self.detail_code,
        }


def aggregate(checks: tuple[Check, ...]) -> CheckResult:
    """INCOMPLETE > FAIL > PASS. NOT_RUN no decide sólo si está justificado; nunca es PASS."""
    decisive = {check.result for check in checks if check.result is not CheckResult.NOT_RUN}
    if not checks or CheckResult.INCOMPLETE in decisive or unjustified_not_run(checks):
        return CheckResult.INCOMPLETE
    if CheckResult.FAIL in decisive:
        return CheckResult.FAIL
    return CheckResult.PASS


@dataclass(frozen=True, slots=True)
class PreflightReport:
    checks: tuple[Check, ...]
    expected_git_sha: str
    git_sha: str | None
    checked_at: datetime
    health_requests: int
    control_probes: int

    @property
    def result(self) -> CheckResult:
        return aggregate(self.checks)

    @property
    def gate(self) -> Gate:
        return _GATES[self.result][0]

    @property
    def exit_code(self) -> int:
        return _GATES[self.result][1]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "protocol_version": ex10_load.PROTOCOL_ID,
            "methodology_version": ex10_load.METHODOLOGY_VERSION,
            "scenario_id": ex10_load.SCENARIO_ID,
            "result": str(self.result),
            "gate": str(self.gate),
            # Invariantes del preflight: nunca prepara ni ejecuta EX-10.
            "ready_for_execution": False,
            "run_id": None,
            "evidence_written": False,
            "report_posts": 0,
            "provider_calls": 0,
            "task_publishes": 0,
            "health_requests": self.health_requests,
            "control_probes": self.control_probes,
            "expected_git_sha": self.expected_git_sha,
            "git_sha": self.git_sha,
            "checked_at": self.checked_at.isoformat().replace("+00:00", "Z"),
            "checks": [check.to_json() for check in self.checks],
        }

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json())


# --- Guarda de efectos laterales --------------------------------------------------------------


_ACTIVE: ContextVar[SideEffectGuard | None] = ContextVar("s11_ex10_preflight_guard", default=None)
_CONTAINED: ContextVar[str | None] = ContextVar("s11_ex10_preflight_contained", default=None)
_IN_HOOK: ContextVar[bool] = ContextVar("s11_ex10_preflight_in_hook", default=False)
_HOOK_LOCK = threading.Lock()
_HOOK_INSTALLED = False
#: Lecturas y control transaccional; cualquier otra sentencia es una escritura.
_READ_STATEMENTS = frozenset({"SELECT", "SHOW", "SAVEPOINT", "RELEASE", "ROLLBACK"})
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_FS_EVENTS = frozenset(
    {
        "os.mkdir",
        "os.rename",
        "os.remove",
        "os.rmdir",
        "os.symlink",
        "os.link",
        "os.truncate",
        "os.chmod",
        "os.chown",
        "os.utime",
        "shutil.rmtree",
        "shutil.move",
        "shutil.copyfile",
    }
)


def _audit_hook(event: str, args: tuple) -> None:
    guard = _ACTIVE.get()
    if guard is None or _IN_HOOK.get():
        return
    token = _IN_HOOK.set(True)
    try:
        guard._on_audit(event, args)
    finally:
        _IN_HOOK.reset(token)


def _install_audit_hook() -> None:
    # Un audit hook no se puede retirar: se instala una vez y queda inerte sin guarda activa.
    global _HOOK_INSTALLED
    with _HOOK_LOCK:
        if not _HOOK_INSTALLED:
            sys.addaudithook(_audit_hook)
            _HOOK_INSTALLED = True


def _violation(kind: str) -> PreflightViolation:
    guard = _ACTIVE.get()
    if guard is not None:
        guard.record(kind)
    return PreflightViolation(kind)


def _port_number(value: Any) -> int | None:
    """El puerto pedido a `getaddrinfo` (psycopg lo pasa como texto); otra cosa, `None`."""
    if isinstance(value, bytes):
        value = value.decode("ascii", "replace")
    if isinstance(value, str):
        return int(value) if value.isascii() and value.isdecimal() else None
    return value if type(value) is int else None


class SideEffectGuard:
    """Bloquea todo efecto persistente o externo mientras corre el preflight.

    - base: sólo lecturas y control transaccional por los cursores de Django, y ninguna
      señal de guardado o borrado de modelos;
    - Celery: `apply_async`/`delay`/`send_task` (el control del pidbox no pasa por ahí);
    - archivos: escrituras bajo las raíces de evidencia, de objetos y `--source-root`;
    - red: resolución de nombres fuera de los hosts aprobados (health y Redis, por nombre) y
      del destino PostgreSQL aprobado (host y puerto exactos). Sólo se observa
      `socket.getaddrinfo`: ni `connect` a una IP literal ni la conexión de libpq pasan por
      aquí, y nada de lo que devuelve una resolución permitida queda aprobado;
    - subprocesos: sólo `git`.
    """

    def __init__(
        self,
        *,
        protected_roots: tuple[Path, ...],
        allowed_hosts: frozenset[str],
        database_endpoint: tuple[str, int] | None = None,
    ):
        self.protected_roots = tuple(os.path.realpath(root) for root in protected_roots)
        self.allowed_hosts = allowed_hosts
        self.database_endpoint = database_endpoint
        self.violations: list[tuple[str, str | None]] = []
        self.health_requests = 0
        self._stack: ExitStack | None = None
        self._token = None

    def record(self, kind: str) -> None:
        self.violations.append((kind, _CONTAINED.get()))

    def uncontained(self) -> list[str]:
        return [kind for kind, scope in self.violations if scope is None]

    @contextmanager
    def contained(self, scope: str) -> Iterator[None]:
        """Un bloqueo aquí lo declara el check que lo contiene, no la guarda global."""
        token = _CONTAINED.set(scope)
        try:
            yield
        finally:
            _CONTAINED.reset(token)

    def __enter__(self) -> SideEffectGuard:
        _install_audit_hook()
        stack = ExitStack()
        for alias in connections:
            stack.enter_context(connections[alias].execute_wrapper(self._database))
        for signal in (pre_save, pre_delete, m2m_changed):
            signal.connect(
                self._model_signal, weak=False, dispatch_uid=f"ex10-preflight-{id(self)}"
            )
            stack.callback(signal.disconnect, dispatch_uid=f"ex10-preflight-{id(self)}")
        stack.enter_context(_patched(Task, "apply_async", _blocked_publish))
        stack.enter_context(_patched(Celery, "send_task", _blocked_publish))
        self._token = _ACTIVE.set(self)
        stack.callback(_ACTIVE.reset, self._token)
        self._stack = stack
        return self

    def __exit__(self, *exc_info) -> None:
        stack, self._stack = self._stack, None
        if stack is not None:
            stack.close()

    def _database(self, execute, sql, params, many, context):
        if _ACTIVE.get() is self:
            statement = str(sql).lstrip().split(None, 1)
            if not statement or statement[0].upper() not in _READ_STATEMENTS:
                raise _violation("database-write")
        return execute(sql, params, many, context)

    def _model_signal(self, **kwargs) -> None:
        if _ACTIVE.get() is self:
            raise _violation("model-write")

    def _protected(self, path: Any) -> bool:
        try:
            resolved = os.path.realpath(os.fsdecode(path))
        except (TypeError, ValueError):
            return False
        return any(
            resolved == root or resolved.startswith(root + os.sep) for root in self.protected_roots
        )

    def _on_audit(self, event: str, args: tuple) -> None:
        if event == "open" and len(args) >= 3 and not isinstance(args[0], int):
            path, mode, flags = args[:3]
            writing = (isinstance(flags, int) and flags & _WRITE_FLAGS) or (
                isinstance(mode, str) and any(char in mode for char in "wax+")
            )
            if writing and self._protected(path):
                raise _violation("filesystem-write")
        elif event in _FS_EVENTS:
            if any(self._protected(arg) for arg in args if isinstance(arg, str | bytes | Path)):
                raise _violation("filesystem-write")
        elif event == "socket.getaddrinfo" and args:
            host = args[0].decode() if isinstance(args[0], bytes) else args[0]
            port = _port_number(args[1]) if len(args) > 1 else None
            if host not in self.allowed_hosts and (host, port) != self.database_endpoint:
                raise _violation("network")
        elif event == "subprocess.Popen" and len(args) >= 2:
            executable, argv = args[0], args[1]
            program = executable or (argv[0] if isinstance(argv, list | tuple) and argv else "")
            if os.path.basename(os.fsdecode(program)) != "git":
                raise _violation("subprocess")


@contextmanager
def _patched(owner: type, name: str, replacement: Callable) -> Iterator[None]:
    original = owner.__dict__[name]

    def guarded(*args, **kwargs):
        if _ACTIVE.get() is not None:
            return replacement(*args, **kwargs)
        return original(*args, **kwargs)

    setattr(owner, name, guarded)
    try:
        yield
    finally:
        setattr(owner, name, original)


def _blocked_publish(*args, **kwargs):
    raise _violation("task-publish")


# --- Adapters reales ---------------------------------------------------------------------------


class RepositoryUnavailable(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SourceCompleteness:
    """Lo que prueba que `--source-root` es el checkout completo y no una vista parcial."""

    git_dir: str  # "directory" o "gitfile"
    toplevel: bool
    shallow: bool
    sparse: bool
    #: Entradas skip-worktree o assume-unchanged: `git status` no vería sus cambios.
    hidden_index_entries: int
    #: Patrones activos en `info/exclude`: podrían ocultar archivos sin seguimiento.
    local_excludes: bool
    missing_top_level: int
    #: De `RUNNING_SOURCES`: sin ellos no hay comparación con el código en ejecución.
    missing_required: int

    @property
    def problem(self) -> str | None:
        if not self.toplevel:
            return "source-root-not-toplevel"
        if self.shallow:
            return "shallow-source-root"
        if (
            self.sparse
            or self.hidden_index_entries
            or self.local_excludes
            or self.missing_top_level
            or self.missing_required
        ):
            return "partial-source-root"
        return None

    def observed(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


#: Un checkout completo: raíz del árbol de trabajo, historia entera y nada oculto a Git.
COMPLETE_SOURCE = MappingProxyType(
    {
        "toplevel": True,
        "shallow": False,
        "sparse": False,
        "hidden_index_entries": 0,
        "local_excludes": False,
        "missing_top_level": 0,
        "missing_required": 0,
    }
)


class GitRepository:
    """Git de sólo lectura sobre `--source-root`: nunca commit, stash, reset ni clean.

    `--no-optional-locks` (el índice nunca se reescribe, así que sirve sobre un montaje de
    sólo lectura) y un entorno mínimo: sin `GIT_DIR` heredado, sin configuración global, sin
    fsmonitor, caché de no seguidos ni `core.excludesFile` que oculten cambios, y sin subir
    por encima de la raíz a buscar otro repositorio. El checkout tiene que pertenecer al
    usuario del proceso o figurar en `safe.directory` de la configuración del sistema.
    """

    _CONFIG = (
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "core.excludesFile=/dev/null",
    )
    _MAX_METADATA_BYTES = 64 * 1024

    def __init__(self, root: Path):
        self.root = root

    def _git(self, *args: str, ok: tuple[int, ...] = (0,)) -> tuple[int, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": "/nonexistent",
            "LC_ALL": "C",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CEILING_DIRECTORIES": os.path.dirname(os.path.realpath(self.root)),
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
        try:
            completed = subprocess.run(
                ("git", "--no-optional-locks", *self._CONFIG, "-C", str(self.root), *args),
                capture_output=True,
                check=False,
                env=env,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            raise RepositoryUnavailable("git-unavailable") from None
        if completed.returncode not in ok:
            raise RepositoryUnavailable("git-command-failed")
        return completed.returncode, completed.stdout

    def _bounded(self, path: Path) -> bytes | None:
        try:
            with path.open("rb") as handle:
                return handle.read(self._MAX_METADATA_BYTES + 1)
        except OSError:
            return None

    def _git_entry(self) -> str:
        """`.git` propio y resoluble; sin él no se ejecuta Git (no se infiere nada)."""
        entry = self.root / ".git"
        if not os.path.lexists(entry):
            raise RepositoryUnavailable("source-root-not-git")
        if entry.is_dir():
            return "directory"
        raw = self._bounded(entry) if entry.is_file() else None
        text = raw.decode("utf-8", "replace") if raw else ""
        target = text.removeprefix("gitdir:").strip() if text.startswith("gitdir:") else ""
        if not target or "\n" in target or not (self.root / target).is_dir():
            raise RepositoryUnavailable("broken-gitfile")
        return "gitfile"

    def completeness(self) -> SourceCompleteness:
        git_dir = self._git_entry()
        lines = self._git(
            "rev-parse", "--is-inside-work-tree", "--is-shallow-repository", "--show-toplevel"
        )[1].splitlines()
        if len(lines) != 3:
            raise RepositoryUnavailable("git-command-failed")
        inside, shallow, toplevel = lines
        sparse = self._git("config", "--bool", "--get", "core.sparseCheckout", ok=(0, 1))[1]
        entries = [item for item in self._git("ls-files", "-v", "-z")[1].split("\0") if item]
        tracked = {item[2:] for item in entries}
        exclude = self._git("rev-parse", "--git-path", "info/exclude")[1].strip()
        exclude_path = self.root / exclude
        exclude_raw = self._bounded(exclude_path) if os.path.lexists(exclude_path) else b""
        tops = {path.split("/", 1)[0] for path in tracked}
        return SourceCompleteness(
            git_dir=git_dir,
            toplevel=inside == "true" and os.path.realpath(toplevel) == os.path.realpath(self.root),
            shallow=shallow == "true",
            sparse=sparse.strip() == "true",
            hidden_index_entries=sum(1 for item in entries if item[0] == "S" or item[0].islower()),
            local_excludes=exclude_raw is None
            or len(exclude_raw) > self._MAX_METADATA_BYTES
            or any(
                line.strip() and not line.lstrip().startswith(b"#")
                for line in exclude_raw.splitlines()
            ),
            missing_top_level=sum(1 for top in tops if not os.path.lexists(self.root / top)),
            missing_required=sum(
                1
                for relative in RUNNING_SOURCES
                if relative not in tracked
                or (self.root / relative).is_symlink()
                or not (self.root / relative).is_file()
            ),
        )

    def head(self) -> str:
        return self._git("rev-parse", "--verify", "HEAD^{commit}")[1].strip()

    def dirty_entries(self) -> int:
        output = self._git(
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=none",
        )[1]
        return sum(1 for entry in output.split("\0") if entry[:2].strip())

    def is_ancestor(self, sha: str) -> bool:
        # Un commit que el repositorio no tiene tampoco está en la historia de HEAD.
        if self._git("cat-file", "-e", f"{sha}^{{commit}}", ok=(0, 1, 128))[0] != 0:
            return False
        return self._git("merge-base", "--is-ancestor", sha, "HEAD", ok=(0, 1))[0] == 0

    def read(self, relative: str) -> bytes | None:
        path = self.root / relative
        try:
            return path.read_bytes() if path.is_file() and not path.is_symlink() else None
        except OSError:
            return None


class HealthUnavailable(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # una redirección sería un segundo request


class HttpHealthClient:
    """Exactamente un `GET` al health: sin proxies, sin redirecciones, cuerpo acotado."""

    def __init__(self, url: str = HEALTH_URL, timeout: float = HEALTH_TIMEOUT_SECONDS):
        self.url = url
        self.timeout = timeout

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or ""

    def get(self) -> tuple[int, bytes]:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
        request = urllib.request.Request(
            self.url, method="GET", headers={"Accept": "application/json"}
        )
        try:
            with opener.open(request, timeout=self.timeout) as response:
                return response.status, response.read(MAX_HEALTH_BODY_BYTES + 1)
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read(MAX_HEALTH_BODY_BYTES + 1)
            finally:
                exc.close()
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
            raise HealthUnavailable() from None


class ControlUnavailable(Exception):
    pass


class _SinglePublishProducer(Producer):
    """Un único intento: el mailbox de Kombu publica con `retry=True` y, sin política, un
    error de conexión republicaría el comando sin límite (un probe oculto de más)."""

    def publish(self, *args, **kwargs):
        kwargs["retry"] = False
        return super().publish(*args, **kwargs)


@contextmanager
def _single_publish_producer(producer=None, channel=None) -> Iterator[Producer]:
    yield _SinglePublishProducer(channel, auto_declare=False)


class CeleryWorkerControl:
    """Probe por el control plane de Celery. Nunca `delay`, `apply_async` ni `send_task`.

    Es lo mismo que `app.control.broadcast`, pero el comando sale por la conexión del
    probe (a `broker_url` de la app, que `api_broker` ya comparó con el Redis aprobado)
    con un productor de un único intento, y la respuesta vuelve por esa misma conexión.
    Un reintento como mucho para conectar, antes de publicar; ninguno para el probe.
    """

    def __init__(self, app: Celery | None = None):
        from config import celery_app

        self.app = app or celery_app

    def probe(self, nonce: str, timeout: float) -> list:
        try:
            with self.app.connection_for_write() as conn:
                conn.ensure_connection(max_retries=1, interval_start=0, timeout=timeout)
                mailbox = self.app.control.mailbox(conn)  # copia ligada: nada global cambia
                mailbox.producer_or_acquire = _single_publish_producer
                return list(
                    mailbox._broadcast(
                        ex10_worker_probe.PROBE_COMMAND,
                        {"nonce": nonce},
                        reply=True,
                        timeout=timeout,
                    )
                    or []
                )
        except (OSError, KombuError, RedisError):
            raise ControlUnavailable() from None


class ReadOnlyRedis:
    """Sólo lectura sobre el Redis aprobado: `FLUSHDB`, borrados y purgas quedan fuera."""

    def __init__(self, client: Any):
        self._client = client

    def ping(self):
        return self._client.ping()

    def llen(self, name):
        return self._client.llen(name)

    def hlen(self, name):
        return self._client.hlen(name)

    def zcard(self, name):
        return self._client.zcard(name)

    def scan_iter(self, match=None, count=None):
        return self._client.scan_iter(match=match, count=count)

    def type(self, name):
        return self._client.type(name)

    def pttl(self, name):
        return self._client.pttl(name)

    def lrange(self, name, start, end):
        return self._client.lrange(name, start, min(end, MAX_CONTROL_MESSAGES - 1))

    def flushdb(self, *args, **kwargs):
        raise _violation("broker-write")


@dataclass(frozen=True, slots=True)
class ApiConfig:
    task_always_eager: bool
    broker: Mapping[str, Any]
    broker_same_as_approved: bool
    result_backend: Mapping[str, Any]
    result_same_as_approved: bool
    approved: Mapping[str, Any]
    fake_ai_enabled: bool
    ai_adapter: str
    classify_task_registered: bool


@dataclass(frozen=True, slots=True)
class ThrottleConfig:
    rate: Any
    view_scope: Any
    scoped_throttle: bool


def real_api_config() -> ApiConfig:
    from config import celery_app

    conf = celery_app.conf
    probe = ex10_worker_probe
    return ApiConfig(
        task_always_eager=bool(conf.task_always_eager),
        broker=probe.endpoint_facts(conf.broker_url),
        broker_same_as_approved=conf.broker_url == settings.REDIS_URL,
        result_backend=probe.endpoint_facts(conf.result_backend),
        result_same_as_approved=conf.result_backend == settings.REDIS_URL,
        approved=probe.endpoint_facts(settings.REDIS_URL),
        fake_ai_enabled=bool(getattr(settings, "S11_FAKE_AI_ENABLED", False)),
        ai_adapter=probe.effective_ai_adapter(),
        classify_task_registered=probe.classify_task_registered(celery_app),
    )


def real_throttle_config() -> ThrottleConfig:
    from rest_framework.settings import api_settings
    from rest_framework.throttling import ScopedRateThrottle

    rate = api_settings.DEFAULT_THROTTLE_RATES.get(ex10_state.THROTTLE_SCOPE)
    try:
        view = resolve(ex10_load.ENDPOINT_PATH).func
    except Resolver404:
        return ThrottleConfig(rate, None, False)
    view_class = getattr(view, "cls", None) or getattr(view, "view_class", None)
    classes = tuple(getattr(view_class, "throttle_classes", ()) or ())
    return ThrottleConfig(
        rate,
        getattr(view_class, "throttle_scope", None),
        any(isinstance(item, type) and issubclass(item, ScopedRateThrottle) for item in classes),
    )


def real_database_identity() -> dict[str, bool]:
    """Base realmente conectada frente a la declarada, sólo como booleanos."""
    declared = urlsplit(runtime.isolation_config().database_url)
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_user, inet_server_port()")
        name, user, port = cursor.fetchone()
    return {
        "database": name == declared.path.lstrip("/"),
        "user": user == (declared.username or connection.settings_dict.get("USER")),
        "host": (connection.settings_dict.get("HOST") or "") == (declared.hostname or ""),
        "port": port == (declared.port or 5432),
    }


def real_category_ids(slugs: set[str]) -> dict[str, str]:
    from apps.admin_app.models import Category

    rows = Category.objects.filter(slug__in=sorted(slugs)).values_list("slug", "id")
    return {slug: str(pk) for slug, pk in rows}


def real_fixture_result(
    specs: tuple[ex10_fixture.ActorSpec, ...],
) -> ex10_fixture.FixtureResult:
    """El resultado de un fixture ya presente, sólo leído: no crea ni corrige nada."""
    from apps.auth_app.models import CitizenProfile

    profiles = dict(
        CitizenProfile.objects.filter(user_id__in=[spec.user_id for spec in specs]).values_list(
            "user_id", "id"
        )
    )
    return ex10_fixture.FixtureResult(
        ex10_fixture.FixtureOutcome.PREPARED,
        tuple(
            ex10_fixture.ActorOutcome(
                spec.alias,
                str(spec.user_id),
                str(profiles[spec.user_id]),
                ex10_fixture.ActorResult.ALREADY_MATCHED,
            )
            for spec in specs
        ),
        None,
    )


def _approve_environment() -> ex10_state.ApprovedEnvironment:
    return ex10_state.approve_environment(
        redis_factory=lambda url: ReadOnlyRedis(
            Redis.from_url(
                url,
                socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
                socket_timeout=REDIS_TIMEOUT_SECONDS,
            )
        )
    )


@dataclass(frozen=True, slots=True)
class PreflightOptions:
    expected_git_sha: str
    source_root: Path
    probe_timeout: float = PROBE_TIMEOUT_SECONDS


@dataclass
class Probes:
    """Todo lo que el preflight observa; los tests sustituyen cada pieza."""

    repository: Callable[[Path], Any] = GitRepository
    protocol_identity: Callable[[], Mapping[str, Any]] = ex10_load.protocol_identity
    load_dataset: Callable[[], ex10_load.LoadDataset] = ex10_load.load_dataset
    build_plan: Callable[[ex10_load.LoadDataset], ex10_load.LoadPlan] = ex10_load.build_plan
    approve_environment: Callable[[], ex10_state.ApprovedEnvironment] = _approve_environment
    database_identity: Callable[[], Mapping[str, bool]] = real_database_identity
    settings_module: Callable[[], str] = lambda: str(getattr(settings, "SETTINGS_MODULE", ""))
    api_config: Callable[[], ApiConfig] = real_api_config
    throttle_config: Callable[[], ThrottleConfig] = real_throttle_config
    health: Any = field(default_factory=HttpHealthClient)
    inspect_ready_state: Callable[..., ex10_state.StateReport] = ex10_state.inspect_ready_state
    category_ids: Callable[[set[str]], Mapping[str, str]] = real_category_ids
    fixture_result: Callable[..., ex10_fixture.FixtureResult] = real_fixture_result
    issue_tokens: Callable[..., ex10_fixture.EphemeralTokens] = ex10_fixture.issue_tokens
    verify_tokens: Callable[..., tuple[ex10_fixture.TokenCheck, ...]] = ex10_fixture.verify_tokens
    worker_control: Any = field(default_factory=CeleryWorkerControl)
    clock: Callable[[], datetime] = timezone.now
    nonce: Callable[[], str] = lambda: secrets.token_hex(16)


# --- Clasificación del broker ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BrokerSnapshot:
    dynamic: int
    static_bindings: int
    control_plane: Mapping[str, int]
    unknown: int
    task_messages: int
    unclassifiable: tuple[str, ...]

    def observed(self) -> dict[str, Any]:
        return {
            "dynamic": self.dynamic,
            "static_bindings": self.static_bindings,
            "control_plane": dict(self.control_plane),
            "unknown": self.unknown,
            "task_messages": self.task_messages,
        }


def _decoded(value: Any) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _is_task_message(raw: Any) -> bool | None:
    """`True` si el mensaje es una tarea Celery (protocolo 2); `None` si no se entiende."""
    try:
        envelope = json.loads(_decoded(raw))
    except ValueError:
        return None
    if not isinstance(envelope, Mapping):
        return None
    headers = envelope.get("headers")
    return isinstance(headers, Mapping) and "task" in headers


def classify_broker(redis_client: Any) -> BrokerSnapshot:
    """Todas las claves del Redis aprobado en tres clases; lo desconocido no se admite.

    A. dinámicas de carga (colas, `unacked`, resultados y throttle): deben ser cero;
    B. bindings estáticos `_kombu.binding.*`: permitidos;
    C. control plane efímero (mutex, pidbox, eventos): permitido si no contiene tareas.
    Nunca se devuelven nombres de clave: sólo clases y cantidades.
    """
    keys = ex10_state.broker_keys()
    exact_dynamic = {*keys.queues, keys.unacked, keys.unacked_index}
    dynamic_patterns = (*(keys.result_patterns or ()), ex10_state.throttle_redis_pattern())
    mutex, pidbox, events = keys.worker_ephemeral
    dynamic = static = unknown = task_messages = 0
    control = {"unacked_mutex": 0, "pidbox": 0, "celeryev": 0}
    unclassifiable: list[str] = []
    for index, raw_key in enumerate(redis_client.scan_iter(match="*", count=500)):
        if index >= MAX_BROKER_KEYS:
            unclassifiable.append("too-many-keys")
            break
        key = _decoded(raw_key)
        if key in exact_dynamic or any(fnmatchcase(key, p) for p in dynamic_patterns):
            dynamic += 1
        elif fnmatchcase(key, keys.static_bindings):
            static += 1
        elif key == mutex:
            control["unacked_mutex"] += 1
            if _decoded(redis_client.type(raw_key)) != "string" or redis_client.pttl(raw_key) <= 0:
                unclassifiable.append("mutex-without-ttl")
        elif fnmatchcase(key, pidbox) or fnmatchcase(key, events):
            control["pidbox" if fnmatchcase(key, pidbox) else "celeryev"] += 1
            if _decoded(redis_client.type(raw_key)) != "list":
                unclassifiable.append("control-plane-type")
                continue
            if redis_client.llen(raw_key) > MAX_CONTROL_MESSAGES:
                unclassifiable.append("control-plane-backlog")
                continue
            for message in redis_client.lrange(raw_key, 0, MAX_CONTROL_MESSAGES - 1):
                verdict = _is_task_message(message)
                if verdict is None:
                    unclassifiable.append("control-plane-message")
                elif verdict:
                    task_messages += 1
        else:
            unknown += 1
    return BrokerSnapshot(
        dynamic, static, control, unknown, task_messages, tuple(sorted(set(unclassifiable)))
    )


# --- Runner --------------------------------------------------------------------------------


def _code(value: str) -> str:
    return value.lower().replace("_", "-")


class _Runner:
    def __init__(self, options: PreflightOptions, probes: Probes, guard: SideEffectGuard):
        self.options = options
        self.probes = probes
        self.guard = guard
        self.checks: list[Check] = []
        self.results: dict[str, CheckResult] = {}
        self.blockers: dict[str, str] = {}
        self.git_sha: str | None = None
        self.control_probes = 0
        self.health_skipped = False
        self.dataset: ex10_load.LoadDataset | None = None
        self.plan: ex10_load.LoadPlan | None = None
        self.specs: tuple[ex10_fixture.ActorSpec, ...] | None = None
        self.environment: ex10_state.ApprovedEnvironment | None = None
        self.api: ApiConfig | None = None

    def add(self, name, result, expected=None, observed=None, detail=None) -> CheckResult:
        self.checks.append(Check(name, result, expected, observed, detail))
        self.results[name] = result
        return result

    def emitted(self, name: str) -> bool:
        if name == "ready_state":
            return any(_family(check) == "ready_state" for check in self.results)
        return name in self.results

    def blocked(self, name: str) -> bool:
        return any(self.results.get(item) is not CheckResult.PASS for item in _declared(name))

    def _root_cause(self, name: str) -> str | None:
        """El prerrequisito FAIL o INCOMPLETE que impide correr `name`, siguiendo NOT_RUN."""
        for prerequisite in _declared(name):
            result = self.results.get(prerequisite)
            if result in _BLOCKING:
                return prerequisite
            if result is CheckResult.NOT_RUN:
                return self.blockers[prerequisite]
            if result is None and (cause := self._root_cause(prerequisite)):
                return cause
        return None

    def skip(self, *names: str, cause: str | None = None) -> None:
        for name in names:
            # Sin causa resoluble queda `unresolved`: el agregado lo trata como injustificado.
            blocker = cause or self._root_cause(name) or "unresolved"
            self.blockers[name] = blocker
            self.add(name, CheckResult.NOT_RUN, detail=f"{_BLOCKED_BY}{blocker}")

    def runnable(self, *names: str) -> bool:
        """Si el primero tiene un prerrequisito sin PASS, todos quedan NOT_RUN."""
        if not self.blocked(names[0]):
            return True
        self.skip(*names)
        return False

    def run(self) -> None:
        for step_name, entries in CHECK_PLAN:
            try:
                getattr(self, step_name)()
            except PreflightViolation as exc:
                # La guarda ya bloqueó el efecto: el paso queda FAIL, lo que no llegó a
                # emitir queda NOT_RUN por ese aborto y el resto del plan sigue.
                marker = f"{step_name}_aborted"
                self.add(marker, CheckResult.FAIL, detail=f"guard-{exc.kind}")
                missing = [name for name, _ in entries if not self.emitted(name)]
                self.skip(*missing, cause=marker)

    # Repositorio y código en ejecución.
    def repository(self) -> None:
        expected = self.options.expected_git_sha
        try:
            repo = self.probes.repository(self.options.source_root)
            facts = repo.completeness()
        except RepositoryUnavailable as exc:
            self.add(
                "repository_source_complete",
                CheckResult.INCOMPLETE,
                COMPLETE_SOURCE,
                None,
                exc.code,
            )
        else:
            # Una vista parcial no prueba nada: ni HEAD ni limpieza se infieren de ella.
            problem = facts.problem
            self.add(
                "repository_source_complete",
                CheckResult.INCOMPLETE if problem else CheckResult.PASS,
                COMPLETE_SOURCE,
                facts.observed(),
                problem,
            )
        dependents = (
            "repository_head",
            "repository_clean",
            "repository_running_source",
            "protocol_decision_commits",
        )
        if not self.runnable(*dependents):
            return
        try:
            head = repo.head()
        except RepositoryUnavailable as exc:
            self.add("repository_head", CheckResult.INCOMPLETE, expected, None, exc.code)
        else:
            valid = bool(_SHA1.fullmatch(head))
            self.git_sha = head if valid else None
            self.add(
                "repository_head",
                CheckResult.PASS if head == expected else CheckResult.FAIL,
                expected,
                {"head": head if valid else "invalid"},
                None if head == expected else "head-mismatch",
            )
        try:
            dirty = repo.dirty_entries()
        except RepositoryUnavailable as exc:
            self.add("repository_clean", CheckResult.INCOMPLETE, detail=exc.code)
        else:
            self.add(
                "repository_clean",
                CheckResult.PASS if dirty == 0 else CheckResult.FAIL,
                {"dirty_entries": 0},
                {"dirty_entries": dirty},
                None if dirty == 0 else "working-tree-dirty",
            )
        backend = Path(settings.BASE_DIR)
        sources = []
        for relative in RUNNING_SOURCES:
            running = backend / relative.removeprefix("backend/")
            try:
                running_bytes = running.read_bytes()
            except OSError:
                running_bytes = None
            checked_out = repo.read(relative)
            if running_bytes is None:
                detail = "runtime-missing"
            elif checked_out is None:
                detail = "source-missing"
            elif checked_out != running_bytes:
                detail = "bytes-differ"
            else:
                detail = None
            # Sólo el identificador relativo al repo y el resultado: ni bytes ni rutas del host.
            sources.append({"source": relative, "match": detail is None, "detail_code": detail})
        mismatched = sum(1 for source in sources if not source["match"])
        self.add(
            "repository_running_source",
            CheckResult.PASS if mismatched == 0 else CheckResult.FAIL,
            {"mismatched": 0},
            {"compared": len(RUNNING_SOURCES), "mismatched": mismatched, "sources": sources},
            None if mismatched == 0 else "running-source-differs",
        )
        try:
            g0 = repo.is_ancestor(G0_DECISION_COMMIT)
            g0_a1 = repo.is_ancestor(G0_A1_DECISION_COMMIT)
            g0_a2 = repo.is_ancestor(G0_A2_DECISION_COMMIT)
        except RepositoryUnavailable as exc:
            self.add("protocol_decision_commits", CheckResult.INCOMPLETE, detail=exc.code)
            return
        ok = g0 and g0_a1 and g0_a2
        self.add(
            "protocol_decision_commits",
            CheckResult.PASS if ok else CheckResult.FAIL,
            {"g0_in_history": True, "g0_a1_in_history": True, "g0_a2_in_history": True},
            {"g0_in_history": g0, "g0_a1_in_history": g0_a1, "g0_a2_in_history": g0_a2},
            None if ok else "decision-commit-not-in-history",
        )

    def protocol(self) -> None:
        identity = self.probes.protocol_identity()
        problems = []
        if not isinstance(identity, Mapping) or set(identity) != EXPECTED_IDENTITY_KEYS:
            problems.append("identity-fields")
        else:
            problems += [
                f"identity-{_code(key)}"
                for key, value in FROZEN_IDENTITY.items()
                if identity.get(key) != value
            ]
        self.add(
            "protocol_identity",
            CheckResult.FAIL if problems else CheckResult.PASS,
            {"fields": len(EXPECTED_IDENTITY_KEYS), "g0": True, "g0_a1": True, "g0_a2": True},
            {"mismatches": len(problems)},
            ",".join(problems) or None,
        )
        versions = {
            "python": ".".join(map(str, sys.version_info[:3])),
            "django": django.get_version(),
            "celery": celery.__version__,
            "kombu": kombu.__version__,
            "redis": redis.__version__,
            "psycopg": psycopg.__version__,
            "djangorestframework": rest_framework.__version__,
            "simplejwt": getattr(rest_framework_simplejwt, "__version__", "unknown"),
        }
        self.add("runtime_versions", CheckResult.PASS, None, versions)

    def dataset_and_plan(self) -> None:
        try:
            dataset = self.probes.load_dataset()
        except ex10_load.Ex10DatasetError:
            self.add(
                "dataset_sha256",
                CheckResult.FAIL,
                ex10_load.DATASET_SHA256,
                None,
                "dataset-rejected",
            )
        except OSError:
            self.add("dataset_sha256", CheckResult.INCOMPLETE, detail="dataset-unreadable")
        else:
            valid = dataset.sha256 == ex10_load.DATASET_SHA256
            self.add(
                "dataset_sha256",
                CheckResult.PASS if valid else CheckResult.FAIL,
                ex10_load.DATASET_SHA256,
                {"matches": valid},
                None if valid else "dataset-sha256-mismatch",
            )
            self.dataset = dataset if valid else None
        if self.runnable("plan_shape", "plan_sha256"):
            self._plan(self.dataset)
        if self.runnable("plan_actors"):
            self._actors(self.dataset)

    def _plan(self, dataset: ex10_load.LoadDataset) -> None:
        try:
            plan = self.probes.build_plan(dataset)
        except ex10_load.Ex10PlanError:
            self.add("plan_shape", CheckResult.FAIL, detail="plan-rejected")
        else:
            per_vu = [len(plan.for_vu(vu)) for vu in plan.vus]
            shape_ok = (
                len(plan.items) == ex10_load.TOTAL_REQUESTS
                and plan.vus == tuple(range(1, ex10_load.VIRTUAL_USERS + 1))
                and per_vu == [ex10_load.REQUESTS_PER_USER] * ex10_load.VIRTUAL_USERS
            )
            self.add(
                "plan_shape",
                CheckResult.PASS if shape_ok else CheckResult.FAIL,
                {"items": ex10_load.TOTAL_REQUESTS, "virtual_users": ex10_load.VIRTUAL_USERS},
                {"items": len(plan.items), "virtual_users": len(plan.vus)},
                None if shape_ok else "plan-shape",
            )
        if not self.runnable("plan_sha256"):
            return
        sha_ok = plan.sha256 == ex10_load.PLAN_SHA256
        self.add(
            "plan_sha256",
            CheckResult.PASS if sha_ok else CheckResult.FAIL,
            ex10_load.PLAN_SHA256,
            {"matches": sha_ok},
            None if sha_ok else "plan-sha256-mismatch",
        )
        self.plan = plan if sha_ok else None

    def _actors(self, dataset: ex10_load.LoadDataset) -> None:
        # Sólo del dataset: un plan rechazado no impide comprobar los actores.
        try:
            first = ex10_fixture.actor_specs(dataset)
            second = ex10_fixture.actor_specs(dataset)
        except ex10_fixture.Ex10FixtureError as exc:
            self.add("plan_actors", CheckResult.FAIL, detail=_code(exc.code))
            return
        users = dataset.payload["users"]
        deterministic = (
            first == second
            and [spec.alias for spec in first]
            == [f"load-citizen-{n:02d}" for n in range(1, ex10_load.VIRTUAL_USERS + 1)]
            and [str(spec.user_id) for spec in first] == [user["id"] for user in users]
        )
        self.add(
            "plan_actors",
            CheckResult.PASS if deterministic else CheckResult.FAIL,
            {"actors": ex10_load.VIRTUAL_USERS, "deterministic": True},
            {"actors": len(first), "deterministic": deterministic},
            None if deterministic else "actors-not-deterministic",
        )
        self.specs = first if deterministic else None

    def isolation(self) -> None:
        dependents = (
            "isolation_database_connected",
            "isolation_redis",
            "isolation_storage_roots",
        )
        try:
            environment = self.probes.approve_environment()
        except ex10_state.Ex10StateError as exc:
            self.add("isolation_guards", CheckResult.FAIL, "approved", None, _code(exc.code))
        except (DatabaseError, RedisError, OSError):
            self.add(
                "isolation_guards",
                CheckResult.INCOMPLETE,
                "approved",
                None,
                "isolation-unavailable",
            )
        else:
            self.add("isolation_guards", CheckResult.PASS, "approved", "approved")
            self.environment = environment
        if self.runnable(*dependents):
            self._isolation_details(self.environment)
        module = self.probes.settings_module()
        productive = module.endswith(".prod") or module.endswith(".production")
        self.add(
            "isolation_non_production",
            CheckResult.FAIL if productive else CheckResult.PASS,
            {"production_settings": False},
            {"production_settings": productive},
            "production-settings" if productive else None,
        )

    def _isolation_details(self, environment: ex10_state.ApprovedEnvironment) -> None:
        try:
            identity = dict(self.probes.database_identity())
        except DatabaseError:
            self.add(
                "isolation_database_connected",
                CheckResult.INCOMPLETE,
                detail="database-unavailable",
            )
        else:
            matches = all(identity.values()) and set(identity) == {
                "database",
                "user",
                "host",
                "port",
            }
            self.add(
                "isolation_database_connected",
                CheckResult.PASS if matches else CheckResult.FAIL,
                {"database": True, "user": True, "host": True, "port": True},
                identity,
                None if matches else "connected-database-mismatch",
            )
        try:
            environment.redis.ping()
            redis_db = ex10_worker_probe.endpoint_facts(runtime.isolation_config().redis_url)["db"]
        except (RedisError, OSError):
            self.add("isolation_redis", CheckResult.INCOMPLETE, detail="redis-unavailable")
        else:
            self.add(
                "isolation_redis",
                CheckResult.PASS if redis_db == 11 else CheckResult.FAIL,
                {"db": 11, "reachable": True},
                {"db": redis_db, "reachable": True},
                None if redis_db == 11 else "redis-db",
            )
        roots = {
            "objects": Path(environment.object_root),
            "artifacts": Path(settings.S11_ARTIFACT_ROOT),
            "backup": Path(settings.S11_BACKUP_ROOT),
            "restore": Path(settings.S11_RESTORE_ROOT),
        }
        linked = sorted(name for name, root in roots.items() if root.is_symlink())
        marker = (roots["objects"] / ex10_state.OBJECT_ROOT_MARKER).is_file()
        ok = not linked and marker
        self.add(
            "isolation_storage_roots",
            CheckResult.PASS if ok else CheckResult.FAIL,
            {"symlinked_roots": 0, "object_root_marker": True},
            {"symlinked_roots": len(linked), "object_root_marker": marker},
            None if ok else ("storage-symlink" if linked else "object-root-marker"),
        )

    def api_config(self) -> None:
        config = self.api = self.probes.api_config()
        approved = dict(config.approved)
        approved_ok = approved.get("db") == 11 and approved.get("matches_protocol") is True

        def endpoint(name, facts, same):
            ok = same and approved_ok and dict(facts) == approved
            self.add(
                name,
                CheckResult.PASS if ok else CheckResult.FAIL,
                {"db": 11, "same_as_approved": True, "matches_protocol": True},
                {**dict(facts), "same_as_approved": same},
                None if ok else f"{name.replace('_', '-')}-not-approved",
            )

        self.add(
            "api_task_always_eager",
            CheckResult.PASS if config.task_always_eager is False else CheckResult.FAIL,
            False,
            config.task_always_eager,
            None if config.task_always_eager is False else "api-eager-enabled",
        )
        endpoint("api_broker", config.broker, config.broker_same_as_approved)
        endpoint("api_result_backend", config.result_backend, config.result_same_as_approved)
        flags = (
            ("api_fake_ai", config.fake_ai_enabled is True, True, config.fake_ai_enabled),
            (
                "api_ai_adapter",
                config.ai_adapter == ex10_load.AI_MODEL_NAME,
                ex10_load.AI_MODEL_NAME,
                config.ai_adapter if _SAFE_TEXT.fullmatch(str(config.ai_adapter)) else "invalid",
            ),
            (
                "api_classify_task_registered",
                config.classify_task_registered is True,
                True,
                config.classify_task_registered,
            ),
        )
        for name, ok, expected, observed in flags:
            self.add(
                name,
                CheckResult.PASS if ok else CheckResult.FAIL,
                expected,
                observed,
                None if ok else name.replace("_", "-"),
            )

    def throttle(self) -> None:
        config = self.probes.throttle_config()
        rate = config.rate
        parsed = None
        if isinstance(rate, str) and _RATE.fullmatch(rate):
            number, period = rate.split("/")
            seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(period[0])
            parsed = [int(number), seconds] if seconds else None
        if rate is None:
            detail = "rate-missing"
        elif parsed is None:
            detail = "rate-unparseable"
        elif rate != ex10_load.THROTTLE_REPORT_CREATE or parsed != [5, 3600]:
            detail = "rate-mismatch"
        elif config.view_scope != ex10_state.THROTTLE_SCOPE or not config.scoped_throttle:
            detail = "scope-mismatch"
        else:
            detail = None
        self.add(
            "throttle_report_create",
            CheckResult.PASS if detail is None else CheckResult.FAIL,
            {"rate": ex10_load.THROTTLE_REPORT_CREATE, "scope": ex10_state.THROTTLE_SCOPE},
            {
                "rate": rate if isinstance(rate, str) and _RATE.fullmatch(rate) else "invalid",
                "parsed": parsed,
                "scope_matches": config.view_scope == ex10_state.THROTTLE_SCOPE,
                "scoped_throttle": config.scoped_throttle,
            },
            detail,
        )

    def health(self) -> None:
        if not self.runnable("health", "health_components"):
            self.health_skipped = True
            return
        self.guard.health_requests += 1
        try:
            status, body = self.probes.health.get()
        except HealthUnavailable:
            self.add(
                "health", CheckResult.INCOMPLETE, {"http_status": 200}, None, "health-unreachable"
            )
        else:
            ok = status == 200
            self.add(
                "health",
                CheckResult.PASS if ok else CheckResult.FAIL,
                {"http_status": 200},
                {"http_status": status if isinstance(status, int) else None},
                None if ok else "health-http-status",
            )
        # Sin un 200 no hay cuerpo de health que evaluar.
        if self.runnable("health_components"):
            self._health_components(body)

    def _health_components(self, body: bytes) -> None:
        expected = {"status": "ok", "db": True, "redis": True}
        try:
            if len(body) > MAX_HEALTH_BODY_BYTES:
                raise ValueError
            payload = json.loads(body)
            state, db, cache = payload["status"], payload["db"], payload["redis"]
            if not isinstance(state, str) or type(db) is not bool or type(cache) is not bool:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            self.add(
                "health_components", CheckResult.INCOMPLETE, expected, None, "health-body-invalid"
            )
            return
        ok = state == "ok" and db and cache
        observed = {
            "status": state if state in {"ok", "degraded"} else "other",
            "db": db,
            "redis": cache,
        }
        self.add(
            "health_components",
            CheckResult.PASS if ok else CheckResult.FAIL,
            expected,
            observed,
            None if ok else "health-degraded",
        )

    def ready_state(self) -> None:
        if not self.runnable("ready_state"):
            return
        try:
            report = self.probes.inspect_ready_state(self.environment)
        except (DatabaseError, RedisError, OSError):
            self.add("ready_state", CheckResult.INCOMPLETE, detail="ready-state-unavailable")
            return
        for state_check in report.checks:
            self.add(
                f"ready_state:{state_check.check}",
                CheckResult(str(state_check.result)),
                state_check.expected,
                state_check.observed,
                state_check.detail,
            )

    def multipart_bodies(self) -> None:
        if not self.runnable("plan_multipart_bodies"):
            return
        slugs = {item.category_slug for item in self.plan.items}
        try:
            category_ids = self.probes.category_ids(slugs)
        except DatabaseError:
            self.add("plan_multipart_bodies", CheckResult.INCOMPLETE, detail="database-unavailable")
            return
        if slugs - set(category_ids):
            self.add(
                "plan_multipart_bodies",
                CheckResult.FAIL,
                {"prepared": ex10_load.TOTAL_REQUESTS},
                {"missing_categories": len(slugs - set(category_ids))},
                "category-missing",
            )
            return
        rows = self.dataset.request_rows()
        prepared = 0
        try:
            for item in self.plan.items:
                # Sólo el cuerpo: la cabecera lleva token y `run_id`, que crea el executor.
                body = ex10_load._report_body(
                    rows[item.request_key],
                    category_id=category_ids[item.category_slug],
                    boundary=ex10_load.boundary_for(item.request_key),
                )
                prepared += bool(body)
                del body
        except (ex10_load.Ex10PreparationError, KeyError, ValueError):
            self.add("plan_multipart_bodies", CheckResult.FAIL, detail="multipart-not-preparable")
            return
        ok = prepared == ex10_load.TOTAL_REQUESTS
        self.add(
            "plan_multipart_bodies",
            CheckResult.PASS if ok else CheckResult.FAIL,
            {"prepared": ex10_load.TOTAL_REQUESTS},
            {"prepared": prepared},
            None if ok else "multipart-count",
        )

    def tokens(self) -> None:
        name = "token_issuance_capability"
        if not self.runnable(name):
            return
        if "rest_framework_simplejwt.token_blacklist" in settings.INSTALLED_APPS:
            self.add(name, CheckResult.INCOMPLETE, detail="token-persistence-possible")
            return
        expected = {
            "issued": ex10_load.VIRTUAL_USERS,
            "verified": ex10_load.VIRTUAL_USERS,
            "released": True,
        }
        tokens = None
        try:
            with self.guard.contained("token-issuance"):
                result = self.probes.fixture_result(self.specs)
                tokens = self.probes.issue_tokens(result)
                checks = self.probes.verify_tokens(tokens, result, now=self.probes.clock())
        except PreflightViolation:
            self.add(
                name, CheckResult.INCOMPLETE, expected, None, "token-issuance-has-side-effects"
            )
            return
        except (DatabaseError, KeyError, ex10_fixture.Ex10FixtureError):
            self.add(name, CheckResult.INCOMPLETE, expected, None, "token-issuance-unavailable")
            return
        finally:
            if tokens is not None:
                issued = len(tokens)
                tokens.release()
        observed = {
            "issued": issued,
            "verified": sum(1 for check in checks if check.ok),
            "signature_valid": sum(1 for check in checks if check.signature_valid),
            "user_matches": sum(1 for check in checks if check.user_matches),
            "remaining_ok": sum(1 for check in checks if check.remaining_ok),
            "released": tokens.released,
        }
        ok = (
            observed["issued"] == observed["verified"] == ex10_load.VIRTUAL_USERS
            and tokens.released
        )
        self.add(
            name,
            CheckResult.PASS if ok else CheckResult.FAIL,
            expected,
            observed,
            None if ok else "token-verification",
        )

    def worker_and_broker(self) -> None:
        names = (
            "broker_dynamic_before",
            "broker_classification_before",
            "worker_probe_1",
            "worker_probe_2",
            "worker_config",
            "worker_stability",
            "broker_dynamic_after",
            "broker_classification_after",
        )
        # Sin aislamiento, Redis aprobado ni broker del API aprobado no sale ningún probe.
        if not self.runnable(*names):
            return
        self.broker_snapshot("before")
        facts = [self.worker_probe(round_number) for round_number in range(1, PROBE_ROUNDS + 1)]
        self.worker_config(facts[0])
        self.worker_stability(facts)
        self.broker_snapshot("after")

    def broker_snapshot(self, when: str) -> None:
        try:
            snapshot = classify_broker(self.environment.redis)
        except (RedisError, OSError):
            for name in (f"broker_dynamic_{when}", f"broker_classification_{when}"):
                self.add(name, CheckResult.INCOMPLETE, detail="broker-unavailable")
            return
        dynamic_ok = snapshot.dynamic == 0 and snapshot.task_messages == 0
        self.add(
            f"broker_dynamic_{when}",
            CheckResult.PASS if dynamic_ok else CheckResult.FAIL,
            {"dynamic": 0, "task_messages": 0},
            {"dynamic": snapshot.dynamic, "task_messages": snapshot.task_messages},
            None if dynamic_ok else "broker-dynamic-state",
        )
        classified = snapshot.unknown == 0 and not snapshot.unclassifiable
        self.add(
            f"broker_classification_{when}",
            CheckResult.PASS if classified else CheckResult.INCOMPLETE,
            {"unknown": 0},
            snapshot.observed(),
            None if classified else ",".join(snapshot.unclassifiable or ("unknown-broker-keys",)),
        )

    def worker_probe(self, round_number: int) -> dict[str, Any] | None:
        name = f"worker_probe_{round_number}"
        nonce = self.probes.nonce()
        self.control_probes += 1
        try:
            replies = self.probes.worker_control.probe(nonce, self.options.probe_timeout)
        except ControlUnavailable:
            self.add(name, CheckResult.INCOMPLETE, {"nodes": 1}, None, "control-unavailable")
            return None
        if not replies:
            self.add(name, CheckResult.INCOMPLETE, {"nodes": 1}, {"nodes": 0}, "no-worker-replied")
            return None
        try:
            validated = [ex10_worker_probe.validate_reply(reply, nonce) for reply in replies]
        except ex10_worker_probe.ReplyRejected as exc:
            self.add(name, CheckResult.FAIL, {"nodes": 1}, {"nodes": len(replies)}, exc.code)
            return None
        if len(validated) != 1:
            self.add(
                name, CheckResult.FAIL, {"nodes": 1}, {"nodes": len(validated)}, "multiple-workers"
            )
            return None
        self.add(name, CheckResult.PASS, {"nodes": 1}, {"nodes": 1, **validated[0]})
        return validated[0]

    def worker_config(self, facts: dict[str, Any] | None) -> None:
        if not self.runnable("worker_config"):
            return
        approved = self.api.approved
        expected_endpoint = {**dict(approved), "matches_protocol": True}
        problems = [
            code
            for code, ok in (
                (
                    "worker-concurrency",
                    facts["pool"]["max_concurrency"] == ex10_load.WORKER_CONCURRENCY,
                ),
                ("worker-eager", facts["task_always_eager"] is False),
                ("worker-broker", facts["broker"] == expected_endpoint),
                ("worker-result-backend", facts["result_backend"] == expected_endpoint),
                ("worker-fake", facts["fake_ai_enabled"] is True),
                ("worker-adapter", facts["ai_adapter"] == ex10_load.AI_MODEL_NAME),
                ("worker-task-not-registered", facts["classify_task_registered"] is True),
            )
            if not ok
        ]
        self.add(
            "worker_config",
            CheckResult.FAIL if problems else CheckResult.PASS,
            {
                "max_concurrency": ex10_load.WORKER_CONCURRENCY,
                "task_always_eager": False,
                "fake_ai_enabled": True,
                "ai_adapter": ex10_load.AI_MODEL_NAME,
                "classify_task_registered": True,
            },
            {
                "max_concurrency": facts["pool"]["max_concurrency"],
                "task_always_eager": facts["task_always_eager"],
                "fake_ai_enabled": facts["fake_ai_enabled"],
                "ai_adapter": facts["ai_adapter"],
                "classify_task_registered": facts["classify_task_registered"],
            },
            ",".join(problems) or None,
        )

    def worker_stability(self, facts: list[dict[str, Any] | None]) -> None:
        if not self.runnable("worker_stability"):
            return
        stable = all(item == facts[0] for item in facts[1:])
        self.add(
            "worker_stability",
            CheckResult.PASS if stable else CheckResult.FAIL,
            {"identical_probes": PROBE_ROUNDS},
            {"identical_probes": sum(1 for item in facts if item == facts[0])},
            None if stable else "worker-not-stable",
        )

    def side_effects(self) -> None:
        blocked = self.guard.uncontained()
        counts = {kind: blocked.count(kind) for kind in sorted(set(blocked))}
        # Un GET, o ninguno si el health quedó bloqueado por su prerrequisito.
        health_requests = 0 if self.health_skipped else 1
        ok = not blocked and self.guard.health_requests == health_requests
        self.add(
            "no_side_effects",
            CheckResult.PASS if ok else CheckResult.FAIL,
            {"blocked_effects": 0, "health_requests": health_requests, "report_posts": 0},
            {
                "blocked_effects": len(blocked),
                "blocked_by_kind": counts,
                "contained_effects": len(self.guard.violations) - len(blocked),
                "health_requests": self.guard.health_requests,
                "report_posts": 0,
            },
            None if ok else "side-effect-blocked",
        )

    def coverage(self) -> None:
        """Todo check obligatorio se emitió y todo NOT_RUN cita una causa que impide PASS."""
        unjustified = unjustified_not_run(tuple(self.checks))
        missing = [name for name in REQUIRED_CHECKS[:-1] if not self.emitted(name)]
        problems = [
            code
            for code, found in (
                ("unexpected-not-run", unjustified),
                ("missing-required-check", missing),
            )
            if found
        ]
        self.add(
            "check_coverage",
            CheckResult.INCOMPLETE if problems else CheckResult.PASS,
            {"unjustified_not_run": [], "missing_required": []},
            {
                "not_run": sum(1 for check in self.checks if check.result is CheckResult.NOT_RUN),
                "unjustified_not_run": list(unjustified),
                "missing_required": missing,
            },
            ",".join(problems) or None,
        )


def _protected_roots(options: PreflightOptions) -> tuple[Path, ...]:
    return (
        Path(settings.S11_ARTIFACT_ROOT),
        Path(settings.S11_BACKUP_ROOT),
        Path(settings.S11_RESTORE_ROOT),
        Path(settings.S11_OBJECT_ROOT),
        options.source_root,
    )


def _allowed_hosts(probes: Probes) -> frozenset[str]:
    hosts = {urlsplit(settings.REDIS_URL).hostname or ""}
    health_host = getattr(probes.health, "host", None)
    if health_host:
        hosts.add(health_host)
    return frozenset(host for host in hosts if host)


def _approved_database_endpoint(
    effective: Mapping[str, Any], declared_url: str
) -> tuple[str, int] | None:
    """El único destino PostgreSQL que la guarda deja resolver, o `None`.

    psycopg 3.2 resuelve el host con `socket.getaddrinfo` desde Python, y en un proceso nuevo
    la primera conexión ocurre ya dentro de la guarda. Se aprueban el host y el puerto de la
    conexión efectiva de Django sólo si son exactamente los de la base S11 declarada: la misma
    pareja que `isolation_database_connected` contrasta después con la base realmente
    conectada, así que aprobar la resolución no da la base por buena. No se resuelve nada:
    ninguna dirección queda aprobada.
    """
    host = effective.get("HOST")
    try:
        declared = urlsplit(declared_url)
        declared_port = declared.port or 5432
        port = int(effective.get("PORT") or 5432)
    except (TypeError, ValueError):
        return None
    # Un socket Unix (`/…`) nunca coincide: un `hostname` de URL no contiene `/`.
    if not isinstance(host, str) or not host or host != (declared.hostname or ""):
        return None
    return (host, port) if port == declared_port else None


def _side_effect_guard(options: PreflightOptions, probes: Probes) -> SideEffectGuard:
    return SideEffectGuard(
        protected_roots=_protected_roots(options),
        allowed_hosts=_allowed_hosts(probes),
        database_endpoint=_approved_database_endpoint(
            connection.settings_dict, runtime.isolation_config().database_url
        ),
    )


def run_preflight(options: PreflightOptions, probes: Probes | None = None) -> PreflightReport:
    """Un preflight completo. Uno a la vez por proceso: el segundo se rechaza sin esperar."""
    if not _SHA1.fullmatch(options.expected_git_sha):
        raise PreflightError("expected-git-sha-format")
    if not options.source_root.is_dir():
        raise PreflightError("source-root-missing")
    if not _RUN_LOCK.acquire(blocking=False):
        raise PreflightBusy()
    try:
        probes = probes or Probes()
        guard = _side_effect_guard(options, probes)
        runner = _Runner(options, probes, guard)
        with guard:
            runner.run()
        return PreflightReport(
            checks=tuple(runner.checks),
            expected_git_sha=options.expected_git_sha,
            git_sha=runner.git_sha,
            checked_at=probes.clock(),
            health_requests=guard.health_requests,
            control_probes=runner.control_probes,
        )
    finally:
        _RUN_LOCK.release()
