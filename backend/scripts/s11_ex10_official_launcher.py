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
- `cleanup-stack --confirm-cleanup`: `docker compose down --remove-orphans` del proyecto
  oficial, sin volúmenes ni imágenes, sólo sin run abierto.

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
import importlib.util
import json
import os
import re
import secrets
import signal
import stat
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def _load_base():
    """El launcher del preflight, sin modificarlo: sus primitivas ya verificadas."""
    name = "s11_ex10_preflight_launcher"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(os.path.realpath(__file__)).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


base = _load_base()

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
    "cleanup-stack": ActionSpec(None, "--confirm-cleanup", 300.0, False, True),
}
CONTAINER_ACTIONS = frozenset(a for a, s in ACTIONS.items() if s.subcommand is not None)
#: Palabras que nunca aparecen en el comando del contenedor.
_FORBIDDEN_WORDS = (
    "local_test",
    "local-test",
    "rehearsal",
    "declare-executor-gone",
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
#: Estrictamente de sólo lectura en el dominio: la evidencia entera debe quedar idéntica.
#: `reconcile` no escribe contenido, pero abre el run con `load_state` (que normaliza permisos
#: y cambia el ctime de sus directorios): sólo puede tocar el run de la sesión, nunca otro.
READ_ONLY_ACTIONS = frozenset({"status"})


# --- Integridad del checkout y del propio launcher -------------------------------------------


def git_blob_sha(content: bytes) -> str:
    """El id de blob de Git de unos bytes (sha1 de `blob <n>\\0` + bytes)."""
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()  # noqa: S324


def verify_launcher_integrity(repo_root: Path, expected_git_sha: str) -> dict[str, str]:
    """Este archivo y el launcher base son exactamente sus blobs del HEAD esperado."""
    git = base.HostGit(repo_root)
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
    for entry in history:
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
    invoked = {e["action"] for e in history if e["classification"] != "VALIDATION_FAILED"}
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
    facts = base.verify_repository(repo, invocation.expected_git_sha)
    tracked = set(base.HostGit(repo)("ls-files", "-z")[1].split("\0"))
    for relative in REQUIRED_FILES:
        path = repo / relative
        if relative not in tracked or path.is_symlink() or not path.is_file():
            raise LauncherError("required-file-missing")
    integrity = invocation.integrity(repo, invocation.expected_git_sha)
    code, out = base.HostGit(repo)("symbolic-ref", "--short", "-q", "HEAD")[:2]
    branch = out.strip() if code == 0 else ""
    if branch != "main":
        raise LauncherError("branch-not-main")
    verify_official_roots(repo)
    return facts, integrity, branch


def execute(invocation: Invocation) -> dict[str, Any]:
    validate_invocation(invocation)
    if invocation.action == "initialize-session":
        return initialize_session(invocation)
    if invocation.action == "inspect":
        return inspect_session(invocation)
    root = invocation.session_root
    with base.exclusive(invocation.repo_root):
        check_session_location(root, invocation.repo_root)
        _private_dir(root, "session-root-unsafe")
        with session_lock(root):
            manifest = load_session(root, invocation.repo_root)
            return _execute_locked(invocation, manifest)


def initialize_session(invocation: Invocation) -> dict[str, Any]:
    root = invocation.session_root
    with base.exclusive(invocation.repo_root):
        facts, integrity, branch = _verify_checkout(invocation)
        check_session_location(root, invocation.repo_root)
        if os.path.lexists(root):
            raise LauncherError("session-root-exists")
        os.mkdir(root, 0o700)
        os.chmod(root, 0o700)
        os.mkdir(root / "actions", 0o700)
        created = base._stamp(invocation.clock())
        session_id = f"s11-ex10-official-session-{invocation.token()}{invocation.token()}"
        manifest = new_session_manifest(
            session_id=session_id,
            created_at=created,
            git_sha=invocation.expected_git_sha,
            branch=branch,
            repo_root=invocation.repo_root,
            integrity=integrity,
        )
        base.write_file(root, "git.json", base.canonical_bytes(facts.to_json()))
        write_session(root, manifest, create=True)
    return _summary(manifest, invocation.action, "COHERENT", EXIT_OK, output=None)


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
        "oneoff_containers": sum(1 for c in state.containers if c.oneoff),
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
        }
    )
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


def main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None) -> int:
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
    except SessionTampered as exc:
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
