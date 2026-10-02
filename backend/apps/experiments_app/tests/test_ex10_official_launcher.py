"""EX-10 fase 4A: launcher de la ejecución OFICIAL. LOCAL TEST / NON-EXPERIMENTAL.

Checkouts Git sintéticos, sesiones en `tmp_path` y un Docker simulado en proceso. Las claves
son sintéticas y sus huellas se inyectan sólo en el test: nunca las de la custodia oficial.
Ningún test lanza Docker, lee `.env`, usa el stack S11 ni toca `var/` del repositorio. La
integración con el executor real usa una custodia temporal y un backend de test en 127.0.0.1.
"""

import fcntl
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import signal
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "s11_ex10_official_launcher.py"
_SPEC = importlib.util.spec_from_file_location("s11_ex10_official_launcher", SCRIPT)
official = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = official
_FLAG, sys.dont_write_bytecode = sys.dont_write_bytecode, True
try:
    _SPEC.loader.exec_module(official)
finally:
    sys.dont_write_bytecode = _FLAG
base = official.base

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
PROJECT = official.PROJECT
RUN_ID = "s11-33b0d2a4-ad98-5b8e-b48d-31701977365f"
OTHER_RUN = "s11-44c1d2a4-ad98-5b8e-b48d-31701977365f"
NONCE = "nonce-official-0001"
SHA256 = "a" * 64
INTEGRITY = {official.LAUNCHER_PATH: "1" * 40, official.BASE_LAUNCHER_PATH: "2" * 40}


def git(root, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


@pytest.fixture
def keys(monkeypatch):
    """Claves sintéticas; sus huellas se declaran oficiales sólo dentro del test."""
    values = {
        "S11_CUSTODY_HMAC_KEY": secrets.token_hex(32),
        "S11_PUBLICATION_APPROVAL_PUBLIC_KEY": secrets.token_hex(32),
        "S11_DOCUMENT_HMAC_SECRET": secrets.token_hex(32),
    }
    custody = hashlib.sha256(values["S11_CUSTODY_HMAC_KEY"].encode()).hexdigest()[:16]
    public = bytes.fromhex(values["S11_PUBLICATION_APPROVAL_PUBLIC_KEY"])
    monkeypatch.setattr(official, "OFFICIAL_CUSTODY_EPOCH", custody)
    monkeypatch.setattr(
        official, "OFFICIAL_PUBLICATION_KEY_ID", hashlib.sha256(public).hexdigest()[:16]
    )
    return values


def make_checkout(parent, monkeypatch):
    root = Path(os.path.realpath(parent)) / "repo"
    for relative in {*base.REQUIRED_FILES, *official.REQUIRED_FILES}:
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text("# LOCAL TEST placeholder\n")
    (root / "infra").mkdir()
    (root / "infra" / "docker-compose.yml").write_text("services: {}\n")
    (root / ".gitignore").write_text("__pycache__/\n.env\n/var/\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "g0")
    first = git(root, "rev-parse", "HEAD")
    (root / "infra" / "docker-compose.yml").write_text("services: {backend: {}}\n")
    git(root, "commit", "-q", "-am", "g0-a1")
    second = git(root, "rev-parse", "HEAD")
    monkeypatch.setattr(base, "G0_DECISION_COMMIT", first)
    monkeypatch.setattr(base, "G0_A1_DECISION_COMMIT", second)
    for name in official.OFFICIAL_ROOTS:
        (root / "var" / name).mkdir(parents=True, mode=0o700)
    home = Path(os.path.realpath(parent)) / "home"
    home.mkdir(mode=0o700)
    return SimpleNamespace(root=root, head=git(root, "rev-parse", "HEAD"), home=home)


@pytest.fixture
def checkout(tmp_path, monkeypatch, keys):
    repo = make_checkout(tmp_path, monkeypatch)
    repo.keys = keys
    return repo


def preflight_payload(sha, **changes):
    payload = {
        **official._PREFLIGHT_EXPECTED,
        "git_sha": sha,
        "expected_git_sha": sha,
        "checked_at": (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "checks": [
            {"check": "health", "result": "PASS", "expected": None,
             "observed_sanitized": None, "detail_code": None}
        ],
    }  # fmt: skip
    payload.update(changes)
    return payload


def write_report(home, payload, name=None, mode=0o600):
    path = home / (name or f"pf-{secrets.token_hex(4)}.json")
    path.write_text(json.dumps(payload))
    path.chmod(mode)
    return path


# --- Docker simulado ---------------------------------------------------------------------------


def container(name, state, service, oneoff=False, healthy=False):
    return {"name": name, "state": state, "service": service, "oneoff": oneoff, "healthy": healthy}


def command_json(payload):
    return (json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n").encode()


START_OK = {
    "run_id": RUN_ID, "scenario_id": "EX-10", "evidence_mode": "OFFICIAL", "phase": "PREPARED",
    "manifest_sha256": SHA256, "start_sha256": SHA256, "preflight_sha256": SHA256,
}  # fmt: skip
EXECUTE_OK = {
    "run_id": RUN_ID, "phase": "RECONCILING", "t0_published": True, "interrupted": False,
    "attempts": 50, "sent": 50, "apparatus_faults": 0, "raw_rejected": False,
    "drain": {"outcome": "COMPLETE", "polls": 3, "uninspected_polls": 0},
}  # fmt: skip
STOPPED_OK = {"run_id": RUN_ID, "phase": "STOPPED_PRE_T0", "reasons": ["fixture-failed"]}
DRAIN_OK = {
    "schema_version": "s11c3-ex10-drain-v1", "scenario_id": "EX-10", "run_id": RUN_ID,
    "recorded_at": "2026-09-30T12:00:00.000000Z", "boot_id": "b", "same_boot": True,
    "requests_sent_during_drain": 0, "config_after": None, "outcome": "COMPLETE", "polls": 2,
    "uninspected_polls": 0, "deadline": None, "final_poll_inspected": True, "pending_at_end": None,
}  # fmt: skip
RECONCILE_OK = {
    "run_id": RUN_ID, "provisional": True, "decision": "PASS", "criteria": {},
    "invalidity_reasons": [], "not_demonstrable": [], "failed_criteria": [],
}  # fmt: skip
FINAL_OK = {
    "run_id": RUN_ID, "decision": "PASS", "lifecycle": "FINALIZED_PASS", "binding": True,
    "reasons": [], "decision_sha256": SHA256, "manifest_status": "VALID",
    "manifest_sha256": SHA256, "custody_errors": [],
}  # fmt: skip
ABANDON_OK = {
    **FINAL_OK, "decision": "NOT_DEMONSTRATED", "lifecycle": "ABANDONED_PRE_T0",
    "binding": False, "reasons": ["abandoned-before-t0:operator-stop"],
    "manifest_status": "INVALID",
}  # fmt: skip


def status_payload(phase, **changes):
    return {
        "run_id": RUN_ID, "exists": True, "scenario_id": "EX-10", "phase": phase,
        "finalized": phase in official.TERMINAL_PHASES, "artifacts": ["ex10-start"],
        "decision": None, "custody_errors": [], **changes,
    }  # fmt: skip


OUTPUT = {
    "start-official": START_OK,
    "status": status_payload("PREPARED"),
    "execute-official": EXECUTE_OK,
    "drain": DRAIN_OK,
    "reconcile": RECONCILE_OK,
    "finalize": FINAL_OK,
    "abandon-official-pre-t0": ABANDON_OK,
}


class FakeDocker:
    def __init__(self, *, web=True, beat=False):
        self.containers = [
            container(f"{PROJECT}-db-1", "running", "db", healthy=True),
            container(f"{PROJECT}-redis-1", "running", "redis", healthy=True),
        ]
        if web:
            self.containers += [
                container(f"{PROJECT}-backend-1", "running", "backend"),
                container(f"{PROJECT}-worker-1", "running", "worker"),
            ]
        if beat:
            self.containers.append(container(f"{PROJECT}-beat-1", "running", "beat"))
        self.networks, self.volumes = [f"{PROJECT}_default"], [f"{PROJECT}_s11_postgres_data"]
        self.calls, self.envs = [], []
        self.result = None
        self.abort = None
        self.leave_container = False
        self.on_run = None

    def factory(self, cwd, env):
        self.envs.append(dict(env))
        return self

    def runs(self):
        return [a for a in self.calls if a[:2] == ["docker", "compose"] and "run" in a]

    def run(self, argv, *, timeout=base.DOCKER_CLI_TIMEOUT):
        argv = list(argv)
        self.calls.append(argv)
        ok = base.Completed
        if argv[:2] == ["docker", "version"]:
            return ok(0, b"29.8.1\n", b"")
        if argv[:3] == ["docker", "compose", "version"]:
            return ok(0, b"5.5.1\n", b"")
        if argv[:3] == ["docker", "image", "ls"]:
            return ok(0, b"abc123\n", b"")
        if argv[:2] == ["docker", "ps"]:
            if "health=healthy" in argv:
                rows = [c["name"] for c in self.containers if c["healthy"]]
            else:
                rows = [
                    "\t".join([c["name"], c["state"], c["service"], str(c["oneoff"])])
                    for c in self.containers
                ]
            return ok(0, "".join(f"{r}\n" for r in rows).encode(), b"")
        if argv[:3] == ["docker", "network", "ls"]:
            return ok(0, "".join(f"{n}\n" for n in self.networks).encode(), b"")
        if argv[:3] == ["docker", "volume", "ls"]:
            return ok(0, "".join(f"{n}\n" for n in self.volumes).encode(), b"")
        if argv[:2] == ["docker", "compose"] and "down" in argv:
            self.timeout = timeout
            return ok(0, b"", b" Container x Stopped\n")
        if argv[:2] == ["docker", "compose"] and "run" in argv:
            self.timeout = timeout
            if self.on_run:
                self.on_run(argv)
            if self.leave_container:
                name = argv[argv.index("--name") + 1]
                self.containers.append(container(name, "running", "backend", oneoff=True))
            if self.abort:
                raise base.Aborted(self.abort)
            return self.result
        if argv[:3] == ["docker", "rm", "-f"]:
            self.containers = [c for c in self.containers if c["name"] != argv[3]]
            return ok(0, b"", b"")
        raise AssertionError(f"comando Docker inesperado: {argv[:3]}")


_TOKENS = iter(f"{n:08x}" for n in range(1, 1_000_000))


def environ(checkout, **changes):
    values = {"HOME": str(checkout.home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    values.update(checkout.keys)
    values.update(changes)
    return {k: v for k, v in values.items() if v is not None}


def invocation(checkout, root, fake, action, **changes):
    spec = official.ACTIONS[action]
    values = {
        "action": action,
        "expected_git_sha": checkout.head,
        "repo_root": checkout.root,
        "session_root": root,
        "environ": environ(checkout),
        "confirmed": spec.confirmation is not None,
        "runner_factory": fake.factory,
        "clock": lambda: NOW,
        "token": lambda: next(_TOKENS),
        "integrity": lambda repo, sha: dict(INTEGRITY),
    }
    if action == "start-official":
        values["nonce"] = NONCE
        values["preflight_report"] = write_report(checkout.home, preflight_payload(checkout.head))
    values.update(changes)
    return official.Invocation(**values)


def run_dir(checkout, run_id=RUN_ID):
    return checkout.root / "var" / "s11-runs" / "runs" / run_id


def make_run(checkout, run_id=RUN_ID):
    path = run_dir(checkout, run_id)
    (path / "metadata").mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def session(checkout):
    root = checkout.home / "s11-ex10-official-test"
    summary = official.execute(invocation(checkout, root, FakeDocker(), "initialize-session"))
    assert summary["classification"] == "COHERENT"
    return root


def act(checkout, root, action, *, fake=None, code=0, stdout=None, stderr=b"", **changes):
    fake = fake or FakeDocker()
    if fake.result is None and action in OUTPUT:
        out = command_json(OUTPUT[action]) if stdout is None and code == 0 else (stdout or b"")
        fake.result = base.Completed(code, out, stderr)
    if action == "start-official" and code == 0 and stdout is None and fake.on_run is None:
        fake.on_run = lambda argv: make_run(checkout)
    summary = official.execute(invocation(checkout, root, fake, action, **changes))
    return summary, fake


def manifest(root):
    return json.loads((root / official.SESSION_FILE).read_text())


def tail(argv):
    return argv[argv.index(official.SERVICE, argv.index("run")) + 1 :]


def to_reconciling(checkout, root):
    assert act(checkout, root, "start-official")[0]["classification"] == "COHERENT"
    assert act(checkout, root, "execute-official")[0]["classification"] == "COHERENT"


def refusal(code, reasons):
    payload = {"reasons": reasons, "refused": True, "terminal": code == 2}
    return json.dumps(payload, sort_keys=True).encode() + b"\nCommandError: S11_EX10_REFUSED\n"


# --- Sesión (1–14) ------------------------------------------------------------------------------


def test_initialize_session_binds_head_branch_project_and_writes_privately(checkout, session):
    # (1, 7) Registro canónico, 0600 en un directorio 0700, sin run y ligado al HEAD exacto.
    data = manifest(session)
    raw = (session / official.SESSION_FILE).read_bytes()
    assert base.canonical_bytes(data) == raw
    assert data["expected_git_sha"] == checkout.head and data["branch"] == "main"
    assert data["project"] == PROJECT and data["evidence_mode"] == "OFFICIAL"
    assert data["official_run_id"] is None and data["history"] == []
    assert data["kind"] == "operator-session-record-not-experimental-evidence"
    assert data["state"]["next_allowed_actions"] == ["cleanup-stack", "inspect", "start-official"]
    assert stat.S_IMODE(session.stat().st_mode) == 0o700
    for path in session.rglob("*"):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    assert json.loads((session / "git.json").read_text())["head"] == checkout.head


def test_initialize_session_refuses_a_dirty_tree_or_another_head(checkout):
    # (2, 3)
    root = checkout.home / "s-dirty"
    (checkout.root / "infra" / "docker-compose.yml").write_text("dirty\n")
    with pytest.raises(base.LauncherError):
        official.execute(invocation(checkout, root, FakeDocker(), "initialize-session"))
    assert not root.exists()
    git(checkout.root, "checkout", "-q", "--", "infra/docker-compose.yml")
    with pytest.raises(base.LauncherError):
        official.execute(
            invocation(
                checkout, root, FakeDocker(), "initialize-session", expected_git_sha="b" * 40
            )
        )
    assert not root.exists()


def test_a_modified_launcher_is_refused(checkout, monkeypatch):
    # (4, 30) El launcher y su base deben ser exactamente sus blobs del HEAD.
    for relative in (official.LAUNCHER_PATH, official.BASE_LAUNCHER_PATH):
        source = Path(__file__).resolve().parents[3] / relative.removeprefix("backend/")
        (checkout.root / relative).write_bytes(source.read_bytes())
    git(checkout.root, "add", "-A")
    git(checkout.root, "commit", "-q", "-m", "launchers")
    head = git(checkout.root, "rev-parse", "HEAD")
    monkeypatch.setattr(official, "__file__", str(checkout.root / official.LAUNCHER_PATH))
    facts = official.verify_launcher_integrity(checkout.root, head)
    assert set(facts) == {official.LAUNCHER_PATH, official.BASE_LAUNCHER_PATH}
    target = checkout.root / official.LAUNCHER_PATH
    target.write_bytes(target.read_bytes() + b"# x\n")
    with pytest.raises(base.LauncherError, match="launcher-source-modified"):
        official.verify_launcher_integrity(checkout.root, head)
    git(checkout.root, "checkout", "-q", "--", official.LAUNCHER_PATH)
    monkeypatch.setattr(official, "__file__", "/elsewhere/s11_ex10_official_launcher.py")
    with pytest.raises(base.LauncherError, match="launcher-not-from-checkout"):
        official.verify_launcher_integrity(checkout.root, head)


def test_initialize_session_refuses_an_existing_or_unsafe_location(checkout, session):
    # (5, 6, 12) Directorio existente, dentro del repositorio, en var/ o bajo un padre abierto.
    fake = FakeDocker()
    for root, code in (
        (session, "session-root-exists"),
        (checkout.root / "s", "session-root-inside-repository"),
        (checkout.root / "var" / "s11-runs" / "s", "session-root-inside-repository"),
    ):
        with pytest.raises(base.LauncherError, match=code):
            official.execute(invocation(checkout, root, fake, "initialize-session"))
    loose = checkout.home / "loose"
    loose.mkdir(mode=0o777)
    loose.chmod(0o777)
    with pytest.raises(base.LauncherError, match="session-parent-unsafe"):
        official.execute(invocation(checkout, loose / "s", fake, "initialize-session"))
    session.chmod(0o755)
    with pytest.raises(base.LauncherError, match="session-root-unsafe"):
        act(checkout, session, "status")
    session.chmod(0o700)


def test_session_updates_are_atomic_canonical_and_hash_every_output(checkout, session):
    # (8, 9) Cada acción añade una entrada encadenada con el SHA-256 de sus salidas.
    summary, _ = act(checkout, session, "start-official")
    data = manifest(session)
    (entry,) = data["history"]
    output = session / entry["output_dir"]
    assert entry["output_sha256"] == {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()
    }
    assert entry["chain"] == official._chain(data["header_sha256"], entry)
    assert not [p for p in session.iterdir() if p.name.endswith(".tmp")]
    assert data["official_run_id"] == RUN_ID == summary["official_run_id"]


@pytest.mark.parametrize(
    "tamper", ["output", "manifest-entry", "manifest-run-id", "new-file", "header", "preflight-sha"]
)
def test_a_tampered_history_blocks_every_action(checkout, session, tamper):
    # (10, 39, 79) Una salida previa o el registro alterados: SESSION_HISTORY_TAMPERED.
    act(checkout, session, "start-official")
    data = manifest(session)
    output = session / data["history"][0]["output_dir"]
    if tamper == "output":
        (output / "start-official.json").chmod(0o600)
        (output / "start-official.json").write_text("{}")
    elif tamper == "new-file":
        base.write_file(output, "extra.txt", b"x")
    else:
        if tamper == "manifest-entry":
            data["history"][0]["classification"] = "REFUSED"
        else:
            data["official_run_id"] = OTHER_RUN
        official.write_session(session, data)
    fake = FakeDocker()
    with pytest.raises(official.SessionTampered):
        act(checkout, session, "status", fake=fake)
    assert fake.calls == []
    with pytest.raises(official.SessionTampered):
        official.execute(invocation(checkout, session, FakeDocker(), "inspect"))


@pytest.mark.parametrize("lock", ["checkout", "session"])
def test_a_concurrent_launcher_is_busy_and_creates_nothing(checkout, session, lock):
    # (11) Ni salida parcial ni contenedor.
    path = checkout.root if lock == "checkout" else session
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        before = sorted(os.listdir(session / "actions"))
        fake = FakeDocker()
        with pytest.raises(base.LauncherBusy):
            official.execute(invocation(checkout, session, fake, "status"))
        assert fake.calls == [] and sorted(os.listdir(session / "actions")) == before
    finally:
        os.close(fd)


def test_a_session_of_another_repository_head_or_project_is_refused(
    checkout, session, tmp_path, monkeypatch
):
    # (12–14)
    fake = FakeDocker()
    summary, fake = act(checkout, session, "status", expected_git_sha="c" * 40)
    assert summary["classification"] == "VALIDATION_FAILED"
    assert summary["problems"] == ["session-other-head"] and fake.runs() == []
    data = manifest(session)
    data["repo_root"] = "/elsewhere"
    official.write_session(session, data)
    with pytest.raises(base.LauncherError, match="session-other-repository"):
        act(checkout, session, "status")
    data["repo_root"], data["project"] = (
        str(checkout.root),
        "ciudadania-s11-ex10-lifecycle-rehearsal",
    )
    official.write_session(session, data)
    with pytest.raises(official.SessionTampered):
        act(checkout, session, "status")


# --- Fuente y preflight (15–30) ------------------------------------------------------------------


def test_the_full_checkout_is_mounted_once_read_only_and_never_the_official_roots(
    checkout, session
):
    # (15, 17, 46, 95) Un único montaje del checkout en sólo lectura; las raíces oficiales son
    # las del overlay (sin montajes ni opciones del operador).
    _, fake = act(checkout, session, "start-official")
    (argv,) = fake.runs()
    mounts = [argv[i + 1] for i, token in enumerate(argv) if token == "-v"]
    assert mounts == [f"{checkout.root}:/s11-full-source:ro"]
    files = [argv[i + 1] for i, token in enumerate(argv) if token == "-f"]
    assert files == list(base.COMPOSE_FILES) and argv[2:4] == ["-p", PROJECT]
    assert not any("/var/" in token or "s11-runs" in token for token in argv)


@pytest.mark.parametrize(
    ("mutate", "problem"),
    [
        (
            lambda a, r: a.insert(a.index("run") + 1, "-v")
            or a.insert(a.index("-v") + 1, f"{r}/var/s11-runs:/x:ro"),
            "official-root-mount",
        ),  # noqa: E501
        (
            lambda a, r: a.__setitem__(
                a.index(f"{r}:/s11-full-source:ro"), f"{r}:/s11-full-source"
            ),
            "checkout-mount",
        ),  # noqa: E501
        (
            lambda a, r: a.__setitem__(
                a.index(f"{r}:/s11-full-source:ro"), f"{r}/backend:/s11-full-source:ro"
            ),
            "checkout-mount",
        ),  # noqa: E501
        (lambda a, r: a.__setitem__(3, "ciudadania-s11-ex10-lifecycle-rehearsal"), "project"),
        (lambda a, r: a.insert(a.index("run") + 1, "--build"), "run-options"),
        (lambda a, r: a.append("--declare-executor-gone"), "container-command"),
        (lambda a, r: a.__setitem__(a.index("OFFICIAL"), "LOCAL_TEST"), "container-command"),
        (lambda a, r: a.insert(4, "-f") or a.insert(5, "x.yml"), "compose-files"),
    ],
    ids=["official-root", "rw", "partial", "project", "build", "declare", "local-test", "files"],
)
def test_unsafe_commands_are_rejected(checkout, mutate, problem):
    # (16, 17, 33, 96)
    argv = official.build_command(
        "start-official", environ_secrets=["s" * 64], expected_git_sha=checkout.head,
        container_name="c", repo_root=checkout.root, run_id=None, nonce=NONCE,
    )  # fmt: skip
    mutate(argv, checkout.root)
    with pytest.raises(base.LauncherError, match=problem):
        official.assert_safe_command(
            argv, action="start-official", repo_root=checkout.root, secrets=["s" * 64]
        )


def test_safe_directory_is_exact_temporary_and_never_a_wildcard(checkout, session):
    # (18, 19)
    seen = {}

    def capture(argv):
        mount = [argv[i + 1] for i, t in enumerate(argv) if t == "-v"][1]
        path = Path(mount.split(":")[0])
        seen["content"] = path.read_text()
        make_run(checkout)

    fake = FakeDocker()
    fake.on_run = capture
    act(checkout, session, "start-official", fake=fake, safe_directory=True)
    assert seen["content"] == "[safe]\n\tdirectory = /s11-full-source\n"
    assert "*" not in seen["content"]
    assert not list((session / "actions").rglob("safe-directory.gitconfig"))


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({}, None),
        ({"git_sha": "b" * 40}, "preflight:git_sha"),
        (
            {"checked_at": (NOW - timedelta(minutes=16)).isoformat().replace("+00:00", "Z")},
            "preflight:stale",
        ),  # noqa: E501
        (
            {"checked_at": (NOW + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")},
            "preflight:checked-at-future",
        ),  # noqa: E501
        ({"gate": "NOT_READY"}, "preflight:gate"),
        ({"result": "FAIL"}, "preflight:result"),
        ({"result": "INCOMPLETE"}, "preflight:result"),
        ({"protocol_version": "s11-ex10-v0"}, "preflight:protocol_version"),
        ({"methodology_version": "S11A-v1.0"}, "preflight:methodology_version"),
        ({"ready_for_execution": True}, "preflight:ready_for_execution"),
        ({"run_id": RUN_ID}, "preflight:run_id"),
        ({"evidence_written": True}, "preflight:evidence_written"),
        ({"checks": [{"check": "x", "result": "NOT_RUN"}]}, "preflight:check-not-pass"),
    ],
    ids=[
        "pass",
        "sha",
        "stale",
        "future",
        "gate",
        "fail",
        "incomplete",
        "protocol",
        "methodology",
        "ready",
        "run-id",
        "evidence",
        "not-run",
    ],  # fmt: skip
)
def test_the_preflight_report_must_be_a_fresh_pass_of_the_same_head(checkout, change, problem):
    # (20–25)
    problems = official.preflight_report_problems(
        json.dumps(preflight_payload(checkout.head, **change)).encode(),
        git_sha=checkout.head,
        now=NOW,
    )
    assert problems == ([] if problem is None else [problem])


@pytest.mark.parametrize("raw", [b"", b"{", b"[]", b'{"result": "PASS"}', b"NaN"])
def test_a_malformed_preflight_report_is_rejected(checkout, raw):
    # (26)
    assert official.preflight_report_problems(raw, git_sha=checkout.head, now=NOW) == [
        "preflight:malformed"
    ]


def test_a_rejected_or_world_readable_report_launches_nothing(checkout, session):
    for report in (
        write_report(checkout.home, preflight_payload("b" * 40)),
        write_report(checkout.home, preflight_payload(checkout.head), mode=0o644),
    ):
        summary, fake = act(checkout, session, "start-official", preflight_report=report)
        assert summary["classification"] == "VALIDATION_FAILED" and fake.runs() == []
    assert manifest(session)["official_run_id"] is None


def test_the_launcher_has_no_options_for_roots_mode_or_run_id(checkout, capsys):
    # (9, 36, 47, 99, 100) Ni raíces, ni modo, ni `--run-id`, ni opciones del ensayo.
    head = ["start-official", "--expected-git-sha", checkout.head, "--session-root", "/x",
            "--nonce", NONCE, "--preflight-report", "/x", "--confirm-official-start"]  # fmt: skip
    for extra in (["--evidence-root", "/x"], ["--backup-root", "/x"], ["--restore-root", "/x"],
                  ["--evidence-mode", "LOCAL_TEST"], ["--run-id", RUN_ID], ["--local-test"],
                  ["--confirm-local-rehearsal"], ["--official"], ["--project", "x"],
                  ["--retry"], ["--force-pass"]):  # fmt: skip
        assert official.main([*head, *extra], environ={}) == 3
    for action in ("rehearsal-started", "abandon-pre-t0", "start-local-test", "retry"):
        assert (
            official.main(
                [action, "--expected-git-sha", checkout.head, "--session-root", "/x"], environ={}
            )
            == 3
        )  # noqa: E501


def test_the_source_anchor_and_source_lists_are_reported_by_the_go_no_go_package():
    # (27–29) Las fuentes en ejecución las verifica el preflight; el launcher exige sus blobs.
    assert official.LAUNCHER_PATH in official.REQUIRED_FILES
    assert official.BASE_LAUNCHER_PATH in official.REQUIRED_FILES


# --- Confirmaciones ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    [
        "start-official",
        "execute-official",
        "drain",
        "reconcile",
        "finalize",
        "abandon-official-pre-t0",
        "declare-executor-gone",
        "cleanup-stack",
    ],  # fmt: skip
)
def test_each_mutating_action_needs_its_own_confirmation(checkout, action):
    # (34, 49) Sin confirmación, o con la de otra acción: uso (3), sin contenedor ni escritura.
    own = official.ACTIONS[action].confirmation
    args = [action, "--expected-git-sha", checkout.head, "--session-root", "/nonexistent-s"]
    if action == "start-official":
        args += ["--nonce", NONCE, "--preflight-report", "/x"]
    with pytest.raises(base.UsageError):
        official.validate_invocation(official.Invocation(
            action=action, expected_git_sha=checkout.head, repo_root=checkout.root,
            session_root=Path("/x"), environ={}, confirmed=False,
            nonce=NONCE if action == "start-official" else None,
            preflight_report=Path("/x") if action == "start-official" else None,
        ))  # fmt: skip
    for other in {s.confirmation for s in official.ACTIONS.values() if s.confirmation} - {own}:
        assert official.main([*args, other], environ={}) == 3
    with pytest.raises(base.UsageError):
        official.parse_args(
            [
                *args,
                own,
                "--confirm-official-start"
                if own != "--confirm-official-start"
                else "--confirm-emission",
            ]
        )  # noqa: E501


def test_read_only_actions_reject_any_confirmation(checkout):
    for action in ("status", "inspect", "initialize-session"):
        with pytest.raises(base.UsageError):
            official.parse_args(
                [action, "--expected-git-sha", checkout.head, "--session-root", "/x",
                 "--confirm-emission"]
            )  # fmt: skip


# --- START (31–47) -------------------------------------------------------------------------------


def test_start_official_runs_start_in_official_mode_and_binds_the_run_id(checkout, session):
    # (31, 32, 40, 44, 45)
    summary, fake = act(checkout, session, "start-official")
    assert summary["classification"] == "COHERENT" and summary["official_run_id"] == RUN_ID
    (argv,) = fake.runs()
    assert tail(argv) == [
        "python", "manage.py", "s11_ex10", "start", "--expected-git-sha", checkout.head,
        "--source-root", "/s11-full-source", "--nonce", NONCE, "--evidence-mode", "OFFICIAL",
        "--confirm-new-run",
    ]  # fmt: skip
    data = manifest(session)
    assert data["official_run_id"] == RUN_ID and data["preflight_report_sha256"]
    assert data["state"]["known_phase"] == "PREPARED"
    assert data["state"]["next_allowed_actions"] == [
        "abandon-official-pre-t0", "execute-official", "inspect", "status",
    ]  # fmt: skip
    assert len(fake.runs()) == 1  # nunca encadena `execute`


def test_start_passes_only_exact_acknowledgements(checkout, session):
    # (37) El reconocimiento de un NOT_DEMONSTRATED oficial es explícito y por run_id.
    _, fake = act(checkout, session, "start-official", acknowledged=(OTHER_RUN,))
    assert tail(fake.runs()[0])[-2:] == ["--acknowledge-not-demonstrated", OTHER_RUN]
    for bad in (("*",), ("all",), (OTHER_RUN, OTHER_RUN)):
        with pytest.raises(base.UsageError):
            official.validate_invocation(
                invocation(checkout, session, FakeDocker(), "start-official", acknowledged=bad)
            )


@pytest.mark.parametrize(
    ("code", "reasons"),
    [
        (2, [f"binding-run-exists:{OTHER_RUN}"]),
        (1, [f"not-demonstrated-unacknowledged:{OTHER_RUN}"]),
        (1, [f"active-run:{OTHER_RUN}"]),
        (1, [f"prior-run-unverifiable:{OTHER_RUN}"]),
    ],
    ids=["pass-or-fail-prior", "nd-without-ack", "active", "corrupt"],
)
def test_domain_refusals_of_start_keep_their_codes_and_bind_nothing(
    checkout, session, code, reasons
):
    # (35–39) El executor decide el primer run válido; el launcher propaga y no avanza.
    summary, fake = act(
        checkout, session, "start-official", code=code, stderr=refusal(code, reasons)
    )
    assert summary["classification"] == "REFUSED" and summary["launcher_exit"] == code
    data = manifest(session)
    assert data["official_run_id"] is None
    assert "start-official" in data["state"]["next_allowed_actions"]


@pytest.mark.parametrize(
    ("stdout", "problem"),
    [
        ({**START_OK, "run_id": "s11-bad"}, "run-id"),
        ({**START_OK, "evidence_mode": "LOCAL_TEST"}, "start:evidence_mode"),
        ({**START_OK, "phase": "STARTED"}, "start:phase"),
        ({**START_OK, "extra": 1}, "payload-keys"),
    ],
    ids=["invalid-run-id", "local-test", "phase", "extra-field"],
)
def test_an_incoherent_start_output_binds_nothing_and_blocks(checkout, session, stdout, problem):
    # (41) Salida incoherente: LAUNCHER_FAILURE, sin run ligado; un START incierto exige revisión.
    summary, _ = act(checkout, session, "start-official", stdout=command_json(stdout))
    assert summary["classification"] == "LAUNCHER_FAILURE" and summary["launcher_exit"] == 4
    assert problem in summary["problems"]
    data = manifest(session)
    assert data["official_run_id"] is None and data["state"]["next_allowed_actions"] == ["inspect"]


def test_a_second_start_is_not_allowed(checkout, session):
    # (43)
    act(checkout, session, "start-official")
    summary, fake = act(checkout, session, "start-official")
    assert summary["problems"] == ["action-not-allowed-now:start-official"] and fake.runs() == []


def test_start_must_create_a_new_run_and_never_touch_prior_runs(checkout, session):
    # (42, 21) Ni un run ya existente como "nuevo" ni un run previo cambiado.
    make_run(checkout, RUN_ID)
    summary, _ = act(checkout, session, "start-official", fake=_fake_with(lambda argv: None))
    assert "start:run-id-not-new" in summary["problems"]
    session2 = checkout.home / "s2"
    official.execute(invocation(checkout, session2, FakeDocker(), "initialize-session"))
    make_run(checkout, OTHER_RUN)
    third = "s11-55d2e3b5-be09-5c9f-a59e-42812088476a"

    def creates_and_touches_prior(argv):
        make_run(checkout, third)
        (run_dir(checkout, OTHER_RUN) / "x").write_text("x")

    fake = _fake_with(creates_and_touches_prior)
    fake.result = base.Completed(0, command_json({**START_OK, "run_id": third}), b"")
    summary, _ = act(checkout, session2, "start-official", fake=fake)
    assert "prior-run-changed" in summary["problems"]


def _fake_with(on_run):
    fake = FakeDocker()
    fake.on_run = on_run
    return fake


def test_start_refuses_synthetic_or_absent_official_keys(checkout, session):
    # (10, 35) Claves que no son las de la custodia oficial: nada se lanza.
    for changes in ({"S11_CUSTODY_HMAC_KEY": secrets.token_hex(32)},
                    {"S11_PUBLICATION_APPROVAL_PUBLIC_KEY": secrets.token_hex(32)},
                    {"S11_DOCUMENT_HMAC_SECRET": None}):  # fmt: skip
        summary, fake = act(
            checkout, session, "start-official", environ=environ(checkout, **changes)
        )
        assert summary["classification"] == "VALIDATION_FAILED" and fake.runs() == []
        assert summary["problems"][0].startswith("official-keys:")


def test_official_roots_must_be_real_directories(checkout, session):
    # (46, 47)
    runs = checkout.root / "var" / "s11-runs"
    runs.rename(checkout.root / "var" / "elsewhere")
    runs.symlink_to(checkout.root / "var" / "elsewhere", target_is_directory=True)
    summary, fake = act(checkout, session, "start-official")
    assert summary["problems"] == ["official-root-unsafe"] and fake.runs() == []


# --- EXECUTE (48–62) -----------------------------------------------------------------------------


def test_execute_runs_once_from_prepared_and_never_chains(checkout, session):
    # (48, 62)
    act(checkout, session, "start-official")
    summary, fake = act(checkout, session, "execute-official")
    assert summary["classification"] == "COHERENT"
    assert tail(fake.runs()[0]) == [
        "python", "manage.py", "s11_ex10", "execute", "--run-id", RUN_ID, "--expected-git-sha",
        checkout.head, "--confirm-emission",
    ]  # fmt: skip
    assert len(fake.runs()) == 1
    state = manifest(session)["state"]
    assert state["known_phase"] == "RECONCILING"
    assert state["next_allowed_actions"] == ["inspect", "reconcile", "status"]


@pytest.mark.parametrize("first", ["COHERENT", "REFUSED", "ABORT", "INCOHERENT"])
def test_execute_is_never_invoked_twice(checkout, session, first):
    # (51, 60, 61) Pase lo que pase, un segundo `execute` no se lanza; uno incierto sólo deja
    # `status` hasta que una lectura coherente fije la fase.
    act(checkout, session, "start-official")
    fake = FakeDocker()
    if first == "REFUSED":
        act(checkout, session, "execute-official", code=1, stderr=refusal(1, ["preflight:stale"]))
    elif first == "ABORT":
        fake.abort = "timeout"
        summary, _ = act(checkout, session, "execute-official", fake=fake)
        assert summary["classification"] == "APPARATUS_ABORT"
    elif first == "INCOHERENT":
        act(checkout, session, "execute-official", stdout=b"{}\n")
    else:
        act(checkout, session, "execute-official")
    state = manifest(session)["state"]
    assert "execute-official" not in state["next_allowed_actions"]
    if first in {"ABORT", "INCOHERENT"}:
        assert state["next_allowed_actions"] == ["inspect", "status"]
    summary, fake = act(checkout, session, "execute-official")
    assert summary["problems"] == ["action-not-allowed-now:execute-official"]
    assert fake.runs() == []


def test_execute_before_start_or_after_a_terminal_is_not_allowed(checkout, session):
    # (50)
    summary, fake = act(checkout, session, "execute-official")
    assert summary["problems"] == ["action-not-allowed-now:execute-official"] and fake.runs() == []


def test_execute_refuses_a_stale_attached_preflight(checkout, session):
    # (54) El mismo plazo que aplica el executor a la emisión.
    act(checkout, session, "start-official")
    later = NOW + timedelta(minutes=20)
    summary, fake = act(checkout, session, "execute-official", clock=lambda: later)
    assert summary["problems"] == ["preflight-report-not-acceptable"] and fake.runs() == []


def test_execute_refuses_another_head_or_a_tampered_attached_preflight(checkout, session):
    # (53)
    act(checkout, session, "start-official")
    summary, fake = act(checkout, session, "execute-official", expected_git_sha="d" * 40)
    assert summary["problems"] == ["session-other-head"] and fake.runs() == []


@pytest.mark.parametrize("web", ["worker-missing", "beat", "oneoff", "db-unhealthy"])
def test_execute_needs_a_stable_stack(checkout, session, web):
    # (55, 56) Worker y backend arriba, sin beat ni contenedores sueltos; la base y Redis sanos.
    act(checkout, session, "start-official")
    fake = FakeDocker(beat=web == "beat")
    if web == "worker-missing":
        fake.containers = [c for c in fake.containers if c["service"] != "worker"]
    elif web == "oneoff":
        fake.containers.append(container(f"{PROJECT}-backend-run-x", "running", "backend", True))
    elif web == "db-unhealthy":
        fake.containers[0]["healthy"] = False
    summary, _ = act(checkout, session, "execute-official", fake=fake)
    assert summary["classification"] == "VALIDATION_FAILED" and fake.runs() == []


@pytest.mark.parametrize(
    ("payload", "problem"),
    [
        ({**EXECUTE_OK, "run_id": OTHER_RUN}, "run-id-differs"),
        ({**EXECUTE_OK, "phase": "DRAINING"}, "execute:phase"),
        ({**EXECUTE_OK, "extra": 1}, "payload-keys"),
    ],
)
def test_an_incoherent_execute_output_does_not_advance_the_session(
    checkout, session, payload, problem
):
    # (52, 57, 61)
    act(checkout, session, "start-official")
    summary, _ = act(checkout, session, "execute-official", stdout=command_json(payload))
    assert summary["launcher_exit"] == 4 and problem in summary["problems"]
    state = manifest(session)["state"]
    assert state["known_phase"] == "PREPARED" and state["uncertain"] is True


@pytest.mark.parametrize("kind", ["timeout", "interrupted"])
def test_timeouts_and_signals_abort_clean_up_and_never_complete(checkout, session, kind):
    # (58, 59) APPARATUS_ABORT, el contenedor exacto se quita y la acción no queda hecha.
    act(checkout, session, "start-official")
    fake = FakeDocker()
    fake.abort, fake.leave_container = kind, True
    summary, _ = act(checkout, session, "execute-official", fake=fake)
    assert summary["classification"] == "APPARATUS_ABORT" and summary["launcher_exit"] == 5
    removed = [c for c in fake.calls if c[:3] == ["docker", "rm", "-f"]]
    assert len(removed) == 1 and removed[0][3].startswith(f"{PROJECT}-execute-official-")
    assert not any(c["oneoff"] for c in fake.containers)
    assert manifest(session)["state"]["known_phase"] == "PREPARED"


def test_execute_stopped_before_t0_only_allows_the_official_abandon(checkout, session):
    act(checkout, session, "start-official")
    act(checkout, session, "execute-official", stdout=command_json(STOPPED_OK))
    state = manifest(session)["state"]
    assert state["known_phase"] == "STOPPED_PRE_T0"
    assert state["next_allowed_actions"] == ["abandon-official-pre-t0", "inspect", "status"]
    summary, fake = act(checkout, session, "abandon-official-pre-t0")
    assert summary["classification"] == "COHERENT"
    assert tail(fake.runs()[0])[-2:] == ["--abandon-reason", "operator-stop"]
    assert manifest(session)["state"]["known_phase"] == "ABANDONED_PRE_T0"


# --- DRAIN, RECONCILE y FINALIZE (63–75) ---------------------------------------------------------


def test_drain_only_after_an_execute_and_with_the_run_in_draining(checkout, session):
    # (63–65) `execute` ya drena: `drain` es la recuperación de un drain interrumpido.
    act(checkout, session, "start-official")
    summary, fake = act(checkout, session, "drain")
    assert summary["problems"] == ["action-not-allowed-now:drain"] and fake.runs() == []
    fake = FakeDocker()
    fake.abort = "interrupted"
    act(checkout, session, "execute-official", fake=fake)
    assert act(checkout, session, "drain")[0]["problems"] == ["action-not-allowed-now:drain"]
    act(checkout, session, "status", stdout=command_json(status_payload("DRAINING")))
    assert "drain" in manifest(session)["state"]["next_allowed_actions"]
    summary, fake = act(checkout, session, "drain")
    assert summary["classification"] == "COHERENT"
    assert tail(fake.runs()[0])[3:] == [
        "drain",
        "--run-id",
        RUN_ID,
        "--expected-git-sha",
        checkout.head,
    ]  # noqa: E501
    assert act(checkout, session, "drain")[0]["problems"] == ["action-not-allowed-now:drain"]
    assert manifest(session)["state"]["next_allowed_actions"] == ["inspect", "reconcile", "status"]


def test_reconcile_after_execute_once_and_never_before(checkout, session):
    # (66–68)
    act(checkout, session, "start-official")
    assert act(checkout, session, "reconcile")[0]["problems"] == [
        "action-not-allowed-now:reconcile"
    ]  # noqa: E501
    act(checkout, session, "execute-official")
    summary, fake = act(checkout, session, "reconcile")
    assert summary["classification"] == "COHERENT"
    assert tail(fake.runs()[0])[3:] == [
        "reconcile",
        "--run-id",
        RUN_ID,
        "--expected-git-sha",
        checkout.head,
    ]  # noqa: E501
    assert act(checkout, session, "reconcile")[0]["problems"] == [
        "action-not-allowed-now:reconcile"
    ]  # noqa: E501
    assert manifest(session)["state"]["next_allowed_actions"] == ["finalize", "inspect", "status"]


def test_reconcile_never_touches_a_run_other_than_the_sessions(checkout, session):
    # `reconcile` sólo puede tocar el run de la sesión (el dominio lo abre con `load_state`);
    # cualquier otro run debe quedar idéntico.
    make_run(checkout, OTHER_RUN)
    to_reconciling(checkout, session)
    fake = _fake_with(lambda argv: os.utime(run_dir(checkout) / "metadata"))
    assert act(checkout, session, "reconcile", fake=fake)[0]["classification"] == "COHERENT"
    session2 = checkout.home / "s-other"
    official.execute(invocation(checkout, session2, FakeDocker(), "initialize-session"))
    fake = _fake_with(lambda argv: os.utime(run_dir(checkout, OTHER_RUN) / "metadata"))
    data = manifest(session)
    assert data["state"]["next_allowed_actions"] == ["finalize", "inspect", "status"]
    summary, _ = act(checkout, session, "finalize", fake=fake)
    assert "prior-run-changed" in summary["problems"]


def test_finalize_after_reconcile_with_the_same_decision(checkout, session):
    # (69, 70, 74)
    to_reconciling(checkout, session)
    assert act(checkout, session, "finalize")[0]["problems"] == ["action-not-allowed-now:finalize"]
    act(checkout, session, "reconcile")
    summary, fake = act(checkout, session, "finalize")
    assert summary["classification"] == "COHERENT"
    assert tail(fake.runs()[0])[3:] == [
        "finalize",
        "--run-id",
        RUN_ID,
        "--expected-git-sha",
        checkout.head,
    ]  # noqa: E501
    state = manifest(session)["state"]
    assert state["known_phase"] == "FINALIZED_PASS"
    assert state["next_allowed_actions"] == ["cleanup-stack", "inspect", "status"]


@pytest.mark.parametrize(
    ("payload", "problem"),
    [
        (
            {**FINAL_OK, "decision": "FAIL", "lifecycle": "FINALIZED_FAIL"},
            "finalize:decision-differs-from-reconciliation",
        ),  # noqa: E501
        ({**FINAL_OK, "lifecycle": "FINALIZED_FAIL"}, "finalize:lifecycle"),
        ({**FINAL_OK, "binding": False}, "finalize:binding"),
        ({**FINAL_OK, "custody_errors": ["sha256:x"]}, "finalize:custody"),
        ({**FINAL_OK, "run_id": OTHER_RUN}, "run-id-differs"),
    ],
)
def test_a_finalize_output_that_contradicts_the_reconciliation_is_a_failure(
    checkout, session, payload, problem
):
    # (71)
    to_reconciling(checkout, session)
    act(checkout, session, "reconcile")
    summary, _ = act(checkout, session, "finalize", stdout=command_json(payload))
    assert summary["launcher_exit"] == 4 and problem in summary["problems"]


def test_an_interrupted_finalize_resumes_once_and_a_finalized_run_never_again(checkout, session):
    # (72, 73) Un finalize incierto: `status` en DECIDED permite retomarlo (misma decisión);
    # tras el sello, ninguna finalización más.
    to_reconciling(checkout, session)
    act(checkout, session, "reconcile")
    fake = FakeDocker()
    fake.abort = "interrupted"
    act(checkout, session, "finalize", fake=fake)
    assert manifest(session)["state"]["next_allowed_actions"] == ["inspect", "status"]
    act(checkout, session, "status", stdout=command_json(status_payload("DECIDED")))
    assert "finalize" in manifest(session)["state"]["next_allowed_actions"]
    assert act(checkout, session, "finalize")[0]["classification"] == "COHERENT"
    summary, fake = act(checkout, session, "finalize")
    assert summary["problems"] == ["action-not-allowed-now:finalize"] and fake.runs() == []


def test_no_action_ever_chains_another(checkout, session):
    # (30, 44, 62, 75) Cada invocación lanza exactamente un contenedor.
    for action in ("start-official", "execute-official", "reconcile", "finalize", "status"):
        _, fake = act(checkout, session, action)
        assert len(fake.runs()) == 1


def test_there_is_no_option_to_choose_the_decision(checkout):
    # (74)
    assert all(a not in official.ACTIONS for a in ("force-pass", "force-fail", "retry"))
    with pytest.raises(base.UsageError):
        official.parse_args(["finalize", "--expected-git-sha", checkout.head, "--session-root",
                             "/x", "--confirm-finalization", "--decision", "PASS"])  # fmt: skip


# --- STATUS e INSPECT (76–83) -------------------------------------------------------------------


def test_status_uses_the_bound_run_and_changes_nothing(checkout, session):
    # (76, 83)
    act(checkout, session, "start-official")
    summary, fake = act(checkout, session, "status")
    assert summary["classification"] == "COHERENT"
    assert tail(fake.runs()[0])[3:] == ["status", "--run-id", RUN_ID]
    fake = _fake_with(lambda argv: os.utime(run_dir(checkout) / "metadata"))
    summary, _ = act(checkout, session, "status", fake=fake)
    assert "evidence-changed-by-read" in summary["problems"]


def test_status_without_a_bound_run_is_not_allowed(checkout, session):
    # (78)
    summary, fake = act(checkout, session, "status")
    assert summary["problems"] == ["action-not-allowed-now:status"] and fake.runs() == []


def test_a_run_session_mismatch_blocks_for_good(checkout, session):
    # (80) La sesión dice RECONCILING y el run dice otra cosa: bloqueo, nunca reparación.
    to_reconciling(checkout, session)
    summary, _ = act(checkout, session, "status", stdout=command_json(status_payload("PREPARED")))
    assert "session-run-mismatch" in summary["problems"]
    state = manifest(session)["state"]
    assert state["blocked"] and state["next_allowed_actions"] == ["inspect"]


def test_inspect_is_host_only_read_only_and_derives_the_next_action(checkout, session):
    # (77, 81, 83) Nada se lanza ni se escribe.
    act(checkout, session, "start-official")
    before = {p: p.stat().st_mtime_ns for p in session.rglob("*")}
    fake = FakeDocker()
    summary = official.execute(invocation(checkout, session, fake, "inspect"))
    assert summary["classification"] == "COHERENT" and fake.calls == []
    assert summary["state"]["next_allowed_actions"] == [
        "abandon-official-pre-t0", "execute-official", "inspect", "status",
    ]  # fmt: skip
    assert {p: p.stat().st_mtime_ns for p in session.rglob("*")} == before


def test_unknown_lifecycle_states_are_failures(checkout, session):
    # (82)
    act(checkout, session, "start-official")
    summary, _ = act(checkout, session, "status", stdout=command_json(status_payload("WEIRD")))
    assert summary["launcher_exit"] == 4 and "status:phase" in summary["problems"]


# --- Limpieza --------------------------------------------------------------------------------


def test_cleanup_stack_is_down_only_without_volumes_images_or_prune(checkout, session):
    # (97, 98)
    summary, fake = act(checkout, session, "cleanup-stack")
    assert summary["classification"] == "COHERENT"
    (call,) = (c for c in fake.calls if "down" in c)
    assert call[call.index("down") :] == ["down", "--remove-orphans"]
    assert not {"-v", "--volumes", "--rmi", "prune", "local"} & set(call)


def test_cleanup_stack_is_not_allowed_with_an_open_run(checkout, session):
    act(checkout, session, "start-official")
    summary, fake = act(checkout, session, "cleanup-stack")
    assert summary["problems"] == ["action-not-allowed-now:cleanup-stack"] and fake.calls == []


# --- Seguridad (84–100) ---------------------------------------------------------------------------


def test_secrets_never_reach_argv_outputs_or_the_session_record(checkout, session):
    # (84–87, 94)
    to_reconciling(checkout, session)
    act(checkout, session, "reconcile")
    _, fake = act(checkout, session, "finalize")
    values = list(checkout.keys.values())
    for call in fake.calls:
        assert not any(v in token for token in call for v in values)
    for path in session.rglob("*"):
        if path.is_file():
            text = path.read_text(errors="replace")
            assert not any(v in text for v in values)
    assert sorted(fake.envs[0]) == sorted({"HOME", "PATH", "S11_HOST_UID", "S11_HOST_GID",
                                           *base.INTERPOLATION_ENV})  # fmt: skip


@pytest.mark.parametrize(
    "line",
    [
        b"Authorization: Bearer abc",
        b"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig",
        b"Content-Disposition: form-data; name=photo",
        b"iVBORw0KGgoAAAANSUhEUg",
        b'{"latitude": -25.3, "longitude": -57.6}',
        b"postgres://user:pass@db/x",
        b"DJANGO_SECRET_KEY=abc",
    ],
    ids=["authorization", "jwt", "multipart", "image", "coordinates", "dsn", "env-dump"],
)
def test_sensitive_stderr_is_redacted(checkout, session, line):
    # (86, 88–94) Sólo líneas de Compose y códigos del command sobreviven.
    act(checkout, session, "start-official")
    summary, _ = act(checkout, session, "status", stderr=line + b"\n")
    output = session / manifest(session)["history"][-1]["output_dir"]
    text = (output / "status.stderr").read_text()
    assert line.decode() not in text and "[redacted-line]" in text


def test_a_secret_in_the_command_output_is_a_failure_and_is_not_persisted(checkout, session):
    act(checkout, session, "start-official")
    leaked = {**status_payload("PREPARED"), "artifacts": [checkout.keys["S11_CUSTODY_HMAC_KEY"]]}
    summary, _ = act(checkout, session, "status", stdout=command_json(leaked))
    assert summary["launcher_exit"] == 4
    output = session / manifest(session)["history"][-1]["output_dir"]
    assert "status.json" not in os.listdir(output)


def test_project_and_container_names_are_official_and_exact(checkout, session):
    # (96)
    _, fake = act(checkout, session, "start-official")
    argv = fake.runs()[0]
    name = argv[argv.index("--name") + 1]
    assert argv[2:4] == ["-p", PROJECT] and name.startswith(f"{PROJECT}-start-official-")
    assert PROJECT not in ("ciudadania-s11-ex10-lifecycle-rehearsal", base.PROJECT)


def test_the_cli_maps_errors_to_codes_without_values(checkout, capsys):
    # (99)
    assert (
        official.main(
            ["status", "--expected-git-sha", "x", "--session-root", "/x", "--bogus"], environ={}
        )
        == 3
    )  # noqa: E501
    assert "bogus" not in capsys.readouterr().err


# --- Integración con el executor real (custodia temporal, OFFICIAL de test) -----------------------


@pytest.fixture
def domain(monkeypatch):
    """Checkout sintético cuyo `var/s11-runs` es una custodia real del executor (bajo
    `.s11-test-artifacts`: la custodia rechaza /tmp) y un Docker simulado que ejecuta
    `s11_ex10` en proceso. Las claves y el backend son los del harness del executor."""
    import contextlib as contextlib_module
    import io

    from django.core.management import call_command
    from django.core.management.base import CommandError

    from apps.experiments_app import ex10_load
    from apps.experiments_app.evidence import EvidenceStore
    from apps.experiments_app.management.commands import s11_ex10
    from apps.experiments_app.tests import ex10_executor_support as support

    parent = support.ARTIFACTS / f"official-integration-{os.getpid()}-{next(_TOKENS)}"
    parent.mkdir(parents=True, mode=0o700)
    repo = make_checkout(parent, monkeypatch)
    keys = {
        "S11_CUSTODY_HMAC_KEY": secrets.token_hex(32),
        "S11_PUBLICATION_APPROVAL_PUBLIC_KEY": secrets.token_hex(32),
        "S11_DOCUMENT_HMAC_SECRET": secrets.token_hex(32),
    }
    custody = hashlib.sha256(keys["S11_CUSTODY_HMAC_KEY"].encode()).hexdigest()[:16]
    public = bytes.fromhex(keys["S11_PUBLICATION_APPROVAL_PUBLIC_KEY"])
    monkeypatch.setattr(official, "OFFICIAL_CUSTODY_EPOCH", custody)
    monkeypatch.setattr(
        official, "OFFICIAL_PUBLICATION_KEY_ID", hashlib.sha256(public).hexdigest()[:16]
    )
    repo.keys = keys
    store = EvidenceStore(
        repo.root / "var" / "s11-runs",
        custody_hmac_key=support.HMAC_KEY,
        publication_verification_key=support.PUBLIC_KEY,
        rolling_checksums=False,
    )
    harness = support.make_harness(
        f"official-integration-{next(_TOKENS)}", ex10_load.load_dataset()
    )
    # El backend de test registra sus eventos HTTP/Celery en la misma custodia del command.
    harness.backend.store = store
    monkeypatch.setattr(s11_ex10.Command, "_store", staticmethod(lambda: store))
    monkeypatch.setattr(s11_ex10.Command, "probes", lambda self: harness.probes)

    def command(fake):
        def run(argv):
            args = tail(argv)[3:]
            out, err = io.StringIO(), io.StringIO()
            code, footer = 0, ""
            with contextlib_module.redirect_stderr(err):
                try:
                    call_command("s11_ex10", *args, stdout=out)
                except CommandError as exc:
                    code, footer = exc.returncode, f"CommandError: {exc}\n"
                except SystemExit:
                    # El executor muere dentro del contenedor: para el launcher, un abort.
                    raise base.Aborted("interrupted") from None
            fake.result = base.Completed(
                code, out.getvalue().encode(), (err.getvalue() + footer).encode()
            )  # noqa: E501

        return run

    root = repo.home / "official-session"

    def go(action, session_root=None, **changes):
        fake = FakeDocker()
        fake.on_run = command(fake)
        fake.result = base.Completed(0, b"", b"")
        values = {"clock": lambda: datetime.now(UTC)}
        if action == "start-official":
            report = support.preflight_report(git_sha=repo.head)
            values["preflight_report"] = write_report(repo.home, report)
            values["nonce"] = f"nonce-official-{next(_TOKENS)}"
        values.update(changes)
        return official.execute(invocation(repo, session_root or root, fake, action, **values))

    official.execute(invocation(repo, root, FakeDocker(), "initialize-session"))
    yield SimpleNamespace(
        repo=repo, store=store, go=go, root=root, harness=harness, monkeypatch=monkeypatch
    )
    support.close_harness(harness)
    shutil.rmtree(parent, ignore_errors=True)


def test_the_official_lifecycle_through_the_real_executor(domain):
    # START → status → execute (emisión y drain) → reconcile → finalize: una acción por
    # invocación; la decisión es la del executor y queda vinculante; un segundo START oficial
    # en la misma custodia lo rechaza el dominio (primer run válido).
    start = domain.go("start-official")
    assert start["classification"] == "COHERENT", start
    run_id = start["official_run_id"]
    assert domain.go("status")["state"]["known_phase"] == "PREPARED"
    executed = domain.go("execute-official")
    assert executed["classification"] == "COHERENT", executed
    assert executed["state"]["known_phase"] == "RECONCILING"
    reconciled = domain.go("reconcile")
    assert reconciled["classification"] == "COHERENT", reconciled["problems"]
    final = domain.go("finalize")
    assert final["classification"] == "COHERENT", final
    output = domain.root / manifest(domain.root)["history"][-1]["output_dir"]
    reasons = json.loads((output / "finalize.json").read_text())["reasons"]
    assert final["state"]["known_phase"] == "FINALIZED_PASS", reasons
    data = manifest(domain.root)
    assert data["official_run_id"] == run_id
    assert [e["action"] for e in data["history"]] == [
        "start-official", "status", "execute-official", "reconcile", "finalize",
    ]  # fmt: skip
    output = domain.root / data["history"][-1]["output_dir"]
    assert json.loads((output / "finalize.json").read_text())["binding"] is True
    second = domain.repo.home / "second-session"
    official.execute(invocation(domain.repo, second, FakeDocker(), "initialize-session"))
    refused = domain.go("start-official", session_root=second)
    assert refused["classification"] == "REFUSED", refused["problems"]
    assert refused["launcher_exit"] == 2


def test_an_official_abandon_before_t0_through_the_real_executor(domain):
    start = domain.go("start-official")
    assert start["classification"] == "COHERENT", start
    abandoned = domain.go("abandon-official-pre-t0")
    assert abandoned["classification"] == "COHERENT", abandoned
    assert abandoned["state"]["known_phase"] == "ABANDONED_PRE_T0"
    output = domain.root / manifest(domain.root)["history"][-1]["output_dir"]
    payload = json.loads((output / "abandon-official-pre-t0.json").read_text())
    assert payload["binding"] is False and payload["decision"] == "NOT_DEMONSTRATED"


def test_signal_handlers_map_sigterm_and_sighup_to_an_interrupt():
    # (59) Igual que el launcher base: SIGTERM/SIGHUP son una interrupción del operador.
    assert official.base._interrupt is not None
    with pytest.raises(KeyboardInterrupt):
        official.base._interrupt(signal.SIGTERM, None)


# --- Cierre oficial de un executor caído (declare-executor-gone) -------------------------------

GONE_OK = {
    **FINAL_OK, "decision": "NOT_DEMONSTRATED", "lifecycle": "NOT_DEMONSTRATED",
    "binding": False, "reasons": ["emission-interrupted:raw-missing"],
    "manifest_status": "INVALID",
}  # fmt: skip
OUTPUT["declare-executor-gone"] = GONE_OK


def crashed(checkout, root, phase, *, leave=None):
    """START, un `execute` que muere (abort) y un `status` que lee `phase`."""
    act(checkout, root, "start-official")
    fake = FakeDocker()
    fake.abort = "interrupted"
    act(checkout, root, "execute-official", fake=fake)
    act(checkout, root, "status", stdout=command_json(status_payload(phase)))
    return manifest(root)["history"][1]["container_name"]


def docker_with(name=None, state=None):
    fake = FakeDocker()
    if name is not None:
        fake.containers.append(container(name, state, "backend", oneoff=True))
    return fake


@pytest.mark.parametrize("phase", ["STARTED", "EMITTING"])
@pytest.mark.parametrize("left", [None, "exited", "dead"])
def test_a_dead_executor_in_started_or_emitting_can_be_declared_gone(
    checkout, session, phase, left
):
    # (1, 2, 25, 29, 30) Sólo un comando, el exacto; el dominio decide (NOT_DEMONSTRATED).
    name = crashed(checkout, session, phase)
    assert "declare-executor-gone" in manifest(session)["state"]["next_allowed_actions"]
    fake = docker_with(name if left else None, left)
    summary, fake = act(checkout, session, "declare-executor-gone", fake=fake)
    assert summary["classification"] == "COHERENT"
    (argv,) = fake.runs()
    assert tail(argv)[3:] == ["finalize", "--run-id", RUN_ID, "--expected-git-sha",
                              checkout.head, "--declare-executor-gone"]  # fmt: skip
    mounts = [argv[i + 1] for i, token in enumerate(argv) if token == "-v"]
    assert mounts == [f"{checkout.root}:/s11-full-source:ro"]
    state = manifest(session)["state"]
    assert state["known_phase"] == "NOT_DEMONSTRATED"
    assert state["next_allowed_actions"] == ["cleanup-stack", "inspect", "status"]
    entry = manifest(session)["history"][-1]
    output = session / entry["output_dir"]
    assert json.loads((output / "launcher-meta.json").read_text())["executor_container"] == {
        "name": name, "state": left or "absent",
    }  # fmt: skip


@pytest.mark.parametrize("state", sorted(official.ACTIVE_CONTAINER_STATES))
def test_a_still_active_executor_container_is_refused(checkout, session, state):
    # (3, 4) running/created/restarting/paused: el launcher no lo mata ni sigue.
    name = crashed(checkout, session, "STARTED")
    summary, fake = act(checkout, session, "declare-executor-gone", fake=docker_with(name, state))
    assert summary["classification"] == "VALIDATION_FAILED" and fake.runs() == []
    assert summary["problems"] == ["executor-still-active"]
    assert not [c for c in fake.calls if c[:3] == ["docker", "rm", "-f"]]


def test_a_container_with_a_similar_name_is_not_the_executor(checkout, session):
    # Nunca por nombre parcial.
    name = crashed(checkout, session, "STARTED")
    fake = docker_with(name + "-x", "exited")
    assert (
        act(checkout, session, "declare-executor-gone", fake=fake)[0]["classification"]
        == "COHERENT"
    )


@pytest.mark.parametrize("phase", ["PREPARED", "DRAINING", "RECONCILING", "STOPPED_PRE_T0"])
def test_declare_executor_gone_is_refused_outside_started_or_emitting(checkout, session, phase):
    # (5–7) Ni PREPARED, ni DRAINING (para eso está `drain`), ni otras fases.
    crashed(checkout, session, phase)
    summary, fake = act(checkout, session, "declare-executor-gone")
    assert summary["problems"] == ["action-not-allowed-now:declare-executor-gone"]
    assert fake.runs() == []


def test_declare_executor_gone_needs_an_executed_bound_run(checkout, session):
    # (10) Sin run ligado o sin `execute`: no hay executor que declarar caído.
    summary, fake = act(checkout, session, "declare-executor-gone")
    assert summary["problems"] == ["action-not-allowed-now:declare-executor-gone"]
    act(checkout, session, "start-official")
    act(checkout, session, "status", stdout=command_json(status_payload("PREPARED")))
    summary, fake = act(checkout, session, "declare-executor-gone")
    assert summary["problems"] == ["action-not-allowed-now:declare-executor-gone"]


def test_declare_executor_gone_needs_its_own_confirmation_and_no_run_id(checkout):
    # (8, 9, 11) Sin su confirmación, con la de otra acción, con `--run-id` o eligiendo un
    # resultado: error de uso antes de tocar nada.
    args = ["declare-executor-gone", "--expected-git-sha", checkout.head, "--session-root", "/x"]
    assert official.parse_args([*args, "--confirm-declare-executor-gone"]).confirmed is True
    with pytest.raises(base.UsageError):
        official.validate_invocation(official.Invocation(
            action="declare-executor-gone", expected_git_sha=checkout.head,
            repo_root=checkout.root, session_root=Path("/x"), environ={}, confirmed=False,
        ))  # fmt: skip
    confirm = "--confirm-declare-executor-gone"
    extras = (
        ["--confirm-finalization"],
        ["--confirm-emission"],
        ["--confirm-drain"],
        [confirm, "--run-id", RUN_ID],
        [confirm, "--decision", "NOT_DEMONSTRATED"],
        [confirm, "--abandon-reason", "operator-stop"],
    )
    for extra in extras:
        with pytest.raises(base.UsageError):
            official.parse_args([*args, *extra])


def test_declare_executor_gone_refuses_another_head_or_a_dirty_tree(checkout, session):
    # (12, 13)
    crashed(checkout, session, "STARTED")
    summary, fake = act(checkout, session, "declare-executor-gone", expected_git_sha="e" * 40)
    assert summary["problems"] == ["session-other-head"] and fake.runs() == []
    (checkout.root / "infra" / "docker-compose.yml").write_text("dirty\n")
    summary, fake = act(checkout, session, "declare-executor-gone")
    assert summary["classification"] == "VALIDATION_FAILED" and fake.runs() == []


@pytest.mark.parametrize("what", ["history", "execute-output"])
def test_declare_executor_gone_refuses_a_tampered_session(checkout, session, what):
    # (14, 15)
    crashed(checkout, session, "STARTED")
    data = manifest(session)
    if what == "history":
        data["history"][1]["container_name"] = "something-else"
        official.write_session(session, data)
    else:
        output = session / data["history"][1]["output_dir"]
        (output / "launcher-meta.json").write_text("{}")
    fake = FakeDocker()
    with pytest.raises(official.SessionTampered):
        act(checkout, session, "declare-executor-gone", fake=fake)
    assert fake.calls == []


@pytest.mark.parametrize(
    ("payload", "problem"),
    [
        ({**GONE_OK, "run_id": OTHER_RUN}, "run-id-differs"),
        ({**GONE_OK, "decision": "PASS", "lifecycle": "FINALIZED_PASS"}, "executor-gone:lifecycle"),
        ({**GONE_OK, "binding": True}, "executor-gone:binding"),
        ({**GONE_OK, "reasons": ["operator-chose"]}, "executor-gone:reasons"),
        ({**GONE_OK, "extra": 1}, "payload-keys"),
    ],
)
def test_an_incoherent_declare_output_does_not_advance_the_session(
    checkout, session, payload, problem
):
    # (16–18) La sesión no avanza: queda incierta, sólo `status`/`inspect`.
    crashed(checkout, session, "EMITTING")
    summary, _ = act(checkout, session, "declare-executor-gone", stdout=command_json(payload))
    assert summary["launcher_exit"] == 4 and problem in summary["problems"]
    state = manifest(session)["state"]
    assert state["known_phase"] == "EMITTING" and state["next_allowed_actions"] == [
        "inspect",
        "status",
    ]


def test_a_refused_declare_keeps_its_exit_code(checkout, session):
    crashed(checkout, session, "STARTED")
    summary, _ = act(checkout, session, "declare-executor-gone", code=2,
                     stderr=refusal(2, ["run-finalized"]))  # fmt: skip
    assert summary["classification"] == "REFUSED" and summary["launcher_exit"] == 2
    # Una sola declaración por sesión, aunque el dominio la rechace.
    summary, fake = act(checkout, session, "declare-executor-gone")
    assert summary["problems"] == ["action-not-allowed-now:declare-executor-gone"]
    assert fake.runs() == []


@pytest.mark.parametrize("kind", ["timeout", "interrupted"])
def test_declare_executor_gone_aborts_without_retry(checkout, session, kind):
    # (19, 20, 22) APPARATUS_ABORT: un único intento; sólo `status`/`inspect` después.
    crashed(checkout, session, "STARTED")
    fake = FakeDocker()
    fake.abort = kind
    summary, fake = act(checkout, session, "declare-executor-gone", fake=fake)
    assert summary["classification"] == "APPARATUS_ABORT" and len(fake.runs()) == 1
    assert manifest(session)["state"]["next_allowed_actions"] == ["inspect", "status"]


def test_declare_executor_gone_runs_once_and_never_chains(checkout, session):
    # (21, 23) Una vez cerrado, nunca otra declaración, ni `execute`, `drain` o `finalize`.
    crashed(checkout, session, "EMITTING")
    _, fake = act(checkout, session, "declare-executor-gone")
    assert len(fake.runs()) == 1
    for action in ("declare-executor-gone", "execute-official", "drain", "finalize"):
        summary, fake = act(checkout, session, action)
        assert summary["problems"] == [f"action-not-allowed-now:{action}"] and fake.runs() == []


def test_declare_executor_gone_keeps_secrets_out_of_argv_and_outputs(checkout, session):
    # (24, 26)
    crashed(checkout, session, "STARTED")
    _, fake = act(checkout, session, "declare-executor-gone")
    values = list(checkout.keys.values())
    assert not any(v in token for call in fake.calls for token in call for v in values)
    assert not any(word in token.lower() for token in tail(fake.runs()[0])
                   for word in ("local_test", "local-test", "rehearsal"))  # fmt: skip
    for path in session.rglob("*"):
        if path.is_file():
            assert not any(v in path.read_text(errors="replace") for v in values)


def test_a_launcher_that_died_during_execute_leaves_an_in_flight_record(checkout, session):
    # El registro previo al lanzamiento conserva el contenedor exacto de `execute`: si el
    # launcher muere, la sesión queda incierta y la declaración usa ese nombre.
    act(checkout, session, "start-official")
    fake = FakeDocker()

    def launcher_dies(argv):
        raise KeyboardInterrupt  # el proceso del launcher muere sin registrar el resultado

    fake.on_run = launcher_dies
    fake.result = base.Completed(0, b"", b"")
    with pytest.raises(KeyboardInterrupt):
        official.execute(invocation(checkout, session, fake, "execute-official"))
    data = manifest(session)
    (record,) = data["in_flight"]
    assert record["action"] == "execute-official" and record["container_name"].startswith(
        f"{PROJECT}-execute-official-"
    )
    assert data["state"]["uncertain"] and data["state"]["next_allowed_actions"] == [
        "inspect",
        "status",
    ]
    act(checkout, session, "status", stdout=command_json(status_payload("EMITTING")))
    assert "declare-executor-gone" in manifest(session)["state"]["next_allowed_actions"]
    summary, _ = act(checkout, session, "declare-executor-gone",
                     fake=docker_with(record["container_name"], "running"))  # fmt: skip
    assert summary["problems"] == ["executor-still-active"]
    summary, _ = act(checkout, session, "declare-executor-gone")
    assert summary["classification"] == "COHERENT"


@pytest.mark.parametrize("phase", ["STARTED", "EMITTING"])
def test_declare_executor_gone_through_the_real_executor(domain, phase):
    # (27–29) El executor muere de verdad en STARTED o EMITTING; la declaración la cierra el
    # dominio como NOT_DEMONSTRATED, no vinculante, con su custodia completa.
    from apps.experiments_app import ex10_executor as executor

    assert domain.go("start-official")["classification"] == "COHERENT"
    if phase == "STARTED":
        domain.monkeypatch.setattr(
            executor, "_arm_emission", lambda *a, **k: (_ for _ in ()).throw(SystemExit(137))
        )
    else:
        from apps.experiments_app.tests.test_ex10_executor import _DiesOnRun

        domain.harness.set_probes(generator=lambda plan, prepared, transport: _DiesOnRun())
    assert domain.go("execute-official")["classification"] == "APPARATUS_ABORT"
    status = domain.go("status")
    assert status["state"]["known_phase"] == phase
    closed = domain.go("declare-executor-gone")
    assert closed["classification"] == "COHERENT", closed["problems"]
    assert closed["state"]["known_phase"] == "NOT_DEMONSTRATED"
    output = domain.root / manifest(domain.root)["history"][-1]["output_dir"]
    payload = json.loads((output / "declare-executor-gone.json").read_text())
    assert payload["binding"] is False and payload["custody_errors"] == []
    expected = {"STARTED": "emission-interrupted:before-arming",
                "EMITTING": "emission-interrupted:raw-missing"}[phase]  # fmt: skip
    assert expected in payload["reasons"]
    assert (
        executor.run_status(domain.store, closed["official_run_id"])["phase"] == "NOT_DEMONSTRATED"
    )
