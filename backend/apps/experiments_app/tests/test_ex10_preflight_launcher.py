"""EX-10 fase 2B-3: launcher de host del preflight. LOCAL TEST / NON-EXPERIMENTAL.

Checkouts Git sintéticos y un Docker simulado (en proceso, o un ejecutable `docker` falso
para los tests de proceso). Ningún test lanza Docker, lee `.env`, usa el stack S11 ni
`var/s11-runs`, y el único JSON de preflight es el del `run_preflight` real con dobles.
"""

import ast
import importlib.util
import inspect
import json
import os
import stat
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.experiments_app import ex10_fixture, ex10_load
from apps.experiments_app import ex10_preflight as pf
from apps.experiments_app import ex10_prepare as pr
from apps.experiments_app import ex10_state as st
from apps.experiments_app.canonical import canonical_json_bytes
from apps.experiments_app.tests import test_ex10_preflight as pft

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "s11_ex10_preflight_launcher.py"
_SPEC = importlib.util.spec_from_file_location("s11_ex10_preflight_launcher", SCRIPT)
launcher = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = launcher
# Cargar el script no deja bytecode en `backend/scripts/`.
_BYTECODE_FLAG = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
    _SPEC.loader.exec_module(launcher)
finally:
    sys.dont_write_bytecode = _BYTECODE_FLAG

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
CUSTODY = "synthetic-custody-key-for-launcher-tests-32-bytes"
PUBLIC = "5e" * 32
DOCUMENT = "synthetic-document-hmac-secret-launcher"
TOKEN = "0badc0de"
PROJECT = launcher.PROJECT


# --- Checkout sintético ----------------------------------------------------------------------


def git(root, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def tree_state(root):
    """Huella de todo el checkout, `.git` incluido: contenido y mtime de cada archivo."""
    state = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            info = path.stat()
            state[str(path.relative_to(root))] = (path.read_bytes(), info.st_mtime_ns)
    return state


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """Checkout completo con los archivos exigidos, dos commits y G0/G0-A1 en la historia."""
    root = Path(os.path.realpath(tmp_path)) / "repo"
    for relative in launcher.REQUIRED_FILES:
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text("# LOCAL TEST placeholder\n")
    (root / "frontend").mkdir()
    (root / "frontend" / "package.json").write_text("{}\n")
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
    (root / "frontend" / "package.json").write_text('{"name": "x"}\n')
    git(root, "commit", "-q", "-am", "head")
    monkeypatch.setattr(launcher, "G0_DECISION_COMMIT", first)
    monkeypatch.setattr(launcher, "G0_A1_DECISION_COMMIT", second)
    home = Path(os.path.realpath(tmp_path)) / "home"
    home.mkdir(mode=0o700)
    head = git(root, "rev-parse", "HEAD")
    return SimpleNamespace(root=root, first=first, second=second, head=head, home=home)


# --- Docker simulado ---------------------------------------------------------------------------


def container(name, state, service, oneoff=False, healthy=False):
    return {"name": name, "state": state, "service": service, "oneoff": oneoff, "healthy": healthy}


def stack_containers(*, web=True):
    items = [
        container(f"{PROJECT}-db-1", "running", "db", healthy=True),
        container(f"{PROJECT}-redis-1", "running", "redis", healthy=True),
    ]
    if web:
        items += [
            container(f"{PROJECT}-backend-1", "running", "backend"),
            container(f"{PROJECT}-worker-1", "running", "worker"),
        ]
    return items


class FakeDocker:
    """El Docker del host tal como lo ve el launcher: listados, imagen, `run` y `rm -f`."""

    def __init__(self, *, stdout=b"", code=0, stderr=b"", web=True):
        self.containers = stack_containers(web=web)
        self.networks = [f"{PROJECT}_default"]
        self.volumes = [f"{PROJECT}_s11_postgres_data"]
        self.stdout, self.code, self.stderr = stdout, code, stderr
        self.calls, self.envs = [], []
        self.available = self.image = True
        self.leave_container = False
        self.abort = None
        self.on_run = None

    def factory(self, cwd, env):
        self.envs.append((cwd, dict(env)))
        return self

    def runs(self):
        """Sólo los `docker compose ... run`, el único comando que lanza un contenedor."""
        return [argv for argv in self.calls if argv[:2] == ["docker", "compose"] and "run" in argv]

    def run(self, argv, *, timeout=launcher.DOCKER_CLI_TIMEOUT):
        argv = list(argv)
        self.calls.append(argv)
        ok = launcher.Completed
        if argv[:2] == ["docker", "version"]:
            return ok(0 if self.available else 1, b"29.8.1\n" if self.available else b"", b"")
        if argv[:3] == ["docker", "compose", "version"]:
            return ok(0, b"5.5.1\n", b"")
        if argv[:3] == ["docker", "image", "ls"]:
            return ok(0, b"abc123\n" if self.image else b"", b"")
        if argv[:2] == ["docker", "ps"]:
            if "health=healthy" in argv:
                names = [c["name"] for c in self.containers if c["healthy"]]
            else:
                names = [
                    "\t".join([c["name"], c["state"], c["service"], str(c["oneoff"])])
                    for c in self.containers
                ]
            return ok(0, "".join(f"{line}\n" for line in names).encode(), b"")
        if argv[:3] == ["docker", "network", "ls"]:
            return ok(0, "".join(f"{n}\n" for n in self.networks).encode(), b"")
        if argv[:3] == ["docker", "volume", "ls"]:
            return ok(0, "".join(f"{n}\n" for n in self.volumes).encode(), b"")
        if argv[:2] == ["docker", "compose"] and "run" in argv:
            if self.on_run:
                self.on_run(argv)
            if self.leave_container:
                name = argv[argv.index("--name") + 1]
                self.containers.append(container(name, "running", "backend", oneoff=True))
            if self.abort:
                raise launcher.Aborted(self.abort)
            return ok(self.code, self.stdout, self.stderr)
        if argv[:3] == ["docker", "rm", "-f"]:
            self.containers = [c for c in self.containers if c["name"] != argv[3]]
            return ok(0, b"", b"")
        raise AssertionError(f"comando Docker inesperado: {argv[:3]}")


def environ(home, **changes):
    values = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "S11_CUSTODY_HMAC_KEY": CUSTODY,
        "S11_PUBLICATION_APPROVAL_PUBLIC_KEY": PUBLIC,
        "S11_DOCUMENT_HMAC_SECRET": DOCUMENT,
    }
    values.update(changes)
    return {k: v for k, v in values.items() if v is not None}


_COUNTER = iter(range(1, 10_000))


def invocation(checkout, fake, mode="preflight", **changes):
    values = {
        "mode": mode,
        "expected_git_sha": checkout.head,
        "repo_root": checkout.root,
        "output_dir": checkout.home / f"out-{next(_COUNTER):04d}",
        "environ": environ(checkout.home),
        "runner_factory": fake.factory,
        "clock": lambda: NOW,
        "token": lambda: TOKEN,
    }
    values.update(changes)
    return launcher.Invocation(**values)


def execute(checkout, fake, mode="preflight", **changes):
    inv = invocation(checkout, fake, mode, **changes)
    summary = launcher.execute(inv)
    return summary, Path(summary["output_dir"])


def meta(output):
    return json.loads((output / "launcher-meta.json").read_text())


# --- JSON reales de los comandos ---------------------------------------------------------------


@pytest.fixture
def preflight_json(settings, tmp_path):
    """El stdout de `run_preflight` real con los dobles de los tests de la fase 2B-2."""
    base = tmp_path / "pf"
    objects = base / "objects"
    objects.mkdir(parents=True)
    (objects / st.OBJECT_ROOT_MARKER).write_text("S11 isolated synthetic objects only\n")
    for name in ("runs", "backup", "restore", "source"):
        (base / name).mkdir()
    settings.S11_ARTIFACT_ROOT = base / "runs"
    settings.S11_BACKUP_ROOT = base / "backup"
    settings.S11_RESTORE_ROOT = base / "restore"
    settings.S11_OBJECT_ROOT = objects
    settings.REDIS_URL = "redis://redis:6379/11"

    def build(sha, breakage=None):
        repo, health, control = pft.FakeRepo(head=sha), pft.FakeHealth(), pft.FakeControl()
        environment = st.ApprovedEnvironment(pft.DECLARED_DB, objects, pft.FakeBrokerRedis())
        probes = pf.Probes(
            repository=lambda root: repo,
            approve_environment=lambda: environment,
            database_identity=lambda: dict.fromkeys(("database", "user", "host", "port"), True),
            settings_module=lambda: "config.settings.test",
            api_config=pft.api_config,
            throttle_config=lambda: pf.ThrottleConfig("5/hour", "report_create", True),
            health=health,
            inspect_ready_state=lambda env: pft.state_report(),
            category_ids=lambda slugs: {s: str(uuid.uuid5(uuid.NAMESPACE_URL, s)) for s in slugs},
            fixture_result=pft.fake_fixture_result,
            issue_tokens=pft.fake_issue_tokens,
            verify_tokens=pft.fake_verify_tokens,
            worker_control=control,
            clock=lambda: NOW,
            nonce=pft.nonces(),
        )
        if breakage == "fail":
            repo.dirty = 1
        elif breakage == "incomplete":
            health.error = pf.HealthUnavailable()
        options = pf.PreflightOptions(expected_git_sha=sha, source_root=base / "source")
        report = pf.run_preflight(options, probes)
        return report.to_bytes(), report.exit_code

    return build


def prepare_json(sha, mode="prepare", result="PASS"):
    """Un `PrepareReport` real: la misma serialización que emite `s11_ex10_prepare`."""
    specs = ex10_fixture.actor_specs()
    created = ex10_fixture.ActorResult.CREATED
    fixture = ex10_fixture.FixtureResult(
        ex10_fixture.FixtureOutcome.PREPARED,
        tuple(ex10_fixture.ActorOutcome(s.alias, str(s.user_id), None, created) for s in specs),
        NOW,
    )
    status = pf.CheckResult(result)
    ready = st.StateReport(
        st.StateName.READY_STATE,
        (st.StateCheck("users_total", 10, 10, st.CheckStatus(result)),),
    )
    prepare = mode == "prepare"
    report = pr.PrepareReport(
        mode=pr.Mode.PREPARE if prepare else pr.Mode.INSPECT,
        guards=(pf.Check("isolation_guards", pf.CheckResult.PASS, "approved", "approved"),),
        services={"backend_resolves": False},
        reset=pf.Check("reset", pf.CheckResult.PASS) if prepare else None,
        flags=pr.ResetFlags(True, True, True, True, True) if prepare else pr.ResetFlags(),
        fixture=fixture if prepare else None,
        ready_state=ready,
        final=(pf.Check("ready_state", status),),
        expected_git_sha=sha,
        git_sha=sha,
        checked_at=NOW,
    )
    return report.to_bytes(), report.exit_code


def rewrite(raw, **changes):
    payload = json.loads(raw)
    payload.update(changes)
    return canonical_json_bytes(payload)


# --- Checkout: validaciones previas a Docker ---------------------------------------------------


def test_a_complete_clean_checkout_is_accepted_without_changing_anything(checkout):
    before = tree_state(checkout.root)
    facts = launcher.verify_repository(checkout.root, checkout.head)
    assert facts.head == checkout.head and facts.root == str(checkout.root)
    assert facts.to_json()["owner_matches_operator"] is True
    assert tree_state(checkout.root) == before  # ni índice, ni refs, ni archivos


def rejected(checkout, root=None, sha=None, **kwargs):
    with pytest.raises(launcher.LauncherError) as error:
        launcher.verify_repository(root or checkout.root, sha or checkout.head, **kwargs)
    return error.value.code


@pytest.mark.parametrize(
    "change",
    [
        lambda root: (root / "infra" / "docker-compose.yml").write_text("changed\n"),
        lambda root: (root / "untracked.txt").write_text("new\n"),
        lambda root: git(root, "rm", "-q", "--cached", "frontend/package.json"),
    ],
    ids=["modified", "untracked", "staged"],
)
def test_a_dirty_checkout_is_rejected(checkout, change):
    change(checkout.root)
    assert rejected(checkout) == "working-tree-dirty"


def test_a_partial_checkout_is_rejected(checkout):
    assert rejected(checkout, root=checkout.root / "backend") == "source-root-not-git"
    (checkout.root / ".git" / "info").mkdir(exist_ok=True)
    (checkout.root / ".git" / "info" / "exclude").write_text("# comentario\nsecret/\n")
    assert rejected(checkout) == "local-excludes"
    (checkout.root / ".git" / "info" / "exclude").write_text("# sólo comentarios\n")
    git(checkout.root, "rm", "-q", launcher.REQUIRED_FILES[0])
    git(checkout.root, "commit", "-q", "-m", "drop a 2B-2 file")
    head = git(checkout.root, "rev-parse", "HEAD")
    assert rejected(checkout, sha=head) == "required-file-missing"


def test_a_shallow_clone_is_rejected(checkout, tmp_path):
    shallow = Path(os.path.realpath(tmp_path)) / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{checkout.root}", str(shallow)],
        check=True,
        capture_output=True,
    )
    assert rejected(checkout, root=shallow) == "shallow-history"


def test_a_sparse_checkout_is_rejected(checkout):
    git(checkout.root, "config", "core.sparseCheckout", "true")
    assert rejected(checkout) == "sparse-checkout"


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_hidden_index_entries_are_rejected(checkout, flag):
    git(checkout.root, "update-index", flag, "frontend/package.json")
    # Con la entrada oculta, un cambio no se vería en `git status`: se rechaza antes.
    (checkout.root / "frontend" / "package.json").write_text("hidden change\n")
    assert rejected(checkout) == "hidden-index-entries"


def test_gitfiles_are_rejected(checkout, tmp_path):
    worktree = Path(os.path.realpath(tmp_path)) / "worktree"
    git(checkout.root, "worktree", "add", "-q", str(worktree), "HEAD")
    # Un gitfile apunta fuera de la raíz: montada sola, el contenedor no lo resolvería.
    assert rejected(checkout, root=worktree) == "gitfile-not-mountable"
    (worktree / ".git").write_text("gitdir: /no/such/admin/dir\n")
    assert rejected(checkout, root=worktree) == "broken-gitfile"
    (worktree / ".git").write_text("not a gitfile\n")
    assert rejected(checkout, root=worktree) == "broken-gitfile"


def test_a_wrong_head_is_rejected(checkout):
    assert rejected(checkout, sha=checkout.first) == "head-mismatch"
    assert rejected(checkout, sha="not-a-sha") == "expected-git-sha-format"


@pytest.mark.parametrize("name", ["G0_DECISION_COMMIT", "G0_A1_DECISION_COMMIT"])
def test_decision_commits_must_be_in_history(checkout, monkeypatch, name):
    code = "g0-not-in-history" if name == "G0_DECISION_COMMIT" else "g0-a1-not-in-history"
    monkeypatch.setattr(launcher, name, "f" * 40)  # no existe en el repositorio
    assert rejected(checkout) == code
    # Existe, pero en otra rama: tampoco es ancestro de HEAD.
    git(checkout.root, "checkout", "-q", "--orphan", "side")
    git(checkout.root, "commit", "-q", "-m", "side")
    side = git(checkout.root, "rev-parse", "HEAD")
    git(checkout.root, "checkout", "-q", "main")
    monkeypatch.setattr(launcher, name, side)
    assert rejected(checkout) == code


def test_the_root_must_be_the_exact_realpath(checkout, tmp_path):
    link = Path(os.path.realpath(tmp_path)) / "link"
    link.symlink_to(checkout.root, target_is_directory=True)
    assert rejected(checkout, root=link) == "repo-root-not-realpath"
    assert rejected(checkout, root=Path("relative")) == "repo-root-not-realpath"


def test_a_foreign_owned_checkout_is_always_rejected_on_the_host(checkout, monkeypatch):
    # Git ejecutaría como el operador la configuración (filtros, fsmonitor) de un checkout
    # ajeno: el host nunca usa `safe.directory`, tampoco con `--git-safe-directory`.
    real = os.getuid()
    monkeypatch.setattr(launcher.os, "getuid", lambda: real + 1)
    assert rejected(checkout) == "checkout-owner-mismatch"
    # No hay forma de pedirle al host que confíe en un checkout ajeno.
    assert list(inspect.signature(launcher.verify_repository).parameters) == [
        "root",
        "expected_git_sha",
    ]


def test_host_git_runs_without_global_or_system_config_or_optional_locks(checkout, monkeypatch):
    seen = []
    original = launcher.subprocess.run

    def recording(argv, *args, **kwargs):
        seen.append((list(argv), kwargs["env"]))
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(launcher.subprocess, "run", recording)
    launcher.verify_repository(checkout.root, checkout.head)
    for argv, env in seen:
        assert argv[:2] == ["git", "--no-optional-locks"]
        assert (env["GIT_CONFIG_GLOBAL"], env["GIT_CONFIG_NOSYSTEM"]) == ("/dev/null", "1")
        assert env["GIT_OPTIONAL_LOCKS"] == "0" and env["HOME"] == "/nonexistent"
        assert not any(token.startswith("safe.directory") for token in argv)


def test_validation_failures_never_reach_docker(checkout):
    (checkout.root / "untracked.txt").write_text("x\n")
    fake = FakeDocker()
    summary, output = execute(checkout, fake)
    assert (summary["classification"], summary["launcher_exit"]) == ("VALIDATION_FAILED", 3)
    assert summary["problems"] == ["working-tree-dirty"]
    assert fake.calls == []
    assert sorted(p.name for p in output.iterdir()) == ["exit-code.txt", "launcher-meta.json"]


# --- Comando construido ------------------------------------------------------------------------


def built(checkout, mode="preflight", gitconfig=None):
    return launcher.build_command(
        launcher.PRODUCTION,
        mode,
        expected_git_sha=checkout.head,
        container_name=f"{PROJECT}-launcher-{mode}-{TOKEN}",
        repo_root=checkout.root,
        gitconfig=gitconfig,
    )


def mounts(argv):
    return [argv[i + 1] for i, token in enumerate(argv) if token == "-v"]


@pytest.mark.parametrize("mode", ["prepare", "inspect", "preflight"])
def test_exactly_one_read_only_mount_of_the_full_checkout(checkout, mode):
    argv = built(checkout, mode)
    assert mounts(argv) == [f"{checkout.root}:/s11-full-source:ro"]
    assert all(mount.endswith(":ro") for mount in mounts(argv))
    assert sum(1 for token in argv if str(checkout.root) in token) == 1
    tail = argv[argv.index("backend") + 1 :]
    assert tail[-4:] == ["--expected-git-sha", checkout.head, "--source-root", "/s11-full-source"]
    assert "/s11-source" not in argv
    assert argv[: argv.index("run")] == [
        "docker",
        "compose",
        "-p",
        PROJECT,
        "--env-file",
        "/dev/null",
        "-f",
        "infra/docker-compose.yml",
        "-f",
        "infra/docker-compose.s11.yml",
    ]
    options = argv[argv.index("run") + 1 : argv.index("backend")]
    assert {"--rm", "--no-deps", "-T"} <= set(options) and "--use-aliases" not in options
    assert options[options.index("--pull") + 1] == "never"


def safe(argv, checkout, mode="preflight"):
    try:
        launcher.assert_safe_command(argv, repo_root=checkout.root, mode=mode)
    except launcher.LauncherError as exc:
        return exc.code
    return None


def _replace(argv, old, new, start=0):
    argv[argv.index(old, start)] = new


def _before_service(argv, *tokens):
    index = argv.index("backend")
    argv[index:index] = list(tokens)


def _checkout_mount(root):
    return f"{root}:/s11-full-source:ro"


UNSAFE = {
    "no-ro": (
        lambda a, r: _replace(a, _checkout_mount(r), f"{r}:/s11-full-source"),
        "rw-mount",
    ),
    "rw": (
        lambda a, r: _replace(a, _checkout_mount(r), f"{r}:/s11-full-source:rw"),
        "rw-mount",
    ),
    "piece-mount": (
        lambda a, r: _before_service(a, "-v", f"{r}/backend:/s11-full-source/b:ro"),
        "extra-mount",
    ),
    "partial-source-root": (
        lambda a, r: _replace(a, "/s11-full-source", "/s11-source", a.index("--source-root")),
        "container-command",
    ),
    "aliases": (lambda a, r: _before_service(a, "--use-aliases"), "run-options"),
    "user": (lambda a, r: _before_service(a, "--user", "1004:1004"), "run-options"),
    "env": (lambda a, r: _before_service(a, "-e", "GIT_CONFIG_COUNT=1"), "run-options"),
    "pull": (lambda a, r: _replace(a, "never", "always"), "run-options"),
    "other-command": (
        lambda a, r: _replace(a, "s11_ex10_preflight", "s11_ex10_run"),
        "container-command",
    ),
    "run-id": (lambda a, r: a.append("--run-id"), "container-command"),
    "config": (lambda a, r: a.insert(a.index("run"), "config"), "not-compose-run"),
}


@pytest.mark.parametrize("name", list(UNSAFE))
def test_any_unsafe_command_is_refused(checkout, name):
    mutate, problem = UNSAFE[name]
    argv = built(checkout)
    mutate(argv, checkout.root)
    code = safe(argv, checkout)
    assert code is not None and problem in code, code


def test_the_safe_directory_config_is_exact(checkout):
    config = checkout.home / "safe-directory.gitconfig"
    argv = built(checkout, gitconfig=config)
    assert mounts(argv) == [
        f"{checkout.root}:/s11-full-source:ro",
        f"{config}:/etc/gitconfig:ro",
    ]
    assert launcher.SAFE_DIRECTORY_CONFIG == "[safe]\n\tdirectory = /s11-full-source\n"
    assert "*" not in launcher.SAFE_DIRECTORY_CONFIG
    assert safe(argv, checkout) is None
    with pytest.raises(launcher.LauncherError, match="extra-mount"):
        built(checkout, gitconfig=checkout.root / "gitconfig")  # nunca un archivo del checkout


# --- Ejecución con Docker simulado -------------------------------------------------------------


def test_a_perfect_preflight_is_coherent_and_persisted(checkout, preflight_json):
    raw, code = preflight_json(checkout.head)
    assert code == 0
    progress = f" Container {PROJECT}-launcher-preflight-{TOKEN} Creating \n"
    fake = FakeDocker(stdout=raw, stderr=progress.encode())
    summary, output = execute(checkout, fake)
    assert summary["classification"] == "COHERENT"
    assert (summary["launcher_exit"], summary["result"], summary["status"]) == (
        0,
        "PASS",
        "READY_FOR_EXECUTOR_INTEGRATION",
    )
    assert (output / "preflight.json").read_bytes() == raw
    data = meta(output)
    assert data["stdout"]["persisted"] is True and data["stdout"]["secret_hits"] == 0
    assert data["cleanup"]["container_absent"] is True
    assert data["docker_after_matches_before"] is True
    assert (data["run_id"], data["evidence_written"]) == (None, False)
    assert data["source_root_container"] == "/s11-full-source"
    assert data["safe_directory"] is None
    assert len(fake.runs()) == 1


@pytest.mark.parametrize(
    ("breakage", "code", "result"), [("fail", 1, "FAIL"), ("incomplete", 2, "INCOMPLETE")]
)
def test_fail_and_incomplete_keep_their_exit_codes(
    checkout, preflight_json, breakage, code, result
):
    raw, exit_code = preflight_json(checkout.head, breakage)
    assert exit_code == code
    summary, output = execute(checkout, FakeDocker(stdout=raw, code=code))
    assert (summary["classification"], summary["launcher_exit"], summary["result"]) == (
        "COHERENT",
        code,
        result,
    )
    assert (output / "preflight.json").read_bytes() == raw


def test_a_usage_error_of_the_command_is_exit_3(checkout):
    stderr = b"CommandError: S11_EX10_PREFLIGHT_CONFIG: source-root-missing\n"
    summary, output = execute(checkout, FakeDocker(code=3, stderr=stderr))
    assert (summary["classification"], summary["launcher_exit"]) == (
        "COMMAND_USAGE_OR_CONFIG_ERROR",
        3,
    )
    assert (output / "preflight.stderr").read_bytes() == stderr
    assert not (output / "preflight.json").exists()


@pytest.mark.parametrize(
    ("stdout_of", "code", "problem"),
    [
        ("pass", 1, "exit-result-mismatch"),
        ("fail", 0, "exit-result-mismatch"),
        ("incomplete", 0, "exit-result-mismatch"),
        ("pass", 3, "stdout-on-usage-error"),
        ("empty", 0, "stdout-not-json"),
        ("empty", 2, "stdout-not-json"),
        ("pass", 137, "exit-code:137"),
    ],
)
def test_json_and_exit_code_must_agree(checkout, preflight_json, stdout_of, code, problem):
    breakage = None if stdout_of in {"pass", "empty"} else stdout_of
    raw = b"" if stdout_of == "empty" else preflight_json(checkout.head, breakage)[0]
    summary, output = execute(checkout, FakeDocker(stdout=raw, code=code))
    assert (summary["classification"], summary["launcher_exit"]) == ("LAUNCHER_FAILURE", 4)
    assert problem in summary["problems"]
    assert summary["result"] is None  # lo afirmado nunca se convierte en resultado


@pytest.mark.parametrize(
    "stdout",
    [
        b"not json\n",
        b'{"a": 1}\n{"b": 2}\n',
        b'{"b":1, "a":2}\n',
        b"[1,2]\n",
        b"\xff\xfe\n",
    ],
    ids=["text", "two-lines", "not-canonical", "array", "not-utf8"],
)
def test_invalid_stdout_is_a_launcher_failure_and_is_not_persisted(checkout, stdout):
    summary, output = execute(checkout, FakeDocker(stdout=stdout, code=0))
    assert (summary["classification"], summary["launcher_exit"]) == ("LAUNCHER_FAILURE", 4)
    assert not (output / "preflight.json").exists()


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"run_id": "s11-00000000-0000-5000-8000-000000000001"}, "invariant:run_id"),
        ({"evidence_written": True}, "invariant:evidence_written"),
        ({"report_posts": 1}, "invariant:report_posts"),
        ({"provider_calls": 1}, "invariant:provider_calls"),
        ({"task_publishes": 1}, "invariant:task_publishes"),
        ({"ready_for_execution": True}, "invariant:ready_for_execution"),
        ({"control_probes": 3}, "control-probes"),
        ({"health_requests": 2}, "health-requests"),
        ({"gate": "NOT_READY"}, "gate-mismatch"),
        ({"expected_git_sha": "e" * 40}, "expected-git-sha"),
        ({"schema_version": "other"}, "schema-version"),
        ({"extra": 1}, "payload-keys"),
    ],
)
def test_preflight_invariants_are_enforced(checkout, preflight_json, changes, problem):
    raw = rewrite(preflight_json(checkout.head)[0], **changes)
    summary, _ = execute(checkout, FakeDocker(stdout=raw, code=0))
    assert summary["classification"] == "LAUNCHER_FAILURE"
    assert problem in summary["problems"]


def test_a_pass_with_a_not_run_check_is_a_bug_not_a_pass(checkout, preflight_json):
    payload = json.loads(preflight_json(checkout.head)[0])
    payload["checks"][3] = {**payload["checks"][3], "result": "NOT_RUN"}
    summary, _ = execute(checkout, FakeDocker(stdout=canonical_json_bytes(payload), code=0))
    assert summary["classification"] == "LAUNCHER_FAILURE"
    assert "pass-with-non-pass-checks" in summary["problems"]


def test_stderr_is_sanitized_and_secrets_never_persist(checkout, preflight_json):
    raw = preflight_json(checkout.head)[0]
    stderr = "\n".join(
        [
            f" Container {PROJECT}-launcher-preflight-{TOKEN} Creating ",
            "CommandError: S11_EX10_PREFLIGHT_CONFIG: source-root-missing",
            "Traceback (most recent call last):",
            f"S11_CUSTODY_HMAC_KEY={CUSTODY}",
            "redis://user:pw@redis:6379/11",
            "Authorization: Bearer abc.def.ghi",
        ]
    ).encode()
    summary, output = execute(checkout, FakeDocker(stdout=raw, stderr=stderr))
    text = (output / "preflight.stderr").read_text()
    assert text.splitlines() == [
        f" Container {PROJECT}-launcher-preflight-{TOKEN} Creating ",
        "CommandError: S11_EX10_PREFLIGHT_CONFIG: source-root-missing",
        *["[redacted-line]"] * 4,
    ]
    assert meta(output)["stderr"]["redacted_secret_lines"] == 0
    data = meta(output)
    assert data["stderr"]["redacted"] == 4 and data["stderr"]["raw_exact_secret_values"] == 1
    # Un secreto en el stderr crudo es un fallo del aparato aunque no llegue a disco.
    assert summary["classification"] == "LAUNCHER_FAILURE"
    assert "stderr-raw-secret-value" in summary["problems"]
    everything = b"".join(p.read_bytes() for p in output.iterdir())
    for value in (CUSTODY, DOCUMENT, PUBLIC, "user:pw", "Bearer"):
        assert value.encode() not in everything


def test_a_secret_inside_an_allowed_stderr_line_is_never_persisted(checkout, preflight_json):
    raw = preflight_json(checkout.head)[0]
    stderr = f"CommandError: S11_EX10_PREFLIGHT_CONFIG: {CUSTODY}\n".encode()
    summary, output = execute(checkout, FakeDocker(stdout=raw, stderr=stderr))
    assert (output / "preflight.stderr").read_text() == "[redacted-secret-line]\n"
    assert meta(output)["stderr"]["redacted_secret_lines"] == 1
    assert summary["classification"] == "LAUNCHER_FAILURE"
    assert CUSTODY.encode() not in b"".join(p.read_bytes() for p in output.iterdir())


def test_a_secret_in_stdout_is_never_persisted(checkout, preflight_json):
    payload = json.loads(preflight_json(checkout.head)[0])
    payload["checks"][0]["detail_code"] = f"leak {DOCUMENT}"
    summary, output = execute(checkout, FakeDocker(stdout=canonical_json_bytes(payload)))
    assert summary["classification"] == "LAUNCHER_FAILURE"
    assert "stdout-secret:exact-secret-value" in summary["problems"]
    assert not (output / "preflight.json").exists()
    assert meta(output)["stdout"]["secret_hits"] == 1
    assert DOCUMENT.encode() not in b"".join(p.read_bytes() for p in output.iterdir())


def test_the_child_environment_is_built_from_scratch(checkout, preflight_json):
    raw = preflight_json(checkout.head)[0]
    fake = FakeDocker(stdout=raw)
    extra = {"COMPOSE_FILE": "evil.yml", "COMPOSE_PROJECT_NAME": "ciudadania-s11", "AWS_KEY": "x"}
    execute(checkout, fake, environ={**environ(checkout.home), **extra})
    env = fake.envs[0][1]
    assert set(env) == {
        "HOME",
        "PATH",
        "S11_CUSTODY_HMAC_KEY",
        "S11_PUBLICATION_APPROVAL_PUBLIC_KEY",
        "S11_DOCUMENT_HMAC_SECRET",
        "S11_HOST_UID",
        "S11_HOST_GID",
    }
    assert (env["S11_HOST_UID"], env["S11_HOST_GID"]) == (str(os.getuid()), str(os.getgid()))
    # Los nombres quedan en el meta; los valores, nunca.
    output = Path(launcher.execute(invocation(checkout, FakeDocker(stdout=raw)))["output_dir"])
    assert meta(output)["child_env_names"] == sorted(env)
    assert CUSTODY.encode() not in (output / "launcher-meta.json").read_bytes()


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"S11_HOST_UID": "0"}, "s11-host-uid-mismatch"),
        ({"S11_HOST_GID": "0"}, "s11-host-gid-mismatch"),
        ({"S11_CUSTODY_HMAC_KEY": None}, "missing-env:S11_CUSTODY_HMAC_KEY"),
        ({"S11_DOCUMENT_HMAC_SECRET": " "}, "missing-env:S11_DOCUMENT_HMAC_SECRET"),
    ],
)
def test_uid_gid_and_interpolation_variables_are_checked(checkout, changes, problem):
    if os.getuid() == 0 and "UID" in problem:
        pytest.skip("el uid del proceso ya es 0")
    fake = FakeDocker()
    summary, _ = execute(checkout, fake, environ=environ(checkout.home, **changes))
    assert (summary["classification"], summary["problems"]) == ("VALIDATION_FAILED", [problem])
    assert fake.calls == []


#: Audit hook de la suite: inerte salvo mientras un test registra aperturas.
recording = []


def _audit(event, args):
    for hook in list(recording):
        hook(event, args)


sys.addaudithook(_audit)


def test_the_launcher_never_reads_dotenv(checkout, preflight_json):
    (checkout.root / ".env").write_text("S11_CUSTODY_HMAC_KEY=planted-from-dotenv-file\n")
    opened = []

    def hook(event, args):
        if event == "open" and args and isinstance(args[0], str | bytes | os.PathLike):
            opened.append(os.fsdecode(args[0]))

    recording.append(hook)
    fake = FakeDocker(stdout=preflight_json(checkout.head)[0])
    try:
        summary, output = execute(checkout, fake)
    finally:
        recording.remove(hook)
    assert summary["classification"] == "COHERENT"
    assert not [path for path in opened if os.path.basename(path) == ".env"]
    assert fake.envs[0][1]["S11_CUSTODY_HMAC_KEY"] == CUSTODY
    assert fake.runs()[0][fake.runs()[0].index("--env-file") + 1] == "/dev/null"


def test_no_compose_config_prune_rmi_or_post_is_ever_run(checkout, preflight_json):
    fake = FakeDocker(stdout=preflight_json(checkout.head)[0])
    fake.leave_container = True
    for mode in ("preflight", "inspect"):
        execute(checkout, fake, mode)
    fake = FakeDocker(stdout=prepare_json(checkout.head)[0], web=False)
    execute(checkout, fake, "prepare")
    tokens = [token for argv in fake.calls for token in argv]
    for word in ("config", "prune", "--rmi", "down", "POST", "exec", "--build"):
        assert word not in tokens, word


def test_the_script_has_no_http_client_or_load_mode():
    tree = ast.parse(SCRIPT.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported.isdisjoint({"urllib", "http", "socket", "requests", "ssl", "django", "apps"})
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "POST" not in literals
    assert set(launcher.MODES) == {"prepare", "inspect", "preflight"}
    assert launcher.ALLOWED_COMMANDS == {"s11_ex10_prepare", "s11_ex10_preflight"}


@pytest.mark.parametrize(
    "stdout",
    [b'{"schema_version":null}\n', b'{"health_requests":NaN}\n', b'{"checks":{"a":1}}\n'],
    ids=["null-schema", "nan", "checks-object"],
)
def test_malformed_json_is_a_launcher_failure_with_its_record(checkout, stdout):
    summary, output = execute(checkout, FakeDocker(stdout=stdout, code=0))
    assert (summary["classification"], summary["launcher_exit"]) == ("LAUNCHER_FAILURE", 4)
    assert summary["result"] is None
    data = meta(output)
    assert (output / "exit-code.txt").exists() and data["classification"] == "LAUNCHER_FAILURE"
    # Sólo se conserva, para diagnosticar, un stdout canónico y sin secretos; nunca un NaN.
    assert (output / "preflight.json").exists() is data["stdout"]["persisted"]
    assert data["stdout"]["persisted"] is (b"NaN" not in stdout)


def test_masked_untracked_env_files_never_reach_the_container(checkout, preflight_json):
    (checkout.root / ".env").write_text("S11_CUSTODY_HMAC_KEY=planted\n")
    (checkout.root / ".env.supabase.local").write_text("SUPABASE_SERVICE_ROLE_KEY=x\n")
    (checkout.root / "frontend" / ".env.local").write_text("NEXT_PUBLIC_X=1\n")
    (checkout.root / ".gitignore").write_text("__pycache__/\n.env\n.env.*\n/var/\n")
    (checkout.root / ".env.example").write_text("S11_CUSTODY_HMAC_KEY=\n")
    (checkout.root / "frontend" / "node_modules" / "pkg").mkdir(parents=True)
    (checkout.root / "frontend" / "node_modules" / "pkg" / ".env").write_text("vendored\n")
    git(checkout.root, "add", "-f", ".gitignore", ".env.example")
    git(checkout.root, "commit", "-q", "-m", "env example")
    head = git(checkout.root, "rev-parse", "HEAD")
    fake = FakeDocker(stdout=preflight_json(head)[0])
    summary, output = execute(checkout, fake, expected_git_sha=head)
    assert summary["launcher_exit"] == 0
    run = fake.runs()[0]
    masks = sorted(m for m in mounts(run) if m.startswith("/dev/null:"))
    assert masks == [
        "/dev/null:/s11-full-source/.env.supabase.local:ro",
        "/dev/null:/s11-full-source/.env:ro",
        "/dev/null:/s11-full-source/frontend/.env.local:ro",
    ]
    # El `.env.example` versionado y lo vendorizado en node_modules no se tapan.
    assert not any(".env.example" in m or "node_modules" in m for m in masks)
    assert meta(output)["repository"]["masked_untracked_env_files"] == [
        ".env",
        ".env.supabase.local",
        "frontend/.env.local",
    ]
    assert b"planted" not in b"".join(p.read_bytes() for p in output.iterdir())


def test_only_the_listed_masks_are_accepted(checkout):
    argv = launcher.build_command(
        launcher.PRODUCTION,
        "preflight",
        expected_git_sha=checkout.head,
        container_name=f"{PROJECT}-launcher-preflight-{TOKEN}",
        repo_root=checkout.root,
        masks=(".env",),
    )
    assert "/dev/null:/s11-full-source/.env:ro" in mounts(argv)
    for mount in ("/dev/null:/s11-full-source/other:ro", "/dev/null:/s11-full-source/.env"):
        bad = list(argv)
        bad.insert(bad.index("backend"), "-v")
        bad.insert(bad.index("backend"), mount)
        code = safe(bad, checkout)
        assert code and ("extra-mount" in code or "rw-mount" in code), code


def test_a_sibling_path_with_the_same_prefix_is_not_the_checkout(checkout, tmp_path):
    base = Path(os.path.realpath(tmp_path))
    config = base / "repo-out" / "safe-directory.gitconfig"  # `/…/repo-out` empieza por `/…/repo`
    argv = built(checkout, gitconfig=config)
    assert safe(argv, checkout) is None


def test_a_large_info_exclude_is_a_local_exclude_like_in_the_preflight(checkout):
    exclude = checkout.root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(exist_ok=True)
    exclude.write_text("# comentario\n" * 6000)  # más de 64 KiB, sólo comentarios
    assert rejected(checkout) == "local-excludes"


def check_json(name, result, detail=None):
    return {
        "check": name,
        "result": result,
        "expected": None,
        "observed_sanitized": None,
        "detail_code": detail,
    }


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        (
            {"final_checks": [check_json("broker_state", "FAIL", "x")]},
            "pass-with-non-pass-final-checks",
        ),
        ({"reset": check_json("reset", "FAIL", "x")}, "pass-without-reset"),
        ({"git_sha": "d" * 40}, "pass-git-sha"),
        ({"guards": []}, "pass-with-non-pass-guards"),
    ],
    ids=["final-fail", "reset-fail", "git-sha", "no-guards"],
)
def test_a_prepare_pass_must_be_complete(checkout, changes, problem):
    raw = rewrite(prepare_json(checkout.head)[0], **changes)
    summary, _ = execute(checkout, FakeDocker(stdout=raw, web=False), "prepare")
    assert summary["classification"] == "LAUNCHER_FAILURE"
    assert problem in summary["problems"]


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"database_reset": True}, "inspect-with-reset"),
        ({"reset": check_json("reset", "PASS")}, "inspect-with-reset"),
        ({"actors": []}, "inspect-with-fixture"),
    ],
    ids=["flag", "reset", "actors"],
)
def test_an_inspection_never_claims_a_reset(checkout, changes, problem):
    raw = rewrite(prepare_json(checkout.head, "inspect")[0], **changes)
    summary, _ = execute(checkout, FakeDocker(stdout=raw), "inspect")
    assert summary["classification"] == "LAUNCHER_FAILURE"
    assert problem in summary["problems"]


def test_docker_resources_that_change_or_cannot_be_listed_fail(checkout, preflight_json):
    fake = FakeDocker(stdout=preflight_json(checkout.head)[0])
    fake.on_run = lambda argv: fake.networks.append(f"{PROJECT}_stray")
    summary, _ = execute(checkout, fake)
    assert "docker-resources-changed" in summary["problems"]
    fake = FakeDocker(stdout=preflight_json(checkout.head)[0])
    real_run = fake.run
    state = {"ran": False}

    def listing_breaks_after_run(argv, **kwargs):
        if argv[:2] == ["docker", "compose"] and "run" in argv:
            state["ran"] = True
        elif state["ran"] and argv[:3] == ["docker", "volume", "ls"]:
            return launcher.Completed(1, b"", b"")
        return real_run(argv, **kwargs)

    fake.run = listing_breaks_after_run
    summary, output = execute(checkout, fake)
    assert summary["classification"] == "LAUNCHER_FAILURE"
    assert "docker-state-after-unavailable" in summary["problems"]
    assert not (output / "docker-after.txt").exists()


def test_the_real_runner_turns_a_timeout_into_an_abort(tmp_path):
    slow = tmp_path / "slow"
    slow.write_text("#!/bin/sh\nsleep 30\n")
    slow.chmod(0o700)
    runner = launcher.DockerRunner(tmp_path, {"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    started = time.monotonic()
    with pytest.raises(launcher.Aborted) as aborted:
        runner.run([str(slow)], timeout=0.5)
    # La señal va al grupo en el acto: sin esperar la gracia de 5 s al `sleep` que hereda
    # la tubería (señalar sólo al hijo dejaría la espera colgada hasta el respaldo).
    assert aborted.value.kind == "timeout" and time.monotonic() - started < 4


# --- Salida, permisos, limpieza ----------------------------------------------------------------


def test_output_dir_is_0700_files_0600_and_outside_the_repository(checkout, preflight_json):
    (checkout.root / "var" / "s11-runs").mkdir(parents=True)
    (checkout.root / "var" / "s11-runs" / "SENTINEL").write_text("evidence\n")
    before = tree_state(checkout.root)
    summary, output = execute(checkout, FakeDocker(stdout=preflight_json(checkout.head)[0]))
    assert summary["launcher_exit"] == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    names = sorted(p.name for p in output.iterdir())
    assert names == [
        "docker-after.txt",
        "docker-before.txt",
        "exit-code.txt",
        "launcher-meta.json",
        "preflight.json",
        "preflight.stderr",
    ]
    assert {stat.S_IMODE(p.stat().st_mode) for p in output.iterdir()} == {0o600}
    assert tree_state(checkout.root) == before  # ni `var/` ni el checkout cambian
    assert not str(output).startswith(str(checkout.root))
    docker_before = (output / "docker-before.txt").read_text()
    assert f"container\t{PROJECT}-db-1\trunning\tdb\toneoff=False\thealthy=True" in docker_before


def test_the_output_dir_is_new_and_never_inside_the_repository(checkout, preflight_json):
    fake = FakeDocker(stdout=preflight_json(checkout.head)[0])
    for target in (checkout.root / "out", checkout.root / "var" / "s11-runs-out", checkout.home):
        with pytest.raises(launcher.LauncherError):
            launcher.execute(invocation(checkout, fake, output_dir=target))
    with pytest.raises(launcher.LauncherError, match="output-dir-not-absolute"):
        launcher.execute(invocation(checkout, fake, output_dir=Path("relative")))
    loose = checkout.home / "loose"
    loose.mkdir(mode=0o777)
    loose.chmod(0o777)
    with pytest.raises(launcher.LauncherError, match="output-dir-parent-unsafe"):
        launcher.execute(invocation(checkout, fake, output_dir=loose / "out"))
    assert fake.calls == []


def test_cleanup_removes_only_its_own_container_by_exact_name(checkout, preflight_json):
    fake = FakeDocker(stdout=preflight_json(checkout.head)[0])
    fake.leave_container = True
    foreign = container(f"{PROJECT}-launcher-preflight-other", "running", "backend", oneoff=True)
    fake.containers.append(foreign)
    summary, output = execute(checkout, fake)
    removals = [argv for argv in fake.calls if argv[:2] == ["docker", "rm"]]
    assert removals == [["docker", "rm", "-f", f"{PROJECT}-launcher-preflight-{TOKEN}"]]
    assert foreign in fake.containers
    cleanup = meta(output)["cleanup"]
    assert (cleanup["container_present_after_run"], cleanup["container_removed"]) == (True, True)
    assert cleanup["container_absent"] is True and summary["launcher_exit"] == 0


def test_a_container_that_cannot_be_removed_is_a_launcher_failure(checkout, preflight_json):
    fake = FakeDocker(stdout=preflight_json(checkout.head)[0])
    fake.leave_container = True
    real_run = fake.run

    def stuck(argv, **kwargs):
        if argv[:2] == ["docker", "rm"]:
            return launcher.Completed(1, b"", b"")
        return real_run(argv, **kwargs)

    fake.run = stuck
    summary, _ = execute(checkout, fake)
    assert summary["classification"] == "LAUNCHER_FAILURE"
    assert "cleanup-failed" in summary["problems"]


def test_a_timeout_is_an_apparatus_abort_and_is_cleaned_up(checkout):
    fake = FakeDocker()
    fake.leave_container, fake.abort = True, "timeout"
    summary, output = execute(checkout, fake)
    assert (summary["classification"], summary["launcher_exit"]) == ("APPARATUS_ABORT", 5)
    assert summary["result"] is None and summary["problems"] == ["timeout"]
    assert meta(output)["cleanup"]["container_absent"] is True
    assert (output / "exit-code.txt").read_text().startswith("command_exit=none\n")


def test_the_safe_directory_mode_mounts_and_removes_the_exact_config(checkout, preflight_json):
    fake = FakeDocker(stdout=preflight_json(checkout.head)[0])
    seen = {}

    def inspect_config(argv):
        source = mounts(argv)[1].split(":")[0]
        seen["content"] = Path(source).read_text()
        seen["mode"] = stat.S_IMODE(os.stat(source).st_mode)
        seen["mount"] = mounts(argv)[1].split(":", 1)[1]

    fake.on_run = inspect_config
    summary, output = execute(checkout, fake, safe_directory=True)
    assert summary["launcher_exit"] == 0
    assert seen == {
        "content": "[safe]\n\tdirectory = /s11-full-source\n",
        "mode": 0o644,
        "mount": "/etc/gitconfig:ro",
    }
    assert not (output / "safe-directory.gitconfig").exists()
    data = meta(output)
    assert (data["safe_directory"], data["cleanup"]["gitconfig_removed"]) == (
        "/s11-full-source",
        True,
    )


# --- Modos prepare e inspect -------------------------------------------------------------------


def test_prepare_requires_stopped_services_and_healthy_db_redis(checkout):
    raw = prepare_json(checkout.head)[0]
    fake = FakeDocker(stdout=raw, web=True)
    summary, _ = execute(checkout, fake, "prepare")
    assert (summary["classification"], summary["problems"]) == (
        "VALIDATION_FAILED",
        ["ex10-services-active"],
    )
    assert fake.runs() == []
    fake = FakeDocker(stdout=raw, web=False)
    fake.containers.append(container(f"{PROJECT}-beat-1", "paused", "beat"))
    assert execute(checkout, fake, "prepare")[0]["problems"] == ["ex10-services-active"]
    fake = FakeDocker(stdout=raw, web=False)
    fake.containers.append(container(f"{PROJECT}-launcher-x", "running", "backend", oneoff=True))
    assert execute(checkout, fake, "prepare")[0]["problems"] == ["ex10-services-active"]
    fake = FakeDocker(stdout=raw, web=False)
    fake.containers[1]["healthy"] = False
    assert execute(checkout, fake, "prepare")[0]["problems"] == ["db-redis-not-healthy"]
    fake = FakeDocker(stdout=raw, web=False)
    fake.containers.append(container(f"{PROJECT}-worker-1", "exited", "worker"))
    summary, output = execute(checkout, fake, "prepare")
    assert (summary["classification"], summary["launcher_exit"]) == ("COHERENT", 0)
    run = fake.runs()[0]
    assert run[run.index("backend") + 1 :][:4] == [
        "python",
        "manage.py",
        "s11_ex10_prepare",
        "--confirm-isolated-reset",
    ]
    assert (output / "prepare.json").read_bytes() == raw


@pytest.mark.parametrize(
    ("mode", "changes", "problem"),
    [
        ("prepare", {"mode": "INSPECT"}, "mode-mismatch"),
        ("prepare", {"state": "NOT_READY"}, "state-mismatch"),
        ("prepare", {"database_reset": None}, "pass-without-complete-reset"),
        ("prepare", {"actors": []}, "pass-without-actors"),
        ("inspect", {"run_id": "s11-x"}, "invariant:run_id"),
        ("inspect", {"ready_state": None}, "pass-without-ready-state"),
    ],
)
def test_prepare_and_inspect_outputs_are_validated(checkout, mode, changes, problem):
    raw = rewrite(prepare_json(checkout.head, mode)[0], **changes)
    summary, _ = execute(checkout, FakeDocker(stdout=raw, web=False), mode)
    assert summary["classification"] == "LAUNCHER_FAILURE"
    assert problem in summary["problems"]


@pytest.mark.parametrize(("result", "code"), [("FAIL", 1), ("INCOMPLETE", 2)])
def test_inspect_keeps_the_exit_code_of_a_coherent_result(checkout, result, code):
    raw, exit_code = prepare_json(checkout.head, "inspect", result)
    assert exit_code == code
    summary, _ = execute(checkout, FakeDocker(stdout=raw, code=code), "inspect")
    assert (summary["classification"], summary["launcher_exit"], summary["result"]) == (
        "COHERENT",
        code,
        result,
    )


def test_docker_or_image_unavailable_is_a_validation_failure(checkout):
    fake = FakeDocker()
    fake.available = False
    assert execute(checkout, fake)[0]["problems"] == ["docker-unavailable"]
    fake = FakeDocker()
    fake.image = False
    summary, _ = execute(checkout, fake)
    assert summary["problems"] == ["image-missing"] and fake.runs() == []


# --- CLI y concurrencia ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["run", "--expected-git-sha", "a" * 40],
        ["preflight"],
        ["preflight", "--expected-git-sha", "not-a-sha"],
        ["preflight", "--expected-git-sha", "a" * 40, "--run-id", "s11-x"],
        ["preflight", "--expected-git-sha", "a" * 40, "--execute"],
        ["preflight", "--expected-git-sha", "a" * 40, "--load"],
        ["preflight", "--expected-git-sha", "a" * 40, "--count", "50"],
        ["preflight", "--expected-git-sha", "a" * 40, "--provider-key", "sk-x"],
        ["preflight", "--expected-git-sha", "a" * 40, "--timeout", "5"],
        ["preflight", "--expected-git-sha", "a" * 40, "--expected", "x"],
        ["preflight", "--help"],
    ],
)
def test_unknown_or_forbidden_options_exit_3_without_echo(argv, capsys):
    assert launcher.main(argv, environ={}) == 3
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "S11_EX10_LAUNCHER_USAGE: invalid-arguments\n")


def test_the_script_from_a_terminal_exits_3_with_a_sanitized_stderr():
    completed = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "preflight", "--provider-key", "sk-canary-launcher"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert (completed.returncode, completed.stdout) == (3, "")
    assert completed.stderr == "S11_EX10_LAUNCHER_USAGE: invalid-arguments\n"


def test_two_launchers_on_one_checkout_never_mix(checkout, preflight_json):
    raw = preflight_json(checkout.head)[0]
    entered, release = threading.Event(), threading.Event()
    fake = FakeDocker(stdout=raw)

    def hold(argv):
        entered.set()
        assert release.wait(10)

    fake.on_run = hold
    first = invocation(checkout, fake)
    results = {}
    thread = threading.Thread(target=lambda: results.setdefault("first", launcher.execute(first)))
    thread.start()
    try:
        assert entered.wait(10)
        second = invocation(checkout, FakeDocker(stdout=raw))
        with pytest.raises(launcher.LauncherBusy):
            launcher.execute(second)
        assert not second.output_dir.exists()
    finally:
        release.set()
        thread.join(10)
    assert results["first"]["launcher_exit"] == 0
    assert len(fake.runs()) == 1


FAKE_DOCKER = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys, time
    base = os.path.join(os.environ["HOME"], "fake-docker")
    argv = sys.argv[1:]
    with open(os.path.join(base, "calls.log"), "a") as log:
        log.write(json.dumps(argv) + "\\n")
    state = json.load(open(os.path.join(base, "state.json")))
    def out(text="", code=0):
        sys.stdout.write(text)
        sys.stdout.flush()
        os._exit(code)
    if argv[:1] == ["version"]:
        out("29.8.1\\n")
    if argv[:2] == ["compose", "version"]:
        out("5.5.1\\n")
    if argv[:2] == ["image", "ls"]:
        out("abc123\\n")
    if argv[:1] == ["ps"]:
        rows = state["containers"]
        if "health=healthy" in argv:
            out("".join(c["name"] + "\\n" for c in rows if c["healthy"]))
        out("".join("\\t".join([c["name"], c["state"], c["service"], str(c["oneoff"])]) + "\\n"
                    for c in rows))
    if argv[:2] in (["network", "ls"], ["volume", "ls"]):
        out("")
    if argv[:1] == ["compose"] and "run" in argv:
        time.sleep(state["sleep"])
        out(open(os.path.join(base, "stdout.json")).read(), state["exit"])
    if argv[:2] == ["rm", "-f"]:
        out("")
    out("", 99)
    """
)


def _bootstrap(checkout):
    """Carga el script como un proceso aparte con los commits de G0 del checkout sintético."""
    return textwrap.dedent(
        f"""\
        import importlib.util, sys
        from pathlib import Path
        spec = importlib.util.spec_from_file_location("launcher", {str(SCRIPT)!r})
        module = importlib.util.module_from_spec(spec)
        sys.modules["launcher"] = module
        spec.loader.exec_module(module)
        module.G0_DECISION_COMMIT = {checkout.first!r}
        module.G0_A1_DECISION_COMMIT = {checkout.second!r}
        module.repository_root = lambda: Path({str(checkout.root)!r})
        sys.exit(module.main(sys.argv[1:]))
        """
    )


def test_two_launcher_processes_are_serialized_by_the_checkout_lock(checkout, preflight_json):
    raw = preflight_json(checkout.head)[0]
    base = checkout.home / "fake-docker"
    (base / "bin").mkdir(parents=True)
    docker = base / "bin" / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o700)
    (base / "stdout.json").write_bytes(raw)
    state = {"containers": stack_containers(), "sleep": 4, "exit": 0}
    (base / "state.json").write_text(json.dumps(state))
    bootstrap = _bootstrap(checkout)
    env = {
        **environ(checkout.home),
        "PATH": f"{base / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}",
    }
    outputs = [checkout.home / "proc-a", checkout.home / "proc-b"]
    log = base / "calls.log"

    def start(output):
        argv = [sys.executable, "-B", "-c", bootstrap, "preflight"]
        argv += ["--expected-git-sha", checkout.head, "--output-dir", str(output)]
        return subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    first = start(outputs[0])
    # Cuando el `run` simulado empieza a dormir, el primero ya tiene el lock.
    deadline = time.monotonic() + 30
    while not (log.exists() and '"run"' in log.read_text()):
        assert first.poll() is None and time.monotonic() < deadline
        time.sleep(0.05)
    processes = [first, start(outputs[1])]
    results = [(p.wait(60), p.stdout.read().decode(), p.stderr.read().decode()) for p in processes]
    assert results[0][0] == 0 and json.loads(results[0][1])["classification"] == "COHERENT"
    assert results[1] == (3, "", "S11_EX10_LAUNCHER_CONFIG: launcher-busy\n")
    assert (outputs[0] / "preflight.json").read_bytes() == raw
    assert not outputs[1].exists()
    runs = [line for line in (base / "calls.log").read_text().splitlines() if '"run"' in line]
    assert len(runs) == 1


def test_sigterm_stops_the_run_cleans_up_and_records_an_abort(checkout, preflight_json):
    raw = preflight_json(checkout.head)[0]
    base = checkout.home / "fake-docker"
    (base / "bin").mkdir(parents=True)
    docker = base / "bin" / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o700)
    (base / "stdout.json").write_bytes(raw)
    state = {"containers": stack_containers(), "sleep": 30, "exit": 0}
    (base / "state.json").write_text(json.dumps(state))
    output = checkout.home / "sigterm"
    env = {**environ(checkout.home), "PATH": f"{base / 'bin'}:{os.environ.get('PATH', '')}"}
    argv = [sys.executable, "-B", "-c", _bootstrap(checkout), "preflight"]
    argv += ["--expected-git-sha", checkout.head, "--output-dir", str(output)]
    process = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    log = base / "calls.log"
    deadline = time.monotonic() + 30
    while not (log.exists() and '"run"' in log.read_text()):
        assert process.poll() is None and time.monotonic() < deadline
        time.sleep(0.05)
    process.terminate()
    code = process.wait(60)
    summary = json.loads(process.stdout.read())
    assert (code, summary["classification"], summary["problems"]) == (
        5,
        "APPARATUS_ABORT",
        ["interrupted"],
    )
    data = meta(output)
    assert data["cleanup"]["container_absent"] is True
    assert (output / "exit-code.txt").read_text().startswith("command_exit=none\n")


def test_the_frozen_references_match_the_preflight():
    assert launcher.G0_DECISION_COMMIT == pf.G0_DECISION_COMMIT
    assert launcher.G0_A1_DECISION_COMMIT == pf.G0_A1_DECISION_COMMIT
    assert launcher.MODES["preflight"].schema == pf.SCHEMA_VERSION
    assert launcher.MODES["prepare"].schema == pr.SCHEMA_VERSION
    assert set(launcher.REQUIRED_FILES) >= {
        *pr.PREPARE_SOURCES,
        "backend/apps/experiments_app/ex10_preflight.py",
        "backend/apps/experiments_app/ex10_worker_probe.py",
    }
    raw, _ = prepare_json("a" * 40)
    assert set(json.loads(raw)) == launcher.PREPARE_KEYS
    assert launcher.canonical_bytes(json.loads(raw)) == raw
    assert ex10_load.PROTOCOL_ID == "s11-ex10-v1"
