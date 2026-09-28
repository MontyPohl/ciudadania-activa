#!/usr/bin/env python3
"""Launcher de host del preflight operacional de EX-10 (S11-C3, fase 2B-3). Sólo stdlib.

Ejecuta en un contenedor efímero del servicio `backend` del stack S11 (`docker compose run
--rm --no-deps`) uno de tres comandos, cada uno una invocación:

- `prepare`: `s11_ex10_prepare --confirm-isolated-reset`, mutante. Exige base y Redis
  sanos y `backend`, `worker` y `beat` detenidos (Kombu no tiene dead-letter: un `FLUSHDB`
  con ellos vivos perdería tareas en silencio);
- `inspect`: `s11_ex10_prepare --inspect-only`, sólo lectura;
- `preflight`: `s11_ex10_preflight`, sólo lectura, contra el stack ya preparado y con web y
  worker arriba. `PASS` sólo significa `READY_FOR_EXECUTOR_INTEGRATION`.

No hay modo de carga, ejecución, `run_id`, POST ni finalización, y no se aceptan opciones
desconocidas. Antes de tocar Docker verifica el checkout: raíz exacta, HEAD esperado, árbol
limpio, sin sparse, skip-worktree ni assume-unchanged, historia completa, `.git` propio y
los commits de G0 y G0-A1 en la historia. Git corre sin configuración global ni del sistema
y sin locks opcionales: la verificación no escribe nada en el repositorio.

El checkout completo se monta una sola vez, en sólo lectura, en `/s11-full-source`; nunca
por piezas ni el `/s11-source` parcial del overlay como source-root. Los montajes propios del
overlay (`/app`, las raíces de `var/`, `/s11-source`) siguen ahí: el launcher no los toca.
Los `.env*` no versionados del checkout (`.env`, `.env.supabase.local`, `frontend/.env.local`)
se tapan con `/dev/null` dentro de ese montaje: están ignorados, así que Git no los ve y los
checks del checkout no cambian. El contenedor corre con el uid/gid del operador
(`S11_HOST_UID`/`S11_HOST_GID` del overlay), así que Git no ve *dubious ownership*; el
checkout tiene que pertenecer al operador. Si el contenedor corriera con otro uid,
`--git-safe-directory` monta sólo en ese contenedor, en sólo lectura, un `/etc/gitconfig`
temporal con exactamente `safe.directory = /s11-full-source`. El preflight ejecuta Git con un
entorno mínimo, así que `GIT_CONFIG_COUNT` no le llegaría. El Git del host nunca usa
`safe.directory`: un checkout ajeno podría definir filtros que Git ejecutaría. Nunca
`safe.directory=*` ni cambios en `~/.gitconfig`, en el `/etc/gitconfig` del host, en el
repositorio o en archivos versionados.

El entorno de `docker compose` se construye desde cero: variables de Docker, las tres de
interpolación del overlay (`S11_CUSTODY_HMAC_KEY`, `S11_PUBLICATION_APPROVAL_PUBLIC_KEY`,
`S11_DOCUMENT_HMAC_SECRET`, exportadas por el operador) y el uid/gid. El launcher nunca lee
`.env` (usa `--env-file /dev/null` para la interpolación), aunque Compose sigue inyectando en
el contenedor el `env_file: ../.env` del servicio, igual que en el stack oficial. Nunca se
ejecuta `docker compose config` y nunca se escriben valores.

Salida en un directorio nuevo fuera del repositorio (por defecto
`$HOME/s11-ex10-<modo>-<timestamp>`), modo 0700, con archivos 0600 creados en exclusiva:
`launcher-meta.json`, `<modo>.json` (stdout del comando, sólo si es un JSON canónico,
coherente y sin patrones de secreto), `<modo>.stderr` (saneado: sólo líneas de progreso de
Compose y códigos del comando), `exit-code.txt`, `docker-before.txt` y `docker-after.txt`
(nombre, estado y servicio de los recursos del proyecto). Nada en `var/`.

Códigos de salida: 0 PASS, 1 FAIL, 2 INCOMPLETE (los del comando, sólo si su JSON es
coherente con ellos), 3 uso, configuración o validación previa (nada se lanzó, o el
comando salió con 3), 4 `LAUNCHER_FAILURE` (JSON y exit incoherentes, stdout inválido,
invariante violado, error de Docker o limpieza fallida) y 5 `APPARATUS_ABORT` (timeout o
interrupción). Un 4 o un 5 nunca se convierte en PASS ni en FAIL.
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
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "s11c3-ex10-launcher-v1"
PROJECT = "ciudadania-s11-ex10-preflight"
COMPOSE_FILES = ("infra/docker-compose.yml", "infra/docker-compose.s11.yml")
SERVICE = "backend"
SOURCE_MOUNT = "/s11-full-source"
GITCONFIG_MOUNT = "/etc/gitconfig"
SAFE_DIRECTORY_CONFIG = f"[safe]\n\tdirectory = {SOURCE_MOUNT}\n"
#: Referencias congeladas de G0 y G0-A1 (las mismas que `ex10_preflight`).
G0_DECISION_COMMIT = "1693d0ae562bfe777c496b616c9f8c8d5fa8a591"
G0_A1_DECISION_COMMIT = "db7fe6649213a8d26f4ee60d36a797ee54f80322"
#: Los seis archivos de la fase 2B-2 y los de esta fase: versionados y regulares.
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
#: Variables de interpolación del overlay S11: el operador las exporta; nunca se escriben.
INTERPOLATION_ENV = (
    "S11_CUSTODY_HMAC_KEY",
    "S11_PUBLICATION_APPROVAL_PUBLIC_KEY",
    "S11_DOCUMENT_HMAC_SECRET",
)
DOCKER_ENV = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT")
EX10_SERVICES = frozenset({"backend", "worker", "beat"})
#: Archivos de entorno con secretos: si no están versionados se tapan en el montaje.
_SECRET_FILE = re.compile(r"^\.env(?:$|\.)")
_UNWALKED = frozenset({".git", "node_modules"})
_MAX_METADATA_BYTES = 64 * 1024
#: Estados de contenedor que no pueden publicar nada.
STOPPED_STATES = frozenset({"exited", "dead"})
MAX_STDOUT_BYTES = 4 * 1024 * 1024
DOCKER_CLI_TIMEOUT = 60.0

EXIT_PASS, EXIT_FAIL, EXIT_INCOMPLETE, EXIT_USAGE = 0, 1, 2, 3
EXIT_LAUNCHER_FAILURE, EXIT_APPARATUS_ABORT = 4, 5
_RESULT_EXIT = {"PASS": EXIT_PASS, "FAIL": EXIT_FAIL, "INCOMPLETE": EXIT_INCOMPLETE}
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class ModeSpec:
    command: str
    arguments: tuple[str, ...]
    schema: str
    timeout: float
    #: Sólo `prepare`: base y Redis sanos, y ningún servicio EX-10 vivo.
    requires_stopped_services: bool = False


MODES: Mapping[str, ModeSpec] = {
    "prepare": ModeSpec(
        "s11_ex10_prepare",
        ("--confirm-isolated-reset",),
        "s11c3-ex10-prepare-v1",
        600.0,
        requires_stopped_services=True,
    ),
    "inspect": ModeSpec("s11_ex10_prepare", ("--inspect-only",), "s11c3-ex10-prepare-v1", 300.0),
    "preflight": ModeSpec("s11_ex10_preflight", (), "s11c3-ex10-preflight-v1", 300.0),
}
ALLOWED_COMMANDS = frozenset(spec.command for spec in MODES.values())


@dataclass(frozen=True)
class Target:
    """Proyecto Compose sobre el que actúa el launcher. La CLI sólo conoce `PRODUCTION`."""

    project: str
    compose_files: tuple[str, ...]
    env_file: str = "/dev/null"


PRODUCTION = Target(PROJECT, COMPOSE_FILES)


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


# --- Git del host, sólo lectura ---------------------------------------------------------------


@dataclass(frozen=True)
class RepositoryFacts:
    root: str
    head: str
    owner_uid: int
    git_dir: str
    hidden_index_entries: int
    local_excludes: bool
    required_files: int
    #: Rutas relativas tapadas con `/dev/null`; sólo nombres, nunca contenido.
    masked_files: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "root_realpath": self.root,
            "head": self.head,
            "owner_matches_operator": self.owner_uid == os.getuid(),
            "git_dir": self.git_dir,
            "shallow": False,
            "sparse": False,
            "hidden_index_entries": self.hidden_index_entries,
            "local_excludes": self.local_excludes,
            "clean": True,
            "g0_in_history": True,
            "g0_a1_in_history": True,
            "required_files_present": self.required_files,
            "masked_untracked_env_files": list(self.masked_files),
        }


class HostGit:
    """Git sobre el checkout del host sin escribir nada: sin configuración global ni del
    sistema, sin fsmonitor, sin locks opcionales, sin subir por encima de la raíz y sin
    `safe.directory` (sólo checkouts del propio operador)."""

    def __init__(self, root: Path):
        self.root = root

    def __call__(self, *args: str, ok: tuple[int, ...] = (0,)) -> tuple[int, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": "/nonexistent",
            "LC_ALL": "C",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CEILING_DIRECTORIES": os.path.dirname(str(self.root)),
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
        config = [
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            "core.excludesFile=/dev/null",
        ]
        try:
            completed = subprocess.run(
                ("git", "--no-optional-locks", *config, "-C", str(self.root), *args),
                capture_output=True,
                check=False,
                env=env,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            raise LauncherError("git-unavailable") from None
        if completed.returncode not in ok:
            raise LauncherError("git-command-failed")
        return completed.returncode, completed.stdout


def _bounded(path: Path, limit: int = _MAX_METADATA_BYTES) -> bytes | None:
    try:
        with path.open("rb") as handle:
            return handle.read(limit + 1)
    except OSError:
        return None


def verify_repository(root: Path, expected_git_sha: str) -> RepositoryFacts:
    """Todas las comprobaciones del checkout antes de Docker. Nada se corrige."""
    if not _SHA1.fullmatch(expected_git_sha):
        raise LauncherError("expected-git-sha-format")
    text = str(root)
    if not root.is_absolute() or os.path.realpath(text) != text:
        raise LauncherError("repo-root-not-realpath")
    if any(char in text for char in ":,\n"):
        raise LauncherError("repo-root-unmountable")
    entry = root / ".git"
    if not os.path.lexists(entry):
        raise LauncherError("source-root-not-git")
    if entry.is_symlink() or not entry.is_dir():
        # Un gitfile apunta fuera de la raíz montada: el contenedor no podría resolverlo.
        raw = _bounded(entry) if entry.is_file() and not entry.is_symlink() else None
        target = (raw or b"").decode("utf-8", "replace").removeprefix("gitdir:").strip()
        broken = not raw or not raw.startswith(b"gitdir:") or not (root / target).is_dir()
        raise LauncherError("broken-gitfile" if broken else "gitfile-not-mountable")
    owner = os.stat(root).st_uid
    if owner != os.getuid() or os.stat(entry).st_uid != os.getuid():
        # Git ejecutaría configuración de un checkout ajeno como el operador.
        raise LauncherError("checkout-owner-mismatch")
    git = HostGit(root)
    lines = git(
        "rev-parse",
        "--is-inside-work-tree",
        "--is-bare-repository",
        "--is-shallow-repository",
        "--show-toplevel",
    )[1].splitlines()
    if len(lines) != 4:
        raise LauncherError("git-command-failed")
    inside, bare, shallow, toplevel = lines
    if inside != "true" or bare != "false" or os.path.realpath(toplevel) != text:
        raise LauncherError("repo-root-not-toplevel")
    head = git("rev-parse", "--verify", "HEAD^{commit}")[1].strip()
    if head != expected_git_sha:
        raise LauncherError("head-mismatch")
    if shallow != "false" or os.path.lexists(entry / "shallow"):
        raise LauncherError("shallow-history")
    if git("config", "--bool", "--get", "core.sparseCheckout", ok=(0, 1))[1].strip() == "true":
        raise LauncherError("sparse-checkout")
    entries = [item for item in git("ls-files", "-v", "-z")[1].split("\0") if item]
    hidden = sum(1 for item in entries if item[0] == "S" or item[0].islower())
    if hidden:
        raise LauncherError("hidden-index-entries")
    exclude = root / git("rev-parse", "--git-path", "info/exclude")[1].strip()
    raw_exclude = _bounded(exclude) if os.path.lexists(exclude) else b""
    # La misma regla que el preflight: ilegible, demasiado grande o con un patrón activo.
    local_excludes = (
        raw_exclude is None
        or len(raw_exclude) > _MAX_METADATA_BYTES
        or any(
            line.strip() and not line.lstrip().startswith(b"#") for line in raw_exclude.splitlines()
        )
    )
    if local_excludes:
        raise LauncherError("local-excludes")
    status = git(
        "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"
    )[1]
    if any(item[:2].strip() for item in status.split("\0") if item):
        raise LauncherError("working-tree-dirty")
    for sha, code in (
        (G0_DECISION_COMMIT, "g0-not-in-history"),
        (G0_A1_DECISION_COMMIT, "g0-a1-not-in-history"),
    ):
        if git("cat-file", "-e", f"{sha}^{{commit}}", ok=(0, 1, 128))[0] != 0:
            raise LauncherError(code)
        if git("merge-base", "--is-ancestor", sha, "HEAD", ok=(0, 1))[0] != 0:
            raise LauncherError(code)
    tracked = {item[2:] for item in entries}
    for relative in REQUIRED_FILES:
        path = root / relative
        if relative not in tracked or path.is_symlink() or not path.is_file():
            raise LauncherError("required-file-missing")
    return RepositoryFacts(
        text,
        head,
        owner,
        "directory",
        hidden,
        local_excludes,
        len(REQUIRED_FILES),
        secret_masks(root, tracked),
    )


def secret_masks(root: Path, tracked: set[str]) -> tuple[str, ...]:
    """`.env*` no versionados del checkout (fuera de `.git` y `node_modules`), por ruta."""
    masks = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in _UNWALKED)
        for name in filenames:
            path = Path(directory) / name
            relative = str(path.relative_to(root))
            if not _SECRET_FILE.match(name) or relative in tracked:
                continue
            if path.is_symlink() or not path.is_file() or any(c in relative for c in ":,\n"):
                raise LauncherError("secret-file-unmaskable")
            masks.append(relative)
    return tuple(sorted(masks))


# --- Entorno y Docker --------------------------------------------------------------------------


def child_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """Entorno mínimo de `docker compose`. Los valores nunca salen de este diccionario."""
    env = {name: environ[name] for name in DOCKER_ENV if environ.get(name)}
    env.setdefault("PATH", "/usr/bin:/bin")
    for name in INTERPOLATION_ENV:
        if not environ.get(name, "").strip():
            raise LauncherError(f"missing-env:{name}")
        env[name] = environ[name]
    for name, value in (("S11_HOST_UID", os.getuid()), ("S11_HOST_GID", os.getgid())):
        if environ.get(name, str(value)) != str(value):
            raise LauncherError(f"{name.lower().replace('_', '-')}-mismatch")
        env[name] = str(value)
    return env


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: bytes
    stderr: bytes


class Aborted(Exception):
    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(kind)


class DockerRunner:
    """Subprocesos de Docker: stdin cerrado, salida capturada, plazo fijo y grupo propio.

    En un timeout o una interrupción se señala al grupo entero: un hijo que conservara la
    tubería no puede dejar colgada la espera. El contenedor, si quedó, lo quita la limpieza.
    """

    def __init__(self, cwd: Path, env: Mapping[str, str]):
        self.cwd = cwd
        self.env = dict(env)

    def run(self, argv: Sequence[str], *, timeout: float = DOCKER_CLI_TIMEOUT) -> Completed:
        process = subprocess.Popen(
            list(argv),
            cwd=self.cwd,
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._stop(process, signal.SIGKILL, grace=5)
            raise Aborted("timeout") from None
        except KeyboardInterrupt:
            self._stop(process, signal.SIGINT, grace=30)
            raise Aborted("interrupted") from None
        return Completed(process.returncode, stdout, stderr)

    @staticmethod
    def _stop(process: subprocess.Popen, first: int, *, grace: float) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, first)
        try:
            process.communicate(timeout=grace)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.communicate(timeout=10)


@dataclass(frozen=True)
class Container:
    name: str
    state: str
    service: str
    oneoff: bool
    healthy: bool


@dataclass(frozen=True)
class DockerState:
    containers: tuple[Container, ...]
    networks: tuple[str, ...]
    volumes: tuple[str, ...]

    def text(self) -> str:
        lines = [
            f"container\t{c.name}\t{c.state}\t{c.service}\toneoff={c.oneoff}\thealthy={c.healthy}"
            for c in self.containers
        ]
        lines += [f"network\t{name}" for name in self.networks]
        lines += [f"volume\t{name}" for name in self.volumes]
        return "\n".join(lines) + "\n"

    def names(self) -> set[str]:
        return {c.name for c in self.containers}


def _lines(completed: Completed, code: str) -> list[str]:
    if completed.returncode != 0:
        raise LauncherError(code)
    return [line for line in completed.stdout.decode("utf-8", "replace").splitlines() if line]


def docker_state(target: Target, runner: DockerRunner) -> DockerState:
    """Sólo nombre, estado, servicio y salud: nunca `inspect`, entorno ni configuración."""
    label = f"label=com.docker.compose.project={target.project}"
    template = (
        '{{.Names}}\t{{.State}}\t{{.Label "com.docker.compose.service"}}'
        '\t{{.Label "com.docker.compose.oneoff"}}'
    )
    rows = _lines(
        runner.run(["docker", "ps", "-a", "--no-trunc", "--filter", label, "--format", template]),
        "docker-state-unavailable",
    )
    healthy = set(
        _lines(
            runner.run(
                [
                    "docker",
                    "ps",
                    "--filter",
                    label,
                    "--filter",
                    "health=healthy",
                    "--format",
                    "{{.Names}}",
                ]
            ),
            "docker-state-unavailable",
        )
    )
    containers = []
    for row in rows:
        parts = row.split("\t")
        if len(parts) != 4 or not _NAME.fullmatch(parts[0]):
            raise LauncherError("docker-state-unparseable")
        name, state, service, oneoff = parts
        containers.append(Container(name, state, service, oneoff == "True", name in healthy))
    networks = _lines(
        runner.run(["docker", "network", "ls", "--filter", label, "--format", "{{.Name}}"]),
        "docker-state-unavailable",
    )
    volumes = _lines(
        runner.run(["docker", "volume", "ls", "--filter", label, "--format", "{{.Name}}"]),
        "docker-state-unavailable",
    )
    return DockerState(
        tuple(sorted(containers, key=lambda c: c.name)),
        tuple(sorted(networks)),
        tuple(sorted(volumes)),
    )


def verify_docker(target: Target, runner: DockerRunner) -> dict[str, str]:
    server = _lines(
        runner.run(["docker", "version", "--format", "{{.Server.Version}}"]), "docker-unavailable"
    )
    compose = _lines(runner.run(["docker", "compose", "version", "--short"]), "compose-unavailable")
    image = _lines(
        runner.run(["docker", "image", "ls", "--format", "{{.ID}}", f"{target.project}-{SERVICE}"]),
        "docker-state-unavailable",
    )
    if not image:
        # `run` construiría la imagen por su cuenta: el operador la construye antes, a la vista.
        raise LauncherError("image-missing")
    return {"server": server[0] if server else "", "compose": compose[0] if compose else ""}


def check_preconditions(spec: ModeSpec, state: DockerState) -> dict[str, Any]:
    active = sorted(
        c.name
        for c in state.containers
        if c.service in EX10_SERVICES and c.state not in STOPPED_STATES
    )
    infra = {
        service: [c for c in state.containers if c.service == service and not c.oneoff]
        for service in ("db", "redis")
    }
    infra_ok = all(
        len(items) == 1 and items[0].state == "running" and items[0].healthy
        for items in infra.values()
    )
    running = {
        service: sum(
            1
            for c in state.containers
            if c.service == service and not c.oneoff and c.state == "running"
        )
        for service in ("backend", "worker")
    }
    facts = {
        "ex10_services_active": len(active),
        "db_redis_healthy": infra_ok,
        "backend_running": running["backend"],
        "worker_running": running["worker"],
    }
    if spec.requires_stopped_services:
        if active:
            raise LauncherError("ex10-services-active")
        if not infra_ok:
            raise LauncherError("db-redis-not-healthy")
    return facts


def build_command(
    target: Target,
    mode: str,
    *,
    expected_git_sha: str,
    container_name: str,
    repo_root: Path,
    gitconfig: Path | None = None,
    masks: tuple[str, ...] = (),
) -> list[str]:
    spec = MODES[mode]
    argv = ["docker", "compose", "-p", target.project, "--env-file", target.env_file]
    for compose_file in target.compose_files:
        argv += ["-f", compose_file]
    argv += ["run", "--rm", "--no-deps", "-T", "--pull", "never", "--name", container_name]
    # Un único montaje del checkout completo, en sólo lectura.
    argv += ["-v", f"{repo_root}:{SOURCE_MOUNT}:ro"]
    for relative in masks:
        argv += ["-v", f"/dev/null:{SOURCE_MOUNT}/{relative}:ro"]
    if gitconfig is not None:
        argv += ["-v", f"{gitconfig}:{GITCONFIG_MOUNT}:ro"]
    argv += [SERVICE, "python", "manage.py", spec.command, *spec.arguments]
    argv += ["--expected-git-sha", expected_git_sha, "--source-root", SOURCE_MOUNT]
    assert_safe_command(argv, repo_root=repo_root, mode=mode, masks=masks)
    return argv


#: Opciones de `compose run` que el launcher nunca emite.
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
#: Subcomandos o banderas de Compose que nunca aparecen como token del comando.
_FORBIDDEN_TOKENS = frozenset(
    {"config", "down", "up", "rm", "stop", "kill", "restart", "exec", "build", "prune", "--rmi"}
)
#: Nada de carga, ejecución ni `run_id` en el comando del contenedor.
_FORBIDDEN_WORDS = ("post", "run_id", "run-id", "execute", "finalize", "load", "count")


def assert_safe_command(
    argv: Sequence[str], *, repo_root: Path, mode: str, masks: tuple[str, ...] = ()
) -> None:
    """Invariantes del único comando que se lanza; una violación es un error del launcher."""
    argv = list(argv)
    try:
        run = argv.index("run")
        service = argv.index(SERVICE, run)
    except ValueError:
        raise LauncherError("unsafe-command:shape") from None
    problems = []
    options = argv[run + 1 : service]
    mounts = [options[i + 1] for i, token in enumerate(options[:-1]) if token == "-v"]
    if argv[:2] != ["docker", "compose"] or set(argv) & _FORBIDDEN_TOKENS:
        problems.append("not-compose-run")
    if mounts[:1] != [f"{repo_root}:{SOURCE_MOUNT}:ro"]:
        problems.append("checkout-mount")
    allowed_masks = {f"/dev/null:{SOURCE_MOUNT}/{relative}:ro" for relative in masks}
    gitconfigs = 0
    for mount in mounts[1:]:
        if mount in allowed_masks:
            continue
        source, _, rest = mount.partition(":")
        gitconfigs += 1
        if rest != f"{GITCONFIG_MOUNT}:ro" or _inside(os.path.realpath(source), str(repo_root)):
            problems.append("extra-mount")
    if gitconfigs > 1:
        problems.append("extra-mount")
    if any(not mount.endswith(":ro") for mount in mounts):
        problems.append("rw-mount")
    # Por directorio, no por prefijo de texto: `/x/s11-out` no está dentro de `/x/s`.
    sources = [mount.partition(":")[0] for mount in mounts]
    inside = [token for token in argv if token not in mounts and _inside(token, str(repo_root))]
    if sum(1 for source in sources if _inside(source, str(repo_root))) != 1 or inside:
        problems.append("checkout-mounted-more-than-once")
    required = {"--rm", "--no-deps", "-T", "--pull"}
    pull = options[options.index("--pull") + 1] if "--pull" in options[:-1] else None
    if set(options) & _FORBIDDEN_RUN_OPTIONS or not required <= set(options) or pull != "never":
        problems.append("run-options")
    spec = MODES.get(mode)
    tail = argv[service + 1 :]
    expected_tail = None
    if spec is not None and len(tail) >= 4:
        expected_tail = [
            "python",
            "manage.py",
            spec.command,
            *spec.arguments,
            "--expected-git-sha",
            tail[-3],
            "--source-root",
            SOURCE_MOUNT,
        ]
    if (
        tail != expected_tail
        or not _SHA1.fullmatch(tail[-3])
        or tail[2] not in ALLOWED_COMMANDS
        or any(word in token.lower() for token in tail for word in _FORBIDDEN_WORDS)
    ):
        problems.append("container-command")
    if problems:
        raise LauncherError("unsafe-command:" + ",".join(sorted(set(problems))))


# --- Salida del comando ------------------------------------------------------------------------


_COMPOSE_LINE = re.compile(
    r"^ ?(?:Container|Network|Volume|Image) [A-Za-z0-9][A-Za-z0-9_.-]{0,200} "
    r"(?:Creating|Created|Starting|Started|Stopping|Stopped|Removing|Removed|Waiting|Healthy"
    r"|Running|Recreate|Recreated|Exited)\s*$"
)
_CODE_LINE = re.compile(
    r"^(?:CommandError: )?S11_EX10_(?:PREFLIGHT|PREPARE)_(?:USAGE|CONFIG|INTERNAL_ERROR): "
    r"[A-Za-z0-9_.:-]{1,80}$"
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


def sanitize_stderr(raw: bytes, secret_values: Sequence[str] = ()) -> tuple[str, dict[str, int]]:
    """Sólo sobreviven las líneas de progreso de Compose y los códigos del comando, y nunca
    una línea con un patrón de secreto o uno de los valores pasados al contenedor."""
    kept, redacted, secret_lines = [], 0, 0
    lines = [line for line in raw.decode("utf-8", "replace").splitlines() if line.strip()]
    for line in lines:
        if not (_COMPOSE_LINE.fullmatch(line) or _CODE_LINE.fullmatch(line)):
            redacted += 1
            kept.append("[redacted-line]")
        elif secret_hits(line, secret_values):
            secret_lines += 1
            kept.append("[redacted-secret-line]")
        else:
            kept.append(line)
    text = "\n".join(kept) + ("\n" if kept else "")
    facts = {"lines": len(lines), "kept": len(lines) - redacted - secret_lines}
    return text, {**facts, "redacted": redacted, "redacted_secret_lines": secret_lines}


def secret_hits(text: str, values: Sequence[str] = ()) -> dict[str, int]:
    """Cantidades por patrón, nunca las coincidencias. `values` son los secretos pasados."""
    hits = {name: len(pattern.findall(text)) for name, pattern in SECRET_PATTERNS}
    hits["exact-secret-value"] = sum(text.count(value) for value in values if len(value) >= 8)
    return {name: count for name, count in hits.items() if count}


def canonical_bytes(value: Any) -> bytes:
    """El mismo JSON canónico que `canonical_json_bytes` del backend."""
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


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
PREPARE_KEYS = frozenset(
    {
        "schema_version",
        "mode",
        "scenario_id",
        "protocol_version",
        "methodology_version",
        "dataset_id",
        "dataset_sha256",
        "result",
        "state",
        "detail_code",
        "expected_git_sha",
        "git_sha",
        "database_reset",
        "catalogs_seeded",
        "object_root_reset",
        "redis_reset",
        "cache_reset",
        "fixture_result",
        "actors",
        "ready_state",
        "services",
        "guards",
        "reset",
        "final_checks",
        "run_id",
        "evidence_written",
        "report_posts",
        "provider_calls",
        "task_publishes",
        "checked_at",
    }
)
_GATES = {"PASS": "READY_FOR_EXECUTOR_INTEGRATION", "FAIL": "NOT_READY", "INCOMPLETE": "INCOMPLETE"}
_STATES = {"PASS": "READY_STATE", "FAIL": "NOT_READY", "INCOMPLETE": "INCOMPLETE"}
_CHECK_KEYS = frozenset({"check", "result", "expected", "observed_sanitized", "detail_code"})


@dataclass
class Outcome:
    classification: str
    exit_code: int
    result: str | None = None
    status: str | None = None
    problems: list[str] = field(default_factory=list)
    payload: dict[str, Any] | None = None
    persist_stdout: bool = False


def _invariant_problems(payload: dict[str, Any]) -> list[str]:
    expected = {
        "run_id": None,
        "evidence_written": False,
        "report_posts": 0,
        "provider_calls": 0,
        "task_publishes": 0,
    }
    problems = [f"invariant:{key}" for key, value in expected.items() if payload.get(key) != value]
    if str(payload.get("schema_version")).startswith("s11c3-ex10-preflight"):
        if payload.get("ready_for_execution") is not False:
            problems.append("invariant:ready_for_execution")
    return problems


def _checks_problems(checks: Any) -> tuple[list[str], set[str]]:
    if not isinstance(checks, list) or not checks:
        return ["checks-shape"], set()
    results = set()
    for check in checks:
        if not isinstance(check, dict) or set(check) != _CHECK_KEYS:
            return ["checks-shape"], results
        results.add(check["result"])
    unknown = results - {"PASS", "FAIL", "INCOMPLETE", "NOT_RUN"}
    return (["checks-result-value"] if unknown else []), results


def validate_output(
    mode: str, completed: Completed, *, expected_git_sha: str, secret_values: Sequence[str]
) -> Outcome:
    """Coherencia entre el exit y el JSON. Una discrepancia nunca se convierte en PASS."""
    code, raw = completed.returncode, completed.stdout
    if code == EXIT_USAGE:
        problems = [] if not raw else ["stdout-on-usage-error"]
        if problems:
            return Outcome("LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE, problems=problems)
        return Outcome("COMMAND_USAGE_OR_CONFIG_ERROR", EXIT_USAGE)
    if code not in _RESULT_EXIT.values():
        return Outcome("LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE, problems=[f"exit-code:{code}"])
    if len(raw) > MAX_STDOUT_BYTES:
        return Outcome("LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE, problems=["stdout-too-large"])
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text, parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError):
        return Outcome("LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE, problems=["stdout-not-json"])
    try:
        return _validated(mode, code, raw, text, payload, expected_git_sha, secret_values)
    except (AttributeError, KeyError, TypeError, ValueError):
        # JSON válido con tipos inesperados: nunca una excepción sin registro.
        return Outcome("LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE, problems=["stdout-malformed"])


def _reject_constant(value: str):
    raise ValueError("NaN e Infinity no son JSON canónico")


def _validated(mode, code, raw, text, payload, expected_git_sha, secret_values) -> Outcome:
    problems: list[str] = []
    hits = secret_hits(text, secret_values)
    if hits:
        # Nunca se persiste: sólo cuántas coincidencias y de qué clase.
        problems += [f"stdout-secret:{name}" for name in sorted(hits)]
    if not isinstance(payload, dict):
        return Outcome("LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE, problems=["stdout-not-object"])
    if text.count("\n") != 1 or canonical_bytes(payload) != raw:
        problems.append("stdout-not-canonical")
    spec = MODES[mode]
    if payload.get("schema_version") != spec.schema:
        problems.append("schema-version")
    keys = PREFLIGHT_KEYS if mode == "preflight" else PREPARE_KEYS
    if set(payload) != keys:
        problems.append("payload-keys")
    identity = {"scenario_id": "EX-10", "protocol_version": "s11-ex10-v1"}
    identity["methodology_version"] = "S11A-v1.1"
    problems += [f"identity:{k}" for k, v in identity.items() if payload.get(k) != v]
    problems += _invariant_problems(payload)
    if payload.get("expected_git_sha") != expected_git_sha:
        problems.append("expected-git-sha")
    result = payload.get("result")
    if result not in _RESULT_EXIT:
        problems.append("result-value")
    elif _RESULT_EXIT[result] != code:
        problems.append("exit-result-mismatch")
    if mode == "preflight":
        status = payload.get("gate")
        if result in _GATES and status != _GATES[result]:
            problems.append("gate-mismatch")
        check_problems, results = _checks_problems(payload.get("checks"))
        problems += check_problems
        if result == "PASS":
            if results != {"PASS"}:
                # Un NOT_RUN, FAIL o INCOMPLETE bajo un PASS global es un bug, no un PASS.
                problems.append("pass-with-non-pass-checks")
            if (payload.get("health_requests"), payload.get("control_probes")) != (1, 2):
                problems.append("pass-without-exact-probes")
            if payload.get("git_sha") != expected_git_sha:
                problems.append("pass-git-sha")
        if not (0 <= _int(payload.get("health_requests")) <= 1):
            problems.append("health-requests")
        if not (0 <= _int(payload.get("control_probes")) <= 2):
            problems.append("control-probes")
    else:
        status = payload.get("state")
        if result in _STATES and status != _STATES[result]:
            problems.append("state-mismatch")
        if payload.get("mode") != ("PREPARE" if mode == "prepare" else "INSPECT"):
            problems.append("mode-mismatch")
        if result == "PASS":
            problems += _prepare_pass_problems(mode, payload)
    if problems:
        return Outcome(
            "LAUNCHER_FAILURE",
            EXIT_LAUNCHER_FAILURE,
            result=result if isinstance(result, str) else None,
            problems=problems,
            persist_stdout=not hits and "stdout-not-canonical" not in problems,
        )
    return Outcome("COHERENT", code, result, status, payload=payload, persist_stdout=True)


def _int(value: Any) -> int:
    return value if type(value) is int else -1


def _all_pass(checks: Any) -> bool:
    shape_problems, results = _checks_problems(checks)
    return not shape_problems and results == {"PASS"}


def _prepare_pass_problems(mode: str, payload: dict[str, Any]) -> list[str]:
    problems = []
    ready = payload.get("ready_state")
    if not isinstance(ready, dict) or ready.get("overall") != "PASS":
        problems.append("pass-without-ready-state")
    if not _all_pass(payload.get("guards")):
        problems.append("pass-with-non-pass-guards")
    if not _all_pass(payload.get("final_checks")):
        problems.append("pass-with-non-pass-final-checks")
    if payload.get("git_sha") != payload.get("expected_git_sha"):
        problems.append("pass-git-sha")
    if mode == "inspect":
        flags = [payload.get(name) for name in ("database_reset", "catalogs_seeded")]
        flags += [payload.get(name) for name in ("object_root_reset", "redis_reset", "cache_reset")]
        if flags != [False] * 5 or payload.get("reset") is not None:
            problems.append("inspect-with-reset")
        if payload.get("fixture_result") is not None or payload.get("actors") is not None:
            problems.append("inspect-with-fixture")
    if mode == "prepare":
        reset = payload.get("reset")
        if not isinstance(reset, dict) or reset.get("result") != "PASS":
            problems.append("pass-without-reset")
        flags = [payload.get(name) for name in ("database_reset", "catalogs_seeded")]
        flags += [payload.get(name) for name in ("object_root_reset", "redis_reset", "cache_reset")]
        fixture = payload.get("fixture_result") or {}
        actors = payload.get("actors")
        if flags != [True] * 5:
            problems.append("pass-without-complete-reset")
        if fixture.get("outcome") != "PREPARED" or fixture.get("count") != 10:
            problems.append("pass-without-fixture")
        if not isinstance(actors, list) or len(actors) != 10:
            problems.append("pass-without-actors")
    return problems


# --- Archivos de salida ------------------------------------------------------------------------


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def prepare_output_dir(path: Path, repo_root: Path) -> Path:
    """Directorio nuevo, 0700, fuera del repositorio. Nunca se reutiliza."""
    if not path.is_absolute():
        raise LauncherError("output-dir-not-absolute")
    parent = path.parent
    real_parent = os.path.realpath(parent)
    if not parent.is_dir() or os.path.lexists(path):
        raise LauncherError("output-dir-unavailable")
    if _inside(real_parent, str(repo_root)) or _inside(
        str(repo_root), os.path.join(real_parent, path.name)
    ):
        raise LauncherError("output-dir-inside-repository")
    info = os.stat(real_parent)
    if info.st_uid != os.getuid() or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise LauncherError("output-dir-parent-unsafe")
    target = Path(real_parent) / path.name
    try:
        os.mkdir(target, 0o700)
    except OSError:
        raise LauncherError("output-dir-unavailable") from None
    os.chmod(target, 0o700)
    if os.lstat(target).st_uid != os.getuid() or stat.S_ISLNK(os.lstat(target).st_mode):
        raise LauncherError("output-dir-unsafe")
    return target


def write_file(directory: Path, name: str, content: bytes, mode: int = 0o600) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(directory / name, flags, mode)
    try:
        os.fchmod(fd, mode)
        view = memoryview(content)
        while view:
            view = view[os.write(fd, view) :]
    finally:
        os.close(fd)


@contextlib.contextmanager
def exclusive(repo_root: Path) -> Iterator[None]:
    """Un launcher a la vez por checkout: `flock` sobre el propio directorio, sin archivos."""
    fd = os.open(repo_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LauncherBusy() from None
        yield
    finally:
        os.close(fd)


# --- Orquestación ------------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class Invocation:
    mode: str
    expected_git_sha: str
    repo_root: Path
    output_dir: Path
    environ: Mapping[str, str]
    target: Target = PRODUCTION
    safe_directory: bool = False
    timeout: float | None = None
    runner_factory: Callable[[Path, Mapping[str, str]], DockerRunner] = DockerRunner
    clock: Callable[[], datetime] = _now
    token: Callable[[], str] = lambda: secrets.token_hex(4)


def execute(invocation: Invocation) -> dict[str, Any]:
    """Una invocación completa. Devuelve el resumen; los archivos quedan en `output_dir`."""
    spec = MODES[invocation.mode]
    started = invocation.clock()
    meta: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": invocation.mode,
        "project": invocation.target.project,
        "compose_files": list(invocation.target.compose_files),
        "service": SERVICE,
        "container_command": [spec.command, *spec.arguments],
        "expected_git_sha": invocation.expected_git_sha,
        "source_root_container": SOURCE_MOUNT,
        "source_mount_mode": "ro",
        "full_checkout_mounts": 1,
        "safe_directory": SOURCE_MOUNT if invocation.safe_directory else None,
        "container_user": {"uid": os.getuid(), "gid": os.getgid()},
        "started_at": _stamp(started),
        "run_id": None,
        "evidence_written": False,
    }
    with exclusive(invocation.repo_root):
        output = prepare_output_dir(invocation.output_dir, invocation.repo_root)
        meta["output_dir"] = str(output)
        try:
            summary = _execute_locked(invocation, spec, output, meta)
        except LauncherError as exc:
            summary = _finish(output, meta, "VALIDATION_FAILED", EXIT_USAGE, problems=[exc.code])
        return summary


def _execute_locked(
    invocation: Invocation, spec: ModeSpec, output: Path, meta: dict[str, Any]
) -> dict[str, Any]:
    facts = verify_repository(invocation.repo_root, invocation.expected_git_sha)
    meta["repository"] = facts.to_json()
    env = child_environment(invocation.environ)
    meta["child_env_names"] = sorted(env)
    runner = invocation.runner_factory(invocation.repo_root, env)
    meta["docker"] = verify_docker(invocation.target, runner)
    before = docker_state(invocation.target, runner)
    write_file(output, "docker-before.txt", before.text().encode())
    meta["preconditions"] = check_preconditions(spec, before)
    container = f"{invocation.target.project}-launcher-{invocation.mode}-{invocation.token()}"
    if container in before.names() or not _NAME.fullmatch(container):
        raise LauncherError("container-name-unavailable")
    gitconfig = None
    if invocation.safe_directory:
        gitconfig = output / "safe-directory.gitconfig"
        # 0644: lo lee el usuario del contenedor, que podría no ser el operador.
        write_file(output, gitconfig.name, SAFE_DIRECTORY_CONFIG.encode(), 0o644)
    argv = build_command(
        invocation.target,
        invocation.mode,
        expected_git_sha=invocation.expected_git_sha,
        container_name=container,
        repo_root=invocation.repo_root,
        gitconfig=gitconfig,
        masks=facts.masked_files,
    )
    meta["container_name"] = container
    secret_values = [env[name] for name in INTERPOLATION_ENV]
    completed = aborted = None
    started = invocation.clock()
    try:
        completed = runner.run(argv, timeout=invocation.timeout or spec.timeout)
    except Aborted as exc:
        aborted = exc.kind
    except OSError:
        aborted = "docker-unavailable"
    finally:
        # Siempre, también ante una excepción inesperada: sólo lo que creó esta invocación.
        cleanup = _cleanup(invocation.target, runner, container, gitconfig)
    meta["command_duration_ms"] = int((invocation.clock() - started).total_seconds() * 1000)
    meta["cleanup"] = cleanup
    try:
        after = docker_state(invocation.target, runner)
    except LauncherError:
        after = None
        meta["docker_state_after"] = "unavailable"
    else:
        write_file(output, "docker-after.txt", after.text().encode())
    meta["docker_after_matches_before"] = after is not None and (
        after.names(),
        after.networks,
        after.volumes,
    ) == (before.names(), before.networks, before.volumes)
    if completed is None:
        classification = (
            "APPARATUS_ABORT" if aborted != "docker-unavailable" else "LAUNCHER_FAILURE"
        )
        code = (
            EXIT_APPARATUS_ABORT if classification == "APPARATUS_ABORT" else EXIT_LAUNCHER_FAILURE
        )
        return _finish(output, meta, classification, code, problems=[aborted], command_exit=None)
    stderr_text, stderr_facts = sanitize_stderr(completed.stderr, secret_values)
    stderr_hits = secret_hits(stderr_text, secret_values)
    # El stderr crudo nunca se escribe; sólo se cuenta si llevaba un valor exacto de secreto.
    raw_exact = secret_hits(completed.stderr.decode("utf-8", "replace"), secret_values).get(
        "exact-secret-value", 0
    )
    meta["stderr"] = {
        **stderr_facts,
        "secret_hits": sum(stderr_hits.values()),
        "raw_exact_secret_values": raw_exact,
    }
    write_file(output, f"{invocation.mode}.stderr", stderr_text.encode())
    outcome = validate_output(
        invocation.mode,
        completed,
        expected_git_sha=invocation.expected_git_sha,
        secret_values=secret_values,
    )
    stdout_hits = secret_hits(completed.stdout.decode("utf-8", "replace"), secret_values)
    meta["stdout"] = {
        "bytes": len(completed.stdout),
        "sha256": hashlib.sha256(completed.stdout).hexdigest(),
        "secret_hits": sum(stdout_hits.values()),
        "persisted": bool(outcome.persist_stdout and completed.stdout and not stdout_hits),
    }
    if meta["stdout"]["persisted"]:
        write_file(output, f"{invocation.mode}.json", completed.stdout)
    problems = list(outcome.problems)
    classification, code = outcome.classification, outcome.exit_code
    if stderr_hits:
        problems.append("stderr-secret-pattern")
    if raw_exact:
        problems.append("stderr-raw-secret-value")
    if not cleanup["container_absent"] or not cleanup["gitconfig_removed"]:
        problems.append("cleanup-failed")
    if after is None:
        problems.append("docker-state-after-unavailable")
    elif not meta["docker_after_matches_before"]:
        problems.append("docker-resources-changed")
    if problems and classification in {"COHERENT", "COMMAND_USAGE_OR_CONFIG_ERROR"}:
        classification, code = "LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE
    # Lo que el comando afirmó sólo cuenta si es coherente; si no, queda como afirmación.
    coherent = classification == "COHERENT"
    meta["claimed_result"] = outcome.result
    return _finish(
        output,
        meta,
        classification,
        code,
        problems=problems,
        command_exit=completed.returncode,
        result=outcome.result if coherent else None,
        status=outcome.status if coherent else None,
    )


def _cleanup(target: Target, runner: DockerRunner, container: str, gitconfig: Path | None):
    """Sólo lo que creó esta invocación: su contenedor, por nombre exacto, y el gitconfig."""
    facts = {"container_present_after_run": False, "container_removed": False}
    try:
        present = container in docker_state(target, runner).names()
        facts["container_present_after_run"] = present
        if present:
            removed = runner.run(["docker", "rm", "-f", container])
            facts["container_removed"] = removed.returncode == 0
        facts["container_absent"] = container not in docker_state(target, runner).names()
    except (LauncherError, Aborted, OSError):
        facts["container_absent"] = False
    if gitconfig is not None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(gitconfig)
    facts["gitconfig_removed"] = gitconfig is None or not os.path.lexists(gitconfig)
    return facts


def _finish(
    output: Path,
    meta: dict[str, Any],
    classification: str,
    code: int,
    *,
    problems: Sequence[str] = (),
    command_exit: int | None = None,
    result: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    meta.update(
        {
            "classification": classification,
            "command_exit": command_exit,
            "launcher_exit": code,
            "result": result,
            "status": status,
            "problems": sorted(set(problems)),
            "finished_at": _stamp(_now()),
        }
    )
    write_file(output, "launcher-meta.json", canonical_bytes(meta))
    write_file(
        output,
        "exit-code.txt",
        (
            f"command_exit={'none' if command_exit is None else command_exit}\n"
            f"launcher_exit={code}\nclassification={classification}\n"
        ).encode(),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": meta["mode"],
        "classification": classification,
        "command_exit": command_exit,
        "launcher_exit": code,
        "result": result,
        "status": status,
        "problems": sorted(set(problems)),
        "output_dir": str(output),
    }


# --- CLI ---------------------------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # nunca cita el valor recibido
        raise UsageError()


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = _Parser(prog="s11_ex10_preflight_launcher", add_help=False, allow_abbrev=False)
    parser.add_argument("mode", choices=tuple(MODES))
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--git-safe-directory", action="store_true")
    parser.add_argument("--timeout", type=int)
    args = parser.parse_args(list(argv))
    if not _SHA1.fullmatch(args.expected_git_sha):
        raise UsageError()
    if args.timeout is not None and not 30 <= args.timeout <= 3600:
        raise UsageError()
    return args


def _interrupt(number, frame):
    raise KeyboardInterrupt


def repository_root() -> Path:
    # backend/scripts/<este archivo>: la raíz del checkout es dos niveles por encima.
    return Path(os.path.realpath(__file__)).parents[2]


def main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None) -> int:
    environ = os.environ if environ is None else environ
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except UsageError as exc:
        print(f"S11_EX10_LAUNCHER_USAGE: {exc.code}", file=sys.stderr)
        return EXIT_USAGE
    root = repository_root()
    clock = _now()
    default = Path(environ.get("HOME", "")) / (
        f"s11-ex10-{args.mode}-{clock.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
    )
    invocation = Invocation(
        mode=args.mode,
        expected_git_sha=args.expected_git_sha,
        repo_root=root,
        output_dir=Path(args.output_dir) if args.output_dir else default,
        environ=environ,
        safe_directory=args.git_safe_directory,
        timeout=args.timeout,
    )
    for number in (signal.SIGTERM, signal.SIGHUP):
        # Como Ctrl-C: se detiene el `run`, se limpia su contenedor y queda el registro.
        signal.signal(number, _interrupt)
    try:
        summary = execute(invocation)
    except LauncherError as exc:
        # Ocupado o sin directorio de salida: no se creó nada.
        print(f"S11_EX10_LAUNCHER_CONFIG: {exc.code}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("S11_EX10_LAUNCHER_ABORT: interrupted", file=sys.stderr)
        return EXIT_APPARATUS_ABORT
    except Exception as exc:  # noqa: BLE001 - nunca un 1 (= FAIL) ni una traza con valores
        print(f"S11_EX10_LAUNCHER_INTERNAL_ERROR: {type(exc).__name__}", file=sys.stderr)
        return EXIT_LAUNCHER_FAILURE
    sys.stdout.write(canonical_bytes(summary).decode("utf-8"))
    return summary["launcher_exit"]


if __name__ == "__main__":
    raise SystemExit(main())
