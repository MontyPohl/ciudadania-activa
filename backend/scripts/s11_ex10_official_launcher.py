#!/usr/bin/env python3
"""Launcher de host de la ejecución OFICIAL de EX-10 (S11-C3, fase 4A). Sólo stdlib.

Separado del launcher del ensayo LOCAL_TEST (`s11_ex10_launcher.py`): aquí el modo es siempre
OFFICIAL y no hay opción para cambiarlo. Cada invocación ejecuta UNA acción y nunca encadena
otra: no hay `start` + `execute`, ni reintentos, ni reanudación de intentos.

Acciones (`--session-root` siempre; la sesión es un registro del operador, no evidencia):

- `initialize-session`: crea la sesión (directorio nuevo 0700) y su registro
  `official-launcher-session.json` ligado al HEAD exacto, la rama y el proyecto. Sin Docker.
- `inspect`: sólo lectura y sólo host: verifica el registro, la cadena de su historial, los
  hashes de cada salida previa, el HEAD y la integridad del launcher, y deriva la próxima
  acción permitida. No lanza nada ni escribe nada.
- `start-official --preflight-report P --nonce N --confirm-official-start
  [--acknowledge-not-demonstrated RUN_ID ...]`: `s11_ex10 start --evidence-mode OFFICIAL`.
  Liga el `run_id` oficial devuelto a la sesión; ninguna acción posterior acepta otro.
- `status`: `s11_ex10 status` del run de la sesión, sólo lectura.
- `execute-official --confirm-emission`: `s11_ex10 execute` (emisión y drain). Una sola vez
  por sesión, pase lo que pase.
- `drain --confirm-drain`: `s11_ex10 drain`, sólo tras un `execute` y con el run en DRAINING
  (recuperación de un drain interrumpido; `execute` ya drena).
- `reconcile --confirm-reconciliation`: `s11_ex10 reconcile`, sólo lectura en el dominio:
  la decisión provisional.
- `finalize --confirm-finalization`: `s11_ex10 finalize`. La decisión la toma el executor y
  debe coincidir con la reconciliada; no hay opción para elegir PASS o FAIL.
- `abandon-official-pre-t0 --confirm-official-abandon-pre-t0`: `s11_ex10 finalize
  --abandon-reason operator-stop`, sólo antes de t0 (PREPARED o STOPPED_PRE_T0).
- `cleanup-stack --confirm-cleanup`: retira sólo IDs/labels aprobados bajo el fence global;
  sin recursos ajenos, volúmenes, imágenes, evidencia ni autoridad. Exige ausencia de actividad
  o incertidumbre y no reintenta una retirada parcial.

El executor sigue siendo la autoridad del lifecycle (fase, custodia, primer run válido,
reconocimientos). La máquina de estados del launcher sólo la complementa: deriva la acción
permitida del historial y del último estado leído, y ante una discrepancia, un resultado
incierto o un historial alterado bloquea hasta la revisión del operador. Nunca repara.

Antes de cada acción: HEAD exacto (el de la sesión), árbol limpio, este archivo y el launcher
base idénticos a sus blobs del HEAD, sesión privada fuera del repositorio y de `var/`, y las
tres claves de interpolación del overlay exportadas por el operador con las huellas de la
custodia oficial vigente (nunca claves sintéticas). Los valores sólo viajan en el entorno de
`docker compose`; nunca por argv, salida ni registro. Las raíces de evidencia son las del
overlay S11 (`var/s11-*`): no hay opción para cambiarlas y el comando no monta ninguna otra.

Códigos de salida: 0 acción coherente, 1 rechazo reintentable y 2 definitivo del executor, 3
uso, configuración o validación previa (nada se lanzó), 4 `LAUNCHER_FAILURE` (salida
incoherente, invariante violado, historial alterado) y 5 `APPARATUS_ABORT` (timeout o
interrupción). Un 4 o un 5 nunca avanza la sesión.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import signal
import stat
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class LauncherError(Exception):
    """Validación previa fallida: no se lanza nada (exit 3). `code` nunca lleva valores."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class LauncherBusy(LauncherError):
    def __init__(self):
        super().__init__("launcher-busy")


class UsageError(LauncherError):
    def __init__(self):
        super().__init__("invalid-arguments")


@dataclass(frozen=True)
class Target:
    """Proyecto Compose sobre el que actúa el launcher. La CLI sólo conoce `PRODUCTION`."""

    project: str
    compose_files: tuple[str, ...]
    env_file: str = "/dev/null"


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: bytes
    stderr: bytes


class Aborted(Exception):
    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(kind)


def _legacy_data_canonical_bytes(value: Any) -> bytes:
    """El mismo JSON canónico que `canonical_json_bytes` del backend."""
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def _legacy_data__stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _legacy_data__inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _legacy_data_secret_hits(text: str, values: Sequence[str] = ()) -> dict[str, int]:
    """Cantidades por patrón, nunca las coincidencias. `values` son los secretos pasados."""
    hits = {name: len(pattern.findall(text)) for name, pattern in _LegacyBaseData.SECRET_PATTERNS}
    hits["exact-secret-value"] = sum(text.count(value) for value in values if len(value) >= 8)
    return {name: count for name, count in hits.items() if count}


def _legacy_data__reject_constant(value: str):
    raise ValueError("NaN e Infinity no son JSON canónico")


class _LegacyParser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError()


class _LegacyBaseData:
    """Local immutable API data; executable sibling code needs an explicit selector."""

    COMPOSE_FILES = ("infra/docker-compose.yml", "infra/docker-compose.s11.yml")

    SERVICE = "backend"

    SOURCE_MOUNT = "/s11-full-source"

    GITCONFIG_MOUNT = "/etc/gitconfig"

    SAFE_DIRECTORY_CONFIG = f"[safe]\n\tdirectory = {SOURCE_MOUNT}\n"

    G0_DECISION_COMMIT = "1693d0ae562bfe777c496b616c9f8c8d5fa8a591"

    G0_A1_DECISION_COMMIT = "db7fe6649213a8d26f4ee60d36a797ee54f80322"

    REQUIRED_FILES = (
        "backend/apps/experiments_app/ex10_preflight.py",
        "backend/apps/experiments_app/ex10_worker_probe.py",
        "backend/apps/experiments_app/tasks.py",
        "backend/apps/experiments_app/management/commands/s11_ex10_preflight.py",
        "backend/apps/experiments_app/tests/test_ex10_preflight.py",
        "backend/apps/experiments_app/tests/test_ex10_worker_probe.py",
        "backend/apps/experiments_app/ex10_prepare.py",
        "backend/apps/experiments_app/management/commands/s11_ex10_prepare.py",
        "backend/scripts/s11_ex10_preflight_launcher.py",
    )

    INTERPOLATION_ENV = (
        "S11_CUSTODY_HMAC_KEY",
        "S11_PUBLICATION_APPROVAL_PUBLIC_KEY",
        "S11_DOCUMENT_HMAC_SECRET",
    )

    DOCKER_CLI_TIMEOUT = 60.0

    _NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

    _FORBIDDEN_RUN_OPTIONS = frozenset(
        {
            "-d",
            "--detach",
            "-e",
            "--env",
            "--env-from-file",
            "-u",
            "--user",
            "--entrypoint",
            "-p",
            "--publish",
            "-P",
            "--service-ports",
            "--use-aliases",
            "--build",
            "--cap-add",
            "-w",
            "--workdir",
            "-l",
            "--label",
            "-i",
            "--interactive",
        }
    )

    _FORBIDDEN_TOKENS = frozenset(
        {"config", "down", "up", "rm", "stop", "kill", "restart", "exec", "build", "prune", "--rmi"}
    )

    _COMPOSE_LINE = re.compile(
        r"^ ?(?:Container|Network|Volume|Image) [A-Za-z0-9][A-Za-z0-9_.-]{0,200} "
        r"(?:Creating|Created|Starting|Started|Stopping|Stopped|Removing|Removed|Waiting|Healthy"
        r"|Running|Recreate|Recreated|Exited)\s*$"
    )

    SECRET_PATTERNS = (
        ("authorization", re.compile(r"(?i)authorization")),
        ("bearer", re.compile(r"(?i)\bbearer\b")),
        ("private-key", re.compile(r"PRIVATE KEY")),
        ("jwt-like", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
        ("api-key-like", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
        ("password-assignment", re.compile(r"(?i)password\s*=")),
        ("secret-assignment", re.compile(r"(?i)secret\s*=")),
        ("redis-credentials", re.compile(r"(?i)rediss?://[^\s/@]*@")),
        ("postgres-credentials", re.compile(r"(?i)postgres(?:ql|is)?://[^\s/@]*@")),
    )

    PREFLIGHT_KEYS = frozenset(
        {
            "schema_version",
            "protocol_version",
            "methodology_version",
            "scenario_id",
            "result",
            "gate",
            "ready_for_execution",
            "run_id",
            "evidence_written",
            "report_posts",
            "provider_calls",
            "task_publishes",
            "health_requests",
            "control_probes",
            "expected_git_sha",
            "git_sha",
            "checked_at",
            "checks",
        }
    )
    LauncherError = LauncherError
    LauncherBusy = LauncherBusy
    UsageError = UsageError
    Target = Target
    Completed = Completed
    Aborted = Aborted
    _Parser = _LegacyParser
    canonical_bytes = staticmethod(_legacy_data_canonical_bytes)
    _stamp = staticmethod(_legacy_data__stamp)
    _inside = staticmethod(_legacy_data__inside)
    secret_hits = staticmethod(_legacy_data_secret_hits)
    _reject_constant = staticmethod(_legacy_data__reject_constant)

    @staticmethod
    def repository_root():
        return Path(__file__).absolute().parents[2]

    @staticmethod
    def _interrupt(number, frame):
        raise KeyboardInterrupt

    def __init__(self):
        self._qualified_module = None
        self._qualified_selector = None
        self._selector_lock = threading.Lock()

    def bind(self, repo_root, expected_git_sha):
        if not self._selector_lock.acquire(blocking=False):
            raise LauncherBusy()
        try:
            selector = (str(repo_root), expected_git_sha)
            if self._qualified_selector not in (None, selector):
                raise LauncherError("source-selector-changed")
            try:
                module = _bootstrap_verified_base(repo_root, expected_git_sha)
            except ValueError as exc:
                allowed = {
                    "launcher-not-from-checkout",
                    "expected-git-sha-format",
                    "source-root-not-git",
                    "checkout-owner-mismatch",
                    "head-mismatch",
                    "repo-root-not-toplevel",
                    "launcher-source-modified",
                    "native-source-integrity",
                    "git-unavailable",
                    "git-command-failed",
                    "git-output-invalid-utf8",
                }
                raise LauncherError(
                    str(exc) if str(exc) in allowed else "native-source-integrity"
                ) from None
            except OSError:
                raise LauncherError("native-source-integrity") from None
            self._qualified_module = module
            self._qualified_selector = selector
            return module

        finally:
            self._selector_lock.release()

    def __getattr__(self, name):
        module = self._qualified_module
        if module is None:
            raise LauncherError("source-selector-required")
        value = getattr(module, name)
        if not callable(value):
            return value
        if name == "exclusive":

            @contextlib.contextmanager
            def exclusive(*args, **kwargs):
                try:
                    with value(*args, **kwargs):
                        yield
                except module.LauncherBusy:
                    raise LauncherBusy() from None
                except module.LauncherError as exc:
                    raise LauncherError(exc.code) from None

            return exclusive

        def call(*args, **kwargs):
            try:
                return value(*args, **kwargs)
            except module.LauncherBusy:
                raise LauncherBusy() from None
            except module.LauncherError as exc:
                raise LauncherError(exc.code) from None
            except module.Aborted as exc:
                raise Aborted(exc.kind) from None

        return call


class DockerRunner:
    """Create the frozen runner only after the invocation's source selection."""

    def __init__(self, cwd, env):
        module = base._qualified_module
        if module is None or base._qualified_selector[0] != str(cwd):
            raise LauncherError("source-selector-required")
        self._module = module
        self._runner = module.DockerRunner(cwd, env)

    def run(self, argv, **kwargs):
        try:
            value = self._runner.run(argv, **kwargs)
        except self._module.Aborted as exc:
            raise Aborted(exc.kind) from None
        return Completed(value.returncode, value.stdout, value.stderr)


base = _LegacyBaseData()
base.DockerRunner = DockerRunner


SCHEMA_VERSION = "s11c3-ex10-official-launcher-v1"
SESSION_SCHEMA = "s11c3-ex10-official-launcher-session-v1"
#: El registro del operador: no es evidencia experimental ni compite con el manifest del run.
SESSION_FILE = "official-launcher-session.json"
PROJECT = "ciudadania-s11-ex10-official"
#: Sólo el overlay S11: sus raíces de evidencia (`var/s11-*`) son las oficiales.
COMPOSE_FILES = base.COMPOSE_FILES
SERVICE = base.SERVICE
SOURCE_MOUNT = base.SOURCE_MOUNT
EVIDENCE_MODE = "OFFICIAL"
OFFICIAL_ROOTS = ("s11-runs", "s11-backup", "s11-restore", "s11-objects")
#: Huellas públicas de la custodia oficial vigente (época activa y clave de publicación).
OFFICIAL_CUSTODY_EPOCH = "587966006e85202a"
OFFICIAL_PUBLICATION_KEY_ID = "8c1e5a8ad7390f4d"
#: Igual que `ex10_executor.PREFLIGHT_MAX_AGE` y su tolerancia al futuro.
PREFLIGHT_MAX_AGE = timedelta(minutes=15)
PREFLIGHT_FUTURE_TOLERANCE = timedelta(0)
ABANDON_REASON = "operator-stop"
LAUNCHER_PATH = "backend/scripts/s11_ex10_official_launcher.py"
BASE_LAUNCHER_PATH = "backend/scripts/s11_ex10_preflight_launcher.py"
REQUIRED_FILES = (
    "backend/apps/experiments_app/ex10_executor.py",
    "backend/apps/experiments_app/ex10_reconcile.py",
    "backend/apps/experiments_app/management/commands/s11_ex10.py",
    LAUNCHER_PATH,
    BASE_LAUNCHER_PATH,
)
MAX_JSON_BYTES = 1024 * 1024

EXIT_OK, EXIT_REFUSED, EXIT_TERMINAL, EXIT_USAGE = 0, 1, 2, 3
EXIT_LAUNCHER_FAILURE, EXIT_APPARATUS_ABORT = 4, 5
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^s11-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_NONCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
_REASON_CODE = re.compile(r"^[A-Za-z0-9_.:@=+,/-]{1,160}$")

LauncherError = base.LauncherError
LauncherBusy = base.LauncherBusy
UsageError = base.UsageError
Completed = base.Completed
Aborted = base.Aborted
DockerRunner = base.DockerRunner
Target = base.Target


class SessionTampered(LauncherError):
    """El registro o una salida previa no es la custodiada: se bloquea, nunca se regenera."""

    def __init__(self, code: str = "SESSION_HISTORY_TAMPERED"):
        super().__init__(code)


@dataclass(frozen=True)
class ActionSpec:
    subcommand: str | None
    confirmation: str | None
    timeout: float
    #: backend y worker arriba: START corre el preflight y `execute` emite contra la API.
    requires_web: bool
    mutating: bool


ACTIONS: Mapping[str, ActionSpec] = {
    "initialize-session": ActionSpec(None, None, 0.0, False, True),
    "inspect": ActionSpec(None, None, 0.0, False, False),
    "start-official": ActionSpec("start", "--confirm-official-start", 600.0, True, True),
    "status": ActionSpec("status", None, 120.0, False, False),
    "execute-official": ActionSpec("execute", "--confirm-emission", 1800.0, True, True),
    "drain": ActionSpec("drain", "--confirm-drain", 900.0, True, True),
    "reconcile": ActionSpec("reconcile", "--confirm-reconciliation", 600.0, False, True),
    "finalize": ActionSpec("finalize", "--confirm-finalization", 900.0, False, True),
    "abandon-official-pre-t0": ActionSpec(
        "finalize", "--confirm-official-abandon-pre-t0", 300.0, False, True
    ),
    "declare-executor-gone": ActionSpec(
        "finalize", "--confirm-declare-executor-gone", 900.0, False, True
    ),
    "cleanup-stack": ActionSpec(None, "--confirm-cleanup", 300.0, False, True),
}
#: Fases en las que el launcher declara caído al executor (el dominio también admite DRAINING,
#: que aquí se recupera con `drain`): un subconjunto, nunca una ampliación.
EXECUTOR_GONE_PHASES = frozenset({"STARTED", "EMITTING"})
#: Estados de contenedor en los que el executor puede seguir vivo.
ACTIVE_CONTAINER_STATES = frozenset({"running", "created", "restarting", "paused"})
CONTAINER_ACTIONS = frozenset(a for a, s in ACTIONS.items() if s.subcommand is not None)
#: Palabras que nunca aparecen en el comando del contenedor.
_FORBIDDEN_WORDS = (
    "local_test",
    "local-test",
    "rehearsal",
    "retry",
    "resume",
    "provider",
    "request-count",
    "force",
)
TERMINAL_PHASES = frozenset(
    {"ABANDONED_PRE_T0", "NOT_DEMONSTRATED", "FINALIZED_PASS", "FINALIZED_FAIL"}
)
_PHASES = frozenset(
    {
        "CREATED",
        "PREPARED",
        "STARTED",
        "EMITTING",
        "STOPPED_PRE_T0",
        "DRAINING",
        "RECONCILING",
        "DECIDED",
        *TERMINAL_PHASES,
    }
)
_DECISIONS = frozenset({"PASS", "FAIL", "NOT_DEMONSTRATED"})
#: `status` es estrictamente de sólo lectura en el dominio: la evidencia entera queda idéntica.
#: `inspect` ni siquiera lanza un contenedor (sólo host). Desde `856c899`, `reconcile` también es
#: estrictamente de sólo lectura sobre la evidencia (`load_existing_state_readonly` bajo el lock
#: compartido, sin `load_state` ni normalización de permisos: ni contenido ni ctime cambian),
#: pero sigue en la cadena de acciones porque registra la decisión provisional que `finalize`
#: debe reproducir. Toda acción, también éstas, añade su salida al registro de la sesión del
#: operador, que no es evidencia.
READ_ONLY_ACTIONS = frozenset({"status"})


# --- Integridad del checkout y del propio launcher -------------------------------------------


# Verified source selection and deferred native apparatus imports.
_NATIVE_MODULE_CACHE = {}


def _capture_native_source(path, *, error_type=ValueError):
    """Bounded nonblocking read beneath retained, non-symlink directory FDs."""
    descriptors = []
    directory_links = []
    descriptor = None

    def signature(info):
        # Active roots gain children during legitimate evidence writes. Their
        # lease binds actual OS identity/permissions, not a child-link counter.
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)

    def file_signature(info):
        return signature(info) + (
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    try:
        parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(parent)
        for name in path.parts[1:-1]:
            child = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
            descriptors.append(child)
            observed = signature(os.fstat(child))
            directory_links.append((parent, name, child, observed))
            parent = child
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= 16 * 1024 * 1024
        ):
            raise error_type("native-source-integrity")
        raw = bytearray()
        while len(raw) <= before.st_size:
            part = os.read(descriptor, min(65536, before.st_size + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
        after = os.fstat(descriptor)
        named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (
            file_signature(before) != file_signature(after)
            or file_signature(after) != file_signature(named)
            or len(raw) != before.st_size
        ):
            raise error_type("native-source-integrity")
        for previous, name, child, expected in directory_links:
            if (
                signature(os.fstat(child)) != expected
                or signature(os.stat(name, dir_fd=previous, follow_symlinks=False)) != expected
            ):
                raise error_type("native-source-integrity")
        return bytes(raw), (tuple(link[3] for link in directory_links), file_signature(after))
    except OSError:
        raise error_type("native-source-integrity") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _fixed_source_git_result(repo_root, *arguments, ok=(0,)):
    """Read the actual selected Git objects, independently of replace refs."""
    import subprocess

    repo = Path(repo_root)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_CEILING_DIRECTORIES": str(repo.parent),
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        result = subprocess.run(
            (
                "/usr/bin/git",
                "--no-optional-locks",
                "--no-replace-objects",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "-c",
                "core.excludesFile=/dev/null",
                "-C",
                str(repo),
                *arguments,
            ),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError("git-unavailable") from None
    if result.returncode not in ok or len(result.stdout) > 1024 * 1024:
        raise ValueError("git-command-failed")
    try:
        stdout = result.stdout.decode("utf-8", "strict")
    except UnicodeError:
        raise ValueError("git-output-invalid-utf8") from None
    return result.returncode, stdout


def _fixed_source_git(repo_root, *arguments):
    return _fixed_source_git_result(repo_root, *arguments)[1].strip()


class _FixedSourceGit:
    def __init__(self, root):
        self.root = root

    def __call__(self, *arguments, ok=(0,)):
        try:
            return _fixed_source_git_result(self.root, *arguments, ok=ok)
        except ValueError as error:
            code = str(error)
            if code not in {"git-unavailable", "git-command-failed", "git-output-invalid-utf8"}:
                code = "git-command-failed"
            raise LauncherError(code) from None


def _fixed_verify_repository(repo_root, expected_git_sha):
    """Run the verified frozen qualifier with a private fixed Git binding.

    Do not mutate W07 source or its loaded module globals. Preserve the frozen
    qualifier code and every existing check; only its own Git primitive is
    selected internally, with an absolute executable and replacement-disabled
    objects. No caller/global module supplies the code or this binding.
    """
    import types

    module = _bootstrap_verified_base(repo_root, expected_git_sha)
    try:
        original = module.verify_repository
        scope = dict(original.__globals__)
        scope["HostGit"] = _FixedSourceGit
        qualified = types.FunctionType(
            original.__code__,
            scope,
            original.__name__,
            original.__defaults__,
            original.__closure__,
        )
        return qualified(Path(repo_root), expected_git_sha)
    except module.LauncherError as error:
        raise LauncherError(error.code) from None
    finally:
        # Only this fresh qualifier module is ephemeral. Registered modules
        # retained by base.bind/DockerRunner remain owned by those lifecycles.
        if sys.modules.get(module.__name__) is module:
            sys.modules.pop(module.__name__)


def _bootstrap_verified_base(repo_root, expected_git_sha):
    """Capture and qualify stdlib launcher sources before executing the base.

    The invocation still performs all existing expected-HEAD, clean checkout,
    branch, authorization and source integrity checks before action effects.
    Development and mutation positives require a real clean disposable snapshot.
    """
    import types

    source = Path(__file__)
    if not source.is_absolute() or len(source.parents) < 3:
        raise ValueError("launcher-not-from-checkout")
    repo = source.parents[2]
    if repo != Path(repo_root) or not re.fullmatch(r"[0-9a-f]{40}", expected_git_sha):
        raise ValueError("expected-git-sha-format")
    if source != repo / "backend/scripts/s11_ex10_official_launcher.py":
        raise ValueError("launcher-not-from-checkout")
    # No base method is invoked to establish the trust used to import base.
    gitdir = repo / ".git"
    observed = gitdir.lstat()
    if not stat.S_ISDIR(observed.st_mode) or observed.st_uid != os.getuid():
        raise ValueError("source-root-not-git")
    if repo.stat().st_uid != os.getuid():
        raise ValueError("checkout-owner-mismatch")

    def git(*arguments):
        return _fixed_source_git(repo, *arguments)

    head = git("rev-parse", "HEAD")
    if head != expected_git_sha:
        raise ValueError("head-mismatch")
    if git("rev-parse", "--show-toplevel") != str(repo):
        raise ValueError("repo-root-not-toplevel")
    captured = {}
    leases = {}
    for relative in (
        "backend/scripts/s11_ex10_official_launcher.py",
        "backend/scripts/s11_ex10_preflight_launcher.py",
    ):
        raw, lease = _capture_native_source(repo / relative, error_type=ValueError)
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if git("rev-parse", expected_git_sha + ":" + relative) != blob:
            raise ValueError("launcher-source-modified")
        captured[relative], leases[relative] = raw, lease
    if git("rev-parse", "HEAD") != head:
        raise ValueError("launcher-source-modified")
    # A fresh private name never adopts a caller-supplied global module object.
    name = "_s11_base_" + hashlib.sha256((str(repo) + "\0" + head).encode()).hexdigest()
    name += "_" + secrets.token_hex(16)
    if name in sys.modules:
        raise ValueError("launcher-source-modified")
    relative = "backend/scripts/s11_ex10_preflight_launcher.py"
    module = types.ModuleType(name)
    module.__file__ = str(repo / relative)
    module.__package__ = ""
    sys.modules[name] = module
    try:
        exec(compile(captured[relative], module.__file__, "exec"), module.__dict__)
        for relative, raw in captured.items():
            after, lease = _capture_native_source(repo / relative, error_type=ValueError)
            if after != raw or lease != leases[relative]:
                raise ValueError("launcher-source-modified")
        if git("rev-parse", "HEAD") != head:
            raise ValueError("launcher-source-modified")
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _load_native_apparatus(repo_root, expected_git_sha, *, _retain_source_leases=False):
    """Import only tracked, unchanged apparatus from the verified source checkout.

    Relative package imports use a private namespace with an explicit package
    path. Existing modules must originate from those same verified files. No
    provider is discovered through cwd, PYTHONPATH, environment or a request.
    This loader does not initialize Django or import the executor.
    """
    import ast
    import importlib.abc
    import importlib.util
    import types

    repo_root = Path(repo_root)
    _fixed_verify_repository(repo_root, expected_git_sha)
    verify_launcher_integrity(repo_root, expected_git_sha)

    def git(*arguments):
        return (0, _fixed_source_git(repo_root, *arguments), "")

    directory = repo_root / "backend/apps/experiments_app"
    required = {
        "custody_policy",
        "manifest",
        "ex10_runtime_profile",
        "ex10_session_v2",
        "ex10_history_authority",
        "ex10_governance_certificate",
        "ex10_authority_sqlite",
        "ex10_authority_witness",
        "ex10_authority_backend",
        "ex10_bridge_protocol",
        "ex10_open_evidence",
        "ex10_start_coordination",
        "ex10_deployment_envelope",
        "ex10_operational_context",
        "ex10_prior_custody",
    }
    verified = {}
    leases = {}

    def verify(name):
        if name in verified:
            return
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise LauncherError("native-source-integrity")
        path = directory / (name + ".py")
        relative = str(path.relative_to(repo_root))
        raw, lease = _capture_native_source(path, error_type=LauncherError)
        code, out = git("rev-parse", expected_git_sha + ":" + relative)[:2]
        if code != 0 or not _SHA1.fullmatch(out.strip()) or git_blob_sha(raw) != out.strip():
            raise LauncherError("native-source-integrity")
        verified[name] = raw
        leases[name] = lease
        tree = ast.parse(raw)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.level:
                continue
            if node.level != 1:
                raise LauncherError("native-source-integrity")
            if node.module:
                verify(node.module.split(".")[0])
            else:
                for alias in node.names:
                    verify(alias.name)

    for name in sorted(required):
        verify(name)
    # Verify every transitive local file before importing any of them.
    namespace = (
        "_s11_native_"
        + hashlib.sha256((str(repo_root) + "\0" + expected_git_sha).encode()).hexdigest()
    )
    cached = _NATIVE_MODULE_CACHE.get(namespace)
    existing_names = {name for name in sys.modules if name.startswith(namespace + ".")}
    if cached is not None:
        package, cached_modules, cached_hashes = cached
        if (
            sys.modules.get(namespace) is not package
            or list(getattr(package, "__path__", ())) != [str(directory)]
            or cached_hashes
            != {name: hashlib.sha256(raw).hexdigest() for name, raw in verified.items()}
            or existing_names != {namespace + "." + name for name in cached_modules}
        ):
            raise LauncherError("native-source-integrity")
        if any(
            sys.modules.get(namespace + "." + name) is not module
            or getattr(package, name, None) is not module
            for name, module in cached_modules.items()
        ):
            raise LauncherError("native-source-integrity")
    else:
        if namespace in sys.modules or existing_names:
            raise LauncherError("native-source-integrity")
        package = types.ModuleType(namespace)
        package.__path__ = [str(directory)]
        sys.modules[namespace] = package
    for name in sorted(verified):
        qualified = namespace + "." + name
        if qualified in sys.modules:
            existing = sys.modules[qualified]
            if (
                getattr(existing, "__file__", None) != str(directory / (name + ".py"))
                or getattr(existing, "_s11_verified_source_sha256", None)
                != hashlib.sha256(verified[name]).hexdigest()
            ):
                raise LauncherError("native-source-integrity")
    from importlib import import_module

    class VerifiedBytesLoader(importlib.abc.Loader):
        def __init__(self, name):
            self.name = name

        def create_module(self, spec):
            return None

        def exec_module(self, module):
            path = directory / (self.name + ".py")
            module.__file__ = str(path)
            module._s11_verified_source_sha256 = hashlib.sha256(verified[self.name]).hexdigest()
            # Compile the exact bytes checked above, with no second source read
            # and no .pyc/cache writes, including transitive relative imports.
            exec(compile(verified[self.name], str(path), "exec"), module.__dict__)

    class VerifiedBytesFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if not fullname.startswith(namespace + "."):
                return None
            name = fullname[len(namespace) + 1 :]
            if name not in verified:
                raise LauncherError("native-source-integrity")
            return importlib.util.spec_from_loader(
                fullname, VerifiedBytesLoader(name), origin=str(directory / (name + ".py"))
            )

    finder = VerifiedBytesFinder()
    sys.meta_path.insert(0, finder)
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        loaded = {name: import_module("." + name, namespace) for name in sorted(verified)}
        modules = {name: loaded[name] for name in required}
    finally:
        sys.dont_write_bytecode = previous
        sys.meta_path.remove(finder)
    # Import is not an opportunity to replace a previously qualified source.
    for name, raw in verified.items():
        path = directory / (name + ".py")
        after, lease = _capture_native_source(path, error_type=LauncherError)
        if after != raw or lease != leases[name]:
            raise LauncherError("native-source-integrity")
    if any(name.startswith(namespace + ".ex10_executor") for name in sys.modules):
        raise LauncherError("native-host-imports-executor")
    _NATIVE_MODULE_CACHE[namespace] = (
        package,
        loaded,
        {name: hashlib.sha256(raw).hexdigest() for name, raw in verified.items()},
    )
    if _retain_source_leases:
        return modules, tuple(
            (directory / (name + ".py"), verified[name], leases[name]) for name in sorted(verified)
        )
    return modules


def git_blob_sha(content: bytes) -> str:
    """El id de blob de Git de unos bytes (sha1 de `blob <n>\\0` + bytes)."""
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()  # noqa: S324


def verify_launcher_integrity(repo_root: Path, expected_git_sha: str) -> dict[str, str]:
    """Este archivo y el launcher base son exactamente sus blobs del HEAD esperado."""
    git = _FixedSourceGit(repo_root)
    facts = {}
    for relative in (LAUNCHER_PATH, BASE_LAUNCHER_PATH):
        path = repo_root / relative
        if path.is_symlink() or not path.is_file():
            raise LauncherError("launcher-source-missing")
        code, out = git("rev-parse", f"{expected_git_sha}:{relative}")[:2]
        expected = out.strip()
        if code != 0 or not _SHA1.fullmatch(expected):
            raise LauncherError("launcher-source-untracked")
        if git_blob_sha(path.read_bytes()) != expected:
            raise LauncherError("launcher-source-modified")
        facts[relative] = expected
    running = os.path.realpath(__file__)
    if running != str(repo_root / LAUNCHER_PATH):
        raise LauncherError("launcher-not-from-checkout")
    return facts


def official_roots(repo_root: Path) -> tuple[Path, ...]:
    return tuple(repo_root / "var" / name for name in OFFICIAL_ROOTS)


def verify_official_roots(repo_root: Path) -> None:
    """Las raíces del overlay existen como directorios reales del checkout (nunca symlinks)."""
    for path in official_roots(repo_root):
        info = os.lstat(path) if os.path.lexists(path) else None
        if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise LauncherError("official-root-unsafe")


def official_key_problems(environ: Mapping[str, str]) -> list[str]:
    """Las claves exportadas son las de la custodia oficial (sólo huellas, nunca valores)."""
    problems = []
    custody = environ.get("S11_CUSTODY_HMAC_KEY", "")
    if len(custody.encode()) < 32 or (
        hashlib.sha256(custody.encode()).hexdigest()[:16] != OFFICIAL_CUSTODY_EPOCH
    ):
        problems.append("official-custody-key")
    try:
        public = bytes.fromhex(environ.get("S11_PUBLICATION_APPROVAL_PUBLIC_KEY", ""))
    except ValueError:
        public = b""
    if len(public) != 32 or hashlib.sha256(public).hexdigest()[:16] != OFFICIAL_PUBLICATION_KEY_ID:
        problems.append("official-publication-key")
    if not environ.get("S11_DOCUMENT_HMAC_SECRET", "").strip():
        problems.append("document-hmac-secret")
    return problems


def child_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """Docker, el uid/gid y las tres claves oficiales. Nada más del host."""
    problems = official_key_problems(environ)
    if problems:
        raise LauncherError("official-keys:" + ",".join(problems))
    return base.child_environment(environ)


# --- Sesión del operador ---------------------------------------------------------------------


def _private_dir(path: Path, code: str) -> None:
    info = os.lstat(path) if os.path.lexists(path) else None
    if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise LauncherError(code)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise LauncherError(code)


def _private_file(path: Path, code: str, limit: int = MAX_JSON_BYTES) -> bytes:
    info = os.lstat(path) if os.path.lexists(path) else None
    if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise LauncherError(code)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > limit:
        raise LauncherError(code)
    return path.read_bytes()


def check_session_location(root: Path, repo_root: Path) -> None:
    """Fuera del repositorio y de las raíces oficiales, realpath, bajo un padre privado."""
    text = str(root)
    if not root.is_absolute() or os.path.realpath(os.path.dirname(text)) != os.path.dirname(text):
        raise LauncherError("session-root-not-realpath")
    if base._inside(text, str(repo_root)) or base._inside(str(repo_root), text):
        raise LauncherError("session-root-inside-repository")
    if any(
        base._inside(text, str(o)) or base._inside(str(o), text) for o in official_roots(repo_root)
    ):
        raise LauncherError("session-root-overlaps-official")
    parent = os.stat(root.parent)
    if parent.st_uid != os.getuid() or parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise LauncherError("session-parent-unsafe")


#: Campos fijos del registro: su digest siembra la cadena del historial.
HEADER_KEYS = (
    "schema_version",
    "kind",
    "session_id",
    "created_at",
    "expected_git_sha",
    "branch",
    "repo_root",
    "project",
    "evidence_mode",
    "launcher_blobs",
)


def _header_digest(manifest: Mapping[str, Any]) -> str:
    header = {key: manifest.get(key) for key in HEADER_KEYS}
    return hashlib.sha256(base.canonical_bytes(header)).hexdigest()


def _chain(previous: str, entry: Mapping[str, Any]) -> str:
    body = {k: v for k, v in entry.items() if k != "chain"}
    return hashlib.sha256(previous.encode() + base.canonical_bytes(body)).hexdigest()


def _output_hashes(output: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(output.iterdir())
        if p.is_file() and not p.is_symlink()
    }


def write_session(root: Path, manifest: Mapping[str, Any], *, create: bool = False) -> None:
    """Escritura canónica: creación exclusiva, o reemplazo atómico (temporal + fsync)."""
    content = base.canonical_bytes(dict(manifest))
    if create:
        base.write_file(root, SESSION_FILE, content)
        return
    temporary = f".{SESSION_FILE}.{secrets.token_hex(6)}.tmp"
    base.write_file(root, temporary, content)
    fd = os.open(root / temporary, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(root / temporary, root / SESSION_FILE)
    dir_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def load_session(root: Path, repo_root: Path) -> dict[str, Any]:
    """El registro de la sesión, con su cadena y cada salida previa verificadas."""
    check_session_location(root, repo_root)
    _private_dir(root, "session-root-unsafe")
    raw = _private_file(root / SESSION_FILE, "session-file-unsafe")
    try:
        manifest = json.loads(raw.decode("utf-8"), parse_constant=base._reject_constant)
    except (UnicodeDecodeError, ValueError):
        raise SessionTampered("SESSION_FILE_MALFORMED") from None
    if not isinstance(manifest, dict) or base.canonical_bytes(manifest) != raw:
        raise SessionTampered("SESSION_FILE_MALFORMED")
    if manifest.get("schema_version") != SESSION_SCHEMA or manifest.get("project") != PROJECT:
        raise SessionTampered("SESSION_FILE_MALFORMED")
    if manifest.get("repo_root") != str(repo_root):
        raise LauncherError("session-other-repository")
    if manifest.get("header_sha256") != _header_digest(manifest):
        raise SessionTampered()
    previous = manifest["header_sha256"]
    for entry in manifest.get("history", []):
        if not isinstance(entry, dict) or entry.get("chain") != _chain(previous, entry):
            raise SessionTampered()
        previous = entry["chain"]
        output = entry.get("output_dir")
        if output is not None:
            path = root / output
            if path.parent != root / "actions" or not path.is_dir() or path.is_symlink():
                raise SessionTampered()
            if _output_hashes(path) != entry.get("output_sha256"):
                raise SessionTampered()
    for record in manifest.get("in_flight", []):
        if (
            not isinstance(record, dict)
            or set(record) != {"action", "container_name", "output_dir", "started_at", "after_seq"}
            or record["action"] not in ACTIONS
            or not ACTIONS[record["action"]].mutating
            or not isinstance(record["after_seq"], int)
            or not 0 <= record["after_seq"] <= len(manifest.get("history", []))
        ):
            raise SessionTampered()
    # El run y el preflight ligados son los del START coherente del historial, nunca otros.
    starts = _coherent(manifest, "start-official")
    bound = starts[-1]["run_id"] if starts else None
    attached = starts[-1]["output_sha256"].get("preflight.json") if starts else None
    if manifest.get("official_run_id") != bound or (
        manifest.get("preflight_report_sha256") != attached
    ):
        raise SessionTampered()
    if manifest.get("state") != derive_state(manifest):
        raise SessionTampered()
    return manifest


def new_session_manifest(
    *, session_id: str, created_at: str, git_sha: str, branch: str, repo_root: Path, integrity
) -> dict[str, Any]:
    manifest = {
        "schema_version": SESSION_SCHEMA,
        "kind": "operator-session-record-not-experimental-evidence",
        "session_id": session_id,
        "created_at": created_at,
        "expected_git_sha": git_sha,
        "branch": branch,
        "repo_root": str(repo_root),
        "project": PROJECT,
        "evidence_mode": EVIDENCE_MODE,
        "launcher_blobs": dict(integrity),
        "preflight_report_sha256": None,
        "official_run_id": None,
        "history": [],
    }
    manifest["header_sha256"] = _header_digest(manifest)
    manifest["state"] = derive_state(manifest)
    return manifest


# --- Máquina de estados del launcher ---------------------------------------------------------


def _coherent(manifest: Mapping[str, Any], action: str) -> list[dict[str, Any]]:
    return [
        e
        for e in manifest.get("history", [])
        if e["action"] == action and e["classification"] == "COHERENT"
    ]


def derive_state(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """La fase conocida, si la sesión está bloqueada y las acciones permitidas.

    Complementa al executor: la fase sale de la última salida coherente que la informa
    (START, `execute`, `drain`, `finalize`, `status`). Una acción mutante incierta (fallo del
    launcher o abort) deja sólo `status` hasta que una lectura coherente fije la fase. Una
    discrepancia entre sesión y run o un historial alterado bloquea para siempre.
    """
    history = list(manifest.get("history", []))
    run_id = manifest.get("official_run_id")
    phase, uncertain, blocked = None, False, False
    in_flight = list(manifest.get("in_flight", []))
    pending = {record["after_seq"] for record in in_flight}
    for position, entry in enumerate(history):
        if position in pending:
            # Un launcher que murió con la acción en curso: su resultado es incierto.
            uncertain = True
        if entry.get("session_blocking"):
            blocked = True
        reported = entry.get("phase")
        if entry["classification"] == "COHERENT" and reported is not None:
            phase, uncertain = reported, False
        elif ACTIONS[entry["action"]].mutating and entry["classification"] in {
            "LAUNCHER_FAILURE",
            "APPARATUS_ABORT",
        }:
            uncertain = True
    if any(record["after_seq"] >= len(history) for record in in_flight):
        uncertain = True
    invoked = {e["action"] for e in history if e["classification"] != "VALIDATION_FAILED"}
    invoked |= {record["action"] for record in in_flight}
    allowed: list[str] = ["inspect"]
    if blocked:
        pass
    elif run_id is None:
        # Un START incierto pudo crear un run que la sesión no conoce: revisión manual.
        if not uncertain:
            allowed += ["start-official", "cleanup-stack"]
    elif uncertain:
        allowed.append("status")
    else:
        allowed.append("status")
        if phase == "PREPARED":
            if "execute-official" not in invoked:
                allowed.append("execute-official")
            allowed.append("abandon-official-pre-t0")
        elif phase == "STOPPED_PRE_T0":
            allowed.append("abandon-official-pre-t0")
        elif phase == "DRAINING" and "execute-official" in invoked and "drain" not in invoked:
            allowed.append("drain")
        elif phase == "RECONCILING" and not _coherent(manifest, "reconcile"):
            allowed.append("reconcile")
        elif phase in {"RECONCILING", "DECIDED"} and _coherent(manifest, "reconcile"):
            allowed.append("finalize")
        elif phase in TERMINAL_PHASES:
            allowed.append("cleanup-stack")
        # Regla propia, fuera de la cadena: sólo sus fases, tras un `execute` y una sola vez.
        if (
            phase in EXECUTOR_GONE_PHASES
            and "execute-official" in invoked
            and "declare-executor-gone" not in invoked
        ):
            allowed.append("declare-executor-gone")
    return {
        "known_phase": phase,
        "uncertain": uncertain,
        "blocked": blocked,
        "next_allowed_actions": sorted(set(allowed)),
    }


# --- Comando del contenedor ------------------------------------------------------------------


def container_command(
    action: str,
    *,
    expected_git_sha: str,
    run_id: str | None,
    nonce: str | None = None,
    acknowledged: Sequence[str] = (),
) -> list[str]:
    head = ["python", "manage.py", "s11_ex10"]
    if action == "start-official":
        command = [*head, "start", "--expected-git-sha", expected_git_sha, "--source-root"]
        command += [SOURCE_MOUNT, "--nonce", nonce, "--evidence-mode", EVIDENCE_MODE]
        command.append("--confirm-new-run")
        for run in acknowledged:
            command += ["--acknowledge-not-demonstrated", run]
        return command
    if action == "status":
        return [*head, "status", "--run-id", run_id]
    step = ACTIONS[action].subcommand
    command = [*head, step, "--run-id", run_id, "--expected-git-sha", expected_git_sha]
    if action == "execute-official":
        command.append("--confirm-emission")
    if action == "abandon-official-pre-t0":
        command += ["--abandon-reason", ABANDON_REASON]
    if action == "declare-executor-gone":
        command.append("--declare-executor-gone")
    return command


def build_command(
    action: str,
    *,
    environ_secrets: Sequence[str],
    expected_git_sha: str,
    container_name: str,
    repo_root: Path,
    run_id: str | None,
    nonce: str | None = None,
    acknowledged: Sequence[str] = (),
    gitconfig: Path | None = None,
    masks: tuple[str, ...] = (),
) -> list[str]:
    argv = ["docker", "compose", "-p", PROJECT, "--env-file", "/dev/null"]
    for compose_file in COMPOSE_FILES:
        argv += ["-f", compose_file]
    argv += ["run", "--rm", "--no-deps", "-T", "--pull", "never", "--name", container_name]
    argv += ["-v", f"{repo_root}:{SOURCE_MOUNT}:ro"]
    for relative in masks:
        argv += ["-v", f"/dev/null:{SOURCE_MOUNT}/{relative}:ro"]
    if gitconfig is not None:
        argv += ["-v", f"{gitconfig}:{base.GITCONFIG_MOUNT}:ro"]
    argv += [
        SERVICE,
        *container_command(
            action,
            expected_git_sha=expected_git_sha,
            run_id=run_id,
            nonce=nonce,
            acknowledged=acknowledged,
        ),
    ]
    assert_safe_command(
        argv, action=action, repo_root=repo_root, secrets=environ_secrets, masks=masks
    )
    return argv


def assert_safe_command(
    argv: Sequence[str],
    *,
    action: str,
    repo_root: Path,
    secrets: Sequence[str],
    masks: tuple[str, ...] = (),
) -> None:
    """Invariantes del único comando que se lanza; una violación es un error del launcher."""
    argv = list(argv)
    try:
        run = argv.index("run")
        service = argv.index(SERVICE, run)
    except ValueError:
        raise LauncherError("unsafe-command:shape") from None
    problems = []
    if argv[:6] != ["docker", "compose", "-p", PROJECT, "--env-file", "/dev/null"]:
        problems.append("project")
    files = [argv[i + 1] for i, token in enumerate(argv[:run]) if token == "-f"]
    if tuple(files) != tuple(COMPOSE_FILES):
        problems.append("compose-files")
    if set(argv) & base._FORBIDDEN_TOKENS:
        problems.append("not-compose-run")
    options = argv[run + 1 : service]
    mounts = [options[i + 1] for i, token in enumerate(options[:-1]) if token == "-v"]
    if mounts[:1] != [f"{repo_root}:{SOURCE_MOUNT}:ro"]:
        problems.append("checkout-mount")
    allowed_masks = {f"/dev/null:{SOURCE_MOUNT}/{relative}:ro" for relative in masks}
    gitconfigs = 0
    for mount in mounts[1:]:
        if mount in allowed_masks:
            continue
        source, _, rest = mount.partition(":")
        gitconfigs += 1
        if rest != f"{base.GITCONFIG_MOUNT}:ro" or base._inside(
            os.path.realpath(source), str(repo_root)
        ):
            problems.append("extra-mount")
    if gitconfigs > 1:
        problems.append("extra-mount")
    if any(not mount.endswith(":ro") for mount in mounts):
        problems.append("rw-mount")
    sources = [mount.partition(":")[0] for mount in mounts]
    if any(base._inside(s, str(o)) for s in sources for o in official_roots(repo_root)):
        problems.append("official-root-mount")
    required = {"--rm", "--no-deps", "-T", "--pull"}
    pull = options[options.index("--pull") + 1] if "--pull" in options[:-1] else None
    if (
        set(options) & base._FORBIDDEN_RUN_OPTIONS
        or not required <= set(options)
        or pull != "never"
    ):
        problems.append("run-options")
    tail = argv[service + 1 :]
    if not _tail_ok(action, tail):
        problems.append("container-command")
    if any(word in token.lower() for token in tail for word in _FORBIDDEN_WORDS):
        problems.append("container-command")
    if any(value and value in token for token in argv for value in secrets):
        problems.append("secret-in-argv")
    if problems:
        raise LauncherError("unsafe-command:" + ",".join(sorted(set(problems))))


def _tail_ok(action: str, tail: Sequence[str]) -> bool:
    """La forma exacta del comando del contenedor para cada acción; nada más."""
    spec = ACTIONS.get(action)
    if spec is None or spec.subcommand is None:
        return False
    if list(tail[:4]) != ["python", "manage.py", "s11_ex10", spec.subcommand]:
        return False
    rest = list(tail[4:])
    if action == "start-official":
        fixed = len(rest) >= 9 and (
            rest[0] == "--expected-git-sha"
            and bool(_SHA1.fullmatch(rest[1]))
            and rest[2:5] == ["--source-root", SOURCE_MOUNT, "--nonce"]
            and bool(_NONCE.fullmatch(rest[5]))
            and rest[6:9] == ["--evidence-mode", EVIDENCE_MODE, "--confirm-new-run"]
        )
        acks = rest[9:]
        return (
            fixed
            and len(acks) % 2 == 0
            and all(flag == "--acknowledge-not-demonstrated" for flag in acks[::2])
            and all(_RUN_ID.fullmatch(run) for run in acks[1::2])
        )
    if action == "status":
        return len(rest) == 2 and rest[0] == "--run-id" and bool(_RUN_ID.fullmatch(rest[1]))
    common = (
        len(rest) >= 4
        and rest[0] == "--run-id"
        and bool(_RUN_ID.fullmatch(rest[1]))
        and rest[2] == "--expected-git-sha"
        and bool(_SHA1.fullmatch(rest[3]))
    )
    extra = {
        "execute-official": ["--confirm-emission"],
        "abandon-official-pre-t0": ["--abandon-reason", ABANDON_REASON],
        "declare-executor-gone": ["--declare-executor-gone"],
    }.get(action, [])
    return common and rest[4:] == extra


# --- Informe de preflight --------------------------------------------------------------------


_PREFLIGHT_EXPECTED = {
    "schema_version": "s11c3-ex10-preflight-v1",
    "scenario_id": "EX-10",
    "protocol_version": "s11-ex10-v1",
    "methodology_version": "S11A-v1.1",
    "result": "PASS",
    "gate": "READY_FOR_EXECUTOR_INTEGRATION",
    "ready_for_execution": False,
    "run_id": None,
    "evidence_written": False,
    "report_posts": 0,
    "provider_calls": 0,
    "task_publishes": 0,
    "health_requests": 1,
    "control_probes": 2,
}


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def preflight_report_problems(raw: bytes, *, git_sha: str, now: datetime) -> list[str]:
    """El informe del preflight operacional: PASS completo del mismo HEAD y reciente."""
    try:
        report = json.loads(raw.decode("utf-8"), parse_constant=base._reject_constant)
    except (UnicodeDecodeError, ValueError):
        return ["preflight:malformed"]
    if not isinstance(report, dict) or set(report) != base.PREFLIGHT_KEYS:
        return ["preflight:malformed"]
    problems = []
    expected = {**_PREFLIGHT_EXPECTED, "git_sha": git_sha, "expected_git_sha": git_sha}
    for key, value in expected.items():
        if report.get(key) != value or type(report.get(key)) is not type(value):
            problems.append(f"preflight:{key}")
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        problems.append("preflight:checks")
    elif any(not isinstance(c, dict) or c.get("result") != "PASS" for c in checks):
        problems.append("preflight:check-not-pass")
    checked = _parse_utc(report.get("checked_at"))
    if checked is None:
        problems.append("preflight:checked-at")
    elif checked - now > PREFLIGHT_FUTURE_TOLERANCE:
        problems.append("preflight:checked-at-future")
    elif now - checked > PREFLIGHT_MAX_AGE:
        problems.append("preflight:stale")
    return problems


# --- Salida del command ----------------------------------------------------------------------


_CODE_LINE = re.compile(
    r"^(?:CommandError: )?S11_EX10_(?:REFUSED|USAGE|ERROR|INTERNAL|INTERRUPTED)"
    r"(?:: [A-Za-z0-9_.:-]{1,80}(?: [a-z]{1,10})?)?$"
)


def _refusal_line(line: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(line)
    except ValueError:
        return None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"refused", "terminal", "reasons"}
        or payload["refused"] is not True
        or type(payload["terminal"]) is not bool
        or not isinstance(payload["reasons"], list)
        or not all(isinstance(r, str) and _REASON_CODE.fullmatch(r) for r in payload["reasons"])
    ):
        return None
    return payload


def sanitize_stderr(raw: bytes, secret_values: Sequence[str]) -> tuple[str, dict[str, int], list]:
    """Sólo progreso de Compose, códigos del command y su JSON de rechazo (sólo códigos)."""
    kept, redacted, secret_lines, refusals = [], 0, 0, []
    lines = [line for line in raw.decode("utf-8", "replace").splitlines() if line.strip()]
    for line in lines:
        refusal = _refusal_line(line)
        if not (base._COMPOSE_LINE.fullmatch(line) or _CODE_LINE.fullmatch(line) or refusal):
            redacted += 1
            kept.append("[redacted-line]")
        elif base.secret_hits(line, secret_values):
            secret_lines += 1
            kept.append("[redacted-secret-line]")
        else:
            kept.append(line)
            if refusal:
                refusals.append(refusal)
    text = "\n".join(kept) + ("\n" if kept else "")
    facts = {"lines": len(lines), "kept": len(lines) - redacted - secret_lines}
    return text, {**facts, "redacted": redacted, "redacted_secret_lines": secret_lines}, refusals


_EXECUTE_KEYS = {
    "run_id",
    "phase",
    "t0_published",
    "interrupted",
    "attempts",
    "sent",
    "apparatus_faults",
    "raw_rejected",
    "drain",
}
_FINAL_KEYS = {
    "run_id",
    "decision",
    "lifecycle",
    "binding",
    "reasons",
    "decision_sha256",
    "manifest_status",
    "manifest_sha256",
    "custody_errors",
}
_OUTPUT_KEYS = {
    "start-official": {
        "run_id",
        "scenario_id",
        "evidence_mode",
        "phase",
        "manifest_sha256",
        "start_sha256",
        "preflight_sha256",
    },
    "status": {
        "run_id",
        "exists",
        "scenario_id",
        "phase",
        "finalized",
        "artifacts",
        "decision",
        "custody_errors",
    },
    "reconcile": {
        "run_id",
        "provisional",
        "decision",
        "criteria",
        "invalidity_reasons",
        "not_demonstrable",
        "failed_criteria",
    },
    "finalize": _FINAL_KEYS,
    "abandon-official-pre-t0": _FINAL_KEYS,
    "declare-executor-gone": _FINAL_KEYS,
}
_DRAIN_REQUIRED = {
    "schema_version",
    "scenario_id",
    "run_id",
    "recorded_at",
    "requests_sent_during_drain",
    "outcome",
    "polls",
    "uninspected_polls",
}


@dataclass
class Outcome:
    classification: str
    exit_code: int
    phase: str | None = None
    run_id: str | None = None
    decision: str | None = None
    problems: list[str] = field(default_factory=list)
    persist_stdout: bool = False


def action_problems(
    action: str, payload: dict[str, Any], *, run_id: str | None, reconciled: str | None
) -> tuple[list[str], str | None, str | None]:
    """Problemas de la salida, la fase que informa y la decisión, si la hay."""
    problems: list[str] = []
    if action == "execute-official":
        stopped = payload.get("phase") == "STOPPED_PRE_T0"
        keys = {"run_id", "phase", "reasons"} if stopped else _EXECUTE_KEYS
    elif action == "drain":
        keys = None
    else:
        keys = _OUTPUT_KEYS[action]
    if keys is not None and set(payload) != keys:
        problems.append("payload-keys")
    if action == "drain" and not _DRAIN_REQUIRED <= set(payload):
        problems.append("payload-keys")
    got = payload.get("run_id")
    if not isinstance(got, str) or not _RUN_ID.fullmatch(got):
        problems.append("run-id")
    elif run_id is not None and got != run_id:
        problems.append("run-id-differs")
    phase = decision = None
    if action == "start-official":
        for key, value in (("scenario_id", "EX-10"), ("phase", "PREPARED")):
            if payload.get(key) != value:
                problems.append(f"start:{key}")
        if payload.get("evidence_mode") != EVIDENCE_MODE:
            problems.append("start:evidence_mode")
        for key in ("manifest_sha256", "start_sha256", "preflight_sha256"):
            if not isinstance(payload.get(key), str) or not _SHA256.fullmatch(payload[key]):
                problems.append(f"start:{key}")
        phase = "PREPARED"
    elif action == "status":
        phase = payload.get("phase")
        if payload.get("exists") is not True or phase not in _PHASES:
            problems.append("status:phase")
        if payload.get("scenario_id") != "EX-10" or payload.get("custody_errors") != []:
            problems.append("status:run")
    elif action == "execute-official":
        phase = payload.get("phase")
        if phase == "STOPPED_PRE_T0":
            if not isinstance(payload.get("reasons"), list):
                problems.append("execute:reasons")
        elif phase != "RECONCILING" or type(payload.get("t0_published")) is not bool:
            problems.append("execute:phase")
    elif action == "drain":
        phase = "RECONCILING"
        if payload.get("schema_version") != "s11c3-ex10-drain-v1":
            problems.append("drain:schema")
        if payload.get("requests_sent_during_drain") != 0:
            problems.append("drain:requests-sent")
        if payload.get("outcome") not in {"COMPLETE", "TIMEOUT", "NO_T0", "RAW_MISSING"}:
            problems.append("drain:outcome")
    elif action == "reconcile":
        decision = payload.get("decision")
        if payload.get("provisional") is not True or decision not in _DECISIONS:
            problems.append("reconcile:decision")
    else:
        decision, lifecycle = payload.get("decision"), payload.get("lifecycle")
        phase = lifecycle
        if action == "abandon-official-pre-t0":
            if lifecycle != "ABANDONED_PRE_T0" or decision != "NOT_DEMONSTRATED":
                problems.append("abandon:not-pre-t0")
            if payload.get("binding") is not False:
                problems.append("abandon:binding")
        elif action == "declare-executor-gone":
            # El dominio cierra un executor caído en STARTED/EMITTING como NOT_DEMONSTRATED.
            reasons = payload.get("reasons")
            if lifecycle != "NOT_DEMONSTRATED" or decision != "NOT_DEMONSTRATED":
                problems.append("executor-gone:lifecycle")
            if payload.get("binding") is not False:
                problems.append("executor-gone:binding")
            if not isinstance(reasons, list) or not any(
                r in {"emission-interrupted:before-arming", "emission-interrupted:raw-missing"}
                for r in reasons
            ):
                problems.append("executor-gone:reasons")
        else:
            expected = {
                "PASS": "FINALIZED_PASS",
                "FAIL": "FINALIZED_FAIL",
                "NOT_DEMONSTRATED": "NOT_DEMONSTRATED",
            }
            if decision not in _DECISIONS or lifecycle != expected.get(decision):
                problems.append("finalize:lifecycle")
            if decision != reconciled:
                problems.append("finalize:decision-differs-from-reconciliation")
            if payload.get("binding") is not (decision in {"PASS", "FAIL"}):
                problems.append("finalize:binding")
            if decision in {"PASS", "FAIL"} and payload.get("custody_errors") != []:
                problems.append("finalize:custody")
    return problems, phase, decision


def validate_output(
    action: str,
    completed: Completed,
    refusals: list,
    *,
    run_id: str | None,
    reconciled: str | None,
    secret_values: Sequence[str],
) -> Outcome:
    """Coherencia entre el exit del command, su stdout y su rechazo en stderr."""
    code, raw = completed.returncode, completed.stdout
    if code == EXIT_USAGE:
        if raw or refusals:
            return Outcome(
                "LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE, problems=["usage-with-output"]
            )
        return Outcome("COMMAND_USAGE_OR_CONFIG_ERROR", EXIT_USAGE)
    if code in (EXIT_REFUSED, EXIT_TERMINAL):
        terminal = code == EXIT_TERMINAL
        if raw or len(refusals) != 1 or refusals[0]["terminal"] is not terminal:
            return Outcome(
                "LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE, problems=["refusal-incoherent"]
            )
        return Outcome("REFUSED", code, run_id=run_id)
    if code != EXIT_OK:
        return Outcome("LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE, problems=[f"exit-code:{code}"])
    if refusals:
        return Outcome("LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE, problems=["refusal-with-exit-0"])
    if len(raw) > MAX_JSON_BYTES:
        return Outcome("LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE, problems=["stdout-too-large"])
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text, parse_constant=base._reject_constant)
    except (UnicodeDecodeError, ValueError):
        return Outcome("LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE, problems=["stdout-not-json"])
    if not isinstance(payload, dict):
        return Outcome("LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE, problems=["stdout-not-object"])
    hits = base.secret_hits(text, secret_values)
    problems = [f"stdout-secret:{name}" for name in sorted(hits)]
    if text != json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n":
        problems.append("stdout-not-the-command-json")
    try:
        found, phase, decision = action_problems(
            action, payload, run_id=run_id, reconciled=reconciled
        )
        problems += found
    except (AttributeError, KeyError, TypeError, ValueError):
        problems.append("stdout-malformed")
        phase = decision = None
    if problems:
        return Outcome(
            "LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE, problems=problems, persist_stdout=not hits
        )
    return Outcome(
        "COHERENT",
        EXIT_OK,
        phase=phase,
        run_id=payload["run_id"],
        decision=decision,
        persist_stdout=True,
    )


# --- Instantáneas de la evidencia oficial (sólo lectura) --------------------------------------


def _tree_facts(base_path: Path) -> dict[str, list]:
    """Metadata completa (salvo atime) de un árbol, sin seguir symlinks ni escribir nada."""
    facts: dict[str, list] = {}
    pending = [base_path]
    while pending:
        path = pending.pop()
        info = os.lstat(path)
        fact = [
            stat.S_IFMT(info.st_mode),
            stat.S_IMODE(info.st_mode),
            info.st_uid,
            info.st_gid,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_ino,
        ]
        if stat.S_ISREG(info.st_mode):
            fact.append(hashlib.sha256(path.read_bytes()).hexdigest())
        elif stat.S_ISLNK(info.st_mode):
            fact.append(os.readlink(path))
        elif stat.S_ISDIR(info.st_mode):
            pending.extend(path / name for name in sorted(os.listdir(path)))
        facts[str(path.relative_to(base_path))] = fact
    return facts


def evidence_snapshot(repo_root: Path) -> dict[str, Any]:
    """Cada run de la raíz oficial, con un resumen por run (nunca contenido)."""
    runs = repo_root / "var" / "s11-runs" / "runs"
    snapshot: dict[str, Any] = {"runs": {}}
    if os.path.lexists(runs) and stat.S_ISDIR(os.lstat(runs).st_mode):
        for name in sorted(os.listdir(runs)):
            facts = _tree_facts(runs / name)
            snapshot["runs"][name] = {
                "entries": len(facts),
                "digest": hashlib.sha256(base.canonical_bytes(facts)).hexdigest(),
            }
    return snapshot


def snapshot_problems(
    action: str, before: Mapping[str, Any], after: Mapping[str, Any], *, run_id: str | None,
    created: str | None,
) -> list[str]:  # fmt: skip
    """Ningún run previo cambia; `status` no cambia nada; START sólo añade su run."""
    problems = []
    previous, current = before["runs"], after["runs"]
    expected_names = set(previous) | ({created} if created else set())
    if set(current) != expected_names:
        problems.append("evidence-runs-changed")
    for name, facts in previous.items():
        if name == run_id and action not in READ_ONLY_ACTIONS:
            continue
        if current.get(name) != facts:
            problems.append("prior-run-changed" if name != run_id else "evidence-changed-by-read")
    return sorted(set(problems))


# --- Orquestación ----------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Invocation:
    action: str
    expected_git_sha: str
    repo_root: Path
    session_root: Path
    environ: Mapping[str, str]
    confirmed: bool = False
    nonce: str | None = None
    preflight_report: Path | None = None
    acknowledged: tuple[str, ...] = ()
    safe_directory: bool = False
    timeout: float | None = None
    runner_factory: Callable[[Path, Mapping[str, str]], DockerRunner] = DockerRunner
    clock: Callable[[], datetime] = _now
    token: Callable[[], str] = lambda: secrets.token_hex(4)
    integrity: Callable[[Path, str], dict[str, str]] = verify_launcher_integrity
    native_bootstrap: Any = None


def validate_invocation(invocation: Invocation) -> None:
    action = invocation.action
    if action not in ACTIONS:
        raise UsageError()
    if not _SHA1.fullmatch(invocation.expected_git_sha):
        raise UsageError()
    spec = ACTIONS[action]
    if spec.confirmation is not None and not invocation.confirmed:
        raise UsageError()
    start = action == "start-official"
    if start != (invocation.nonce is not None) or start != (
        invocation.preflight_report is not None
    ):
        raise UsageError()
    if invocation.nonce is not None and not _NONCE.fullmatch(invocation.nonce):
        raise UsageError()
    if invocation.acknowledged and not start:
        raise UsageError()
    if any(not _RUN_ID.fullmatch(run) for run in invocation.acknowledged):
        raise UsageError()
    if len(set(invocation.acknowledged)) != len(invocation.acknowledged):
        raise UsageError()


@contextlib.contextmanager
def session_lock(root: Path) -> Iterator[None]:
    """Un launcher a la vez por sesión, además del de checkout (compartido con los demás)."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LauncherBusy() from None
        yield
    finally:
        os.close(fd)


def _verify_checkout(invocation: Invocation) -> tuple[Any, dict[str, str], str]:
    repo = invocation.repo_root
    base.bind(repo, invocation.expected_git_sha)
    facts = _fixed_verify_repository(repo, invocation.expected_git_sha)
    tracked = set(_FixedSourceGit(repo)("ls-files", "-z")[1].split("\0"))
    for relative in REQUIRED_FILES:
        path = repo / relative
        if relative not in tracked or path.is_symlink() or not path.is_file():
            raise LauncherError("required-file-missing")
    integrity = invocation.integrity(repo, invocation.expected_git_sha)
    code, out = _FixedSourceGit(repo)("symbolic-ref", "--short", "-q", "HEAD", ok=(0, 1))[:2]
    branch = out.strip() if code == 0 else ""
    if branch != "main":
        raise LauncherError("branch-not-main")
    verify_official_roots(repo)
    return facts, integrity, branch


def _session_schema_for_dispatch(invocation: Invocation) -> str:
    """Bounded nofollow read for dispatch; never repair, create outputs or adopt v1."""
    root = invocation.session_root
    check_session_location(root, invocation.repo_root)
    _private_dir(root, "session-root-unsafe")
    try:
        raw, lease = _capture_native_source(root / SESSION_FILE, error_type=SessionTampered)
    except SessionTampered:
        raise SessionTampered("SESSION_FILE_UNSAFE") from None
    info = lease[1]
    if stat.S_IMODE(info[2]) != 0o600 or info[3] != os.getuid() or len(raw) > MAX_JSON_BYTES:
        raise SessionTampered("SESSION_FILE_UNSAFE")
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=base._reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise SessionTampered("SESSION_FILE_MALFORMED") from None
    if type(value) is not dict or base.canonical_bytes(value) != raw:
        raise SessionTampered("SESSION_FILE_MALFORMED")
    schema = value.get("schema_version")
    if schema not in {SESSION_SCHEMA, "s11c3-ex10-official-launcher-session-v2"}:
        raise SessionTampered("SESSION_SCHEMA_UNKNOWN")
    return schema


@dataclass(frozen=True, init=False)
class NativeLauncherBootstrap:
    """Fixed host providers, never selected from a request or environment.

    The concrete class is not approval authority. The installed verifier must
    independently authorize the exact profile, complete registry, source,
    registered session location and global guard. Checkpoints belong to an
    independent durable store; neither reader nor publisher may derive them
    from the session being verified. There is no repair or retry here.
    """

    repo_root: Path
    expected_git_sha: str
    profile_path: Path
    path_context_bytes: bytes
    approval_reference_bytes: bytes
    session_context_bytes: bytes
    global_store: Any
    global_name: str
    global_identity: Any
    approve: Callable
    read_authority: Callable
    read_checkpoint: Callable
    publish_checkpoint: Callable
    apparatus: Mapping[str, Any] = field(repr=False)
    _source_leases: tuple = field(repr=False)
    action_factory: Any = field(default=None, repr=False)

    def __init__(
        self,
        *,
        repo_root,
        expected_git_sha,
        profile_path,
        path_context,
        approval_reference,
        session_context,
        global_store,
        global_name,
        global_identity,
        approve,
        read_authority,
        read_checkpoint,
        publish_checkpoint,
        action_factory=None,
    ):
        if action_factory is not None and type(action_factory) is not NativeActionFactory:
            raise LauncherError("native-factory-installation-invalid")
        from types import MappingProxyType

        apparatus, source_leases = _load_native_apparatus(
            repo_root, expected_git_sha, _retain_source_leases=True
        )
        durable = apparatus["ex10_authority_backend"]
        if (
            type(global_store) is not durable.PrivateStore
            or type(global_identity) is not durable.FileIdentity
            or not all(
                callable(p) for p in (approve, read_authority, read_checkpoint, publish_checkpoint)
            )
        ):
            raise LauncherError("native-host-bootstrap-invalid")
        fields = {
            "repo_root": Path(repo_root),
            "expected_git_sha": expected_git_sha,
            "profile_path": Path(profile_path),
            "path_context_bytes": base.canonical_bytes(path_context),
            "approval_reference_bytes": base.canonical_bytes(approval_reference),
            "session_context_bytes": base.canonical_bytes(session_context),
            "global_store": global_store,
            "global_name": global_name,
            "global_identity": global_identity,
            "approve": approve,
            "read_authority": read_authority,
            "read_checkpoint": read_checkpoint,
            "publish_checkpoint": publish_checkpoint,
            "apparatus": MappingProxyType(dict(apparatus)),
            "_source_leases": source_leases,
            "action_factory": action_factory,
        }
        for name, value in fields.items():
            object.__setattr__(self, name, value)

    def checkpoint(self, *, allow_missing=False):
        value = self.read_checkpoint()
        if value is None and allow_missing:
            return None
        if (
            type(value) is not tuple
            or len(value) != 2
            or type(value[0]) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", value[0])
            or type(value[1]) is not int
            or value[1] < 0
        ):
            raise SessionTampered("native-independent-checkpoint-required")
        return value

    def publish(self, previous, session):
        replacement = (session._expected_tip, session._expected_count)
        try:
            if self.publish_checkpoint(previous, replacement) is not None:
                raise LauncherError("native-write-uncertain")
            if self.checkpoint() != replacement:
                raise LauncherError("native-write-uncertain")
        except Exception:
            # Keep the created/updated record; absence or an old checkpoint
            # blocks every later writer. Never derive a replacement or retry.
            raise LauncherError("native-write-uncertain") from None

    def writer(self):
        durable = self.apparatus["ex10_authority_backend"]
        return durable.HeldWriter(self.global_store, self.global_name, self.global_identity)

    def context(self):
        return json.loads(self.session_context_bytes)

    def qualify(self, invocation, *, session=None):
        """Read and approve actual inputs, then recheck every observed artifact."""
        import ast

        rp = self.apparatus["ex10_runtime_profile"]
        history = self.apparatus["ex10_history_authority"]
        if (
            invocation.repo_root != self.repo_root
            or invocation.expected_git_sha != self.expected_git_sha
            or str(invocation.session_root) != self.context()["parent"]
        ):
            raise LauncherError("native-host-bootstrap-mismatch")
        _fixed_verify_repository(self.repo_root, self.expected_git_sha)
        blobs = verify_launcher_integrity(self.repo_root, self.expected_git_sha)
        launcher_sources = []
        for relative, blob in blobs.items():
            path = self.repo_root / relative
            raw, lease = _capture_native_source(path, error_type=LauncherError)
            if git_blob_sha(raw) != blob:
                raise LauncherError("native-source-integrity")
            launcher_sources.append((path, raw, lease))
        launcher_sources = tuple(launcher_sources)
        apparatus_sources, original_apparatus = self._source_leases, self.apparatus
        apparatus_bindings = tuple(original_apparatus.items())
        branch = _fixed_source_git(self.repo_root, "symbolic-ref", "--short", "HEAD").strip()
        if not branch:
            raise LauncherError("native-source-branch-required")
        paths_context = json.loads(self.path_context_bytes)
        source_path = self.repo_root / "backend/apps/experiments_app/ex10_preflight.py"
        source_raw, source_lease = _capture_native_source(source_path, error_type=LauncherError)
        if (
            git_blob_sha(source_raw)
            != _fixed_source_git(
                self.repo_root, "rev-parse", "HEAD:backend/apps/experiments_app/ex10_preflight.py"
            ).strip()
        ):
            raise LauncherError("native-source-integrity")
        sources = next(
            ast.literal_eval(node.value)
            for node in ast.parse(source_raw).body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "RUNNING_SOURCES" for t in node.targets)
        )
        if paths_context.get("running_sources") != list(sources):
            raise LauncherError("native-running-source-closure-invalid")
        profile = rp.load_runtime_profile(self.profile_path, context=paths_context)
        value = profile.to_dict()
        if value["source_revision"] != self.expected_git_sha:
            raise LauncherError("native-source-integrity")
        paths = rp.validate_runtime_profile_paths(profile, paths_context)
        context = self.context()
        if (
            context.get("repo_root") != str(self.repo_root)
            or (context.get("owner_uid"), context.get("owner_gid")) != (os.getuid(), os.getgid())
            or context.get("historical_roots") != paths_context["historical_roots"]
            or context.get("prior_session_roots") != paths_context["prior_session_roots"]
        ):
            raise LauncherError("native-session-context-invalid")
        root = str(invocation.session_root)
        excluded = [
            str(self.repo_root),
            *paths_context["historical_roots"],
            *paths_context["prior_session_roots"],
            *(r["path"] for r in value["host_roots"].values()),
            *(m["source_path"] for m in value["masks"] if m["kind"] == "directory"),
        ]
        if (
            root == "/tmp"
            or root.startswith("/tmp/")
            or any(rp._overlap(root, p) for p in excluded)
        ):
            raise LauncherError("native-session-location-unsafe")
        guard_path = self.global_store.root + "/" + self.global_name
        rp._outside(guard_path, str(self.repo_root), [*excluded[1:], root])
        self.global_store.check()
        guard_fd = self.global_store.open(self.global_name, expected=self.global_identity)
        os.close(guard_fd)
        approval = json.loads(self.approval_reference_bytes)
        rp._reference(approval)
        references = {
            **{name: value[name] for name in rp.REFERENCE_FIELDS},
            "runtime_profile_approval_reference": approval,
        }
        captured = {}
        for name, reference in references.items():
            rp._outside(reference["path"], str(self.repo_root), [*excluded[1:], root])
            raw, lease = _capture_native_source(Path(reference["path"]), error_type=LauncherError)
            info = lease[1]
            if (
                stat.S_IMODE(info[2]) != 0o600
                or (info[3], info[4]) != (os.getuid(), os.getgid())
                or hashlib.sha256(raw).hexdigest() != reference["sha256"]
            ):
                raise LauncherError("native-approval-reference-changed")
            captured[name] = (raw, lease)
        compose_captured = {}
        for item in value["compose_files"]:
            raw, lease = _capture_native_source(Path(item["path"]), error_type=LauncherError)
            compose_captured[item["path"]] = (raw, lease)
            if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise LauncherError("native-compose-integrity")
            if item["kind"] == "tracked" and (
                git_blob_sha(raw) != item["git_blob"]
                or _fixed_source_git(
                    self.repo_root, "rev-parse", "HEAD:" + item["git_path"]
                ).strip()
                != item["git_blob"]
            ):
                raise LauncherError("native-compose-integrity")
        snapshot = self.read_authority(value["historical_lifecycle_authority_reference"])
        revision = history.current_revision(snapshot)
        if (
            history._verified(snapshot)["descriptor_reference"]
            != value["historical_lifecycle_authority_reference"]
        ):
            raise LauncherError("native-history-reference-mismatch")
        history.list_official_runs(snapshot)
        first = history.first_valid_status(snapshot)
        if first["status"] == "HISTORY_INVALID":
            raise LauncherError("native-history-invalid")
        binding = self.path_binding(value, approval)
        profile_raw, profile_lease = _capture_native_source(
            self.profile_path, error_type=LauncherError
        )
        if profile_raw != rp.canonical_runtime_profile_bytes(profile):
            raise LauncherError("native-profile-changed")
        if session is not None:
            header = session.header
            if (
                header["launcher_blobs"] != blobs
                or header["branch"] != branch
                or header["runtime_profile_approval_reference"] != approval
                or header["path_identity_binding"] != binding
            ):
                raise SessionTampered("native-session-binding-changed")
        original_approve = self.approve
        approval_facts = {
            "profile": profile,
            "session": session,
            "session_root": root,
            "source_revision": self.expected_git_sha,
            "source_tree": _fixed_source_git(self.repo_root, "rev-parse", "HEAD^{tree}").strip(),
            "launcher_blobs": blobs,
            "path_identities": paths,
            "path_identity_binding": binding,
            "guard_identity": self.global_identity,
            "authority_revision": revision,
            "references": tuple(sorted((name, data[0]) for name, data in captured.items())),
        }

        mutable_approval_fields = ("launcher_blobs", "authority_revision")
        approval_mapping_bytes = base.canonical_bytes(
            {name: approval_facts.pop(name) for name in mutable_approval_fields}
        )

        def approve_current():
            # Keep the original independent approver and its exact qualified
            # facts. A retained process may not renew its authority snapshot.
            if self.approve is not original_approve:
                raise LauncherError("native-host-approval-required")
            try:
                delivered = {**approval_facts, **json.loads(approval_mapping_bytes)}
                approved = original_approve(**delivered)
                unchanged = (
                    base.canonical_bytes(
                        {name: delivered[name] for name in mutable_approval_fields}
                    )
                    == approval_mapping_bytes
                )
            except Exception:
                raise LauncherError("native-host-approval-required") from None
            if approved is not None or not unchanged or self.approve is not original_approve:
                raise LauncherError("native-host-approval-required")

        approve_current()

        def recheck(*, directory_transition=None):
            def unchanged(path, expected):
                raw, (directories, file_info) = expected
                if directory_transition is not None:
                    previous, replacement = directory_transition
                    if replacement != (*previous[:-1], previous[-1] + 1):
                        raise LauncherError("native-session-parent-changed")
                    # The initializer's exclusive single-child transition is
                    # checked separately. Directory leases retain dev/inode,
                    # mode and ownership; file leases still retain nlink=1.
                return _capture_native_source(path, error_type=LauncherError) == (
                    raw,
                    (directories, file_info),
                )

            if (
                rp.validate_runtime_profile_paths(profile, paths_context) != paths
                or self.path_binding(value, approval) != binding
                or not unchanged(self.profile_path, (profile_raw, profile_lease))
                or not unchanged(source_path, (source_raw, source_lease))
            ):
                raise LauncherError("native-host-input-changed")
            for name, reference in references.items():
                if not unchanged(Path(reference["path"]), captured[name]):
                    raise LauncherError("native-host-input-changed")
            _fixed_verify_repository(self.repo_root, self.expected_git_sha)
            if verify_launcher_integrity(self.repo_root, self.expected_git_sha) != blobs:
                raise LauncherError("native-source-integrity")
            for path, captured_compose in compose_captured.items():
                if not unchanged(Path(path), captured_compose):
                    raise LauncherError("native-compose-changed")
            self.global_store.check()
            guard_fd = self.global_store.open(self.global_name, expected=self.global_identity)
            os.close(guard_fd)
            history.current_revision(snapshot)

        recheck()
        # Fixed local closure over this exact verified snapshot. Callers which
        # perform later guard I/O can check expiry again without a provider
        # read, authority refresh, filesystem access or new protocol fields.
        recheck.check_authority_current = lambda: history.current_revision(snapshot)
        recheck.check_approval_current = approve_current

        def source_leases_current():
            # Original physical leases, without any provider, refresh or FD close.
            if (
                self._source_leases is not apparatus_sources
                or self.apparatus is not original_apparatus
                or len(original_apparatus) != len(apparatus_bindings)
                or any(
                    original_apparatus.get(name) is not module
                    for name, module in apparatus_bindings
                )
            ):
                raise LauncherError("native-source-integrity")
            retained_sources = (
                (self.profile_path, profile_raw, profile_lease),
                (source_path, source_raw, source_lease),
                *launcher_sources,
                *apparatus_sources,
                *(
                    (Path(references[name]["path"]), raw, lease)
                    for name, (raw, lease) in captured.items()
                ),
                *((Path(path), raw, lease) for path, (raw, lease) in compose_captured.items()),
            )
            for path, _, lease in retained_sources:
                _native_check_source_lease(path, lease, code="native-host-input-changed")

        recheck.check_source_leases_current = source_leases_current

        def read_start_prior():
            # Fixed pure closure over the same qualified B3 observation.
            # Never renew it or fabricate a run from global uncertainty.
            history.current_revision(snapshot)
            if snapshot.authority_uncertainty or snapshot.write_closed:
                return None
            rows = sorted(history.list_official_runs(snapshot), key=lambda row: row["run_id"])
            result = [
                {
                    "run_id": row["run_id"],
                    "evidence_mode": EVIDENCE_MODE,
                    "finalized": row["lifecycle_state"] == "TERMINAL",
                    "decision": row["result"],
                    "problems": ["authority-uncertain"]
                    if row["lifecycle_state"] == "UNCERTAIN"
                    else [],
                }
                for row in rows
            ]
            history.current_revision(snapshot)
            return base.canonical_bytes(result)

        recheck.read_start_prior = read_start_prior
        return profile, blobs, branch, binding, first, recheck

    def path_binding(self, profile, approval):
        rp = self.apparatus["ex10_runtime_profile"]
        directories = {
            profile["source_host_root"],
            *(r["path"] for r in profile["host_roots"].values()),
            *(m["source_path"] for m in profile["masks"] if m["kind"] == "directory"),
        }
        paths = [
            *directories,
            *(c["path"] for c in profile["compose_files"][:2]),
            *(m["source_path"] for m in profile["masks"]),
            profile["override_path"],
            approval["path"],
            *(profile[name]["path"] for name in rp.REFERENCE_FIELDS[1:]),
        ]
        result = []
        for path in sorted(set(paths)):
            fd, _ = rp._walk(
                path,
                "dev_null"
                if path == "/dev/null"
                else ("directory" if path in directories else "file"),
            )
            try:
                info = os.fstat(fd)
                result.append(
                    {
                        "path": path,
                        "device": info.st_dev,
                        "inode": info.st_ino,
                        "uid": info.st_uid,
                        "gid": info.st_gid,
                        "mode": f"{stat.S_IMODE(info.st_mode):04o}",
                    }
                )
            finally:
                os.close(fd)
        return result

    def load(self, invocation):
        checkpoint = self.checkpoint()
        profile, *_ = self.qualify(invocation)
        codec = self.apparatus["ex10_session_v2"]
        session = codec.load_official_session(
            invocation.session_root / SESSION_FILE,
            context=self.context(),
            profile=profile,
            expected_tip=checkpoint[0],
            expected_count=checkpoint[1],
        )
        if type(session) is not codec.OfficialSessionV2:
            raise LauncherError("v1-read-only")
        *_, final_recheck = self.qualify(invocation, session=session)
        if self.checkpoint() != checkpoint:
            raise SessionTampered("native-checkpoint-changed")
        reread = codec.load_official_session(
            invocation.session_root / SESSION_FILE,
            context=self.context(),
            profile=profile,
            expected_tip=checkpoint[0],
            expected_count=checkpoint[1],
        )
        if type(reread) is not codec.OfficialSessionV2 or reread._bytes != session._bytes:
            raise SessionTampered("native-session-changed-during-approval")
        # The final recheck ends with the same B3 snapshot's pure expiry check,
        # after checkpoint/provider/file I/O. Never refresh expired authority.
        final_recheck()
        return session

    def initialize(self, invocation):
        rp = self.apparatus["ex10_runtime_profile"]
        codec = self.apparatus["ex10_session_v2"]
        root = invocation.session_root
        # Never adopt an existing directory, even when no record exists in it.
        if os.path.lexists(root):
            raise LauncherError("native-session-already-exists")
        self.qualify(invocation)
        if self.checkpoint(allow_missing=True) is not None:
            raise LauncherError("native-session-already-checkpointed")
        with self.writer() as writer:
            profile, blobs, branch, binding, first, recheck = self.qualify(invocation)
            if self.checkpoint(allow_missing=True) is not None:
                raise LauncherError("native-session-already-checkpointed")
            parent_fd, components = rp._walk(str(root.parent), "directory")
            try:
                before = os.fstat(parent_fd)
                if (before.st_uid, before.st_gid) != (os.getuid(), os.getgid()) or stat.S_IMODE(
                    before.st_mode
                ) != 0o700:
                    raise LauncherError("native-session-parent-unsafe")
                writer.check()
                os.mkdir(root.name, mode=0o700, dir_fd=parent_fd)
                created = os.stat(root.name, dir_fd=parent_fd, follow_symlinks=False)
                created_identity = (
                    created.st_dev,
                    created.st_ino,
                    created.st_uid,
                    created.st_gid,
                    created.st_mode,
                )
                os.fsync(parent_fd)
                after = os.fstat(parent_fd)

                def parent_signature(info):
                    return (
                        info.st_dev,
                        info.st_ino,
                        info.st_mode,
                        info.st_uid,
                        info.st_gid,
                        info.st_nlink,
                    )

                parent_transition = (parent_signature(before), parent_signature(after))
                if parent_transition[1] != (
                    *parent_transition[0][:-1],
                    parent_transition[0][-1] + 1,
                ):
                    raise LauncherError("native-session-parent-changed")
                check_fd, current = rp._walk(str(root.parent), "directory")
                os.close(check_fd)
                if current != components:
                    raise LauncherError("native-write-uncertain")
                with _native_session_lock(
                    self, root, expected_identity=created_identity
                ) as session_current:
                    header = codec.new_session_v2_header(
                        profile,
                        session_id="s11-ex10-official-session-" + secrets.token_hex(16),
                        created_at=invocation.clock()
                        .astimezone(UTC)
                        .strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        branch=branch,
                        launcher_blobs=blobs,
                        runtime_profile_approval_reference=json.loads(
                            self.approval_reference_bytes
                        ),
                        path_identity_binding=binding,
                    )
                    recheck(directory_transition=parent_transition)
                    session_current()
                    writer.check()
                    initial = codec.create_official_session_v2(
                        root / SESSION_FILE,
                        header,
                        profile=profile,
                        context=self.context(),
                    )
                    blocked = first["status"] != "NONE"
                    payload = {
                        "event": "initialize-session",
                        "output_sha256": {},
                        "state": {
                            "known_phase": None,
                            "uncertain": False,
                            "blocked": blocked,
                            "next_allowed_actions": ["inspect"]
                            if blocked
                            else ["cleanup-stack", "inspect", "start-official"],
                        },
                        "official_run_id": None,
                        "preflight_report_sha256": None,
                        "operational_envelope_sha256": None,
                        "in_flight": [],
                    }
                    replacement = codec.append_history_entry_v2(initial, payload)
                    recheck(directory_transition=parent_transition)
                    session_current()
                    writer.check()
                    session = codec.update_official_session_v2(
                        root / SESSION_FILE,
                        initial,
                        replacement,
                        context=self.context(),
                        profile=profile,
                        caller_serialized=True,
                    )
                    recheck(directory_transition=parent_transition)
                    session_current()
                    writer.check()
                    self.publish(None, session)
                    writer.check()
                    return _summary(
                        session.to_dict(), invocation.action, "COHERENT", EXIT_OK, output=None
                    )
            except FileExistsError:
                raise LauncherError("native-session-already-exists") from None
            finally:
                os.close(parent_fd)


class NativeExclusiveProofPublication:
    """Publish complete existing-role bytes once; publication never grants permission.

    Fixed host callers must supply coherent authenticated proofs. Each rename
    is atomic and refuses an existing destination; the set is not a transaction.
    Readers still verify every signature and crossbinding. The destination can
    be observed before its subsequent directory fsync finishes.
    """

    ROLES = frozenset(
        {"profile", "header", "process", "envelope", "reservation"}
        | {
            prefix + "-" + suffix
            for prefix in ("base", "action", "commit", "emission")
            for suffix in ("material", "head", "coordination", "receipt")
        }
    )

    def __init__(self, *, invocation, bootstrap, store, exchange_id):
        import ctypes
        from uuid import UUID

        if type(bootstrap) is not NativeLauncherBootstrap:
            raise LauncherError("native-proof-bootstrap-required")
        durable = bootstrap.apparatus["ex10_authority_backend"]
        if type(store) is not durable.PrivateStore:
            raise LauncherError("native-proof-store-required")
        try:
            if type(exchange_id) is not str or str(UUID(exchange_id)) != exchange_id:
                raise ValueError()
            session = bootstrap.load(invocation)
            profile, _, _, _, _, recheck = bootstrap.qualify(invocation, session=session)
            expected = profile.to_dict()["host_roots"]["evidence"]["path"]
            if store.root != expected + "/.s11-operational/" + exchange_id:
                raise ValueError()
            store.check()
            rename = ctypes.CDLL(None, use_errno=True).renameat2
            rename.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            rename.restype = ctypes.c_int
        except LauncherError:
            raise
        except Exception:
            raise LauncherError("native-proof-publication-unavailable") from None
        self.store, self.bootstrap, self.recheck = store, bootstrap, recheck
        self.rename, self.ctypes = rename, ctypes
        self.evidence_root = expected
        self.namespace = None
        self._namespace()
        self.attempted = set()
        self._lock = threading.Lock()

    def _namespace(self):
        rp = self.bootstrap.apparatus["ex10_runtime_profile"]
        directories = (
            self.evidence_root,
            self.evidence_root + "/.s11-operational",
            self.store.root,
        )
        leases = []
        try:
            self.store.check()
            held = os.fstat(self.store.fd)
            for path in directories:
                fd, lease = rp._walk(path, "directory")
                try:
                    info = os.fstat(fd)
                    if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (
                        os.geteuid(),
                        os.getegid(),
                        0o700,
                    ) or (
                        path == self.store.root
                        and (info.st_dev, info.st_ino) != (held.st_dev, held.st_ino)
                    ):
                        raise LauncherError("native-proof-namespace-changed")
                    leases.append(lease)
                finally:
                    os.close(fd)
            observed = tuple(leases)
            if self.namespace is not None and observed != self.namespace:
                raise LauncherError("native-proof-namespace-changed")
            # Retain the first qualified components and verify all three again
            # after walking them, including the private owner/group/mode.
            for path, lease in zip(directories, leases, strict=True):
                fd, current = rp._walk(path, "directory")
                try:
                    info = os.fstat(fd)
                    if current != lease or (
                        info.st_uid,
                        info.st_gid,
                        stat.S_IMODE(info.st_mode),
                    ) != (os.geteuid(), os.getegid(), 0o700):
                        raise LauncherError("native-proof-namespace-changed")
                finally:
                    os.close(fd)
            self.store.check()
            if self.namespace is None:
                self.namespace = observed
        except LauncherError:
            raise
        except Exception:
            raise LauncherError("native-proof-namespace-changed") from None

    @staticmethod
    def _stamp(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_uid,
            info.st_gid,
            info.st_mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    def _check_file(self, name, fd, raw, *, original_identity, expected_stamp=None):
        self._namespace()
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode))
            != (os.geteuid(), os.getegid(), 0o600)
            or (before.st_dev, before.st_ino) != original_identity
            or before.st_size != len(raw)
            or self.store.entry_identity(name) != original_identity
            or (expected_stamp is not None and self._stamp(before) != expected_stamp)
        ):
            raise LauncherError("native-proof-publication-changed")
        blocks, offset = [], 0
        while offset < len(raw):
            block = os.pread(fd, min(65536, len(raw) - offset), offset)
            if not block:
                raise LauncherError("native-proof-publication-changed")
            blocks.append(block)
            offset += len(block)
        after = os.fstat(fd)
        if (
            b"".join(blocks) != raw
            or self._stamp(after) != self._stamp(before)
            or self.store.entry_identity(name) != original_identity
        ):
            raise LauncherError("native-proof-publication-changed")
        self._namespace()
        return self._stamp(after)

    def publish(self, role, raw):
        if type(role) is not str or role not in self.ROLES:
            raise LauncherError("native-proof-role-invalid")
        if not self._lock.acquire(blocking=False):
            raise LauncherBusy()
        fd = None
        try:
            if role in self.attempted:
                raise LauncherError("native-proof-second-attempt-denied")
            self.attempted.add(role)
            sql = self.bootstrap.apparatus["ex10_authority_sqlite"]
            try:
                if type(raw) is not bytes or not 0 < len(raw) <= 16 * 1024 * 1024:
                    raise ValueError()
                sql.parse_canonical(raw)
            except Exception:
                raise LauncherError("native-proof-data-invalid") from None
            self.recheck()
            self._namespace()
            stage = ".s11-proof-" + secrets.token_hex(16) + ".tmp"
            fd = os.open(
                stage,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=self.store.fd,
            )
            born = os.fstat(fd)
            original = (born.st_dev, born.st_ino)
            if (
                not stat.S_ISREG(born.st_mode)
                or born.st_nlink != 1
                or (born.st_uid, born.st_gid, stat.S_IMODE(born.st_mode))
                != (os.geteuid(), os.getegid(), 0o600)
                or self.store.entry_identity(stage) != original
            ):
                raise LauncherError("native-proof-publication-changed")
            view = memoryview(raw)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise LauncherError("native-proof-write-uncertain")
                view = view[count:]
            os.fsync(fd)
            captured = self._check_file(stage, fd, raw, original_identity=original)
            self.recheck()
            self._check_file(stage, fd, raw, original_identity=original, expected_stamp=captured)
            # Linux fixed ABI, one attempt, no replacement and no fallback.
            status = self.rename(
                self.store.fd,
                stage.encode("ascii"),
                self.store.fd,
                (role + ".json").encode("ascii"),
                1,
            )
            if status != 0:
                error = self.ctypes.get_errno()
                if error == 17:
                    raise LauncherError("native-proof-publication-conflict")
                raise LauncherError("native-proof-publication-uncertain")
            # Rename may change ctime. Verify the birth inode, then capture its
            # final stamp and keep that exact stamp stable across fsync/recheck.
            final = self._check_file(role + ".json", fd, raw, original_identity=original)
            os.fsync(self.store.fd)
            self.recheck()
            self._check_file(
                role + ".json", fd, raw, original_identity=original, expected_stamp=final
            )
        except LauncherError:
            raise
        except Exception:
            raise LauncherError("native-proof-publication-uncertain") from None
        finally:
            try:
                if fd is not None:
                    os.close(fd)
            finally:
                self._lock.release()


@contextlib.contextmanager
def _create_native_proof_publication(
    *, invocation, bootstrap, exchange_id, preparation_deadline, authority_snapshot
):
    """Own one new publication namespace inside the approved evidence root.

    The shared parent may already exist after complete private-path validation.
    An exchange directory is always new; no existing exchange is adopted,
    repaired or removed. Failure retains created directories and published bytes.
    Only descriptors opened here are closed. The outer factory must still bind
    actual Docker/PF/B5/core/worker/action gates to this publication context.
    """
    import time
    from uuid import UUID

    owned = []
    publisher = original_publication = publication_current = None
    completed = False
    try:
        if (
            type(bootstrap) is not NativeLauncherBootstrap
            or invocation.native_bootstrap is not bootstrap
            or type(exchange_id) is not str
            or str(UUID(exchange_id)) != exchange_id
            or type(preparation_deadline) not in (int, float)
            or not 0 < preparation_deadline < float("inf")
        ):
            raise ValueError()
        durable = bootstrap.apparatus["ex10_authority_backend"]
        history = bootstrap.apparatus["ex10_history_authority"]
        original_revision = history.current_revision(authority_snapshot)
        if time.monotonic() >= preparation_deadline:
            raise ValueError()
        session = bootstrap.load(invocation)
        profile, *_, recheck = bootstrap.qualify(invocation, session=session)
        original_bootstrap_authority_current = recheck.check_authority_current
        p = profile.to_dict()
        if (
            history._verified(authority_snapshot)["descriptor_reference"]
            != p["historical_lifecycle_authority_reference"]
        ):
            raise ValueError()
        evidence_root = p["host_roots"]["evidence"]["path"]
        original_bootstrap = tuple(
            (name, getattr(bootstrap, name))
            for name in (
                "repo_root",
                "expected_git_sha",
                "profile_path",
                "path_context_bytes",
                "approval_reference_bytes",
                "session_context_bytes",
                "global_store",
                "global_name",
                "global_identity",
                "approve",
                "read_authority",
                "read_checkpoint",
                "publish_checkpoint",
                "apparatus",
                "action_factory",
            )
        )

        def pure():
            if (
                invocation.native_bootstrap is not bootstrap
                or any(getattr(bootstrap, name) is not value for name, value in original_bootstrap)
                or history.current_revision(authority_snapshot) != original_revision
                or time.monotonic() >= preparation_deadline
            ):
                raise ValueError()
            original_bootstrap_authority_current()
            if time.monotonic() >= preparation_deadline:
                raise ValueError()

        def open_store(path, info):
            if not stat.S_ISDIR(info.st_mode) or (
                info.st_uid,
                info.st_gid,
                stat.S_IMODE(info.st_mode),
            ) != (os.geteuid(), os.getegid(), 0o700):
                raise ValueError()
            store = durable.PrivateStore(
                path, durable.FileIdentity(info.st_dev, info.st_ino, info.st_uid, 0o700)
            )
            resolver = store.resolver
            # Capture actual owned ancestor descriptors before exposing the
            # namespace. Cleanup never follows mutable store/resolver handles.
            original_fds = tuple(resolver._fds)
            # The constructor has returned all of these owned descriptors. Put
            # them in cleanup before the first subsequent fallible syscall.
            owned.append(
                (
                    store,
                    store.root,
                    store.identity,
                    store.fd,
                    resolver,
                    tuple((fd, None, None) for fd in original_fds),
                )
            )
            fds = tuple((fd, os.fstat(fd).st_dev, os.fstat(fd).st_ino) for fd in original_fds)
            owned[-1] = (store, store.root, store.identity, store.fd, resolver, fds)
            return store

        def current():
            pure()
            recheck()
            for store, root, identity, fd, resolver, fds in owned:
                if (
                    store.root != root
                    or store.identity != identity
                    or store.fd != fd
                    or store.resolver is not resolver
                    or tuple(resolver._fds) != tuple(row[0] for row in fds)
                ):
                    raise ValueError()
                store.check()
                if os.fstat(fd).st_gid != os.getegid():
                    raise ValueError()
            if publisher is not None and (
                publisher.store is not owned[-1][0]
                or publisher.bootstrap is not bootstrap
                or publisher.recheck is not publication_current
                or publisher.namespace is not original_publication[0]
                or publisher.rename is not original_publication[1]
                or publisher.attempted is not original_publication[2]
                or publisher._lock is not original_publication[3]
            ):
                raise ValueError()
            pure()

        evidence = open_store(evidence_root, os.stat(evidence_root, follow_symlinks=False))
        current()
        parent_path = evidence_root + "/.s11-operational"
        try:
            info = os.stat(".s11-operational", dir_fd=evidence.fd, follow_symlinks=False)
        except FileNotFoundError:
            # If a concurrent creator wins after this absence, mkdir fails.
            # Never adopt its parent as a fallback or retry this creation.
            current()
            os.mkdir(".s11-operational", mode=0o700, dir_fd=evidence.fd)
            info = os.stat(".s11-operational", dir_fd=evidence.fd, follow_symlinks=False)
            parent = open_store(parent_path, info)
            os.fsync(evidence.fd)
        else:
            parent = open_store(parent_path, info)
        if evidence.entry_identity(".s11-operational") != (info.st_dev, info.st_ino):
            raise ValueError()
        current()
        os.mkdir(exchange_id, mode=0o700, dir_fd=parent.fd)
        born = os.stat(exchange_id, dir_fd=parent.fd, follow_symlinks=False)
        directory = open_store(parent_path + "/" + exchange_id, born)
        os.fsync(parent.fd)
        if parent.entry_identity(exchange_id) != (born.st_dev, born.st_ino):
            raise ValueError()
        current()
        candidate = NativeExclusiveProofPublication(
            invocation=invocation, bootstrap=bootstrap, store=directory, exchange_id=exchange_id
        )
        original_recheck = candidate.recheck

        def checked_publication():
            current()
            original_recheck()
            current()

        original_publication = (
            candidate.namespace,
            candidate.rename,
            candidate.attempted,
            candidate._lock,
        )
        publication_current = checked_publication
        candidate.recheck = publication_current
        publisher = candidate
        current()
        yield publisher, current
        current()
        completed = True
    except Aborted:
        # Keep the original known public abort after closing captured owned FDs.
        # A physical close failure below still overrides it as uncertainty.
        raise
    except Exception:
        raise LauncherError("native-proof-namespace-uncertain") from None
    finally:
        close_failed = False
        for _, _, _, _, resolver, fds in reversed(owned):
            for fd, device, inode in reversed(fds):
                try:
                    # None marks constructor-owned FDs whose initial metadata
                    # capture failed before any store was exposed to providers.
                    if device is not None:
                        info = os.fstat(fd)
                        if (info.st_dev, info.st_ino) != (device, inode):
                            close_failed = True
                            continue
                    os.close(fd)
                except Exception:
                    close_failed = True
            try:
                # Never call .clear()/close() on a mutable replacement list or
                # resolver. Only the captured original descriptors were closed.
                resolver._fds = []
                resolver._closed = True
            except Exception:
                close_failed = True
        if close_failed:
            raise LauncherError("native-proof-namespace-uncertain") from None
        if completed:
            # Last owned close is I/O too. Never renew the original authority
            # snapshot or absolute preparation budget after releasing it.
            try:
                pure()
            except Exception:
                raise LauncherError("native-proof-namespace-uncertain") from None


def _prepare_native_policy(
    *,
    apparatus,
    session_qualifier,
    registry,
    authorization_binding,
    resource_plan,
    policy_store,
    policy_name,
    policy_schema_version,
    approved_root_binding_sha256,
    approval_verifier,
    envelope_verifier,
    required_preflight_checks,
    resolve_policy_binding,
    check_current,
    preparation_deadline,
    authority_snapshot,
):
    """Select existing fixed policy bytes; the real journal must still admit them.

    This internal factory preparation creates no approval, grant or policy file.
    The installed resolver can select only the constructor-approved private
    locator. Complete native/resource/document rechecks follow its single call.
    """
    import math
    import time

    op = apparatus["ex10_operational_context"]
    if (
        type(preparation_deadline) not in (int, float)
        or not math.isfinite(preparation_deadline)
        or type(session_qualifier) is not op.NativeSessionQualifier
        or type(registry) is not op.HostArtifactRegistry
        or type(authorization_binding) is not op.HostArtifactBinding
        or authorization_binding.name != "authorization"
        or type(resource_plan) is not op.NativePolicyResourcePlan
        or type(policy_store) is not op.durable.PrivateStore
        or type(policy_name) is not str
        or policy_name in ("", ".", "..")
        or "/" in policy_name
        or "\\" in policy_name
        or "\x00" in policy_name
        or type(policy_schema_version) is not str
        or not policy_schema_version
        or not all(
            callable(provider)
            for provider in (
                approval_verifier,
                envelope_verifier,
                resolve_policy_binding,
                check_current,
            )
        )
    ):
        raise LauncherError("native-policy-preparation-context")
    names = (
        "host_identity",
        "bridge_approval",
        "witness_transport",
        "writer_closure",
        "recovery_policy",
        "coordinator_registry",
    )
    original_bindings = registry.bindings
    if set(original_bindings) != set(names):
        raise LauncherError("native-policy-preparation-context")
    authorization_registry = op.HostArtifactRegistry((authorization_binding,))
    stores = (policy_store, authorization_binding.store) + tuple(
        binding.store for binding in original_bindings.values()
    )
    retained_stores = tuple(
        (store, store.root, store.identity, store.fd, store.resolver) for store in stores
    )

    policy_fd = None
    policy_stamp_original = policy_bytes_original = None
    policy_path = policy_store.root + "/" + policy_name
    returning_policy = False

    def file_stamp(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_uid,
            info.st_gid,
            info.st_mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    def retained_policy():
        # This is the already existing file, captured before any provider call.
        # Same JSON/reference in a newly created or replaced inode is insufficient.
        if policy_fd is None or policy_stamp_original is None or policy_bytes_original is None:
            raise LauncherError("native-policy-preparation-changed")
        policy_store.check()
        if (
            file_stamp(os.fstat(policy_fd)) != policy_stamp_original
            or policy_store.entry_identity(policy_name) != policy_stamp_original[:2]
            or policy_store.read(policy_path) != policy_bytes_original
            or file_stamp(os.fstat(policy_fd)) != policy_stamp_original
            or policy_store.entry_identity(policy_name) != policy_stamp_original[:2]
        ):
            raise LauncherError("native-policy-preparation-changed")

    def guard():
        # Check the original absolute preparation budget and genuine retained
        # authority without another provider read, refresh or new deadline.
        if time.monotonic() >= preparation_deadline or registry.bindings is not original_bindings:
            raise LauncherError("native-policy-preparation-changed")
        revision = op.history.current_revision(authority_snapshot)
        if (revision["authority_id"], revision["epoch"]) != (
            resource_plan.authority_id,
            resource_plan.authority_epoch,
        ):
            raise LauncherError("native-policy-preparation-changed")
        resource_plan.check()
        for store, root, identity, fd, resolver in retained_stores:
            if (
                store.root != root
                or store.identity != identity
                or store.fd != fd
                or store.resolver is not resolver
            ):
                raise LauncherError("native-policy-preparation-changed")
            store.check()
        retained_policy()
        op.history.current_revision(authority_snapshot)
        if time.monotonic() >= preparation_deadline:
            raise LauncherError("native-policy-preparation-changed")

    def native_bytes(value):
        return (
            value.profile._bytes,
            value.session._bytes,
            value.profile_reference,
            value.header_reference,
            value.process,
            value.path_identities,
        )

    def current():
        if check_current() is not None:
            raise LauncherError("native-policy-preparation-changed")
        guard()

    try:
        policy_fd = policy_store.open(policy_name)
        policy_stamp_original = file_stamp(os.fstat(policy_fd))
        policy_bytes_original = policy_store.read(policy_path)
        retained_policy()
        current()
        native = session_qualifier.qualify()
        original_native = native_bytes(native)
        documents = tuple(
            (name, registry.read(name), op.sql.canonical_bytes(registry.reference(name)))
            for name in names
        )
        authorization_raw = authorization_registry.read("authorization")
        options = dict(
            ledger_store=resource_plan.ledger_store,
            ledger_name=resource_plan.ledger_name,
            ledger_identity=resource_plan.ledger_identity,
            session_qualifier=session_qualifier,
            approved_root_binding_sha256=approved_root_binding_sha256,
            approval_verifier=approval_verifier,
            envelope_verifier=envelope_verifier,
            required_preflight_checks=required_preflight_checks,
            authorization_binding=authorization_binding,
            resource_plan=resource_plan,
        )
        preparatory = op.NativeHostPolicy(registry=registry, **options)
        canonical_facts = op.sql.canonical_bytes(
            preparatory.facts_from_resources(native=native, resources=resource_plan)
        )
        if policy_bytes_original != canonical_facts:
            raise LauncherError("native-policy-preparation-binding")
        current()
        binding = resolve_policy_binding(canonical_facts=canonical_facts, native=native)
        retained_policy()
        if type(binding) is not op.HostArtifactBinding or binding.name != "policy":
            raise LauncherError("native-policy-preparation-binding")
        reference = op.sql.parse_canonical(binding.reference)
        if (
            binding.store is not policy_store
            or reference.get("path") != policy_store.root + "/" + policy_name
            or reference.get("schema_version") != policy_schema_version
        ):
            raise LauncherError("native-policy-preparation-binding")
        final_registry = op.HostArtifactRegistry(tuple(original_bindings.values()) + (binding,))
        if final_registry.read("policy") != canonical_facts:
            raise LauncherError("native-policy-preparation-binding")
        policy = op.NativeHostPolicy(registry=final_registry, **options)
        current()
        if native_bytes(session_qualifier.qualify()) != original_native:
            raise LauncherError("native-policy-preparation-changed")
        for name, raw, expected_reference in documents:
            if (
                registry.read(name) != raw
                or op.sql.canonical_bytes(registry.reference(name)) != expected_reference
            ):
                raise LauncherError("native-policy-preparation-changed")
        if (
            authorization_registry.read("authorization") != authorization_raw
            or authorization_registry.reference("authorization")
            != op.sql.parse_canonical(authorization_binding.reference)
            or op.sql.canonical_bytes(
                policy.facts_from_resources(native=native, resources=resource_plan)
            )
            != canonical_facts
            or final_registry.read("policy") != canonical_facts
        ):
            raise LauncherError("native-policy-preparation-changed")
        guard()
        returning_policy = True
        return policy
    except LauncherError:
        raise
    except Exception:
        raise LauncherError("native-policy-preparation-denied") from None
    finally:
        if policy_fd is not None:
            try:
                os.close(policy_fd)
            except OSError:
                raise LauncherError("native-policy-preparation-uncertain") from None
        if returning_policy:
            # Closing our own descriptor is the final I/O. No provider refresh
            # or renewed budget may follow it, even on a successful projection.
            try:
                op.history.current_revision(authority_snapshot)
                if time.monotonic() >= preparation_deadline:
                    raise ValueError()
            except Exception:
                raise LauncherError("native-policy-preparation-changed") from None


class NativeBridgeProofSigner:
    """Fixed host signing hook and INTERNAL proof availability for one exchange.

    The factory supplies the verified apparatus, key, admitted B1/B2/process
    context and exclusive publisher. No provider is selected by RPC. Publication
    is availability only; native GOV, journal, independent heads and the client's
    complete proof qualification still decide admission. Failure consumes this
    signer; published files and durable uncertainty are retained.
    """

    def __init__(
        self,
        *,
        apparatus,
        signer,
        bridge_pin,
        action,
        exchange_id,
        expected_run_id,
        admitted,
        publish_role,
        start_nonce=None,
    ):
        import threading
        import uuid
        from types import MappingProxyType

        self.apparatus = MappingProxyType(dict(apparatus))
        self.op = self.apparatus["ex10_operational_context"]
        self.wire, self.sql = self.op.wire, self.op.sql
        self._require(
            type(bridge_pin) is self.wire.PublicKeyPin
            and type(admitted) is self.op.QualifiedHostSession
            and action in ("start", "execute", "drain", "reconcile", "finalize")
            and str(uuid.UUID(exchange_id)) == exchange_id
            and (
                type(expected_run_id) is str
                or (
                    expected_run_id is None
                    and action == "start"
                    and type(start_nonce) is str
                    and _NONCE.fullmatch(start_nonce) is not None
                )
            )
            and (start_nonce is None or action == "start")
            and callable(publish_role)
            and callable(signer.sign)
            and signer.public_key().public_bytes_raw() == bridge_pin.public_key.public_bytes_raw(),
            "native-proof-signer-context",
        )
        self.key, self.pin, self.action = signer, bridge_pin, action
        self.exchange, self.run_id, self.admitted = exchange_id, expected_run_id, admitted
        # Only the already qualified reserve body may supply the worker's
        # clock-derived identity. Capture the installed nonce independently;
        # no challenge, RPC field or later permit can select a replacement.
        start_binding = None

        def admit_start(approval, body):
            nonlocal start_binding
            if expected_run_id is not None:
                return
            identity = self.wire.qualify_run_identity(body["payload"]["run_identity"])
            if start_binding is None:
                self._require(
                    self.action == "start"
                    and self.index == 1
                    and self.sources.channel.position == 2
                    and body["operation"] == "reserve-start"
                    and body["nonce"] == identity["execution_nonce"] == start_nonce
                    and identity["git_sha"] == self._fixed()["source_revision"]
                    and identity["run_id"] == approval.context.expected_run_id
                    and approval.reservation_record is not None,
                    "native-proof-start-binding",
                )
                record = self.wire.qualify_reservation(approval.reservation_record).to_dict()
                self._require(
                    record["state"] == "PENDING"
                    and record["run_identity"] == identity
                    and record["run_id"] == identity["run_id"]
                    and record["nonce"] == body["nonce"]
                    and record["request_id"] == body["request_id"]
                    and record["request_digest"]
                    == hashlib.sha256(approval.canonical_body).hexdigest()
                    and all(record[k] == v for k, v in self._fixed().items() if k in record),
                    "native-proof-start-binding",
                )
                start_binding = (
                    identity["run_id"],
                    self.sql.canonical_bytes(identity),
                    approval,
                    approval.canonical_body,
                    approval.reservation_record,
                )
                self.run_id = identity["run_id"]
            self._require(
                self.run_id == start_binding[0]
                and self.sql.canonical_bytes(identity) == start_binding[1]
                and approval.context.expected_run_id == start_binding[0]
                and (
                    body["operation"] != "reserve-start"
                    or (
                        approval is start_binding[2]
                        and approval.canonical_body == start_binding[3]
                        and approval.reservation_record == start_binding[4]
                    )
                ),
                "native-proof-start-binding",
            )

        self._admit_start = admit_start
        self.publish_role = publish_role
        self.sources = self.journal = None
        self.index, self.failed = 0, False
        self.challenge = self.permit = None
        self.signed_challenge = self.signed_response = None
        self.approved = {}
        self.original_observation = None
        self.lock = threading.Lock()
        self.steps = (
            (
                ("host_challenge", 0, None),
                ("capability", 2, "reserve-start"),
                ("open_one_permit", 4, None),
                ("response", 4, "reserve-start"),
                ("capability", 6, "commit-open"),
                ("response", 8, "commit-open"),
            )
            if action == "start"
            else (
                ("host_challenge", 0, None),
                ("capability", 2, "inspect-reservation"),
                ("response", 4, "inspect-reservation"),
            )
        )

    @staticmethod
    def _require(condition, code="native-proof-signer-binding"):
        if not condition:
            raise LauncherError(code)

    def public_key(self):
        return self.pin.public_key

    def bind_once(self, *, sources, journal):
        self._require(self.lock.acquire(blocking=False), "native-proof-signer-busy")
        try:
            self._require(
                self.sources is None
                and not self.failed
                and type(sources) is self.op.NativeHostSources
                and type(journal) is self.op.coordination.CoordinationJournal
                and sources.signer is self
                and sources.journal is journal
                and journal.sources is sources
                and sources.pin == self.pin
                and journal.bridge_pin == self.pin,
                "native-proof-signer-bind-once",
            )
            sources.policy.postcheck(self.admitted)
            self.sources, self.journal = sources, journal
        finally:
            self.lock.release()

    def _fixed(self):
        native, sql = self.admitted, self.sql
        return {
            "exchange_id": self.exchange,
            "session_id": native.session.header["session_id"],
            "session_header_sha256": sql.parse_canonical(native.header_reference)["sha256"],
            "runtime_profile_sha256": sql.parse_canonical(native.profile_reference)["sha256"],
            "source_revision": native.profile.to_dict()["source_revision"],
            "project": native.profile.to_dict()["project"],
            "authority_id": self.journal.authority_id,
            "authority_epoch": self.journal.authority_epoch,
            "authorization_reference": sql.parse_canonical(self.sources.authorization_reference),
            "container_identity_digest": native.process.identity_digest,
            "deployment_verification_sha256": native.process.deployment_verification_sha256,
            "bridge_key_id": self.pin.key_id,
            "bridge_key_epoch": self.pin.key_epoch,
        }

    def _approval(self, digest, operation, *, registered=False):
        approval = self.sources.approvals.get(digest)
        self._require(
            type(approval) is self.op.BridgeBodyApproval
            and type(approval.context) is self.wire.RequestContext
            and hashlib.sha256(approval.canonical_body).hexdigest() == digest
            and approval.context.operation == operation
            and (not registered or any(a is approval for a in self.sources.requests.values())),
        )
        body = self.sql.parse_canonical(approval.canonical_body)
        self._require(all(body.get(k) == v for k, v in self._fixed().items() if k in body))
        self.wire._fresh(body, self.journal._now(), maximum=120, code="REQUEST_EXPIRED")
        if self.action == "start":
            self._admit_start(approval, body)
        self._require(approval.context.expected_run_id == self.run_id)
        previous = self.approved.get(operation)
        self._require(previous is None or previous is approval)
        self.approved[operation] = approval
        return approval, body

    def _record(self, state, reservation_id, *, committed):
        record = state["latest"].get(reservation_id)
        self._require(
            type(record) is dict
            and record["run_id"] == self.run_id
            and record["state"] == ("COMMITTED" if committed else "PENDING")
            and state["active"] == reservation_id,
        )
        self.wire.qualify_reservation(self.sql.canonical_bytes(record))
        self._require(all(record.get(k) == v for k, v in self._fixed().items() if k in record))
        return record

    def _receipt(self, state, record):
        reference = record["reservation_receipt_reference"]
        raw = self.journal.receipt_store.read(reference["path"])
        value = self.wire.parse_reservation_receipt_bytes(raw).to_dict()
        self._require(
            hashlib.sha256(raw).hexdigest() == reference["sha256"]
            and value["schema_version"] == reference["schema_version"]
            and value == state["witness_latest"][record["reservation_id"]]
            and value["reservation_digest"] == record["reservation_digest"]
            and value["fencing_token"] == record["fencing_token"]
            and value["state"] == record["state"],
        )
        pin = next(
            (
                p
                for p in self.journal.floor.pins
                if (p.key_id, p.key_epoch) == (value["key_id"], value["key_epoch"])
            ),
            None,
        )
        self._require(pin is not None)
        expected = {k: v for k, v in value.items() if k not in ("issued_at", "signature")}
        self.wire.verify_reservation_receipt(
            raw,
            expected_context=self.wire.ReceiptContext(
                self.sql.canonical_bytes(expected), self.journal._now()
            ),
            witness_pin=pin,
        )
        return raw

    def _proofs(self, snapshot, state, *, prefix, record=None):
        import time

        challenge = self.challenge["challenge"]
        before = time.monotonic()
        head = self.sources.publisher.transport.head_b3(challenge)
        elapsed = time.monotonic() - before
        witness = self.op.history.witness
        pins = [witness.TrustPin(raw) for raw in snapshot.canonical_trust_pins]
        pin = next(
            (
                p
                for p in pins
                if (p.record["key_id"], p.record["key_epoch"])
                == (head["key_id"], head["key_epoch"])
            ),
            None,
        )
        self._require(pin is not None)
        witness._verify_current_head(
            head,
            pin,
            challenge=challenge,
            now=self.op.coordination._utc(self.journal._now()),
            request_elapsed_seconds=elapsed,
            clock_healthy=True,
        )
        current = self.op.history.current_revision(snapshot)
        self._require(
            (
                head["authority_id"],
                head["authority_epoch"],
                head["revision"],
                head["ledger_digest"],
                head["witness_digest"],
            )
            == (
                current["authority_id"],
                current["epoch"],
                current["revision"],
                current["ledger_digest"],
                current["witness_digest"],
            ),
        )
        values = [
            (prefix + "-material", snapshot.canonical_material),
            (prefix + "-head", self.sql.canonical_bytes(head)),
        ]
        heads = [head]
        if record is not None:
            floor = self.journal.floor.current()
            before = time.monotonic()
            coord = self.journal.transport.head(challenge)
            self._require(time.monotonic() - before <= 30)
            coord = self.journal.floor.verify_head(
                coord,
                challenge=challenge,
                now=self.journal._now(),
                minimum_sequence=floor["sequence"],
                minimum_token=floor["fencing_token"],
            )
            expected = (state["sequence"], state["digest"], state["token"], state["active"])
            self._require(
                (
                    coord["sequence"],
                    coord["receipt_digest"],
                    coord["fencing_token"],
                    coord["active_reservation_id"],
                )
                == expected
                and (
                    floor["sequence"],
                    floor["receipt_digest"],
                    floor["fencing_token"],
                    floor["active_reservation_id"],
                )
                == expected,
            )
            receipt = self._receipt(state, record)
            values += [
                (prefix + "-coordination", self.sql.canonical_bytes(coord)),
                (prefix + "-receipt", receipt),
            ]
            heads.append(coord)
        self._fresh(snapshot, heads, None)
        for role, raw in values:
            self._require(self.publish_role(role, raw) is None, "native-proof-publication-verdict")
        return heads

    def _fresh(self, snapshot, heads, body):
        import time

        self.op.history.current_revision(snapshot)  # Original verified snapshot, never refreshed.
        now = self.journal._now()
        self.wire._fresh(self.challenge, now, maximum=120, code="REQUEST_EXPIRED")
        if body is not None:
            self.wire._fresh(body, now, maximum=120, code="REQUEST_EXPIRED")
        for head in heads:
            start, end = self.wire._time(head["issued_at"]), self.wire._time(head["expires_at"])
            self._require(start <= now < end <= start + timedelta(seconds=30))
        self._require(time.monotonic() < self.sources.channel.deadline, "native-proof-deadline")

    def sign(self, data):
        self._require(self.lock.acquire(blocking=False), "native-proof-signer-busy")
        try:
            self._require(
                not self.failed and self.sources is not None and self.index < len(self.steps),
                "native-proof-signer-consumed",
            )
            wire, sql = self.wire, self.sql
            self._require(type(data) is bytes and 0 < len(data) <= wire.MAX_RESPONSE_BYTES)
            matches = [
                k
                for k in ("host_challenge", "capability", "open_one_permit", "response")
                if data.startswith(wire.DOMAINS[k])
            ]
            self._require(len(matches) == 1, "native-proof-signing-domain")
            kind = matches[0]
            value = sql.parse_canonical(data[len(wire.DOMAINS[kind]) :])
            self._require("signature" not in value)
            parsed = wire._parse(
                kind,
                sql.canonical_bytes(dict(value, signature="0" * 128)),
                maximum=wire.MAX_RESPONSE_BYTES,
            )
            self._require(wire.signing_preimage(parsed) == data)
            channel = self.sources.channel
            expected_kind, position, operation = self.steps[self.index]
            self._require(
                type(channel) is self.op.PipeFrameChannel
                and channel.role == "HOST"
                and channel.exchange_id == self.exchange
                and channel.exchange == ("START" if self.action == "start" else "SINGLE_RPC")
                and channel.position == position
                and kind == expected_kind
                and (operation is None or value.get("operation") == operation),
                "native-proof-signing-order",
            )
            self._require(
                self.sources.signer is self
                and self.sources.journal is self.journal
                and self.journal.sources is self.sources
            )
            self.sources._live()
            self.sources.policy.postcheck(self.admitted)
            self.sources.policy.qualify(native=self.admitted, journal=self.journal)
            fixed = self._fixed()
            self._require(all(value[k] == v for k, v in fixed.items() if k in value))
            snapshot = self.sources.read_authority()
            state = self.journal.read()
            current = self.op.history.current_revision(snapshot)
            body, heads = None, []
            original = self.original_observation
            # The post-OPEN response has its own new revision/commit proofs;
            # no pre-OPEN observation is refreshed to rescue its expiry.
            retain_original = original is not None and operation != "commit-open"
            if original is not None and kind == "capability":
                retain_original = True
            if retain_original:
                self._fresh(original[0], original[1], None)
                previous = self.op.history.current_revision(original[0])
                self._require(
                    all(
                        current[k] == previous[k]
                        for k in ("revision", "ledger_digest", "witness_digest")
                    )
                )
            if kind == "host_challenge":
                self.challenge = value
                record = None
                if self.action != "start":
                    records = [r for r in state["latest"].values() if r["run_id"] == self.run_id]
                    self._require(len(records) == 1)
                    record = self._record(state, records[0]["reservation_id"], committed=True)
                heads = self._proofs(
                    snapshot,
                    state,
                    prefix="base" if self.action == "start" else "action",
                    record=record,
                )
            elif kind == "capability":
                approval, body = self._approval(value["request_body_digest"], operation)
                context = approval.context
                expected = {
                    "role": "SESSION_EXECUTOR",
                    "namespace": "S11C3-EX10",
                    "request_nonce": body["nonce"],
                    "request_id": body["request_id"],
                    "expected_revision": context.authority_revision,
                    "expected_run_id": context.expected_run_id,
                    "reservation_id": context.reservation_id,
                    "reservation_digest": context.reservation_digest,
                    "fencing_token": context.fencing_token,
                    "holder_public_key_hex": context.holder_public_key_hex,
                    "expires_at": body["expires_at"],
                }
                self._require(
                    all(value[k] == v for k, v in expected.items())
                    and current["revision"] == context.authority_revision
                )
            elif kind == "open_one_permit":
                approval, body = self._approval(
                    value["reserve_request_digest"], "reserve-start", registered=True
                )
                record = self._record(state, value["reservation_id"], committed=False)
                expected = {
                    "base_authority_revision": record["previous_authority_revision"],
                    "expected_open_revision": record["expected_next_revision"],
                    "expires_at": record["freshness_deadline"],
                    "holder_public_key_hex": approval.context.holder_public_key_hex,
                    "preflight_reference": sql.parse_canonical(approval.preflight_reference),
                    "reserve_request_id": body["request_id"],
                    "attempt_nonce": body["nonce"],
                    **{
                        k: record[k]
                        for k in (
                            "reservation_id",
                            "reservation_digest",
                            "reservation_receipt_reference",
                            "fencing_token",
                            "run_identity",
                            "acknowledgment_digest",
                        )
                    },
                }
                self._require(
                    all(value[k] == v for k, v in expected.items())
                    and value["permit_digest"] == wire.open_permit_digest(parsed)
                    and current["revision"] == record["previous_authority_revision"]
                )
                self._receipt(state, record)
            else:
                approval, body = self._approval(value["request_digest"], operation, registered=True)
                self._require(
                    all(
                        value[k] == body[k]
                        for k in (
                            "request_id",
                            "nonce",
                            "exchange_id",
                            "operation",
                            "session_id",
                            "runtime_profile_sha256",
                            "authority_id",
                            "authority_epoch",
                        )
                    )
                    and value["status"] == "OK"
                    and value["uncertain"] is False
                    and value["run_id"] == self.run_id
                    and wire.result_digest(parsed) == value["result_digest"]
                    and wire.response_digest(parsed) == value["response_digest"]
                )
                record = self._record(
                    state, value["reservation_id"], committed=operation != "reserve-start"
                )
                self._require(value["result"]["reservation"] == record)
                if operation == "reserve-start":
                    grant = self.sources.grants.get(record["reservation_id"])
                    self._require(grant is not None and grant["permit"].to_dict() == self.permit)
                    expected = {
                        "open_permit": self.permit,
                        "reservation": record,
                        "reservation_receipt": sql.parse_canonical(self._receipt(state, record)),
                    }
                    witness_head = body["payload"]["expected_head"]
                    before = after = record["previous_authority_revision"]
                elif operation == "commit-open":
                    reserve = self.approved["reserve-start"].context
                    self._require(
                        approval.context.request_id != reserve.request_id
                        and approval.context.nonce != reserve.nonce
                    )
                    expected = {
                        "open_evidence": body["payload"]["open_evidence"],
                        "open_witness": record["open_witness_reference"],
                        "opened": True,
                        "reservation": record,
                    }
                    witness_head = record["open_witness_reference"]
                    before, after = (
                        record["previous_authority_revision"],
                        record["expected_next_revision"],
                    )
                    self._require(
                        after == before + 1
                        and (current["revision"], current["witness_digest"])
                        == (after, witness_head["witness_digest"])
                    )
                else:
                    claim = state["claims"].get(body["payload"]["original_request_id"])
                    self._require(
                        claim is not None and claim["reservation_id"] == record["reservation_id"]
                    )
                    expected = {
                        "reservation": record,
                        "original_response_reference": self.sources.saved_response_reference(
                            state=state, request_id=body["payload"]["original_request_id"]
                        ),
                    }
                    witness_head = self.sources._base(snapshot, require_configured=False)
                    before = after = current["revision"]
                self._require(
                    value["result"] == expected
                    and value["witness_head"] == witness_head
                    and value["authority_revision_before"] == before
                    and value["authority_revision_after"] == after
                    and value["coordination_head"] is None
                )
            if kind == "response" and operation == "commit-open":
                heads = self._proofs(snapshot, state, prefix="commit", record=record)
            if kind == "host_challenge":
                self.original_observation = (snapshot, tuple(heads))
            self.sources.policy.postcheck(self.admitted)
            self.sources._live()
            if retain_original:
                self._fresh(original[0], original[1], body)
            self._fresh(snapshot, heads, body)
            signature = self.key.sign(data)
            self._require(
                type(signature) is bytes and len(signature) == 64, "native-proof-signature-invalid"
            )
            self.pin.public_key.verify(signature, data)
            # Signing is an external fixed provider: its IO/delay cannot bypass
            # the admitted profile/session/process lease or original clocks.
            self.sources.policy.qualify(native=self.admitted, journal=self.journal)
            self.sources._live()
            if body is not None:
                # Fixed providers may perform IO while signing. Retain the
                # same admitted identity/body through the final signature gate.
                self._approval(
                    hashlib.sha256(sql.canonical_bytes(body)).hexdigest(),
                    body["operation"],
                    registered=kind in ("open_one_permit", "response"),
                )
            if retain_original:
                self._fresh(original[0], original[1], body)
            self._fresh(snapshot, heads, body)
            if kind == "open_one_permit":
                self.permit = dict(value, signature=signature.hex())
            if kind == "host_challenge":
                self.signed_challenge = sql.canonical_bytes(dict(value, signature=signature.hex()))
            elif kind == "response":
                self.signed_response = sql.canonical_bytes(dict(value, signature=signature.hex()))
            self.index += 1
            return signature
        except BaseException:
            self.failed = True
            raise
        finally:
            self.lock.release()


class NativeArmedArtifactReader:
    """Read the existing authenticated ARMED marker without custody mutation.

    This only observes availability and the original issuance lower bound. The
    emission producer and executor still independently qualify full current
    authority, governance, coordination, process and deployment. The existing
    shared custody lock is released before yielding or doing any remote IO.
    """

    def __init__(self, *, apparatus, admitted, run_id, custody_hmac_key, deadline):
        import math
        import time
        import uuid
        from types import MappingProxyType

        self.apparatus = MappingProxyType(dict(apparatus))
        self.op = self.apparatus["ex10_operational_context"]
        self.custody = self.apparatus["custody_policy"]
        self._require(
            type(admitted) is self.op.QualifiedHostSession
            and type(run_id) is str
            and run_id.startswith("s11-")
            and str(uuid.UUID(run_id[4:])) == run_id[4:]
            and type(custody_hmac_key) is bytes
            and len(custody_hmac_key) >= 32
            and type(deadline) in (int, float)
            and math.isfinite(deadline)
            and time.monotonic() < deadline <= time.monotonic() + 900,
            "native-armed-context",
        )
        profile = admitted.profile.to_dict()
        root = profile["host_roots"]["evidence"]
        self.root, self.owner, self.group = root["path"], root["owner_uid"], root["owner_gid"]
        self._require(
            (os.geteuid(), os.getegid()) == (self.owner, self.group), "native-armed-owner"
        )
        self.admitted = admitted
        self.run_id, self.key, self.deadline = run_id, custody_hmac_key, deadline
        self.index = "runs/" + run_id + "/metadata/artifacts.jsonl"
        self.signature = self.index + ".hmac"
        scenario = self.op.evidence.ex10_load.SCENARIO_ID
        self.artifact = "runs/" + run_id + "/metadata/" + scenario + "/ex10-emission-armed.json"

    @staticmethod
    def _require(condition, code="native-armed-binding"):
        if not condition:
            raise LauncherError(code)

    def _check_deadline(self):
        import time

        self._require(time.monotonic() < self.deadline, "native-armed-deadline")

    def _guards(self, resolver, guards):
        resolver._check_lease()
        self._require(os.fstat(resolver._root_fd).st_gid == self.group, "native-armed-owner")
        for parent, name, child, expected in guards:
            current = os.fstat(child)
            mapped = os.stat(name, dir_fd=parent, follow_symlinks=False)
            self._require(current.st_gid == mapped.st_gid == self.group, "native-armed-owner")
            if stat.S_ISDIR(current.st_mode):
                resolver._private_directory(current)
                self._require(
                    (mapped.st_dev, mapped.st_ino)
                    == (current.st_dev, current.st_ino)
                    == expected[:2],
                    "native-armed-lease",
                )
            else:
                self._require(
                    self.op.evidence._stamp(current) == self.op.evidence._stamp(mapped) == expected,
                    "native-armed-lease",
                )

    def _inventory(self, resolver, stack):
        guards = []
        raw, identity = resolver._read(self.index, 16 * 1024 * 1024, stack, guards)
        signature, _ = resolver._read(self.signature, 65, stack, guards)
        self._require(
            re.fullmatch(rb"[0-9a-f]{64}\n", signature) is not None, "native-armed-index-signature"
        )
        self.custody.validate_custody_index_signature(
            raw,
            signature[:-1].decode("ascii"),
            custody_hmac_key=self.key,
        )
        records, paths, ids = [], set(), set()
        for line in raw.splitlines(keepends=True):
            record = self.op.sql.parse_canonical(line)
            self._require(type(record) is dict and record["run_id"] == self.run_id)
            self.custody.validate_custody_record_signature(record, custody_hmac_key=self.key)
            self._require(
                record["artifact_id"] not in ids and record["relative_path"] not in paths,
                "native-armed-duplicate",
            )
            ids.add(record["artifact_id"])
            paths.add(record["relative_path"])
            records.append(record)
        self._guards(resolver, guards)
        return raw, identity, records

    @contextlib.contextmanager
    def capture(self):
        """Yield None while absent/busy, or exact existing bytes plus a fixed lease check."""
        evidence = self.op.evidence
        self._check_deadline()
        with evidence.ConfinedArtifactResolver(self.root, expected_owner=self.owner) as resolver:
            fd = os.open(
                ".s11-custody.lock",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=resolver._root_fd,
            )
            try:
                initial = os.fstat(fd)

                def check_lock():
                    resolver._check_lease()
                    current = os.fstat(fd)
                    mapped = os.stat(
                        ".s11-custody.lock", dir_fd=resolver._root_fd, follow_symlinks=False
                    )
                    self._require(
                        stat.S_ISREG(current.st_mode)
                        and current.st_nlink == 1
                        and (current.st_uid, current.st_gid) == (self.owner, self.group)
                        and stat.S_IMODE(current.st_mode) == 0o600
                        and (current.st_dev, current.st_ino)
                        == (initial.st_dev, initial.st_ino)
                        == (mapped.st_dev, mapped.st_ino),
                        "native-armed-custody-lock",
                    )

                def unlock():
                    try:
                        check_lock()
                    finally:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    self._check_deadline()

                def lock():
                    self._check_deadline()
                    check_lock()
                    try:
                        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    except BlockingIOError:
                        self._check_deadline()
                        return False
                    try:
                        check_lock()
                        self._check_deadline()
                    except BaseException:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                        raise
                    return True

                if not lock():
                    self._check_deadline()
                    yield None
                    return
                with contextlib.ExitStack() as retained:
                    try:
                        prefix, index_identity, records = self._inventory(resolver, retained)
                        armed = [r for r in records if r["artifact_type"] == "ex10-emission-armed"]
                        self._require(len(armed) <= 1, "native-armed-duplicate")
                        if not armed:
                            observation = None
                        else:
                            record = armed[0]
                            scenario = self.op.evidence.ex10_load.SCENARIO_ID
                            self._require(
                                record["scenario_id"] == scenario
                                and record["artifact_id"]
                                == "S11-" + scenario + "-" + self.run_id + "-ex10-emission-armed"
                                and record["kind"] == "metadata"
                                and record["classification"] == "INTERNAL"
                                and record["mime_type"] == "application/json"
                                and record["source"] == "s11_ex10.execute"
                                and record["relative_path"]
                                == "metadata/" + scenario + "/ex10-emission-armed.json",
                            )
                            guards = []
                            raw, _ = resolver._read(
                                self.artifact, evidence.MAX_ARTIFACT_BYTES, retained, guards
                            )
                            self._require(
                                type(record["size_bytes"]) is int
                                and len(raw) == record["size_bytes"]
                                and hashlib.sha256(raw).hexdigest() == record["sha256"],
                            )
                            value = self.op.sql.parse_canonical(raw)
                            self._require(
                                set(value)
                                == {
                                    "schema_version",
                                    "scenario_id",
                                    "run_id",
                                    "recorded_at",
                                    "requests_prepared",
                                    "tokens_verified",
                                }
                            )
                            self._require(
                                value["schema_version"] == "s11c3-ex10-emission-armed-v1"
                                and value["scenario_id"] == scenario
                                and value["run_id"] == self.run_id
                                and type(value["requests_prepared"]) is int
                                and value["requests_prepared"] == evidence.ex10_load.TOTAL_REQUESTS
                                and type(value["tokens_verified"]) is int
                                and value["tokens_verified"] == evidence.ex10_load.VIRTUAL_USERS,
                            )
                            self.op.wire._time(value["recorded_at"])
                            self._guards(resolver, guards)

                            def recheck():
                                self._require(lock(), "native-armed-custody-busy")
                                try:
                                    self._guards(resolver, guards)
                                    with contextlib.ExitStack() as current_stack:
                                        current, identity, inventory = self._inventory(
                                            resolver, current_stack
                                        )
                                    self._require(
                                        identity == index_identity and current.startswith(prefix),
                                        "native-armed-inventory-prefix",
                                    )
                                    current_armed = [
                                        r
                                        for r in inventory
                                        if r["artifact_type"] == "ex10-emission-armed"
                                    ]
                                    self._require(current_armed == [record])
                                    self._guards(resolver, guards)
                                    self._check_deadline()
                                finally:
                                    unlock()
                                return None

                            observation = (raw, recheck)
                        self._check_deadline()
                    finally:
                        unlock()
                    yield observation
            finally:
                os.close(fd)


class NativeTerminalArtifactReader:
    """Qualify existing new-finalization evidence using confined original bytes.

    The private result and native admission are fixed host inputs. This only
    verifies signed custody inventory, decision, final manifest and its seal.
    The historical branch without a final manifest is outside this reader.
    Existing ND custody errors remain data, never a new scientific evaluation.
    Publication, real worker/bridge completion and RETIRE remain separate gates.
    """

    def __init__(
        self,
        *,
        apparatus,
        admitted,
        action,
        expected_run_id,
        result_bytes,
        custody_hmac_key,
        deadline,
        reconciled=None,
    ):
        import threading
        from types import MappingProxyType

        self.apparatus = MappingProxyType(dict(apparatus))
        self.op, self.custody = (
            self.apparatus["ex10_operational_context"],
            self.apparatus["custody_policy"],
        )
        self.manifest = self.apparatus["manifest"]
        self._require(action in ("finalize", "abandon-official-pre-t0", "declare-executor-gone"))
        self._require(
            type(result_bytes) is bytes and 0 < len(result_bytes) <= self.op.wire.MAX_REQUEST_BYTES
        )
        try:
            result = self.op.sql.parse_canonical(result_bytes)
            self._require(type(result) is dict)
            problems, _, _ = action_problems(
                action, result, run_id=expected_run_id, reconciled=reconciled
            )
            self._require(not problems and set(result) == _FINAL_KEYS)
        except Exception:
            raise LauncherError("native-terminal-result-invalid") from None
        self.result_bytes, self.action, self.run_id = result_bytes, action, expected_run_id
        self.reader = NativeArmedArtifactReader(
            apparatus=apparatus,
            admitted=admitted,
            run_id=expected_run_id,
            custody_hmac_key=custody_hmac_key,
            deadline=deadline,
        )
        self.admitted, self.deadline, self.key = admitted, deadline, custody_hmac_key
        self.root, self.owner, self.group = self.reader.root, self.reader.owner, self.reader.group
        self.lock, self.failed = threading.Lock(), False

    @staticmethod
    def _require(condition, code="native-terminal-binding"):
        if not condition:
            raise LauncherError(code)

    def _deadline(self):
        import time

        self._require(
            not self.failed
            and self.reader.deadline == self.deadline
            and time.monotonic() < self.deadline,
            "native-terminal-deadline",
        )

    @contextlib.contextmanager
    def _shared(self, resolver):
        """Existing custody lock only; release before any authority or publication IO."""
        self._deadline()
        fd = os.open(
            ".s11-custody.lock",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=resolver._root_fd,
        )
        locked = False
        try:
            initial = os.fstat(fd)

            def check():
                resolver._check_lease()
                current = os.fstat(fd)
                mapped = os.stat(
                    ".s11-custody.lock", dir_fd=resolver._root_fd, follow_symlinks=False
                )
                self._require(
                    stat.S_ISREG(current.st_mode)
                    and current.st_nlink == 1
                    and (current.st_uid, current.st_gid) == (self.owner, self.group)
                    and stat.S_IMODE(current.st_mode) == 0o600
                    and (current.st_dev, current.st_ino)
                    == (initial.st_dev, initial.st_ino)
                    == (mapped.st_dev, mapped.st_ino),
                    "native-terminal-custody-lock",
                )

            check()
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                self._deadline()
                raise LauncherBusy() from None
            locked = True
            check()
            self._deadline()
            yield
            check()
            self._deadline()
        finally:
            try:
                if locked:
                    try:
                        check()
                    finally:
                        fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
            self._deadline()

    def _record(
        self, resolver, retained, guards, records, *, kind, artifact_type, classification, filename
    ):
        matches = [r for r in records if r["artifact_type"] == artifact_type]
        self._require(len(matches) == 1, "native-terminal-required-artifact")
        record = matches[0]
        relative = kind + "/EX-10/" + filename
        self._require(
            record["run_id"] == self.run_id
            and record["scenario_id"] == "EX-10"
            and record["artifact_id"] == "S11-EX-10-" + self.run_id + "-" + artifact_type
            and record["kind"] == kind
            and record["classification"] == classification
            and record["mime_type"] == "application/json"
            and record["source"] == "s11_ex10.finalize"
            and record["relative_path"] == relative,
            "native-terminal-required-artifact",
        )
        raw, _ = resolver._read(
            "runs/" + self.run_id + "/" + relative,
            self.op.evidence.MAX_ARTIFACT_BYTES,
            retained,
            guards,
        )
        self._require(
            type(record["size_bytes"]) is int
            and len(raw) == record["size_bytes"]
            and hashlib.sha256(raw).hexdigest() == record["sha256"],
            "native-terminal-artifact-hash",
        )
        value = self.op.sql.parse_canonical(raw)
        self._require(type(value) is dict)
        return record, value

    def _terminal(self, resolver, retained, guards, records):
        result = self.op.sql.parse_canonical(self.result_bytes)
        decision_record, decision = self._record(
            resolver,
            retained,
            guards,
            records,
            kind="derived",
            artifact_type="ex10-decision",
            classification="EXPERIMENTAL",
            filename="ex10-decision.json",
        )
        final_record, final = self._record(
            resolver,
            retained,
            guards,
            records,
            kind="metadata",
            artifact_type="run-manifest-final",
            classification="INTERNAL",
            filename="run-manifest.final.json",
        )
        self._require(
            type(decision["binding"]) is bool
            and decision["evidence_mode"] == EVIDENCE_MODE
            and decision["schema_version"] == self.op.evidence.ex10_load.DECISION_SCHEMA_VERSION
            and decision["protocol_id"] == self.op.evidence.ex10_load.PROTOCOL_ID
            and decision["methodology_version"] == self.op.evidence.ex10_load.METHODOLOGY_VERSION
            and decision["scenario_id"] == "EX-10"
            and decision["dataset_id"] == self.op.evidence.ex10_load.DATASET_ID
            and decision["decision_reference"] == self.op.evidence.ex10_load.DECISION_REFERENCE
            and all(
                decision[k] == result[k]
                for k in (
                    "run_id",
                    "decision",
                    "binding",
                    "lifecycle",
                    "reasons",
                    "manifest_status",
                )
            ),
            "native-terminal-decision",
        )
        self._require(
            decision_record["sha256"] == result["decision_sha256"]
            and final_record["sha256"] == result["manifest_sha256"],
            "native-terminal-artifact-hash",
        )
        binding = result["decision"] in ("PASS", "FAIL")
        status = "COMPLETED" if binding else "INVALID"
        self._require(
            type(result["binding"]) is bool
            and result["binding"] is binding
            and result["manifest_status"] == status
            and final["status"] == status,
            "native-terminal-decision",
        )
        references = tuple(
            self.manifest.ArtifactReference(**ref) for ref in final["artifact_references"]
        )
        checked = self.manifest.RunManifest(**dict(final, artifact_references=references))
        self.manifest.validate_run_manifest(checked)
        self._require(
            checked.run_id == self.run_id
            and checked.dataset_id == decision["dataset_id"]
            and checked.dataset_sha256 == decision["dataset_sha256"]
            and checked.scenario_id == "EX-10"
            and checked.experiment_id == "S11C3-EX10"
            and checked.git_sha == self.admitted.profile.to_dict()["source_revision"]
            and checked.configuration["evidence_mode"] == EVIDENCE_MODE
            and checked.configuration["plan_sha256"] == decision["plan_sha256"],
            "native-terminal-manifest",
        )
        expected_refs = [
            {k: r[k] for k in ("artifact_id", "relative_path", "sha256")}
            for r in records
            if r is not final_record
        ]
        self._require(final["artifact_references"] == expected_refs, "native-terminal-inventory")
        raw, _ = resolver._read(
            "runs/" + self.run_id + "/.s11-finalized",
            self.op.evidence.MAX_ARTIFACT_BYTES,
            retained,
            guards,
        )
        seal = self.op.sql.parse_canonical(raw)
        self._require(
            type(seal) is dict and set(seal) == {"payload", "signature"}, "native-terminal-seal"
        )
        self.custody.validate_custody_seal_signature(
            self.op.sql.canonical_bytes(seal["payload"]),
            seal["signature"],
            custody_hmac_key=self.key,
        )
        expected = {
            "artifact_id": final_record["artifact_id"],
            "finished_at": checked.finished_at,
            "manifest_sha256": final_record["sha256"],
            "run_id": self.run_id,
        }
        self._require(seal["payload"] == expected, "native-terminal-seal")
        self.op.wire._time(checked.finished_at)
        return {
            "path": self.root + "/runs/" + self.run_id + "/" + final_record["relative_path"],
            "sha256": final_record["sha256"],
            "schema_version": checked.schema_version,
        }, result

    @contextlib.contextmanager
    def capture(self):
        if not self.lock.acquire(blocking=False):
            raise LauncherBusy()
        evidence = self.op.evidence
        try:
            self._deadline()
            with evidence.ConfinedArtifactResolver(
                self.root, expected_owner=self.owner
            ) as resolver:
                with contextlib.ExitStack() as retained:
                    with self._shared(resolver):
                        index, identity, records = self.reader._inventory(resolver, retained)
                        guards = []
                        current, _ = resolver._read(
                            self.reader.index, 16 * 1024 * 1024, retained, guards
                        )
                        resolver._read(self.reader.signature, 65, retained, guards)
                        self._require(current == index, "native-terminal-inventory")
                        reference, result = self._terminal(resolver, retained, guards, records)
                        self.reader._guards(resolver, guards)
                        reference_bytes = self.op.sql.canonical_bytes(reference)
                        result_bytes = self.op.sql.canonical_bytes(result)

                    def recheck():
                        with self._shared(resolver):
                            self.reader._guards(resolver, guards)
                            with contextlib.ExitStack() as fresh:
                                current, observed, _ = self.reader._inventory(resolver, fresh)
                            self._require(
                                current == index and observed == identity,
                                "native-terminal-inventory",
                            )
                            self._require(
                                self.op.sql.canonical_bytes(reference) == reference_bytes
                                and self.op.sql.canonical_bytes(result) == result_bytes
                                and self.result_bytes == result_bytes,
                                "native-terminal-binding",
                            )
                            self.reader._guards(resolver, guards)
                        self._deadline()
                        return None

                    recheck()
                    yield reference, result, recheck
                    recheck()
            self._deadline()
        except LauncherError:
            self.failed = True
            raise
        except Exception:
            self.failed = True
            raise LauncherError("native-terminal-evidence-invalid") from None
        except BaseException:
            self.failed = True
            raise
        finally:
            self.lock.release()


class NativeTerminalLifecycleSource:
    """One fixed host route to the actual post-EOF terminal publisher."""

    __slots__ = ("_guard", "_bind", "_bound", "_qualify")

    def __init__(self):
        target = []

        def guard():
            if (
                self._guard is not guard
                or self._bind is not bind
                or self._bound is not bound
                or self._qualify is not qualify
            ):
                raise LauncherError("native-terminal-lifecycle-changed")

        def bind(publisher):
            guard()
            if (
                target
                or type(publisher) is not NativeLifecycleTerminalPublisher
                or publisher.sources.lifecycle is not self
                or publisher.used
            ):
                raise LauncherError("native-terminal-lifecycle-binding")
            publisher._graph(require_lifecycle=False)
            target.append(publisher)
            guard()

        def bound(publisher):
            guard()
            return len(target) == 1 and target[0] is publisher

        def qualify(**facts):
            guard()
            if len(target) != 1:
                raise LauncherError("native-terminal-lifecycle-unbound")
            publisher = target[0]
            if publisher.sources.lifecycle is not self:
                raise LauncherError("native-terminal-lifecycle-changed")
            result = publisher.qualify_terminal(**facts)
            guard()
            return result

        self._guard, self._bind, self._bound, self._qualify = guard, bind, bound, qualify

    def bind_once(self, publisher):
        self._bind(publisher)

    def is_bound_to(self, publisher):
        return self._bound(publisher)

    def qualify_terminal(self, **facts):
        return self._qualify(**facts)


class NativeTerminalDispatchTransport:
    """Fixed transport identity with one exact, host-bound retirement attempt."""

    __slots__ = ("_guard", "_head", "_reserve", "_transition", "_arm")

    def __init__(self, remote):
        if not all(
            callable(getattr(remote, name, None)) for name in ("head", "reserve", "transition")
        ):
            raise LauncherError("native-terminal-transport-invalid")
        raw_head, raw_reserve, raw_transition = remote.head, remote.reserve, remote.transition
        admission = []
        consumed = []

        def guard():
            if (
                self._guard is not guard
                or self._head is not head
                or self._reserve is not reserve
                or self._transition is not transition
                or self._arm is not arm
            ):
                raise LauncherError("native-terminal-transport-changed")

        def head(challenge):
            guard()
            result = raw_head(challenge)
            guard()
            return result

        def reserve(*args, **kwargs):
            guard()
            if admission:
                raise LauncherError("native-terminal-retirement-operation")
            result = raw_reserve(*args, **kwargs)
            guard()
            return result

        def arm(*, owner, writer, record, result, before, qualified, witness_reference):
            guard()
            if (
                admission
                or type(owner) is not NativeLifecycleTerminalPublisher
                or owner.remote_b4 is not self
                or owner.journal.transport is not self
                or not owner.used
                or not owner.running
                or owner.published is not qualified
            ):
                raise LauncherError("native-terminal-retirement-binding")
            op = owner.op
            owner._graph()
            writer.check()
            original_check = owner._check
            record_bytes, result_bytes = (
                op.sql.canonical_bytes(record),
                op.sql.canonical_bytes(result),
            )
            if owner.record != record_bytes or owner.reader.result_bytes != result_bytes:
                raise LauncherError("native-terminal-retirement-binding")
            current = owner.journal.read()
            expected = op.sql.canonical_bytes(
                {
                    "reservation_id": record["reservation_id"],
                    "transition": "RETIRE_TERMINAL",
                    "operation_id": owner.retire_id,
                    "expected_sequence": current["sequence"],
                    "expected_receipt_digest": current["digest"],
                    "open_witness_digest": record["open_witness_reference"]["witness_digest"],
                    "terminal_witness_digest": witness_reference["witness_digest"],
                    "recovery_approval": None,
                }
            )

            def check():
                guard()
                if owner.journal.transport is not self or owner.remote_b4 is not self:
                    raise LauncherError("native-terminal-transport-changed")
                original_check(
                    writer=writer,
                    record=op.sql.parse_canonical(record_bytes),
                    result=op.sql.parse_canonical(result_bytes),
                )
                writer.check()
                op.history.current_revision(qualified)
                op.history.current_revision(before)
                owner.reader._deadline()
                guard()

            check()
            admission.append((expected, check, op.sql))
            guard()

        def transition(**kwargs):
            guard()
            if kwargs.get("transition") != "RETIRE_TERMINAL":
                if admission:
                    raise LauncherError("native-terminal-retirement-operation")
                result = raw_transition(**kwargs)
                guard()
                return result
            if len(admission) != 1 or consumed:
                raise LauncherError("native-terminal-retirement-operation")
            consumed.append(True)
            expected, check, sql = admission[0]
            if sql.canonical_bytes(kwargs) != expected:
                raise LauncherError("native-terminal-retirement-operation")
            # The original journal already durably recorded this action. This
            # final host check runs after its head/SQL IO and before the one CAS.
            check()
            result = raw_transition(**kwargs)
            guard()
            return result

        self._guard, self._head, self._reserve, self._transition, self._arm = (
            guard,
            head,
            reserve,
            transition,
            arm,
        )

    def head(self, challenge):
        return self._head(challenge)

    def reserve(self, *args, **kwargs):
        return self._reserve(*args, **kwargs)

    def transition(self, **kwargs):
        return self._transition(**kwargs)

    def arm_terminal(self, **facts):
        return self._arm(**facts)


class NativeLifecycleTerminalPublisher:
    """Publish a qualified sealed terminal under the journal's existing writer.

    The signed SINGLE response proves provenance, never terminal permission.
    Fixed action and finished-worker providers must qualify their existing
    independent contracts before both publication checks. The real factory
    must bind actual Docker client completion, PID1 exit and private result;
    callbacks are not selected by RPC. Uncertainty consumes this attempt.
    """

    def __init__(self, *, broker, reader, approve_terminal, verify_worker):
        import threading
        import uuid

        self._require(type(reader) is NativeTerminalArtifactReader)
        op = reader.op
        self._require(
            type(broker) is op.NativeBridgeBroker
            and type(broker.signer) is NativeBridgeProofSigner
            and broker.signer.action == "finalize"
            and broker.signer.run_id == reader.run_id
            and callable(approve_terminal)
            and callable(verify_worker),
            "native-terminal-publication-context",
        )
        self.op, self.reader, self.broker = op, reader, broker
        self.sources, self.signer, self.journal = broker.sources, broker.signer, broker.journal
        self.approve, self.worker = approve_terminal, verify_worker
        self.publisher = self.sources.publisher
        self.remote_b3, self.remote_b4 = self.publisher.transport, self.journal.transport
        self._require(
            self.sources.policy._resource_plan is None
            or type(self.remote_b4) is NativeTerminalDispatchTransport,
            "native-terminal-fixed-transport-required",
        )
        self.retirement_transport = None
        self.response = self.signer.signed_response
        self._require(type(self.response) is bytes)
        self.provenance = op.wire.parse_response_bytes(self.response).to_dict()
        self.record = op.sql.canonical_bytes(self.provenance["result"]["reservation"])
        self.publication_id, self.retire_id = str(uuid.uuid4()), str(uuid.uuid4())
        self.lock, self.used, self.running = threading.Lock(), False, False
        self.observation = self.original = self.published = None
        self.verification_count = 0
        self._graph(require_lifecycle=False)

    @staticmethod
    def _require(condition, code="native-terminal-publication-binding"):
        if not condition:
            raise LauncherError(code)

    def _graph(self, *, require_lifecycle=True):
        op, source, signer = self.op, self.sources, self.signer
        channel = source.channel
        publisher, policy = source.publisher, source.policy
        self._require(
            type(publisher) is op.NativeOpenPublisher
            and publisher is self.publisher
            and publisher.transport is self.remote_b3
            and (
                self.journal.transport is self.remote_b4
                or self.journal.transport is self.retirement_transport
            )
            and (
                publisher.ledger.root,
                publisher.ledger.identity,
                publisher.name,
                publisher.identity,
            )
            == (policy.ledger.root, policy.ledger.identity, policy.name, policy.identity),
            "native-terminal-publication-storage",
        )
        self._require(
            type(source) is op.NativeHostSources
            and self.broker.sources is source
            and self.broker.signer is signer
            and self.broker.journal is self.journal
            and source.signer is signer
            and source.journal is self.journal
            and self.journal.sources is source
            and signer.sources is source
            and signer.journal is self.journal
            and source.pin == signer.pin == self.journal.bridge_pin
            and self.broker.used
            and self.broker.role == "SESSION_EXECUTOR"
            and not signer.failed
            and signer.index == 3
            and signer.signed_response == self.response
            and op.sql.canonical_bytes(self.provenance) == self.response
            and type(channel) is op.PipeFrameChannel
            and channel.role == "HOST"
            and channel.exchange == "SINGLE_RPC"
            and channel.exchange_id == signer.exchange
            and channel.position == 5
            and channel.closed
            and signer.admitted.profile._bytes == self.reader.admitted.profile._bytes
            and signer.admitted.session._bytes == self.reader.admitted.session._bytes
            and (
                not require_lifecycle
                or source.lifecycle is self
                or type(source.lifecycle) is NativeTerminalLifecycleSource
                and source.lifecycle.is_bound_to(self)
            ),
            "native-terminal-publication-context",
        )
        source.policy.postcheck(signer.admitted)
        source.policy.qualify(native=signer.admitted, journal=self.journal)
        self.reader._deadline()

    def _provenance(self, state):
        wire, sql, signer = self.op.wire, self.op.sql, self.signer
        value = wire.parse_response_bytes(self.response).to_dict()
        wire._verify_pin(value, signer.pin, bridge=True)
        wire._verify_signature("response", value, signer.pin.public_key)
        approval = signer.approved.get("inspect-reservation")
        self._require(
            type(approval) is self.op.BridgeBodyApproval
            and type(approval.context) is wire.RequestContext
            and any(a is approval for a in self.sources.requests.values())
            and approval.context.expected_run_id == self.reader.run_id,
        )
        body = sql.parse_canonical(approval.canonical_body)
        for key in (
            "request_id",
            "nonce",
            "exchange_id",
            "operation",
            "session_id",
            "runtime_profile_sha256",
            "authority_id",
            "authority_epoch",
        ):
            self._require(value[key] == body[key])
        self._require(
            value["request_digest"] == hashlib.sha256(approval.canonical_body).hexdigest()
            and value["operation"] == "inspect-reservation"
            and value["status"] == "OK"
            and value["uncertain"] is False
            and value["run_id"] == self.reader.run_id
            and value["result_digest"]
            == wire.result_digest(wire.parse_response_bytes(self.response))
            and value["response_digest"]
            == wire.response_digest(wire.parse_response_bytes(self.response))
            and all(body[k] == v for k, v in signer._fixed().items() if k in body),
        )
        record = sql.parse_canonical(self.record)
        self._require(
            record["state"] == "COMMITTED"
            and record["run_id"] == self.reader.run_id
            and value["reservation_id"] == record["reservation_id"]
            and state["latest"].get(record["reservation_id"]) == record
            and state["active"] == record["reservation_id"]
            and value["result"]
            == {
                "reservation": record,
                "original_response_reference": self.sources.saved_response_reference(
                    state=state, request_id=body["payload"]["original_request_id"]
                ),
            },
        )
        wire.qualify_reservation(self.record)
        return record

    def _check(self, *, writer, record, result):
        self._graph()
        self._require(
            type(writer) is self.op.durable.HeldWriter
            and writer.store is self.journal.lock_store
            and writer.name == self.journal.lock_name
            and writer.identity == self.journal.lock_identity,
            "native-terminal-publication-writer",
        )
        writer.check()
        self._require(self._provenance(self.journal.read()) == record)
        try:
            accepted = self.worker(
                result_bytes=self.reader.result_bytes,
                action=self.reader.action,
                admitted=self.signer.admitted,
                exchange_id=self.signer.exchange,
            )
            self._require(accepted is None, "native-terminal-worker-required")
            accepted = self.approve(
                action=self.reader.action,
                admitted=self.signer.admitted,
                record=record,
                result=result,
                writer=writer,
            )
            self._require(accepted is None, "native-terminal-action-approval-required")
        except LauncherError:
            raise
        except Exception:
            raise LauncherError("native-terminal-action-or-worker-required") from None
        self._require(self.op.sql.canonical_bytes(record) == self.record)
        self.observation[2]()
        self._graph()
        writer.check()
        if self.original is not None:
            self.op.history.current_revision(self.original)
        self.reader._deadline()

    def qualify_terminal(self, *, operation_id, record, writer):
        op, sql, publisher = self.op, self.op.sql, self.sources.publisher
        self._require(self.running and operation_id == self.retire_id and self.published is None)
        self._require(sql.canonical_bytes(record) == self.record)
        reference, result, recheck = self.observation
        self._check(writer=writer, record=record, result=result)
        before = self.sources.read_authority()
        self.original = before
        base = op.history.current_revision(before)
        self._require(
            base["revision"]
            == self.provenance["authority_revision_after"]
            == self.provenance["authority_revision_before"]
        )
        self._require(type(publisher) is op.NativeOpenPublisher)
        now = publisher.clock()
        self._require(publisher.health() is True and type(now) is datetime and now.tzinfo == UTC)
        issued, revision = op.coordination._utc(now), base["revision"] + 1
        evidence = dict(reference, kind="EVIDENCE", introduced_revision=revision)
        evidence["reference_id"] = sql.digest(
            {k: evidence[k] for k in ("path", "sha256", "schema_version", "kind")}
        )
        run = dict(op.history.find_run(before, self.reader.run_id))
        self._require(run["lifecycle_state"] == "OPEN" and run["run_version"] == 0)
        run.update(
            run_version=1,
            previous_run_version=0,
            introduced_revision=revision,
            lifecycle_state="TERMINAL",
            phase="FINALIZED",
            result=result["decision"],
            binding=result["binding"],
            ack_required=result["decision"] == "NOT_DEMONSTRATED",
            validity_classification="VALID_BINDING" if result["binding"] else "NON_BINDING_ND",
            finalized_at=issued,
            evidence_authority_reference_id=evidence["reference_id"],
        )
        original = op.history._verified(before)["state"]
        event = {
            "event_sequence": len(original["authority_events"]) + 1,
            "revision": revision,
            "event_kind": "RUN_TERMINAL",
            "run_id": run["run_id"],
            "run_version": 1,
            "evidence_reference_id": evidence["reference_id"],
            "occurred_at": issued,
            "reason_code": None,
            "resolves_event_sequence": None,
        }
        for table, value in (
            ("artifact_references", evidence),
            ("official_runs", run),
            ("authority_events", event),
        ):
            sql.validate_row(table, value)
        additions = {
            "artifact_references": [evidence],
            "official_runs": [run],
            "authority_events": [event],
        }
        owner = self

        class PublicationTransport:
            def __init__(self):
                self.sent = False

            def head_b3(self, challenge):
                return owner.remote_b3.head_b3(challenge)

            def publish_b3(self, payload, expected_previous):
                owner._require(
                    not self.sent
                    and payload["operation_id"] == owner.publication_id
                    and payload["revision"] == revision
                    and expected_previous == base["witness_digest"]
                )
                # The candidate/fsync/SQL intent have already occurred. Recheck
                # the original admission after that IO, directly before CAS.
                owner._check(writer=writer, record=record, result=result)
                self.sent = True
                return owner.remote_b3.publish_b3(payload, expected_previous)

        adapter = op.durable.AuthorityBackend(
            ledger_store=publisher.ledger,
            ledger_name=publisher.name,
            ledger_identity=publisher.identity,
            candidate_store=publisher.candidates,
            floor=publisher.floor,
            writer=writer,
            transport=PublicationTransport(),
            signer_pin=publisher.pin,
            clock_health=publisher.health,
            clock=lambda: op.coordination._utc(publisher.clock()),
        )
        path = publisher.candidates.root + "/" + self.publication_id + ".json"
        owner = self

        class Verifier:
            def verify(
                self, *, authority_id, epoch, operation_id, revision, action, proof, candidate_state
            ):
                owner._require(
                    (authority_id, epoch, operation_id, revision, action, proof)
                    == (
                        base["authority_id"],
                        base["epoch"],
                        owner.publication_id,
                        base["revision"] + 1,
                        "RUN_TERMINAL",
                        owner.provenance,
                    )
                )
                owner._require(owner.verification_count in (0, 1))
                owner._check(writer=writer, record=record, result=result)
                candidate = sql.parse_canonical(candidate_state)
                controls = (
                    "history_revisions",
                    "witness_checkpoints",
                    "publication_intents",
                    "publication_resolutions",
                )
                for table, rows in original.items():
                    owner._require(candidate[table][: len(rows)] == rows)
                    if table not in controls:
                        owner._require(candidate[table] == rows + additions.get(table, []))
                suffix = {table: candidate[table][len(original[table]) :] for table in controls}
                if owner.verification_count == 0:
                    owner._require(all(rows == [] for rows in suffix.values()))
                else:
                    owner._require(all(len(rows) == 1 for rows in suffix.values()))
                    history = {
                        "revision": revision,
                        "operation_id": operation_id,
                        "previous_ledger_digest": base["ledger_digest"],
                        "ledger_digest": sql.logical_digest(candidate, revision),
                        "recorded_at": issued,
                        "first_event_sequence": event["event_sequence"],
                        "last_event_sequence": event["event_sequence"],
                        "event_count": 1,
                    }
                    receipt = adapter.receipt
                    owner._require(
                        suffix["history_revisions"] == [history]
                        and suffix["witness_checkpoints"] == [op.history._checkpoint(receipt)]
                        and suffix["publication_intents"] == [adapter.intent]
                        and adapter.intent
                        == {
                            "operation_id": operation_id,
                            "base_revision": base["revision"],
                            "candidate_revision": revision,
                            "base_ledger_digest": base["ledger_digest"],
                            "candidate_ledger_digest": history["ledger_digest"],
                            "candidate_payload_path": path,
                            "candidate_payload_sha256": sql.digest(
                                sql.logical_projection(candidate, revision)
                            ),
                            "serialization_capability_digest": sql.digest(owner.provenance),
                            "created_at": issued,
                        }
                    )
                    owner._require(
                        suffix["publication_resolutions"]
                        == [
                            {
                                "operation_id": operation_id,
                                "disposition": "COMMITTED",
                                "resolved_at": issued,
                                "committed_revision": revision,
                                "receipt_digest": sql.digest(receipt),
                                "operator_approval_path": None,
                                "operator_approval_sha256": None,
                                "operator_approval_schema": None,
                                "fresh_witness_observation_digest": sql.digest(
                                    adapter.observation["head"]
                                ),
                            }
                        ]
                    )
                writer.check()
                op.history.current_revision(before)
                owner.reader._deadline()
                owner.verification_count += 1

        self._check(writer=writer, record=record, result=result)
        published = op.history._publication(
            before,
            additions,
            operation_id=self.publication_id,
            occurred_at=issued,
            evidence_reference_id=evidence["reference_id"],
            action="RUN_TERMINAL",
            verifier=Verifier(),
            capability=self.provenance,
            adapter=adapter,
            signer_identity=publisher.pin,
            candidate_payload_path=path,
        )
        self._require(self.verification_count == 2)
        recheck()
        qualified = self.sources.read_authority()
        self._require(
            op.history._verified(qualified)["state"] == op.history._verified(published)["state"]
            and op.history.find_run(qualified, self.reader.run_id) == run
        )
        raw = publisher.floor.store.read(publisher.floor._path(revision))
        receipt = sql.parse_canonical(raw)
        witness_ref = {
            "authority_id": base["authority_id"],
            "authority_epoch": base["epoch"],
            "revision": revision,
            "ledger_digest": receipt["ledger_digest"],
            "witness_digest": hashlib.sha256(raw).hexdigest(),
            "receipt_reference": {
                "path": publisher.floor._path(revision),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "schema_version": receipt["witness_schema_version"],
            },
        }
        writer.check()
        recheck()
        op.history.current_revision(before)
        self.reader._deadline()
        self.published = qualified

        if type(self.remote_b4) is NativeTerminalDispatchTransport:
            self.remote_b4.arm_terminal(
                owner=self,
                writer=writer,
                record=record,
                result=result,
                before=before,
                qualified=qualified,
                witness_reference=witness_ref,
            )
        else:

            class RetirementTransport:
                def __init__(self):
                    self.sent = False

                def head(self, challenge):
                    return owner.remote_b4.head(challenge)

                def reserve(self, *args, **kwargs):
                    raise LauncherError("native-terminal-retirement-operation")

                def transition(self, **kwargs):
                    owner._require(
                        not self.sent
                        and kwargs
                        == {
                            "reservation_id": record["reservation_id"],
                            "transition": "RETIRE_TERMINAL",
                            "operation_id": owner.retire_id,
                            "expected_sequence": owner.journal.read()["sequence"],
                            "expected_receipt_digest": owner.journal.read()["digest"],
                            "open_witness_digest": record["open_witness_reference"][
                                "witness_digest"
                            ],
                            "terminal_witness_digest": witness_ref["witness_digest"],
                            "recovery_approval": None,
                        },
                        "native-terminal-retirement-operation",
                    )
                    # This is after the journal's real head/SQL action IO, while
                    # its original global writer remains held. No second writer.
                    owner._check(writer=writer, record=record, result=result)
                    op.history.current_revision(qualified)
                    op.history.current_revision(before)
                    owner.reader._deadline()
                    self.sent = True
                    return owner.remote_b4.transition(**kwargs)

            self._require(self.journal.transport is self.remote_b4)
            self.retirement_transport = RetirementTransport()
            # Retain this one-attempt fence on uncertainty; never restore a raw
            # dispatch path or automatically resend an unconfirmed transition.
            self.journal.transport = self.retirement_transport
        return op.coordination.TerminalQualification(
            qualified, sql.canonical_bytes(witness_ref), publisher.floor
        )

    def retire(self):
        self._require(self.lock.acquire(blocking=False), "native-terminal-publication-busy")
        try:
            self._require(not self.used, "native-terminal-publication-consumed")
            self.used, self.running = True, True
            self._graph()
            with self.reader.capture() as observation:
                self.observation = observation
                record = self.op.sql.parse_canonical(self.record)
                retired = self.journal.retire_terminal(
                    reservation_id=record["reservation_id"], operation_id=self.retire_id
                )
                state = self.journal.read()
                self._require(
                    self.published is not None
                    and state["active"] is None
                    and state["witness_active"] is None
                    and state["latest"][record["reservation_id"]] == retired
                )
                current = self.sources.read_authority()
                self._require(
                    self.op.history._verified(current)["state"]
                    == self.op.history._verified(self.published)["state"]
                )
                observation[2]()
                self.sources.policy.postcheck(self.signer.admitted)
                self.op.history.current_revision(self.original)
                self.reader._deadline()
            self.op.history.current_revision(self.original)
            self.reader._deadline()
            return current, retired
        except LauncherError:
            raise
        except Exception:
            raise LauncherError("native-terminal-publication-uncertain") from None
        finally:
            self.running = False
            self.lock.release()


class NativeEmissionProofPublication:
    """Finish one actual SINGLE5, then publish the existing post-ARMED proofs.

    The fixed broker's return follows its actual authenticated exchange and
    dual EOF. This component observes ARMED under the existing custody lock,
    then qualifies full native authority/GOV, committed ownership, B5 and
    independently signed fresh heads. Publication only makes bytes available;
    the executor retains its complete final admission before T0. No extra RPC,
    application PF, clock reset, permission or retry is introduced here.
    """

    def __init__(self, *, broker, reader, publish_role):
        import threading

        self._require(type(reader) is NativeArmedArtifactReader and callable(publish_role))
        op = reader.op
        self._require(
            type(broker) is op.NativeBridgeBroker
            and type(broker.signer) is NativeBridgeProofSigner
            and broker.signer.action == "execute"
            and broker.signer.op is op
            and broker.signer.run_id == reader.run_id
            and broker.signer.sources is broker.sources
            and broker.sources.journal is broker.journal
            and broker.journal.sources is broker.sources
            and broker.sources.signer is broker.signer
            and broker.signer.journal is broker.journal
            and broker.signer.admitted.profile._bytes == reader.admitted.profile._bytes
            and broker.signer.admitted.session._bytes == reader.admitted.session._bytes
            and broker.signer.admitted.process == reader.admitted.process,
            "native-emission-context",
        )
        self.broker, self.reader, self.publish_role = broker, reader, publish_role
        self.op, self.signer, self.sources, self.journal = (
            op,
            broker.signer,
            broker.sources,
            broker.journal,
        )
        self.deadline = reader.deadline
        self.lock = threading.Lock()
        self.used, self.failed = False, False

    @staticmethod
    def _require(condition, code="native-emission-binding"):
        if not condition:
            raise LauncherError(code)

    def _deadline(self):
        import time

        self._require(
            self.reader.deadline == self.deadline and time.monotonic() < self.deadline,
            "native-emission-deadline",
        )

    def _graph(self):
        self._require(
            self.broker.sources is self.sources
            and self.broker.signer is self.signer
            and self.broker.journal is self.journal
            and self.sources.signer is self.signer
            and self.sources.journal is self.journal
            and self.journal.sources is self.sources
            and self.signer.sources is self.sources
            and self.signer.journal is self.journal
            and not self.signer.failed,
            "native-emission-context",
        )
        self.sources.policy.qualify(native=self.signer.admitted, journal=self.journal)
        self._deadline()

    def serve_single(self, channel):
        """Own the existing SINGLE and retain all failures/partial availability."""
        import time

        self._require(self.lock.acquire(blocking=False), "native-emission-busy")
        try:
            self._require(not self.used, "native-emission-consumed")
            self.used = True
            self._deadline()
            self._graph()
            self._require(
                type(channel) is self.op.PipeFrameChannel
                and channel.exchange == "SINGLE_RPC"
                and channel.exchange_id == self.signer.exchange
                and channel.role == "HOST"
                and channel.position == 0
                and not channel.closed
                and self.signer.index == 0,
                "native-emission-exchange",
            )
            # The actual broker checks EOF and returns only after finish().
            response = self.broker.serve_single(channel)
            self._require(
                channel is self.sources.channel
                and channel.closed
                and channel.position == 5
                and channel.read_fd is channel.write_fd is channel.error_fd is None
                and self.signer.index == 3
                and response == self.signer.signed_response,
                "native-emission-incomplete-single",
            )
            value = self.op.wire.parse_response_bytes(response).to_dict()
            self._require(
                value["status"] == "OK"
                and value["operation"] == "inspect-reservation"
                and value["uncertain"] is False
                and value["run_id"] == self.reader.run_id,
            )
            while True:
                finalcheck = None
                self._deadline()
                self._graph()
                with self.reader.capture() as observation:
                    if observation is not None:
                        raw, recheck = observation
                        finalcheck = self._publish(raw, recheck, value["result"]["reservation"])
                if finalcheck is not None:
                    # Retained custody FDs have also been closed; clock after all IO.
                    finalcheck()
                    return response
                # Only availability is polled. No new exchange or refreshed
                # authorization can rescue the original absolute deadline.
                self._deadline()
                time.sleep(0.05)
        except BaseException:
            self.failed = True
            raise
        finally:
            self.lock.release()

    def _fresh(self, snapshot, heads, envelope, minimum):
        self.op.history.current_revision(snapshot)
        now = self.journal._now()
        for value in heads:
            issued, expires = (
                self.op.wire._time(value["issued_at"]),
                self.op.wire._time(value["expires_at"]),
            )
            self._require(
                minimum <= issued <= now < expires <= issued + timedelta(seconds=30),
                "native-emission-head-stale",
            )
        self._require(
            self.op.wire._time(envelope["checked_at"])
            <= now
            < self.op.wire._time(envelope["expires_at"]),
            "native-emission-envelope-stale",
        )
        self._deadline()

    def _publish(self, raw, recheck, expected_record):
        import time

        op, signer, sources, journal = self.op, self.signer, self.sources, self.journal
        minimum = op.wire._time(op.sql.parse_canonical(raw)["recorded_at"])
        # This is historical cryptographic provenance of the original nonce,
        # never a renewed 120s capability or a current authorization verdict.
        challenge_raw = signer.signed_challenge
        challenge = op.wire.parse_host_challenge_bytes(challenge_raw).to_dict()
        self._require(
            all(challenge[k] == v for k, v in signer._fixed().items())
            and {k: v for k, v in challenge.items() if k != "signature"} == signer.challenge,
        )
        projection = {
            k: v for k, v in challenge.items() if k not in ("signature", "issued_at", "expires_at")
        }
        op.wire.verify_host_challenge(
            challenge_raw,
            expected_context=op.wire.ReceiptContext(
                op.sql.canonical_bytes(projection), op.wire._time(challenge["issued_at"])
            ),
            bridge_pin=signer.pin,
        )
        snapshot = sources.read_authority()
        state = journal.read()
        record = signer._record(state, expected_record["reservation_id"], committed=True)
        run = op.history.find_run(snapshot, self.reader.run_id)
        self._require(
            record == expected_record
            and not snapshot.authority_uncertainty
            and not snapshot.write_closed
            and run is not None
            and run["lifecycle_state"] in ("OPEN", "ACTIVE"),
            "native-emission-ownership",
        )
        before = time.monotonic()
        head = sources.publisher.transport.head_b3(challenge["challenge"])
        elapsed = time.monotonic() - before
        witness = op.history.witness
        pins = [witness.TrustPin(raw_pin) for raw_pin in snapshot.canonical_trust_pins]
        pin = next(
            (
                p
                for p in pins
                if (p.record["key_id"], p.record["key_epoch"])
                == (head["key_id"], head["key_epoch"])
            ),
            None,
        )
        self._require(pin is not None)
        witness._verify_current_head(
            head,
            pin,
            challenge=challenge["challenge"],
            now=op.coordination._utc(journal._now()),
            request_elapsed_seconds=elapsed,
            clock_healthy=True,
        )
        current = op.history.current_revision(snapshot)
        self._require(
            (
                head["authority_id"],
                head["authority_epoch"],
                head["revision"],
                head["ledger_digest"],
                head["witness_digest"],
            )
            == (
                current["authority_id"],
                current["epoch"],
                current["revision"],
                current["ledger_digest"],
                current["witness_digest"],
            ),
        )
        floor = journal.floor.current()
        before = time.monotonic()
        coord = journal.transport.head(challenge["challenge"])
        self._require(time.monotonic() - before <= 30)
        coord = journal.floor.verify_head(
            coord,
            challenge=challenge["challenge"],
            now=journal._now(),
            minimum_sequence=floor["sequence"],
            minimum_token=floor["fencing_token"],
        )
        expected = (state["sequence"], state["digest"], state["token"], state["active"])
        self._require(
            (
                coord["sequence"],
                coord["receipt_digest"],
                coord["fencing_token"],
                coord["active_reservation_id"],
            )
            == (
                floor["sequence"],
                floor["receipt_digest"],
                floor["fencing_token"],
                floor["active_reservation_id"],
            )
            == expected,
        )
        receipt = signer._receipt(state, record)
        envelope_raw = sources.registry.read("envelope")
        b5 = self.reader.apparatus["ex10_deployment_envelope"]
        envelope = b5.parse_envelope_bytes(envelope_raw).to_dict()
        profile = signer.admitted.profile.to_dict()
        fixed = {
            "result": "PASS",
            "expected_git_sha": profile["source_revision"],
            "runtime_profile_sha256": op.sql.parse_canonical(signer.admitted.profile_reference)[
                "sha256"
            ],
            "session_id": signer.admitted.session.header["session_id"],
            "session_header_sha256": op.sql.parse_canonical(signer.admitted.header_reference)[
                "sha256"
            ],
            "project": profile["project"],
            "host_roots": profile["host_roots"],
            "deployment_verification_reference": profile["deployment_verification_reference"],
            "application_preflight_sha256": sources.registry.reference("preflight")["sha256"],
        }
        self._require(
            all(envelope[k] == v for k, v in fixed.items()), "native-emission-envelope-binding"
        )
        verdict = op._host_call(
            sources.policy.envelope,
            "ENVELOPE_INVALID",
            canonical_envelope=envelope_raw,
            reference=op.sql.canonical_bytes(sources.registry.reference("envelope")),
            native=signer.admitted,
            snapshot=snapshot,
            issued_at=op.coordination._utc(journal._now()),
        )
        self._require(verdict is None, "native-emission-envelope-binding")
        heads = (head, coord)
        recheck()
        self._fresh(snapshot, heads, envelope, minimum)
        values = (
            ("emission-material", snapshot.canonical_material),
            ("emission-head", op.sql.canonical_bytes(head)),
            ("emission-coordination", op.sql.canonical_bytes(coord)),
            ("emission-receipt", receipt),
        )
        for role, proof in values:
            self._fresh(snapshot, heads, envelope, minimum)
            self._require(
                self.publish_role(role, proof) is None, "native-emission-publication-verdict"
            )
        # Later provider/lock IO cannot rescue an expired original observation.
        self._graph()
        current_snapshot = sources.read_authority()
        observed = op.history.current_revision(current_snapshot)
        self._require(
            all(
                observed[k] == current[k]
                for k in ("authority_id", "epoch", "revision", "ledger_digest", "witness_digest")
            ),
            "native-emission-authority-changed",
        )
        current_state = journal.read()
        self._require(
            (
                current_state["sequence"],
                current_state["digest"],
                current_state["token"],
                current_state["active"],
            )
            == expected
            and signer._record(current_state, record["reservation_id"], committed=True) == record,
            "native-emission-ownership",
        )
        verdict = op._host_call(
            sources.policy.envelope,
            "ENVELOPE_INVALID",
            canonical_envelope=envelope_raw,
            reference=op.sql.canonical_bytes(sources.registry.reference("envelope")),
            native=signer.admitted,
            snapshot=current_snapshot,
            issued_at=op.coordination._utc(journal._now()),
        )
        self._require(verdict is None, "native-emission-envelope-binding")
        self._require(
            sources.registry.read("envelope") == envelope_raw, "native-emission-envelope-binding"
        )
        sources.policy.postcheck(signer.admitted)
        recheck()

        def finalcheck():
            op.history.current_revision(current_snapshot)
            self._fresh(snapshot, heads, envelope, minimum)

        finalcheck()
        return finalcheck


_NATIVE_EXEC_PRECONDITION_REASONS = frozenset(
    {
        "web-worker-not-running",
        "beat-running",
        "oneoff-container-present",
        "db-redis-not-healthy",
        "preflight-report-not-acceptable",
    }
)


@dataclass(frozen=True)
class NativeExecutionPreconditionRefused:
    """Known EXEC negative from the already installed precondition verifier.

    This result grants nothing. The verifier retains original attached PF and
    profile/instance provenance and reads a complete current namespace/health
    observation as applicable. Missing, unknown or ambiguous observations are
    errors, never permission or a known negative. No B5 field or grant changes.
    """

    session_sha256: str
    action: str
    run_id: str
    phase: str
    reason: str


@dataclass(frozen=True)
class NativeExecutorStillActive:
    """Known negative from the installed exact prior-executor observer.

    This result never grants permission. The installed verifier retains the
    prior identity/provenance and observes it mechanically; a name alone is
    not proof. Ambiguous observations still raise or return an invalid result.
    No identity is inferred here or added to the durable B2 schema.
    """

    session_sha256: str
    action: str
    run_id: str
    phase: str
    container_name: str
    container_id: str
    container_state: str


class _NativeIntentStillActive(Exception):
    """Host-owned identity signal, created only after a no-write intent closes."""

    def __init__(self, check_current):
        self.check_current = check_current
        super().__init__("native-intent-known-active")


class _NativeIntentExecutionRefused(Exception):
    """Own host signal minted after original no-write EXEC intent guards close."""

    def __init__(self, check_current, reason):
        self.check_current = check_current
        self.reason = reason
        super().__init__("native-intent-execution-precondition-refused")


class NativeActionIntent:
    """Persist one qualified pending attempt before any container or worker effect.

    The constructor-fixed action verifier independently checks the exact action
    matrix, current authority, explicit ACK and run ownership as applicable.
    Neither the B2 state nor a pending entry grants operational/scientific
    permission. The subsequent native broker still performs its own gates.
    On publication loss the record is retained, with no retry or repair.
    """

    def __init__(self, *, invocation, bootstrap, container_name, output_dir, approve_action):
        import threading

        if (
            type(bootstrap) is not NativeLauncherBootstrap
            or not callable(approve_action)
            or invocation.action not in bootstrap.apparatus["ex10_session_v2"].V1_MUTATING
            or invocation.action == "initialize-session"
            or type(container_name) is not str
            or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,62}", container_name)
            or type(output_dir) is not str
        ):
            raise LauncherError("native-intent-plan-invalid")
        self.invocation, self.bootstrap = invocation, bootstrap
        self.action, self.name, self.output = invocation.action, container_name, output_dir
        self.approve, self.attempted = approve_action, False
        self._active_signal = None
        self._execution_signal = None
        self._mutation_lock = threading.Lock()

    def begin(self):
        validate_invocation(self.invocation)
        if self.invocation.action != self.action:
            raise LauncherError("native-intent-action-changed")
        if not self._mutation_lock.acquire(blocking=False):
            raise LauncherBusy()
        try:
            if self.attempted:
                raise LauncherError("native-intent-second-attempt-denied")
            self.attempted = True
            return self._begin_locked()
        finally:
            self._mutation_lock.release()

    def _begin_locked(self):
        bootstrap, invocation = self.bootstrap, self.invocation
        codec = bootstrap.apparatus["ex10_session_v2"]
        with contextlib.ExitStack() as writer_scope:
            try:
                writer = writer_scope.enter_context(bootstrap.writer())
            except bootstrap.apparatus["ex10_authority_sqlite"].AuthorityError as error:
                # Only failure to acquire the real global writer is retry-free
                # BUSY. Later authority/provider errors remain uncertain.
                if error.code == "LOCK_BUSY":
                    raise LauncherBusy() from None
                raise
            with _native_session_lock(bootstrap, invocation.session_root) as session_current:
                session = bootstrap.load(invocation)
                value = session.to_dict()
                state = value["state"]
                admitted_bytes = session._bytes
                admitted_run = value["official_run_id"]
                admitted_phase = state["known_phase"]
                known_active = False
                execution_refusal_reason = None
                if (
                    value["in_flight"]
                    or state["uncertain"]
                    or state["blocked"]
                    or self.action not in state["next_allowed_actions"]
                ):
                    raise LauncherError("native-intent-state-blocked")
                profile, *_, recheck = bootstrap.qualify(invocation, session=session)
                # Construct and validate the exact closed B2 snapshot before
                # calling external approval or changing the private record.
                payload = dict(value["history"][-1]["payload"])
                payload["event"] = "intent-" + self.action
                payload["in_flight"] = [
                    {
                        "action": self.action,
                        "container_name": self.name,
                        "output_dir": self.output,
                        "started_at": invocation.clock()
                        .astimezone(UTC)
                        .strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        "after_seq": session._expected_count,
                    }
                ]
                payload["state"] = {
                    "known_phase": state["known_phase"],
                    "uncertain": True,
                    "blocked": True,
                    "next_allowed_actions": ["inspect"],
                }
                replacement = codec.append_history_entry_v2(session, payload)
                admitted_raw, admitted_session_lease = _capture_native_source(
                    invocation.session_root / SESSION_FILE, error_type=LauncherError
                )
                admitted_checkpoint = (session._expected_tip, session._expected_count)
                checkpoint_reader = bootstrap.read_checkpoint

                def checkpoint_current():
                    if bootstrap.read_checkpoint is not checkpoint_reader:
                        raise LauncherError("native-intent-record-changed")
                    observed = bootstrap.checkpoint()
                    if (
                        bootstrap.read_checkpoint is not checkpoint_reader
                        or observed != admitted_checkpoint
                    ):
                        raise LauncherError("native-intent-record-changed")

                if admitted_raw != admitted_bytes:
                    raise LauncherError("native-intent-record-changed")
                checkpoint_current()
                try:
                    accepted = self.approve(
                        action=self.action,
                        session=session,
                        profile=profile,
                        container_name=self.name,
                        output_dir=self.output,
                        pending=replacement,
                    )
                except Exception:
                    raise LauncherError("native-intent-approval-required") from None
                if type(accepted) is NativeExecutorStillActive:
                    if (
                        self.action != "declare-executor-gone"
                        or any(
                            type(getattr(accepted, name)) is not str
                            for name in accepted.__dataclass_fields__
                        )
                        or accepted.action != self.action
                        or accepted.session_sha256 != hashlib.sha256(admitted_bytes).hexdigest()
                        or accepted.run_id != admitted_run
                        or accepted.phase != admitted_phase
                        or admitted_phase not in {"STARTED", "EMITTING"}
                        or type(accepted.container_name) is not str
                        or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,62}", accepted.container_name)
                        or type(accepted.container_id) is not str
                        or not _SHA256.fullmatch(accepted.container_id)
                        or type(accepted.container_state) is not str
                        or accepted.container_state not in ACTIVE_CONTAINER_STATES
                    ):
                        raise LauncherError("native-intent-approval-required")
                    known_active = True
                elif type(accepted) is NativeExecutionPreconditionRefused:
                    if (
                        self.action != "execute-official"
                        or any(
                            type(getattr(accepted, name)) is not str
                            for name in accepted.__dataclass_fields__
                        )
                        or accepted.action != self.action
                        or accepted.session_sha256 != hashlib.sha256(admitted_bytes).hexdigest()
                        or type(admitted_run) is not str
                        or accepted.run_id != admitted_run
                        or accepted.phase != admitted_phase
                        or admitted_phase != "PREPARED"
                        or accepted.reason not in _NATIVE_EXEC_PRECONDITION_REASONS
                    ):
                        raise LauncherError("native-intent-approval-required")
                    execution_refusal_reason = accepted.reason
                elif accepted is not None:
                    raise LauncherError("native-intent-approval-required")
                # Approval may do I/O. Do not let it replace the record or
                # independent checkpoint that was admitted under both locks.
                raw, session_lease = _capture_native_source(
                    invocation.session_root / SESSION_FILE, error_type=LauncherError
                )
                if (
                    raw != admitted_bytes
                    or session._bytes != admitted_bytes
                    or session_lease != admitted_session_lease
                ):
                    raise LauncherError("native-intent-record-changed")
                checkpoint_current()
                recheck()
                session_current()
                writer.check()
                recheck.check_authority_current()
                if not known_active and execution_refusal_reason is None:
                    replacement = codec.update_official_session_v2(
                        invocation.session_root / SESSION_FILE,
                        session,
                        replacement,
                        context=bootstrap.context(),
                        profile=profile,
                        caller_serialized=True,
                    )
                    recheck()
                    session_current()
                    writer.check()
                    recheck.check_authority_current()
                    bootstrap.publish((session._expected_tip, session._expected_count), replacement)
                    observed = bootstrap.load(invocation)
                    if observed._bytes != replacement._bytes:
                        raise LauncherError("native-intent-publication-uncertain")
                    recheck()
                    session_current()
                    writer.check()
                    recheck.check_authority_current()
        # Session __exit__ rechecks the path, then the global writer unlocks
        # and closes its fd. Those are still I/O; expiry must also hold after
        # both guards have exited, before exposing this pending result.
        if known_active or execution_refusal_reason is not None:

            def denial_current():
                # The original independent witness may read providers/FDs.
                # Finish that IO before all original passive and pure fences.
                checkpoint_current()
                recheck.check_source_leases_current()
                _native_check_source_lease(
                    invocation.session_root / SESSION_FILE,
                    admitted_session_lease,
                    code="native-intent-record-changed",
                )
                recheck.check_authority_current()

            denial_current()
            if known_active:
                self._active_signal = _NativeIntentStillActive(denial_current)
                raise self._active_signal
            self._execution_signal = _NativeIntentExecutionRefused(
                denial_current,
                execution_refusal_reason,
            )
            raise self._execution_signal
        recheck.check_authority_current()
        return replacement


class NativeOneOffDocker:
    """One owned create/inspect/start attempt, with no adoption, retry or removal.

    This component does not issue scientific permission or qualify the opaque
    bootstrap's nested trust/payload. A fixed host creation verifier must bind
    that complete material to the current owned live attempt, approved image,
    network and environment. The actual B2 durable intent is required too; its
    presence alone is never authority. The surrounding action engine retains
    it on every uncertain result. Real bridge/worker/terminal gates are separate.
    """

    _BOOTSTRAP_FIELDS = frozenset(
        {
            "action",
            "exchange_id",
            "profile_sha256",
            "header_sha256",
            "bridge_pin",
            "witness_pin",
            "b3_pins",
            "genesis_digest",
            "genesis_binding",
            "authority_revision",
            "request_payload",
            "expected_reservation",
            "host_root_binding_sha256",
            "source_hashes",
            "staging_name",
            "command_options",
            "authorization_reference",
        }
    )
    _WORKER_ACTIONS = {
        "start-official": "start",
        "execute-official": "execute",
        "drain": "drain",
        "reconcile": "reconcile",
        "finalize": "finalize",
        "abandon-official-pre-t0": "finalize",
        "declare-executor-gone": "finalize",
    }

    def __init__(
        self,
        *,
        invocation,
        bootstrap,
        name,
        image_digest,
        network_id,
        bootstrap_bytes,
        service_environment,
        owner_labels,
        approve_creation,
        runner_factory=DockerRunner,
    ):
        import threading
        import uuid

        if (
            type(bootstrap) is not NativeLauncherBootstrap
            or not callable(approve_creation)
            or not callable(runner_factory)
            or invocation.action not in self._WORKER_ACTIONS
            or type(name) is not str
            or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,62}", name)
            or type(image_digest) is not str
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest)
            or type(network_id) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", network_id)
        ):
            raise LauncherError("native-oneoff-plan-invalid")
        self.invocation, self.bootstrap, self.action = invocation, bootstrap, invocation.action
        self.name, self.image_digest, self.network_id = name, image_digest, network_id
        self.approve = approve_creation
        self.container_id = None
        self._mutation_lock = threading.Lock()
        self.create_attempted = self.start_attempted = self.started = False
        profile, *_ = bootstrap.qualify(invocation)
        self.profile = profile
        value = profile.to_dict()
        if type(bootstrap_bytes) is not bytes or len(bootstrap_bytes) > 32768:
            raise LauncherError("native-oneoff-bootstrap-invalid")
        try:
            cfg = json.loads(bootstrap_bytes.decode("utf-8"), parse_constant=base._reject_constant)
            if (
                type(cfg) is not dict
                or set(cfg) != self._BOOTSTRAP_FIELDS
                or base.canonical_bytes(cfg) != bootstrap_bytes
                or cfg["action"] != self._WORKER_ACTIONS[self.action]
                or str(uuid.UUID(cfg["exchange_id"])) != cfg["exchange_id"]
                or cfg["profile_sha256"]
                != bootstrap.apparatus["ex10_runtime_profile"].runtime_profile_sha256(profile)
            ):
                raise ValueError()
        except (UnicodeError, ValueError, KeyError, TypeError, RecursionError):
            raise LauncherError("native-oneoff-bootstrap-invalid") from None
        self.bootstrap_bytes, self.initial_header_sha256 = bootstrap_bytes, cfg["header_sha256"]
        self.exchange_id = cfg["exchange_id"]
        if type(service_environment) is not dict or len(service_environment) > 256:
            raise LauncherError("native-oneoff-environment-invalid")
        environment = {}
        reserved = {
            "PATH",
            "LC_ALL",
            "HOME",
            "PYTHONPATH",
            "PYTHONHOME",
            "PYTHONDONTWRITEBYTECODE",
            "S11_EX10_NATIVE_BOOTSTRAP",
        }
        for key, val in service_environment.items():
            if (
                type(key) is not str
                or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", key)
                or key in reserved
                or key.startswith(("DOCKER_", "LD_", "DYLD_"))
                or type(val) is not str
                or "\x00" in val
                or len(val) > 8192
            ):
                raise LauncherError("native-oneoff-environment-invalid")
            environment[key] = val
        environment.update(
            {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": "/s11-full-source/backend",
                "S11_EX10_NATIVE_BOOTSTRAP": bootstrap_bytes.decode("utf-8"),
            }
        )
        self._environment_bytes = base.canonical_bytes(environment)
        if type(owner_labels) is not dict or len(owner_labels) > 32 or not owner_labels:
            raise LauncherError("native-oneoff-owner-invalid")
        labels = {
            "com.docker.compose.project": value["project"],
            "com.docker.compose.service": "backend",
            "com.docker.compose.oneoff": "True",
            "com.docker.compose.project.working_dir": value["source_host_root"],
            "com.docker.compose.project.config_files": ",".join(
                c["path"] for c in value["compose_files"]
            ),
        }
        for key, val in owner_labels.items():
            if (
                type(key) is not str
                or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", key)
                or key in labels
                or type(val) is not str
                or not val
                or len(val) > 512
                or any(ord(c) < 32 or ord(c) == 127 for c in val)
            ):
                raise LauncherError("native-oneoff-owner-invalid")
            labels[key] = val
        self._labels_bytes = base.canonical_bytes(labels)
        self.mounts = tuple(
            [
                (value["source_host_root"], "/s11-full-source", True),
                *(
                    (value["host_roots"][role]["path"], target, False)
                    for role, target in value["fixed_targets"].items()
                ),
                *(
                    (m["source_path"], "/s11-full-source/" + m["relative_path"], True)
                    for m in value["masks"]
                ),
            ]
        )
        # Docker receives secret values through environment only; argv contains
        # names. No ambient DOCKER/TLS/proxy/PATH config selects an endpoint.
        child = dict(
            environment, DOCKER_HOST="unix:///var/run/docker.sock", LC_ALL="C", HOME="/nonexistent"
        )
        base.bind(bootstrap.repo_root, bootstrap.expected_git_sha)
        self.runner = runner_factory(bootstrap.repo_root, child)

    @property
    def labels(self):
        return json.loads(self._labels_bytes)

    @property
    def container_environment(self):
        return json.loads(self._environment_bytes)

    def _admit(self):
        if self.invocation.action != self.action:
            raise LauncherError("native-oneoff-action-changed")
        session = self.bootstrap.load(self.invocation)
        *_, recheck = self.bootstrap.qualify(self.invocation, session=session)
        value = session.to_dict()
        pending = value["in_flight"]
        if (
            len(pending) != 1
            or pending[0]["action"] != self.action
            or pending[0]["container_name"] != self.name
            or value["header_sha256"] != self.initial_header_sha256
        ):
            raise LauncherError("native-oneoff-owned-intent-required")
        try:
            accepted = self.approve(
                profile=self.profile,
                session=session,
                name=self.name,
                image_digest=self.image_digest,
                network_id=self.network_id,
                labels=self.labels,
                mounts=self.mounts,
                environment=self.container_environment,
                bootstrap_bytes=self.bootstrap_bytes,
            )
        except Exception:
            raise LauncherError("native-oneoff-approval-required") from None
        if accepted is not None:
            raise LauncherError("native-oneoff-approval-required")
        if self.bootstrap.load(self.invocation)._bytes != session._bytes:
            raise LauncherError("native-oneoff-intent-changed")
        recheck()
        return session, recheck

    def _command(self, *args):
        try:
            result = self.runner.run(("/usr/bin/docker", "container", *args), timeout=30)
        except Exception:
            raise LauncherError("native-oneoff-command-uncertain") from None
        if (
            type(result.returncode) is not int
            or result.returncode != 0
            or type(result.stdout) is not bytes
            or len(result.stdout) > MAX_JSON_BYTES
        ):
            raise LauncherError("native-oneoff-command-uncertain")
        return result.stdout

    @contextlib.contextmanager
    def _mutation(self):
        if not self._mutation_lock.acquire(blocking=False):
            raise LauncherBusy()
        try:
            yield
        finally:
            self._mutation_lock.release()

    def create(self):
        with self._mutation():
            return self._create_locked()

    def _create_locked(self):
        if self.create_attempted:
            raise LauncherError("native-oneoff-second-create-denied")
        self.create_attempted = True
        self._admit()
        owner = self.profile.to_dict()["expected_owner"]
        args = [
            "create",
            "--name",
            self.name,
            "--pull",
            "never",
            "--user",
            f'{owner["uid"]}:{owner["gid"]}',
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--no-healthcheck",
            "--restart",
            "no",
            "--network",
            self.network_id,
        ]
        for key, value in sorted(self.labels.items()):
            args.extend(("--label", key + "=" + value))
        for source, target, read_only in self.mounts:
            args.extend(
                (
                    "--mount",
                    f"type=bind,source={source},target={target}"
                    + (",readonly" if read_only else ""),
                )
            )
        for key in sorted(self.container_environment):
            args.extend(("--env", key))
        args.extend(
            (
                "--workdir",
                "/s11-full-source/backend",
                "--entrypoint",
                "/usr/local/bin/python",
                self.image_digest,
                "-B",
                "/app/manage.py",
                "s11_ex10",
                "bridge",
            )
        )
        raw = self._command(*args)
        if re.fullmatch(rb"[0-9a-f]{64}\n?", raw) is None:
            raise LauncherError("native-oneoff-create-response-uncertain")
        self.container_id = raw.decode("ascii").strip()
        return self.container_id

    def _inspect(self, *, allowed):
        if self.container_id is None:
            raise LauncherError("native-oneoff-instance-required")
        raw = self._command("inspect", self.container_id)
        try:
            values = json.loads(raw.decode("utf-8"), parse_constant=base._reject_constant)
            if type(values) is not list or len(values) != 1 or type(values[0]) is not dict:
                raise ValueError()
            value = values[0]
            config, host, state = value["Config"], value["HostConfig"], value["State"]
            owner = self.profile.to_dict()["expected_owner"]
            if (
                value["Id"] != self.container_id
                or value["Name"] != "/" + self.name
                or value["Image"] != self.image_digest
                or config["Labels"] != self.labels
                or config["User"] != f'{owner["uid"]}:{owner["gid"]}'
                or config["Entrypoint"] != ["/usr/local/bin/python"]
                or config["Cmd"] != ["-B", "/app/manage.py", "s11_ex10", "bridge"]
                or config["WorkingDir"] != "/s11-full-source/backend"
                or config["Healthcheck"].get("Test") != ["NONE"]
            ):
                raise ValueError()
            observed_env = {}
            for entry in config["Env"]:
                key, val = entry.split("=", 1)
                if key in observed_env:
                    raise ValueError()
                observed_env[key] = val
            if observed_env != self.container_environment:
                raise ValueError()
            if (
                host["ReadonlyRootfs"] is not True
                or host["Privileged"] is not False
                or host["CapDrop"] != ["ALL"]
                or host.get("CapAdd") not in (None, [])
                or host["SecurityOpt"] != ["no-new-privileges"]
                or host["RestartPolicy"].get("Name") != "no"
                or host["PortBindings"] not in (None, {})
                or host["PublishAllPorts"] is not False
                or any(
                    host.get(key) not in (None, [], "")
                    for key in (
                        "Binds",
                        "VolumesFrom",
                        "Devices",
                        "DeviceRequests",
                        "PidMode",
                        "UsernsMode",
                    )
                )
            ):
                raise ValueError()
            mounts = value["Mounts"]
            if type(mounts) is not list or len(mounts) != len(self.mounts):
                raise ValueError()
            actual = []
            for mount in mounts:
                if mount["Type"] != "bind" or type(mount["RW"]) is not bool:
                    raise ValueError()
                actual.append((mount["Source"], mount["Destination"], not mount["RW"]))
            if sorted(actual) != sorted(self.mounts):
                raise ValueError()
            networks = value["NetworkSettings"]["Networks"]
            if (
                type(networks) is not dict
                or len(networks) != 1
                or next(iter(networks.values()))["NetworkID"] != self.network_id
            ):
                raise ValueError()
            phase = state["Status"]
            if (
                phase not in allowed
                or state["Restarting"] is not False
                or state["OOMKilled"] is not False
                or type(state["Running"]) is not bool
                or state["Running"] != (phase == "running")
                or type(state["Pid"]) is not int
                or state["Pid"] < 0
                or (state["Pid"] > 0) != (phase == "running")
                or type(state["ExitCode"]) is not int
            ):
                raise ValueError()
            return state
        except (UnicodeError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
            raise LauncherError("native-oneoff-observation-invalid") from None

    def start(self):
        with self._mutation():
            return self._start_locked()

    def _start_locked(self):
        if self.start_attempted or self.started:
            raise LauncherError("native-oneoff-second-start-denied")
        initial, recheck = self._admit()
        self._inspect(allowed={"created"})
        # Docker inspect can block or run a callback. Requalify the current
        # owned intent and approval after it, retaining the original B3
        # snapshot's expiry fence rather than refreshing expired authority.
        current, _ = self._admit()
        if current._bytes != initial._bytes:
            raise LauncherError("native-oneoff-intent-changed")
        recheck()
        self.start_attempted = True
        raw = self._command("start", self.container_id)
        if raw not in (self.container_id.encode(), (self.container_id + "\n").encode()):
            raise LauncherError("native-oneoff-start-response-uncertain")
        self._inspect(allowed={"running"})
        self.started = True

    def worker_exit(self):
        if not self.started:
            raise LauncherError("native-oneoff-worker-not-started")
        state = self._inspect(allowed={"running", "exited"})
        return None if state["Running"] else state["ExitCode"]


def _native_process_commitments(
    *, apparatus, bootstrap_bytes, profile, container_id, envelope_bytes
):
    """Calculate the worker's unchanged commitments from complete canonical bytes.

    This projection is availability data, never deployment qualification or
    process admission. The surrounding factory must independently qualify B5,
    observe the exact owned instance and retain its original source/OS budget.
    Neither a published process DTO nor a caller-selected policy enters here.
    """
    try:
        rp = apparatus["ex10_runtime_profile"]
        b5 = apparatus["ex10_deployment_envelope"]
        if (
            type(profile) is not rp.RuntimeProfile
            or type(bootstrap_bytes) is not bytes
            or not 0 < len(bootstrap_bytes) <= 32768
            or type(container_id) is not str
            or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
        ):
            raise ValueError()
        cfg = json.loads(bootstrap_bytes.decode("utf-8"), parse_constant=base._reject_constant)
        if (
            type(cfg) is not dict
            or set(cfg) != NativeOneOffDocker._BOOTSTRAP_FIELDS
            or base.canonical_bytes(cfg) != bootstrap_bytes
        ):
            raise ValueError()
        p = rp.parse_runtime_profile_bytes(rp.canonical_runtime_profile_bytes(profile)).to_dict()
        envelope = b5.parse_envelope_bytes(envelope_bytes).to_dict()
        if (
            cfg["profile_sha256"] != rp.runtime_profile_sha256(profile)
            or type(cfg["header_sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", cfg["header_sha256"]) is None
            or type(cfg["source_hashes"]) is not dict
            or not cfg["source_hashes"]
            or any(
                type(name) is not str
                or not name
                or type(digest) is not str
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                for name, digest in cfg["source_hashes"].items()
            )
            or envelope["runtime_profile_sha256"] != cfg["profile_sha256"]
            or envelope["session_header_sha256"] != cfg["header_sha256"]
            or envelope["expected_git_sha"] != p["source_revision"]
            or envelope["project"] != p["project"]
        ):
            raise ValueError()
        identity = hashlib.sha256(
            base.canonical_bytes(
                {
                    "container_id": container_id,
                    "runtime_profile_sha256": cfg["profile_sha256"],
                    "session_header_sha256": cfg["header_sha256"],
                    "project": p["project"],
                    "source_revision": p["source_revision"],
                    "source_hashes": cfg["source_hashes"],
                    "native_bootstrap_sha256": hashlib.sha256(bootstrap_bytes).hexdigest(),
                }
            )
        ).hexdigest()
        deployment = hashlib.sha256(
            base.canonical_bytes(
                {
                    "container_identity_digest": identity,
                    "operational_envelope_sha256": hashlib.sha256(envelope_bytes).hexdigest(),
                }
            )
        ).hexdigest()
        return {
            "container_id": container_id,
            "identity_digest": identity,
            "deployment_verification_sha256": deployment,
        }
    except Exception:
        raise LauncherError("native-process-commitment-invalid") from None


@dataclass(frozen=True, slots=True)
class NativePreparedWorkerBindings:
    """Availability for the existing worker; bridge permission remains separate."""

    process: Any
    process_bytes: bytes
    envelope_bytes: bytes
    ready_deadline: float
    check_retained: Callable
    reobserve_completed: Callable
    check_completed_inputs_passive: Callable


def _prepare_native_worker_bindings(
    *,
    control,
    publication,
    envelope_configuration,
    check_current,
    preparation_deadline,
    authority_snapshot,
    attached_envelope_bytes=None,
):
    """Qualify the original B5 and publish the owned instance once.

    The outer factory supplies fixed raw observation/approval providers and its
    original attachment/namespace guard. No caller-built qualifier, process DTO,
    renewed PF, new trust root or scientific permission enters this component.
    """
    import ast
    import math
    import time

    try:
        if (
            type(control) is not NativeOneOffDocker
            or type(publication) is not NativeExclusiveProofPublication
            or control.action not in NativeOneOffDocker._WORKER_ACTIONS
            or not control.started
            or control.invocation.native_bootstrap is not control.bootstrap
            or publication.bootstrap is not control.bootstrap
            or not callable(check_current)
            or type(preparation_deadline) not in (int, float)
            or not math.isfinite(preparation_deadline)
            or time.monotonic() >= preparation_deadline
            or type(envelope_configuration) is not dict
        ):
            raise ValueError()
        bootstrap, invocation = control.bootstrap, control.invocation
        apparatus = bootstrap.apparatus
        b5, op, history = (
            apparatus["ex10_deployment_envelope"],
            apparatus["ex10_operational_context"],
            apparatus["ex10_history_authority"],
        )
        original_authority_revision = history.current_revision(authority_snapshot)
        initial_control = {
            name: getattr(control, name)
            for name in (
                "invocation",
                "bootstrap",
                "action",
                "name",
                "image_digest",
                "network_id",
                "container_id",
                "profile",
                "bootstrap_bytes",
                "initial_header_sha256",
                "exchange_id",
                "_environment_bytes",
                "_labels_bytes",
                "mounts",
                "approve",
                "runner",
                "_mutation_lock",
            )
        }
        p = control.profile.to_dict()
        cfg = json.loads(control.bootstrap_bytes)
        expected_root = (
            p["host_roots"]["evidence"]["path"] + "/.s11-operational/" + cfg["exchange_id"]
        )
        if publication.store.root != expected_root:
            raise ValueError()
        original_publication = {
            name: getattr(publication, name)
            for name in (
                "store",
                "bootstrap",
                "recheck",
                "rename",
                "namespace",
                "attempted",
                "_lock",
            )
        }
        providers = {
            name: envelope_configuration[name]
            for name in (
                "compose_observer",
                "container_observer",
                "running_source_reader",
                "bootstrap_verifier",
                "source_verifier",
                "session_verifier",
                "authority_verifier",
                "ready_verifier",
                "clock",
            )
        }
        raw_config = {
            name: value for name, value in envelope_configuration.items() if name not in providers
        }
        required_checks = raw_config.pop("application_required_checks")
        raw_configuration = base.canonical_bytes(raw_config)
        fixed = json.loads(raw_configuration)
        original_path_context = json.loads(bootstrap.path_context_bytes)
        envelope_path_context = dict(fixed["path_context"])
        context_sources = envelope_path_context.pop("running_sources")
        original_context_sources = original_path_context.pop("running_sources")
        if (
            fixed["profile_reference"]["path"] != str(bootstrap.profile_path)
            or fixed["profile_reference"]["sha256"] != cfg["profile_sha256"]
            or fixed["header_reference"]["sha256"] != cfg["header_sha256"]
            or envelope_path_context != original_path_context
            or type(context_sources) is not list
            or len(context_sources) != len(set(context_sources))
            or set(context_sources)
            != set(original_context_sources) | set(fixed["host_operational_sources"])
        ):
            raise ValueError()
        initial_bootstrap = {
            name: getattr(bootstrap, name) for name in bootstrap.__dataclass_fields__
        }
        # Derive the unchanged worker inventory without importing Executor/Django.
        pf_path = bootstrap.repo_root / "backend/apps/experiments_app/ex10_preflight.py"
        pf_source, _ = _capture_native_source(pf_path, error_type=LauncherError)
        inventory = next(
            ast.literal_eval(n.value)
            for n in ast.parse(pf_source).body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "RUNNING_SOURCES" for t in n.targets)
        )
        inventory = (*inventory, "backend/apps/experiments_app/ex10_prepare.py")
        if (
            type(cfg["source_hashes"]) is not dict
            or set(cfg["source_hashes"]) != set(inventory)
            or set(fixed["running_sources"]) != set(inventory[:-1])
            or any(
                cfg["source_hashes"].get(name) != digest
                for name, digest in {
                    **fixed["running_sources"],
                }.items()
            )
        ):
            raise ValueError()
        sources = {}
        source_hashes = {**cfg["source_hashes"], **fixed["host_operational_sources"]}
        for name, digest in source_hashes.items():
            path = bootstrap.repo_root / name
            raw, lease = _capture_native_source(path, error_type=LauncherError)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError()
            sources[name] = (path, raw, lease)
        references = {}
        for role, reference in (
            ("profile", fixed["profile_reference"]),
            ("header", fixed["header_reference"]),
            ("preflight", fixed["application_preflight_reference"]),
            ("authority", fixed["historical_authority_view_reference"]),
            ("ready", fixed["ready_state"]["report_reference"]),
            ("deployment", p["deployment_verification_reference"]),
        ):
            path = Path(reference["path"])
            raw, lease = _capture_native_source(path, error_type=LauncherError)
            if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
                raise ValueError()
            references[role] = (path, raw, lease)
        pending, original_bootstrap_current = control._admit()
        original_bootstrap_authority = original_bootstrap_current.check_authority_current
        pending_value = pending.to_dict()
        start = control.action == "start-official"
        fresh_required = control.action in ("start-official", "execute-official")
        if (start and attached_envelope_bytes is not None) or (
            not start
            and (
                type(attached_envelope_bytes) is not bytes
                or hashlib.sha256(attached_envelope_bytes).hexdigest()
                != pending_value["operational_envelope_sha256"]
                or fixed["application_preflight_reference"]["sha256"]
                != pending_value["preflight_report_sha256"]
            )
        ):
            raise ValueError()
        pending_bytes = pending._bytes
        checkpoint = pending._expected_tip, pending._expected_count
        if references["profile"][1] != control.profile._bytes or references["header"][
            1
        ] != apparatus["ex10_session_v2"].canonical_session_header_bytes(pending.header):
            raise ValueError()
        qualifier = None
        qualifier_state = None
        temporal = None
        temporal_deadline = None
        ready_deadline = None
        published = {}
        process = None
        process_value = None

        # B5 observes the four-service SUT. The owned one-off is a separate
        # instance, qualified by exact inspect below and bound through I/D;
        # it must neither replace the API backend nor become a fifth service.

        def pure():
            if (
                any(getattr(control, name) is not value for name, value in initial_control.items())
                or not control.started
                or invocation.native_bootstrap is not bootstrap
                or any(
                    getattr(bootstrap, name) is not value
                    for name, value in initial_bootstrap.items()
                )
                or any(
                    getattr(publication, name) is not value
                    for name, value in original_publication.items()
                )
                or history.current_revision(authority_snapshot) != original_authority_revision
                or any(
                    envelope_configuration.get(name) is not value
                    for name, value in providers.items()
                )
                or envelope_configuration.get("application_required_checks") is not required_checks
                or base.canonical_bytes(
                    {
                        name: value
                        for name, value in envelope_configuration.items()
                        if name not in providers and name != "application_required_checks"
                    }
                )
                != raw_configuration
                or (
                    process is not None
                    and any(
                        getattr(process, name) != value for name, value in process_value.items()
                    )
                )
            ):
                raise ValueError()
            original_bootstrap_authority()
            if (
                time.monotonic() >= preparation_deadline
                or (ready_deadline is not None and time.monotonic() >= ready_deadline)
                or (temporal_deadline is not None and time.monotonic() > temporal_deadline)
            ):
                raise ValueError()

        def retained():
            nonlocal temporal_deadline
            pure()
            if check_current() is not None:
                raise ValueError()
            original_bootstrap_current()
            control._inspect(allowed={"running"})
            if (
                initial_control["approve"](
                    profile=initial_control["profile"],
                    session=pending,
                    name=initial_control["name"],
                    image_digest=initial_control["image_digest"],
                    network_id=initial_control["network_id"],
                    labels=control.labels,
                    mounts=initial_control["mounts"],
                    environment=control.container_environment,
                    bootstrap_bytes=initial_control["bootstrap_bytes"],
                )
                is not None
            ):
                raise ValueError()
            original_bootstrap_current.check_approval_current()
            # This is a retained owned process, not a fresh global admission.
            # A witnessed B3 publication may already have advanced its remote
            # head before local atomic commit. Its ordinary readers stay blocked;
            # the original leases, B2/checkpoint and expiry remain mandatory here.
            codec = apparatus["ex10_session_v2"]
            current_session = codec.load_official_session(
                invocation.session_root / SESSION_FILE,
                context=bootstrap.context(),
                profile=initial_control["profile"],
                expected_tip=checkpoint[0],
                expected_count=checkpoint[1],
            )
            if (
                type(current_session) is not codec.OfficialSessionV2
                or current_session._bytes != pending_bytes
                or bootstrap.checkpoint() != checkpoint
            ):
                raise ValueError()
            publication.store.check()
            publication._namespace()
            if temporal is not None:
                # Read the fixed clock before final passive OS/source/B2 reads.
                # Retain the earliest remaining expiry budget; later observations
                # cannot renew the original B5/PF or ignore time spent in IO.
                clock_started = time.monotonic()
                current_time = providers["clock"]()
                if (
                    type(current_time) is not datetime
                    or current_time.tzinfo is None
                    or not temporal[0] <= current_time <= temporal[1]
                    or not 0 <= (current_time - temporal[2]).total_seconds() <= b5.MAX_AGE
                ):
                    raise ValueError()
                observed_deadline = clock_started + (temporal[1] - current_time).total_seconds()
                temporal_deadline = (
                    observed_deadline
                    if temporal_deadline is None
                    else min(temporal_deadline, observed_deadline)
                )
            for path, raw, lease in (*sources.values(), *references.values()):
                observed, current_lease = _capture_native_source(path, error_type=LauncherError)
                if observed != raw or current_lease != lease:
                    raise ValueError()
            codec = apparatus["ex10_session_v2"]
            final_session = codec.load_official_session(
                invocation.session_root / SESSION_FILE,
                context=bootstrap.context(),
                profile=initial_control["profile"],
                expected_tip=checkpoint[0],
                expected_count=checkpoint[1],
            )
            if (
                type(final_session) is not codec.OfficialSessionV2
                or final_session._bytes != pending_bytes
            ):
                raise ValueError()
            for role, (raw, lease) in published.items():
                path = Path(expected_root) / (role + ".json")
                observed, current_lease = _capture_native_source(path, error_type=LauncherError)
                if observed != raw or current_lease != lease:
                    raise ValueError()
            if qualifier_state is not None and any(
                getattr(qualifier, name) is not value for name, value in qualifier_state.items()
            ):
                raise ValueError()
            pure()

        retained()
        qualifier = b5.EnvelopeQualifier(
            **json.loads(raw_configuration),
            application_required_checks=required_checks,
            **providers,
        )
        qualifier_state = {
            name: getattr(qualifier, name)
            for name in (
                "config",
                "compose_observer",
                "container_observer",
                "running_reader",
                "bootstrap",
                "source",
                "session",
                "authority",
                "ready",
                "clock",
            )
        }
        if start:
            envelope = qualifier.build()
            envelope_bytes = envelope._bytes
        else:
            envelope_bytes = attached_envelope_bytes
            envelope = qualifier.qualify_attached(envelope_bytes, fresh_required=fresh_required)
        envelope_value = envelope.to_dict()
        temporal = (
            b5._time(envelope_value["checked_at"]),
            b5._time(envelope_value["expires_at"]),
            b5._pf_time(json.loads(references["preflight"][1])["checked_at"]),
        )
        if not fresh_required:
            temporal = None
        retained()
        if (
            type(qualifier.qualify_attached(envelope_bytes, fresh_required=fresh_required))
            is not b5.OperationalEnvelope
        ):
            raise ValueError()
        retained()
        process_value = _native_process_commitments(
            apparatus=apparatus,
            bootstrap_bytes=control.bootstrap_bytes,
            profile=control.profile,
            container_id=control.container_id,
            envelope_bytes=envelope_bytes,
        )
        process = op.ExecutorProcessIdentity(**process_value)
        process_bytes = base.canonical_bytes(process_value)
        publication.publish("process", process_bytes)
        raw, lease = _capture_native_source(
            Path(expected_root) / "process.json", error_type=LauncherError
        )
        if raw != process_bytes:
            raise ValueError()
        published["process"] = (process_bytes, lease)
        retained()
        # The envelope is the final existing-role publication unblocking the
        # worker. Capture READY's budget BEFORE that write, never after return.
        ready_deadline = min(time.monotonic() + 120, preparation_deadline)
        publication.publish("envelope", envelope_bytes)
        raw, lease = _capture_native_source(
            Path(expected_root) / "envelope.json", error_type=LauncherError
        )
        if raw != envelope_bytes:
            raise ValueError()
        published["envelope"] = (envelope_bytes, lease)
        retained()

        def current():
            retained()
            qualifier.qualify_attached(envelope_bytes, fresh_required=fresh_required)
            retained()

        def checked():
            try:
                current()
            except Exception:
                raise LauncherError("native-worker-bindings-uncertain") from None

        def completed_inputs(*, passive):
            if (
                any(getattr(control, n) is not v for n, v in initial_control.items())
                or invocation.native_bootstrap is not bootstrap
                or any(getattr(bootstrap, n) is not v for n, v in initial_bootstrap.items())
                or any(envelope_configuration.get(n) is not v for n, v in providers.items())
                or envelope_configuration.get("application_required_checks") is not required_checks
                or base.canonical_bytes(
                    {
                        n: v
                        for n, v in envelope_configuration.items()
                        if n not in providers and n != "application_required_checks"
                    }
                )
                != raw_configuration
                or any(getattr(qualifier, n) is not v for n, v in qualifier_state.items())
            ):
                raise ValueError()
            for path, original_raw, original_lease in (*sources.values(), *references.values()):
                if passive:
                    _native_check_source_lease(
                        path, original_lease, code="native-completed-deployment-uncertain"
                    )
                else:
                    raw, lease = _capture_native_source(path, error_type=LauncherError)
                    if raw != original_raw or lease != original_lease:
                        raise ValueError()
            if (
                _native_process_commitments(
                    apparatus=apparatus,
                    bootstrap_bytes=initial_control["bootstrap_bytes"],
                    profile=initial_control["profile"],
                    container_id=initial_control["container_id"],
                    envelope_bytes=envelope_bytes,
                )
                != process_value
            ):
                raise ValueError()
            if any(getattr(process, n) != v for n, v in process_value.items()):
                raise ValueError()
            for role, (original_raw, original_lease) in published.items():
                path = Path(expected_root) / (role + ".json")
                if passive:
                    _native_check_source_lease(
                        path, original_lease, code="native-completed-deployment-uncertain"
                    )
                else:
                    raw, lease = _capture_native_source(path, error_type=LauncherError)
                    if raw != original_raw or lease != original_lease:
                        raise ValueError()

        def completed_passive():
            # Only constructor-owned bindings and original metadata leases.
            # No provider, new descriptor, qualifier admission or budget renewal.
            try:
                completed_inputs(passive=True)
            except Exception:
                raise LauncherError("native-completed-deployment-uncertain") from None

        def completed(check_worker, check_authority_current):
            # Finished worker observation, never scientific permission.
            try:
                if not callable(check_worker) or check_worker() is not None:
                    raise ValueError()
                completed_inputs(passive=False)
                qualifier.qualify_attached(envelope_bytes, fresh_required=False)
                if check_worker() is not None:
                    raise ValueError()
                completed_inputs(passive=False)
                if check_authority_current() is not None:
                    raise ValueError()
                completed_passive()
            except Aborted:
                raise
            except Exception:
                raise LauncherError("native-completed-deployment-uncertain") from None

        checked()
        return NativePreparedWorkerBindings(
            process,
            process_bytes,
            envelope_bytes,
            ready_deadline,
            checked,
            completed,
            completed_passive,
        )
    except Exception:
        raise LauncherError("native-worker-bindings-uncertain") from None


@dataclass(frozen=True, slots=True)
class NativeBridgeGraph:
    """Internally composed native objects; construction grants no worker action."""

    sessions: Any
    policy: Any
    publisher: Any
    sources: Any
    journal: Any
    signer: Any
    broker: Any
    check_retained: Callable


def _compose_native_bridge(
    *,
    apparatus,
    session_bindings,
    session_configuration,
    policy_configuration,
    publisher_configuration,
    source_configuration,
    resource_plan,
    signer,
    action,
    exchange_id,
    expected_run_id,
    publish_role,
    holder_admitter,
    check_current,
    preparation_deadline,
    authority_snapshot,
    start_nonce=None,
):
    """Assemble the actual bridge core from fixed host primitives, once.

    The outer factory owns Docker, B5 and exclusive role publication. This
    component accepts their fixed raw host providers, never a caller-assembled
    qualifier, policy, Sources, journal, publisher, proof signer or broker.
    Installed external approval/history callbacks retain their existing
    contracts; no request can select them. Borrowed resources remain owned by
    the installation on every failure. There is no retry or partial return.
    """
    import time
    from types import MappingProxyType

    try:
        modules = MappingProxyType(dict(apparatus))
        op = modules["ex10_operational_context"]
        session_fields = {
            "path_context",
            "source_revision",
            "launcher_blobs",
            "expected_tip",
            "expected_count",
            "process",
            "approval_verifier",
            "deployment_observer",
        }
        policy_fields = {
            "bindings",
            "authorization_binding",
            "policy_store",
            "policy_name",
            "policy_schema_version",
            "approved_root_binding_sha256",
            "approval_verifier",
            "envelope_verifier",
            "required_preflight_checks",
            "resolve_policy_binding",
        }
        publisher_fields = {
            "ledger_store",
            "ledger_name",
            "ledger_identity",
            "candidate_store",
            "floor",
            "transport",
            "signer_pin",
            "clock",
            "clock_health",
        }
        source_fields = {
            "bindings",
            "authority_reader",
            "historical_contexts",
            "artifact_store",
            "certificate_store",
            "executor_fingerprint",
            "live_owner",
            "uncertainty_verifier",
            "recovery_sources",
            "lifecycle_sources",
        }
        configs = []
        for config, fields in (
            (session_configuration, session_fields),
            (policy_configuration, policy_fields),
            (publisher_configuration, publisher_fields),
            (source_configuration, source_fields),
        ):
            if type(config) is not dict or set(config) != fields:
                raise ValueError()
            configs.append(MappingProxyType(dict(config)))
        session_cfg, policy_cfg, publisher_cfg, source_cfg = configs
        if (
            type(resource_plan) is not op.NativePolicyResourcePlan
            or type(session_bindings) is not tuple
            or type(policy_cfg["bindings"]) is not tuple
            or type(source_cfg["bindings"]) is not tuple
            or type(publisher_cfg["floor"]) is not op.durable.DurableFloor
            or publisher_cfg["ledger_store"] is not resource_plan.ledger_store
            or publisher_cfg["ledger_name"] != resource_plan.ledger_name
            or publisher_cfg["ledger_identity"] != resource_plan.ledger_identity
            or not callable(check_current)
            or not callable(holder_admitter)
        ):
            raise ValueError()
        session_registry = op.HostArtifactRegistry(session_bindings)
        policy_registry = op.HostArtifactRegistry(policy_cfg["bindings"])
        source_registry = op.HostArtifactRegistry(source_cfg["bindings"])
        authorization_registry = op.HostArtifactRegistry((policy_cfg["authorization_binding"],))
        all_bindings = (
            *session_bindings,
            *policy_cfg["bindings"],
            *source_cfg["bindings"],
            policy_cfg["authorization_binding"],
        )
        floor = publisher_cfg["floor"]
        original_floor = (
            floor.store,
            floor.pins,
            floor.genesis_digest,
            op.sql.canonical_bytes(floor.genesis_binding),
        )
        stores = (
            *(b.store for b in all_bindings),
            policy_cfg["policy_store"],
            publisher_cfg["ledger_store"],
            publisher_cfg["candidate_store"],
            floor.store,
            source_cfg["artifact_store"],
            source_cfg["certificate_store"],
        )
        if not all(type(s) is op.durable.PrivateStore for s in stores):
            raise ValueError()
        retained_stores = tuple((s, s.root, s.identity, s.fd, s.resolver) for s in stores)
        original_bindings = tuple((b, b.name, b.reference, b.store) for b in all_bindings)
        original_maps = tuple(
            (r, r.bindings)
            for r in (session_registry, policy_registry, source_registry, authorization_registry)
        )
        original_files = tuple((r, n, r.read(n)) for r, _ in original_maps for n in r.bindings)
        if (
            type(policy_cfg["policy_name"]) is not str
            or policy_cfg["policy_name"] in ("", ".", "..")
            or any(c in policy_cfg["policy_name"] for c in ("/", "\\", "\x00"))
        ):
            raise ValueError()
        policy_path = Path(policy_cfg["policy_store"].root) / policy_cfg["policy_name"]
        original_policy_file = _capture_native_source(policy_path, error_type=LauncherError)
        if policy_cfg["policy_store"].read(str(policy_path)) != original_policy_file[0]:
            raise ValueError()

        graph = sessions = policy = publisher = sources = journal = proof_signer = broker = None
        policy_registry_original = policy_map_original = start_admitter_original = None
        authorization_guard_original = authorization_registry_original = (
            authorization_map_original
        ) = None

        def guard():
            if (
                type(preparation_deadline) not in (float, int)
                or not 0 < preparation_deadline < float("inf")
                or time.monotonic() >= preparation_deadline
            ):
                raise ValueError()
            resource_plan.check()
            if (
                floor.store is not original_floor[0]
                or floor.pins is not original_floor[1]
                or floor.genesis_digest != original_floor[2]
                or op.sql.canonical_bytes(floor.genesis_binding) != original_floor[3]
                or any(r.bindings is not original for r, original in original_maps)
                or any(
                    b.name != n or b.reference != raw or b.store is not store
                    for b, n, raw, store in original_bindings
                )
            ):
                raise ValueError()
            for store, root, identity, fd, resolver in retained_stores:
                if (
                    store.root != root
                    or store.identity != identity
                    or store.fd != fd
                    or store.resolver is not resolver
                ):
                    raise ValueError()
                store.check()
            if sessions is not None:
                if (
                    sessions.registry is not session_registry
                    or any(
                        getattr(sessions, name) != expected
                        for name, expected in (
                            ("path_context", session_cfg["path_context"]),
                            ("source_revision", session_cfg["source_revision"]),
                            ("launcher_blobs", session_cfg["launcher_blobs"]),
                            ("expected_tip", session_cfg["expected_tip"]),
                            ("expected_count", session_cfg["expected_count"]),
                        )
                    )
                    or (
                        sessions.process is not session_cfg["process"]
                        or sessions.approve is not session_cfg["approval_verifier"]
                        or sessions.observe is not session_cfg["deployment_observer"]
                    )
                ):
                    raise ValueError()
            if policy is not None and (
                policy._authorization_guard is not authorization_guard_original
                or policy._authorization_registry is not authorization_registry_original
                or policy._authorization_registry.bindings is not authorization_map_original
                or policy.sessions is not sessions
                or policy.registry is not policy_registry_original
                or policy.registry.bindings is not policy_map_original
                or policy.approve is not policy_cfg["approval_verifier"]
                or policy.envelope is not policy_cfg["envelope_verifier"]
                or policy.checks is not policy_cfg["required_preflight_checks"]
                or policy.root_binding != policy_cfg["approved_root_binding_sha256"]
                or policy._resource_plan is not resource_plan
            ):
                raise ValueError()
            if (
                policy is not None
                and authorization_guard_original() is not authorization_registry_original
            ):
                raise ValueError()
            if publisher is not None and any(
                getattr(publisher, attr) is not publisher_cfg[key]
                for attr, key in (
                    ("ledger", "ledger_store"),
                    ("candidates", "candidate_store"),
                    ("floor", "floor"),
                    ("transport", "transport"),
                    ("pin", "signer_pin"),
                    ("clock", "clock"),
                    ("health", "clock_health"),
                )
            ):
                raise ValueError()
            if publisher is not None and (
                publisher.name != publisher_cfg["ledger_name"]
                or publisher.identity != publisher_cfg["ledger_identity"]
            ):
                raise ValueError()
            if sources is not None and (
                sources.sessions is not sessions
                or sources.registry is not source_registry
                or sources.policy is not policy
                or sources.publisher is not publisher
                or sources.signer is not proof_signer
                or sources.pin is not resource_plan.bridge_pin
                or any(
                    getattr(sources, attr) is not source_cfg[key]
                    for attr, key in (
                        ("reader", "authority_reader"),
                        ("histories", "historical_contexts"),
                        ("artifacts", "artifact_store"),
                        ("certificates", "certificate_store"),
                        ("owner", "live_owner"),
                        ("uncertainty", "uncertainty_verifier"),
                        ("recovery", "recovery_sources"),
                        ("lifecycle", "lifecycle_sources"),
                    )
                )
                or sources.fingerprint != source_cfg["executor_fingerprint"]
            ):
                raise ValueError()
            if journal is not None:
                resource_plan.matches(policy=policy, journal=journal)
                if journal.sources is not sources or sources.journal is not journal:
                    raise ValueError()
            if proof_signer is not None and (
                proof_signer.action != action
                or proof_signer.exchange != exchange_id
                or (expected_run_id is not None and proof_signer.run_id != expected_run_id)
                or (
                    expected_run_id is None
                    and proof_signer.index == 0
                    and proof_signer.run_id is not None
                )
                or proof_signer._admit_start is not start_admitter_original
                or proof_signer.key is not signer
                or proof_signer.pin is not resource_plan.bridge_pin
                or proof_signer.admitted is not admitted
                or proof_signer.publish_role is not publish_role
            ):
                raise ValueError()
            if broker is not None and (
                broker.pin is not resource_plan.bridge_pin
                or broker.journal is not journal
                or broker.sessions is not sessions
                or broker.sources is not sources
                or broker.signer is not proof_signer
                or broker.admit is not holder_admitter
                or broker.role != "SESSION_EXECUTOR"
                or proof_signer.sources is not sources
                or proof_signer.journal is not journal
            ):
                raise ValueError()
            if graph is not None and any(
                getattr(graph, name) is not value
                for name, value in (
                    ("sessions", sessions),
                    ("policy", policy),
                    ("publisher", publisher),
                    ("sources", sources),
                    ("journal", journal),
                    ("signer", proof_signer),
                    ("broker", broker),
                    ("check_retained", guard),
                )
            ):
                raise ValueError()
            # The helper closes its own FD before returning. Retain the original
            # whole-file lease across the rest of graph assembly and its providers.
            if (
                _capture_native_source(policy_path, error_type=LauncherError)
                != original_policy_file
            ):
                raise ValueError()
            revision = op.history.current_revision(authority_snapshot)
            if (revision["authority_id"], revision["epoch"]) != (
                resource_plan.authority_id,
                resource_plan.authority_epoch,
            ) or time.monotonic() >= preparation_deadline:
                raise ValueError()

        def current():
            if check_current() is not None:
                raise ValueError()
            guard()

        current()
        sessions = op.NativeSessionQualifier(registry=session_registry, **session_cfg)
        admitted = sessions.qualify()
        policy = _prepare_native_policy(
            apparatus=modules,
            session_qualifier=sessions,
            registry=policy_registry,
            resource_plan=resource_plan,
            check_current=current,
            preparation_deadline=preparation_deadline,
            authority_snapshot=authority_snapshot,
            **{k: v for k, v in policy_cfg.items() if k != "bindings"},
        )
        authorization_guard_original = policy._authorization_guard
        authorization_registry_original = authorization_guard_original()
        authorization_map_original = authorization_registry_original.bindings
        policy_registry_original = policy.registry
        policy_map_original = policy.registry.bindings
        publisher = op.NativeOpenPublisher(**publisher_cfg)
        proof_signer = NativeBridgeProofSigner(
            apparatus=modules,
            signer=signer,
            bridge_pin=resource_plan.bridge_pin,
            action=action,
            exchange_id=exchange_id,
            expected_run_id=expected_run_id,
            admitted=admitted,
            publish_role=publish_role,
            start_nonce=start_nonce,
        )
        start_admitter_original = proof_signer._admit_start
        sources = op.NativeHostSources(
            session_qualifier=sessions,
            registry=source_registry,
            policy=policy,
            publisher=publisher,
            signer=proof_signer,
            bridge_pin=resource_plan.bridge_pin,
            **{k: v for k, v in source_cfg.items() if k != "bindings"},
        )
        journal = op.coordination.CoordinationJournal(
            store=resource_plan.sidecar_store,
            name=resource_plan.sidecar_name,
            identity=resource_plan.sidecar_identity,
            registry_binding=resource_plan.registry_binding,
            authority_id=resource_plan.authority_id,
            authority_epoch=resource_plan.authority_epoch,
            receipt_store=resource_plan.receipt_store,
            floor=resource_plan.floor,
            transport=resource_plan.transport,
            bridge_pin=resource_plan.bridge_pin,
            host_sources=sources,
            clock=resource_plan.clock,
            clock_health=resource_plan.clock_health,
            lock_store=resource_plan.lock_store,
            lock_name=resource_plan.lock_name,
            lock_identity=resource_plan.lock_identity,
        )
        proof_signer.bind_once(sources=sources, journal=journal)
        broker = op.NativeBridgeBroker(
            journal=journal,
            session_qualifier=sessions,
            signer=proof_signer,
            host_sources=sources,
            holder_admitter=holder_admitter,
        )
        graph = NativeBridgeGraph(
            sessions, policy, publisher, sources, journal, proof_signer, broker, guard
        )
        current()
        sources.qualify_authority(snapshot=authority_snapshot, native=admitted)
        floor.current()
        final_native = sessions.qualify()
        policy.qualify(native=final_native, journal=journal)
        if final_native != admitted:
            raise ValueError()
        if sources.authorization_reference != policy_cfg["authorization_binding"].reference:
            raise ValueError()
        for registry, name, original in original_files:
            if registry.read(name) != original:
                raise ValueError()
        guard()
        return graph
    except Exception:
        raise LauncherError("native-bridge-composition-denied") from None


class NativeWorkerAttachment:
    """Create the owned Docker client before PF/B5, then hand it off once.

    PID1 only supervises; Docker exec starts the worker that prepares PF. This
    technical preparation grants no bridge or scientific permission. The fixed
    factory's ready-deadline provider retains the instant before the publication
    that unblocks the relay. Full B5/process/session qualification and holder PoP
    remain mandatory in that factory and the native broker. No provisional
    process identity, new framing, remote cleanup, respawn or retry is used.
    """

    def __init__(self, *, control, read_ready_deadline, process_factory=None):
        import subprocess
        import time

        if (
            type(control) is not NativeOneOffDocker
            or control.invocation.native_bootstrap is not control.bootstrap
            or not control.started
            or not callable(read_ready_deadline)
            or (process_factory is not None and not callable(process_factory))
        ):
            raise LauncherError("native-attachment-context")
        self.control, self.bootstrap = control, control.bootstrap
        self.invocation, self.cid = control.invocation, control.container_id
        self.op = self.bootstrap.apparatus["ex10_operational_context"]
        self.factory = subprocess.Popen if process_factory is None else process_factory
        self.read_ready = read_ready_deadline
        self.owner, self._deadline_value = os.getpid(), time.monotonic() + 900
        self.created = self.admitted = self.transferred = False
        self._client = lambda: None
        self.adapter = None
        self._lock = threading.Lock()
        pending, self.original = control._admit()
        self.pending_bytes, self.profile_bytes = pending._bytes, control.profile._bytes
        self.checkpoint = pending._expected_tip, pending._expected_count
        with control._mutation():
            if getattr(control, "_worker_attachment", None) is not None:
                raise LauncherError("native-attachment-owned-client-already-claimed")
            control._worker_attachment = self
        self._check()

    @property
    def deadline(self):
        return self._deadline_value

    @property
    def process(self):
        return self._client()

    @property
    def argv(self):
        return (
            "/usr/bin/docker",
            "exec",
            "--interactive",
            self.cid,
            "/usr/local/bin/python",
            "-B",
            "/app/manage.py",
            "s11_ex10",
            "bridge",
        )

    @staticmethod
    def _process_options():
        import subprocess

        return {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
            "close_fds": True,
            "start_new_session": True,
            "env": {
                "PATH": "/usr/bin:/bin",
                "LC_ALL": "C",
                "DOCKER_HOST": "unix:///var/run/docker.sock",
            },
            "bufsize": 0,
        }

    def _pure(self):
        import time

        if os.getpid() != self.owner or time.monotonic() >= self.deadline:
            raise LauncherError("native-attachment-preparation-deadline")
        self.original.check_authority_current()

    def _check(self):
        self._pure()
        if (
            self.control.invocation is not self.invocation
            or self.control.bootstrap is not self.bootstrap
            or self.invocation.native_bootstrap is not self.bootstrap
            or self.control.container_id != self.cid
            or self.control.profile._bytes != self.profile_bytes
            or self.control._worker_attachment is not self
        ):
            raise LauncherError("native-attachment-owned-control")
        codec = self.bootstrap.apparatus["ex10_session_v2"]

        def load_pending():
            # This owned process already has a fully qualified bootstrap. Check
            # its original B2 and independent checkpoint without admitting a new
            # global authority snapshot during our own witness-to-ledger window.
            if self.bootstrap.checkpoint() != self.checkpoint:
                raise LauncherError("native-attachment-owned-intent")
            try:
                record = codec.load_official_session(
                    self.invocation.session_root / SESSION_FILE,
                    context=self.bootstrap.context(),
                    profile=self.control.profile,
                    expected_tip=self.checkpoint[0],
                    expected_count=self.checkpoint[1],
                )
            except codec.OfficialSessionError:
                raise LauncherError("native-attachment-owned-intent") from None
            if type(record) is not codec.OfficialSessionV2 or record._bytes != self.pending_bytes:
                raise LauncherError("native-attachment-owned-intent")
            value = record.to_dict()
            pending = value["in_flight"]
            if (
                self.invocation.action != self.control.action
                or len(pending) != 1
                or pending[0]["action"] != self.control.action
                or pending[0]["container_name"] != self.control.name
                or value["header_sha256"] != self.control.initial_header_sha256
            ):
                raise LauncherError("native-attachment-owned-intent")
            return record

        self.original.check_approval_current()
        self.original()
        load_pending()
        self.control._inspect(allowed={"running"})
        # Inspect and approval providers can perform IO. Retain the same B2,
        # source/approval leases and original authority expiry around that IO.
        after = load_pending()
        try:
            accepted = self.control.approve(
                profile=self.control.profile,
                session=after,
                name=self.control.name,
                image_digest=self.control.image_digest,
                network_id=self.control.network_id,
                labels=self.control.labels,
                mounts=self.control.mounts,
                environment=self.control.container_environment,
                bootstrap_bytes=self.control.bootstrap_bytes,
            )
        except Exception:
            raise LauncherError("native-oneoff-approval-required") from None
        if accepted is not None:
            raise LauncherError("native-oneoff-approval-required")
        self.original.check_approval_current()
        self.original()
        load_pending()
        self.original()
        self._pure()

    def create(self):
        import subprocess

        if not self._lock.acquire(blocking=False):
            raise LauncherBusy()
        try:
            if self.created:
                raise LauncherError("native-attachment-second-create")
            self.created = True
            self._check()
            process = self.factory(self.argv, **self._process_options())
            if type(process) is not subprocess.Popen:
                raise LauncherError("native-attachment-local-client")
            self._client = lambda: process
            reap = NativeWorkerAttachment._reap
            self._reap_owned = lambda: reap(process)
            guards = []
            for stream, access in (
                (process.stdin, os.O_WRONLY),
                (process.stdout, os.O_RDONLY),
                (process.stderr, os.O_RDONLY),
            ):
                fd = stream.fileno()
                info = os.fstat(fd)
                if (
                    not stat.S_ISFIFO(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or os.get_inheritable(fd)
                    or fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != access
                    or os.readlink("/proc/self/fd/" + str(fd)) != "pipe:[" + str(info.st_ino) + "]"
                ):
                    raise LauncherError("native-attachment-private-pipe")
                guards.append((stream, fd, info.st_dev, info.st_ino, access))
            if len({(g[2], g[3]) for g in guards}) != 3:
                raise LauncherError("native-attachment-private-pipe")
            guards = tuple(guards)

            def pipes():
                if self.process is not process or process.poll() is not None:
                    raise LauncherError("native-attachment-local-client")
                for stream, fd, device, inode, access in guards:
                    info = os.fstat(fd)
                    if (
                        stream.fileno() != fd
                        or (info.st_dev, info.st_ino) != (device, inode)
                        or not stat.S_ISFIFO(info.st_mode)
                        or info.st_uid != os.geteuid()
                        or os.get_inheritable(fd)
                        or fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != access
                        or os.readlink("/proc/self/fd/" + str(fd)) != "pipe:[" + str(inode) + "]"
                    ):
                        raise LauncherError("native-attachment-private-pipe")

            def retained():
                pipes()
                self._check()
                pipes()
                self._pure()

            self._retained = retained
            self._pipe_binding = lambda: tuple((g[2], g[3], g[4]) for g in guards)
            retained()
            return None
        except BaseException:
            self.abort_client()
            raise
        finally:
            self._lock.release()

    def admit(self, *, identity, observer):
        import time

        if not self._lock.acquire(blocking=False):
            raise LauncherBusy()
        try:
            if not self.created or self.admitted:
                raise LauncherError("native-attachment-second-admission")
            self.admitted = True
            if (
                type(identity) is not self.op.ExecutorProcessIdentity
                or identity.container_id != self.cid
                or not callable(observer)
            ):
                raise LauncherError("native-attachment-process-binding")
            self._retained()
            original_pipes = self._pipe_binding()
            ready_deadline = self.read_ready()
            now = time.monotonic()
            if type(ready_deadline) not in (int, float) or not now < ready_deadline <= now + 120:
                raise LauncherError("native-attachment-ready-deadline")
            # Retain the first publication deadline; never call its provider to
            # renew it. This also fences the constructor/observer/FD handoff IO.
            deadline = min(ready_deadline, self.deadline)
            handed_over = False
            transferred_pipes = None

            retired = set()

            def check_transferred(*, allow_retirement=False):
                if transferred_pipes is None:
                    raise LauncherError("native-attachment-transferred-pipe")
                owned_channel, fields, bindings = transferred_pipes
                live = (owned_channel.write_fd, owned_channel.read_fd, owned_channel.error_fd)
                expected = {}
                for index, (fd, binding) in enumerate(zip(fields, bindings, strict=True)):
                    device, inode, access = binding
                    if live[index] is None:
                        # Frozen finish retires stdin only after all frames;
                        # frozen _wait retires stderr after its observed EOF.
                        # Recognize those transitions only before provider IO.
                        if (
                            (
                                index == 1
                                and not (
                                    owned_channel.finished
                                    and owned_channel.closed
                                    and owned_channel.position == len(owned_channel.steps)
                                    and not owned_channel.identities
                                )
                            )
                            or (index == 0 and owned_channel.position != len(owned_channel.steps))
                            or fd in owned_channel.identities
                            or (fd not in retired and not allow_retirement)
                        ):
                            raise LauncherError("native-attachment-transferred-pipe")
                        # The channel records the original pipe object before
                        # closing it. The numeric FD may already belong to an
                        # unrelated later open, so never inspect or adopt it.
                        if owned_channel.retired_identities.get(fd) != (device, inode):
                            raise LauncherError("native-attachment-transferred-pipe")
                        retired.add(fd)
                        continue
                    if live[index] != fd or fd in retired:
                        raise LauncherError("native-attachment-transferred-pipe")
                    info = os.fstat(fd)
                    if (
                        (info.st_dev, info.st_ino) != (device, inode)
                        or not stat.S_ISFIFO(info.st_mode)
                        or info.st_uid != os.geteuid()
                        or os.get_inheritable(fd)
                        or fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != access
                        or os.readlink("/proc/self/fd/" + str(fd)) != "pipe:[" + str(inode) + "]"
                    ):
                        raise LauncherError("native-attachment-transferred-pipe")
                    expected[fd] = (device, inode)
                if owned_channel.identities != expected:
                    raise LauncherError("native-attachment-transferred-pipe")
                return live, tuple(sorted(expected.items()))

            def current():
                if handed_over:
                    # The peer may reach EOF while a retained provider is running.
                    # Accept only monotonic retirement that check_transferred()
                    # proves closed with the original binding; replacement,
                    # descriptor reuse and resurrection remain rejected.
                    check_transferred(allow_retirement=True)
                    self._check()
                    check_transferred(allow_retirement=True)
                else:
                    self._retained()
                observed = observer()
                if handed_over:
                    check_transferred(allow_retirement=True)
                    self._check()
                    check_transferred(allow_retirement=True)
                else:
                    self._retained()
                if observed != identity or time.monotonic() >= deadline:
                    raise LauncherError("native-attachment-process-binding")
                self._pure()
                return observed

            def handoff(argv, **options):
                if self.transferred or argv != self.argv or options != self._process_options():
                    raise LauncherError("native-attachment-handoff")
                self.transferred = True
                self._retained()
                current()
                return self.process

            adapter = self.op.FixedDockerAdapter(
                identity=identity, observer=current, process_factory=handoff
            )
            self.adapter = adapter
            budget = deadline - time.monotonic()
            if not 0 < budget <= 120:
                raise LauncherError("native-attachment-ready-deadline")
            channel = adapter.open(
                exchange="START" if self.control.action == "start-official" else "SINGLE_RPC",
                exchange_id=self.control.exchange_id,
                budget=budget,
            )
            try:
                if not all(
                    s.closed for s in (self.process.stdin, self.process.stdout, self.process.stderr)
                ):
                    raise LauncherError("native-attachment-original-pipes-not-transferred")
                transferred_pipes = (
                    channel,
                    (channel.write_fd, channel.read_fd, channel.error_fd),
                    original_pipes,
                )
                handed_over = True
                check_transferred()
                channel.deadline = min(channel.deadline, deadline)
                channel._check()
                self._pure()
            except BaseException:
                channel.close()
                raise
            return adapter, channel
        except BaseException:
            self.abort_client()
            raise
        finally:
            self._lock.release()

    def abort_client(self):
        reap = getattr(self, "_reap_owned", None)
        if reap is not None:
            reap()

    @staticmethod
    def _reap(process):
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        try:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=1)
        except Exception:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=1)
            except Exception:
                pass


def _native_parse_public_result(raw: bytes, *, maximum: int) -> dict[str, Any]:
    """Qualify unchanged scientific JSON, separately from B3 authority counters."""
    if type(raw) is not bytes or not 0 < len(raw) <= maximum:
        raise ValueError("native-public-result-size")

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("native-public-result-duplicate-key")
            value[key] = item
        return value

    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=unique,
        parse_constant=base._reject_constant,
    )
    if type(value) is not dict or base.canonical_bytes(value) != raw:
        raise ValueError("native-public-result-noncanonical")
    return value


def _native_check_source_lease(path, lease, *, code):
    """Compare original path identities after reads close; never adopt a new lease."""

    def identity(info):
        return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid

    path = Path(path)
    directories, expected_file = lease
    # _capture_native_source omits / and includes every named parent.
    parents = tuple(Path(*path.parts[:i]) for i in range(2, len(path.parts)))
    if not path.is_absolute() or len(parents) != len(directories):
        raise LauncherError(code)
    try:
        for parent, expected in zip(parents, directories, strict=True):
            info = os.lstat(parent)
            if not stat.S_ISDIR(info.st_mode) or identity(info) != expected:
                raise LauncherError(code)
        info = os.lstat(path)
        current = identity(info) + (info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if not stat.S_ISREG(info.st_mode) or current != expected_file:
            raise LauncherError(code)
        for parent, expected in reversed(tuple(zip(parents, directories, strict=True))):
            if identity(os.lstat(parent)) != expected:
                raise LauncherError(code)
    except OSError:
        raise LauncherError(code) from None


class _NativeExistingRunRefusal(LauncherError):
    """Private provisional rejection; never a coherent worker result."""

    def __init__(self):
        super().__init__("native-worker-result-invalid")


class _NativeExistingRunDiagnostic:
    """One-use failure-only diagnosis anchored to the original START baseline."""

    def __init__(self, *, prior_guard, expected_run_id, pending_bytes):
        self.guard, self.expected_run, self.pending = prior_guard, expected_run_id, pending_bytes
        self.contains_prior = prior_guard._contains_prior
        self.check_current = prior_guard._check_current
        self.observation = self.original = self.signal = None

    def qualify(self, observation, raw, value, problems, original, resolver, guards):
        if (
            self.signal is not None
            or observation.action != "start-official"
            or observation.run_id != self.expected_run
            or problems != ["run-id-differs"]
            or type(value.get("run_id")) is not str
            or not self.contains_prior(value["run_id"])
        ):
            return None
        observation._guards(resolver, guards)
        result_path = Path(observation.root) / observation.name
        result = _capture_native_source(result_path, error_type=LauncherError)
        if result[0] != raw:
            raise LauncherError("native-worker-result-lease")
        # Normalize capture's file signature to the resolver's original stamp.
        info = result[1][1]
        captured_stamp = (
            info[0],
            info[1],
            info[3],
            info[4],
            info[2],
            info[5],
            info[6],
            info[7],
            info[8],
        )
        if len(guards) != 1 or captured_stamp != guards[0][3]:
            raise LauncherError("native-worker-result-lease")
        observation._guards(resolver, guards)
        session_path = observation.control.invocation.session_root / SESSION_FILE
        session = _capture_native_source(session_path, error_type=LauncherError)
        if session[0] != self.pending or observation.intent_bytes != self.pending:
            raise LauncherError("native-worker-result-intent-changed")
        original.check_pending_exact()
        original()
        original.check_pending_checkpoint_only()
        original.check_source_leases_current()
        _native_check_source_lease(
            session_path, session[1], code="native-worker-result-intent-changed"
        )
        _native_check_source_lease(result_path, result[1], code="native-worker-result-lease")
        original.check_authority_current()
        self.observation, self.original = observation, original
        self.result_path, self.result = result_path, result
        self.session_path, self.session = session_path, session
        self.signal = _NativeExistingRunRefusal()
        return self.signal

    def finish(self):
        observation, original = self.observation, self.original
        if (
            type(self.signal) is not _NativeExistingRunRefusal
            or observation._start_failure_diagnostic is not self
            or observation.run_id != self.expected_run
            or observation.intent_bytes != self.pending
        ):
            raise LauncherError("native-worker-result-context")
        observation._binding()
        original()
        original.check_authority_current()
        original.check_pending_exact()
        worker_code = observation.control.worker_exit()
        if type(worker_code) is not int or worker_code != 0:
            raise LauncherError("native-worker-exit-unsuccessful")
        original()
        original.check_authority_current()
        self.guard(self.expected_run, self.pending)
        original()
        original.check_authority_current()
        for path, expected, code in (
            (self.session_path, self.session, "native-worker-result-intent-changed"),
            (self.result_path, self.result, "native-worker-result-lease"),
        ):
            if _capture_native_source(path, error_type=LauncherError) != expected:
                raise LauncherError(code)
        # These source/checkpoint checks follow diagnostic reads and their closures.
        original()
        original.check_pending_exact()
        original.check_pending_checkpoint_only()
        observation._binding()
        original.check_source_leases_current()
        _native_check_source_lease(
            self.session_path, self.session[1], code="native-worker-result-intent-changed"
        )
        _native_check_source_lease(
            self.result_path, self.result[1], code="native-worker-result-lease"
        )
        self.check_current()
        original.check_authority_current()

    def fail(self):
        if self.observation is not None:
            self.observation.failed = True


class NativeWorkerResultObservation:
    """Observe PID1 and its existing private output without advancing an intent.

    This does not attest a bridge exchange, terminal publication or permission
    to return public output. The action engine must separately finish the real
    broker/client and complete its authority and RETIRE gates. Each observation
    is bounded by the original B3 lease; no scientific timeout, retry, worker
    termination, cleanup or custody write is introduced here.
    """

    def __init__(self, *, control, expected_run_id, reconciled=None):
        import threading

        if (
            type(control) is not NativeOneOffDocker
            or type(expected_run_id) is not str
            or _RUN_ID.fullmatch(expected_run_id) is None
            or reconciled not in (None, *_DECISIONS)
        ):
            raise LauncherError("native-worker-result-context")
        self.control, self.bootstrap = control, control.bootstrap
        self.op = self.bootstrap.apparatus["ex10_operational_context"]
        self.action, self.run_id, self.reconciled = control.action, expected_run_id, reconciled
        self.profile_bytes = self.bootstrap.apparatus[
            "ex10_runtime_profile"
        ].canonical_runtime_profile_bytes(control.profile)
        profile = control.profile.to_dict()
        root = profile["host_roots"]["evidence"]
        self.root, self.owner, self.group = root["path"], root["owner_uid"], root["owner_gid"]
        self.exchange, self.container_id, self.runner = (
            control.exchange_id,
            control.container_id,
            control.runner,
        )
        self.bootstrap_bytes = control.bootstrap_bytes
        self.name = "executor-result." + self.exchange + ".json"
        self.intent_bytes = None
        self.lock, self.failed = threading.Lock(), False
        self._start_failure_diagnostic = None

    def _binding(self):
        control = self.control
        codec = self.bootstrap.apparatus["ex10_runtime_profile"]
        if (
            self.failed
            or control.bootstrap is not self.bootstrap
            or control.runner is not self.runner
            or control.action != self.action
            or control.exchange_id != self.exchange
            or control.bootstrap_bytes != self.bootstrap_bytes
            or control.container_id != self.container_id
            or self.container_id is None
            or not control.started
            or codec.canonical_runtime_profile_bytes(control.profile) != self.profile_bytes
            or (os.geteuid(), os.getegid()) != (self.owner, self.group)
        ):
            raise LauncherError("native-worker-result-context")

    def _admit(self):
        self._binding()
        session, recheck = self.control._admit()
        if self.intent_bytes is None:
            self.intent_bytes = session._bytes
        if session._bytes != self.intent_bytes:
            raise LauncherError("native-worker-result-intent-changed")
        control, bootstrap = self.control, self.bootstrap
        checkpoint = session._expected_tip, session._expected_count
        checkpoint_reader = bootstrap.read_checkpoint
        identities = {name: getattr(control, name) for name in ("invocation", "profile", "approve")}
        facts = {
            name: getattr(control, name)
            for name in (
                "name",
                "image_digest",
                "network_id",
                "mounts",
                "initial_header_sha256",
                "_labels_bytes",
                "_environment_bytes",
            )
        }
        approval_bytes = base.canonical_bytes(
            {"labels": control.labels, "environment": control.container_environment}
        )
        codec = bootstrap.apparatus["ex10_session_v2"]

        def binding():
            self._binding()
            if (
                any(getattr(control, name) is not value for name, value in identities.items())
                or any(getattr(control, name) != value for name, value in facts.items())
                or control.invocation.action != self.action
            ):
                raise LauncherError("native-worker-result-context")

        def pending():
            binding()
            if bootstrap.checkpoint() != checkpoint:
                raise LauncherError("native-worker-result-intent-changed")
            current = codec.load_official_session(
                control.invocation.session_root / SESSION_FILE,
                context=bootstrap.context(),
                profile=identities["profile"],
                expected_tip=checkpoint[0],
                expected_count=checkpoint[1],
            )
            if type(current) is not codec.OfficialSessionV2 or current._bytes != self.intent_bytes:
                raise LauncherError("native-worker-result-intent-changed")
            binding()

        def approval():
            binding()
            recheck.check_approval_current()
            delivered = json.loads(approval_bytes)
            try:
                accepted = identities["approve"](
                    profile=identities["profile"],
                    session=session,
                    name=facts["name"],
                    image_digest=facts["image_digest"],
                    network_id=facts["network_id"],
                    labels=delivered["labels"],
                    mounts=facts["mounts"],
                    environment=delivered["environment"],
                    bootstrap_bytes=self.bootstrap_bytes,
                )
                unchanged = base.canonical_bytes(delivered) == approval_bytes
            except Exception:
                raise LauncherError("native-oneoff-approval-required") from None
            if accepted is not None or not unchanged:
                raise LauncherError("native-oneoff-approval-required")
            binding()
            recheck.check_approval_current()
            recheck()

        def retained_pending():
            # Retain the original admitted B2/CP and approval facts. Ordinary
            # global admission stays separate during witness-to-local commit.
            recheck()
            pending()
            approval()
            pending()
            recheck()
            recheck.check_authority_current()

        def final_pending():
            # Check providers before the final B2 read, then close only original
            # passive source leases and authority expiry, never new approval IO.
            pending()
            recheck()
            recheck.check_authority_current()

        def checkpoint_only():
            # Last original provider observation; no new B2 read or FD closure.
            binding()
            if bootstrap.read_checkpoint is not checkpoint_reader:
                raise LauncherError("native-worker-result-intent-changed")
            observed = checkpoint_reader()
            if (
                type(observed) is not tuple
                or len(observed) != 2
                or type(observed[0]) is not str
                or type(observed[1]) is not int
                or observed != checkpoint
                or bootstrap.read_checkpoint is not checkpoint_reader
            ):
                raise LauncherError("native-worker-result-intent-changed")
            binding()

        recheck.check_pending_checkpoint_only = checkpoint_only
        recheck.check_pending_current = retained_pending
        recheck.check_pending_exact = final_pending
        recheck.check_owned_approval_current = approval
        return recheck

    def _guards(self, resolver, guards):
        resolver._check_lease()
        if os.fstat(resolver._root_fd).st_gid != self.group:
            raise LauncherError("native-worker-result-lease")
        for parent, name, child, expected in guards:
            current = os.fstat(child)
            mapped = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                current.st_gid != self.group
                or self.op.evidence._stamp(current) != expected
                or self.op.evidence._stamp(mapped) != expected
            ):
                raise LauncherError("native-worker-result-lease")

    @contextlib.contextmanager
    def capture(self):
        """Yield None while PID1 runs, otherwise private bytes/value and a lease check."""
        if not self.lock.acquire(blocking=False):
            raise LauncherBusy()
        evidence = self.op.evidence
        try:
            original = self._admit()
            code = self.control.worker_exit()
            original()
            if code is None:
                original.check_authority_current()
                yield None
                original.check_authority_current()
                return
            if type(code) is not int or code != 0:
                raise LauncherError("native-worker-exit-unsuccessful")
            with evidence.ConfinedArtifactResolver(
                self.root, expected_owner=self.owner
            ) as resolver:
                with contextlib.ExitStack() as retained:
                    guards = []
                    raw, _ = resolver._read(
                        self.name, self.op.wire.MAX_REQUEST_BYTES, retained, guards
                    )
                    try:
                        value = _native_parse_public_result(
                            raw, maximum=self.op.wire.MAX_REQUEST_BYTES
                        )
                        if type(value) is not dict:
                            raise ValueError()
                        problems, _, _ = action_problems(
                            self.action, value, run_id=self.run_id, reconciled=self.reconciled
                        )
                    except Exception:
                        raise LauncherError("native-worker-result-invalid") from None
                    if problems:
                        diagnostic = self._start_failure_diagnostic
                        if type(diagnostic) is _NativeExistingRunDiagnostic:
                            signal = diagnostic.qualify(
                                self, raw, value, problems, original, resolver, guards
                            )
                            if signal is not None:
                                raise signal
                        raise LauncherError("native-worker-result-invalid")

                    import threading

                    owner, invocation = threading.get_ident(), self.control.invocation
                    active, completion = True, None
                    scope_lock = threading.Lock()

                    @contextlib.contextmanager
                    def scope():
                        if (
                            not active
                            or threading.get_ident() != owner
                            or self.control.invocation is not invocation
                        ):
                            raise LauncherError("native-worker-observation-scope")
                        if not scope_lock.acquire(blocking=False):
                            raise LauncherBusy()
                        try:
                            yield
                        except BaseException:
                            self.failed = True
                            raise
                        finally:
                            scope_lock.release()

                    def retained():
                        # This observes the original process/result and lease,
                        # never permission or a replacement B2 state.
                        self._binding()
                        current_code = self.control.worker_exit()
                        if type(current_code) is not int or current_code != 0:
                            raise LauncherError("native-worker-exit-unsuccessful")
                        original()
                        original.check_owned_approval_current()
                        self._guards(resolver, guards)
                        if base.canonical_bytes(value) != raw:
                            raise LauncherError("native-worker-result-invalid")
                        original.check_authority_current()

                    def exact_completion(commit):
                        if type(commit) is not NativeSessionActionCommit:
                            raise LauncherError("native-worker-completion-binding")
                        receipt = commit.completed_observation()
                        if (
                            receipt[0] is not invocation
                            or receipt[1] is not self.bootstrap
                            or receipt[2] != self.action
                            or receipt[3] != self.intent_bytes
                            or receipt[5] != raw
                            or receipt[6] != self.profile_bytes
                        ):
                            raise LauncherError("native-worker-completion-binding")
                        return receipt

                    def recheck():
                        with scope():
                            if completion is None:
                                original.check_pending_current()
                            else:
                                commit, receipt = completion
                                if exact_completion(commit) != receipt:
                                    raise LauncherError("native-worker-completion-binding")
                            retained()
                            if completion is None:
                                original.check_pending_exact()
                            if completion is not None:
                                if exact_completion(completion[0]) != completion[1]:
                                    raise LauncherError("native-worker-completion-binding")
                            original.check_authority_current()
                        return None

                    def check_retained():
                        with scope():
                            retained()
                        return None

                    def bind_completed(commit):
                        nonlocal completion
                        with scope():
                            if completion is not None:
                                raise LauncherError("native-worker-completion-bound")
                            receipt = exact_completion(commit)
                            retained()
                            if exact_completion(commit) != receipt:
                                raise LauncherError("native-worker-completion-binding")
                            original.check_authority_current()
                            completion = commit, receipt
                        return None

                    def check_authority_current():
                        if (
                            threading.get_ident() != owner
                            or self.control.invocation is not invocation
                        ):
                            raise LauncherError("native-worker-observation-scope")
                        self._binding()
                        original.check_authority_current()
                        return None

                    recheck.check_retained = check_retained
                    recheck.check_authority_current = check_authority_current
                    recheck.bind_completed = bind_completed
                    try:
                        recheck()
                        yield raw, value, recheck
                        recheck()
                    finally:
                        active = False
            # Closing retained FDs and the root resolver is real IO. The final
            # local fence uses the first observed snapshot, never a refresh.
            original.check_authority_current()
        except evidence.OpenEvidenceError:
            self.failed = True
            raise LauncherError("native-worker-result-unsafe") from None
        except BaseException as error:
            diagnostic = self._start_failure_diagnostic
            if not (
                type(diagnostic) is _NativeExistingRunDiagnostic
                and error is diagnostic.signal
                and diagnostic.observation is self
            ):
                self.failed = True
            raise
        finally:
            self.lock.release()


def _native_check_refusal_input(path, captured, *, apparatus):
    """Passive original private-file/ancestor facts, with no fresh descriptor."""
    prior = apparatus["ex10_prior_custody"]
    raw, components, frozen = captured
    for name, expected in components:
        info = os.lstat(name)
        observed = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
        if not stat.S_ISDIR(info.st_mode) or observed != expected:
            raise LauncherError("native-refusal-original-input-changed")
    if frozen[1] or len(frozen[0]) != 1 or frozen[0][0][0] != (path.name,):
        raise LauncherError("native-refusal-original-input-context")
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or prior._PriorFDReader._stamp(info) != frozen[0][0][1]:
        raise LauncherError("native-refusal-original-input-changed")
    for name, expected in reversed(components):
        info = os.lstat(name)
        if (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid) != expected:
            raise LauncherError("native-refusal-original-input-changed")


def _native_capture_refusal_input(path, expected, *, apparatus, check_current):
    """Pin original regular metadata before any callback or confined data read."""
    prior, rp = apparatus["ex10_prior_custody"], apparatus["ex10_runtime_profile"]
    if type(path) is not type(Path()) or not path.is_absolute() or type(expected) is not bytes:
        raise LauncherError("native-refusal-original-input-context")
    root_fd, lease = rp._walk(str(path.parent), "directory")
    try:
        root_info = os.fstat(root_fd)
        if (root_info.st_uid, root_info.st_gid, stat.S_IMODE(root_info.st_mode)) != (
            os.geteuid(),
            os.getegid(),
            0o700,
        ):
            raise LauncherError("native-refusal-original-input-context")
        components = []
        for name, device, inode in lease:
            info = os.lstat(name)
            if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (device, inode):
                raise LauncherError("native-refusal-original-input-changed")
            components.append(
                (name, (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid))
            )
        components = tuple(components)
        leaf = os.stat(path.name, dir_fd=root_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(leaf.st_mode)
            or (leaf.st_uid, leaf.st_gid, stat.S_IMODE(leaf.st_mode), leaf.st_nlink)
            != (os.geteuid(), os.getegid(), 0o600, 1)
            or leaf.st_size != len(expected)
        ):
            raise LauncherError("native-refusal-original-input-context")
        frozen = ((((path.name,), prior._PriorFDReader._stamp(leaf)),), ())
        captured = (expected, components, frozen)

        def current():
            _native_check_refusal_input(path, captured, apparatus=apparatus)
            if check_current() is not None:
                raise LauncherError("native-refusal-original-input-context")
            _native_check_refusal_input(path, captured, apparatus=apparatus)

        reader = prior._PriorFDReader(root_fd, current)
        reader._fact((path.name,), leaf)
        raw, _ = reader.read(path.name)
        if raw != expected or reader.freeze() != frozen:
            raise LauncherError("native-refusal-private-payload-invalid")
        reader.verify(frozen)
        current()
    finally:
        os.close(root_fd)
    current()
    return captured


class _NativeScientificRefusal(Exception):
    """One identity-owned provisional negative, never a successful completion."""


class NativeScientificRefusalObservation:
    """Observe genuine scientific2; keep original uncertain B2 and checkpoint."""

    def __init__(
        self,
        *,
        control,
        pending,
        pending_capture,
        expected_run_id,
        installation,
        original,
        host_deadline,
        final_io,
        final_passive,
    ):
        import time

        if (
            type(control) is not NativeOneOffDocker
            or control.action != "declare-executor-gone"
            or type(pending) is not control.bootstrap.apparatus["ex10_session_v2"].OfficialSessionV2
            or not all(callable(f) for f in (installation, original, final_io, final_passive))
            or type(host_deadline) not in (int, float)
            or not time.monotonic() < host_deadline < float("inf")
        ):
            raise LauncherError("native-refusal-observation-context")
        obs = NativeWorkerResultObservation(control=control, expected_run_id=expected_run_id)
        pending_bytes = pending._bytes
        checkpoint = pending._expected_tip, pending._expected_count
        session_path = control.invocation.session_root / SESSION_FILE
        result_path = Path(obs.root) / obs.name
        apparatus = obs.bootstrap.apparatus
        owner = os.getpid(), threading.get_ident()
        checkpoint_reader = control.bootstrap.read_checkpoint
        obs.intent_bytes = pending_bytes
        obs_values = tuple(vars(obs).items())
        control_values = tuple(vars(control).items())
        bootstrap_values = tuple(
            (n, getattr(control.bootstrap, n)) for n in control.bootstrap.__dataclass_fields__
        )
        obs_binding, obs_admit, worker_exit = obs._binding, obs._admit, control.worker_exit
        source_check, source_authority = (
            original.check_source_leases_current,
            original.check_authority_current,
        )
        cfg = json.loads(control.bootstrap_bytes)
        options = cfg["command_options"]
        if (
            pending_capture[0] != pending_bytes
            or cfg["action"] != "finalize"
            or options["declare_executor_gone"] is not True
            or options["abandon_reason"] is not None
            or options["run_id"] != expected_run_id
        ):
            raise LauncherError("native-refusal-observation-context")
        admitted = result_capture = signal = None
        admitted_functions = ()
        attempted = finished = False
        self.observation, self.pending, self.checkpoint = obs, pending_bytes, checkpoint
        self.pending_capture, self.session_path, self.result_path = (
            pending_capture,
            session_path,
            result_path,
        )
        self.installation, self.source_original, self.host_deadline = (
            installation,
            original,
            host_deadline,
        )
        self.owner, self.checkpoint_reader = owner, checkpoint_reader
        self.original = self.result_capture = self.signal = None
        self.attempted = self.finished = False
        fixed = tuple(vars(self).items())
        changing = {"original", "result_capture", "signal", "attempted", "finished"}

        def binding():
            if (
                finished
                or owner != (os.getpid(), threading.get_ident())
                or any(getattr(self, n) is not v for n, v in fixed if n not in changing)
                or self.original is not admitted
                or self.result_capture is not result_capture
                or self.signal is not signal
                or self.attempted is not attempted
                or self.finished is not finished
                or any(getattr(self, n) is not v for n, v in methods)
                or any(getattr(obs, n) is not v for n, v in obs_values)
                or any(getattr(control, n) is not v for n, v in control_values)
                or any(getattr(control.bootstrap, n) is not v for n, v in bootstrap_values)
                or original.check_source_leases_current is not source_check
                or original.check_authority_current is not source_authority
                or any(getattr(admitted, n) is not v for n, v in admitted_functions)
                or control.bootstrap.read_checkpoint is not checkpoint_reader
            ):
                raise LauncherError("native-refusal-observation-context")
            obs_binding()

        def passive():
            binding()
            _native_check_refusal_input(session_path, pending_capture, apparatus=apparatus)
            if result_capture is not None:
                _native_check_refusal_input(result_path, result_capture, apparatus=apparatus)

        def authority():
            binding()
            source_authority()
            if admitted is not None:
                dict(admitted_functions)["check_authority_current"]()
            if time.monotonic() >= host_deadline:
                raise Aborted("timeout")

        def initial_current():
            binding()
            installation()
            passive()
            code = worker_exit()
            if type(code) is not int or code != EXIT_TERMINAL:
                raise LauncherError("native-refusal-worker-unsuccessful")
            installation()
            source_check()
            passive()
            authority()

        def retained():
            if admitted is None:
                raise LauncherError("native-refusal-observation-context")
            binding()
            installation()
            passive()
            functions = dict(admitted_functions)
            functions["check_pending_current"]()
            code = worker_exit()
            if type(code) is not int or code != EXIT_TERMINAL:
                raise LauncherError("native-refusal-worker-unsuccessful")
            functions["check_pending_exact"]()
            functions["check_pending_checkpoint_only"]()
            # The last external CP/provider may have mutated Factory bindings.
            installation()
            binding()
            source_check()
            functions["check_source_leases_current"]()
            passive()
            authority()

        def qualify():
            nonlocal attempted, admitted, admitted_functions, result_capture, signal
            binding()
            if attempted:
                raise LauncherError("native-refusal-observation-consumed")
            attempted = self.attempted = True
            # Seed original inode/stamp before admission/provider or client-close
            # callbacks; capture itself uses confined O_PATH-qualified reads.
            expected = base.canonical_bytes(
                {"refused": True, "terminal": True, "reasons": ["run-finalized"]}
            )
            result_capture = _native_capture_refusal_input(
                result_path, expected, apparatus=apparatus, check_current=initial_current
            )
            self.result_capture = result_capture
            initial_current()
            admitted = obs_admit()
            self.original = admitted
            admitted_functions = tuple(
                (n, getattr(admitted, n))
                for n in (
                    "check_pending_current",
                    "check_pending_exact",
                    "check_pending_checkpoint_only",
                    "check_source_leases_current",
                    "check_authority_current",
                )
            )
            retained()
            signal = _NativeScientificRefusal()
            self.signal = signal
            return signal

        def finish():
            nonlocal finished
            binding()
            if type(signal) is not _NativeScientificRefusal:
                raise LauncherError("native-refusal-observation-consumed")
            # Owned namespace/channel/client/Factorylock closes have completed.
            # All prepared/prior IO precedes the last CP/provider observation.
            final_io(retained, authority)
            retained()
            dict(admitted_functions)["check_pending_checkpoint_only"]()
            installation()
            binding()
            source_check()
            dict(admitted_functions)["check_source_leases_current"]()
            final_passive()
            passive()
            authority()
            finished = self.finished = True

        self.qualify, self.finish = qualify, finish
        self.check_retained, self.check_authority_current = retained, authority
        self.check_binding = binding
        methods = tuple(
            (n, getattr(self, n))
            for n in (
                "qualify",
                "finish",
                "check_retained",
                "check_authority_current",
                "check_binding",
            )
        )


class NativeSessionActionCommit:
    """Commit one independently qualified completion to B2 and its checkpoint.

    This writer does not derive permission, next actions or scientific state.
    The constructor-fixed host verifier must bind the complete real action
    closure to these exact proposed bytes, including terminal B3/RETIRE when
    required. Its post-write observation must admit only this exact transition;
    a pending-only worker recheck cannot be reused after clearing the intent.
    Neither the callback nor this transaction is selected through RPC or Env.
    """

    def __init__(
        self,
        *,
        invocation,
        bootstrap,
        pending,
        proposal,
        result_bytes,
        qualify_completion,
        reconciled=None,
    ):
        import threading

        if (
            type(bootstrap) is not NativeLauncherBootstrap
            or invocation.native_bootstrap is not bootstrap
            or invocation.action not in NativeOneOffDocker._WORKER_ACTIONS
            or not callable(qualify_completion)
            or type(result_bytes) is not bytes
        ):
            raise LauncherError("native-action-commit-context")
        self.invocation, self.bootstrap = invocation, bootstrap
        self.action, self.qualifier, self.reconciled = (
            invocation.action,
            qualify_completion,
            reconciled,
        )
        self.codec = bootstrap.apparatus["ex10_session_v2"]
        if (
            type(pending) is not self.codec.OfficialSessionV2
            or type(proposal) is not self.codec.OfficialSessionV2
        ):
            raise LauncherError("native-action-commit-context")
        self.pending, self.proposal, self.result_bytes = (
            pending._bytes,
            proposal._bytes,
            result_bytes,
        )
        self.count, self.tip = pending._expected_count, pending._expected_tip
        self.new_count, self.new_tip = proposal._expected_count, proposal._expected_tip
        self.qualifier_identity = qualify_completion
        self.lock, self.used = threading.Lock(), False
        self._completed_observation = None
        self._validate()

    def _validate(self):
        validate_invocation(self.invocation)
        if (
            self.invocation.action != self.action
            or self.invocation.native_bootstrap is not self.bootstrap
            or self.qualifier is not self.qualifier_identity
        ):
            raise LauncherError("native-action-commit-context")
        pending = self.codec.load_session_v2(
            self.pending, expected_tip=self.tip, expected_count=self.count
        )
        proposal = self.codec.load_session_v2(
            self.proposal, expected_tip=self.new_tip, expected_count=self.new_count
        )
        before, after = pending.to_dict(), proposal.to_dict()
        flights = before["in_flight"]
        if (
            len(flights) != 1
            or flights[0]["action"] != self.action
            or not before["state"]["uncertain"]
            or after["in_flight"] != []
            or after["state"]["uncertain"]
            or proposal.header != pending.header
            or self.new_count != self.count + 1
            or after["history"][:-1] != before["history"]
            or after["history"][-1]["payload"]["event"] != "complete-" + self.action
        ):
            raise LauncherError("native-action-commit-proposal")
        op = self.bootstrap.apparatus["ex10_operational_context"]
        if len(self.result_bytes) > op.wire.MAX_REQUEST_BYTES:
            raise LauncherError("native-action-commit-result")
        try:
            result = _native_parse_public_result(
                self.result_bytes, maximum=op.wire.MAX_REQUEST_BYTES
            )
            if type(result) is not dict:
                raise ValueError()
            problems, phase, _ = action_problems(
                self.action,
                result,
                run_id=after["official_run_id"]
                if self.action == "start-official"
                else before["official_run_id"],
                reconciled=self.reconciled,
            )
            expected_phase = before["state"]["known_phase"] if phase is None else phase
            if (
                problems
                or after["official_run_id"] != result["run_id"]
                or after["state"]["known_phase"] != expected_phase
            ):
                raise ValueError()
            if self.action != "start-official" and (
                before["official_run_id"] != after["official_run_id"]
                or before["preflight_report_sha256"] != after["preflight_report_sha256"]
                or before["operational_envelope_sha256"] != after["operational_envelope_sha256"]
            ):
                raise ValueError()
        except Exception:
            raise LauncherError("native-action-commit-result") from None
        return pending, proposal

    def completed_observation(self):
        """Recheck the exact successful transition; grant no next action."""
        if not self.lock.acquire(blocking=False):
            raise LauncherBusy()
        try:
            if self._completed_observation is None:
                raise LauncherError("native-action-completion-receipt-required")
            return self._completed_observation()
        except LauncherError:
            raise
        except Exception:
            # Underlying codec/provider failures still deny the observation;
            # expose only the existing sanitized launcher error family.
            raise LauncherError("native-action-completion-receipt") from None
        finally:
            self.lock.release()

    def commit(self):
        if not self.lock.acquire(blocking=False):
            raise LauncherBusy()
        try:
            if self.used:
                raise LauncherError("native-action-commit-consumed")
            self.used = True
            pending, proposal = self._validate()
            # Local immutable originals survive every later provider call.
            observed = self.pending, self.proposal, self.result_bytes
            bootstrap, invocation, codec = self.bootstrap, self.invocation, self.codec
            with bootstrap.writer() as writer:
                with _native_session_lock(bootstrap, invocation.session_root) as current:
                    found = bootstrap.load(invocation)
                    if found._bytes != pending._bytes:
                        raise LauncherError("native-action-commit-pending-changed")
                    profile, *_, original = bootstrap.qualify(invocation, session=found)

                    def exact(checkpoint, raw):
                        loaded = bootstrap.load(invocation)
                        if (
                            loaded._bytes != raw
                            or bootstrap.checkpoint() != checkpoint
                            or (self.pending, self.proposal, self.result_bytes) != observed
                        ):
                            raise LauncherError("native-action-commit-binding")
                        self._validate()
                        return loaded

                    def qualify(*, written):
                        try:
                            verdict = self.qualifier(
                                action=self.action,
                                pending_bytes=observed[0],
                                proposal_bytes=observed[1],
                                result_bytes=observed[2],
                                profile=profile,
                                writer=writer,
                                written=written,
                            )
                        except _NativePriorEvidenceViolation as error:
                            if (
                                type(error) is not _NativePriorEvidenceViolation
                                or type(error.code) is not str
                                or error.code not in ("prior-run-changed", "evidence-runs-changed")
                            ):
                                raise LauncherError("native-action-completion-required") from None
                            raise _NativePriorEvidenceViolation(error.code) from None
                        except Exception:
                            raise LauncherError("native-action-completion-required") from None
                        if verdict is not None:
                            raise LauncherError("native-action-completion-required")

                    previous = (pending._expected_tip, pending._expected_count)
                    replacement = (proposal._expected_tip, proposal._expected_count)
                    # A codec-valid state is advisory; the independent host
                    # verifier must approve its exact next actions and proofs.
                    qualify(written=False)
                    exact(previous, pending._bytes)
                    original()
                    current()
                    writer.check()
                    original.check_authority_current()
                    completed = codec.update_official_session_v2(
                        invocation.session_root / SESSION_FILE,
                        pending,
                        proposal,
                        context=bootstrap.context(),
                        profile=profile,
                        caller_serialized=True,
                    )
                    if completed._bytes != observed[1]:
                        raise LauncherError("native-action-commit-write-uncertain")
                    # Preserve written bytes if checkpoint publication or any
                    # later guard fails. No rollback, retry or inferred success.
                    original()
                    current()
                    writer.check()
                    original.check_authority_current()
                    bootstrap.publish(previous, completed)
                    exact(replacement, completed._bytes)
                    qualify(written=True)
                    exact(replacement, completed._bytes)
                    original()
                    current()
                    writer.check()
                    original.check_authority_current()
                original.check_authority_current()
            # Session FD, global unlock and global FD close are actual IO.
            original.check_authority_current()
            # Emit a local receipt only after all actual writer cleanup and
            # original authority fences succeeded. Consumed or coherent B2/CP
            # alone cannot supply this proof. No wire/custody record is added.
            profile_bytes = bootstrap.apparatus[
                "ex10_runtime_profile"
            ].canonical_runtime_profile_bytes(profile)
            action = invocation.action

            def completed_observation():
                if (
                    self.invocation is not invocation
                    or self.bootstrap is not bootstrap
                    or self.action != action
                    or (self.pending, self.proposal, self.result_bytes) != observed
                ):
                    raise LauncherError("native-action-completion-receipt")
                self._validate()
                original()
                current_session = bootstrap.load(invocation)
                if current_session._bytes != observed[1] or bootstrap.checkpoint() != replacement:
                    raise LauncherError("native-action-completion-receipt")
                # The independent checkpoint is a real provider call. Recheck
                # original source/profile/ref leases after it, then reread B2
                # with no external provider and finish with the pure old fence.
                original()
                confirmed = codec.load_official_session(
                    invocation.session_root / SESSION_FILE,
                    context=bootstrap.context(),
                    profile=profile,
                    expected_tip=replacement[0],
                    expected_count=replacement[1],
                )
                if (
                    type(confirmed) is not codec.OfficialSessionV2
                    or confirmed._bytes != observed[1]
                ):
                    raise LauncherError("native-action-completion-receipt")
                original.check_authority_current()
                return (
                    invocation,
                    bootstrap,
                    action,
                    *observed,
                    profile_bytes,
                    replacement,
                )

            self._completed_observation = completed_observation
            return completed
        except LauncherError:
            raise
        except self.bootstrap.apparatus["ex10_authority_sqlite"].AuthorityError as error:
            if error.code == "LOCK_BUSY":
                raise LauncherBusy() from None
            raise LauncherError("native-action-commit-uncertain") from None
        except Exception:
            raise LauncherError("native-action-commit-uncertain") from None
        finally:
            self.lock.release()


@contextlib.contextmanager
def _native_session_lock(bootstrap, root, *, expected_identity=None):
    rp = bootstrap.apparatus["ex10_runtime_profile"]
    fd, components = rp._walk(str(root), "directory")
    try:
        before = os.fstat(fd)
        identity = (before.st_dev, before.st_ino, before.st_uid, before.st_gid, before.st_mode)
        if expected_identity is not None and identity != expected_identity:
            raise LauncherError("native-session-root-changed")
        if stat.S_IMODE(before.st_mode) != 0o700 or (before.st_uid, before.st_gid) != (
            os.getuid(),
            os.getgid(),
        ):
            raise LauncherError("native-session-root-unsafe")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LauncherBusy() from None

        def check():
            check_fd, current = rp._walk(str(root), "directory")
            try:
                info = os.fstat(check_fd)
                observed = (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode)
                if current != components or observed != identity:
                    raise LauncherError("native-session-root-changed")
            finally:
                os.close(check_fd)

        check()
        yield check
        check()
    finally:
        os.close(fd)


def _native_check_recorded_outputs(invocation, session):
    """Verify completed native output inventories and every historical hash.

    Only parents beneath a recorded actions/<canonical UUID> participate in
    inventory checks. Unrecorded pending directories remain untouched; other
    confined codec paths retain their existing hash verification semantics.
    """
    from uuid import UUID

    entries = session.to_dict()["history"]
    inventories = {}
    for entry in entries:
        for relative in entry["payload"]["output_sha256"]:
            parts = Path(relative).parts
            if len(parts) < 3 or parts[0] != "actions":
                continue
            try:
                native = str(UUID(parts[1])) == parts[1]
            except ValueError:
                native = False
            if native:
                for depth in range(2, len(parts)):
                    parent = invocation.session_root.joinpath(*parts[:depth])
                    inventories.setdefault(parent, set()).add(parts[depth])

    rp = invocation.native_bootstrap.apparatus["ex10_runtime_profile"]
    retained = []
    try:
        for parent, names in inventories.items():
            fd, components = rp._walk(str(parent), "directory")
            retained.append((fd, parent, components, names, None))
            info = os.fstat(fd)
            if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (
                os.getuid(),
                os.getgid(),
                0o700,
            ) or set(os.listdir(fd)) != names:
                raise SessionTampered("native-action-output-changed")
            retained[-1] = (
                fd,
                parent,
                components,
                names,
                NativeExclusiveProofPublication._stamp(info),
            )
        for entry in entries:
            for relative, expected in entry["payload"]["output_sha256"].items():
                raw, _ = _capture_native_source(
                    invocation.session_root / relative, error_type=LauncherError
                )
                if hashlib.sha256(raw).hexdigest() != expected:
                    raise SessionTampered("native-action-output-changed")
        for fd, parent, components, names, stamp in retained:
            if (
                NativeExclusiveProofPublication._stamp(os.fstat(fd)) != stamp
                or set(os.listdir(fd)) != names
            ):
                raise SessionTampered("native-action-output-changed")
            check_fd, current = rp._walk(str(parent), "directory")
            try:
                if (
                    current != components
                    or NativeExclusiveProofPublication._stamp(os.fstat(check_fd)) != stamp
                    or NativeExclusiveProofPublication._stamp(os.fstat(fd)) != stamp
                ):
                    raise SessionTampered("native-action-output-changed")
            finally:
                os.close(check_fd)
    except (OSError, rp.RuntimeProfileError):
        raise SessionTampered("native-action-output-changed") from None
    finally:
        for fd, *_ in reversed(retained):
            os.close(fd)


@contextlib.contextmanager
def _native_record_action_output(
    *, bootstrap, invocation, exchange_id, result_bytes, check_current, check_authority_current
):
    """Own a new private action directory and retain the exact output until commit."""
    from uuid import UUID

    if str(UUID(exchange_id)) != exchange_id or type(result_bytes) is not bytes:
        raise LauncherError("native-action-output-invalid")
    rp = bootstrap.apparatus["ex10_runtime_profile"]
    owned = []
    guards = []
    try:
        root_fd, root_lease = rp._walk(str(invocation.session_root), "directory")
        owned.append(root_fd)
        owner = os.geteuid(), os.getegid()

        def private_directory(fd):
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode) or (
                info.st_uid,
                info.st_gid,
                stat.S_IMODE(info.st_mode),
            ) != (*owner, 0o700):
                raise LauncherError("native-action-output-unsafe")
            return info.st_dev, info.st_ino

        root_identity = private_directory(root_fd)

        def directory(parent, name, *, exclusive):
            created = False
            if exclusive:
                os.mkdir(name, mode=0o700, dir_fd=parent)
                created = True
            else:
                try:
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    os.mkdir(name, mode=0o700, dir_fd=parent)
                    created = True
            fd = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent
            )
            owned.append(fd)
            identity = private_directory(fd)
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (named.st_dev, named.st_ino) != identity:
                raise LauncherError("native-action-output-unsafe")
            guards.append((parent, name, fd, identity))
            if created:
                os.fsync(parent)
            return fd

        check_current()
        parent_fd = directory(root_fd, "actions", exclusive=False)
        action_fd = directory(parent_fd, exchange_id, exclusive=True)
        path = invocation.session_root / "actions" / exchange_id / "worker-result.json"
        fd = os.open(
            "worker-result.json",
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=action_fd,
        )
        owned.append(fd)
        born = os.fstat(fd)
        identity = born.st_dev, born.st_ino
        view = memoryview(result_bytes)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise LauncherError("native-action-output-uncertain")
            view = view[count:]
        os.fsync(fd)
        os.fsync(action_fd)
        original, lease = _capture_native_source(path, error_type=LauncherError)
        file_stamp = NativeExclusiveProofPublication._stamp(os.fstat(fd))
        named_stamp = NativeExclusiveProofPublication._stamp(
            os.stat("worker-result.json", dir_fd=action_fd, follow_symlinks=False)
        )
        if (
            file_stamp != named_stamp
            or born.st_nlink != 1
            or (born.st_uid, born.st_gid, stat.S_IMODE(born.st_mode)) != (*owner, 0o600)
            or original != result_bytes
            or (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != identity
        ):
            raise LauncherError("native-action-output-changed")

        action_stamp = NativeExclusiveProofPublication._stamp(os.fstat(action_fd))

        def action_inventory():
            if (
                NativeExclusiveProofPublication._stamp(os.fstat(action_fd)) != action_stamp
                or set(os.listdir(action_fd)) != {"worker-result.json"}
                or NativeExclusiveProofPublication._stamp(os.fstat(action_fd)) != action_stamp
            ):
                raise LauncherError("native-action-output-changed")

        def passive():
            check_fd, components = rp._walk(str(invocation.session_root), "directory")
            try:
                if components != root_lease or private_directory(check_fd) != root_identity:
                    raise LauncherError("native-action-output-changed")
            finally:
                os.close(check_fd)
            for parent, name, child, expected in guards:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if private_directory(child) != expected or (info.st_dev, info.st_ino) != expected:
                    raise LauncherError("native-action-output-changed")
            action_inventory()
            observed, current_lease = _capture_native_source(path, error_type=LauncherError)
            if (
                observed != original
                or current_lease != lease
                or NativeExclusiveProofPublication._stamp(os.fstat(fd)) != file_stamp
                or NativeExclusiveProofPublication._stamp(
                    os.stat("worker-result.json", dir_fd=action_fd, follow_symlinks=False)
                )
                != file_stamp
            ):
                raise LauncherError("native-action-output-changed")
            action_inventory()
            check_authority_current()

        def current():
            check_current()
            passive()

        current()
        yield str(path.relative_to(invocation.session_root)), current
        passive()
    except LauncherError:
        raise
    except Exception:
        raise LauncherError("native-action-output-uncertain") from None
    finally:
        for fd in reversed(owned):
            os.close(fd)
        check_authority_current()


class _NativeDockerResources:
    """Read exact constructor-approved instances through fixed Docker argv."""

    def __init__(
        self,
        *,
        bootstrap,
        profile,
        plans,
        runner_factory,
        check_current,
        timeout,
        before_effect=None,
    ):
        self.profile = profile
        self.plans = base.canonical_bytes(plans)
        self.check_current, self.timeout = check_current, timeout
        self.before_effect = before_effect
        self.runner = runner_factory(
            bootstrap.repo_root,
            {
                "PATH": "/usr/bin:/bin",
                "LC_ALL": "C",
                "HOME": "/nonexistent",
                "DOCKER_HOST": "unix:///var/run/docker.sock",
            },
        )
        fields = {
            "container_id",
            "container_name",
            "image_digest",
            "labels",
            "mounts",
            "network_id",
            "environment",
        }
        if (
            type(plans) is not list
            or not plans
            or any(type(p) is not dict or set(p) != fields for p in plans)
        ):
            raise LauncherError("native-resource-plan-invalid")
        if len({p["container_id"] for p in plans}) != len(plans):
            raise LauncherError("native-resource-plan-invalid")
        for plan in plans:
            if (
                not _SHA256.fullmatch(plan["container_id"])
                or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,62}", plan["container_name"])
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", plan["image_digest"])
                or not _SHA256.fullmatch(plan["network_id"])
                or plan["labels"].get("com.docker.compose.project") != profile.to_dict()["project"]
            ):
                raise LauncherError("native-resource-plan-invalid")

    def command(self, *argv):
        self.check_current()
        effect = argv[0] in {"stop", "rm"} or argv[:2] == ("network", "rm")
        if effect:
            if self.before_effect is None:
                raise LauncherError("native-resource-effect-fence-required")
            self.before_effect(argv)
        result = self.runner.run(["docker", *argv], timeout=self.timeout)
        self.check_current()
        if result.returncode != 0:
            raise LauncherError("native-resource-command-failed")
        return result.stdout

    def inspect(self, plan, *, states):
        raw = self.command("inspect", plan["container_id"])
        self.check_current()
        value = self.validate_inspection(plan, raw, states=states)
        self.check_current()
        return value

    @staticmethod
    def validate_inspection(plan, raw, *, states):
        value = json.loads(raw, parse_constant=base._reject_constant)
        if type(value) is not list or len(value) != 1:
            raise LauncherError("native-resource-instance-changed")
        v = value[0]
        environment = {}
        for item in v["Config"]["Env"]:
            key, text = item.split("=", 1)
            if key in environment:
                raise LauncherError("native-resource-instance-changed")
            environment[key] = text
        mounts = sorted((m["Type"], m["Source"], m["Destination"], m["RW"]) for m in v["Mounts"])
        if (
            v["Id"] != plan["container_id"]
            or v["Name"] != "/" + plan["container_name"]
            or v["Image"] != plan["image_digest"]
            or v["Config"]["Labels"] != plan["labels"]
            or environment != plan["environment"]
            or mounts != sorted(tuple(m) for m in plan["mounts"])
            or any(m[0] != "bind" or type(m[3]) is not bool for m in mounts)
            or len(v["NetworkSettings"]["Networks"]) != 1
            or next(iter(v["NetworkSettings"]["Networks"].values()))["NetworkID"]
            != plan["network_id"]
            or v["State"]["Status"] not in states
        ):
            raise LauncherError("native-resource-instance-changed")
        return v

    def project_ids(self):
        raw = self.command(
            "ps",
            "-a",
            "--no-trunc",
            "--filter",
            "label=com.docker.compose.project=" + self.profile.to_dict()["project"],
            "--format",
            "{{.ID}}",
        )
        ids = raw.decode("ascii").splitlines()
        if len(ids) != len(set(ids)) or any(not _SHA256.fullmatch(i) for i in ids):
            raise LauncherError("native-resource-inventory-invalid")
        return frozenset(ids)


class _NativeStatusEvidenceViolation(LauncherError):
    """A physical change observed during this one bound-run STATUS read."""


def _native_status_evidence_guard(*, bootstrap, profile, run, installation, original, deadline):
    """Retain one run's physical inventory; never create, repair or scan history."""
    import time

    rp = bootstrap.apparatus["ex10_runtime_profile"]
    root = profile.to_dict()["host_roots"]["evidence"]
    owner, group = root["owner_uid"], root["owner_gid"]
    if (
        type(run) is not str
        or _RUN_ID.fullmatch(run) is None
        or (owner, group) != (os.geteuid(), os.getegid())
    ):
        raise LauncherError("native-status-evidence-invalid")

    def passive():
        installation()
        original.check_source_leases_current()
        original.check_authority_current()
        if time.monotonic() >= deadline:
            raise Aborted("timeout")

    def identity(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)

    def qualified_directory(fd):
        info = os.fstat(fd)
        if (
            not stat.S_ISDIR(info.st_mode)
            or (info.st_uid, info.st_gid) != (owner, group)
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ValueError("status-directory-invalid")
        return identity(info)

    def observe():
        passive()
        with contextlib.ExitStack() as scope:
            root_fd, components = rp._walk(root["path"], "directory")
            scope.callback(os.close, root_fd)
            root_identity = qualified_directory(root_fd)
            with _native_prior_custody_read(
                root_fd, owner=owner, group=group, check_current=passive
            ) as lock_identity:
                if lock_identity is None:
                    raise ValueError("status-custody-required")
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
                runs_fd = os.open("runs", flags, dir_fd=root_fd)
                scope.callback(os.close, runs_fd)
                runs_identity = qualified_directory(runs_fd)
                run_fd = os.open(run, flags, dir_fd=runs_fd)
                scope.callback(os.close, run_fd)
                run_identity = qualified_directory(run_fd)
                digest = _native_prior_tree_digest(run_fd, check_current=passive)
                if (
                    identity(os.fstat(run_fd)) != run_identity
                    or identity(os.stat(run, dir_fd=runs_fd, follow_symlinks=False)) != run_identity
                    or identity(os.fstat(runs_fd)) != runs_identity
                    or identity(os.stat("runs", dir_fd=root_fd, follow_symlinks=False))
                    != runs_identity
                    or identity(os.fstat(root_fd)) != root_identity
                ):
                    raise ValueError("status-directory-changed")
                check_fd, current_components = rp._walk(root["path"], "directory")
                try:
                    if (
                        current_components != components
                        or identity(os.fstat(check_fd)) != root_identity
                    ):
                        raise ValueError("status-root-changed")
                finally:
                    os.close(check_fd)
        passive()
        return components, root_identity, runs_identity, run_identity, lock_identity, digest

    try:
        baseline = observe()
    except (OSError, ValueError, rp.RuntimeProfileError) as error:
        if not (isinstance(error, OSError | rp.RuntimeProfileError) or type(error) is ValueError):
            raise
        raise LauncherError("native-status-evidence-invalid") from None

    def current():
        # Read/context failures do not prove a physical modification. Keep them
        # as observation uncertainty; only a completed comparison gets this cause.
        observed = observe()
        if observed != baseline:
            raise _NativeStatusEvidenceViolation("evidence-changed-by-read")

    return current


def _native_domain_status(
    *,
    invocation,
    bootstrap,
    session,
    profile,
    call,
    runner_factory,
    installation,
    host_deadline,
    custody_hmac_key,
):
    """A bounded existing-run read; no history, output or lifecycle writes."""
    run = session.to_dict()["official_run_id"]
    if run is None or "status" not in session.to_dict()["state"]["next_allowed_actions"]:
        raise LauncherError("native-status-run-required")
    _, *_, original = bootstrap.qualify(invocation, session=session)
    evidence_current = _native_status_evidence_guard(
        bootstrap=bootstrap,
        profile=profile,
        run=run,
        installation=installation,
        original=original,
        deadline=host_deadline,
    )
    raw = call("prepare_domain", invocation=invocation, profile=profile, session=session)
    if type(raw) is not dict or set(raw) != {"container"}:
        raise LauncherError("native-status-plan-invalid")
    plans = base.canonical_bytes([raw["container"]])
    _, session_lease = _capture_native_source(
        invocation.session_root / SESSION_FILE, error_type=LauncherError
    )
    checkpoint = session._expected_tip, session._expected_count

    def current():
        installation()
        original()
        if (
            bootstrap.load(invocation)._bytes != session._bytes
            or bootstrap.checkpoint() != checkpoint
        ):
            raise LauncherError("native-status-session-changed")
        passive()

    def passive():
        installation()
        original()
        observed, lease = _capture_native_source(
            invocation.session_root / SESSION_FILE, error_type=LauncherError
        )
        if (
            observed != session._bytes
            or lease != session_lease
            or base.canonical_bytes([raw["container"]]) != plans
        ):
            raise LauncherError("native-status-session-changed")
        _native_check_recorded_outputs(invocation, session)
        original.check_authority_current()

    resources = _NativeDockerResources(
        bootstrap=bootstrap,
        profile=profile,
        plans=json.loads(plans),
        runner_factory=runner_factory,
        check_current=current,
        timeout=ACTIONS["status"].timeout,
    )
    plan = json.loads(plans)[0]
    before = resources.inspect(plan, states={"running"})
    if (
        call(
            "approve_domain",
            invocation=invocation,
            session=session,
            profile=profile,
            container=before,
            run_id=run,
            written=False,
        )
        is not None
    ):
        raise LauncherError("native-domain-approval-required")
    current()
    resources.inspect(plan, states={"running"})
    raw_result = resources.command(
        "exec",
        plan["container_id"],
        "/usr/local/bin/python",
        "-B",
        "/app/manage.py",
        "s11_ex10",
        "status",
        "--run-id",
        run,
    )
    if type(raw_result) is not bytes or len(raw_result) > MAX_JSON_BYTES:
        raise LauncherError("native-domain-output-invalid")
    # STATUS never persists its payload. A recognized or installed custody
    # secret still invalidates the observation; retain only a value-free code.
    # The key is the original already-installed Factory key, never an env read.
    import base64

    known_secrets = [custody_hmac_key.hex(), base64.b64encode(custody_hmac_key).decode("ascii")]
    try:
        known_secrets.append(custody_hmac_key.decode("utf-8"))
    except UnicodeDecodeError:
        pass
    if base.secret_hits(raw_result.decode("utf-8", "replace"), known_secrets):
        raise NativeActionFailure("native-domain-output-secret")

    def unique_object(pairs):
        value = {}
        for name, item in pairs:
            if name in value:
                raise ValueError("duplicate-key")
            value[name] = item
        return value

    try:
        result = json.loads(
            raw_result, parse_constant=base._reject_constant, object_pairs_hook=unique_object
        )
        problems, observed_phase, _ = action_problems("status", result, run_id=run, reconciled=None)
    except Exception:
        raise LauncherError("native-domain-output-invalid") from None
    # JSON escapes can hide a sensitive value from a raw-byte scan. Inspect
    # the decoded strings and keys too, without persisting or repairing them.
    pending_values = [result]
    while pending_values:
        value = pending_values.pop()
        if type(value) is str:
            if base.secret_hits(value, known_secrets):
                raise NativeActionFailure("native-domain-output-secret")
        elif type(value) is dict:
            pending_values.extend(value)
            pending_values.extend(value.values())
        elif type(value) is list:
            pending_values.extend(value)
    if problems:
        raise NativeActionFailure("native-domain-output-invalid")
    state = session.to_dict()["state"]
    if (
        not state["uncertain"]
        and state["known_phase"] is not None
        and observed_phase != state["known_phase"]
    ):
        raise NativeActionFailure("session-run-mismatch")
    after = resources.inspect(plan, states={"running"})
    if (
        call(
            "approve_domain",
            invocation=invocation,
            session=session,
            profile=profile,
            container=after,
            run_id=run,
            written=False,
            result=result,
        )
        is not None
    ):
        raise LauncherError("native-domain-approval-required")
    current()
    # All host approval/checkpoint callbacks precede the last actual metadata
    # observation. The remaining original file/source/B3 fences are passive;
    # none can invoke a new host provider after this exact instance was read.
    final = resources.runner.run(
        ["docker", "inspect", plan["container_id"]], timeout=resources.timeout
    )
    if final.returncode != 0:
        raise LauncherError("native-resource-command-failed")
    resources.validate_inspection(plan, final.stdout, states={"running"})
    passive()
    result = _summary(session.to_dict(), "status", "COHERENT", EXIT_OK, output=None)

    def final_fence():
        original.check_authority_current()
        evidence_current()

    return result, final_fence


def _native_cleanup_resources(
    *, invocation, bootstrap, session, profile, call, runner_factory, installation
):
    """Fence exact approved IDs; retain intent on loss, never prune evidence."""
    import time
    from uuid import uuid4

    op = bootstrap.apparatus["ex10_operational_context"]
    codec, history = bootstrap.apparatus["ex10_session_v2"], op.history
    raw = call("prepare_cleanup", invocation=invocation, session=session, profile=profile)
    if type(raw) is not dict or set(raw) != {"containers", "networks", "resource_configuration"}:
        raise LauncherError("native-cleanup-plan-invalid")
    plans = base.canonical_bytes({k: raw[k] for k in ("containers", "networks")})
    resource = op.NativePolicyResourcePlan(**raw["resource_configuration"])
    if (
        resource.lock_store is not bootstrap.global_store
        or resource.lock_name != bootstrap.global_name
        or resource.lock_identity != bootstrap.global_identity
    ):
        raise LauncherError("native-cleanup-global-fence-required")

    def approve(**facts):
        installation()
        if "profile" in facts:
            supplied_profile = facts.pop("profile")
            if (
                type(supplied_profile) is not type(profile)
                or supplied_profile._bytes != profile._bytes
            ):
                raise LauncherError("native-cleanup-profile-changed")
        resource.check()
        if base.canonical_bytes({k: raw[k] for k in ("containers", "networks")}) != plans:
            raise LauncherError("native-cleanup-plan-changed")
        if (
            call(
                "approve_cleanup",
                invocation=invocation,
                original_session=session,
                profile=profile,
                resource_plan=resource,
                **facts,
            )
            is not None
        ):
            raise LauncherError("native-cleanup-approval-required")
        resource.check()
        installation()

    class ReadOnlyRegistry:
        def verify_registry(self, *, canonical_binding, journal):
            if canonical_binding != resource.registry_binding:
                raise LauncherError("native-cleanup-registry-changed")
            approve(stage="coordination-registry", journal=journal)

    journal = op.coordination.CoordinationJournal(
        store=resource.sidecar_store,
        name=resource.sidecar_name,
        identity=resource.sidecar_identity,
        registry_binding=resource.registry_binding,
        authority_id=resource.authority_id,
        authority_epoch=resource.authority_epoch,
        receipt_store=resource.receipt_store,
        floor=resource.floor,
        transport=resource.transport,
        bridge_pin=resource.bridge_pin,
        host_sources=ReadOnlyRegistry(),
        clock=resource.clock,
        clock_health=resource.clock_health,
        lock_store=resource.lock_store,
        lock_name=resource.lock_name,
        lock_identity=resource.lock_identity,
    )

    def no_active():
        resource.check()
        snapshot = bootstrap.read_authority(
            profile.to_dict()["historical_lifecycle_authority_reference"]
        )
        revision = history.current_revision(snapshot)
        if (revision["authority_id"], revision["epoch"]) != (
            resource.authority_id,
            resource.authority_epoch,
        ):
            raise LauncherError("native-cleanup-authority-changed")
        state = history._verified(snapshot)["state"]
        latest = {}
        for row in state["official_runs"]:
            if (
                row["run_id"] not in latest
                or row["run_version"] > latest[row["run_id"]]["run_version"]
            ):
                latest[row["run_id"]] = row
        resolved = {
            r["operation_id"]
            for r in state["publication_resolutions"]
            if r["disposition"] == "COMMITTED"
        }
        blockers = history.active_or_uncertain_runs(snapshot)
        if (
            blockers["runs"]
            or blockers["authority_uncertainty"]
            or blockers["write_closed"]
            or any(i["operation_id"] not in resolved for i in state["publication_intents"])
        ):
            raise LauncherError("native-cleanup-active-or-uncertain")
        local = journal.read()
        head = journal.fresh_head(local)
        if (
            local["active"] is not None
            or head["active_reservation_id"] is not None
            or any(r["state"] in {"PENDING", "UNCERTAIN"} for r in local["latest"].values())
        ):
            raise LauncherError("native-cleanup-active-or-uncertain")
        clock_started = time.monotonic()
        current_time = resource.clock()
        if (
            type(current_time) is not datetime
            or current_time.tzinfo != UTC
            or resource.clock_health() is not True
        ):
            raise LauncherError("native-cleanup-clock-unavailable")
        qualified = resource.floor.verify_head(
            head,
            challenge=head["challenge"],
            now=current_time,
            minimum_sequence=local["sequence"],
            minimum_token=local["token"],
        )
        if qualified != head:
            raise LauncherError("native-cleanup-coordination-changed")
        head_deadline = (
            clock_started
            + (
                op.sql.utc_time(head["expires_at"]).replace(tzinfo=UTC) - current_time
            ).total_seconds()
        )
        history.current_revision(snapshot)
        if time.monotonic() >= head_deadline:
            raise LauncherError("native-cleanup-coordination-expired")
        return snapshot, head, head_deadline

    no_active()
    exchange = str(uuid4())
    intent = NativeActionIntent(
        invocation=invocation,
        bootstrap=bootstrap,
        container_name="cleanup-" + exchange,
        output_dir="actions/" + exchange,
        approve_action=lambda **facts: (no_active(), approve(stage="intent", **facts))[-1],
    )
    pending = intent.begin()
    approved = json.loads(plans)
    remaining = {p["container_id"] for p in approved["containers"]}
    networks = approved["networks"]
    if (
        type(networks) is not list
        or not networks
        or len({n["id"] for n in networks}) != len(networks)
    ):
        raise LauncherError("native-cleanup-network-plan-invalid")
    removed_networks = set()
    with bootstrap.writer() as writer:
        with _native_session_lock(bootstrap, invocation.session_root) as session_current:
            _, *_, original = bootstrap.qualify(invocation, session=pending)

            def current():
                installation()
                resource.check()
                writer.check()
                session_current()
                if bootstrap.load(invocation)._bytes != pending._bytes:
                    raise LauncherError("native-cleanup-pending-changed")
                original()
                resource.check()
                writer.check()
                session_current()
                original.check_authority_current()

            retained_effect = None
            resources = _NativeDockerResources(
                bootstrap=bootstrap,
                profile=profile,
                plans=approved["containers"],
                runner_factory=runner_factory,
                check_current=current,
                timeout=ACTIONS["cleanup-stack"].timeout,
            )

            def inventory():
                if resources.project_ids() != frozenset(remaining):
                    raise LauncherError("native-cleanup-foreign-container")
                raw_networks = resources.command(
                    "network",
                    "ls",
                    "--no-trunc",
                    "--filter",
                    "label=com.docker.compose.project=" + profile.to_dict()["project"],
                    "--format",
                    "{{.ID}}",
                )
                observed_network_ids = raw_networks.decode("ascii").splitlines()
                expected_network_ids = {n["id"] for n in networks} - removed_networks
                if (
                    len(observed_network_ids) != len(set(observed_network_ids))
                    or set(observed_network_ids) != expected_network_ids
                ):
                    raise LauncherError("native-cleanup-foreign-network")
                for network in networks:
                    if set(network) != {"id", "name", "labels"} or not _SHA256.fullmatch(
                        network["id"]
                    ):
                        raise LauncherError("native-cleanup-network-plan-invalid")
                    if network["id"] in removed_networks:
                        continue
                    values = json.loads(resources.command("network", "inspect", network["id"]))
                    if type(values) is not list or len(values) != 1:
                        raise LauncherError("native-cleanup-network-changed")
                    n = values[0]
                    if (
                        n["Id"] != network["id"]
                        or n["Name"] != network["name"]
                        or n["Labels"] != network["labels"]
                        or n["Labels"].get("com.docker.compose.project")
                        != profile.to_dict()["project"]
                        or not set(n["Containers"]) <= remaining
                    ):
                        raise LauncherError("native-cleanup-foreign-network")
                current()

            def fence(stage, target):
                nonlocal retained_effect
                current()
                inventory()
                snapshot, head, head_deadline = no_active()
                approve(
                    stage=stage,
                    target=target,
                    pending=pending,
                    writer=writer,
                    authority_snapshot=snapshot,
                    coordination_head=head,
                )
                inventory()
                history.current_revision(snapshot)
                current()
                history.current_revision(snapshot)
                if time.monotonic() >= head_deadline:
                    raise LauncherError("native-cleanup-coordination-expired")
                retained_effect = stage, target, snapshot, head, head_deadline

            def before_effect(argv):
                if retained_effect is None:
                    raise LauncherError("native-cleanup-effect-required")
                stage, target, snapshot, _, head_deadline = retained_effect
                expected = (
                    ("stop", "--time", "10", target)
                    if stage == "stop"
                    else (
                        ("rm", target) if stage == "remove-container" else ("network", "rm", target)
                    )
                )
                if stage not in {"stop", "remove-container", "remove-network"} or argv != expected:
                    raise LauncherError("native-cleanup-effect-changed")
                # No authority/checkpoint/approval callback follows these actual
                # last metadata observations. Only original OS/pure fences do.
                ids = resources.runner.run(
                    [
                        "docker",
                        "ps",
                        "-a",
                        "--no-trunc",
                        "--filter",
                        "label=com.docker.compose.project=" + profile.to_dict()["project"],
                        "--format",
                        "{{.ID}}",
                    ],
                    timeout=resources.timeout,
                )
                if ids.returncode != 0 or set(ids.stdout.decode("ascii").splitlines()) != remaining:
                    raise LauncherError("native-cleanup-foreign-container")
                network_ids = resources.runner.run(
                    [
                        "docker",
                        "network",
                        "ls",
                        "--no-trunc",
                        "--filter",
                        "label=com.docker.compose.project=" + profile.to_dict()["project"],
                        "--format",
                        "{{.ID}}",
                    ],
                    timeout=resources.timeout,
                )
                observed_ids = network_ids.stdout.decode("ascii").splitlines()
                if (
                    network_ids.returncode != 0
                    or len(observed_ids) != len(set(observed_ids))
                    or set(observed_ids) != {n["id"] for n in networks} - removed_networks
                ):
                    raise LauncherError("native-cleanup-foreign-network")
                for network in networks:
                    if network["id"] in removed_networks:
                        continue
                    result = resources.runner.run(
                        ["docker", "network", "inspect", network["id"]], timeout=resources.timeout
                    )
                    values = json.loads(result.stdout) if result.returncode == 0 else None
                    if type(values) is not list or len(values) != 1:
                        raise LauncherError("native-cleanup-network-changed")
                    observed = values[0]
                    if (
                        observed["Id"] != network["id"]
                        or observed["Name"] != network["name"]
                        or observed["Labels"] != network["labels"]
                        or not set(observed["Containers"]) <= remaining
                        or (
                            stage == "remove-network"
                            and network["id"] == target
                            and observed["Containers"]
                        )
                    ):
                        raise LauncherError("native-cleanup-foreign-network")
                if stage in {"stop", "remove-container"}:
                    plan = next(p for p in approved["containers"] if p["container_id"] == target)
                    result = resources.runner.run(
                        ["docker", "inspect", target], timeout=resources.timeout
                    )
                    if result.returncode != 0:
                        raise LauncherError("native-cleanup-instance-changed")
                    resources.validate_inspection(
                        plan,
                        result.stdout,
                        states={"running"} if stage == "stop" else {"exited", "created"},
                    )
                installation()
                resource.check()
                writer.check()
                session_current()
                original.check_authority_current()
                history.current_revision(snapshot)
                if time.monotonic() >= head_deadline:
                    raise LauncherError("native-cleanup-coordination-expired")

            resources.before_effect = before_effect
            for plan in approved["containers"]:
                observed = resources.inspect(plan, states={"running", "exited", "created"})
                if observed["State"]["Status"] == "running":
                    fence("stop", plan["container_id"])
                    resources.inspect(plan, states={"running"})
                    resources.command("stop", "--time", "10", plan["container_id"])
                resources.inspect(plan, states={"exited", "created"})
                fence("remove-container", plan["container_id"])
                resources.inspect(plan, states={"exited", "created"})
                resources.command("rm", plan["container_id"])
                remaining.remove(plan["container_id"])
                inventory()
            for network in networks:
                fence("remove-network", network["id"])
                resources.command("network", "rm", network["id"])
                removed_networks.add(network["id"])
                inventory()
            payload = dict(pending.to_dict()["history"][-1]["payload"])
            payload.update(
                event="complete-cleanup-stack",
                in_flight=[],
                output_sha256={},
                state={
                    "known_phase": session.to_dict()["state"]["known_phase"],
                    "uncertain": False,
                    "blocked": False,
                    "next_allowed_actions": ["inspect"],
                },
            )
            proposal = codec.append_history_entry_v2(pending, payload)
            fence("complete", None)
            approve(
                stage="completion", pending=pending, proposal=proposal, writer=writer, written=False
            )
            current()
            completed = codec.update_official_session_v2(
                invocation.session_root / SESSION_FILE,
                pending,
                proposal,
                context=bootstrap.context(),
                profile=profile,
                caller_serialized=True,
            )
            if completed._bytes != proposal._bytes:
                raise LauncherError("native-cleanup-completion-uncertain")
            writer.check()
            session_current()
            original()
            original.check_authority_current()
            bootstrap.publish((pending._expected_tip, pending._expected_count), completed)
            approve(
                stage="completion", pending=pending, proposal=proposal, writer=writer, written=True
            )
            if bootstrap.load(invocation)._bytes != proposal._bytes:
                raise LauncherError("native-cleanup-completion-uncertain")
            writer.check()
            session_current()
            original()
            original.check_authority_current()
        original.check_authority_current()
    original.check_authority_current()
    result = _summary(completed.to_dict(), "cleanup-stack", "COHERENT", EXIT_OK, output=None)
    return result, original.check_authority_current


def _native_completed_state(session, action, phase):
    """Preserve the existing action/phase rules for the v2 chained snapshots."""
    value = session.to_dict()
    completed = {
        e["payload"]["event"].removeprefix("complete-")
        for e in value["history"]
        if e["payload"]["event"].startswith("complete-")
    } | {action}
    allowed = ["inspect", "status"]
    if phase == "PREPARED":
        if "execute-official" not in completed:
            allowed.append("execute-official")
        allowed.append("abandon-official-pre-t0")
    elif phase == "STOPPED_PRE_T0":
        allowed.append("abandon-official-pre-t0")
    elif phase == "DRAINING" and "execute-official" in completed and "drain" not in completed:
        allowed.append("drain")
    elif phase == "RECONCILING" and "reconcile" not in completed:
        allowed.append("reconcile")
    elif phase in {"RECONCILING", "DECIDED"} and "reconcile" in completed:
        allowed.append("finalize")
    elif phase in TERMINAL_PHASES and "cleanup-stack" not in completed:
        allowed.append("cleanup-stack")
    if (
        phase in EXECUTOR_GONE_PHASES
        and "execute-official" in completed
        and "declare-executor-gone" not in completed
    ):
        allowed.append("declare-executor-gone")
    if value["official_run_id"] is None and action == "cleanup-stack":
        allowed = ["inspect"]
    return {
        "known_phase": phase,
        "uncertain": False,
        "blocked": False,
        "next_allowed_actions": sorted(set(allowed)),
    }


def _native_result_proposal(
    *, bootstrap, pending, action, result, result_bytes, prepared, output_path, reconciled
):
    codec = bootstrap.apparatus["ex10_session_v2"]
    value = pending.to_dict()
    problems, phase, _ = action_problems(
        action, result, run_id=value["official_run_id"], reconciled=reconciled
    )
    if problems:
        raise LauncherError("native-action-result-incoherent")
    phase = value["state"]["known_phase"] if phase is None else phase
    payload = dict(value["history"][-1]["payload"])
    payload.update(
        event="complete-" + action,
        official_run_id=result["run_id"],
        in_flight=[],
        state=_native_completed_state(pending, action, phase),
        output_sha256={output_path: hashlib.sha256(result_bytes).hexdigest()},
    )
    if action == "start-official":
        envelope = (
            bootstrap.apparatus["ex10_deployment_envelope"]
            .parse_envelope_bytes(prepared.envelope_bytes)
            .to_dict()
        )
        if result["preflight_sha256"] != envelope["application_preflight_sha256"]:
            raise LauncherError("native-action-preflight-incoherent")
        payload["preflight_report_sha256"] = result["preflight_sha256"]
        payload["operational_envelope_sha256"] = hashlib.sha256(prepared.envelope_bytes).hexdigest()
    return codec.append_history_entry_v2(pending, payload)


def _native_prior_tree_digest(root_fd, *, check_current, metadata_lease=None):
    """Read-only inventory; caller owns the qualified root and custody lock.

    This computes an internal inventory, never an authority or public result.
    No file content is returned, no evidence symlink is followed, no FD is lent
    to the worker, and all owned descriptors close before the final callback.
    """

    def stamp(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_uid,
            info.st_gid,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_rdev,
        )

    def current_directory(fd):
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("prior-directory-invalid")
        return stamp(info)

    def regular_digest(parent_fd, name, expected):
        # O_PATH binds metadata without invoking a replaced device's open.
        with contextlib.ExitStack() as scope:
            metadata = os.open(
                name,
                os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
            scope.callback(os.close, metadata)
            pinned = os.fstat(metadata)
            if not stat.S_ISREG(pinned.st_mode) or stamp(pinned) != expected:
                raise ValueError("prior-entry-changed")
            # This branch requires the already qualified Linux genuine procfs
            # used by the native backend. Owner checks alone do not attest FS type.
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            proc = os.open("/proc", flags)
            scope.callback(os.close, proc)
            info = os.fstat(proc)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError("prior-proc-context")
            process = os.open(str(os.getpid()), flags, dir_fd=proc)
            scope.callback(os.close, process)
            descriptors = os.open("fd", flags, dir_fd=process)
            scope.callback(os.close, descriptors)
            for directory in (process, descriptors):
                info = os.fstat(directory)
                if (info.st_uid, info.st_gid) != (os.geteuid(), os.getegid()):
                    raise ValueError("prior-proc-context")
                if info.st_mode & 0o022:
                    raise ValueError("prior-proc-context")
            # Follow only our live metadata FD's kernel link, never the mutable
            # evidence name. Inode type is immutable while that FD remains open.
            child = os.open(
                str(metadata),
                os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=descriptors,
            )
            scope.callback(os.close, child)
            if stamp(os.fstat(child)) != expected:
                raise ValueError("prior-entry-changed")
            checksum = hashlib.sha256()
            remaining = pinned.st_size
            while remaining:
                check_current()
                chunk = os.read(child, min(65536, remaining))
                if not chunk:
                    raise ValueError("prior-file-truncated")
                checksum.update(chunk)
                remaining -= len(chunk)
            if (
                os.read(child, 1)
                or stamp(os.fstat(child)) != expected
                or stamp(os.fstat(metadata)) != expected
            ):
                raise ValueError("prior-entry-changed")
            return checksum.hexdigest()

    def scan(fd, prefix, facts, *, contents):
        check_current()
        before = current_directory(fd)
        names = sorted(os.listdir(fd))
        facts[prefix] = (before, None)
        for name in names:
            check_current()
            named = os.stat(name, dir_fd=fd, follow_symlinks=False)
            identity = stamp(named)
            relative = name if prefix == "." else prefix + "/" + name
            digest = None
            if stat.S_ISDIR(named.st_mode):
                child = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                    dir_fd=fd,
                )
                try:
                    if stamp(os.fstat(child)) != identity:
                        raise ValueError("prior-entry-changed")
                    scan(child, relative, facts, contents=contents)
                    if stamp(os.fstat(child)) != identity:
                        raise ValueError("prior-entry-changed")
                finally:
                    os.close(child)
            elif stat.S_ISREG(named.st_mode) and contents:
                digest = regular_digest(fd, name, identity)
                facts[relative] = (identity, digest)
            elif stat.S_ISLNK(named.st_mode):
                digest = os.readlink(name, dir_fd=fd)
                facts[relative] = (identity, digest)
            else:
                # Metadata only for nonregular nodes: never open FIFO/dev/socket.
                facts[relative] = (identity, None)
            if stamp(os.stat(name, dir_fd=fd, follow_symlinks=False)) != identity:
                raise ValueError("prior-entry-changed")
        if sorted(os.listdir(fd)) != names or current_directory(fd) != before:
            raise ValueError("prior-directory-changed")
        check_current()

    if type(root_fd) is not int or root_fd < 0 or not callable(check_current):
        raise ValueError("prior-reader-context")
    check_current()
    facts = {}
    scan(root_fd, ".", facts, contents=True)
    observed = {}
    scan(root_fd, ".", observed, contents=False)
    if {name: value[0] for name, value in observed.items()} != {
        name: value[0] for name, value in facts.items()
    }:
        raise ValueError("prior-inventory-changed")
    # ASCII escaping handles arbitrary OS names without leaking actual bytes.
    raw = json.dumps(facts, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    result = hashlib.sha256(raw.encode("ascii")).hexdigest()
    if metadata_lease is not None:
        if type(metadata_lease) is not list or metadata_lease:
            raise ValueError("prior-reader-context")
        # Private immutable originals from the admitted read. No new baseline
        # is collected by the later metadata-only closure.
        metadata_lease.extend((name, value[0]) for name, value in sorted(facts.items()))
    check_current()
    return result


class _NativePriorEvidenceViolation(LauncherError):
    """Private, fixed existing evidence invariant causes; no provider error text."""

    def __init__(self, code):
        if type(code) is not str or code not in ("prior-run-changed", "evidence-runs-changed"):
            raise ValueError("native-prior-violation-context")
        super().__init__(code)


@contextlib.contextmanager
def _native_prior_custody_read(root_fd, *, owner, group, check_current):
    """Hold only the existing SDK lock, without creating or normalizing evidence."""

    def identity(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink)

    check_current()
    with contextlib.ExitStack() as scope:
        try:
            metadata = os.open(
                ".s11-custody.lock",
                os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=root_fd,
            )
        except FileNotFoundError:
            yield None
            try:
                os.stat(".s11-custody.lock", dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise LauncherError("native-prior-custody-changed")
        else:
            scope.callback(os.close, metadata)
            pinned = os.fstat(metadata)
            if (
                not stat.S_ISREG(pinned.st_mode)
                or pinned.st_nlink != 1
                or stat.S_IMODE(pinned.st_mode) != 0o600
                or (pinned.st_uid, pinned.st_gid) != (owner, group)
            ):
                raise LauncherError("native-prior-custody-invalid")
            # Follow only the live, type-qualified metadata FD's kernel link.
            # Genuine Linux procfs is the native OS precondition, not inferred
            # merely from these directory owner and mode checks.
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            proc = os.open("/proc", flags)
            scope.callback(os.close, proc)
            info = os.fstat(proc)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise LauncherError("native-prior-proc-context")
            process = os.open(str(os.getpid()), flags, dir_fd=proc)
            scope.callback(os.close, process)
            descriptors = os.open("fd", flags, dir_fd=process)
            scope.callback(os.close, descriptors)
            for fd in (process, descriptors):
                info = os.fstat(fd)
                if (info.st_uid, info.st_gid) != (
                    os.geteuid(),
                    os.getegid(),
                ) or info.st_mode & 0o022:
                    raise LauncherError("native-prior-proc-context")
            lock_fd = os.open(
                str(metadata),
                os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=descriptors,
            )
            scope.callback(os.close, lock_fd)
            expected = identity(pinned)
            if identity(os.fstat(lock_fd)) != expected:
                raise LauncherError("native-prior-custody-changed")
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise LauncherError("native-prior-custody-busy") from None
            check_current()
            yield expected
            if (
                identity(os.fstat(lock_fd)) != expected
                or identity(os.fstat(metadata)) != expected
                or identity(os.stat(".s11-custody.lock", dir_fd=root_fd, follow_symlinks=False))
                != expected
            ):
                raise LauncherError("native-prior-custody-changed")
    # Closing all owned FDs releases the shared lock; no writer survives this scope.
    check_current()


def _native_prior_start_inventory(
    *, runtime_profile, profile, check_current, new_run=None, prior_names=None, passive_lease=None
):
    """Observe the approved root and its entire prior namespace under custody."""

    def identity(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)

    if type(passive_lease) is not list or passive_lease:
        raise LauncherError("native-prior-evidence-context")

    def stamp(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_uid,
            info.st_gid,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_rdev,
        )

    value = profile.to_dict()
    root = value["host_roots"]["evidence"]
    owner, group = root["owner_uid"], root["owner_gid"]
    if (owner, group) != (os.geteuid(), os.getegid()):
        raise LauncherError("native-prior-evidence-invalid")
    check_current()
    with contextlib.ExitStack() as scope:
        root_fd, components = runtime_profile._walk(root["path"], "directory")
        scope.callback(os.close, root_fd)
        root_identity = identity(os.fstat(root_fd))
        root_stamp = stamp(os.fstat(root_fd))
        tree_leases = []
        bound_identity = None
        if root_identity[3:] != (owner, group) or stat.S_IMODE(root_identity[2]) != 0o700:
            raise ValueError("prior-root-invalid")
        with _native_prior_custody_read(
            root_fd,
            owner=owner,
            group=group,
            check_current=check_current,
        ) as lock_identity:
            try:
                runs_fd = os.open(
                    "runs",
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                    dir_fd=root_fd,
                )
            except FileNotFoundError:
                runs_identity, entries = None, ()
                runs_stamp = None
                if prior_names is not None:
                    raise _NativePriorEvidenceViolation(
                        "prior-run-changed" if prior_names else "evidence-runs-changed"
                    ) from None
                try:
                    os.stat("runs", dir_fd=root_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise ValueError("prior-runs-changed")
            else:
                scope.callback(os.close, runs_fd)
                runs_identity = identity(os.fstat(runs_fd))
                runs_stamp = stamp(os.fstat(runs_fd))
                if runs_identity[3:] != (owner, group) or stat.S_IMODE(runs_identity[2]) != 0o700:
                    raise ValueError("prior-runs-invalid")
                names = sorted(os.listdir(runs_fd))
                if prior_names is not None:
                    if not set(prior_names).issubset(names):
                        raise _NativePriorEvidenceViolation("prior-run-changed")
                    if set(names) != set(prior_names) | {new_run}:
                        raise _NativePriorEvidenceViolation("evidence-runs-changed")
                if lock_identity is None and names:
                    raise LauncherError("native-prior-custody-required")
                entries = []
                for name in names:
                    check_current()
                    run_fd = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                        dir_fd=runs_fd,
                    )
                    try:
                        run_identity = identity(os.fstat(run_fd))
                        if (
                            run_identity[3:] != (owner, group)
                            or stat.S_IMODE(run_identity[2]) != 0o700
                        ):
                            raise ValueError("prior-entry-invalid")
                        if name == new_run:
                            digest = None
                            bound_identity = (name, run_identity)
                        else:
                            metadata = []
                            digest = _native_prior_tree_digest(
                                run_fd,
                                check_current=check_current,
                                metadata_lease=metadata,
                            )
                            tree_leases.extend(
                                (
                                    root["path"]
                                    + "/runs/"
                                    + name
                                    + ("" if relative == "." else "/" + relative),
                                    facts,
                                )
                                for relative, facts in metadata
                            )
                        if (
                            identity(os.stat(name, dir_fd=runs_fd, follow_symlinks=False))
                            != run_identity
                        ):
                            raise ValueError("prior-entry-changed")
                        entries.append((name, run_identity, digest))
                    finally:
                        os.close(run_fd)
                if (
                    sorted(os.listdir(runs_fd)) != names
                    or identity(os.fstat(runs_fd)) != runs_identity
                    or identity(os.stat("runs", dir_fd=root_fd, follow_symlinks=False))
                    != runs_identity
                ):
                    raise ValueError("prior-runs-changed")
                entries = tuple(entries)
            if identity(os.fstat(root_fd)) != root_identity:
                raise ValueError("prior-root-changed")
            check_fd, current_components = runtime_profile._walk(root["path"], "directory")
            try:
                if (
                    current_components != components
                    or identity(os.fstat(check_fd)) != root_identity
                ):
                    raise ValueError("prior-root-changed")
            finally:
                os.close(check_fd)
    # This tuple was built exclusively from the original admitted FD read,
    # before scope cleanup. Returning it does not refresh any original fact.
    passive_lease.append(
        (
            components,
            root["path"],
            root_stamp,
            runs_stamp,
            lock_identity,
            tuple(tree_leases),
            bound_identity,
        )
    )
    check_current()
    return components, root_identity, runs_identity, lock_identity, entries


def _native_prior_passive_closure(lease):
    """Compare original known metadata only; no opens, parsing, locks or renewal."""
    components, root, root_stamp, runs_stamp, lock_identity, trees, bound = lease

    def stamp(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_uid,
            info.st_gid,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_rdev,
        )

    def observe(path, expected, *, code, mode="stamp"):
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            if expected is None:
                return
            raise _NativePriorEvidenceViolation(code) from None
        except OSError:
            # An unreadable observation cannot establish a physical mismatch.
            raise LauncherError("native-prior-evidence-unreadable") from None
        facts = stamp(info)
        if mode == "component":
            facts = facts[:2]
            if not stat.S_ISDIR(info.st_mode):
                raise _NativePriorEvidenceViolation(code)
        elif mode == "identity":
            facts = facts[:5]
        elif mode == "lock":
            facts = facts[:6]
        if expected is None or facts != expected:
            raise _NativePriorEvidenceViolation(code)

    for path, device, inode in components:
        observe(path, (device, inode), code="prior-run-changed", mode="component")
    observe(root, root_stamp, code="prior-run-changed")
    for path, facts in trees:
        observe(path, facts, code="prior-run-changed")
    if bound is not None:
        observe(root + "/runs/" + bound[0], bound[1], code="prior-run-changed", mode="identity")
    observe(root + "/runs", runs_stamp, code="evidence-runs-changed")
    observe(root + "/.s11-custody.lock", lock_identity, code="prior-run-changed", mode="lock")


def _native_start_prior_guard(
    *, invocation, bootstrap, profile, session, installation, host_deadline
):
    """Retain an immutable prior baseline before effects; never grant an action."""
    import time

    rp = bootstrap.apparatus["ex10_runtime_profile"]
    durable = bootstrap.apparatus["ex10_authority_backend"]
    profile_bytes = rp.canonical_runtime_profile_bytes(profile)

    def current():
        installation()
        if time.monotonic() >= host_deadline:
            raise Aborted("timeout")

    bound_run = (
        None if invocation.action == "start-official" else session.to_dict()["official_run_id"]
    )
    if invocation.action != "start-official" and (
        type(bound_run) is not str or _RUN_ID.fullmatch(bound_run) is None
    ):
        raise LauncherError("native-prior-run-context")
    completion_writer_lease = None
    last_verified_passive = None

    def observe(expected_session_bytes, *, writer=None, new_run=None, prior_names=None):
        nonlocal completion_writer_lease
        current()
        provided_writer = writer is not None
        lease = []
        with contextlib.ExitStack() as scope:
            session_current = None
            if writer is None:
                writer = scope.enter_context(bootstrap.writer())
                session_current = scope.enter_context(
                    _native_session_lock(bootstrap, invocation.session_root)
                )
            if type(writer) is not durable.HeldWriter:
                raise LauncherError("native-prior-writer-context")
            writer.check()
            started, budget = writer.started, writer.budget
            if (
                type(started) not in (int, float)
                or type(budget) not in (int, float)
                or not 0 < budget <= 120
            ):
                raise LauncherError("native-prior-writer-context")
            if provided_writer:
                if completion_writer_lease is None:
                    completion_writer_lease = (writer, started, budget)
                elif (
                    writer is not completion_writer_lease[0]
                    or (started, budget) != completion_writer_lease[1:]
                ):
                    raise LauncherError("native-prior-writer-context")

            def scan_current():
                current()
                if (writer.started, writer.budget) != (started, budget):
                    raise LauncherError("native-prior-writer-context")
                try:
                    writer.check()
                except Exception:
                    # Writer context or expiry is not a physical prior mismatch.
                    raise LauncherError("native-prior-writer-context") from None

            found = bootstrap.load(invocation)
            if found._bytes != expected_session_bytes:
                raise LauncherError("native-prior-session-changed")
            observed_profile, *_, recheck = bootstrap.qualify(invocation, session=found)
            if rp.canonical_runtime_profile_bytes(observed_profile) != profile_bytes:
                raise LauncherError("native-prior-profile-changed")
            # Finish external approval/checkpoint calls before the passive
            # evidence inventory. Later checks retain the same observed facts.
            if bootstrap.load(invocation)._bytes != expected_session_bytes:
                raise LauncherError("native-prior-session-changed")
            checkpoint = (found._expected_tip, found._expected_count)
            writer.check()
            if session_current is not None:
                session_current()
            recheck()
            try:
                result = _native_prior_start_inventory(
                    runtime_profile=rp,
                    profile=observed_profile,
                    check_current=scan_current,
                    new_run=new_run,
                    prior_names=prior_names,
                    passive_lease=lease,
                )
            except ValueError as error:
                if type(error) is ValueError and error.args in (
                    ("prior-directory-invalid",),
                    ("prior-entry-changed",),
                    ("prior-file-truncated",),
                    ("prior-directory-changed",),
                    ("prior-inventory-changed",),
                    ("prior-runs-changed",),
                    ("prior-root-changed",),
                    ("prior-root-invalid",),
                    ("prior-runs-invalid",),
                    ("prior-entry-invalid",),
                ):
                    if prior_names is not None:
                        raise _NativePriorEvidenceViolation("prior-run-changed") from None
                    raise LauncherError("native-prior-evidence-invalid") from None
                # Procfs/parser/context failures do not establish a changed prior.
                raise LauncherError("native-prior-evidence-context") from None
            except OSError:
                raise LauncherError("native-prior-evidence-unreadable") from None
            codec = bootstrap.apparatus["ex10_session_v2"]
            confirmed = codec.load_official_session(
                invocation.session_root / SESSION_FILE,
                context=bootstrap.context(),
                profile=observed_profile,
                expected_tip=checkpoint[0],
                expected_count=checkpoint[1],
            )
            if (
                type(confirmed) is not codec.OfficialSessionV2
                or confirmed._bytes != expected_session_bytes
            ):
                raise LauncherError("native-prior-session-changed")
            recheck()
            writer.check()
            if session_current is not None:
                session_current()
            current()
            recheck.check_authority_current()
        # Neither initial nor repeated observations hold global/session/custody
        # locks over the worker. Existing completion caller keeps its own locks.
        current()
        recheck.check_authority_current()
        if len(lease) != 1:
            raise LauncherError("native-prior-evidence-context")
        _native_prior_passive_closure(lease[0])
        # Lease checks do not create IO descriptors. Only original passive
        # sources and pure installation/time/authority checks follow them.
        recheck.check_source_leases_current()
        current()
        recheck.check_authority_current()
        return result, lease[0]

    # Only the current bound run may evolve during a non-START action. The
    # approved root, runs namespace and every other tree remain immutable.
    # START retains its original new-run transition and complete prior set.
    baseline, _baseline_passive = observe(session._bytes, new_run=bound_run)
    all_runs = {name: (identity, digest) for name, identity, digest in baseline[4]}
    if bound_run is not None and bound_run not in all_runs:
        raise LauncherError("native-prior-run-context")
    previous = tuple(sorted((name, facts) for name, facts in all_runs.items() if name != bound_run))
    admitted_transition = (
        None if bound_run is None else (bound_run, baseline[2], baseline[3], all_runs[bound_run][0])
    )

    def verify(expected_run_id, expected_session_bytes, *, writer=None):
        nonlocal admitted_transition, last_verified_passive
        current()
        if type(expected_run_id) is not str or _RUN_ID.fullmatch(expected_run_id) is None:
            raise LauncherError("native-prior-run-context")
        if bound_run is not None and expected_run_id != bound_run:
            raise LauncherError("native-prior-run-context")
        if expected_run_id in dict(previous):
            raise _NativePriorEvidenceViolation("evidence-runs-changed")
        observed, observed_passive = observe(
            expected_session_bytes,
            writer=writer,
            new_run=expected_run_id,
            prior_names=tuple(name for name, _ in previous),
        )
        if observed[:2] != baseline[:2] or (baseline[2] is not None and observed[2] != baseline[2]):
            raise _NativePriorEvidenceViolation("prior-run-changed")
        if baseline[3] is not None and observed[3] != baseline[3]:
            raise LauncherError("native-prior-custody-changed")
        found = {name: (identity, digest) for name, identity, digest in observed[4]}
        if any(found.get(name) != facts for name, facts in previous):
            raise _NativePriorEvidenceViolation("prior-run-changed")
        if set(found) != {name for name, _ in previous} | {expected_run_id}:
            raise _NativePriorEvidenceViolation("evidence-runs-changed")
        transition = (expected_run_id, observed[2], observed[3], found[expected_run_id][0])
        if admitted_transition is None:
            admitted_transition = transition
        elif transition != admitted_transition:
            raise _NativePriorEvidenceViolation("prior-run-changed")
        current()
        _native_prior_passive_closure(observed_passive)
        last_verified_passive = observed_passive

    def contains_prior(returned_run_id):
        current()
        return type(returned_run_id) is str and any(returned_run_id == name for name, _ in previous)

    def original_passive():
        if last_verified_passive is None:
            raise LauncherError("native-prior-evidence-context")
        current()
        _native_prior_passive_closure(last_verified_passive)
        current()

    verify._check_original_passive_current = original_passive
    verify._contains_prior = contains_prior
    verify._check_current = current
    return verify


def _native_start_domain_refusal(
    *, invocation, bootstrap, profile, session, installation, host_deadline, custody_hmac_key
):
    """Return only a source-qualified known prior refusal before durable effects."""
    import time

    apparatus = bootstrap.apparatus
    rp, codec, durable, policy = (
        apparatus["ex10_runtime_profile"],
        apparatus["ex10_session_v2"],
        apparatus["ex10_authority_backend"],
        apparatus["ex10_prior_custody"],
    )
    expected_session = session._bytes
    expected_profile = rp.canonical_runtime_profile_bytes(profile)
    writer_lease = None

    def current():
        installation()
        if time.monotonic() >= host_deadline:
            raise Aborted("timeout")
        if writer_lease is not None and time.monotonic() >= writer_lease[0] + writer_lease[1]:
            raise NativeActionFailure("native-start-prior-writer-expired")

    def eligible(value):
        return (
            value["official_run_id"] is None
            and not value["in_flight"]
            and value["state"]["uncertain"] is False
            and value["state"]["blocked"] is False
            and "start-official" in value["state"]["next_allowed_actions"]
        )

    current()
    with contextlib.ExitStack() as writer_scope:
        try:
            writer = writer_scope.enter_context(bootstrap.writer())
        except apparatus["ex10_authority_sqlite"].AuthorityError as error:
            if error.code == "LOCK_BUSY":
                raise LauncherBusy() from None
            raise
        if type(writer) is not durable.HeldWriter:
            raise NativeActionFailure("native-start-prior-writer-context")
        writer.check()
        writer_lease = (writer.started, writer.budget)
        if (
            type(writer.started) not in (int, float)
            or type(writer.budget) not in (int, float)
            or not 0 < writer.budget <= 120
        ):
            raise NativeActionFailure("native-start-prior-writer-context")
        with _native_session_lock(bootstrap, invocation.session_root) as session_current:
            found = bootstrap.load(invocation)
            if found._bytes != expected_session:
                raise SessionTampered("native-start-prior-session-changed")
            original_bytes, session_lease = _capture_native_source(
                invocation.session_root / SESSION_FILE, error_type=SessionTampered
            )
            if original_bytes != expected_session:
                raise SessionTampered("native-start-prior-session-changed")

            def passive_session():
                captured, lease = _capture_native_source(
                    invocation.session_root / SESSION_FILE, error_type=SessionTampered
                )
                if captured != expected_session or lease != session_lease:
                    raise SessionTampered("native-start-prior-session-changed")

            _native_check_recorded_outputs(invocation, found)
            manifest = found.to_dict()
            if not eligible(manifest):
                raise NativeActionFailure("native-intent-state-blocked")
            observed_profile, *_, qualified = bootstrap.qualify(invocation, session=found)
            if rp.canonical_runtime_profile_bytes(observed_profile) != expected_profile:
                raise NativeActionFailure("native-start-prior-profile-changed")
            common_bytes = qualified.read_start_prior()
            # Unknown/global uncertainty is not a causal known-run refusal.
            if common_bytes is None:
                return None
            common = tuple(
                policy.PriorRun(**{**row, "problems": tuple(row["problems"])})
                for row in json.loads(common_bytes)
            )
            root = observed_profile.to_dict()["host_roots"]["evidence"]
            owner, group = root["owner_uid"], root["owner_gid"]
            if (owner, group) != (os.geteuid(), os.getegid()):
                raise NativeActionFailure("native-start-prior-root-context")

            def scan_current():
                current()
                if (writer.started, writer.budget) != writer_lease:
                    raise NativeActionFailure("native-start-prior-writer-context")
                writer.check()
                session_current()
                qualified.check_authority_current()

            # Finish externally installed approvals/checkpoint reads before scanning.
            qualified.check_approval_current()
            if bootstrap.load(invocation)._bytes != expected_session:
                raise SessionTampered("native-start-prior-session-changed")
            qualified()
            passive_session()
            _native_check_recorded_outputs(invocation, found)
            scan_current()
            root_fd, components = rp._walk(root["path"], "directory")
            try:
                root_stamp = policy._PriorFDReader._stamp(os.fstat(root_fd))
                with _native_prior_custody_read(
                    root_fd, owner=owner, group=group, check_current=scan_current
                ) as custody_identity:
                    try:
                        if custody_identity is None:
                            probe = policy._PriorFDReader(root_fd, scan_current)
                            runs = probe.probe("runs")
                            if runs is not None:
                                if not stat.S_ISDIR(runs.st_mode) or probe.names("runs"):
                                    return None
                        local, frozen = policy.read_prior_custody(
                            root_fd,
                            custody_hmac_key=custody_hmac_key,
                            publication_verification_key=None,
                            check_current=scan_current,
                        )
                    except policy.PriorContextUnavailable:
                        # This installation carries no approved publication verification
                        # key. PUBLIC and unsupported roots use the existing worker path.
                        return None
                    except policy.PriorCustodyError:
                        raise NativeActionFailure("native-start-prior-read-context") from None
                    local_reasons, terminal = policy.binding_refusal(
                        local,
                        evidence_mode=policy.LOCAL_TEST_MODE,
                        acknowledged=(),
                    )
                    if local_reasons:
                        reasons = local_reasons
                    else:
                        common_by_id = {run.run_id: run for run in common}
                        if len(common_by_id) != len(common):
                            raise NativeActionFailure("native-start-prior-common-context")
                        diagnostics = []
                        for run in local:
                            if run.evidence_mode == policy.OFFICIAL_MODE:
                                if common_by_id.get(run.run_id) != run:
                                    raise NativeActionFailure("native-start-prior-common-context")
                            elif (
                                run.evidence_mode == policy.LOCAL_TEST_MODE
                                and run.run_id not in common_by_id
                            ):
                                diagnostics.append(run)
                            else:
                                raise NativeActionFailure("native-start-prior-common-context")
                        reasons, terminal = policy.binding_refusal(
                            [*common, *diagnostics],
                            evidence_mode=policy.OFFICIAL_MODE,
                            acknowledged=invocation.acknowledged,
                        )
                    if not reasons:
                        return None
                    checkpoint = (found._expected_tip, found._expected_count)
                    result = _summary(
                        manifest,
                        invocation.action,
                        "REFUSED",
                        EXIT_TERMINAL if terminal else EXIT_REFUSED,
                        output=None,
                    )
                    result["problems"] = list(reasons)
                    qualified()
                    if qualified.read_start_prior() != common_bytes:
                        raise NativeActionFailure("native-start-prior-common-changed")
                    policy.verify_prior_snapshot(root_fd, frozen, check_current=scan_current)
                    scan_current()
            finally:
                os.close(root_fd)

    def final_fence():
        current()
        # Read-only final observation after all owned locks and factory lock close.
        confirmed = bootstrap.load(invocation)
        if confirmed._bytes != expected_session:
            raise SessionTampered("native-start-prior-session-changed")
        passive_session()
        _native_check_recorded_outputs(invocation, confirmed)
        qualified.check_approval_current()
        if bootstrap.checkpoint() != checkpoint:
            raise SessionTampered("native-start-prior-checkpoint-changed")
        qualified()
        if qualified.read_start_prior() != common_bytes:
            raise NativeActionFailure("native-start-prior-common-changed")
        fd, mapped = rp._walk(root["path"], "directory")
        try:
            if mapped != components or policy._PriorFDReader._stamp(os.fstat(fd)) != root_stamp:
                raise NativeActionFailure("native-start-prior-root-changed")
            with _native_prior_custody_read(
                fd, owner=owner, group=group, check_current=current
            ) as final_custody:
                if final_custody != custody_identity:
                    raise NativeActionFailure("native-start-prior-custody-changed")
                policy.verify_prior_snapshot(fd, frozen, check_current=current)
                last = codec.load_official_session(
                    invocation.session_root / SESSION_FILE,
                    context=bootstrap.context(),
                    profile=observed_profile,
                    expected_tip=checkpoint[0],
                    expected_count=checkpoint[1],
                )
                if type(last) is not codec.OfficialSessionV2 or last._bytes != expected_session:
                    raise SessionTampered("native-start-prior-session-changed")
                passive_session()
                _native_check_recorded_outputs(invocation, last)
                qualified()
                policy.verify_prior_snapshot(fd, frozen, check_current=current)
        finally:
            os.close(fd)
        # Retain the original qualification after every owned descriptor closes.
        # Never refresh the authority snapshot or writer deadline here.
        qualified()
        passive_session()
        _native_check_recorded_outputs(invocation, confirmed)
        qualified()
        if qualified.read_start_prior() != common_bytes:
            raise NativeActionFailure("native-start-prior-common-changed")
        passive_session()
        current()
        qualified.check_authority_current()

    final_fence()
    return result, final_fence


class NativeActionFailure(LauncherError):
    """A failed native operational attempt; its durable intent stays retained."""


class NativeActionFactory:
    """Host installation of raw resources and independent approval providers.

    No request, environment, plugin or profile file selects these callbacks.
    Raw selectors are independently qualified by the actual internally built
    native components. The installation never supplies an assembled policy,
    qualifier, Sources, journal, publisher, proof signer or broker.
    """

    _PROVIDERS = frozenset(
        {
            "prepare_oneoff",
            "approve_action",
            "approve_creation",
            "prepare_envelope",
            "prepare_bridge",
            "holder_admitter",
            "approve_completion",
            "approve_terminal",
            "prepare_domain",
            "approve_domain",
            "prepare_cleanup",
            "approve_cleanup",
        }
    )

    def __init__(
        self,
        *,
        providers,
        signer,
        custody_hmac_key,
        runner_factory=DockerRunner,
        process_factory=None,
    ):
        import subprocess
        from types import MappingProxyType

        if (
            type(providers) is not dict
            or set(providers) != self._PROVIDERS
            or not all(callable(v) for v in providers.values())
            or type(custody_hmac_key) is not bytes
            or len(custody_hmac_key) < 32
            or not callable(runner_factory)
            or (process_factory is not None and not callable(process_factory))
        ):
            raise LauncherError("native-factory-installation-invalid")
        self.providers = MappingProxyType(dict(providers))
        self.signer, self.custody_key = signer, custody_hmac_key
        self.runner = runner_factory
        self.process_factory = subprocess.Popen if process_factory is None else process_factory
        self._lock = threading.Lock()
        self._attempted = []

    def execute(self, invocation):
        operational_attempt = False
        final_success_fence = None
        failure_diagnostic = None
        known_active = None
        known_execution_refusal = None
        scientific_refusal = known_scientific_refusal = None
        refusal_pending_capture = None
        refusal_methods = None
        lock = self._lock
        if not lock.acquire(blocking=False):
            raise LauncherBusy()
        try:
            if any(previous is invocation for previous in self._attempted):
                raise LauncherError("native-factory-attempt-consumed")
            self._attempted.append(invocation)
            attempts = self._attempted
            original_attempts = tuple(attempts)
            import time

            host_deadline = time.monotonic() + (
                invocation.timeout or ACTIONS[invocation.action].timeout
            )
            bootstrap = invocation.native_bootstrap
            if (
                type(bootstrap) is not NativeLauncherBootstrap
                or bootstrap.action_factory is not self
            ):
                raise LauncherError("native-factory-installation-required")
            providers, signer, key, runner, process_factory = (
                self.providers,
                self.signer,
                self.custody_key,
                self.runner,
                self.process_factory,
            )
            original_invocation = {
                name: getattr(invocation, name) for name in invocation.__dataclass_fields__
            }
            original_bootstrap = {
                name: getattr(bootstrap, name) for name in bootstrap.__dataclass_fields__
            }

            def installation():
                if (
                    self._lock is not lock
                    or self._attempted is not attempts
                    or len(attempts) != len(original_attempts)
                    or any(a is not b for a, b in zip(attempts, original_attempts, strict=True))
                    or bootstrap.action_factory is not self
                    or self.providers is not providers
                    or self.signer is not signer
                    or self.custody_key is not key
                    or self.runner is not runner
                    or self.process_factory is not process_factory
                    or any(getattr(invocation, n) is not v for n, v in original_invocation.items())
                    or any(getattr(bootstrap, n) is not v for n, v in original_bootstrap.items())
                ):
                    raise LauncherError("native-factory-installation-changed")

            def call(name, **facts):
                installation()
                if time.monotonic() >= host_deadline:
                    raise Aborted("timeout")
                value = providers[name](**facts)
                installation()
                return value

            session = bootstrap.load(invocation)
            _native_check_recorded_outputs(invocation, session)
            profile, *_, original = bootstrap.qualify(invocation, session=session)
            if invocation.action == "status":
                status_eligibility = session.to_dict()
                if (
                    status_eligibility["official_run_id"] is None
                    or "status" not in status_eligibility["state"]["next_allowed_actions"]
                ):
                    # Preserve the existing STATUS predicate and its native
                    # cause as validation before an operational observation.
                    original()
                    original.check_authority_current()
                    installation()
                    original.check_source_leases_current()
                    if time.monotonic() >= host_deadline:
                        raise Aborted("timeout")
                    raise LauncherError("native-status-run-required")
                operational_attempt = True
                result, final_success_fence = _native_domain_status(
                    invocation=invocation,
                    bootstrap=bootstrap,
                    session=session,
                    profile=profile,
                    call=call,
                    runner_factory=runner,
                    installation=installation,
                    host_deadline=host_deadline,
                    custody_hmac_key=key,
                )
                return result
            if invocation.action == "cleanup-stack":
                cleanup_eligibility = session.to_dict()
                cleanup_state = cleanup_eligibility["state"]
                if (
                    cleanup_eligibility["in_flight"]
                    or cleanup_state["uncertain"]
                    or cleanup_state["blocked"]
                    or "cleanup-stack" not in cleanup_state["next_allowed_actions"]
                ):
                    # Known local ineligibility precedes all cleanup providers.
                    # Global no_active and the late intent fence remain required.
                    original()
                    original.check_authority_current()
                    installation()
                    original.check_source_leases_current()
                    if time.monotonic() >= host_deadline:
                        raise Aborted("timeout")
                    raise LauncherError("native-intent-state-blocked")
                operational_attempt = True
                result, final_success_fence = _native_cleanup_resources(
                    invocation=invocation,
                    bootstrap=bootstrap,
                    session=session,
                    profile=profile,
                    call=call,
                    runner_factory=runner,
                    installation=installation,
                )
                return result
            if invocation.action not in NativeOneOffDocker._WORKER_ACTIONS:
                raise LauncherError("native-factory-action-invalid")
            eligibility = session.to_dict()
            eligibility_state = eligibility["state"]
            if (
                eligibility["in_flight"]
                or eligibility_state["uncertain"]
                or eligibility_state["blocked"]
                or invocation.action not in eligibility_state["next_allowed_actions"]
            ):
                # Known ineligible state is public validation before this action
                # acquires an intent or invokes an operational provider. Preserve
                # the original admission and all existing uncertainty unchanged.
                original()
                original.check_authority_current()
                installation()
                original.check_source_leases_current()
                if time.monotonic() >= host_deadline:
                    raise Aborted("timeout")
                raise LauncherError("native-intent-state-blocked")
            prior_guard = None
            if invocation.action == "start-official":
                refusal = _native_start_domain_refusal(
                    invocation=invocation,
                    bootstrap=bootstrap,
                    profile=profile,
                    session=session,
                    installation=installation,
                    host_deadline=host_deadline,
                    custody_hmac_key=key,
                )
                if refusal is not None:
                    result, final_success_fence = refusal
                    return result
            prior_guard = _native_start_prior_guard(
                invocation=invocation,
                bootstrap=bootstrap,
                profile=profile,
                session=session,
                installation=installation,
                host_deadline=host_deadline,
            )
            prior_passive = prior_guard._check_original_passive_current
            plan = call("prepare_oneoff", invocation=invocation, session=session, profile=profile)
            oneoff_fields = {
                "name",
                "image_digest",
                "network_id",
                "bootstrap_bytes",
                "service_environment",
                "owner_labels",
            }
            if type(plan) is not dict or set(plan) != oneoff_fields:
                raise LauncherError("native-factory-oneoff-plan-invalid")
            plan = dict(plan)
            cfg = json.loads(plan["bootstrap_bytes"])
            if cfg["action"] != NativeOneOffDocker._WORKER_ACTIONS[invocation.action]:
                raise LauncherError("native-factory-action-invalid")
            options = cfg["command_options"]
            if invocation.action == "start-official":
                if (
                    not _SHA256.fullmatch(invocation.nonce)
                    or options.get("nonce") != invocation.nonce
                ) or options.get("acknowledge_not_demonstrated") != list(invocation.acknowledged):
                    raise LauncherError("native-factory-explicit-start-facts")
            elif options.get("run_id") != session.to_dict()["official_run_id"]:
                raise LauncherError("native-factory-run-required")
            if options.get("expected_git_sha") != invocation.expected_git_sha:
                raise LauncherError("native-factory-source-options")
            if cfg["action"] == "finalize":
                expected_reason = (
                    ABANDON_REASON if invocation.action == "abandon-official-pre-t0" else None
                )
                expected_gone = invocation.action == "declare-executor-gone"
                if (
                    options.get("abandon_reason") != expected_reason
                    or options.get("declare_executor_gone") is not expected_gone
                ):
                    raise LauncherError("native-factory-terminal-options")
            original()
            installation()
            operational_attempt = True
            intent = NativeActionIntent(
                invocation=invocation,
                bootstrap=bootstrap,
                container_name=plan["name"],
                output_dir="actions/" + cfg["exchange_id"],
                approve_action=providers["approve_action"],
            )
            try:
                pending = intent.begin()
            except (_NativeIntentStillActive, _NativeIntentExecutionRefused) as error:
                if type(error) is _NativeIntentStillActive and error is intent._active_signal:
                    known_active = error
                elif (
                    type(error) is _NativeIntentExecutionRefused
                    and error is intent._execution_signal
                    and type(error.reason) is str
                    and error.reason in _NATIVE_EXEC_PRECONDITION_REASONS
                ):
                    known_execution_refusal = error
                else:
                    raise NativeActionFailure("native-intent-approval-required") from None

                def final_denial_fence(signal=error):
                    installation()
                    signal.check_current()
                    installation()
                    original.check_source_leases_current()
                    if time.monotonic() >= host_deadline:
                        raise Aborted("timeout")
                    original.check_authority_current()

                final_success_fence = final_denial_fence
                raise
            installation()
            if invocation.action == "declare-executor-gone":

                def refusal_capture_current():
                    installation()
                    original.check_source_leases_current()
                    if time.monotonic() >= host_deadline:
                        raise Aborted("timeout")
                    original.check_authority_current()

                # Capture the published original pending file BEFORE container,
                # approval, worker and final scientific/refusal callbacks.
                refusal_pending_capture = _native_capture_refusal_input(
                    invocation.session_root / SESSION_FILE,
                    pending._bytes,
                    apparatus=bootstrap.apparatus,
                    check_current=refusal_capture_current,
                )
            control = NativeOneOffDocker(
                invocation=invocation,
                bootstrap=bootstrap,
                approve_creation=providers["approve_creation"],
                runner_factory=runner,
                **plan,
            )
            control.create()
            control.start()
            ready = {"deadline": None}
            attachment = NativeWorkerAttachment(
                control=control,
                read_ready_deadline=lambda: ready["deadline"],
                process_factory=process_factory,
            )
            deadline = attachment.deadline
            history = bootstrap.apparatus["ex10_history_authority"]
            snapshot = bootstrap.read_authority(
                profile.to_dict()["historical_lifecycle_authority_reference"]
            )
            history.current_revision(snapshot)
            prepared = graph = adapter = channel = None
            completed_worker = None
            terminal_action = invocation.action in {
                "finalize",
                "abandon-official-pre-t0",
                "declare-executor-gone",
            }
            namespace_scope = contextlib.ExitStack()
            owned_client_reaper = attachment.abort_client
            client_reaped = False
            try:
                publication, namespace_current = namespace_scope.enter_context(
                    _create_native_proof_publication(
                        invocation=invocation,
                        bootstrap=bootstrap,
                        exchange_id=cfg["exchange_id"],
                        preparation_deadline=deadline,
                        authority_snapshot=snapshot,
                    )
                )
                publication.publish("profile", control.profile._bytes)
                publication.publish(
                    "header",
                    bootstrap.apparatus["ex10_session_v2"].canonical_session_header_bytes(
                        pending.header
                    ),
                )
                attachment.create()
                owned_client_reaper = attachment._reap_owned

                def current():
                    installation()
                    if completed_worker is not None:
                        completed_worker.check_retained()
                    elif attachment.transferred:
                        attachment._check()
                    else:
                        attachment._retained()
                    namespace_current()
                    history.current_revision(snapshot)

                raw_envelope = call(
                    "prepare_envelope",
                    invocation=invocation,
                    profile=profile,
                    pending=pending,
                    control=control,
                    attachment=attachment,
                    preparation_deadline=deadline,
                )
                if type(raw_envelope) is not dict or set(raw_envelope) != {
                    "configuration",
                    "attached_bytes",
                }:
                    raise LauncherError("native-factory-envelope-plan-invalid")
                if invocation.action == "start-official" and raw_envelope["configuration"][
                    "application_preflight_reference"
                ]["path"] != str(invocation.preflight_report):
                    raise LauncherError("native-factory-explicit-preflight-report")
                current()
                prepared = _prepare_native_worker_bindings(
                    control=control,
                    publication=publication,
                    envelope_configuration=raw_envelope["configuration"],
                    check_current=current,
                    preparation_deadline=deadline,
                    authority_snapshot=snapshot,
                    attached_envelope_bytes=raw_envelope["attached_bytes"],
                )
                ready["deadline"] = prepared.ready_deadline
                prepared_completed = prepared.reobserve_completed
                prepared_passive = prepared.check_completed_inputs_passive

                def refusal_original_functions():
                    if (
                        type(prepared) is not NativePreparedWorkerBindings
                        or prepared.reobserve_completed is not prepared_completed
                        or prepared.check_completed_inputs_passive is not prepared_passive
                        or prior_guard._check_original_passive_current is not prior_passive
                    ):
                        raise LauncherError("native-refusal-original-input-context")

                def refusal_final_io(check_worker, check_authority):
                    refusal_original_functions()
                    prepared_completed(check_worker, check_authority)
                    prior_guard(graph.signer.run_id, pending._bytes)
                    refusal_original_functions()

                def refusal_final_passive():
                    refusal_original_functions()
                    prepared_passive()
                    prior_passive()
                    refusal_original_functions()
                    installation()
                    if time.monotonic() >= host_deadline:
                        raise Aborted("timeout")
                    original.check_authority_current()

                raw_bridge = call(
                    "prepare_bridge",
                    invocation=invocation,
                    profile=profile,
                    pending=pending,
                    control=control,
                    prepared=prepared,
                    publication=publication,
                    authority_snapshot=snapshot,
                )
                fields = {
                    "session_bindings",
                    "session_configuration",
                    "policy_configuration",
                    "publisher_configuration",
                    "source_configuration",
                    "resource_configuration",
                    "reconciled",
                }
                if type(raw_bridge) is not dict or set(raw_bridge) != fields:
                    raise LauncherError("native-factory-bridge-plan-invalid")
                raw_bridge = dict(raw_bridge)
                op = bootstrap.apparatus["ex10_operational_context"]
                terminal_lifecycle = None
                if terminal_action:
                    if (
                        type(raw_bridge["source_configuration"]) is not dict
                        or type(raw_bridge["resource_configuration"]) is not dict
                        or raw_bridge["source_configuration"]["lifecycle_sources"] is not None
                    ):
                        raise LauncherError("native-factory-terminal-provider-incompatible")
                    terminal_lifecycle = NativeTerminalLifecycleSource()
                    source_config = dict(raw_bridge["source_configuration"])
                    source_config["lifecycle_sources"] = terminal_lifecycle
                    resources = dict(raw_bridge["resource_configuration"])
                    resources["transport"] = NativeTerminalDispatchTransport(resources["transport"])
                    raw_bridge["source_configuration"] = source_config
                    raw_bridge["resource_configuration"] = resources
                resource_plan = op.NativePolicyResourcePlan(**raw_bridge["resource_configuration"])
                session_cfg = dict(raw_bridge["session_configuration"])
                if (
                    session_cfg["process"] != prepared.process
                    or session_cfg["expected_tip"] != pending._expected_tip
                    or session_cfg["expected_count"] != pending._expected_count
                ):
                    raise LauncherError("native-factory-session-plan-invalid")
                observed_deployment = session_cfg["deployment_observer"]

                def deployment(**facts):
                    if completed_worker is None:
                        prepared.check_retained()
                    else:
                        prepared.reobserve_completed(
                            completed_worker.check_retained,
                            completed_worker.check_authority_current,
                        )
                    observed = observed_deployment(**facts)
                    if completed_worker is None:
                        prepared.check_retained()
                    else:
                        prepared.reobserve_completed(
                            completed_worker.check_retained,
                            completed_worker.check_authority_current,
                        )
                    if observed != prepared.process:
                        raise LauncherError("native-factory-process-changed")
                    return prepared.process

                session_cfg["deployment_observer"] = deployment
                graph = _compose_native_bridge(
                    apparatus=bootstrap.apparatus,
                    session_bindings=raw_bridge["session_bindings"],
                    session_configuration=session_cfg,
                    policy_configuration=raw_bridge["policy_configuration"],
                    publisher_configuration=raw_bridge["publisher_configuration"],
                    source_configuration=raw_bridge["source_configuration"],
                    resource_plan=resource_plan,
                    signer=signer,
                    action=cfg["action"],
                    exchange_id=cfg["exchange_id"],
                    expected_run_id=None if cfg["action"] == "start" else options["run_id"],
                    publish_role=publication.publish,
                    holder_admitter=providers["holder_admitter"],
                    check_current=current,
                    preparation_deadline=deadline,
                    authority_snapshot=snapshot,
                    start_nonce=invocation.nonce,
                )

                def observe_process():
                    graph.check_retained()
                    prepared.check_retained()
                    return prepared.process

                adapter, channel = attachment.admit(
                    identity=prepared.process, observer=observe_process
                )
                if cfg["action"] == "start":
                    response = graph.broker.serve_start(channel)
                elif cfg["action"] == "execute":
                    reader = NativeArmedArtifactReader(
                        apparatus=bootstrap.apparatus,
                        admitted=graph.signer.admitted,
                        run_id=options["run_id"],
                        custody_hmac_key=key,
                        deadline=deadline,
                    )
                    response = NativeEmissionProofPublication(
                        broker=graph.broker, reader=reader, publish_role=publication.publish
                    ).serve_single(channel)
                else:
                    response = graph.broker.serve_single(channel)
                if (
                    not channel.closed
                    or channel.position != len(channel.steps)
                    or channel.read_fd is not None
                    or channel.write_fd is not None
                    or channel.error_fd is not None
                    or response != graph.signer.signed_response
                ):
                    raise LauncherError("native-factory-incomplete-exchange")
                namespace_current()
                # Preparation ends only after real frames/dual EOF and publication
                # have completed. The scientific worker can outlive the RPC pipes.
                if not terminal_action:
                    namespace_scope.close()
                while control.worker_exit() is None:
                    installation()
                    if time.monotonic() >= host_deadline:
                        raise Aborted("timeout")
                    time.sleep(0.05)
                worker_code = control.worker_exit()
                if worker_code != 0:
                    if (
                        type(worker_code) is int
                        and worker_code == EXIT_TERMINAL
                        and (invocation.action == "declare-executor-gone")
                    ):
                        scientific_refusal = NativeScientificRefusalObservation(
                            control=control,
                            pending=pending,
                            pending_capture=refusal_pending_capture,
                            expected_run_id=graph.signer.run_id,
                            installation=installation,
                            original=original,
                            host_deadline=host_deadline,
                            final_io=refusal_final_io,
                            final_passive=refusal_final_passive,
                        )
                        refusal_methods = (
                            scientific_refusal.qualify,
                            scientific_refusal.finish,
                            scientific_refusal.check_retained,
                            scientific_refusal.check_authority_current,
                            scientific_refusal.check_binding,
                        )
                        signal = refusal_methods[0]()
                        adapter.finish_client()
                        # Reobserve the ORIGINAL process/source/envelope commitments
                        # as a finished worker observation, never scientific grant.
                        refusal_final_io(refusal_methods[2], refusal_methods[3])
                        refusal_methods[2]()
                        raise signal
                    raise LauncherError("native-factory-worker-unsuccessful")
                adapter.finish_client()
                expected_run = graph.signer.run_id
                if prior_guard is not None:
                    prior_guard(expected_run, pending._bytes)
                observation = NativeWorkerResultObservation(
                    control=control,
                    expected_run_id=expected_run,
                    reconciled=raw_bridge["reconciled"],
                )
                if prior_guard is not None and invocation.action == "start-official":
                    failure_diagnostic = _NativeExistingRunDiagnostic(
                        prior_guard=prior_guard,
                        expected_run_id=expected_run,
                        pending_bytes=pending._bytes,
                    )
                    observation._start_failure_diagnostic = failure_diagnostic
                with contextlib.ExitStack() as output_scope:
                    with observation.capture() as (result_bytes, result, worker_current):
                        completed_worker = worker_current
                        terminal = None
                        if terminal_action:
                            reader = NativeTerminalArtifactReader(
                                apparatus=bootstrap.apparatus,
                                admitted=graph.signer.admitted,
                                action=invocation.action,
                                expected_run_id=expected_run,
                                result_bytes=result_bytes,
                                custody_hmac_key=key,
                                deadline=deadline,
                                reconciled=raw_bridge["reconciled"],
                            )
                            terminal = NativeLifecycleTerminalPublisher(
                                broker=graph.broker,
                                reader=reader,
                                approve_terminal=providers["approve_terminal"],
                                verify_worker=lambda **_: worker_current(),
                            )
                            terminal_lifecycle.bind_once(terminal)
                            terminal = terminal.retire()
                            namespace_scope.close()
                        output_path, output_current = output_scope.enter_context(
                            _native_record_action_output(
                                bootstrap=bootstrap,
                                invocation=invocation,
                                exchange_id=cfg["exchange_id"],
                                result_bytes=result_bytes,
                                check_current=worker_current.check_retained,
                                check_authority_current=worker_current.check_authority_current,
                            )
                        )
                        proposal = _native_result_proposal(
                            bootstrap=bootstrap,
                            pending=pending,
                            action=invocation.action,
                            result=result,
                            result_bytes=result_bytes,
                            prepared=prepared,
                            output_path=output_path,
                            reconciled=raw_bridge["reconciled"],
                        )

                        def qualify_completion(**facts):
                            installation()
                            prepared.reobserve_completed(
                                worker_current.check_retained,
                                worker_current.check_authority_current,
                            )
                            output_current()
                            if (
                                facts["pending_bytes"] != pending._bytes
                                or facts["proposal_bytes"] != proposal._bytes
                                or facts["result_bytes"] != result_bytes
                                or facts["action"] != invocation.action
                            ):
                                raise LauncherError("native-factory-completion-binding")
                            if prior_guard is not None:
                                prior_guard(
                                    expected_run,
                                    proposal._bytes if facts["written"] else pending._bytes,
                                    writer=facts["writer"],
                                )
                            if (
                                call(
                                    "approve_completion",
                                    **facts,
                                    control=control,
                                    prepared=prepared,
                                    response=response,
                                    terminal=terminal,
                                    output_path=output_path,
                                )
                                is not None
                            ):
                                raise LauncherError("native-factory-completion-required")
                            prepared.reobserve_completed(
                                worker_current.check_retained,
                                worker_current.check_authority_current,
                            )
                            output_current()
                            if prior_guard is not None:
                                prior_guard(
                                    expected_run,
                                    proposal._bytes if facts["written"] else pending._bytes,
                                    writer=facts["writer"],
                                )
                            if terminal is not None:
                                history.current_revision(terminal[0])

                        commit = NativeSessionActionCommit(
                            invocation=invocation,
                            bootstrap=bootstrap,
                            pending=pending,
                            proposal=proposal,
                            result_bytes=result_bytes,
                            qualify_completion=qualify_completion,
                            reconciled=raw_bridge["reconciled"],
                        )
                        commit.commit()
                        worker_current.bind_completed(commit)
                        worker_current()
                        completed = bootstrap.load(invocation)
                        if completed._bytes != proposal._bytes:
                            raise LauncherError("native-factory-completion-changed")
                        installation()
                        # Own local CLI cleanup precedes the observation's last
                        # providers and the recorded output's final passive reads.
                        owned_client_reaper()
                        client_reaped = True

                        def final_fence():
                            if prior_guard is not None:
                                prior_guard(expected_run, proposal._bytes)
                            # A fresh guard admission cannot replace original
                            # source identities or renew original worker authority.
                            # Both checks are passive/pure and open no descriptors.
                            original.check_source_leases_current()
                            worker_current.check_authority_current()

                        final_success_fence = final_fence
                        return _summary(
                            completed.to_dict(), invocation.action, "COHERENT", EXIT_OK, output=None
                        )
            finally:
                try:
                    original_failure = sys.exc_info()
                    if isinstance(original_failure[1], Aborted | KeyboardInterrupt):
                        # Deliver only known public aborts to the retained terminal
                        # namespace. Normal exits and other errors keep close()'s
                        # existing final deadline/authority and error semantics.
                        namespace_scope.__exit__(*original_failure)
                    else:
                        namespace_scope.close()
                finally:
                    try:
                        if channel is not None:
                            channel.close()
                    finally:
                        if not client_reaped:
                            owned_client_reaper()
        except _NativeScientificRefusal as error:
            if (
                type(scientific_refusal) is not NativeScientificRefusalObservation
                or type(error) is not _NativeScientificRefusal
                or error is not scientific_refusal.signal
            ):
                raise NativeActionFailure("native-refusal-observation-context") from None
            try:
                refusal_methods[4]()
            except Aborted:
                raise
            except LauncherError as binding_error:
                raise NativeActionFailure(binding_error.code) from None
            except Exception:
                raise NativeActionFailure("native-refusal-observation-context") from None
            known_scientific_refusal = error
            return _summary(
                json.loads(scientific_refusal.pending),
                invocation.action,
                "REFUSED",
                EXIT_TERMINAL,
                output=None,
            )
        except _NativeExistingRunRefusal as error:
            if failure_diagnostic is None or error is not failure_diagnostic.signal:
                raise NativeActionFailure("native-worker-result-invalid") from None
            raise
        except _NativeIntentStillActive as error:
            if known_active is not error:
                raise NativeActionFailure("native-intent-approval-required") from None
            raise LauncherError("executor-still-active") from None
        except _NativeIntentExecutionRefused as error:
            if (
                known_execution_refusal is not error
                or type(error) is not _NativeIntentExecutionRefused
                or type(error.reason) is not str
                or error.reason not in _NATIVE_EXEC_PRECONDITION_REASONS
            ):
                raise NativeActionFailure("native-intent-approval-required") from None
            raise LauncherError(error.reason) from None
        except Aborted:
            raise
        except LauncherError as error:
            if operational_attempt and not isinstance(error, NativeActionFailure | LauncherBusy):
                raise NativeActionFailure(error.code) from None
            raise
        except Exception:
            raise NativeActionFailure("native-factory-uncertain") from None
        finally:
            pending_error = sys.exc_info()[1]
            try:
                lock.release()
                if known_scientific_refusal is not None:
                    if (
                        type(scientific_refusal) is not NativeScientificRefusalObservation
                        or known_scientific_refusal is not scientific_refusal.signal
                    ):
                        raise NativeActionFailure("native-refusal-observation-context") from None
                    try:
                        refusal_methods[4]()
                        refusal_methods[1]()
                    except Aborted:
                        raise
                    except LauncherError as error:
                        raise NativeActionFailure(error.code) from None
                    except Exception:
                        raise NativeActionFailure(
                            "native-refusal-final-observation-uncertain"
                        ) from None
                if (
                    failure_diagnostic is not None
                    and failure_diagnostic.signal is not None
                    and pending_error is failure_diagnostic.signal
                ):
                    try:
                        failure_diagnostic.finish()
                    except Aborted:
                        raise
                    except LauncherError as error:
                        raise NativeActionFailure(error.code) from None
                    except Exception:
                        raise NativeActionFailure(
                            "native-factory-final-observation-expired"
                        ) from None
                    raise NativeActionFailure("start:run-id-not-new") from None
            finally:
                if failure_diagnostic is not None:
                    failure_diagnostic.fail()
            if final_success_fence is not None:
                try:
                    final_success_fence()
                except Aborted:
                    if (
                        invocation.action == "status"
                        or known_active is not None
                        or known_execution_refusal is not None
                    ):
                        raise
                    raise NativeActionFailure("native-factory-final-observation-expired") from None
                except _NativeStatusEvidenceViolation as error:
                    if (
                        type(error) is not _NativeStatusEvidenceViolation
                        or error.code != "evidence-changed-by-read"
                    ):
                        raise NativeActionFailure(
                            "native-factory-final-observation-expired"
                        ) from None
                    raise NativeActionFailure("evidence-changed-by-read") from None
                except _NativePriorEvidenceViolation as error:
                    if (
                        type(error) is not _NativePriorEvidenceViolation
                        or type(error.code) is not str
                        or error.code not in ("prior-run-changed", "evidence-runs-changed")
                    ):
                        raise NativeActionFailure(
                            "native-factory-final-observation-expired"
                        ) from None
                    raise NativeActionFailure(error.code) from None
                except Exception:
                    raise NativeActionFailure("native-factory-final-observation-expired") from None


def _execute_native_action(invocation: Invocation) -> dict[str, Any]:
    """Use only host-installed providers; absence denies before locks or effects."""
    bootstrap = invocation.native_bootstrap
    if type(bootstrap) is not NativeLauncherBootstrap:
        raise LauncherError("native-launcher-bootstrap-required")
    codec = bootstrap.apparatus["ex10_session_v2"]
    sql = bootstrap.apparatus["ex10_authority_sqlite"]
    try:
        if invocation.action == "initialize-session":
            return bootstrap.initialize(invocation)
        session = bootstrap.load(invocation)
        if invocation.action == "inspect":
            _, *_, original = bootstrap.qualify(invocation, session=session)
            _native_check_recorded_outputs(invocation, session)
            original()
            original.check_authority_current()
            return _summary(session.to_dict(), "inspect", "COHERENT", EXIT_OK, output=None)
        if type(bootstrap.action_factory) is not NativeActionFactory:
            raise LauncherError("native-action-factory-required")
        _native_check_recorded_outputs(invocation, session)
        return bootstrap.action_factory.execute(invocation)
    except codec.OfficialSessionError as exc:
        raise SessionTampered("native-session-" + exc.code) from None
    except sql.AuthorityError as exc:
        if exc.code == "LOCK_BUSY":
            raise LauncherBusy() from None
        raise LauncherError("native-authority-" + exc.code) from None
    except bootstrap.apparatus["ex10_runtime_profile"].RuntimeProfileError as exc:
        raise LauncherError("native-profile-" + exc.code) from None
    except OSError:
        raise LauncherError("native-storage-uncertain") from None


def execute(invocation: Invocation) -> dict[str, Any]:
    validate_invocation(invocation)
    base.bind(invocation.repo_root, invocation.expected_git_sha)
    if invocation.action == "initialize-session":
        return initialize_session(invocation)
    schema = _session_schema_for_dispatch(invocation)
    if schema == SESSION_SCHEMA:
        if invocation.action != "inspect":
            raise LauncherError("v1-read-only")
        return inspect_session(invocation)
    return _execute_native_action(invocation)


def initialize_session(invocation: Invocation) -> dict[str, Any]:
    """Create only an approved native session; never manufacture or convert v1."""
    return _execute_native_action(invocation)


def inspect_session(invocation: Invocation) -> dict[str, Any]:
    """Sólo lectura: nada se lanza ni se escribe. Verifica y deriva la próxima acción."""
    problems = []
    manifest = load_session(invocation.session_root, invocation.repo_root)
    if manifest["expected_git_sha"] != invocation.expected_git_sha:
        problems.append("session-other-head")
    try:
        _verify_checkout(invocation)
    except LauncherError as exc:
        problems.append(exc.code)
    classification = "COHERENT" if not problems else "VALIDATION_FAILED"
    summary = _summary(
        manifest, "inspect", classification, EXIT_OK if not problems else EXIT_USAGE, output=None
    )
    summary["problems"] = sorted(problems)
    return summary


def _execute_locked(invocation: Invocation, manifest: dict[str, Any]) -> dict[str, Any]:
    action, root = invocation.action, invocation.session_root
    spec = ACTIONS[action]
    started = invocation.clock()
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    seq = len(manifest["history"]) + 1
    output = base.prepare_output_dir(
        root / "actions" / f"{seq:03d}-{action}-{stamp}-{invocation.token()}", invocation.repo_root
    )
    meta: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session_id": manifest["session_id"],
        "action": action,
        "project": PROJECT,
        "evidence_mode": EVIDENCE_MODE,
        "official_run_id": manifest["official_run_id"],
        "expected_git_sha": invocation.expected_git_sha,
        "source_root_container": SOURCE_MOUNT,
        "source_mount_mode": "ro",
        "output_dir": str(output),
        "started_at": base._stamp(started),
    }
    entry: dict[str, Any] = {
        "seq": seq,
        "action": action,
        "started_at": meta["started_at"],
        "run_id": manifest["official_run_id"],
        "phase": None,
        "decision": None,
        "session_blocking": False,
    }
    try:
        outcome = _act(invocation, manifest, spec, output, meta, entry)
    except LauncherError as exc:
        outcome = Outcome("VALIDATION_FAILED", EXIT_USAGE, problems=[exc.code])
    return _record(invocation, manifest, output, meta, entry, outcome)


def _preconditions(spec: ActionSpec, state) -> dict[str, Any]:
    def count(service, running=True):
        return sum(
            1
            for c in state.containers
            if c.service == service and not c.oneoff and (c.state == "running" if running else True)
        )

    infra = all(
        count(s) == 1
        and all(c.healthy for c in state.containers if c.service == s and not c.oneoff)
        for s in ("db", "redis")
    )
    facts = {
        "db_redis_healthy": infra,
        "backend_running": count("backend"),
        "worker_running": count("worker"),
        "beat_running": count("beat"),
        "oneoff_containers": sum(
            1 for c in state.containers if c.oneoff and c.state in ACTIVE_CONTAINER_STATES
        ),
    }
    if not infra:
        raise LauncherError("db-redis-not-healthy")
    if facts["beat_running"]:
        raise LauncherError("beat-running")
    if facts["oneoff_containers"]:
        raise LauncherError("oneoff-container-present")
    if spec.requires_web and (facts["backend_running"], facts["worker_running"]) != (1, 1):
        raise LauncherError("web-worker-not-running")
    return facts


def _act(invocation, manifest, spec, output, meta, entry) -> Outcome:
    action, repo = invocation.action, invocation.repo_root
    if manifest["expected_git_sha"] != invocation.expected_git_sha:
        raise LauncherError("session-other-head")
    facts, integrity, branch = _verify_checkout(invocation)
    if integrity != manifest["launcher_blobs"] or branch != manifest["branch"]:
        raise LauncherError("session-launcher-differs")
    meta["repository"] = facts.to_json()
    state = derive_state(manifest)
    meta["session_state_before"] = state
    if action not in state["next_allowed_actions"]:
        raise LauncherError(f"action-not-allowed-now:{action}")
    run_id = manifest["official_run_id"]
    if action == "start-official":
        # Sólo los run_id públicos reconocidos: la sesión registra qué NOT_DEMONSTRATED se
        # aceptó para este START (la salida queda encadenada en el historial).
        meta["acknowledged_not_demonstrated"] = sorted(invocation.acknowledged)
        raw = _private_file(invocation.preflight_report, "preflight-report-unsafe")
        problems = preflight_report_problems(
            raw, git_sha=invocation.expected_git_sha, now=invocation.clock()
        )
        if problems:
            meta["preflight_report_problems"] = problems
            raise LauncherError("preflight-report-not-acceptable")
        base.write_file(output, "preflight.json", raw)
        meta["preflight_report_sha256"] = hashlib.sha256(raw).hexdigest()
    elif action == "execute-official":
        # El executor revalida el preflight custodiado; el launcher falla antes, igual.
        attached = _attached_preflight(invocation, manifest)
        problems = preflight_report_problems(
            attached, git_sha=invocation.expected_git_sha, now=invocation.clock()
        )
        if problems:
            meta["preflight_report_problems"] = problems
            raise LauncherError("preflight-report-not-acceptable")
    target = Target(PROJECT, tuple(COMPOSE_FILES))
    env = child_environment(invocation.environ)
    meta["child_env_names"] = sorted(env)
    secret_values = [env[name] for name in base.INTERPOLATION_ENV]
    runner = invocation.runner_factory(repo, env)
    if action == "cleanup-stack":
        return _cleanup_stack(runner, output, meta, secret_values)
    meta["docker"] = base.verify_docker(target, runner)
    before = base.docker_state(target, runner)
    base.write_file(output, "docker-before.txt", before.text().encode())
    if action == "declare-executor-gone":
        # Primero el contenedor exacto de `execute`: su liveness es la razón específica.
        meta["executor_container"] = _executor_container_state(manifest, before)
    meta["preconditions"] = _preconditions(ACTIONS[action], before)
    evidence_before = evidence_snapshot(repo)
    base.write_file(output, "evidence-before.json", base.canonical_bytes(evidence_before))
    container = f"{PROJECT}-{action}-{invocation.token()}"
    if container in before.names() or not base._NAME.fullmatch(container):
        raise LauncherError("container-name-unavailable")
    gitconfig = None
    if invocation.safe_directory:
        gitconfig = output / "safe-directory.gitconfig"
        base.write_file(output, gitconfig.name, base.SAFE_DIRECTORY_CONFIG.encode(), 0o644)
    argv = build_command(
        action,
        environ_secrets=secret_values,
        expected_git_sha=invocation.expected_git_sha,
        container_name=container,
        repo_root=repo,
        run_id=run_id,
        nonce=invocation.nonce,
        acknowledged=invocation.acknowledged,
        gitconfig=gitconfig,
        masks=facts.masked_files,
    )
    meta["container_name"] = container
    if ACTIONS[action].mutating:
        # Antes de lanzar: si el launcher muere, la sesión sabe qué contenedor quedó en curso.
        manifest.setdefault("in_flight", []).append(
            {
                "action": action,
                "container_name": container,
                "output_dir": str(output.relative_to(invocation.session_root)),
                "started_at": meta["started_at"],
                "after_seq": len(manifest["history"]),
            }
        )
        manifest["state"] = derive_state(manifest)
        write_session(invocation.session_root, manifest)
    completed = aborted = None
    try:
        completed = runner.run(argv, timeout=invocation.timeout or ACTIONS[action].timeout)
    except Aborted as exc:
        aborted = exc.kind
    except OSError:
        aborted = "docker-unavailable"
    finally:
        cleanup = base._cleanup(target, runner, container, gitconfig)
    meta["cleanup"] = cleanup
    try:
        after = base.docker_state(target, runner)
    except LauncherError:
        after = None
    else:
        base.write_file(output, "docker-after.txt", after.text().encode())
    evidence_after = evidence_snapshot(repo)
    base.write_file(output, "evidence-after.json", base.canonical_bytes(evidence_after))
    if completed is None:
        kind = "APPARATUS_ABORT" if aborted != "docker-unavailable" else "LAUNCHER_FAILURE"
        code = EXIT_APPARATUS_ABORT if kind == "APPARATUS_ABORT" else EXIT_LAUNCHER_FAILURE
        return Outcome(kind, code, run_id=run_id, problems=[aborted])
    text, stderr_facts, refusals = sanitize_stderr(completed.stderr, secret_values)
    meta["stderr"] = {
        **stderr_facts,
        "secret_hits": sum(base.secret_hits(text, secret_values).values()),
    }
    base.write_file(output, f"{action}.stderr", text.encode())
    meta["command_exit"] = completed.returncode
    reconciled = (_coherent(manifest, "reconcile") or [{}])[-1].get("decision")
    outcome = validate_output(
        action,
        completed,
        refusals,
        run_id=run_id,
        reconciled=reconciled,
        secret_values=secret_values,
    )
    stdout_hits = base.secret_hits(completed.stdout.decode("utf-8", "replace"), secret_values)
    if outcome.persist_stdout and completed.stdout and not stdout_hits:
        base.write_file(output, f"{action}.json", completed.stdout)
    if refusals:
        meta["refusal"] = refusals[0]
    problems = list(outcome.problems)
    if meta["stderr"]["secret_hits"]:
        problems.append("stderr-secret")
    if not cleanup["container_absent"] or not cleanup["gitconfig_removed"]:
        problems.append("cleanup-failed")
    if after is None:
        problems.append("docker-state-after-unavailable")
    elif (after.names(), after.networks, after.volumes) != (
        before.names(),
        before.networks,
        before.volumes,
    ):
        problems.append("docker-resources-changed")
    created = None
    if action == "start-official" and outcome.classification == "COHERENT":
        created = outcome.run_id
        if created in evidence_before["runs"]:
            problems.append("start:run-id-not-new")
    problems += snapshot_problems(
        action, evidence_before, evidence_after, run_id=run_id, created=created
    )
    if action == "status" and outcome.classification == "COHERENT":
        expected = state["known_phase"]
        if not state["uncertain"] and expected is not None and outcome.phase != expected:
            # La sesión y el run discrepan: se bloquea, nunca se repara.
            problems.append("session-run-mismatch")
            entry["session_blocking"] = True
    if problems and outcome.classification != "LAUNCHER_FAILURE":
        outcome = Outcome(
            "LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE, run_id=outcome.run_id, problems=problems
        )
    else:
        outcome.problems = problems
    return outcome


def _executor_container_state(manifest: Mapping[str, Any], state) -> dict[str, Any]:
    """El contenedor exacto del `execute` de la sesión (por nombre exacto, nunca parcial) no
    puede seguir vivo: el launcher no lo mata; esa limpieza es otra decisión del operador."""
    names = [
        e.get("container_name")
        for e in manifest["history"]
        if e["action"] == "execute-official" and e.get("container_name")
    ]
    names += [
        r["container_name"]
        for r in manifest.get("in_flight", [])
        if r["action"] == "execute-official"
    ]
    if not names:
        raise LauncherError("executor-container-unknown")
    name = names[-1]
    found = [c for c in state.containers if c.name == name]
    current = found[0].state if found else "absent"
    if current in ACTIVE_CONTAINER_STATES:
        raise LauncherError("executor-still-active")
    return {"name": name, "state": current}


def _attached_preflight(invocation: Invocation, manifest: Mapping[str, Any]) -> bytes:
    starts = _coherent(manifest, "start-official")
    if not starts:
        raise LauncherError("preflight-not-attached")
    path = invocation.session_root / starts[-1]["output_dir"] / "preflight.json"
    raw = _private_file(path, "preflight-report-unsafe")
    if hashlib.sha256(raw).hexdigest() != manifest["preflight_report_sha256"]:
        raise SessionTampered()
    return raw


def _cleanup_stack(runner, output, meta, secret_values) -> Outcome:
    """`down --remove-orphans` del proyecto oficial: sin volúmenes, imágenes ni prune."""
    argv = ["docker", "compose", "-p", PROJECT, "--env-file", "/dev/null"]
    for compose_file in COMPOSE_FILES:
        argv += ["-f", compose_file]
    argv += ["down", "--remove-orphans"]
    if {"-v", "--volumes", "--rmi", "prune"} & set(argv) or any(
        value in token for token in argv for value in secret_values
    ):
        raise LauncherError("unsafe-command:cleanup")
    meta["cleanup_command"] = argv[argv.index("down") :]
    try:
        completed = runner.run(argv, timeout=ACTIONS["cleanup-stack"].timeout)
    except Aborted as exc:
        return Outcome("APPARATUS_ABORT", EXIT_APPARATUS_ABORT, problems=[exc.kind])
    text, facts, _ = sanitize_stderr(completed.stderr, secret_values)
    base.write_file(output, "cleanup-stack.stderr", text.encode())
    meta["command_exit"] = completed.returncode
    if completed.returncode != 0:
        return Outcome("LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE, problems=["cleanup-exit"])
    return Outcome("COHERENT", EXIT_OK)


def _record(invocation, manifest, output, meta, entry, outcome: Outcome) -> dict[str, Any]:
    action = invocation.action
    meta.update(
        {
            "classification": outcome.classification,
            "launcher_exit": outcome.exit_code,
            "problems": sorted(set(outcome.problems)),
            "finished_at": base._stamp(_now()),
        }
    )
    base.write_file(output, "launcher-meta.json", base.canonical_bytes(meta))
    base.write_file(
        output,
        "exit-code.txt",
        (
            f"command_exit={meta.get('command_exit', 'none')}\n"
            f"launcher_exit={outcome.exit_code}\nclassification={outcome.classification}\n"
        ).encode(),
    )
    entry.update(
        {
            "finished_at": meta["finished_at"],
            "classification": outcome.classification,
            "launcher_exit": outcome.exit_code,
            "problems": sorted(set(outcome.problems)),
            "output_dir": str(output.relative_to(invocation.session_root)),
            "output_sha256": _output_hashes(output),
            "container_name": meta.get("container_name"),
        }
    )
    relative = entry["output_dir"]
    manifest["in_flight"] = [
        r for r in manifest.get("in_flight", []) if r["output_dir"] != relative
    ]
    if not manifest["in_flight"]:
        del manifest["in_flight"]
    if outcome.classification == "COHERENT":
        entry["phase"], entry["decision"] = outcome.phase, outcome.decision
        if action == "start-official":
            manifest["official_run_id"] = outcome.run_id
            manifest["preflight_report_sha256"] = meta["preflight_report_sha256"]
            entry["run_id"] = outcome.run_id
    history = manifest["history"]
    previous = history[-1]["chain"] if history else manifest["header_sha256"]
    entry["chain"] = _chain(previous, entry)
    manifest["history"].append(entry)
    manifest["state"] = derive_state(manifest)
    write_session(invocation.session_root, manifest)
    return _summary(manifest, action, outcome.classification, outcome.exit_code, output=output)


def _summary(manifest, action, classification, code, *, output) -> dict[str, Any]:
    last = manifest["history"][-1] if manifest["history"] else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "action": action,
        "classification": classification,
        "launcher_exit": code,
        "evidence_mode": EVIDENCE_MODE,
        "session_id": manifest["session_id"],
        "official_run_id": manifest["official_run_id"],
        "expected_git_sha": manifest["expected_git_sha"],
        "state": manifest["state"],
        "problems": last.get("problems", []) if output is not None else [],
        "output_dir": None if output is None else str(output),
    }


# --- CLI -------------------------------------------------------------------------------------


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = base._Parser(prog="s11_ex10_official_launcher", add_help=False, allow_abbrev=False)
    parser.add_argument("action", choices=tuple(ACTIONS))
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--session-root", required=True)
    parser.add_argument("--nonce")
    parser.add_argument("--preflight-report")
    parser.add_argument("--acknowledge-not-demonstrated", action="append", default=[])
    parser.add_argument("--git-safe-directory", action="store_true")
    parser.add_argument("--timeout", type=int)
    for confirmation in sorted({s.confirmation for s in ACTIONS.values() if s.confirmation}):
        parser.add_argument(confirmation, action="store_true")
    args = parser.parse_args(list(argv))
    if args.timeout is not None and not 30 <= args.timeout <= 3600:
        raise UsageError()
    # Cada acción sólo acepta su propia confirmación: nunca la de otra.
    given = {
        s.confirmation
        for s in ACTIONS.values()
        if s.confirmation and getattr(args, s.confirmation[2:].replace("-", "_"))
    }
    own = ACTIONS[args.action].confirmation
    if given - {own}:
        raise UsageError()
    args.confirmed = own in given
    return args


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    native_bootstrap=None,
) -> int:
    environ = os.environ if environ is None else environ
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
        invocation = Invocation(
            action=args.action,
            expected_git_sha=args.expected_git_sha,
            repo_root=base.repository_root(),
            session_root=Path(args.session_root),
            environ=environ,
            confirmed=args.confirmed,
            nonce=args.nonce,
            preflight_report=Path(args.preflight_report) if args.preflight_report else None,
            acknowledged=tuple(args.acknowledge_not_demonstrated),
            safe_directory=args.git_safe_directory,
            timeout=args.timeout,
            native_bootstrap=native_bootstrap,
        )
        validate_invocation(invocation)
    except LauncherError as exc:
        print(f"S11_EX10_OFFICIAL_LAUNCHER_USAGE: {exc.code}", file=sys.stderr)
        return EXIT_USAGE
    for number in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, base._interrupt)
    try:
        summary = execute(invocation)
    except LauncherBusy:
        print("S11_EX10_OFFICIAL_LAUNCHER_BUSY: OFFICIAL_LAUNCHER_BUSY", file=sys.stderr)
        return EXIT_USAGE
    except Aborted as exc:
        print(
            "S11_EX10_OFFICIAL_LAUNCHER_ABORT: "
            + ("timeout" if exc.kind == "timeout" else "interrupted"),
            file=sys.stderr,
        )
        return EXIT_APPARATUS_ABORT
    except (SessionTampered, NativeActionFailure) as exc:
        print(f"S11_EX10_OFFICIAL_LAUNCHER_FAILURE: {exc.code}", file=sys.stderr)
        return EXIT_LAUNCHER_FAILURE
    except LauncherError as exc:
        print(f"S11_EX10_OFFICIAL_LAUNCHER_CONFIG: {exc.code}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("S11_EX10_OFFICIAL_LAUNCHER_ABORT: interrupted", file=sys.stderr)
        return EXIT_APPARATUS_ABORT
    except Exception as exc:  # noqa: BLE001 - nunca un 0 ni una traza con valores
        print(f"S11_EX10_OFFICIAL_LAUNCHER_INTERNAL_ERROR: {type(exc).__name__}", file=sys.stderr)
        return EXIT_LAUNCHER_FAILURE
    sys.stdout.write(base.canonical_bytes(summary).decode("utf-8"))
    return summary["launcher_exit"]


if __name__ == "__main__":
    raise SystemExit(main())
