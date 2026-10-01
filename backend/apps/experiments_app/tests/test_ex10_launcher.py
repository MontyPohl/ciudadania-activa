"""EX-10 fase 3B: launcher de host del lifecycle LOCAL_TEST. LOCAL TEST / NON-EXPERIMENTAL.

Checkouts Git sintéticos, sesiones de ensayo reales en `tmp_path` y un Docker simulado en
proceso. Ningún test lanza Docker, lee `.env`, usa el stack S11 ni toca `var/`.
"""

import fcntl
import hashlib
import importlib.util
import inspect
import json
import os
import signal
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.experiments_app import ex10_executor

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "s11_ex10_launcher.py"
_SPEC = importlib.util.spec_from_file_location("s11_ex10_launcher", SCRIPT)
launcher = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = launcher
_FLAG, sys.dont_write_bytecode = sys.dont_write_bytecode, True
try:
    _SPEC.loader.exec_module(launcher)
finally:
    sys.dont_write_bytecode = _FLAG
base = launcher.base

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
PROJECT = launcher.PROJECT
RUN_ID = "s11-33b0d2a4-ad98-5b8e-b48d-31701977365f"
RUN_B = "s11-44c1d2a4-ad98-5b8e-b48d-31701977365f"
NONCE = "nonce-rehearsal-0001"
SHA256 = "a" * 64


def git(root, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """Checkout completo con los archivos exigidos y G0/G0-A1 en la historia."""
    root = Path(os.path.realpath(tmp_path)) / "repo"
    for relative in {*base.REQUIRED_FILES, *launcher.REQUIRED_FILES}:
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
    home = Path(os.path.realpath(tmp_path)) / "home"
    home.mkdir(mode=0o700)
    (root / "var").mkdir()
    return SimpleNamespace(root=root, head=git(root, "rev-parse", "HEAD"), home=home)


@pytest.fixture
def session(checkout):
    return launcher.write_session(checkout.home / "s11-ex10-lifecycle-rehearsal-test")


def preflight_payload(sha, **changes):
    payload = {
        **launcher._PREFLIGHT_EXPECTED,
        "git_sha": sha,
        "expected_git_sha": sha,
        "checked_at": (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "checks": [
            {
                "check": "health",
                "result": "PASS",
                "expected": None,
                "observed_sanitized": None,
                "detail_code": None,
            }
        ],
    }
    payload.update(changes)
    return payload


def write_report(home, payload, name="preflight.json", mode=0o600):
    path = home / name
    path.write_text(json.dumps(payload))
    path.chmod(mode)
    return path


# --- Docker simulado ---------------------------------------------------------------------------


def container(name, state, service, oneoff=False, healthy=False):
    return {"name": name, "state": state, "service": service, "oneoff": oneoff, "healthy": healthy}


def command_json(payload):
    return (json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n").encode()


START_OK = {
    "run_id": RUN_ID,
    "scenario_id": "EX-10",
    "evidence_mode": "LOCAL_TEST",
    "phase": "PREPARED",
    "manifest_sha256": SHA256,
    "start_sha256": SHA256,
    "preflight_sha256": SHA256,
}
STARTED_OK = {
    "run_id": RUN_ID,
    "evidence_mode": "LOCAL_TEST",
    "phase": "STARTED",
    "emission_armed": False,
    "t0_published": False,
    "tokens_issued": 0,
    "requests_prepared": 0,
    "requests_sent": 0,
}
ABANDON_OK = {
    "run_id": RUN_ID,
    "decision": "NOT_DEMONSTRATED",
    "lifecycle": "ABANDONED_PRE_T0",
    "binding": False,
    "reasons": ["abandoned-before-t0:rehearsal-complete"],
    "decision_sha256": SHA256,
    "manifest_status": "INVALID",
    "manifest_sha256": SHA256,
    "custody_errors": [],
}
STATUS_OK = {
    "run_id": RUN_ID,
    "exists": True,
    "scenario_id": "EX-10",
    "phase": "STARTED",
    "finalized": False,
    "artifacts": ["ex10-emission"],
    "decision": None,
    "custody_errors": [],
}
OUTPUT = {
    "start-local-test": START_OK,
    "rehearsal-started": STARTED_OK,
    "abandon-pre-t0": ABANDON_OK,
    "status": STATUS_OK,
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


def environ(home, **changes):
    values = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    values.update(changes)
    return {k: v for k, v in values.items() if v is not None}


_TOKENS = iter(f"{n:08x}" for n in range(1, 10_000))


def invocation(checkout, session, fake, action, **changes):
    values = {
        "action": action,
        "expected_git_sha": checkout.head,
        "repo_root": checkout.root,
        "session_root": session.root,
        "environ": environ(checkout.home),
        "runner_factory": fake.factory,
        "clock": lambda: NOW,
        "token": lambda: next(_TOKENS),
    }
    if action == "start-local-test":
        values["nonce"] = NONCE
        values["preflight_report"] = write_report(
            checkout.home, preflight_payload(checkout.head), name=f"pf-{next(_TOKENS)}.json"
        )
    else:
        values["run_id"] = RUN_ID
    if action == "abandon-pre-t0":
        values["abandon_reason"] = "rehearsal-complete"
    values.update(changes)
    return launcher.Invocation(**values)


def run_action(checkout, session, fake, action, *, code=0, stdout=None, stderr=b"", **changes):
    if action == "start-local-test" and code == 0 and stdout is None and fake.on_run is None:
        # Como el command real: un START coherente crea su run en la evidencia de la sesión.
        fake.on_run = lambda argv: make_run(session, RUN_ID)
    if fake.result is None:
        out = command_json(OUTPUT[action]) if stdout is None and code == 0 else (stdout or b"")
        fake.result = base.Completed(code, out, stderr)
    if action != "start-local-test" and "session_evidence" not in changes:
        (session.root / "evidence" / "runs" / RUN_ID).mkdir(parents=True, exist_ok=True)
    changes.pop("session_evidence", None)
    summary = launcher.execute(invocation(checkout, session, fake, action, **changes))
    return summary, Path(summary["output_dir"])


def make_run(session, run_id=RUN_ID):
    path = session.root / "evidence" / "runs" / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def meta(output):
    return json.loads((output / "launcher-meta.json").read_text())


def tail(argv):
    return argv[argv.index(launcher.SERVICE, argv.index("run")) + 1 :]


# --- Acciones y comando (1–8) ----------------------------------------------------------------


def test_start_local_test_runs_start_in_local_test_mode_only(checkout, session):
    # (1, 2) START siempre LOCAL_TEST, con confirmación de run nuevo y el source-root completo.
    fake = FakeDocker()
    summary, output = run_action(checkout, session, fake, "start-local-test")
    assert summary["launcher_exit"] == 0 and summary["classification"] == "COHERENT"
    assert summary["status"] == "PREPARED" and summary["rehearsal_run_id"] == RUN_ID
    assert summary["official_run_id"] is None and summary["evidence_mode"] == "LOCAL_TEST"
    (argv,) = fake.runs()
    assert tail(argv) == [
        "python",
        "manage.py",
        "s11_ex10",
        "start",
        "--expected-git-sha",
        checkout.head,
        "--source-root",
        "/s11-full-source",
        "--nonce",
        NONCE,
        "--evidence-mode",
        "LOCAL_TEST",
        "--confirm-new-run",
    ]
    assert json.loads((output / "start-local-test.json").read_text()) == START_OK


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (
            "rehearsal-started",
            [
                "rehearsal-started",
                "--run-id",
                RUN_ID,
                "--expected-git-sha",
                None,
                "--confirm-local-rehearsal",
            ],
        ),
        (
            "abandon-pre-t0",
            [
                "finalize",
                "--run-id",
                RUN_ID,
                "--expected-git-sha",
                None,
                "--abandon-reason",
                "rehearsal-complete",
            ],
        ),
        ("status", ["status", "--run-id", RUN_ID]),
    ],
)
def test_each_action_maps_to_exactly_one_command(checkout, session, action, expected):
    # (4–7) La confirmación propia del checkpoint, el abandono con código y el status.
    fake = FakeDocker(web=action == "rehearsal-started")
    summary, _ = run_action(checkout, session, fake, action)
    assert summary["launcher_exit"] == 0, summary
    expected = [checkout.head if item is None else item for item in expected]
    (argv,) = fake.runs()
    assert tail(argv) == ["python", "manage.py", "s11_ex10", *expected]


def test_only_the_four_rehearsal_actions_exist():
    # (8) Nada de execute, drain, reconcile, finalize con decisión, run, retry ni resume.
    assert set(launcher.ACTIONS) == {
        "start-local-test",
        "rehearsal-started",
        "abandon-pre-t0",
        "status",
    }
    for action in ("execute", "drain", "reconcile", "finalize", "run", "retry", "resume"):
        with pytest.raises(base.UsageError):
            launcher.parse_args([action, "--expected-git-sha", "a" * 40, "--session-root", "/x"])


@pytest.mark.parametrize(
    "extra",
    [
        ["--official"],
        ["--confirm-emission"],
        ["--execute"],
        ["--request-count", "50"],
        ["--retry"],
        ["--resume"],
        ["--provider-key", "x"],
        ["--real-custody-key", "x"],
        ["--evidence-mode", "OFFICIAL"],
        ["--root", "var/s11-runs"],
        ["--bogus"],
    ],
)
def test_forbidden_and_unknown_options_are_usage_errors(checkout, extra, capsys):
    # (3, 35) Opciones prohibidas o desconocidas: exit 3, sin citar el valor.
    argv = ["status", "--expected-git-sha", "a" * 40, "--session-root", "/x", "--run-id", RUN_ID]
    assert launcher.main([*argv, *extra], environ={}) == 3
    err = capsys.readouterr().err
    assert err.strip() == "S11_EX10_LAUNCHER_USAGE: invalid-arguments"


@pytest.mark.parametrize(
    ("action", "changes"),
    [
        ("start-local-test", {"nonce": None}),
        ("start-local-test", {"preflight_report": None}),
        ("start-local-test", {"run_id": RUN_ID}),
        ("rehearsal-started", {"run_id": None}),
        ("rehearsal-started", {"run_id": "../etc"}),
        ("abandon-pre-t0", {"abandon_reason": None}),
        ("status", {"nonce": NONCE}),
    ],
)
def test_action_arguments_are_exact(checkout, session, action, changes):
    with pytest.raises(base.LauncherError):
        launcher.validate_invocation(invocation(checkout, session, FakeDocker(), action, **changes))


def test_abandon_reason_must_be_an_allowed_code(checkout, session):
    inv = invocation(checkout, session, FakeDocker(), "abandon-pre-t0", abandon_reason="free text")
    with pytest.raises(base.LauncherError, match="abandon-reason-not-allowed"):
        launcher.validate_invocation(inv)


# --- Checkout, montajes y safe.directory (9–13, 40) -------------------------------------------


def test_the_full_checkout_is_mounted_once_read_only_and_official_roots_never(checkout, session):
    # (9, 40) Un único montaje del checkout, ro; ninguna raíz oficial; el override de la sesión.
    fake = FakeDocker(web=False)
    run_action(checkout, session, fake, "status")
    (argv,) = fake.runs()
    mounts = [argv[i + 1] for i, t in enumerate(argv) if t == "-v"]
    assert mounts == [f"{checkout.root}:/s11-full-source:ro"]
    files = [argv[i + 1] for i, t in enumerate(argv) if t == "-f"]
    assert files == [*launcher.COMPOSE_FILES, str(session.override)]
    assert not any("var/s11" in token for token in argv)
    override = session.override.read_text()
    volumes = [line for line in override.splitlines() if line.startswith("      - ")]
    assert len(volumes) == 8 and not any("var/" in line for line in volumes)
    assert override.count("env_file: !reset []") == 3
    for name, target in launcher.ROOT_MOUNTS.items():
        assert f"- {session.root / name}:{target}" in override


def _argv(checkout, session, **kwargs):
    target = launcher.Target(PROJECT, (*launcher.COMPOSE_FILES, str(session.override)))
    return launcher.build_command(
        target,
        "status",
        session=session,
        expected_git_sha=checkout.head,
        container_name=f"{PROJECT}-lifecycle-status-x",
        repo_root=checkout.root,
        run_id=RUN_ID,
        **kwargs,
    )


def _unsafe(checkout, session, argv, action="status"):
    target = launcher.Target(PROJECT, (*launcher.COMPOSE_FILES, str(session.override)))
    with pytest.raises(base.LauncherError) as info:
        launcher.assert_safe_command(
            argv, target=target, action=action, session=session, repo_root=checkout.root
        )
    return info.value.code


@pytest.mark.parametrize(
    ("mutate", "problem"),
    [
        (
            lambda a, c, s: [
                t.replace(":ro", ":rw") if t.endswith("/s11-full-source:ro") else t for t in a
            ],
            "rw-mount",
        ),
        (
            lambda a, c, s: [t.replace("/s11-full-source:ro", "/s11-source:ro") for t in a],
            "checkout-mount",
        ),
        (
            lambda a, c, s: a[: a.index("backend")]
            + ["-v", f"{c.root}/backend:/s11-full-source/b:ro"]
            + a[a.index("backend") :],
            "checkout-mounted-more-than-once",
        ),
        (
            lambda a, c, s: a[: a.index("backend")]
            + ["-v", f"{c.root}/var/s11-runs:/x:ro"]
            + a[a.index("backend") :],
            "official-root-mount",
        ),
        (
            lambda a, c, s: a[: a.index("backend")] + ["-e", "X=1"] + a[a.index("backend") :],
            "run-options",
        ),
        (lambda a, c, s: [t for t in a if t != str(s.override)][:-0] or a, "compose-files"),
        (lambda a, c, s: a[:-3] + ["drain", "--run-id", RUN_ID], "container-command"),
        (lambda a, c, s: a + ["--confirm-emission"], "container-command"),
        (lambda a, c, s: a + [s.secrets["S11_CUSTODY_HMAC_KEY"]], "secret-in-argv"),
    ],
    ids=[
        "rw",
        "partial",
        "piecewise",
        "official-root",
        "env-option",
        "override-missing",
        "other-step",
        "emission-flag",
        "secret",
    ],
)
def test_unsafe_commands_are_rejected(checkout, session, mutate, problem):
    # (10, 11, 14, 16, 40) Cada violación de forma es un error del launcher, nunca un comando.
    argv = _argv(checkout, session)
    mutated = mutate(list(argv), checkout, session)
    if problem == "compose-files":
        index = mutated.index(str(session.override))
        mutated = mutated[: index - 1] + mutated[index + 1 :]
    assert problem in _unsafe(checkout, session, mutated)


def test_safe_directory_is_exact_and_temporary(checkout, session):
    # (12, 13) Sólo /s11-full-source, nunca un comodín; el gitconfig se borra al terminar.
    assert base.SAFE_DIRECTORY_CONFIG == "[safe]\n\tdirectory = /s11-full-source\n"
    assert "*" not in base.SAFE_DIRECTORY_CONFIG
    fake = FakeDocker(web=False)
    seen = {}

    def capture(argv):
        mount = [argv[i + 1] for i, t in enumerate(argv) if t == "-v"][-1]
        source = mount.partition(":")[0]
        seen["mount"], seen["content"] = mount, Path(source).read_text()

    fake.on_run = capture
    summary, output = run_action(checkout, session, fake, "status", safe_directory=True)
    assert summary["launcher_exit"] == 0
    assert seen["mount"].endswith(":/etc/gitconfig:ro")
    assert seen["content"] == base.SAFE_DIRECTORY_CONFIG
    assert not (output / "safe-directory.gitconfig").exists()


# --- Entorno y secretos (13–15, 34) -----------------------------------------------------------


def test_the_child_environment_is_a_whitelist_with_synthetic_keys(checkout, session):
    # (13, 14, 34) uid/gid del operador, claves sintéticas y nada más del host.
    fake = FakeDocker(web=False)
    host = environ(
        checkout.home,
        OPENAI_API_KEY="sk-" + "x" * 30,
        DJANGO_SECRET_KEY="real",
        S11_CUSTODY_HMAC_KEY="host-real-key",
        AWS_SECRET_ACCESS_KEY="y",
    )
    run_action(checkout, session, fake, "status", environ=host)
    (env,) = fake.envs
    assert set(env) == {"HOME", "PATH", *base.INTERPOLATION_ENV, "S11_HOST_UID", "S11_HOST_GID"}
    assert env["S11_HOST_UID"] == str(os.getuid()) and env["S11_HOST_GID"] == str(os.getgid())
    for name in base.INTERPOLATION_ENV:
        assert env[name] == session.secrets[name]
    assert "OPENAI_API_KEY" not in env and "DJANGO_SECRET_KEY" not in env


def test_secrets_never_reach_argv_or_any_output_file(checkout, session):
    # (14, 15) Ni en el comando ni en los archivos de salida, tampoco si el command los imprime.
    fake = FakeDocker()
    leak = session.secrets["S11_DOCUMENT_HMAC_SECRET"]
    fake.result = base.Completed(0, command_json({**STARTED_OK, "phase": leak}), leak.encode())
    summary, output = run_action(checkout, session, fake, "rehearsal-started")
    assert summary["launcher_exit"] == 4
    (argv,) = fake.runs()
    values = set(session.secrets.values())
    assert not any(value in token for token in argv for value in values)
    for path in output.iterdir():
        text = path.read_text()
        assert not any(value in text for value in values), path.name


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda s: {**s, "S11_CUSTODY_HMAC_KEY": "zz"}, "secrets-file-malformed"),
        (
            lambda s: {k: v for k, v in s.items() if k != "DJANGO_SECRET_KEY"},
            "secrets-file-malformed",
        ),
        (
            lambda s: {**s, "S11_DOCUMENT_HMAC_SECRET": s["S11_CUSTODY_HMAC_KEY"]},
            "secrets-not-distinct",
        ),
    ],
)
def test_malformed_or_reused_synthetic_secrets_are_rejected(checkout, session, change, code):
    raw = json.dumps(change(dict(session.secrets))).encode()
    with pytest.raises(base.LauncherError, match=code):
        launcher.synthetic_secrets(raw, {})


def test_real_custody_keys_and_host_values_are_refused(monkeypatch, session):
    # Nunca la custodia oficial ni las claves que el operador tenga exportadas.
    values = dict(session.secrets)
    raw = json.dumps(values).encode()
    epoch = hashlib.sha256(values["S11_CUSTODY_HMAC_KEY"].encode()).hexdigest()[:16]
    monkeypatch.setattr(launcher, "OFFICIAL_CUSTODY_EPOCH", epoch)
    with pytest.raises(base.LauncherError, match="real-custody-key"):
        launcher.synthetic_secrets(raw, {})
    monkeypatch.undo()
    public = hashlib.sha256(bytes.fromhex(values["S11_PUBLICATION_APPROVAL_PUBLIC_KEY"]))
    monkeypatch.setattr(launcher, "OFFICIAL_PUBLICATION_KEY_ID", public.hexdigest()[:16])
    with pytest.raises(base.LauncherError, match="real-publication-key"):
        launcher.synthetic_secrets(raw, {})
    monkeypatch.undo()
    with pytest.raises(base.LauncherError, match="secret-equals-host-environment"):
        launcher.synthetic_secrets(raw, {"S11_CUSTODY_HMAC_KEY": values["S11_CUSTODY_HMAC_KEY"]})


# --- Sesión aislada (16–18, 37) ------------------------------------------------------------------


def test_session_roots_inside_the_repository_or_official_roots_are_rejected(checkout):
    # (16, 18) Ni bajo el repositorio ni bajo var/, ni conteniéndolos.
    inside = launcher.write_session(checkout.root / "var" / "s11-runs-rehearsal")
    with pytest.raises(base.LauncherError, match="session-root-inside-repository"):
        launcher.verify_session(inside.root, checkout.root, {})
    with pytest.raises(base.LauncherError, match="session-root-inside-repository"):
        launcher.verify_session(checkout.root.parent, checkout.root, {})


def test_symlinked_or_loose_session_directories_are_rejected(checkout, session):
    # (17) Symlinks y permisos laxos: ninguno se corrige, todos se rechazan.
    link = checkout.home / "link-session"
    link.symlink_to(session.root)
    with pytest.raises(base.LauncherError, match="session-root-not-realpath"):
        launcher.verify_session(link, checkout.root, {})
    (session.root / "objects").chmod(0o755)
    with pytest.raises(base.LauncherError, match="session-dir-unsafe:objects"):
        launcher.verify_session(session.root, checkout.root, {})
    (session.root / "objects").chmod(0o700)
    evidence = session.root / "evidence"
    evidence.rename(session.root / "evidence-real")
    evidence.symlink_to(session.root / "evidence-real")
    with pytest.raises(base.LauncherError, match="session-dir-unsafe:evidence"):
        launcher.verify_session(session.root, checkout.root, {})


def test_the_override_must_be_the_generated_one(checkout, session):
    session.override.chmod(0o600)
    session.override.write_text(session.override.read_text().replace("/s11-evidence", "/x"))
    with pytest.raises(base.LauncherError, match="override-file-differs"):
        launcher.verify_session(session.root, checkout.root, {})


def test_later_actions_require_their_run_in_this_session(checkout, session):
    # Un run que no existe en la evidencia de esta sesión no se opera (aunque haya otros).
    make_run(session, RUN_B)
    fake = FakeDocker()
    summary, _ = run_action(checkout, session, fake, "status", session_evidence=True)
    assert summary["problems"] == ["target-run-not-found"] and fake.runs() == []


def test_status_creates_no_run_and_no_root(checkout, session):
    # (37) status sólo lee: la sesión queda igual salvo su propio directorio de salida.
    (session.root / "evidence" / "runs" / RUN_ID).mkdir(parents=True)
    before = sorted(
        str(p.relative_to(session.root)) for p in session.root.rglob("*") if "output" not in p.parts
    )
    run_action(checkout, session, FakeDocker(web=False), "status")
    after = sorted(
        str(p.relative_to(session.root)) for p in session.root.rglob("*") if "output" not in p.parts
    )
    assert before == after


# --- Informe de preflight (19–23) ------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"git_sha": "b" * 40}, "preflight:git_sha"),
        ({"expected_git_sha": "b" * 40}, "preflight:expected_git_sha"),
        (
            {"checked_at": (NOW - timedelta(minutes=16)).isoformat().replace("+00:00", "Z")},
            "preflight:stale",
        ),
        (
            {"checked_at": (NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")},
            "preflight:checked-at-future",
        ),
        ({"gate": "NOT_READY"}, "preflight:gate"),
        ({"result": "FAIL"}, "preflight:result"),
        ({"result": "INCOMPLETE"}, "preflight:result"),
        ({"ready_for_execution": True}, "preflight:ready_for_execution"),
        ({"run_id": RUN_ID}, "preflight:run_id"),
        ({"evidence_written": True}, "preflight:evidence_written"),
        ({"report_posts": 1}, "preflight:report_posts"),
        ({"checks": [{"check": "x", "result": "NOT_RUN"}]}, "preflight:check-not-pass"),
    ],
    ids=[
        "sha",
        "expected-sha",
        "stale",
        "future",
        "gate",
        "fail",
        "incomplete",
        "ready-true",
        "run-id",
        "evidence",
        "posts",
        "not-run",
    ],
)
def test_the_preflight_report_must_be_a_fresh_pass_of_the_same_head(checkout, change, problem):
    # (19–22) Un informe de otro HEAD, viejo, de otro gate o no PASS no abre el START.
    raw = json.dumps(preflight_payload(checkout.head, **change)).encode()
    assert problem in launcher.preflight_report_problems(raw, git_sha=checkout.head, now=NOW)


def test_a_valid_preflight_report_has_no_problems_and_the_bound_matches_the_executor(checkout):
    raw = json.dumps(preflight_payload(checkout.head)).encode()
    assert launcher.preflight_report_problems(raw, git_sha=checkout.head, now=NOW) == []
    assert launcher.PREFLIGHT_MAX_AGE == ex10_executor.PREFLIGHT_MAX_AGE
    assert launcher.PREFLIGHT_FUTURE_TOLERANCE == ex10_executor.PREFLIGHT_FUTURE_TOLERANCE


@pytest.mark.parametrize("raw", [b"not json", b"[]", b'{"result": "PASS"}', b"NaN"])
def test_a_malformed_preflight_report_is_rejected(checkout, raw):
    # (23)
    assert launcher.preflight_report_problems(raw, git_sha=checkout.head, now=NOW) == [
        "preflight:malformed"
    ]


def test_a_rejected_report_launches_nothing(checkout, session):
    fake = FakeDocker()
    report = write_report(checkout.home, preflight_payload("b" * 40), name="bad.json")
    summary, output = run_action(
        checkout, session, fake, "start-local-test", preflight_report=report
    )
    assert summary["launcher_exit"] == 3 and fake.runs() == []
    assert "preflight:git_sha" in meta(output)["preflight_report_problems"]


def test_a_world_readable_report_is_rejected(checkout, session):
    fake = FakeDocker()
    report = write_report(checkout.home, preflight_payload(checkout.head), "loose.json", 0o644)
    summary, _ = run_action(checkout, session, fake, "start-local-test", preflight_report=report)
    assert summary["problems"] == ["preflight-report-unsafe"] and fake.runs() == []


# --- Coherencia exit/JSON (26, 29, 30) ------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "stdout", "stderr", "problem"),
    [
        (0, b"not json\n", b"", "stdout-not-json"),
        (0, command_json({**STARTED_OK, "phase": "EMITTING"}), b"", "started:phase"),
        (0, command_json({**STARTED_OK, "tokens_issued": 10}), b"", "started:tokens_issued"),
        (0, b'{"run_id": "x"}', b"", "stdout-not-the-command-json"),
        (
            0,
            command_json(STARTED_OK),
            b'{"reasons": ["x"], "refused": true, "terminal": true}\n',
            "refusal-with-exit-0",
        ),
        (
            1,
            command_json(STARTED_OK),
            b'{"reasons": ["x"], "refused": true, "terminal": false}\n',
            "refusal-incoherent",
        ),
        (2, b"", b'{"reasons": ["x"], "refused": true, "terminal": false}\n', "refusal-incoherent"),
        (1, b"", b"", "refusal-incoherent"),
        (3, b"x", b"", "usage-with-output"),
        (7, b"", b"", "exit-code:7"),
    ],
    ids=[
        "invalid-json",
        "phase",
        "tokens",
        "not-canonical",
        "refusal-exit-0",
        "refused-stdout",
        "terminal-mismatch",
        "refused-without-json",
        "usage-stdout",
        "unknown-exit",
    ],
)
def test_incoherent_exit_and_output_is_a_launcher_failure(
    checkout, session, code, stdout, stderr, problem
):
    # (26, 29, 30) Una salida incoherente nunca se reinterpreta como éxito.
    fake = FakeDocker()
    fake.result = base.Completed(code, stdout, stderr)
    summary, _ = run_action(checkout, session, fake, "rehearsal-started")
    assert summary["launcher_exit"] == 4 and summary["classification"] == "LAUNCHER_FAILURE"
    assert problem in summary["problems"]


@pytest.mark.parametrize(("code", "terminal"), [(1, False), (2, True)])
def test_command_refusals_keep_their_codes(checkout, session, code, terminal):
    fake = FakeDocker()
    refusal = {
        "reasons": ["phase:STARTED:rehearsal-started-not-allowed"],
        "refused": True,
        "terminal": terminal,
    }
    stderr = json.dumps(refusal, sort_keys=True).encode() + b"\nCommandError: S11_EX10_REFUSED\n"
    fake.result = base.Completed(code, b"", stderr)
    summary, output = run_action(checkout, session, fake, "rehearsal-started")
    assert summary["launcher_exit"] == code and summary["classification"] == "REFUSED"
    assert meta(output)["refusal"] == refusal
    assert "rehearsal-started.json" not in os.listdir(output)


def test_unknown_stderr_lines_are_redacted(checkout, session):
    fake = FakeDocker()
    fake.result = base.Completed(
        0, command_json(STARTED_OK), b"Traceback (most recent call last):\npostgres://u:p@db/x\n"
    )
    summary, output = run_action(checkout, session, fake, "rehearsal-started")
    text = (output / "rehearsal-started.stderr").read_text()
    assert "postgres" not in text and text.count("[redacted-line]") == 2


# --- Timeout, señales y limpieza (24, 25, 26, 39) ----------------------------------------------


@pytest.mark.parametrize("kind", ["timeout", "interrupted"])
def test_timeouts_and_interruptions_abort_and_clean_up_exactly(checkout, session, kind):
    # (24, 25, 39) Nunca un 0; sólo el contenedor de la invocación, por nombre exacto.
    fake = FakeDocker()
    fake.abort, fake.leave_container = kind, True
    summary, output = run_action(checkout, session, fake, "rehearsal-started")
    assert summary["launcher_exit"] == 5 and summary["classification"] == "APPARATUS_ABORT"
    removed = [argv for argv in fake.calls if argv[:3] == ["docker", "rm", "-f"]]
    assert removed == [["docker", "rm", "-f", meta(output)["container_name"]]]
    assert meta(output)["cleanup"]["container_absent"] is True


def test_each_action_has_an_explicit_timeout(checkout, session):
    fake = FakeDocker()
    run_action(checkout, session, fake, "rehearsal-started")
    assert fake.timeout == launcher.ACTIONS["rehearsal-started"].timeout
    fake = FakeDocker()
    run_action(checkout, session, fake, "rehearsal-started", timeout=45.0)
    assert fake.timeout == 45.0


def test_the_real_runner_kills_its_process_group_on_interrupt(tmp_path):
    # (25, 26) SIGINT (y SIGTERM/SIGHUP, que main convierte en Ctrl-C) detienen al grupo entero.
    pidfile = tmp_path / "pid"
    runner = base.DockerRunner(tmp_path, {"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    previous = signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(KeyboardInterrupt))
    signal.setitimer(signal.ITIMER_REAL, 0.5)
    try:
        with pytest.raises(base.Aborted) as info:
            runner.run(["sh", "-c", f"echo $$ > {pidfile}; exec sleep 60"], timeout=30)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert info.value.kind == "interrupted"
    child = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            state = Path(f"/proc/{child}/status").read_text()
        except (FileNotFoundError, ProcessLookupError):
            break
        if "State:\tZ" in state:
            break
        time.sleep(0.01)
    else:
        pytest.fail("el nieto sigue vivo")
    source = inspect.getsource(launcher.main)
    assert "signal.SIGTERM, signal.SIGHUP" in source and "base._interrupt" in source


def test_a_preexisting_container_name_launches_nothing(checkout, session):
    # (27)
    fake = FakeDocker()
    fake.containers.append(
        container(f"{PROJECT}-lifecycle-rehearsal-started-cafe0001", "exited", "backend", True)
    )
    summary, _ = run_action(checkout, session, fake, "rehearsal-started", token=lambda: "cafe0001")
    assert summary["problems"] == ["container-name-unavailable"] and fake.runs() == []


def test_cleanup_never_prunes_or_removes_images():
    # (32, 33) Ni prune ni --rmi local en el código del launcher.
    source = SCRIPT.read_text()
    for forbidden in ("prune", "--rmi", "rmi local", "system prune", "image rm"):
        assert forbidden not in source.replace("_FORBIDDEN_TOKENS", "")
    assert {"prune", "--rmi"} <= base._FORBIDDEN_TOKENS


# --- Precondiciones del stack ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fake", "problem"),
    [
        (lambda: FakeDocker(web=False), "web-worker-not-running"),
        (lambda: FakeDocker(beat=True), "beat-running"),
    ],
)
def test_stack_preconditions(checkout, session, fake, problem):
    docker = fake()
    summary, _ = run_action(checkout, session, docker, "rehearsal-started")
    assert summary["problems"] == [problem] and docker.runs() == []


# --- Repositorio --------------------------------------------------------------------------------


def test_another_head_or_a_dirty_tree_launches_nothing(checkout, session):
    fake = FakeDocker()
    summary, _ = run_action(checkout, session, fake, "status", expected_git_sha="c" * 40)
    assert summary["problems"] == ["head-mismatch"] and fake.runs() == []
    (checkout.root / "infra" / "docker-compose.yml").write_text("services: {dirty: {}}\n")
    fake = FakeDocker()
    summary, _ = run_action(checkout, session, fake, "status")
    assert summary["problems"] == ["working-tree-dirty"] and fake.runs() == []


@pytest.mark.parametrize(
    ("prepare", "problem"),
    [
        (lambda root: git(root, "config", "core.sparseCheckout", "true"), "sparse-checkout"),
        (
            lambda root: git(root, "update-index", "--skip-worktree", "infra/docker-compose.yml"),
            "hidden-index-entries",
        ),
        (
            lambda root: git(
                root, "update-index", "--assume-unchanged", "infra/docker-compose.yml"
            ),
            "hidden-index-entries",
        ),
        (
            lambda root: (root / ".git" / "shallow").write_text("x\n"),
            ("shallow-history", "git-command-failed"),
        ),
    ],
)
def test_repository_conditions_are_verified_and_never_repaired(checkout, session, prepare, problem):
    prepare(checkout.root)
    fake = FakeDocker()
    summary, _ = run_action(checkout, session, fake, "status")
    allowed = problem if isinstance(problem, tuple) else (problem,)
    assert len(summary["problems"]) == 1 and summary["problems"][0] in allowed
    assert fake.runs() == []


# --- Salida y concurrencia (28, 31, 36, 38) --------------------------------------------------


def test_output_files_are_private_and_never_mixed(checkout, session):
    # (28, 36, 38) 0700/0600, un directorio por invocación y nunca un run oficial.
    outputs = []
    for _ in range(2):
        fake = FakeDocker(web=False)
        summary, output = run_action(checkout, session, fake, "status")
        assert summary["official_run_id"] is None and meta(output)["official_run_id"] is None
        outputs.append(output)
    assert outputs[0] != outputs[1]
    for output in outputs:
        assert output.parent == session.root / "output"
        assert stat.S_IMODE(output.stat().st_mode) == 0o700
        assert {stat.S_IMODE(p.stat().st_mode) for p in output.iterdir()} == {0o600}


@pytest.mark.parametrize("lock", ["checkout", "session"])
def test_a_concurrent_launcher_is_busy_and_creates_nothing(checkout, session, lock):
    # (31) La guarda de checkout (compartida con el launcher del preflight) y la de sesión.
    path = checkout.root if lock == "checkout" else session.root
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        before = sorted(os.listdir(session.root / "output"))
        fake = FakeDocker()
        with pytest.raises(base.LauncherBusy):
            launcher.execute(invocation(checkout, session, fake, "status"))
        assert fake.calls == [] and sorted(os.listdir(session.root / "output")) == before
    finally:
        os.close(fd)


def test_write_session_creates_private_dirs_and_files(checkout):
    created = launcher.write_session(checkout.home / "fresh-session")
    assert stat.S_IMODE(created.root.stat().st_mode) == 0o700
    for name in launcher.SESSION_DIRS:
        assert stat.S_IMODE((created.root / name).stat().st_mode) == 0o700
    secrets_file = created.root / "temporary-secrets" / launcher.SECRETS_FILE
    assert stat.S_IMODE(secrets_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(created.override.stat().st_mode) == 0o600
    with pytest.raises(base.LauncherError):
        launcher.write_session(created.root)
    assert launcher.verify_session(created.root, checkout.root, {}).secrets == created.secrets


# --- Sesión con varios runs LOCAL_TEST --------------------------------------------------------
# El launcher sólo valida la estructura de la evidencia de la sesión: START admite runs previos
# (el executor decide con ellos dentro de su lock) y las demás acciones exigen su run, no que sea
# el único. Ningún run se borra, mueve ni oculta; la salida de cada acción es suya.


def entries(session):
    return sorted(os.listdir(session.root / "evidence" / "runs"))


def refusal_result(code, reasons):
    payload = {"reasons": reasons, "refused": True, "terminal": code == 2}
    stderr = json.dumps(payload, sort_keys=True).encode() + b"\nCommandError: S11_EX10_REFUSED\n"
    return base.Completed(code, b"", stderr), payload


@pytest.mark.parametrize("layout", ["no-runs-dir", "empty-runs-dir", "contractual-root"])
def test_start_with_an_empty_store_is_launched(checkout, session, layout):
    # (1) Sin `runs/`, con `runs/` vacío o con los sidecars contractuales del EvidenceStore.
    evidence = session.root / "evidence"
    if layout != "no-runs-dir":
        (evidence / "runs").mkdir()
    if layout == "contractual-root":
        (evidence / "finalization-transactions").mkdir()
        (evidence / ".s11-custody.lock").write_bytes(b"")
    fake = FakeDocker()
    summary, output = run_action(checkout, session, fake, "start-local-test")
    assert summary["classification"] == "COHERENT" and len(fake.runs()) == 1
    assert meta(output)["evidence_runs_before"] == [] and entries(session) == [RUN_ID]
    assert meta(output)["evidence_runs_after"] == [RUN_ID]


def test_a_target_action_without_its_run_or_run_id_launches_nothing(checkout, session):
    # (2, 3) Sin el run: rechazo previo; sin `--run-id`: uso.
    for action in ("rehearsal-started", "abandon-pre-t0", "status"):
        fake = FakeDocker()
        summary, _ = run_action(checkout, session, fake, action, session_evidence=True)
        assert summary["problems"] == ["target-run-not-found"] and fake.runs() == []
    with pytest.raises(base.UsageError):
        launcher.validate_invocation(
            invocation(checkout, session, FakeDocker(), "status", run_id=None)
        )


@pytest.mark.parametrize("prior", [[RUN_B], [RUN_B, "s11-55d2e3b5-be09-5c9f-a59e-42812088476a"]])
def test_start_with_prior_runs_is_launched_and_leaves_them_in_place(checkout, session, prior):
    # (4, 5, 21, 28) Con runs previos START se lanza igual; los previos siguen visibles y
    # nada de ellos cambia: el launcher no los lee, mueve ni oculta (la sesión se monta entera).
    for run in prior:
        (make_run(session, run) / "raw").mkdir()
    before = {run: os.lstat(session.root / "evidence" / "runs" / run) for run in prior}
    fake = FakeDocker()
    summary, output = run_action(checkout, session, fake, "start-local-test")
    assert summary["classification"] == "COHERENT" and summary["rehearsal_run_id"] == RUN_ID
    assert meta(output)["evidence_runs_before"] == sorted(prior)
    assert meta(output)["evidence_runs_after"] == sorted([*prior, RUN_ID])
    (argv,) = fake.runs()
    assert not any(run in token for token in argv for run in prior)
    mounts = [argv[i + 1] for i, token in enumerate(argv) if token == "-v"]
    assert not any("evidence" in mount for mount in mounts)
    for run, info in before.items():
        after = os.lstat(session.root / "evidence" / "runs" / run)
        assert (after.st_ino, after.st_mtime_ns, after.st_ctime_ns) == (
            info.st_ino, info.st_mtime_ns, info.st_ctime_ns,
        )  # fmt: skip


@pytest.mark.parametrize("action", ["rehearsal-started", "abandon-pre-t0", "status"])
@pytest.mark.parametrize("target", [RUN_ID, RUN_B])
def test_target_actions_select_their_run_among_several(checkout, session, action, target):
    # (6–9, 29, 30) A con B presente y B con A presente: cada acción opera sólo su run.
    make_run(session, RUN_ID)
    make_run(session, RUN_B)
    fake = FakeDocker(web=action != "status")
    fake.result = base.Completed(0, command_json({**OUTPUT[action], "run_id": target}), b"")
    summary, output = run_action(checkout, session, fake, action, run_id=target)
    assert summary["classification"] == "COHERENT" and summary["rehearsal_run_id"] == target
    (argv,) = fake.runs()
    assert tail(argv)[tail(argv).index("--run-id") + 1] == target
    assert entries(session) == sorted([RUN_ID, RUN_B])
    assert json.loads((output / f"{action}.json").read_text())["run_id"] == target


def test_outputs_of_two_runs_are_never_mixed(checkout, session):
    # (10, 39–41, 44) Directorios de salida y contenedores distintos; la salida de A no
    # puede traer B.
    make_run(session, RUN_ID)
    make_run(session, RUN_B)
    outputs, containers = [], []
    for target in (RUN_ID, RUN_B):
        fake = FakeDocker(web=False)
        fake.result = base.Completed(0, command_json({**STATUS_OK, "run_id": target}), b"")
        summary, output = run_action(checkout, session, fake, "status", run_id=target)
        outputs.append(output)
        containers.append(meta(output)["container_name"])
        assert RUN_B not in (output / "status.json").read_text() or target == RUN_B
    assert len(set(outputs)) == 2 and len(set(containers)) == 2
    fake = FakeDocker(web=False)
    fake.result = base.Completed(0, command_json({**STATUS_OK, "run_id": RUN_B}), b"")
    summary, output = run_action(checkout, session, fake, "status", run_id=RUN_ID)
    assert summary["launcher_exit"] == 4 and "run-id-differs" in summary["problems"]
    # La salida incoherente queda sólo en el directorio de esa acción, clasificada como fallo.
    assert summary["classification"] == "LAUNCHER_FAILURE" and summary["rehearsal_run_id"] == RUN_ID
    assert output.parent == session.root / "output" and output not in outputs


@pytest.mark.parametrize(
    ("code", "reasons"),
    [
        (1, [f"active-run:{RUN_B}"]),
        (2, [f"binding-run-exists:{RUN_B}"]),
        (1, [f"not-demonstrated-unacknowledged:{RUN_B}"]),
        (1, [f"prior-run-unverifiable:{RUN_B}"]),
        (2, ["run-id-reused"]),
    ],
    ids=["active", "official-binding", "official-nd", "unverifiable", "run-id-reused"],
)
def test_domain_refusals_of_start_are_propagated_not_reinterpreted(
    checkout, session, code, reasons
):
    # (11–24) El executor rechaza con el run previo: el launcher conserva exit, JSON y stderr
    # saneado; no es un error de configuración ni un PASS, no queda contenedor ni run nuevo.
    (make_run(session, RUN_B) / "metadata").mkdir()
    fake = FakeDocker()
    fake.result, payload = refusal_result(code, reasons)
    fake.on_run = lambda argv: None
    summary, output = run_action(checkout, session, fake, "start-local-test")
    assert summary["classification"] == "REFUSED" and summary["launcher_exit"] == code
    assert summary["problems"] == [] and meta(output)["refusal"] == payload
    assert summary["rehearsal_run_id"] is None and entries(session) == [RUN_B]
    assert meta(output)["cleanup"]["container_absent"] is True
    assert "start-local-test.json" not in os.listdir(output)


def test_the_launcher_has_no_acknowledgement_or_official_option(checkout, capsys):
    # (19, 52) Un NOT_DEMONSTRATED oficial no se reconoce desde el launcher del ensayo.
    for extra in (
        ["--acknowledge-not-demonstrated", RUN_B],
        ["--official"],
        ["--evidence-mode", "OFFICIAL"],
    ):
        args = ["start-local-test", "--expected-git-sha", checkout.head, "--session-root", "/x",
                "--nonce", NONCE, "--preflight-report", "/x", *extra]  # fmt: skip
        assert launcher.main(args, environ={}) == 3


def _fifo(path):
    os.mkfifo(path)


def _socket(path):
    import socket as socket_module
    import tempfile

    # AF_UNIX limita la ruta: se crea en un directorio corto y se mueve (el inodo es el mismo).
    short = Path(tempfile.mkdtemp(prefix="s", dir="/tmp"))
    sock = socket_module.socket(socket_module.AF_UNIX)
    try:
        sock.bind(str(short / "s"))
    finally:
        sock.close()
    os.rename(short / "s", path)
    short.rmdir()


@pytest.mark.parametrize(
    ("make", "problem"),
    [
        (lambda e, o: (e / "runs" / RUN_B).write_bytes(b"x"), "evidence-unexpected-run-entry"),
        (
            lambda e, o: (e / "runs" / RUN_B).symlink_to(o, target_is_directory=True),
            "evidence-unexpected-run-entry",
        ),
        (lambda e, o: (e / "runs" / "not-a-run").mkdir(), "evidence-unexpected-run-entry"),
        (lambda e, o: (e / "runs" / RUN_B.upper()).mkdir(), "evidence-unexpected-run-entry"),
        (lambda e, o: _fifo(e / "runs" / RUN_B), "evidence-unexpected-run-entry"),
        (lambda e, o: _socket(e / "runs" / RUN_B), "evidence-unexpected-run-entry"),
        (lambda e, o: (e / "notes.txt").write_bytes(b"x"), "evidence-unexpected-entry"),
        (lambda e, o: (e / RUN_B).mkdir(), "evidence-unexpected-entry"),
        (lambda e, o: (e / ".s11-custody.lock").mkdir(), "evidence-unexpected-entry"),
        (lambda e, o: (e / "finalization-transactions").symlink_to(o), "evidence-unexpected-entry"),
    ],
    ids=[
        "file",
        "symlink",
        "non-canonical",
        "case",
        "fifo",
        "socket",
        "root-file",
        "root-run-like",
        "lock-not-file",
        "sidecar-symlink",
    ],  # fmt: skip
)
@pytest.mark.parametrize("action", ["start-local-test", "status"])
def test_unexpected_evidence_entries_launch_nothing(checkout, session, make, problem, action):
    # (25–27, 34) Lo que podría ocultar, suplantar o sacar un run de la sesión se rechaza antes
    # de lanzar nada, y no se toca.
    evidence = session.root / "evidence"
    (evidence / "runs").mkdir()
    make_run(session, RUN_ID)
    other = checkout.home / "other"
    other.mkdir()
    make(evidence, other)
    listing = sorted(os.listdir(evidence)), entries(session)
    fake = FakeDocker()
    summary, _ = run_action(checkout, session, fake, action, session_evidence=True)
    assert summary["launcher_exit"] == 3 and summary["problems"] == [problem]
    assert fake.runs() == [] and (sorted(os.listdir(evidence)), entries(session)) == listing


@pytest.mark.parametrize("runs", ["symlink", "file"])
def test_a_runs_directory_that_is_not_real_launches_nothing(checkout, session, runs):
    evidence = session.root / "evidence"
    if runs == "symlink":
        (checkout.home / "elsewhere" / RUN_ID).mkdir(parents=True)
        (evidence / "runs").symlink_to(checkout.home / "elsewhere", target_is_directory=True)
    else:
        (evidence / "runs").write_bytes(b"x")
    for action in ("start-local-test", "status"):
        fake = FakeDocker()
        summary, _ = run_action(checkout, session, fake, action, session_evidence=True)
        assert summary["problems"] == ["evidence-unexpected-entry"] and fake.runs() == []


@pytest.mark.parametrize(
    "run_id",
    ["../" + RUN_ID, RUN_ID + "/..", RUN_ID[:-1], RUN_ID + "0", "", RUN_ID.upper(), RUN_ID + " "],
    ids=["traversal", "traversal-suffix", "prefix", "longer", "empty", "case", "space"],
)
def test_non_canonical_targets_are_usage_errors(checkout, session, run_id):
    # (32, 33, 36–38) Un `--run-id` no canónico nunca llega a la evidencia.
    make_run(session, RUN_ID)
    with pytest.raises(base.UsageError):
        launcher.validate_invocation(
            invocation(checkout, session, FakeDocker(), "status", run_id=run_id)
        )


def test_a_prefix_collision_or_another_session_run_is_not_the_target(checkout, session):
    # (31, 33, 35) Coincidencia exacta del nombre, sólo en esta sesión.
    make_run(session, RUN_ID)
    other = launcher.write_session(checkout.home / "other-session")
    make_run(other, RUN_B)
    for target in (RUN_B, RUN_ID[:-4] + "0000"):
        fake = FakeDocker()
        summary, _ = run_action(checkout, session, fake, "status", run_id=target)
        assert summary["problems"] == ["target-run-not-found"] and fake.runs() == []


@pytest.mark.parametrize(
    ("action", "effect", "problem"),
    [
        ("start-local-test", "delete-prior", "evidence-runs-changed"),
        ("start-local-test", "reuse-prior", "start:run-id-not-new"),
        ("start-local-test", "extra-run", "evidence-runs-changed"),
        ("status", "create-run", "evidence-runs-changed"),
        ("abandon-pre-t0", "delete-prior", "evidence-runs-changed"),
        ("status", "unsafe-entry", "evidence-structure-after"),
    ],
)
def test_the_evidence_after_the_command_must_keep_every_prior_run(
    checkout, session, action, effect, problem
):
    # (14, 21, 28) Ningún run previo desaparece y como mucho aparece el que START creó; si no,
    # es un fallo del launcher (exit 4), nunca un PASS.
    make_run(session, RUN_B)
    if action != "start-local-test":
        make_run(session, RUN_ID)
    runs = session.root / "evidence" / "runs"
    effects = {
        "delete-prior": lambda argv: os.rmdir(runs / RUN_B),
        "reuse-prior": lambda argv: None,
        "extra-run": lambda argv: [make_run(session, RUN_ID), make_run(session, RUN_ID[:-1] + "9")],
        "create-run": lambda argv: make_run(session, RUN_ID[:-1] + "9"),
        "unsafe-entry": lambda argv: (runs / "junk").write_bytes(b"x"),
    }
    fake = FakeDocker()
    fake.on_run = effects[effect]
    output_payload = {**OUTPUT[action], "run_id": RUN_B if effect == "reuse-prior" else RUN_ID}
    fake.result = base.Completed(0, command_json(output_payload), b"")
    summary, _ = run_action(checkout, session, fake, action, session_evidence=True)
    assert summary["launcher_exit"] == 4 and problem in summary["problems"]
    assert (
        summary["rehearsal_run_id"] in (None, RUN_ID)
        and summary["classification"] == "LAUNCHER_FAILURE"
    )


def test_a_multi_run_session_keeps_every_safety_invariant(checkout, session):
    # (45–54) Con varios runs: ni secretos en argv/salidas, un único montaje del checkout en
    # sólo lectura, ninguna raíz oficial ni montaje por run, y nada de prune/--rmi.
    make_run(session, RUN_ID)
    make_run(session, RUN_B)
    fake = FakeDocker(web=False)
    summary, output = run_action(checkout, session, fake, "status")
    (argv,) = fake.runs()
    values = list(session.secrets.values())
    assert not any(v in token for token in argv for v in values)
    for path in output.iterdir():
        assert not any(v in path.read_text() for v in values)
    mounts = [argv[i + 1] for i, token in enumerate(argv) if token == "-v"]
    assert (
        all(m.endswith(":ro") for m in mounts) and sum(str(checkout.root) in m for m in mounts) == 1
    )
    assert not any("/var/" in m or RUN_B in m for m in mounts)
    assert not any(tok in ("prune", "--rmi") for call in fake.calls for tok in call)


# --- Integración: launcher → management command → prior scan → binding_refusal ---------------


@pytest.fixture
def domain(checkout, monkeypatch):
    """Una sesión cuya evidencia es una custodia real del executor y un Docker simulado que
    ejecuta el management command `s11_ex10` en proceso con esa custodia (claves de test)."""
    import contextlib as contextlib_module
    import io

    from django.core.management import call_command
    from django.core.management.base import CommandError

    from apps.experiments_app import ex10_load
    from apps.experiments_app.evidence import EvidenceStore
    from apps.experiments_app.management.commands import s11_ex10
    from apps.experiments_app.tests import ex10_executor_support as support

    # La custodia rechaza /tmp: la sesión vive bajo `.s11-test-artifacts`.
    parent = support.ARTIFACTS / f"launcher-integration-{os.getpid()}-{next(_TOKENS)}"
    parent.mkdir(parents=True, mode=0o700)
    session = launcher.write_session(Path(os.path.realpath(parent)) / "session")
    store = EvidenceStore(
        session.root / "evidence",
        custody_hmac_key=support.HMAC_KEY,
        publication_verification_key=support.PUBLIC_KEY,
        rolling_checksums=False,
    )
    harness = support.make_harness(
        f"launcher-integration-{next(_TOKENS)}", ex10_load.load_dataset()
    )
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
            fake.result = base.Completed(
                code, out.getvalue().encode(), (err.getvalue() + footer).encode()
            )

        return run

    def act(action, **changes):
        fake = FakeDocker(web=action in ("start-local-test", "rehearsal-started"))
        fake.on_run = command(fake)
        fake.result = base.Completed(0, b"", b"")
        if action == "start-local-test":
            changes.setdefault("nonce", f"nonce-integration-{next(_TOKENS)}")
        summary = launcher.execute(invocation(checkout, session, fake, action, **changes))
        return summary, Path(summary["output_dir"])

    yield SimpleNamespace(session=session, store=store, act=act, harness=harness)
    support.close_harness(harness)
    import shutil

    shutil.rmtree(parent, ignore_errors=True)


def run_tree(store, run_id):
    """Metadata completa (salvo atime) del árbol de un run, sin seguir symlinks."""
    base_path = store.root / "runs" / run_id
    facts = {}
    for path in [base_path, *sorted(base_path.rglob("*"))]:
        info = os.lstat(path)
        fact = (
            stat.S_IFMT(info.st_mode), stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino,
        )  # fmt: skip
        if stat.S_ISREG(info.st_mode):
            fact += (hashlib.sha256(path.read_bytes()).hexdigest(),)
        facts[str(path.relative_to(base_path))] = fact
    return facts


def test_a_second_start_through_the_launcher_sees_the_first_run_and_leaves_it_identical(domain):
    # (25) RUN A: START → STARTED → status → ABANDONED_PRE_T0; RUN B: START vía el launcher en
    # la misma custodia, el executor explora A (read-only) y crea B; A queda idéntico.
    summary, _ = domain.act("start-local-test")
    assert summary["classification"] == "COHERENT" and summary["status"] == "PREPARED"
    run_a = summary["rehearsal_run_id"]
    assert domain.act("rehearsal-started", run_id=run_a)[0]["status"] == "STARTED"
    assert domain.act("status", run_id=run_a)[0]["status"] == "STARTED"
    abandon, _ = domain.act("abandon-pre-t0", run_id=run_a)
    assert abandon["classification"] == "COHERENT" and abandon["status"] == "ABANDONED_PRE_T0"
    snapshot = run_tree(domain.store, run_a)
    started, output = domain.act("start-local-test")
    run_b = started["rehearsal_run_id"]
    assert started["classification"] == "COHERENT" and started["status"] == "PREPARED"
    assert run_b != run_a and run_tree(domain.store, run_a) == snapshot
    assert meta(output)["evidence_runs_before"] == [run_a]
    assert meta(output)["evidence_runs_after"] == sorted([run_a, run_b])
    start = ex10_executor.load_existing_state_readonly(domain.store, run_b).payload(
        ex10_executor.ART_START
    )
    assert start["prior_ex10_runs"] == [
        {"run_id": run_a, "evidence_mode": "LOCAL_TEST", "finalized": True,
         "decision": "NOT_DEMONSTRATED", "problems": []}
    ]  # fmt: skip
    for target, phase in ((run_a, "ABANDONED_PRE_T0"), (run_b, "PREPARED")):
        assert domain.act("status", run_id=target)[0]["status"] == phase
    closed, _ = domain.act("abandon-pre-t0", run_id=run_b)
    assert closed["status"] == "ABANDONED_PRE_T0" and run_tree(domain.store, run_a) == snapshot
    prior = ex10_executor.prior_ex10_runs(domain.store)
    assert ex10_executor.binding_refusal(
        prior, evidence_mode=ex10_executor.OFFICIAL_MODE, acknowledged=()
    ) == ([], False)


def test_an_active_or_corrupt_prior_run_is_refused_by_the_domain_through_the_launcher(domain):
    # (11–15, 22–24) El launcher deja llegar al executor, que rechaza; no se crea ningún run y
    # los previos quedan idénticos.
    run_a = domain.act("start-local-test")[0]["rehearsal_run_id"]
    snapshot = run_tree(domain.store, run_a)
    refused, output = domain.act("start-local-test")
    assert refused["classification"] == "REFUSED" and refused["launcher_exit"] == 1
    assert meta(output)["refusal"]["reasons"] == [f"active-run:{run_a}"]
    assert entries(domain.session) == [run_a] and run_tree(domain.store, run_a) == snapshot
    domain.act("abandon-pre-t0", run_id=run_a)
    signature = domain.store.root / "runs" / run_a / "metadata" / "artifacts.jsonl.hmac"
    signature.write_text("0" * 64 + "\n", encoding="ascii")
    snapshot = run_tree(domain.store, run_a)
    refused, output = domain.act("start-local-test")
    assert refused["classification"] == "REFUSED"
    assert meta(output)["refusal"]["reasons"] == [f"prior-run-unverifiable:{run_a}"]
    assert entries(domain.session) == [run_a] and run_tree(domain.store, run_a) == snapshot
