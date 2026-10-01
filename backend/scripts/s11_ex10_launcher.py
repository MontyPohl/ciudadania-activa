#!/usr/bin/env python3
"""Launcher de host del lifecycle de EX-10 para el ensayo LOCAL_TEST (S11-C3, fase 3B).

Sólo stdlib. Cada acción es una invocación del management command `s11_ex10` en un
contenedor efímero del servicio `backend` del proyecto de ensayo
(`ciudadania-s11-ex10-lifecycle-rehearsal`, `docker compose run --rm --no-deps`):

- `start-local-test`: `start --evidence-mode LOCAL_TEST --confirm-new-run`, después de validar
  el informe del preflight operacional del mismo HEAD (PASS, `READY_FOR_EXECUTOR_INTEGRATION`,
  reciente). El modo es siempre LOCAL_TEST: no hay opción para elegirlo;
- `rehearsal-started`: `rehearsal-started --confirm-local-rehearsal`, PREPARED → STARTED sin
  emitir;
- `abandon-pre-t0`: `finalize --abandon-reason <código permitido>`;
- `status`: `status`, sólo lectura.

No hay `execute`, `drain`, `reconcile`, `finalize` con decisión, reintento ni reanudación, y
no se aceptan opciones desconocidas. El checkout se verifica como en el launcher del
preflight (raíz exacta, HEAD, árbol limpio, sin sparse ni shallow, sin entradas ocultas del
índice, G0 y G0-A1) y se monta una sola vez, en sólo lectura, en `/s11-full-source`, con los
`.env*` no versionados tapados y, si hace falta, un `/etc/gitconfig` temporal con
exactamente `safe.directory = /s11-full-source`.

El ensayo nunca toca la custodia oficial. Exige una sesión fuera del repositorio:

    <sesión>/{evidence,backup,restore,objects,output,temporary-secrets}   (todo 0700)

con `temporary-secrets/rehearsal-secrets.json` (0600: claves sintéticas de custodia,
publicación y documento, y un `DJANGO_SECRET_KEY` sintético) y
`temporary-secrets/compose.rehearsal.yml` (0600, idéntico byte a byte a
`rehearsal_override()`): el override sustituye en `backend`, `worker` y `beat` los montajes
de `var/` por las raíces de la sesión y vacía el `env_file` (el `.env` real no entra). Las
claves sintéticas se rechazan si coinciden con las de la custodia oficial o con las del
entorno del operador. Sólo viajan por el entorno de `docker compose`, nunca por argv ni a la
salida.

Salida en `<sesión>/output/<acción>-<timestamp>-<token>` (0700, archivos 0600 exclusivos):
`launcher-meta.json`, `<acción>.json` (stdout coherente), `<acción>.stderr` (saneado),
`exit-code.txt`, `docker-before.txt` y `docker-after.txt`.

Códigos de salida: 0 acción completada (stdout coherente), 1 rechazo reintentable y 2 rechazo
definitivo del command (sólo con su JSON de códigos en stderr), 3 uso, configuración o
validación previa, 4 `LAUNCHER_FAILURE` (exit y salida incoherentes, invariante violado o
limpieza fallida) y 5 `APPARATUS_ABORT` (timeout o interrupción). Un 4 o un 5 nunca es un 0.
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

SCHEMA_VERSION = "s11c3-ex10-lifecycle-launcher-v1"
PROJECT = "ciudadania-s11-ex10-lifecycle-rehearsal"
COMPOSE_FILES = base.COMPOSE_FILES
SERVICE = base.SERVICE
SOURCE_MOUNT = base.SOURCE_MOUNT
EVIDENCE_MODE = "LOCAL_TEST"
SESSION_DIRS = ("evidence", "backup", "restore", "objects", "output", "temporary-secrets")
#: Montaje de cada raíz de la sesión en el contenedor (los del overlay S11).
ROOT_MOUNTS = {
    "evidence": "/s11-evidence",
    "backup": "/s11-backup",
    "restore": "/s11-restore",
    "objects": "/s11-objects",
}
SECRETS_FILE = "rehearsal-secrets.json"
OVERRIDE_FILE = "compose.rehearsal.yml"
SYNTHETIC_ENV = (*base.INTERPOLATION_ENV, "DJANGO_SECRET_KEY")
#: Huellas públicas de la custodia oficial vigente: un ensayo nunca las usa.
OFFICIAL_CUSTODY_EPOCH = "587966006e85202a"
OFFICIAL_PUBLICATION_KEY_ID = "8c1e5a8ad7390f4d"
#: Igual que `ex10_executor.PREFLIGHT_MAX_AGE` y su tolerancia al futuro.
PREFLIGHT_MAX_AGE = timedelta(minutes=15)
PREFLIGHT_FUTURE_TOLERANCE = timedelta(0)
ABANDON_REASONS = frozenset({"rehearsal-complete", "operator-stop"})
REQUIRED_FILES = (
    "backend/apps/experiments_app/ex10_executor.py",
    "backend/apps/experiments_app/ex10_reconcile.py",
    "backend/apps/experiments_app/management/commands/s11_ex10.py",
    "backend/scripts/s11_ex10_launcher.py",
    "backend/scripts/s11_ex10_preflight_launcher.py",
)
MAX_JSON_BYTES = 1024 * 1024

EXIT_OK, EXIT_REFUSED, EXIT_TERMINAL, EXIT_USAGE = 0, 1, 2, 3
EXIT_LAUNCHER_FAILURE, EXIT_APPARATUS_ABORT = 4, 5
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_RUN_ID = re.compile(r"^s11-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_NONCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REASON_CODE = re.compile(r"^[A-Za-z0-9_.:@=+,/-]{1,160}$")

LauncherError = base.LauncherError
LauncherBusy = base.LauncherBusy
UsageError = base.UsageError
Completed = base.Completed
Aborted = base.Aborted
DockerRunner = base.DockerRunner


@dataclass(frozen=True)
class ActionSpec:
    timeout: float
    #: backend y worker arriba: el START corre el preflight y el checkpoint observa el worker.
    requires_web: bool


ACTIONS: Mapping[str, ActionSpec] = {
    "start-local-test": ActionSpec(600.0, True),
    "rehearsal-started": ActionSpec(300.0, True),
    "abandon-pre-t0": ActionSpec(300.0, False),
    "status": ActionSpec(120.0, False),
}
#: Palabras que nunca aparecen en el comando del contenedor: ni emisión ni pasos oficiales.
_FORBIDDEN_WORDS = (
    "execute",
    "drain",
    "reconcile",
    "official",
    "confirm-emission",
    "declare-executor-gone",
    "acknowledge",
    "provider",
    "retry",
    "resume",
    "request-count",
)


# --- Sesión de ensayo ------------------------------------------------------------------------


@dataclass(frozen=True)
class Session:
    root: Path
    secrets: Mapping[str, str] = field(repr=False)

    def path(self, name: str) -> Path:
        return self.root / name

    @property
    def override(self) -> Path:
        return self.root / "temporary-secrets" / OVERRIDE_FILE


def _private_dir(path: Path, code: str) -> None:
    info = os.lstat(path) if os.path.lexists(path) else None
    if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise LauncherError(code)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise LauncherError(code)


def _private_file(path: Path, code: str) -> bytes:
    info = os.lstat(path) if os.path.lexists(path) else None
    if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise LauncherError(code)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise LauncherError(code)
    if info.st_size > MAX_JSON_BYTES:
        raise LauncherError(code)
    return path.read_bytes()


def official_roots(repo_root: Path) -> tuple[str, ...]:
    return tuple(
        str(repo_root / "var" / name)
        for name in ("s11-runs", "s11-backup", "s11-restore", "s11-objects")
    )


def verify_session(root: Path, repo_root: Path, environ: Mapping[str, str]) -> Session:
    """La sesión aislada: fuera del repositorio y de las raíces oficiales, privada y propia."""
    text = str(root)
    if not root.is_absolute() or os.path.realpath(text) != text:
        raise LauncherError("session-root-not-realpath")
    if any(char in text for char in ":,\n\"'"):
        raise LauncherError("session-root-unmountable")
    repo = str(repo_root)
    if base._inside(text, repo) or base._inside(repo, text):
        raise LauncherError("session-root-inside-repository")
    if any(base._inside(text, o) or base._inside(o, text) for o in official_roots(repo_root)):
        raise LauncherError("session-root-overlaps-official")
    _private_dir(root, "session-root-unsafe")
    parent = os.stat(root.parent)
    if parent.st_uid != os.getuid() or parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise LauncherError("session-parent-unsafe")
    for name in SESSION_DIRS:
        _private_dir(root / name, f"session-dir-unsafe:{name}")
    raw = _private_file(root / "temporary-secrets" / SECRETS_FILE, "secrets-file-unsafe")
    values = synthetic_secrets(raw, environ)
    session = Session(root, values)
    expected = rehearsal_override(session).encode()
    if _private_file(session.override, "override-file-unsafe") != expected:
        raise LauncherError("override-file-differs")
    return session


def synthetic_secrets(raw: bytes, environ: Mapping[str, str]) -> dict[str, str]:
    """Las claves del ensayo: sintéticas, nunca las de la custodia oficial ni las del host."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise LauncherError("secrets-file-malformed") from None
    if not isinstance(payload, dict) or set(payload) != set(SYNTHETIC_ENV):
        raise LauncherError("secrets-file-malformed")
    if not all(
        isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64,128}", v) for v in payload.values()
    ):
        raise LauncherError("secrets-file-malformed")
    custody = payload["S11_CUSTODY_HMAC_KEY"]
    if hashlib.sha256(custody.encode()).hexdigest()[:16] == OFFICIAL_CUSTODY_EPOCH:
        raise LauncherError("real-custody-key")
    public = bytes.fromhex(payload["S11_PUBLICATION_APPROVAL_PUBLIC_KEY"])
    if len(public) != 32:
        raise LauncherError("secrets-file-malformed")
    if hashlib.sha256(public).hexdigest()[:16] == OFFICIAL_PUBLICATION_KEY_ID:
        raise LauncherError("real-publication-key")
    for name, value in payload.items():
        if environ.get(name) and environ.get(name) == value:
            raise LauncherError("secret-equals-host-environment")
    if len(set(payload.values())) != len(payload):
        raise LauncherError("secrets-not-distinct")
    return dict(payload)


def generate_secrets() -> dict[str, str]:
    """Claves sintéticas nuevas para una sesión (para el operador; nunca se imprimen)."""
    return {
        "S11_CUSTODY_HMAC_KEY": secrets.token_hex(32),
        "S11_PUBLICATION_APPROVAL_PUBLIC_KEY": secrets.token_hex(32),
        "S11_DOCUMENT_HMAC_SECRET": secrets.token_hex(32),
        "DJANGO_SECRET_KEY": secrets.token_hex(48),
    }


def rehearsal_override(session: Session) -> str:
    """El override Compose del ensayo: raíces de la sesión y nada del `.env` real."""
    lines = [
        "# Override del ensayo LOCAL_TEST de EX-10 (fase 3B). Generado por s11_ex10_launcher.",
        "# Raíces de la sesión en lugar de var/ y sin el env_file del repositorio.",
        "services:",
    ]
    for service in ("backend", "worker", "beat"):
        lines += [
            f"  {service}:",
            "    env_file: !reset []",
            "    environment:",
            f"      DJANGO_SECRET_KEY: \"{session.secrets['DJANGO_SECRET_KEY']}\"",
        ]
        if service != "beat":
            lines.append("    volumes:")
            lines += [
                f"      - {session.path(name)}:{target}" for name, target in ROOT_MOUNTS.items()
            ]
    return "\n".join(lines) + "\n"


def write_session(root: Path) -> Session:
    """Crea una sesión nueva (directorio inexistente): estructura, claves y override."""
    if not root.is_absolute() or os.path.lexists(root):
        raise LauncherError("session-root-unavailable")
    os.mkdir(root, 0o700)
    os.chmod(root, 0o700)
    for name in SESSION_DIRS:
        os.mkdir(root / name, 0o700)
        os.chmod(root / name, 0o700)
    session = Session(root, generate_secrets())
    base.write_file(
        root / "temporary-secrets",
        SECRETS_FILE,
        json.dumps(dict(session.secrets), sort_keys=True).encode(),
    )
    base.write_file(root / "temporary-secrets", OVERRIDE_FILE, rehearsal_override(session).encode())
    return session


def check_evidence(session: Session, action: str, run_id: str | None) -> list[str]:
    """La raíz de evidencia de la sesión: vacía para START; sólo el run esperado después."""
    runs = session.path("evidence") / "runs"
    present = sorted(p.name for p in runs.iterdir()) if runs.is_dir() else []
    if runs.is_symlink():
        raise LauncherError("evidence-runs-symlink")
    if action == "start-local-test":
        if present:
            raise LauncherError("session-evidence-not-empty")
    elif present != [run_id]:
        raise LauncherError("session-evidence-unexpected-run")
    return present


# --- Informe de preflight ----------------------------------------------------------------------


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


# --- Comando -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    project: str
    compose_files: tuple[str, ...]
    env_file: str = "/dev/null"


def container_command(
    action: str,
    *,
    expected_git_sha: str,
    run_id: str | None,
    nonce: str | None,
    abandon_reason: str | None,
) -> list[str]:
    head = ["python", "manage.py", "s11_ex10"]
    if action == "start-local-test":
        return [
            *head,
            "start",
            "--expected-git-sha",
            expected_git_sha,
            "--source-root",
            SOURCE_MOUNT,
            "--nonce",
            nonce,
            "--evidence-mode",
            EVIDENCE_MODE,
            "--confirm-new-run",
        ]
    if action == "rehearsal-started":
        return [
            *head,
            "rehearsal-started",
            "--run-id",
            run_id,
            "--expected-git-sha",
            expected_git_sha,
            "--confirm-local-rehearsal",
        ]
    if action == "abandon-pre-t0":
        return [
            *head,
            "finalize",
            "--run-id",
            run_id,
            "--expected-git-sha",
            expected_git_sha,
            "--abandon-reason",
            abandon_reason,
        ]
    if action == "status":
        return [*head, "status", "--run-id", run_id]
    raise LauncherError("action-not-allowed")


def build_command(
    target: Target,
    action: str,
    *,
    session: Session,
    expected_git_sha: str,
    container_name: str,
    repo_root: Path,
    run_id: str | None = None,
    nonce: str | None = None,
    abandon_reason: str | None = None,
    gitconfig: Path | None = None,
    masks: tuple[str, ...] = (),
) -> list[str]:
    argv = ["docker", "compose", "-p", target.project, "--env-file", target.env_file]
    for compose_file in target.compose_files:
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
            abandon_reason=abandon_reason,
        ),
    ]
    assert_safe_command(
        argv, target=target, action=action, session=session, repo_root=repo_root, masks=masks
    )
    return argv


def assert_safe_command(
    argv: Sequence[str],
    *,
    target: Target,
    action: str,
    session: Session,
    repo_root: Path,
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
    if target.project != PROJECT:
        problems.append("project")
    expected_files = (*COMPOSE_FILES, str(session.override))
    files = [argv[i + 1] for i, token in enumerate(argv[:run]) if token == "-f"]
    if tuple(files) != expected_files or tuple(target.compose_files) != expected_files:
        problems.append("compose-files")
    options = argv[run + 1 : service]
    mounts = [options[i + 1] for i, token in enumerate(options[:-1]) if token == "-v"]
    if argv[:2] != ["docker", "compose"] or set(argv) & base._FORBIDDEN_TOKENS:
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
        if rest != f"{base.GITCONFIG_MOUNT}:ro" or base._inside(
            os.path.realpath(source), str(repo_root)
        ):
            problems.append("extra-mount")
    if gitconfigs > 1:
        problems.append("extra-mount")
    if any(not mount.endswith(":ro") for mount in mounts):
        problems.append("rw-mount")
    sources = [mount.partition(":")[0] for mount in mounts]
    if any(base._inside(s, o) for s in sources for o in official_roots(repo_root)):
        problems.append("official-root-mount")
    inside = [
        token for token in argv if token not in mounts and base._inside(token, str(repo_root))
    ]
    if sum(1 for s in sources if base._inside(s, str(repo_root))) != 1 or inside:
        problems.append("checkout-mounted-more-than-once")
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
    if any(value in token for token in argv for value in session.secrets.values()):
        problems.append("secret-in-argv")
    if problems:
        raise LauncherError("unsafe-command:" + ",".join(sorted(set(problems))))


def _tail_ok(action: str, tail: Sequence[str]) -> bool:
    """La forma exacta del comando del contenedor para cada acción; nada más."""
    if action not in ACTIONS or list(tail[:4]) != [
        "python",
        "manage.py",
        "s11_ex10",
        _SUBCOMMAND[action],
    ]:
        return False
    rest = list(tail[4:])
    sha = len(rest) > 3 and bool(
        _SHA1.fullmatch(rest[1] if action == "start-local-test" else rest[3])
    )
    run_id = len(rest) > 1 and bool(_RUN_ID.fullmatch(rest[1]))
    if action == "start-local-test":
        return (
            len(rest) == 9
            and rest[0] == "--expected-git-sha"
            and sha
            and rest[2:5] == ["--source-root", SOURCE_MOUNT, "--nonce"]
            and bool(_NONCE.fullmatch(rest[5]))
            and rest[6:] == ["--evidence-mode", EVIDENCE_MODE, "--confirm-new-run"]
        )
    if action == "rehearsal-started":
        return (
            len(rest) == 5
            and rest[0] == "--run-id"
            and run_id
            and rest[2] == "--expected-git-sha"
            and sha
            and rest[4] == "--confirm-local-rehearsal"
        )
    if action == "abandon-pre-t0":
        return (
            len(rest) == 6
            and rest[0] == "--run-id"
            and run_id
            and rest[2] == "--expected-git-sha"
            and sha
            and rest[4] == "--abandon-reason"
            and rest[5] in ABANDON_REASONS
        )
    return len(rest) == 2 and rest[0] == "--run-id" and run_id


_SUBCOMMAND = {
    "start-local-test": "start",
    "rehearsal-started": "rehearsal-started",
    "abandon-pre-t0": "finalize",
    "status": "status",
}


# --- Salida del comando ------------------------------------------------------------------------


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
        "ABANDONED_PRE_T0",
        "NOT_DEMONSTRATED",
        "FINALIZED_PASS",
        "FINALIZED_FAIL",
    }
)
_OUTPUT_KEYS = {
    "start-local-test": {
        "run_id",
        "scenario_id",
        "evidence_mode",
        "phase",
        "manifest_sha256",
        "start_sha256",
        "preflight_sha256",
    },
    "rehearsal-started": {
        "run_id",
        "evidence_mode",
        "phase",
        "emission_armed",
        "t0_published",
        "tokens_issued",
        "requests_prepared",
        "requests_sent",
    },
    "abandon-pre-t0": {
        "run_id",
        "decision",
        "lifecycle",
        "binding",
        "reasons",
        "decision_sha256",
        "manifest_status",
        "manifest_sha256",
        "custody_errors",
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
}


@dataclass
class Outcome:
    classification: str
    exit_code: int
    status: str | None = None
    run_id: str | None = None
    problems: list[str] = field(default_factory=list)
    persist_stdout: bool = False


def action_problems(action: str, payload: dict[str, Any], run_id: str | None) -> list[str]:
    problems = []
    if set(payload) != _OUTPUT_KEYS[action]:
        problems.append("payload-keys")
    got = payload.get("run_id")
    if not isinstance(got, str) or not _RUN_ID.fullmatch(got):
        problems.append("run-id")
    elif run_id is not None and got != run_id:
        problems.append("run-id-differs")
    if action == "start-local-test":
        problems += [
            f"start:{k}"
            for k, v in (
                ("scenario_id", "EX-10"),
                ("phase", "PREPARED"),
                ("evidence_mode", EVIDENCE_MODE),
            )
            if payload.get(k) != v
        ]
        for key in ("manifest_sha256", "start_sha256", "preflight_sha256"):
            if not isinstance(payload.get(key), str) or not _SHA256.fullmatch(payload[key]):
                problems.append(f"start:{key}")
    elif action == "rehearsal-started":
        expected = {
            "evidence_mode": EVIDENCE_MODE,
            "phase": "STARTED",
            "emission_armed": False,
            "t0_published": False,
            "tokens_issued": 0,
            "requests_prepared": 0,
            "requests_sent": 0,
        }
        problems += [
            f"started:{k}"
            for k, v in expected.items()
            if payload.get(k) != v or type(payload.get(k)) is not type(v)
        ]
    elif action == "abandon-pre-t0":
        if payload.get("lifecycle") != "ABANDONED_PRE_T0" or payload.get("binding") is not False:
            problems.append("abandon:not-pre-t0")
        if payload.get("decision") != "NOT_DEMONSTRATED" or payload.get("custody_errors") != []:
            problems.append("abandon:decision")
    elif action == "status":
        if payload.get("exists") is not True or payload.get("phase") not in _PHASES:
            problems.append("status:phase")
        if payload.get("scenario_id") != "EX-10" or payload.get("custody_errors") != []:
            problems.append("status:run")
    return problems


def validate_output(
    action: str,
    completed: Completed,
    refusals: list,
    *,
    run_id: str | None,
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
        return Outcome(
            "REFUSED", code, status="TERMINAL" if terminal else "RETRYABLE", run_id=run_id
        )
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
    problems = []
    hits = base.secret_hits(text, secret_values)
    problems += [f"stdout-secret:{name}" for name in sorted(hits)]
    if text != json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n":
        problems.append("stdout-not-the-command-json")
    try:
        problems += action_problems(action, payload, run_id)
    except (AttributeError, KeyError, TypeError, ValueError):
        problems.append("stdout-malformed")
    status = payload.get("lifecycle") if action == "abandon-pre-t0" else payload.get("phase")
    if problems:
        return Outcome(
            "LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE, problems=problems, persist_stdout=not hits
        )
    return Outcome(
        "COHERENT", EXIT_OK, status=status, run_id=payload["run_id"], persist_stdout=True
    )


# --- Orquestación ------------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Invocation:
    action: str
    expected_git_sha: str
    repo_root: Path
    session_root: Path
    environ: Mapping[str, str]
    run_id: str | None = None
    nonce: str | None = None
    abandon_reason: str | None = None
    preflight_report: Path | None = None
    safe_directory: bool = False
    timeout: float | None = None
    target: Target | None = None
    runner_factory: Callable[[Path, Mapping[str, str]], DockerRunner] = DockerRunner
    clock: Callable[[], datetime] = _now
    token: Callable[[], str] = lambda: secrets.token_hex(4)


def child_environment(environ: Mapping[str, str], session: Session) -> dict[str, str]:
    """Docker, el uid/gid y las claves sintéticas de la sesión. Nada más del host."""
    env = {name: environ[name] for name in base.DOCKER_ENV if environ.get(name)}
    env.setdefault("PATH", "/usr/bin:/bin")
    for name in base.INTERPOLATION_ENV:
        env[name] = session.secrets[name]
    for name, value in (("S11_HOST_UID", os.getuid()), ("S11_HOST_GID", os.getgid())):
        if environ.get(name, str(value)) != str(value):
            raise LauncherError(f"{name.lower().replace('_', '-')}-mismatch")
        env[name] = str(value)
    return env


def validate_invocation(invocation: Invocation) -> None:
    if invocation.action not in ACTIONS:
        raise LauncherError("action-not-allowed")
    if not _SHA1.fullmatch(invocation.expected_git_sha):
        raise LauncherError("expected-git-sha-format")
    start = invocation.action == "start-local-test"
    if start != (invocation.nonce is not None) or start != (
        invocation.preflight_report is not None
    ):
        raise UsageError()
    if invocation.nonce is not None and not _NONCE.fullmatch(invocation.nonce):
        raise UsageError()
    if start == (invocation.run_id is not None):
        raise UsageError()
    if invocation.run_id is not None and not _RUN_ID.fullmatch(invocation.run_id):
        raise UsageError()
    abandon = invocation.action == "abandon-pre-t0"
    if abandon != (invocation.abandon_reason is not None):
        raise UsageError()
    if abandon and invocation.abandon_reason not in ABANDON_REASONS:
        raise LauncherError("abandon-reason-not-allowed")


@contextlib.contextmanager
def session_lock(root: Path) -> Iterator[None]:
    """Un launcher a la vez por sesión, además del de checkout del launcher del preflight."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LauncherBusy() from None
        yield
    finally:
        os.close(fd)


def execute(invocation: Invocation) -> dict[str, Any]:
    validate_invocation(invocation)
    spec = ACTIONS[invocation.action]
    started = invocation.clock()
    with base.exclusive(invocation.repo_root), session_lock(invocation.session_root):
        session = verify_session(invocation.session_root, invocation.repo_root, invocation.environ)
        stamp = started.strftime("%Y%m%dT%H%M%SZ")
        output = base.prepare_output_dir(
            session.path("output") / f"{invocation.action}-{stamp}-{invocation.token()}",
            invocation.repo_root,
        )
        meta: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "action": invocation.action,
            "project": PROJECT,
            "evidence_mode": EVIDENCE_MODE,
            "official_run_id": None,
            "rehearsal_run_id": invocation.run_id,
            "expected_git_sha": invocation.expected_git_sha,
            "source_root_container": SOURCE_MOUNT,
            "source_mount_mode": "ro",
            "full_checkout_mounts": 1,
            "safe_directory": SOURCE_MOUNT if invocation.safe_directory else None,
            "container_user": {"uid": os.getuid(), "gid": os.getgid()},
            "session_root": str(session.root),
            "output_dir": str(output),
            "started_at": base._stamp(started),
            "official_evidence_written": False,
        }
        try:
            summary = _execute_locked(invocation, spec, session, output, meta)
        except LauncherError as exc:
            summary = _finish(output, meta, "VALIDATION_FAILED", EXIT_USAGE, problems=[exc.code])
        return summary


def _execute_locked(invocation, spec, session, output, meta) -> dict[str, Any]:
    repo = invocation.repo_root
    facts = base.verify_repository(repo, invocation.expected_git_sha)
    tracked = set(base.HostGit(repo)("ls-files", "-z")[1].split("\0"))
    for relative in REQUIRED_FILES:
        path = repo / relative
        if relative not in tracked or path.is_symlink() or not path.is_file():
            raise LauncherError("required-file-missing")
    meta["repository"] = facts.to_json()
    meta["evidence_runs_before"] = check_evidence(session, invocation.action, invocation.run_id)
    if invocation.preflight_report is not None:
        raw = _private_file(invocation.preflight_report, "preflight-report-unsafe")
        problems = preflight_report_problems(
            raw, git_sha=invocation.expected_git_sha, now=invocation.clock()
        )
        if problems:
            meta["preflight_report_problems"] = problems
            raise LauncherError("preflight-report-not-acceptable")
        meta["preflight_report_sha256"] = hashlib.sha256(raw).hexdigest()
    target = invocation.target or Target(PROJECT, (*COMPOSE_FILES, str(session.override)))
    env = child_environment(invocation.environ, session)
    meta["child_env_names"] = sorted(env)
    runner = invocation.runner_factory(repo, env)
    meta["docker"] = base.verify_docker(target, runner)
    before = base.docker_state(target, runner)
    base.write_file(output, "docker-before.txt", before.text().encode())
    meta["preconditions"] = preconditions(spec, before)
    container = f"{PROJECT}-lifecycle-{invocation.action}-{invocation.token()}"
    if container in before.names() or not base._NAME.fullmatch(container):
        raise LauncherError("container-name-unavailable")
    gitconfig = None
    if invocation.safe_directory:
        gitconfig = output / "safe-directory.gitconfig"
        base.write_file(output, gitconfig.name, base.SAFE_DIRECTORY_CONFIG.encode(), 0o644)
    argv = build_command(
        target,
        invocation.action,
        session=session,
        expected_git_sha=invocation.expected_git_sha,
        container_name=container,
        repo_root=repo,
        run_id=invocation.run_id,
        nonce=invocation.nonce,
        abandon_reason=invocation.abandon_reason,
        gitconfig=gitconfig,
        masks=facts.masked_files,
    )
    meta["container_name"] = container
    secret_values = list(session.secrets.values())
    completed = aborted = None
    begun = invocation.clock()
    try:
        completed = runner.run(argv, timeout=invocation.timeout or spec.timeout)
    except Aborted as exc:
        aborted = exc.kind
    except OSError:
        aborted = "docker-unavailable"
    finally:
        cleanup = base._cleanup(target, runner, container, gitconfig)
    meta["command_duration_ms"] = int((invocation.clock() - begun).total_seconds() * 1000)
    meta["cleanup"] = cleanup
    try:
        after = base.docker_state(target, runner)
    except LauncherError:
        after = None
    else:
        base.write_file(output, "docker-after.txt", after.text().encode())
    meta["docker_after_matches_before"] = after is not None and (
        after.names(),
        after.networks,
        after.volumes,
    ) == (before.names(), before.networks, before.volumes)
    if completed is None:
        kind = "APPARATUS_ABORT" if aborted != "docker-unavailable" else "LAUNCHER_FAILURE"
        code = EXIT_APPARATUS_ABORT if kind == "APPARATUS_ABORT" else EXIT_LAUNCHER_FAILURE
        return _finish(output, meta, kind, code, problems=[aborted])
    text, facts_stderr, refusals = sanitize_stderr(completed.stderr, secret_values)
    raw_exact = base.secret_hits(completed.stderr.decode("utf-8", "replace"), secret_values).get(
        "exact-secret-value", 0
    )
    meta["stderr"] = {
        **facts_stderr,
        "raw_exact_secret_values": raw_exact,
        "secret_hits": sum(base.secret_hits(text, secret_values).values()),
    }
    base.write_file(output, f"{invocation.action}.stderr", text.encode())
    outcome = validate_output(
        invocation.action,
        completed,
        refusals,
        run_id=invocation.run_id,
        secret_values=secret_values,
    )
    stdout_hits = base.secret_hits(completed.stdout.decode("utf-8", "replace"), secret_values)
    meta["stdout"] = {
        "bytes": len(completed.stdout),
        "sha256": hashlib.sha256(completed.stdout).hexdigest(),
        "secret_hits": sum(stdout_hits.values()),
        "persisted": bool(outcome.persist_stdout and completed.stdout and not stdout_hits),
    }
    if meta["stdout"]["persisted"]:
        base.write_file(output, f"{invocation.action}.json", completed.stdout)
    if refusals:
        meta["refusal"] = refusals[0]
    problems = list(outcome.problems)
    classification, code = outcome.classification, outcome.exit_code
    if meta["stderr"]["secret_hits"] or raw_exact:
        problems.append("stderr-secret")
    if not cleanup["container_absent"] or not cleanup["gitconfig_removed"]:
        problems.append("cleanup-failed")
    if after is None:
        problems.append("docker-state-after-unavailable")
    elif not meta["docker_after_matches_before"]:
        problems.append("docker-resources-changed")
    if problems and classification != "LAUNCHER_FAILURE":
        classification, code = "LAUNCHER_FAILURE", EXIT_LAUNCHER_FAILURE
    if outcome.run_id and classification == "COHERENT":
        meta["rehearsal_run_id"] = outcome.run_id
    return _finish(
        output,
        meta,
        classification,
        code,
        problems=problems,
        command_exit=completed.returncode,
        status=outcome.status if classification in {"COHERENT", "REFUSED"} else None,
    )


def preconditions(spec: ActionSpec, state) -> dict[str, Any]:
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
    }
    if not infra:
        raise LauncherError("db-redis-not-healthy")
    if facts["beat_running"]:
        raise LauncherError("beat-running")
    if spec.requires_web and (facts["backend_running"], facts["worker_running"]) != (1, 1):
        raise LauncherError("web-worker-not-running")
    return facts


def _finish(output, meta, classification, code, *, problems=(), command_exit=None, status=None):
    meta.update(
        {
            "classification": classification,
            "command_exit": command_exit,
            "launcher_exit": code,
            "status": status,
            "problems": sorted(set(problems)),
            "finished_at": base._stamp(_now()),
        }
    )
    base.write_file(output, "launcher-meta.json", base.canonical_bytes(meta))
    base.write_file(
        output,
        "exit-code.txt",
        (
            f"command_exit={'none' if command_exit is None else command_exit}\n"
            f"launcher_exit={code}\nclassification={classification}\n"
        ).encode(),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "action": meta["action"],
        "classification": classification,
        "command_exit": command_exit,
        "launcher_exit": code,
        "status": status,
        "evidence_mode": EVIDENCE_MODE,
        "official_run_id": None,
        "rehearsal_run_id": meta.get("rehearsal_run_id"),
        "problems": sorted(set(problems)),
        "output_dir": str(output),
    }


# --- CLI ---------------------------------------------------------------------------------------


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = base._Parser(prog="s11_ex10_launcher", add_help=False, allow_abbrev=False)
    parser.add_argument("action", choices=tuple(ACTIONS))
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--session-root", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--nonce")
    parser.add_argument("--abandon-reason")
    parser.add_argument("--preflight-report")
    parser.add_argument("--git-safe-directory", action="store_true")
    parser.add_argument("--timeout", type=int)
    args = parser.parse_args(list(argv))
    if args.timeout is not None and not 30 <= args.timeout <= 3600:
        raise UsageError()
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
            run_id=args.run_id,
            nonce=args.nonce,
            abandon_reason=args.abandon_reason,
            preflight_report=Path(args.preflight_report) if args.preflight_report else None,
            safe_directory=args.git_safe_directory,
            timeout=args.timeout,
        )
        validate_invocation(invocation)
    except LauncherError as exc:
        print(f"S11_EX10_LAUNCHER_USAGE: {exc.code}", file=sys.stderr)
        return EXIT_USAGE
    for number in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, base._interrupt)
    try:
        summary = execute(invocation)
    except LauncherError as exc:
        print(f"S11_EX10_LAUNCHER_CONFIG: {exc.code}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("S11_EX10_LAUNCHER_ABORT: interrupted", file=sys.stderr)
        return EXIT_APPARATUS_ABORT
    except Exception as exc:  # noqa: BLE001 - nunca un 0 ni una traza con valores
        print(f"S11_EX10_LAUNCHER_INTERNAL_ERROR: {type(exc).__name__}", file=sys.stderr)
        return EXIT_LAUNCHER_FAILURE
    sys.stdout.write(base.canonical_bytes(summary).decode("utf-8"))
    return summary["launcher_exit"]


if __name__ == "__main__":
    raise SystemExit(main())
