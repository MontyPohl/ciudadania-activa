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
import time
from dataclasses import replace
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


def test_initialize_session_binds_head_branch_project_and_writes_privately(v2_native_host):
    # Current approved native initialization; no scientific START or v1 creation.
    host = v2_native_host
    result = host.module.execute(host.invocation())
    assert result["classification"] == "COHERENT" and result["launcher_exit"] == 0
    raw = (host.root / host.module.SESSION_FILE).read_bytes()
    data = json.loads(raw)
    codec = host.apparatus["ex10_session_v2"]
    assert host.module.base.canonical_bytes(data) == raw
    assert data["schema_version"] == codec.SESSION_V2_SCHEMA
    assert len(codec.HEADER_KEYS) == 27
    assert data["expected_git_sha"] == host.profile["source_revision"]
    assert data["repo_root"] == host.profile["source_host_root"]
    assert data["branch"] == "main" and data["project"] == host.profile["project"]
    assert data["evidence_mode"] == "OFFICIAL" and data["kind"] == codec.SESSION_KIND
    assert data["launcher_blobs"] == host.module.verify_launcher_integrity(
        Path(data["repo_root"]), data["expected_git_sha"]
    )
    assert data["runtime_profile_sha256"] == host.apparatus[
        "ex10_runtime_profile"
    ].runtime_profile_sha256(host.profile)
    assert data["official_run_id"] is None and data["in_flight"] == []
    assert len(data["history"]) == 1
    assert data["history"][0]["payload"]["event"] == "initialize-session"
    assert host.checkpoint["value"] == (data["history"][0]["chain"], 1)
    assert host.checkpoint["publishes"] == 1
    assert data["state"]["next_allowed_actions"] == ["cleanup-stack", "inspect", "start-official"]
    assert stat.S_IMODE(host.root.stat().st_mode) == 0o700
    for path in host.root.rglob("*"):
        assert not path.is_symlink()
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    assert not any(path.name.endswith(".tmp") for path in host.root.rglob("*"))
    before = _v2_private_tree_bytes(host.home)
    assert host.module.execute(host.invocation("inspect"))["launcher_exit"] == 0
    assert _v2_private_tree_bytes(host.home) == before


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


def test_initialize_session_refuses_an_existing_or_unsafe_location(monkeypatch):
    # Register each exact negative root with a real bootstrap, so mismatched
    # context or /tmp cannot mask the repository/private-permission guards.
    import contextlib
    import tempfile

    with (
        tempfile.TemporaryDirectory(
            prefix="w12-location-source-", dir=os.environ["S11_B2_TEST_BASE"]
        ) as temporary,
        tempfile.TemporaryDirectory(prefix="w12-location-tmp-", dir="/tmp") as tmp_temporary,
    ):
        source_parent = Path(temporary)
        assert Path(tmp_temporary).is_relative_to(Path("/tmp"))
        source_parent.chmod(0o700)
        source_case = contextlib.contextmanager(v2_source_checkout.__wrapped__)
        host_case = contextlib.contextmanager(v2_native_host.__wrapped__)
        with source_case(source_parent) as source:
            with host_case(source, SimpleNamespace(param=None), monkeypatch) as host:
                assert not str(source[0]).startswith("/tmp/")
                original_bootstrap = host.invocation().native_bootstrap
                approval_reference = json.loads(original_bootstrap.approval_reference_bytes)

                def for_root(root):
                    approved = {"calls": 0}
                    context = original_bootstrap.context()
                    context["parent"] = str(root)

                    def approve(**facts):
                        # Independent synthetic approval for this exact root;
                        # never replace facts before passing them to a provider.
                        assert facts["session_root"] == str(root)
                        assert facts["profile"].to_dict() == host.profile
                        assert facts["source_revision"] == source[1]
                        assert facts["guard_identity"] == original_bootstrap.global_identity
                        assert (
                            facts["authority_revision"]["authority_id"]
                            == (host.authority.descriptor["authority_id"])
                        )
                        assert (
                            dict(facts["references"])["runtime_profile_approval_reference"]
                            == Path(approval_reference["path"]).read_bytes()
                        )
                        approved["calls"] += 1

                    bootstrap = host.module.NativeLauncherBootstrap(
                        repo_root=original_bootstrap.repo_root,
                        expected_git_sha=original_bootstrap.expected_git_sha,
                        profile_path=original_bootstrap.profile_path,
                        path_context=json.loads(original_bootstrap.path_context_bytes),
                        approval_reference=approval_reference,
                        session_context=context,
                        global_store=original_bootstrap.global_store,
                        global_name=original_bootstrap.global_name,
                        global_identity=original_bootstrap.global_identity,
                        approve=approve,
                        read_authority=original_bootstrap.read_authority,
                        read_checkpoint=original_bootstrap.read_checkpoint,
                        publish_checkpoint=original_bootstrap.publish_checkpoint,
                    )
                    return host.invocation(session_root=root, native_bootstrap=bootstrap), approved

                for root in (
                    source[0] / "unsafe-session",
                    source[0] / "var" / "s11-runs" / "unsafe-session",
                    Path(tmp_temporary) / "unsafe-session",
                ):
                    invocation, approved = for_root(root)
                    assert not os.path.lexists(root)
                    home_before = _v2_private_tree_bytes(host.home)
                    source_before = _v2_private_tree_bytes(source_parent)
                    checkpoint = dict(host.checkpoint)
                    with pytest.raises(
                        host.module.LauncherError, match="^native-session-location-unsafe$"
                    ):
                        host.module.execute(invocation)
                    assert not os.path.lexists(root) and approved["calls"] == 0
                    assert _v2_private_tree_bytes(host.home) == home_before
                    assert _v2_private_tree_bytes(source_parent) == source_before
                    assert host.checkpoint == checkpoint

                loose = host.home / "loose"
                loose.mkdir(mode=0o700)
                loose.chmod(0o777)
                root = loose / "unsafe-session"
                invocation, approved = for_root(root)
                before = _v2_private_tree_bytes(host.home)
                checkpoint = dict(host.checkpoint)
                try:
                    with pytest.raises(
                        host.module.LauncherError, match="^native-session-parent-unsafe$"
                    ):
                        host.module.execute(invocation)
                    assert not os.path.lexists(root) and approved["calls"] > 0
                    assert _v2_private_tree_bytes(host.home) == before
                    assert host.checkpoint == checkpoint
                finally:
                    loose.chmod(0o700)

                # Positive control reaches actual initialization; existing-root
                # denial must preserve its record and independent checkpoint.
                assert host.module.execute(host.invocation())["launcher_exit"] == 0
                before = _v2_private_tree_bytes(host.home)
                checkpoint = dict(host.checkpoint)
                approval_calls = host.approvals["calls"]
                with pytest.raises(
                    host.module.LauncherError, match="^native-session-already-exists$"
                ):
                    host.module.execute(host.invocation())
                assert _v2_private_tree_bytes(host.home) == before
                assert host.checkpoint == checkpoint
                assert host.approvals["calls"] == approval_calls

                host.root.chmod(0o755)
                before = _v2_private_tree_bytes(host.home)
                try:
                    with pytest.raises(host.module.LauncherError, match="^session-root-unsafe$"):
                        host.module.execute(host.invocation("inspect"))
                    assert _v2_private_tree_bytes(host.home) == before
                    assert host.checkpoint == checkpoint
                    assert host.approvals["calls"] == approval_calls
                finally:
                    host.root.chmod(0o700)
                assert host.module.execute(host.invocation("inspect"))["launcher_exit"] == 0


def test_session_updates_are_atomic_canonical_and_hash_every_output(v2_native_host):
    # Actual B2 intent/output/commit IO with exact synthetic completion approval.
    # This checks record persistence, not a scientific START, Docker or RUN_OPENED.
    from uuid import uuid4

    host = v2_native_host
    mod, codec = host.module, host.apparatus["ex10_session_v2"]
    assert mod.execute(host.invocation())["launcher_exit"] == 0
    invocation = host.invocation(
        "start-official", confirmed=True, nonce="c" * 64, preflight_report=host.profile_path
    )
    bootstrap = invocation.native_bootstrap
    initial = bootstrap.load(invocation)
    exchange = str(uuid4())
    output_dir = "actions/" + exchange
    approved = []

    def approve_intent(**facts):
        assert facts["action"] == "start-official"
        assert facts["session"]._bytes == initial._bytes
        assert facts["profile"].to_dict() == host.profile
        assert facts["container_name"] == "s11-owned-atomic-test"
        assert facts["output_dir"] == output_dir
        pending = facts["pending"].to_dict()
        assert pending["history"][:-1] == initial.to_dict()["history"]
        assert pending["in_flight"][0]["output_dir"] == output_dir
        assert pending["in_flight"][0]["container_name"] == "s11-owned-atomic-test"
        approved.append("intent")

    intent = mod.NativeActionIntent(
        invocation=invocation,
        bootstrap=bootstrap,
        container_name="s11-owned-atomic-test",
        output_dir=output_dir,
        approve_action=approve_intent,
    )
    pending = intent.begin()
    result_bytes = mod.base.canonical_bytes(
        {
            "run_id": RUN_ID,
            "scenario_id": "EX-10",
            "evidence_mode": "OFFICIAL",
            "phase": "PREPARED",
            "manifest_sha256": "a" * 64,
            "start_sha256": "b" * 64,
            "preflight_sha256": "c" * 64,
        }
    )
    _, *_, original = bootstrap.qualify(invocation, session=pending)
    with mod._native_record_action_output(
        bootstrap=bootstrap,
        invocation=invocation,
        exchange_id=exchange,
        result_bytes=result_bytes,
        check_current=original,
        check_authority_current=original.check_authority_current,
    ) as (output_path, output_current):
        assert output_path == output_dir + "/worker-result.json"
        payload = dict(pending.to_dict()["history"][-1]["payload"])
        payload.update(
            event="complete-start-official",
            official_run_id=RUN_ID,
            preflight_report_sha256="d" * 64,
            operational_envelope_sha256="e" * 64,
            output_sha256={output_path: hashlib.sha256(result_bytes).hexdigest()},
            in_flight=[],
            state={
                "known_phase": "PREPARED",
                "uncertain": False,
                "blocked": False,
                "next_allowed_actions": [
                    "abandon-official-pre-t0",
                    "execute-official",
                    "inspect",
                    "status",
                ],
            },
        )
        proposal = codec.append_history_entry_v2(pending, payload)

        def approve_completion(**facts):
            facts["writer"].check()
            assert facts["action"] == "start-official"
            assert facts["pending_bytes"] == pending._bytes
            assert facts["proposal_bytes"] == proposal._bytes
            assert facts["result_bytes"] == result_bytes
            expected = proposal if facts["written"] else pending
            assert bootstrap.load(invocation)._bytes == expected._bytes
            output_current()
            approved.append("completion-after" if facts["written"] else "completion-before")

        commit = mod.NativeSessionActionCommit(
            invocation=invocation,
            bootstrap=bootstrap,
            pending=pending,
            proposal=proposal,
            result_bytes=result_bytes,
            qualify_completion=approve_completion,
        )
        completed = commit.commit()
        output_current()
        assert completed._bytes == proposal._bytes and completed.header == initial.header
        assert completed.to_dict()["history"][:-1] == pending.to_dict()["history"]
        assert completed._expected_count == pending._expected_count + 1
        assert host.checkpoint["value"] == (completed._expected_tip, completed._expected_count)
        assert host.checkpoint["publishes"] == 3
        assert approved == ["intent", "completion-before", "completion-after"]

    raw = (host.root / mod.SESSION_FILE).read_bytes()
    assert raw == completed._bytes == mod.base.canonical_bytes(json.loads(raw))
    data = json.loads(raw)
    assert data["official_run_id"] == RUN_ID and data["in_flight"] == []
    assert len(data["history"]) == 3
    tip = data["header_sha256"]
    for entry in data["history"]:
        assert entry["previous_digest"] == tip
        assert entry["chain"] == codec.history_entry_chain(tip, entry)
        tip = entry["chain"]
    assert tip == host.checkpoint["value"][0]
    output = host.root / output_dir
    assert data["history"][-1]["payload"]["output_sha256"] == {
        str(path.relative_to(host.root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output.iterdir()
    }
    assert (output / "worker-result.json").read_bytes() == result_bytes
    for path in (host.root, *host.root.rglob("*")):
        assert not path.is_symlink() and not path.name.endswith(".tmp")
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    before = _v2_private_tree_bytes(host.home)
    checkpoint = dict(host.checkpoint)
    assert mod.execute(host.invocation("inspect"))["launcher_exit"] == 0
    assert _v2_private_tree_bytes(host.home) == before and host.checkpoint == checkpoint
    with pytest.raises(mod.LauncherError, match="^native-action-commit-consumed$"):
        commit.commit()
    assert _v2_private_tree_bytes(host.home) == before and host.checkpoint == checkpoint


@pytest.mark.parametrize(
    "tamper", ["output", "manifest-entry", "manifest-run-id", "new-file", "header", "preflight-sha"]
)
def test_a_tampered_history_blocks_every_action(native_factory_recorded_output_case, tamper):
    # Distinct native tamper cases with a valid installed factory, so a missing
    # factory or malformed schema cannot stand in for the original rejection.
    x = native_factory_recorded_output_case
    c, codec = x.c, x.c.boot.apparatus["ex10_session_v2"]
    assert type(c.factory) is c.mod.NativeActionFactory
    assert c.mod.execute(c.invocation("status"))["launcher_exit"] == 0
    assert c.mod.execute(c.invocation("inspect"))["launcher_exit"] == 0
    target = c.h.root / c.mod.SESSION_FILE
    data = json.loads(target.read_bytes())
    if tamper == "output":
        x.output.write_bytes(b"LOCAL TEST changed registered output\n")
        expected = "native-action-output-changed"
    elif tamper == "new-file":
        extra = x.output.parent / "extra.txt"
        extra.write_bytes(b"LOCAL TEST unregistered output\n")
        extra.chmod(0o600)
        expected = "native-action-output-changed"
    else:
        if tamper == "manifest-entry":
            data["history"][-1]["payload"]["event"] = "complete-reconcile"
            expected = "native-session-history"
        elif tamper == "manifest-run-id":
            assert data["official_run_id"] != OTHER_RUN
            data["official_run_id"] = OTHER_RUN
            expected = "native-session-state-binding"
        elif tamper == "header":
            assert "branch" in codec.HEADER_KEYS
            data["branch"] = "foreign-main"
            expected = "native-session-header-binding"
        else:
            assert tamper == "preflight-sha"
            assert data["preflight_report_sha256"] is not None
            assert data["preflight_report_sha256"] != "f" * 64
            data["preflight_report_sha256"] = "f" * 64
            expected = "native-session-state-binding"
        target.write_bytes(c.mod.base.canonical_bytes(data))
    before = _v2_private_tree_bytes(c.h.home)
    checkpoint = dict(c.h.checkpoint)
    commands, effects, approvals = (
        list(c.state["commands"]),
        list(c.state["effects"]),
        list(c.approvals),
    )
    for action in ("status", "inspect"):
        with pytest.raises(c.mod.SessionTampered, match="^" + expected + "$"):
            c.mod.execute(c.invocation(action))
        assert _v2_private_tree_bytes(c.h.home) == before
        assert c.h.checkpoint == checkpoint
        assert c.state["commands"] == commands and c.state["effects"] == effects
        assert c.approvals == approvals


@pytest.mark.parametrize("lock", ["checkout", "session"])
def test_a_concurrent_launcher_is_busy_and_creates_nothing(v2_action_intent, lock):
    # The historical checkout lock is now the profile's actual global writer.
    # Exercise the current B2 intent writer, rather than a read-only inspect.
    case = v2_action_intent
    path = case.host.guard if lock == "checkout" else case.host.root
    flags = os.O_RDONLY | (os.O_DIRECTORY if lock == "session" else 0)
    fd = os.open(path, flags)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        before = _v2_private_tree_bytes(case.host.home)
        checkpoint = dict(case.host.checkpoint)
        approval_calls = case.host.approvals["calls"]
        with pytest.raises(case.host.module.LauncherBusy, match="^launcher-busy$"):
            case.intent.begin()
        assert _v2_private_tree_bytes(case.host.home) == before
        assert case.host.checkpoint == checkpoint
        assert case.host.approvals["calls"] == approval_calls
        assert case.observed["calls"] == 0
        assert case.intent.attempted and not case.intent._mutation_lock.locked()
        with pytest.raises(
            case.host.module.LauncherError, match="^native-intent-second-attempt-denied$"
        ):
            case.intent.begin()
        assert _v2_private_tree_bytes(case.host.home) == before
        assert case.host.checkpoint == checkpoint
        assert case.host.approvals["calls"] == approval_calls
    finally:
        os.close(fd)


def test_a_session_of_another_repository_head_or_project_is_refused(v2_native_host):
    host = v2_native_host
    host.module.execute(host.invocation())
    target = host.root / host.module.SESSION_FILE
    raw = target.read_bytes()
    original = json.loads(raw)
    codec = host.apparatus["ex10_session_v2"]
    checkpoint = dict(host.checkpoint)
    before = _v2_private_tree_bytes(host.home)
    assert original["expected_git_sha"] != "c" * 40
    with pytest.raises(host.module.LauncherError, match="^source-selector-changed$"):
        host.module.execute(host.invocation("inspect", expected_git_sha="c" * 40))
    assert _v2_private_tree_bytes(host.home) == before and host.checkpoint == checkpoint
    for field, replacement in [
        ("expected_git_sha", "c" * 40),
        ("repo_root", str(Path(original["repo_root"]).with_name("foreign-source"))),
        ("project", "ciudadania-s11-ex10-lifecycle-rehearsal"),
    ]:
        assert host.module.execute(host.invocation("inspect"))["launcher_exit"] == 0
        data = json.loads(raw)
        data[field] = replacement
        if field == "repo_root":
            # A self-consistent attacker header must still fail the approved profile.
            data["source_binding"]["host"] = replacement
            for entry in data["compose_files"][:2]:
                entry["path"] = replacement + "/" + entry["git_path"]
            for entry in data["path_identity_binding"]:
                path = entry["path"]
                if path == original["repo_root"] or path.startswith(original["repo_root"] + "/"):
                    entry["path"] = replacement + path[len(original["repo_root"]) :]
            data["path_identity_binding"].sort(key=lambda entry: entry["path"])
        header = {key: data[key] for key in codec.HEADER_KEYS}
        tip = data["header_sha256"] = codec.session_header_sha256(header)
        for entry in data["history"]:
            entry["previous_digest"] = tip
            entry["chain"] = tip = codec.history_entry_chain(tip, entry)
        forged_raw = host.module.base.canonical_bytes(data)
        forged = codec.OfficialSessionV2(forged_raw, tip, len(data["history"]))
        # Even an attacker-selected internally coherent checkpoint grants no profile trust.
        with pytest.raises(codec.OfficialSessionError, match="^profile-binding$"):
            codec.verify_official_session_v2(forged, profile=host.profile)
        target.write_bytes(forged_raw)
        after_tamper = _v2_private_tree_bytes(host.home)
        try:
            with pytest.raises(host.module.SessionTampered):
                host.module.execute(host.invocation("inspect"))
            assert _v2_private_tree_bytes(host.home) == after_tamper
            assert host.checkpoint == checkpoint
        finally:
            target.write_bytes(raw)
    assert target.read_bytes() == raw and host.checkpoint == checkpoint


# --- Fuente y preflight (15–30) ------------------------------------------------------------------


def test_the_full_checkout_is_mounted_once_read_only_and_never_the_official_roots(
    v2_oneoff_control,
):
    # Current native owned create, with approved external roots. Historical
    # repo/var roots are never mounts; this component fixture grants no START.
    c = v2_oneoff_control
    control, host = c.control, c.host
    # NativeOneOffDocker consumes the canonical decoded profile, whose
    # object keys have canonical order; preserve exact argv expectations.
    profile = json.loads(
        host.apparatus["ex10_runtime_profile"].canonical_runtime_profile_bytes(host.profile)
    )
    source = profile["source_host_root"]
    expected = [
        (source, "/s11-full-source", True),
        *(
            (profile["host_roots"][role]["path"], target, False)
            for role, target in profile["fixed_targets"].items()
        ),
        *(
            (mask["source_path"], "/s11-full-source/" + mask["relative_path"], True)
            for mask in profile["masks"]
        ),
    ]
    assert list(control.mounts) == expected
    assert sum(target == "/s11-full-source" for _, target, _ in control.mounts) == 1
    historical = [control.bootstrap.repo_root / "var" / role for role in host.module.OFFICIAL_ROOTS]
    assert all(
        not Path(mount_source).is_relative_to(root)
        for mount_source, _, _ in control.mounts
        for root in historical
    )
    before = _v2_private_tree_bytes(host.home)
    checkpoint = dict(host.checkpoint)
    assert control.create() == c.container_id
    assert [kind for kind, _ in c.events] == ["create"]
    argv = c.events[0][1]
    mounts = [argv[index + 1] for index, token in enumerate(argv) if token == "--mount"]
    assert mounts == [
        "type=bind,source=" + mount_source + ",target=" + target + (",readonly" if readonly else "")
        for mount_source, target, readonly in expected
    ]
    assert "-v" not in argv and "--privileged" not in argv
    labels = [argv[index + 1] for index, token in enumerate(argv) if token == "--label"]
    assert "com.docker.compose.project=" + profile["project"] in labels
    assert (
        "com.docker.compose.project.config_files="
        + ",".join(item["path"] for item in profile["compose_files"])
        in labels
    )
    assert _v2_private_tree_bytes(host.home) == before and host.checkpoint == checkpoint
    assert c.secret not in repr(argv)


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


def test_safe_directory_is_exact_temporary_and_never_a_wildcard(v2_source_checkout):
    # The retained Git config/command/cleanup component is tested through the
    # actual source-qualified base. No v1 mutation or native START is executed.
    repo, expected, mod = v2_source_checkout
    qualified = mod.base.bind(repo, expected)
    assert qualified is mod.base._qualified_module
    output = repo.parent / "temporary-git-config-component"
    output.mkdir(mode=0o700)
    gitconfig = output / "safe-directory.gitconfig"
    raw = mod.base.SAFE_DIRECTORY_CONFIG.encode()
    assert raw == b"[safe]\n\tdirectory = /s11-full-source\n" and b"*" not in raw
    mod.base.write_file(output, gitconfig.name, raw, 0o644)
    assert gitconfig.read_bytes() == raw and not gitconfig.is_symlink()
    argv = mod.build_command(
        "start-official",
        environ_secrets=[],
        expected_git_sha=expected,
        container_name="s11-component-not-launched",
        repo_root=repo,
        run_id=None,
        nonce=NONCE,
        gitconfig=gitconfig,
    )
    mounts = [argv[index + 1] for index, token in enumerate(argv) if token == "-v"]
    assert mounts == [str(repo) + ":/s11-full-source:ro", str(gitconfig) + ":/etc/gitconfig:ro"]
    assert "*" not in repr(argv)
    fake = FakeDocker()
    target = mod.base.Target(mod.PROJECT, mod.COMPOSE_FILES)
    facts = mod.base._cleanup(target, fake, "s11-component-not-launched", gitconfig)
    assert facts["gitconfig_removed"] and facts["container_absent"]
    assert not facts["container_present_after_run"] and not facts["container_removed"]
    assert not gitconfig.exists() and not list(output.iterdir())
    assert fake.runs() == []
    assert all(
        call[:2] == ["docker", "ps"]
        or call[:3] in (["docker", "network", "ls"], ["docker", "volume", "ls"])
        for call in fake.calls
    )


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


def test_a_rejected_or_world_readable_report_launches_nothing(native_worker_bindings_case):
    """B5 denies scientific admission; the synthetic one-off is already attached."""
    c = native_worker_bindings_case
    path = Path(c.pf_ref["path"])
    original = path.read_bytes()
    original_reference = dict(c.pf_ref)
    qualifier = c.b5.EnvelopeQualifier(**c.options)
    positive = qualifier.build()
    assert positive.to_dict()["result"] == "PASS"
    assert qualifier.qualify(positive._bytes)._bytes == positive._bytes
    before = _v2_private_tree_bytes(c.h.root)
    checkpoint = dict(c.h.checkpoint)
    pending = c.pending.to_dict()
    assert pending["official_run_id"] is None
    assert pending["state"]["uncertain"] and pending["state"]["blocked"]
    assert len(pending["in_flight"]) == 1
    published = _v2_private_tree_bytes(Path(c.publication.store.root))
    attempted = tuple(c.publication.attempted)
    effects = [event for event in c.case.events if event[0] != "inspect"]
    try:
        # Independent component validations, never a factory action retry.
        for change, code in (
            ("other-head", "PREFLIGHT_INVALID"),
            ("world-readable", "FILE_INVALID"),
        ):
            value = json.loads(original)
            if change == "other-head":
                foreign = "b" * 40
                if foreign == c.h.profile["source_revision"]:
                    foreign = "c" * 40
                value["expected_git_sha"] = value["git_sha"] = foreign
            raw = c.mod.base.canonical_bytes(value)
            path.write_bytes(raw)
            path.chmod(0o644 if change == "world-readable" else 0o600)
            # Rehash each real report so digest mismatch cannot mask either cause.
            c.pf_ref["sha256"] = hashlib.sha256(raw).hexdigest()
            invalid = c.b5.EnvelopeQualifier(**c.options)
            denied = invalid.build().to_dict()
            assert denied["result"] == "FAIL"
            assert [check["detail_code"] for check in denied["checks"]] == [code]
            with pytest.raises(c.b5.EnvelopeError, match="^" + code + "$"):
                invalid.qualify(positive._bytes)
            with pytest.raises(c.mod.LauncherError, match="^native-worker-bindings-uncertain$"):
                c.mod._prepare_native_worker_bindings(**c.arguments)
            assert _v2_private_tree_bytes(c.h.root) == before
            assert c.h.checkpoint == checkpoint
            assert _v2_private_tree_bytes(Path(c.publication.store.root)) == published
            assert tuple(c.publication.attempted) == attempted
            assert [event for event in c.case.events if event[0] != "inspect"] == effects
            assert not c.attachment.transferred and c.ready["reads"] == 0
            assert c.control.bootstrap.load(c.control.invocation)._bytes == c.pending._bytes
    finally:
        path.write_bytes(original)
        path.chmod(0o600)
        c.pf_ref.clear()
        c.pf_ref.update(original_reference)
    restored = c.b5.EnvelopeQualifier(**c.options)
    assert restored.qualify(positive._bytes)._bytes == positive._bytes
    assert _v2_private_tree_bytes(c.h.root) == before
    assert c.h.checkpoint == checkpoint


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


def test_start_official_runs_start_in_official_mode_and_binds_the_run_id(
    native_start_admission_migration_case,
):
    _assert_native_start_admission_completed(native_start_admission_migration_case, ())


def test_start_passes_only_exact_acknowledgements(native_start_admission_migration_case):
    c = native_start_admission_migration_case
    before = _v2_private_tree_bytes(c.h.home)
    checkpoint, effects, attempts = (
        dict(c.h.checkpoint),
        list(c.state["effects"]),
        list(c.factory._attempted),
    )
    for bad in (("*",), ("all",), (OTHER_RUN, OTHER_RUN)):
        with pytest.raises(c.mod.base.UsageError):
            c.mod.validate_invocation(replace(c.invocation, acknowledged=bad))
    assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint
    assert c.state["effects"] == effects and c.factory._attempted == attempts
    _assert_native_start_admission_completed(c, (OTHER_RUN,))


#: El run oficial 1 (NOT_DEMONSTRATED, `binding=false`): el próximo START oficial lo reconoce.
HISTORICAL_RUN_1 = "s11-0ff9a5d1-2309-5f40-b4a7-dabad703e43a"


def test_start_forwards_and_records_the_exact_acknowledgement_of_run_1(
    native_start_admission_migration_case,
):
    # The exact historical ID is synthetic authority input, never actual run1 evidence.
    _assert_native_start_admission_completed(
        native_start_admission_migration_case, (HISTORICAL_RUN_1,)
    )


def test_start_without_acknowledgement_records_none(native_start_admission_migration_case):
    _assert_native_start_admission_completed(native_start_admission_migration_case, ())


def _assert_invalid_ack_before_effects(checkout, good, acknowledged, monkeypatch):
    # Input-only validation must not initialize either a historical or a native session.
    official.validate_invocation(good)
    assert not good.session_root.exists()
    before = {
        p.relative_to(checkout.home): p.read_bytes()
        for p in checkout.home.rglob("*")
        if p.is_file()
    }
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("forbidden effect")
        raise AssertionError("invalid ACK reached source binding or Docker")

    bad = replace(good, acknowledged=acknowledged, runner_factory=forbidden)
    monkeypatch.setattr(base, "bind", forbidden)
    with pytest.raises(base.UsageError):
        official.validate_invocation(bad)
    with pytest.raises(base.UsageError):
        official.execute(bad)
    assert calls == [] and not good.session_root.exists()
    after = {
        p.relative_to(checkout.home): p.read_bytes()
        for p in checkout.home.rglob("*")
        if p.is_file()
    }
    assert after == before


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "*",
        "s11-*",
        "../" + HISTORICAL_RUN_1,
        HISTORICAL_RUN_1 + "/..",
        HISTORICAL_RUN_1.upper(),
        HISTORICAL_RUN_1 + " ",
        HISTORICAL_RUN_1[:-1],
    ],
    ids=[
        "empty",
        "wildcard",
        "prefix-wildcard",
        "path-up",
        "path-suffix",
        "upper",
        "space",
        "short",
    ],
)
def test_malformed_acknowledgements_are_rejected_before_anything_runs(checkout, monkeypatch, bad):
    root = checkout.home / "uninitialized-input-only-session"
    good = invocation(
        checkout, root, FakeDocker(), "start-official", acknowledged=(HISTORICAL_RUN_1,)
    )
    _assert_invalid_ack_before_effects(checkout, good, (bad,), monkeypatch)


@pytest.mark.parametrize(
    "action",
    [a for a in official.ACTIONS if a != "start-official"],
)
def test_the_acknowledgement_belongs_only_to_start_official(checkout, monkeypatch, action):
    # La confirmación propia de cada acción está dada: el rechazo es por el reconocimiento.
    root = checkout.home / "uninitialized-input-only-session"
    good = invocation(checkout, root, FakeDocker(), action)
    _assert_invalid_ack_before_effects(checkout, good, (HISTORICAL_RUN_1,), monkeypatch)


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
    v2_source_checkout, request, monkeypatch, code, reasons
):
    """Preserve the four original refusal IDs/codes through native admission.

    Binding uses the actual generated terminal run ID rather than the original
    fake SDK fixture ID. LOCAL/ND retain OTHER_RUN exactly. External approvals,
    Docker observations and the scientific fixture remain synthetic; no real
    operational run or scientific qualification is inferred.
    """
    from contextlib import ExitStack, contextmanager
    from dataclasses import replace

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    assert len(reasons) == 1
    prefix, target = reasons[0].split(":", 1)
    assert target == OTHER_RUN
    variants = {
        "binding-run-exists": (2, "typed-b4-historic-run", None),
        "not-demonstrated-unacknowledged": (1, "typed-b4-legacy-ack-other", "nd-without-ack"),
        "active-run": (1, "typed-b4-legacy-ack-other", "local-active"),
        "prior-run-unverifiable": (1, "typed-b4-legacy-ack-other", "local-corrupt"),
    }
    expected_code, variant, case = variants[prefix]
    assert code == expected_code
    with ExitStack() as owned:
        proxy = SimpleNamespace(param=variant, addfinalizer=owned.callback, node=request.node)
        h = owned.enter_context(
            contextmanager(v2_native_host.__wrapped__)(v2_source_checkout, proxy, monkeypatch)
        )
        mod = h.module
        if prefix == "binding-run-exists":
            resource = owned.enter_context(
                contextmanager(native_factory_resource_case.__wrapped__)(h, monkeypatch, proxy)
            )
            c = owned.enter_context(
                contextmanager(native_factory_execution_chain_case.__wrapped__)(
                    resource, proxy, monkeypatch
                )
            )
            h, mod, original = c.h, c.mod, c.boot
            second_root = h.home / "independent-eligible-session"
            checkpoint = {"value": None, "publishes": 0}
            providers_called = []
            expected_approval = Path(
                json.loads(original.approval_reference_bytes)["path"]
            ).read_bytes()

            def approve_second(**facts):
                assert h.approvals["allowed"]
                assert facts["profile"].to_dict() == h.profile
                assert facts["session_root"] == str(second_root)
                assert facts["source_revision"] == original.expected_git_sha
                assert facts["guard_identity"] == original.global_identity
                assert (
                    facts["authority_revision"]["authority_id"]
                    == h.authority.descriptor["authority_id"]
                )
                assert (
                    dict(facts["references"])["runtime_profile_approval_reference"]
                    == expected_approval
                )

            def read_checkpoint():
                return checkpoint["value"]

            def publish_checkpoint(expected, replacement):
                assert checkpoint["value"] == expected
                checkpoint["value"] = replacement
                checkpoint["publishes"] += 1

            def forbidden(**facts):
                providers_called.append(facts)
                raise AssertionError("binding refusal must precede every second-session provider")

            def forbidden_process(*args, **kwargs):
                raise AssertionError(
                    "binding refusal must not construct Docker or a worker process"
                )

            factory = mod.NativeActionFactory(
                providers={name: forbidden for name in mod.NativeActionFactory._PROVIDERS},
                signer=c.factory.signer,
                custody_hmac_key=c.factory.custody_key,
                runner_factory=forbidden_process,
                process_factory=forbidden_process,
            )
            context = json.loads(original.session_context_bytes)
            context["parent"] = str(second_root)
            context["prior_session_roots"] = [*context["prior_session_roots"], str(h.root)]
            paths = json.loads(original.path_context_bytes)
            paths["prior_session_roots"] = [*paths["prior_session_roots"], str(h.root)]
            configuration = {
                name: getattr(original, name)
                for name in (
                    "repo_root",
                    "expected_git_sha",
                    "profile_path",
                    "global_store",
                    "global_name",
                    "global_identity",
                    "read_authority",
                )
            }
            second = mod.NativeLauncherBootstrap(
                **configuration,
                path_context=paths,
                approval_reference=json.loads(original.approval_reference_bytes),
                session_context=context,
                approve=approve_second,
                read_checkpoint=read_checkpoint,
                publish_checkpoint=publish_checkpoint,
                action_factory=factory,
            )
            initial_invocation = replace(
                c.invocation,
                action="initialize-session",
                nonce=None,
                preflight_report=None,
                acknowledged=(),
                confirmed=False,
                session_root=second_root,
                native_bootstrap=second,
            )
            initialized = mod.execute(initial_invocation)
            assert initialized["launcher_exit"] == 0
            initial = second.load(initial_invocation)
            assert initial.to_dict()["official_run_id"] is None
            assert "start-official" in initial.to_dict()["state"]["next_allowed_actions"]
            assert checkpoint["publishes"] == 1 and not providers_called

            first = mod.execute(c.invocation)
            assert first["launcher_exit"] == 0 and not c.errors
            for action in ("execute-official", "reconcile", "finalize"):
                invocation = c.advance(action)
                outcome = mod.execute(invocation)
                assert outcome["launcher_exit"] == 0 and not c.errors
                assert c.action_results[-1]["steps"] == 5
                assert c.state["completion"] == [False, True]
            terminal = c.action_results[-1]["result"]
            run_id = terminal["run_id"]
            assert terminal["decision"] in {"PASS", "FAIL"} and terminal["binding"] is True
            assert c.state["local_seal_checked"] and c.state["actual_emission_proof_admitted"]
            assert c.science.backend.accepted == 50 and all(
                token.released for token in c.science.tokens
            )
            assert c.state["store"].custody_hmac_key == factory.custody_key
            op = second.apparatus["ex10_operational_context"]
            snapshot = second.read_authority(h.authority.descriptor_ref)
            row = op.history.find_run(snapshot, run_id)
            assert row["lifecycle_state"] == "TERMINAL" and row["binding"] is True
            assert row["result"] == terminal["decision"]
            assert (
                op.history.first_valid_status(snapshot)["status"]
                == terminal["decision"] + "_BINDING"
            )
            assert c.transport.calls.count("RESERVE") == c.transport.calls.count("COMMIT_OPEN") == 1
            assert c.transport.calls.count("B3_CAS") == 2
            assert c.transport.calls.count("RETIRE_TERMINAL") == 1
            assert all(child.poll() == 0 for child in c.children)

            # ACK only the genuine historical ND carried by the unchanged fixture.
            acknowledged = tuple(
                run["run_id"]
                for run in op.history.list_official_runs(snapshot)
                if run["lifecycle_state"] == "TERMINAL" and run["result"] == "NOT_DEMONSTRATED"
            )
            assert run_id not in acknowledged
            attempt = replace(
                c.invocation,
                session_root=second_root,
                native_bootstrap=second,
                acknowledged=acknowledged,
            )
            before = _v2_private_tree_bytes(h.home)
            checkpoint_before = dict(checkpoint)
            transport_before = list(c.transport.calls)
            actual = mod.execute(attempt)
            assert actual["classification"] == "REFUSED" and actual["launcher_exit"] == code
            assert actual["problems"] == [prefix + ":" + run_id]
            assert actual["output_dir"] is None and not providers_called
            assert _v2_private_tree_bytes(h.home) == before
            assert checkpoint == checkpoint_before and c.transport.calls == transport_before
            last = second.load(attempt)
            assert last._bytes == initial._bytes and last.to_dict()["official_run_id"] is None
            assert not last.to_dict()["in_flight"]
            assert "start-official" in last.to_dict()["state"]["next_allowed_actions"]
            return
        report = h.home / "unconsumed-preflight.json"
        report.write_bytes(b"{}\n")
        report.chmod(0o600)
        mod.execute(h.invocation())
        original = h.invocation("inspect").native_bootstrap
        calls = []

        def forbidden(**facts):
            calls.append(facts)
            raise AssertionError("known refusal must precede every factory provider")

        def forbidden_runner(*args, **kwargs):
            raise AssertionError("known refusal must not construct a Docker runner")

        key = b"synthetic-refusal-private-custody-" + b"x" * 32
        factory = mod.NativeActionFactory(
            providers={name: forbidden for name in mod.NativeActionFactory._PROVIDERS},
            signer=Ed25519PrivateKey.generate(),
            custody_hmac_key=key,
            runner_factory=forbidden_runner,
            process_factory=forbidden_runner,
        )
        configuration = {
            name: getattr(original, name)
            for name in (
                "repo_root",
                "expected_git_sha",
                "profile_path",
                "global_store",
                "global_name",
                "global_identity",
                "approve",
                "read_authority",
                "read_checkpoint",
                "publish_checkpoint",
            )
        }
        boot = mod.NativeLauncherBootstrap(
            **configuration,
            path_context=json.loads(original.path_context_bytes),
            approval_reference=json.loads(original.approval_reference_bytes),
            session_context=json.loads(original.session_context_bytes),
            action_factory=factory,
        )
        initial = boot.load(h.invocation("inspect", native_bootstrap=boot))
        historical = boot.apparatus["ex10_operational_context"].history.list_official_runs(
            boot.read_authority(h.authority.descriptor_ref)
        )
        assert len(historical) == 1
        assert historical[0]["run_id"] == OTHER_RUN
        assert historical[0]["lifecycle_state"] == "TERMINAL"
        assert historical[0]["result"] == "NOT_DEMONSTRATED"
        assert historical[0]["binding"] is False
        prior_id = historical[0]["run_id"]
        if case == "nd-without-ack":
            expected = "not-demonstrated-unacknowledged:" + prior_id
        else:
            prior_id = OTHER_RUN
            namespace = h.apparatus["ex10_prior_custody"].__package__
            ev, constants = (
                sys.modules[namespace + "." + name] for name in ("evidence", "constants")
            )
            for name, loaded in (("evidence", ev), ("constants", constants)):
                path = boot.repo_root / "backend/apps/experiments_app" / (name + ".py")
                assert loaded.__file__ == str(path)
                assert (
                    loaded._s11_verified_source_sha256
                    == hashlib.sha256(path.read_bytes()).hexdigest()
                )
            root = Path(h.profile["host_roots"]["evidence"]["path"])
            store = ev.EvidenceStore(
                root,
                custody_hmac_key=key,
                publication_verification_key=Ed25519PrivateKey.generate()
                .public_key()
                .public_bytes_raw(),
            )
            store.persist_json(
                run_id=prior_id,
                scenario_id="EX-10",
                artifact_type="run-manifest-initial",
                kind=constants.ArtifactKind.METADATA,
                classification=constants.Classification.INTERNAL,
                filename="initial.json",
                payload={
                    "scenario_id": "EX-10",
                    "experiment_id": "S11C3-EX10-LOCAL-TEST-NON-EXPERIMENTAL",
                },
                source="synthetic actual-custody native refusal fixture",
            )
            if case == "local-corrupt":
                signature = root / "runs" / prior_id / "metadata/artifacts.jsonl.hmac"
                signature.write_bytes(b"0" * 64 + b"\n")
                expected = "prior-run-unverifiable:" + prior_id
            else:
                expected = "active-run:" + prior_id
        assert expected == reasons[0]
        invocation = h.invocation(
            "start-official",
            confirmed=True,
            nonce="b" * 64,
            preflight_report=report,
            native_bootstrap=boot,
        )
        before = _v2_private_tree_bytes(h.home)
        checkpoint = dict(h.checkpoint)
        result = mod.execute(invocation)
        assert result["classification"] == "REFUSED" and result["launcher_exit"] == code
        assert result["problems"] == [expected] and result["output_dir"] is None
        assert not calls and _v2_private_tree_bytes(h.home) == before
        assert h.checkpoint == checkpoint
        session = boot.load(h.invocation("inspect", native_bootstrap=boot))
        assert session.to_dict()["official_run_id"] is None
        assert not session.to_dict()["in_flight"]
        assert "start-official" in session.to_dict()["state"]["next_allowed_actions"]
        assert session.to_dict()["history"][-1]["payload"]["event"] == "initialize-session"
        assert session._bytes == initial._bytes


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
def test_an_incoherent_start_output_binds_nothing_and_blocks(
    v2_worker_result_observation, stdout, problem
):
    """Actual private result rejection preserves the original uncertain intent."""
    x = v2_worker_result_observation
    host, mod = x.case.host, x.case.host.module
    x.case.observations["phase"] = "exited"
    with x.observer.capture() as (raw, value, recheck):
        assert raw == x.raw and value == x.payload and recheck() is None
    mutations = {
        key: value for key, value in stdout.items() if key not in START_OK or value != START_OK[key]
    }
    assert len(mutations) == 1
    payload = {**x.payload, **mutations}
    problems, _, _ = mod.action_problems(
        "start-official", payload, run_id=x.observer.run_id, reconciled=None
    )
    assert problems == [problem]
    x.path.write_bytes(mod.base.canonical_bytes(payload))
    assert stat.S_IMODE(x.path.stat().st_mode) == 0o600
    before = _v2_private_tree_bytes(host.home)
    checkpoint = dict(host.checkpoint)
    control = x.case.control
    pending = control.bootstrap.load(control.invocation)
    pending_value = pending.to_dict()
    assert pending_value["official_run_id"] is None and len(pending_value["in_flight"]) == 1
    assert pending_value["state"] == {
        "known_phase": None,
        "uncertain": True,
        "blocked": True,
        "next_allowed_actions": ["inspect"],
    }
    effects = [event for event in x.case.events if event[0] != "inspect"]
    with pytest.raises(mod.LauncherError, match="^native-worker-result-invalid$"):
        with x.observer.capture():
            pytest.fail("Incoherent private START result was exposed")
    assert x.observer.failed and not x.observer.lock.locked()
    assert _v2_private_tree_bytes(host.home) == before and host.checkpoint == checkpoint
    assert [event for event in x.case.events if event[0] != "inspect"] == effects
    assert control.bootstrap.load(control.invocation)._bytes == pending._bytes


def test_a_second_start_is_not_allowed(native_factory_resource_case):
    """A closed PREPARED B2 fixture cannot acquire another native START intent."""
    from uuid import uuid4

    c = native_factory_resource_case
    # Explicit closed B2 setup; no assertion that a scientific START ran here.
    c.seed_status()
    assert c.mod.execute(c.invocation("status"))["launcher_exit"] == 0
    original = c.boot.load(c.invocation("inspect"))
    value = original.to_dict()
    assert value["official_run_id"] == RUN_ID and value["state"]["known_phase"] == "PREPARED"
    assert not value["in_flight"] and not value["state"]["uncertain"]
    assert "start-official" not in value["state"]["next_allowed_actions"]
    before = _v2_private_tree_bytes(c.h.root)
    checkpoint = dict(c.h.checkpoint)
    commands = list(c.state["commands"])
    effects = list(c.state["effects"])
    called = []

    def forbidden(**facts):
        called.append(facts)
        raise AssertionError("Second START must be denied before action approval")

    exchange = str(uuid4())
    intent = c.mod.NativeActionIntent(
        invocation=c.invocation(
            "start-official", confirmed=True, nonce="d" * 64, preflight_report=c.h.profile_path
        ),
        bootstrap=c.boot,
        container_name="s11-second-start-" + exchange,
        output_dir="actions/" + exchange,
        approve_action=forbidden,
    )
    with pytest.raises(c.mod.LauncherError, match="^native-intent-state-blocked$"):
        intent.begin()
    assert intent.attempted and not intent._mutation_lock.locked() and called == []
    assert _v2_private_tree_bytes(c.h.root) == before and c.h.checkpoint == checkpoint
    assert c.state["commands"] == commands and c.state["effects"] == effects
    assert c.boot.load(c.invocation("inspect"))._bytes == original._bytes


def test_start_must_create_a_new_run_and_never_touch_prior_runs(
    v2_source_checkout, request, monkeypatch
):
    """Preserve both original causes through two independent real native STARTs.

    The prior LOCAL ID is created and finalized by the actual executor. Native
    rejection raises NativeActionFailure (CLI exit4), retains B2 uncertainty and
    scientific OPEN, and performs no completion or RETIRE. No uncertain session
    is reused, no verdict/history is rewritten, and external approvals are synthetic.
    """
    from contextlib import ExitStack, contextmanager

    homes = []
    for fault in ("returned-existing-run", "prior-touched-after-new-run"):
        # Close every owned actor before restoring this attempt's monkeypatches.
        with monkeypatch.context() as patches:
            with ExitStack() as owned:
                proxy = SimpleNamespace(
                    param="typed-b4-empty-history",
                    addfinalizer=owned.callback,
                    node=SimpleNamespace(name=request.node.name + "-" + fault),
                )
                h = owned.enter_context(
                    contextmanager(v2_native_host.__wrapped__)(v2_source_checkout, proxy, patches)
                )
                assert h.home not in homes
                homes.append(h.home)
                resource = owned.enter_context(
                    contextmanager(native_factory_resource_case.__wrapped__)(h, patches, proxy)
                )
                c = owned.enter_context(
                    contextmanager(native_factory_full_case.__wrapped__)(resource, proxy, patches)
                )
                _assert_native_post_worker_prior_faults(c, patches, fault)
    assert len(homes) == 2


def _fake_with(on_run):
    fake = FakeDocker()
    fake.on_run = on_run
    return fake


def test_start_refuses_synthetic_or_absent_official_keys(v2_source_checkout, monkeypatch):
    """Retained key/environment component rejects all three original identities.

    Native keys come from external installation, not these environment fields.
    This component proof executes neither a historical mutation nor Native START.
    """
    repo, expected, mod = v2_source_checkout
    mod.base.bind(repo, expected)
    values = {
        "S11_CUSTODY_HMAC_KEY": secrets.token_hex(32),
        "S11_PUBLICATION_APPROVAL_PUBLIC_KEY": secrets.token_hex(32),
        "S11_DOCUMENT_HMAC_SECRET": secrets.token_hex(32),
    }
    # Ephemeral test-only trust fingerprints, never actual official authority.
    monkeypatch.setattr(
        mod,
        "OFFICIAL_CUSTODY_EPOCH",
        hashlib.sha256(values["S11_CUSTODY_HMAC_KEY"].encode()).hexdigest()[:16],
    )
    monkeypatch.setattr(
        mod,
        "OFFICIAL_PUBLICATION_KEY_ID",
        hashlib.sha256(bytes.fromhex(values["S11_PUBLICATION_APPROVAL_PUBLIC_KEY"])).hexdigest()[
            :16
        ],
    )
    good = {"PATH": "/usr/bin:/bin", **values}
    assert mod.official_key_problems(good) == []
    admitted = mod.child_environment(good)
    assert all(admitted[key] == value for key, value in values.items())
    before = _v2_private_tree_bytes(repo.parent)
    cases = [
        ({"S11_CUSTODY_HMAC_KEY": secrets.token_hex(32)}, "official-custody-key"),
        (
            {"S11_PUBLICATION_APPROVAL_PUBLIC_KEY": secrets.token_hex(32)},
            "official-publication-key",
        ),
        ({"S11_DOCUMENT_HMAC_SECRET": None}, "document-hmac-secret"),
    ]
    for changes, cause in cases:
        bad = {key: value for key, value in {**good, **changes}.items() if value is not None}
        assert mod.official_key_problems(bad) == [cause]
        with pytest.raises(mod.LauncherError) as failure:
            mod.child_environment(bad)
        assert failure.value.code == "official-keys:" + cause
        assert all(value not in failure.value.code for value in values.values())
        assert _v2_private_tree_bytes(repo.parent) == before
    assert mod.official_key_problems(good) == [] and mod.child_environment(good) == admitted


def test_official_roots_must_be_real_directories(native_factory_resource_case):
    """All approved external roles reject symlinks before entering the factory."""
    c = native_factory_resource_case
    inv = c.invocation(
        "start-official", confirmed=True, nonce="d" * 64, preflight_report=c.h.profile_path
    )
    c.mod.validate_invocation(inv)
    assert c.boot.action_factory is c.factory
    assert c.mod.execute(c.invocation("inspect"))["launcher_exit"] == 0
    before = _v2_private_tree_bytes(c.h.root)
    checkpoint = dict(c.h.checkpoint)
    attempts = tuple(c.factory._attempted)
    commands = list(c.state["commands"])
    effects = list(c.state["effects"])
    for role in ("evidence", "backup", "restore"):
        root = Path(c.h.profile["host_roots"][role]["path"])
        identity = root.stat()
        retained = root.with_name(root.name + "-original-retained")
        assert not os.path.lexists(retained)
        root.rename(retained)
        root.symlink_to(retained, target_is_directory=True)
        link = root.lstat()
        retained_tree = _v2_private_tree_bytes(retained)
        try:
            with pytest.raises(c.mod.LauncherError, match="^native-profile-symlink$"):
                c.mod.execute(inv)
            assert root.is_symlink() and os.readlink(root) == str(retained)
            assert (root.lstat().st_dev, root.lstat().st_ino) == (link.st_dev, link.st_ino)
            assert _v2_private_tree_bytes(retained) == retained_tree
            assert _v2_private_tree_bytes(c.h.root) == before
            assert c.h.checkpoint == checkpoint and tuple(c.factory._attempted) == attempts
            assert c.state["commands"] == commands and c.state["effects"] == effects
        finally:
            assert root.is_symlink() and os.readlink(root) == str(retained)
            root.unlink()
            retained.rename(root)
        assert (root.stat().st_dev, root.stat().st_ino) == (identity.st_dev, identity.st_ino)
        assert c.mod.execute(c.invocation("inspect"))["launcher_exit"] == 0
    assert c.boot.load(c.invocation("inspect")).to_dict()["official_run_id"] is None
    assert _v2_private_tree_bytes(c.h.root) == before and c.h.checkpoint == checkpoint


# --- EXECUTE (48–62) -----------------------------------------------------------------------------


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_execute_runs_once_from_prepared_and_never_chains(native_factory_execution_chain_case):
    # v2 uses the installed bridge; assert the actual isolated worker action,
    # complete SINGLE_RPC exchange and unchanged native phase/permission rule.
    c = native_factory_execution_chain_case
    started = c.mod.execute(c.invocation)
    assert started["launcher_exit"] == 0 and not c.errors
    assert len(c.action_results) == 1 and c.action_results[0]["steps"] == 9
    invocation = c.advance("execute-official")
    executed = c.mod.execute(invocation)
    assert executed["launcher_exit"] == 0 and executed["classification"] == "COHERENT"
    assert not c.errors and c.state["completion"] == [False, True]
    assert [item["action"] for item in c.action_results] == ["start", "execute"]
    assert c.action_results[-1]["steps"] == 5
    assert len({item["cid"] for item in c.action_results}) == 2
    assert len({item["exchange"] for item in c.action_results}) == 2
    run_id = c.action_results[0]["result"]["run_id"]
    assert c.state["cfg"]["action"] == "execute"
    assert c.state["cfg"]["command_options"]["run_id"] == run_id
    assert c.state["effects"] == ["create", "start", "create", "start"]
    assert c.transport.calls.count("B3_CAS") == 1
    assert c.transport.calls.count("RETIRE_TERMINAL") == 0
    current = c.boot.load(invocation)
    value = current.to_dict()
    assert current.header == c.c.initial.header and value["official_run_id"] == run_id
    assert value["state"]["known_phase"] == "RECONCILING"
    assert value["state"]["next_allowed_actions"] == ["inspect", "reconcile", "status"]
    assert value["history"][-1]["payload"]["event"] == "complete-execute-official"
    assert value["in_flight"] == []
    assert c.state["actual_emission_proof_admitted"] and c.science.backend.accepted == 50
    assert all(token.released for token in c.science.tokens)
    assert all(child.poll() == 0 for child in c.children)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
@pytest.mark.parametrize("first", ["COHERENT", "REFUSED", "ABORT", "INCOHERENT"])
def test_execute_is_never_invoked_twice(native_factory_execution_chain_case, monkeypatch, first):
    """Real SDK outcomes never permit a second native EXEC or resolve uncertainty."""
    from dataclasses import replace

    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_execution_chain_case
    executor = helper.executor
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    invocation = c.advance("execute-official")
    run_id = c.action_results[0]["result"]["run_id"]
    mutations = []
    if first == "REFUSED":
        # This original oracle is never-repeat, not stale-PF precedence or CLI1.
        # Refuse at the genuine SDK pre-emission READY_STATE probe after SINGLE5.
        c.science.probes = replace(
            c.science.probes,
            ready_state=lambda dataset: {
                "state": "READY_STATE",
                "overall": "FAIL",
            },
        )
        c.state["retire_after_sdk_refusal"] = True
    elif first == "ABORT":
        # Actual SDK result/science completes; only the owned synthetic relay's
        # exit is held at the real host wait boundary. This is not pre-T0 abort.
        c.state["hold_relay_retirement_for_timeout"] = True
    elif first == "INCOHERENT":
        original = executor.ExecutorOperationalContext.persist_public_result

        def persist_then_corrupt(context, store, result):
            value = original(context, store, result)
            assert context is c.state["context"] and context.action == "execute"
            assert context.finished and context.scientific_finished and context.step_authorized
            assert context.consumed is False and context.channel.position == 5
            assert result["run_id"] == run_id and result["phase"] == "RECONCILING"
            path = store.root / ("executor-result." + context.channel.exchange_id + ".json")
            assert path.read_bytes() == executor.canonical_json_bytes(result)
            bad = dict(result, extra=True)
            path.write_bytes(executor.canonical_json_bytes(bad))
            mutations.append(bad)
            return value

        monkeypatch.setattr(
            executor.ExecutorOperationalContext, "persist_public_result", persist_then_corrupt
        )
    if first == "COHERENT":
        summary = c.mod.execute(invocation)
        assert summary["launcher_exit"] == 0 and summary["classification"] == "COHERENT"
    else:
        expected = c.mod.Aborted if first == "ABORT" else c.mod.NativeActionFailure
        with pytest.raises(expected) as failure:
            c.mod.execute(invocation)
        if first == "ABORT":
            assert failure.value.kind == "timeout"
            assert c.state["held_relay_at_actual_worker_wait"] is True
        else:
            assert failure.value.code == (
                "native-factory-uncertain" if first == "REFUSED" else "native-worker-result-invalid"
            )
            if first == "REFUSED":
                # Actual relay exit may be observed during the final pipe EOF
                # or subsequent policy deployment qualification. Both authentic
                # guards deny before emission wait can return without ARMED.
                op = c.boot.apparatus["ex10_operational_context"]
                cause = failure.value.__context__
                assert type(cause) is op.OperationalError
                assert cause.code in {
                    "UNCERTAIN_PUBLICATION",
                    "PROCESS_IDENTITY_CHANGED",
                    "HOST_CONTEXT_INVALID",
                }
                c.state["refused_original_guard_code"] = cause.code
    for thread in c.threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in c.threads)
    context = c.state["context"]
    assert context.finished and context.step_authorized and context.consumed is False
    assert context.channel.position == 5 and context.channel.closed
    assert len(c.children) == 2 and all(child.poll() is not None for child in c.children)
    if first == "REFUSED":
        assert len(c.errors) == 1 and isinstance(c.errors[0], executor.Ex10Refusal)
        assert c.errors[0].reasons == ("ready-state",) and c.errors[0].terminal is False
        assert c.state["sdk_refusal"] is c.errors[0]
        assert c.state["refusal_relay_observed_exit"] == c.children[-1].poll() == 4
        assert len(c.action_results) == 1 and not context.scientific_finished
        actual = executor.load_existing_state_readonly(c.state["store"], run_id)
        assert executor.phase_of(actual) is executor.Phase.PREPARED
        assert not actual.has(executor.ART_EMISSION) and not actual.has(executor.ART_ARMED)
        assert not actual.has(executor.ART_RAW) and c.science.backend.accepted == 0
    else:
        assert not c.errors and len(c.action_results) == 2
        assert c.action_results[-1]["steps"] == 5 and context.scientific_finished
        assert c.results[-1]["phase"] == "RECONCILING" and c.science.backend.accepted == 50
        assert c.state["actual_emission_proof_admitted"] and all(
            token.released for token in c.science.tokens
        )
        if first == "INCOHERENT":
            assert len(mutations) == 1
            problems, _, _ = c.mod.action_problems(
                "execute-official", mutations[0], run_id=run_id, reconciled=None
            )
            assert problems == ["payload-keys"]
        if first != "ABORT":
            assert all(child.poll() == 0 for child in c.children)
    current = c.boot.load(invocation)
    value = current.to_dict()
    assert value["official_run_id"] == run_id
    if first == "COHERENT":
        assert value["state"]["known_phase"] == "RECONCILING" and not value["in_flight"]
        assert not value["state"]["uncertain"] and not value["state"]["blocked"]
        assert value["state"]["next_allowed_actions"] == ["inspect", "reconcile", "status"]
        assert c.state["completion"] == [False, True]
    else:
        assert value["state"]["known_phase"] == "PREPARED"
        assert (
            len(value["in_flight"]) == 1 and value["in_flight"][0]["action"] == "execute-official"
        )
        assert value["state"]["uncertain"] and value["state"]["blocked"]
        assert value["state"]["next_allowed_actions"] == ["inspect"]
        assert c.state["completion"] == []
    assert c.state["effects"] == ["create", "start", "create", "start"]
    assert (
        c.transport.calls.count("B3_CAS") == 1 and c.transport.calls.count("RETIRE_TERMINAL") == 0
    )
    op = c.boot.apparatus["ex10_operational_context"]
    snapshot = c.boot.read_authority(c.h.authority.descriptor_ref)
    assert op.history.find_run(snapshot, run_id)["lifecycle_state"] == "OPEN"
    before = _v2_private_tree_bytes(c.h.home)
    checkpoint = dict(c.h.checkpoint)
    effects, rpc = list(c.state["effects"]), list(c.transport.calls)
    children, results, actions = list(c.children), list(c.results), list(c.action_results)
    attempts = len(c.factory._attempted)
    # Fresh invocation, same installed factory/session: never advance a failed
    # intent and never use STATUS to clear uncertainty or release authority.
    second = replace(c.c.invocation("execute-official", confirmed=True), native_bootstrap=c.boot)
    with pytest.raises(c.mod.LauncherError, match="^native-intent-state-blocked$") as denied:
        c.mod.execute(second)
    assert not isinstance(denied.value, c.mod.NativeActionFailure)
    assert len(c.factory._attempted) == attempts + 1 and c.factory._attempted[-1] is second
    assert not c.factory._lock.locked() and c.boot.load(second)._bytes == current._bytes
    assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint
    assert c.state["effects"] == effects and c.transport.calls == rpc
    assert c.children == children and c.results == results and c.action_results == actions


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
def test_execute_before_start_or_after_a_terminal_is_not_allowed(
    native_factory_resource_case,
    capsys,
):
    """Full native API and public CLI reject EXECUTE before START with validation3."""
    c = native_factory_resource_case
    invocation = c.invocation("execute-official", confirmed=True)
    original = c.boot.load(invocation)
    value = original.to_dict()
    assert value["official_run_id"] is None and not value["in_flight"]
    assert not value["state"]["uncertain"] and not value["state"]["blocked"]
    assert "execute-official" not in value["state"]["next_allowed_actions"]
    before = _v2_private_tree_bytes(c.h.root)
    checkpoint = dict(c.h.checkpoint)
    commands, effects = list(c.state["commands"]), list(c.state["effects"])
    approvals, attempts = list(c.approvals), list(c.factory._attempted)
    # Resource providers explicitly forbid oneoff preparation; the real early
    # guard must reject before any such provider, without a fabricated Intent.
    with pytest.raises(c.mod.LauncherError, match="^native-intent-state-blocked$") as failure:
        c.mod.execute(invocation)
    assert not isinstance(failure.value, c.mod.NativeActionFailure)
    assert len(c.factory._attempted) == len(attempts) + 1
    assert c.factory._attempted[-1] is invocation and not c.factory._lock.locked()
    assert _v2_private_tree_bytes(c.h.root) == before and c.h.checkpoint == checkpoint
    assert c.state["commands"] == commands and c.state["effects"] == effects
    assert c.approvals == approvals and c.boot.load(invocation)._bytes == original._bytes
    capsys.readouterr()
    saved_handlers = {
        number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)
    }
    try:
        code = c.mod.main(
            [
                "execute-official",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
                c.mod.ACTIONS["execute-official"].confirmation,
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in saved_handlers.items():
            signal.signal(number, handler)
    output = capsys.readouterr()
    assert code == 3 and output.out == ""
    assert output.err == "S11_EX10_OFFICIAL_LAUNCHER_CONFIG: native-intent-state-blocked\n"
    assert len(c.factory._attempted) == len(attempts) + 2 and not c.factory._lock.locked()
    assert c.factory._attempted[-1].action == "execute-official"
    assert c.factory._attempted[-1].native_bootstrap is c.boot
    assert _v2_private_tree_bytes(c.h.root) == before and c.h.checkpoint == checkpoint
    assert c.state["commands"] == commands and c.state["effects"] == effects
    assert c.approvals == approvals and c.boot.load(invocation)._bytes == original._bytes


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
def test_execute_refuses_a_stale_attached_preflight(
    native_factory_execution_chain_case, capsys, monkeypatch
):
    """Same actual START report ages899-to901 before new EXEC, never refreshed.

    The existing probe producer is synthetic; SDK START/custody are actual.
    Both report and original envelope expire; no expired envelope is consumed.
    Only common fixture UTC advances, with original authority still valid.
    """
    c = native_factory_execution_chain_case
    observed = _native_current_exec_precondition_case(c, "stale", monkeypatch=monkeypatch)
    assert observed.report_age_at_start == timedelta(seconds=899)
    assert c.c.now["utc"] - observed.checked_at == timedelta(seconds=901)
    assert observed.original_authority_expires > c.c.now["utc"]
    assert observed.original_envelope_expires <= c.c.now["utc"]
    _native_assert_current_exec_denial(c, capsys, observed, "preflight-report-not-acceptable", 3)
    assert [row["pf_problems"] for row in observed.reads] == [
        ["preflight:stale"],
        ["preflight:stale"],
    ]


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_execute_refuses_another_head_or_a_tampered_attached_preflight(
    native_factory_execution_chain_case,
):
    """Physical HEAD mismatch stops EXECUTE before factory or session effects."""
    c = native_factory_execution_chain_case
    started = c.mod.execute(c.invocation)
    assert started["launcher_exit"] == 0 and not c.errors
    assert len(c.action_results) == 1 and c.action_results[0]["action"] == "start"
    invocation = c.advance("execute-official")
    original = c.boot.load(invocation)
    before = _v2_private_tree_bytes(c.h.root)
    checkpoint = dict(c.h.checkpoint)
    effects, calls = list(c.state["effects"]), list(c.transport.calls)
    attempts = list(c.factory._attempted)
    children, results, action_results = list(c.children), list(c.results), list(c.action_results)
    repo = invocation.repo_root
    expected = invocation.expected_git_sha
    assert expected == c.boot.expected_git_sha == _v2_source_git(repo, "rev-parse", "HEAD")
    ref = _v2_source_git(repo, "symbolic-ref", "HEAD")
    tree = _v2_source_git(repo, "rev-parse", "HEAD^{tree}")
    changed = None
    try:
        # Empty commit in this test's owned disposable checkout; source files stay exact.
        _v2_source_git(repo, "commit", "-q", "--allow-empty", "-m", "LOCAL TEST HEAD fault")
        changed = _v2_source_git(repo, "rev-parse", "HEAD")
        assert changed != expected and _v2_source_git(repo, "rev-parse", "HEAD^{tree}") == tree
        with pytest.raises(c.mod.LauncherError, match="^head-mismatch$") as failure:
            c.mod.execute(invocation)
        assert not isinstance(failure.value, c.mod.NativeActionFailure)
        assert invocation.expected_git_sha == c.boot.expected_git_sha == expected
        assert _v2_private_tree_bytes(c.h.root) == before and c.h.checkpoint == checkpoint
        assert (invocation.session_root / c.mod.SESSION_FILE).read_bytes() == original._bytes
        assert c.state["effects"] == effects and c.transport.calls == calls
        assert c.factory._attempted == attempts and not c.factory._lock.locked()
        assert (
            c.children == children and c.results == results and c.action_results == action_results
        )
        assert not c.errors and all(child.poll() == 0 for child in c.children)
        assert c.state["prepared"] is None and c.state["context"] is None
        assert not c.state["created"] and not c.state["running"] and c.state["completion"] == []
    finally:
        if changed is not None:
            # Exact expected-current CAS restores only this fixture's own branch reference.
            _v2_source_git(repo, "update-ref", ref, expected, changed)
    assert _v2_source_git(repo, "rev-parse", "HEAD") == expected
    assert _v2_source_git(repo, "rev-parse", "HEAD^{tree}") == tree
    assert c.boot.load(invocation)._bytes == original._bytes


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
@pytest.mark.parametrize("web", ["worker-missing", "beat", "oneoff", "db-unhealthy"])
def test_execute_needs_a_stable_stack(native_factory_execution_chain_case, capsys, web):
    """Current synthetic namespace/health negative before any native EXEC intent.

    START and its SDK/custody are real. The installed complete namespace query
    is synthetic mechanical integration, not Docker/PID1 qualification. Health
    is independently observed while DB remains running, never a READY/B5 proxy.
    Pure prepare_oneoff planning precedes the installed denial.
    """
    c = native_factory_execution_chain_case
    observed = _native_current_exec_precondition_case(c, web)
    reasons = {
        "worker-missing": "web-worker-not-running",
        "beat": "beat-running",
        "oneoff": "oneoff-container-present",
        "db-unhealthy": "db-redis-not-healthy",
    }
    _native_assert_current_exec_denial(c, capsys, observed, reasons[web], 3)
    assert [row["reason"] for row in observed.reads] == [reasons[web], reasons[web]]
    if web == "db-unhealthy":
        (db,) = (row for row in observed.namespace["containers"] if row["service"] == "db")
        assert db["state"] == "running" and db["healthy"] is False


@pytest.mark.parametrize("bad", ["run-id", "phase", "extra"])
@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_an_incoherent_execute_output_does_not_advance_the_session(
    native_factory_execution_chain_case,
    monkeypatch,
    bad,
):
    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_execution_chain_case
    started = c.mod.execute(c.invocation)
    assert started["launcher_exit"] == 0 and not c.errors
    invocation = c.advance("execute-official")
    executor = helper.executor
    original_persist = executor.ExecutorOperationalContext.persist_public_result
    pending = {}
    mutations = []

    def persist_then_corrupt_private_result(context, store, result):
        value = original_persist(context, store, result)
        assert context is c.state["context"] and context.action == "execute"
        assert context.finished and context.scientific_finished and context.step_authorized
        assert context.consumed is False
        assert context.channel.position == 5 and result["phase"] == "RECONCILING"
        run_id = c.action_results[0]["result"]["run_id"]
        assert result["run_id"] == run_id == context.proposal["run_id"]
        result_path = store.root / ("executor-result." + context.channel.exchange_id + ".json")
        assert result_path.read_bytes() == executor.canonical_json_bytes(result)
        checkpoint = c.boot.checkpoint()
        codec = c.boot.apparatus["ex10_session_v2"]
        current = codec.load_official_session(
            invocation.session_root / c.mod.SESSION_FILE,
            context=c.boot.context(),
            profile=c.h.profile,
            expected_tip=checkpoint[0],
            expected_count=checkpoint[1],
        )
        pending.update(raw=current._bytes, value=current.to_dict(), checkpoint=checkpoint)
        assert pending["value"]["official_run_id"] == run_id
        assert pending["value"]["state"]["known_phase"] == "PREPARED"
        assert pending["value"]["in_flight"][0]["action"] == "execute-official"
        altered = dict(result)
        if bad == "run-id":
            altered["run_id"] = "s11-01234567-89ab-5cde-8f01-23456789abcd"
            assert altered["run_id"] != run_id
        elif bad == "phase":
            altered["phase"] = "FINALIZED_PASS"
        else:
            altered["extra"] = True
        result_path.write_bytes(executor.canonical_json_bytes(altered))
        assert not mutations
        mutations.append(altered)
        return value

    monkeypatch.setattr(
        executor.ExecutorOperationalContext,
        "persist_public_result",
        persist_then_corrupt_private_result,
    )
    with pytest.raises(c.mod.NativeActionFailure) as failure:
        c.mod.execute(invocation)
    assert failure.value.code == "native-worker-result-invalid"
    assert len(mutations) == 1 and not c.errors and len(c.action_results) == 2
    expected_problems = {
        "run-id": ["run-id-differs"],
        "phase": ["execute:phase"],
        "extra": ["payload-keys"],
    }
    problems, _, _ = c.mod.action_problems(
        "execute-official",
        mutations[0],
        run_id=pending["value"]["official_run_id"],
        reconciled=None,
    )
    assert problems == expected_problems[bad]
    assert (invocation.session_root / c.mod.SESSION_FILE).read_bytes() == pending["raw"]
    assert c.boot.checkpoint() == pending["checkpoint"]
    assert pending["value"]["state"]["uncertain"] and pending["value"]["state"]["blocked"]
    assert pending["value"]["state"]["next_allowed_actions"] == ["inspect"]
    assert c.state["completion"] == [] and c.state["steps"] == 5
    assert [item["action"] for item in c.action_results] == ["start", "execute"]
    assert c.state["effects"] == ["create", "start", "create", "start"]
    assert c.state["actual_emission_proof_admitted"] and c.science.backend.accepted == 50
    assert all(token.released for token in c.science.tokens)
    assert c.transport.calls.count("RESERVE") == c.transport.calls.count("COMMIT_OPEN") == 1
    assert c.transport.calls.count("B3_CAS") == 1
    assert c.transport.calls.count("RETIRE_TERMINAL") == 0
    snapshot = c.boot.read_authority(c.h.authority.descriptor_ref)
    op = c.boot.apparatus["ex10_operational_context"]
    assert (
        op.history.find_run(snapshot, pending["value"]["official_run_id"])["lifecycle_state"]
        == "OPEN"
    )
    assert all(child.poll() == 0 for child in c.children)
    from dataclasses import replace

    for thread in c.threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in c.threads)
    before_tree = _v2_private_tree_bytes(c.h.home)
    before_checkpoint = dict(c.h.checkpoint)
    before_effects, before_calls = list(c.state["effects"]), list(c.transport.calls)
    before_children, before_results = list(c.children), list(c.action_results)
    before_attempts = len(c.factory._attempted)
    for index, (action, cause) in enumerate(
        (
            ("execute-official", "native-intent-state-blocked"),
            ("status", "native-status-run-required"),
        ),
        start=1,
    ):
        denied = replace(c.c.invocation(action, confirmed=True), native_bootstrap=c.boot)
        with pytest.raises(c.mod.LauncherError) as failure:
            c.mod.execute(denied)
        assert not isinstance(failure.value, c.mod.NativeActionFailure)
        assert failure.value.code == cause
        assert len(c.factory._attempted) == before_attempts + index
        assert c.factory._attempted[-1] is denied and not c.factory._lock.locked()
        assert _v2_private_tree_bytes(c.h.home) == before_tree
        assert c.h.checkpoint == before_checkpoint
        assert c.state["effects"] == before_effects and c.transport.calls == before_calls
        assert c.children == before_children and c.action_results == before_results
        assert c.state["completion"] == [] and c.science.backend.accepted == 50
        assert not c.errors and all(child.poll() == 0 for child in c.children)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
@pytest.mark.parametrize("kind", ["timeout", "interrupted"])
def test_timeouts_and_signals_abort_clean_up_and_never_complete(
    native_factory_execution_chain_case, capsys, kind
):
    """Public abort5, actual owned client cleanup and preserved uncertain worker.

    ORIGINAL_V1_RM_FOOTPRINT_NOT_COVERED: PhaseB450/G0A3section20 require v2
    worker preservation and separate explicit management. This tests the v2
    replacement contract; no literal v1 rm-f coverage or executor-gone claim.
    """
    import fcntl

    c = native_factory_execution_chain_case
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    invocation = c.advance("execute-official")
    c.state["hold_relay_retirement_for_public_abort"] = kind
    capsys.readouterr()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        code = c.mod.main(
            [
                "execute-official",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
                c.mod.ACTIONS["execute-official"].confirmation,
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    captured = capsys.readouterr()
    assert code == c.mod.EXIT_APPARATUS_ABORT == 5 and captured.out == ""
    assert captured.err == "S11_EX10_OFFICIAL_LAUNCHER_ABORT: " + kind + "\n"
    assert all(signal.getsignal(number) is handler for number, handler in handlers.items())
    assert c.state["held_relay_at_actual_worker_wait"] is True
    if kind == "interrupted":
        assert c.state["actual_public_abort_signals"] == [(os.getpid(), signal.SIGTERM)]
    else:
        assert c.state.get("actual_public_abort_signals", []) == []
    for thread in c.threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in c.threads)
    assert not c.errors and len(c.action_results) == len(c.children) == 2
    assert c.children[0].poll() == 0 and c.children[-1].poll() is not None
    assert all(
        stream is None or stream.closed
        for stream in (
            c.children[-1].stdin,
            c.children[-1].stdout,
            c.children[-1].stderr,
        )
    )
    context = c.state["context"]
    host = c.state["host_channel"]
    assert context.finished and context.scientific_finished and context.step_authorized
    assert context.consumed is False and context.channel.position == 5
    assert (
        context.channel.closed
        and context.channel.read_fd is context.channel.write_fd is context.channel.error_fd is None
    )
    assert host.closed and host.read_fd is host.write_fd is host.error_fd is None
    assert c.state["publication"].store.resolver._closed
    assert c.state["publication"].store.resolver._fds == []
    assert c.action_results[-1]["result"]["phase"] == "RECONCILING"
    assert c.science.backend.accepted == 50 and all(token.released for token in c.science.tokens)
    assert not c.factory._lock.locked() and c.state["completion"] == []
    pending = c.state["actual_public_abort_pending_boundary"]
    current = c.boot.load(invocation)
    assert current._bytes == pending["b2_raw"]
    assert (
        c.boot.checkpoint() == pending["checkpoint"]
        and c.h.checkpoint == pending["checkpoint_state"]
    )
    assert _v2_private_tree_bytes(c.h.home) == pending["physical_home"]
    value = current.to_dict()
    assert value["official_run_id"] == c.action_results[0]["result"]["run_id"]
    assert value["state"]["known_phase"] == "PREPARED"
    assert value["state"]["uncertain"] and value["state"]["blocked"]
    assert value["state"]["next_allowed_actions"] == ["inspect"]
    assert len(value["in_flight"]) == 1 and value["in_flight"][0]["action"] == "execute-official"
    assert c.state["effects"] == ["create", "start", "create", "start"]
    assert c.state["running"] and c.state["worker_retired"] is False
    assert c.transport.calls == pending["rpc"]
    assert (
        c.transport.calls.count("B3_CAS") == 1 and c.transport.calls.count("RETIRE_TERMINAL") == 0
    )
    op = c.boot.apparatus["ex10_operational_context"]
    assert (
        op.history.find_run(
            c.boot.read_authority(c.h.authority.descriptor_ref), value["official_run_id"]
        )["lifecycle_state"]
        == "OPEN"
    )
    # SDK custody lock is actually free after client/actor cleanup. Acquiring it
    # in this test does not discharge the retained native operational intent.
    fd = os.open(
        Path(c.state["store"].root) / ".s11-custody.lock",
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_execute_stopped_before_t0_only_allows_the_official_abandon(
    native_factory_execution_chain_case,
    monkeypatch,
):
    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_execution_chain_case
    executor = helper.executor
    started = c.mod.execute(c.invocation)
    assert started["launcher_exit"] == 0 and not c.errors
    run_id = c.action_results[0]["result"]["run_id"]
    invocation = c.advance("execute-official")
    original_persist = executor._persist
    interrupted = []

    def interrupt_after_actual_armed(store, actual_run, artifact_type, payload, **kwargs):
        record = original_persist(store, actual_run, artifact_type, payload, **kwargs)
        if artifact_type == executor.ART_ARMED:
            # Fault delivery stays inside this fixture's actual executor thread;
            # no process signal, fabricated ARMED record or replaced domain result.
            assert not interrupted and store is c.state["store"]
            assert actual_run == run_id and c.state["cfg"]["action"] == "execute"
            context = c.state["context"]
            assert context.finished and context.step_authorized and context.consumed is False
            assert context.owner == (os.getpid(), threading.get_ident())
            assert payload["requests_prepared"] == helper.ex10.TOTAL_REQUESTS
            assert payload["tokens_verified"] == helper.ex10.VIRTUAL_USERS
            interrupted.append(record)
            raise KeyboardInterrupt
        return record

    import threading

    monkeypatch.setattr(executor, "_persist", interrupt_after_actual_armed)
    executed = c.mod.execute(invocation)
    assert executed["launcher_exit"] == 0 and executed["classification"] == "COHERENT"
    assert len(interrupted) == 1 and not c.errors
    stopped = c.action_results[-1]["result"]
    assert stopped == {
        "run_id": run_id,
        "phase": "STOPPED_PRE_T0",
        "reasons": ["operator-interrupted"],
    }
    store = c.state["store"]
    actual = executor.load_existing_state_readonly(store, run_id)
    assert executor.phase_of(actual) is executor.Phase.STOPPED_PRE_T0
    assert actual.has(executor.ART_ARMED) and actual.has(executor.ART_EMISSION_ABORTED)
    assert actual.has(executor.ART_EMISSION)
    assert not actual.has(executor.ART_RAW) and not actual.has(executor.ART_RAW_REJECTED)
    aborted = actual.payload(executor.ART_EMISSION_ABORTED)
    assert aborted["pre_t0"] is True and aborted["t0_published"] is False
    assert aborted["requests_sent"] == 0 and aborted["reasons"] == ["operator-interrupted"]
    assert c.science.backend.accepted == 0 and all(token.released for token in c.science.tokens)
    current = c.boot.load(invocation).to_dict()
    assert current["official_run_id"] == run_id and current["in_flight"] == []
    assert not current["state"]["uncertain"] and not current["state"]["blocked"]
    assert current["state"]["known_phase"] == "STOPPED_PRE_T0"
    assert current["state"]["next_allowed_actions"] == [
        "abandon-official-pre-t0",
        "inspect",
        "status",
    ]
    assert current["history"][-1]["payload"]["event"] == "complete-execute-official"
    assert c.state["completion"] == [False, True]
    assert c.transport.calls.count("B3_CAS") == 1
    assert c.transport.calls.count("RETIRE_TERMINAL") == 0
    abandonment = c.advance("abandon-official-pre-t0")
    finalized = c.mod.execute(abandonment)
    assert finalized["launcher_exit"] == 0 and finalized["classification"] == "COHERENT"
    terminal = c.action_results[-1]["result"]
    assert terminal["run_id"] == run_id and terminal["decision"] == "NOT_DEMONSTRATED"
    assert terminal["lifecycle"] == "ABANDONED_PRE_T0" and terminal["binding"] is False
    assert "abandoned-before-t0:operator-stop" in terminal["reasons"]
    assert terminal["custody_errors"] == [] and c.state["local_seal_checked"]
    assert c.state["cfg"]["command_options"]["abandon_reason"] == c.mod.ABANDON_REASON
    assert [item["action"] for item in c.action_results] == ["start", "execute", "finalize"]
    assert [item["steps"] for item in c.action_results] == [9, 5, 5]
    assert len({item["cid"] for item in c.action_results}) == 3
    assert len({item["exchange"] for item in c.action_results}) == 3
    assert c.science.backend.accepted == 0 and not c.errors
    assert c.state["completion"] == [False, True]
    assert c.transport.calls.count("B3_CAS") == 2
    assert c.transport.calls.count("RETIRE_TERMINAL") == 1
    final = c.boot.load(abandonment).to_dict()
    assert final["official_run_id"] == run_id and final["in_flight"] == []
    assert final["state"]["known_phase"] == "ABANDONED_PRE_T0"
    assert final["state"]["next_allowed_actions"] == ["cleanup-stack", "inspect", "status"]
    assert final["history"][-1]["payload"]["event"] == "complete-abandon-official-pre-t0"
    assert not final["state"]["uncertain"] and not final["state"]["blocked"]
    assert all(child.poll() == 0 for child in c.children)


# --- DRAIN, RECONCILE y FINALIZE (63–75) ---------------------------------------------------------


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_drain_only_after_an_execute_and_with_the_run_in_draining(
    native_factory_recovery_precondition_case, capsys
):
    """Actual SDK DRAINING component and native drain.

    It never uses STATUS to unlock an interrupted Factory intent.
    """
    from dataclasses import replace

    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_recovery_precondition_case

    def deny_drain_without_effects():
        invocation = replace(c.c.invocation("drain", confirmed=True), native_bootstrap=c.boot)
        original = c.boot.load(invocation)
        before, checkpoint = _v2_private_tree_bytes(c.h.home), dict(c.h.checkpoint)
        effects, rpc, commands = (
            list(c.state["effects"]),
            list(c.transport.calls),
            list(c.c.state["commands"]),
        )
        children, results, actions = list(c.children), list(c.results), list(c.action_results)
        attempts = len(c.factory._attempted)
        with pytest.raises(c.mod.LauncherError, match="^native-intent-state-blocked$") as denied:
            c.mod.execute(invocation)
        assert not isinstance(denied.value, c.mod.NativeActionFailure)
        capsys.readouterr()
        handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
        try:
            code = c.mod.main(
                [
                    "drain",
                    "--expected-git-sha",
                    invocation.expected_git_sha,
                    "--session-root",
                    str(invocation.session_root),
                    c.mod.ACTIONS["drain"].confirmation,
                ],
                environ=invocation.environ,
                native_bootstrap=c.boot,
            )
        finally:
            for number, handler in handlers.items():
                signal.signal(number, handler)
        captured = capsys.readouterr()
        assert code == 3 and captured.out == ""
        assert captured.err == "S11_EX10_OFFICIAL_LAUNCHER_CONFIG: native-intent-state-blocked\n"
        assert len(c.factory._attempted) == attempts + 2 and not c.factory._lock.locked()
        assert c.boot.load(invocation)._bytes == original._bytes
        assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint
        assert c.state["effects"] == effects and c.transport.calls == rpc
        assert c.c.state["commands"] == commands
        assert c.children == children and c.results == results and c.action_results == actions

    # No drain before any START or directly from authentic SDK PREPARED.
    deny_drain_without_effects()
    started = c.mod.execute(c.invocation)
    assert started["launcher_exit"] == 0 and not c.errors
    assert c.action_results[0]["steps"] == 9
    deny_drain_without_effects()
    previous = c.prepare_precondition("drain")
    assert previous["phase"] == "DRAINING" and previous["actual_exit137"]
    assert previous["host_single5_completed"] and not previous["child"].is_alive()
    run_id = previous["run_id"]
    invocation = c.advance("drain")
    before_requests = c.science.backend.accepted
    outcome = c.mod.execute(invocation)
    assert outcome["launcher_exit"] == 0 and outcome["classification"] == "COHERENT"
    assert outcome["state"]["known_phase"] == "RECONCILING"
    assert c.science.backend.accepted == before_requests == 50
    assert [item["action"] for item in c.action_results] == ["start", "drain"]
    assert [item["steps"] for item in c.action_results] == [9, 5]
    assert c.results[-1]["run_id"] == run_id and c.results[-1]["requests_sent_during_drain"] == 0
    assert c.mod.action_problems("drain", c.results[-1], run_id=run_id, reconciled=None)[0] == []
    assert not c.errors and c.state["completion"] == [False, True]
    assert len(c.children) == 2 and all(child.poll() == 0 for child in c.children)
    state = helper.executor.load_existing_state_readonly(c.state["store"], run_id)
    assert helper.executor.phase_of(state) is helper.executor.Phase.RECONCILING
    assert state.has(helper.executor.ART_DRAIN) and c.state["store"].verify(run_id) == []
    final = c.boot.load(invocation)
    assert final.to_dict()["official_run_id"] == run_id and not final.to_dict()["in_flight"]
    assert final.to_dict()["history"][-1]["payload"]["event"] == "complete-drain"
    assert "reconcile" in final.to_dict()["state"]["next_allowed_actions"]
    assert "drain" not in final.to_dict()["state"]["next_allowed_actions"]
    assert (
        c.transport.calls.count("B3_CAS") == 1 and c.transport.calls.count("RETIRE_TERMINAL") == 0
    )
    # Drain never chains reconciliation/finalization and cannot be repeated.
    deny_drain_without_effects()


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_reconcile_after_execute_once_and_never_before(native_factory_execution_chain_case):
    c = native_factory_execution_chain_case
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    completed_start_observer = tuple(c.state["completion"])
    assert completed_start_observer == (False, True)
    before_invocation = c.advance("reconcile")
    prior = c.boot.load(before_invocation)
    checkpoint = c.boot.checkpoint()
    effects, calls = list(c.state["effects"]), list(c.transport.calls)
    assert prior.to_dict()["state"]["known_phase"] == "PREPARED"
    with pytest.raises(c.mod.LauncherError, match="^native-intent-state-blocked$") as failure:
        c.mod.execute(before_invocation)
    assert not isinstance(failure.value, c.mod.NativeActionFailure)
    assert c.boot.load(before_invocation)._bytes == prior._bytes
    assert c.boot.checkpoint() == checkpoint
    assert c.state["effects"] == effects and c.transport.calls == calls
    assert len(c.children) == len(c.action_results) == 1
    assert not c.errors and not c.state["created"] and not c.state["running"]
    assert c.state["completion"] == [] and not c.factory._lock.locked()
    # Restore only the saved observation of actual START completion after the
    # refused RECONCILE proved that it caused no worker or B2 effect.
    c.state["completion"].extend(completed_start_observer)
    execute_invocation = c.advance("execute-official")
    assert c.mod.execute(execute_invocation)["launcher_exit"] == 0 and not c.errors
    invocation = c.advance("reconcile")
    result = c.mod.execute(invocation)
    assert result["launcher_exit"] == 0 and result["classification"] == "COHERENT"
    assert not c.errors and c.state["completion"] == [False, True]
    actual = c.action_results[-1]
    assert actual["action"] == "reconcile" and actual["steps"] == 5
    assert actual["context"].finished and actual["context"].step_authorized
    assert actual["context"].scientific_finished and actual["context"].consumed is False
    decision = actual["result"]["decision"]
    assert decision in {"PASS", "FAIL", "NOT_DEMONSTRATED"}
    assert c.state["cfg"]["action"] == "reconcile"
    assert c.state["cfg"]["command_options"]["run_id"] == actual["result"]["run_id"]
    completed = c.boot.load(invocation)
    value = completed.to_dict()
    assert value["state"]["known_phase"] == "RECONCILING"
    assert value["state"]["next_allowed_actions"] == ["finalize", "inspect", "status"]
    assert not value["in_flight"] and not value["state"]["uncertain"]
    assert value["history"][-1]["payload"]["event"] == "complete-reconcile"
    assert [item["action"] for item in c.action_results] == ["start", "execute", "reconcile"]
    assert [item["steps"] for item in c.action_results] == [9, 5, 5]
    assert len({item["cid"] for item in c.action_results}) == 3
    assert len({item["exchange"] for item in c.action_results}) == 3
    assert c.science.backend.accepted == 50 and all(token.released for token in c.science.tokens)
    assert c.transport.calls.count("B3_CAS") == 1
    assert c.transport.calls.count("RETIRE_TERMINAL") == 0
    repeat_invocation = c.advance("reconcile")
    before = _v2_private_tree_bytes(c.h.root)
    checkpoint = c.boot.checkpoint()
    effects, calls = list(c.state["effects"]), list(c.transport.calls)
    with pytest.raises(c.mod.LauncherError, match="^native-intent-state-blocked$") as failure:
        c.mod.execute(repeat_invocation)
    assert not isinstance(failure.value, c.mod.NativeActionFailure)
    assert _v2_private_tree_bytes(c.h.root) == before
    assert c.boot.load(repeat_invocation)._bytes == completed._bytes
    assert c.boot.checkpoint() == checkpoint
    assert c.state["effects"] == effects and c.transport.calls == calls
    assert len(c.children) == len(c.action_results) == 3
    assert not c.errors and not c.state["created"] and not c.state["running"]
    assert c.state["completion"] == [] and not c.factory._lock.locked()
    assert all(child.poll() == 0 for child in c.children)


def _v2_verify_custody_without_normalizing(store, run_id):
    """Observe existing custody under its shared lock without mkdir/chmod."""
    from apps.experiments_app.tests.test_ex10_executor import executor

    with executor._shared_custody_lock(store):
        run_root = store.root / "runs" / run_id
        before = executor._index_fingerprint(run_root)
        state = executor._read_existing_state_under_caller_lock(store, run_id)
        assert state is not None
        errors = executor.verify_readonly(store, state)
        assert executor._index_fingerprint(run_root) == before
        return errors


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
def test_reconcile_never_touches_a_run_other_than_the_sessions(
    native_factory_execution_chain_case, monkeypatch, capsys
):
    """Real LOCAL prior remains intact through RECON; FINAL must detect its mutation.

    The v1 repo/var snapshot is replaced by the approved safe evidence root.
    Both the original positive RECON and negative FINAL obligations remain.
    """
    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_execution_chain_case
    executor = helper.executor
    store = helper.EvidenceStore(
        Path(c.h.profile["host_roots"]["evidence"]["path"]),
        custody_hmac_key=c.factory.custody_key,
        publication_verification_key=c.factory.signer.public_key().public_bytes_raw(),
        rolling_checksums=False,
    )
    probes = executor.ExecutorProbes(
        preflight=lambda options: helper.preflight_report(
            git_sha=c.boot.expected_git_sha,
            checked_at=c.c.now["utc"],
        ),
        dataset=helper.ex10.load_dataset,
        provider=lambda: [],
        sources=lambda root: [],
        fingerprint=executor.executor_fingerprint,
        now=lambda: c.c.now["utc"],
    )
    local = executor.start_run(
        store,
        git_sha=c.boot.expected_git_sha,
        source_root=c.boot.repo_root,
        nonce="native-prior-reconcile-preservation",
        evidence_mode=helper.LOCAL,
        timezone="UTC",
        probes=probes,
    )
    executor.finalize_run(
        store,
        run_id=local["run_id"],
        git_sha=c.boot.expected_git_sha,
        abandon_reason="synthetic-pre-t0-stop",
        probes=probes,
    )
    prior = executor.prior_ex10_runs(store)
    assert len(prior) == 1 and prior[0].finalized and not prior[0].problems
    assert prior[0].run_id == local["run_id"] and prior[0].evidence_mode == helper.LOCAL
    retained = store.root / "runs" / local["run_id"]
    before = _v2_private_tree_bytes(retained)
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    assert c.action_results[-1]["result"]["run_id"] != local["run_id"]
    assert _v2_private_tree_bytes(retained) == before
    for action in ("execute-official", "reconcile"):
        invocation = c.advance(action)
        assert c.mod.execute(invocation)["launcher_exit"] == 0 and not c.errors
        assert (
            _v2_private_tree_bytes(retained) == before
            and _v2_verify_custody_without_normalizing(store, local["run_id"]) == []
        )
    reconciliation = c.action_results[-1]["result"]
    assert reconciliation["decision"] == "PASS" and c.science.backend.accepted == 50
    invocation = c.advance("finalize")
    original_persist = executor.ExecutorOperationalContext.persist_public_result
    boundary, mutations = {}, []

    def persist_final_then_change_other_run(context, worker_store, result):
        value = original_persist(context, worker_store, result)
        assert context is c.state["context"] and context.action == "finalize"
        assert context.finished and context.scientific_finished and context.step_authorized
        assert context.channel.position == 5 and not context.consumed
        assert result["run_id"] == reconciliation["run_id"] != local["run_id"]
        assert result["decision"] == "PASS" and result["binding"] is True
        assert result["lifecycle"] == "FINALIZED_PASS" and result["custody_errors"] == []
        assert _v2_private_tree_bytes(retained) == before and not mutations
        checkpoint = c.boot.checkpoint()
        codec = c.boot.apparatus["ex10_session_v2"]
        pending = codec.load_official_session(
            invocation.session_root / c.mod.SESSION_FILE,
            context=c.boot.context(),
            profile=c.h.profile,
            expected_tip=checkpoint[0],
            expected_count=checkpoint[1],
        )
        assert pending.to_dict()["state"]["known_phase"] == "RECONCILING"
        assert pending.to_dict()["in_flight"][0]["action"] == "finalize"
        result_path = worker_store.root / (
            "executor-result." + context.channel.exchange_id + ".json"
        )
        raw = result_path.read_bytes()
        assert raw == executor.canonical_json_bytes(result)
        metadata = retained / "metadata"
        facts = metadata.stat()
        os.utime(metadata, ns=(facts.st_atime_ns, facts.st_mtime_ns + 1000000000))
        assert metadata.stat().st_mtime_ns != facts.st_mtime_ns
        assert _v2_private_tree_bytes(retained) != before
        assert worker_store.verify(result["run_id"]) == [] and result_path.read_bytes() == raw
        boundary.update(
            b2_raw=pending._bytes, checkpoint=checkpoint, result_path=result_path, result_raw=raw
        )
        mutations.append((local["run_id"], str(metadata)))
        return value

    monkeypatch.setattr(
        executor.ExecutorOperationalContext,
        "persist_public_result",
        persist_final_then_change_other_run,
    )
    capsys.readouterr()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        code = c.mod.main(
            [
                "finalize",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
                c.mod.ACTIONS["finalize"].confirmation,
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    captured = capsys.readouterr()
    assert code == c.mod.EXIT_LAUNCHER_FAILURE == 4 and captured.out == ""
    assert captured.err == "S11_EX10_OFFICIAL_LAUNCHER_FAILURE: prior-run-changed\n"
    assert len(mutations) == 1 and not c.errors
    assert [item["action"] for item in c.action_results] == [
        "start",
        "execute",
        "reconcile",
        "finalize",
    ]
    assert [item["steps"] for item in c.action_results] == [9, 5, 5, 5]
    assert all(child.poll() == 0 for child in c.children)
    assert c.state["completion"] == [] and not c.factory._lock.locked()
    assert c.science.backend.accepted == 50 and all(token.released for token in c.science.tokens)
    current = c.boot.load(invocation)
    assert current._bytes == boundary["b2_raw"] and c.boot.checkpoint() == boundary["checkpoint"]
    assert boundary["result_path"].read_bytes() == boundary["result_raw"]
    value = current.to_dict()
    assert value["official_run_id"] == reconciliation["run_id"]
    assert value["state"]["known_phase"] == "RECONCILING"
    assert value["state"]["uncertain"] and value["state"]["blocked"]
    assert value["state"]["next_allowed_actions"] == ["inspect"]
    assert len(value["in_flight"]) == 1 and value["in_flight"][0]["action"] == "finalize"
    assert (
        c.transport.calls.count("B3_CAS") == 1 and c.transport.calls.count("RETIRE_TERMINAL") == 0
    )
    op = c.boot.apparatus["ex10_operational_context"]
    assert (
        op.history.find_run(
            c.boot.read_authority(c.h.authority.descriptor_ref), value["official_run_id"]
        )["lifecycle_state"]
        == "OPEN"
    )
    sdk = executor.load_existing_state_readonly(store, value["official_run_id"])
    assert (
        executor.phase_of(sdk) is executor.Phase.FINALIZED_PASS
        and store.verify(value["official_run_id"]) == []
    )
    assert (
        _v2_private_tree_bytes(retained) != before
    )  # Physical corruption is preserved, never rolled back.


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_finalize_after_reconcile_with_the_same_decision(native_factory_execution_chain_case):
    c = native_factory_execution_chain_case
    started = c.mod.execute(c.invocation)
    assert started["launcher_exit"] == 0 and not c.errors
    execute_invocation = c.advance("execute-official")
    executed = c.mod.execute(execute_invocation)
    assert executed["launcher_exit"] == 0 and not c.errors
    # Save the actual successful EXECUTE observer before the fixture driver
    # prepares another action. The product never reads this observer list.
    completed_execute_observer = tuple(c.state["completion"])
    assert completed_execute_observer == (False, True)
    early_invocation = c.advance("finalize")
    prior = c.boot.load(early_invocation)
    checkpoint = c.boot.checkpoint()
    effects = list(c.state["effects"])
    calls = list(c.transport.calls)
    worker_count = len(c.children)
    assert c.state["completion"] == []
    with pytest.raises(c.mod.LauncherError, match="^native-intent-state-blocked$") as failure:
        c.mod.execute(early_invocation)
    assert not isinstance(failure.value, c.mod.NativeActionFailure)
    assert c.boot.load(early_invocation)._bytes == prior._bytes
    assert c.boot.checkpoint() == checkpoint
    assert c.state["effects"] == effects and c.transport.calls == calls
    assert len(c.children) == worker_count and len(c.action_results) == 2
    assert c.state["completion"] == []
    assert not c.state["created"] and not c.state["running"] and not c.errors
    # No attempt reached the worker or B2. Restore only the fixture's saved
    # observation of the previous actual completion, so its driver can prepare
    # RECONCILE. No completion is synthesized for the refused FINALIZE.
    c.state["completion"].extend(completed_execute_observer)
    reconciled_invocation = c.advance("reconcile")
    reconciled = c.mod.execute(reconciled_invocation)
    assert reconciled["launcher_exit"] == 0 and not c.errors
    decision = c.action_results[-1]["result"]["decision"]
    assert decision in {"PASS", "FAIL", "NOT_DEMONSTRATED"}
    finalize_invocation = c.advance("finalize")
    finalized = c.mod.execute(finalize_invocation)
    assert finalized["launcher_exit"] == 0 and finalized["classification"] == "COHERENT"
    assert not c.errors and c.state["completion"] == [False, True]
    terminal = c.action_results[-1]["result"]
    assert terminal["decision"] == decision
    expected_phase = {
        "PASS": "FINALIZED_PASS",
        "FAIL": "FINALIZED_FAIL",
        "NOT_DEMONSTRATED": "NOT_DEMONSTRATED",
    }[decision]
    assert terminal["lifecycle"] == expected_phase
    assert terminal["binding"] is (decision in {"PASS", "FAIL"})
    assert [item["action"] for item in c.action_results] == [
        "start",
        "execute",
        "reconcile",
        "finalize",
    ]
    assert [item["steps"] for item in c.action_results] == [9, 5, 5, 5]
    assert len({item["cid"] for item in c.action_results}) == 4
    assert len({item["exchange"] for item in c.action_results}) == 4
    assert c.state["cfg"]["action"] == "finalize"
    assert c.state["cfg"]["command_options"]["run_id"] == terminal["run_id"]
    assert c.state["local_seal_checked"]
    assert c.transport.calls.count("RESERVE") == c.transport.calls.count("COMMIT_OPEN") == 1
    assert c.transport.calls.count("B3_CAS") == 2
    assert c.transport.calls.count("RETIRE_TERMINAL") == 1
    current = c.boot.load(finalize_invocation)
    value = current.to_dict()
    assert current.header == c.c.initial.header and value["official_run_id"] == terminal["run_id"]
    assert value["state"]["known_phase"] == expected_phase
    assert value["state"]["next_allowed_actions"] == ["cleanup-stack", "inspect", "status"]
    assert value["history"][-1]["payload"]["event"] == "complete-finalize"
    assert value["in_flight"] == []
    assert all(child.poll() == 0 for child in c.children)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
@pytest.mark.parametrize(
    ("fault", "problem"),
    [
        pytest.param(
            "decision",
            "finalize:decision-differs-from-reconciliation",
            id="payload0-finalize:decision-differs-from-reconciliation",
        ),
        pytest.param("lifecycle", "finalize:lifecycle", id="payload1-finalize:lifecycle"),
        pytest.param("binding", "finalize:binding", id="payload2-finalize:binding"),
        pytest.param("custody", "finalize:custody", id="payload3-finalize:custody"),
        pytest.param("run-id", "run-id-differs", id="payload4-run-id-differs"),
    ],
)
def test_a_finalize_output_that_contradicts_the_reconciliation_is_a_failure(
    native_factory_execution_chain_case,
    monkeypatch,
    fault,
    problem,
):
    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_execution_chain_case
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    for action in ("execute-official", "reconcile"):
        invocation = c.advance(action)
        assert c.mod.execute(invocation)["launcher_exit"] == 0 and not c.errors
    reconciliation = c.action_results[-1]["result"]
    decision = reconciliation["decision"]
    # This fixture has demonstrated binding decisions; ND fails the premise.
    assert decision in {"PASS", "FAIL"} and reconciliation["provisional"] is True
    invocation = c.advance("finalize")
    executor = helper.executor
    original_persist = executor.ExecutorOperationalContext.persist_public_result
    pending, mutations = {}, []

    def persist_then_corrupt_private_result(context, store, result):
        value = original_persist(context, store, result)
        assert context is c.state["context"] and context.action == "finalize"
        assert context.finished and context.scientific_finished and context.step_authorized
        assert context.consumed is False
        assert context.channel.position == 5 and c.state["local_seal_checked"]
        assert result["run_id"] == reconciliation["run_id"] == context.proposal["run_id"]
        assert result["decision"] == decision and result["binding"] is True
        assert result["custody_errors"] == []
        expected_lifecycle = {"PASS": "FINALIZED_PASS", "FAIL": "FINALIZED_FAIL"}[decision]
        assert result["lifecycle"] == expected_lifecycle
        result_path = store.root / ("executor-result." + context.channel.exchange_id + ".json")
        assert result_path.read_bytes() == executor.canonical_json_bytes(result)
        checkpoint = c.boot.checkpoint()
        codec = c.boot.apparatus["ex10_session_v2"]
        current = codec.load_official_session(
            invocation.session_root / c.mod.SESSION_FILE,
            context=c.boot.context(),
            profile=c.h.profile,
            expected_tip=checkpoint[0],
            expected_count=checkpoint[1],
        )
        pending.update(raw=current._bytes, value=current.to_dict(), checkpoint=checkpoint)
        assert pending["value"]["official_run_id"] == reconciliation["run_id"]
        assert pending["value"]["state"]["known_phase"] == "RECONCILING"
        assert pending["value"]["in_flight"][0]["action"] == "finalize"
        altered = dict(result)
        if fault == "decision":
            altered["decision"] = {"PASS": "FAIL", "FAIL": "PASS"}[decision]
            altered["lifecycle"] = {
                "PASS": "FINALIZED_PASS",
                "FAIL": "FINALIZED_FAIL",
            }[altered["decision"]]
        elif fault == "lifecycle":
            altered["lifecycle"] = {
                "PASS": "FINALIZED_FAIL",
                "FAIL": "FINALIZED_PASS",
            }[decision]
        elif fault == "binding":
            altered["binding"] = False
        elif fault == "custody":
            altered["custody_errors"] = ["sha256:x"]
        else:
            assert fault == "run-id"
            altered["run_id"] = "s11-01234567-89ab-5cde-8f01-23456789abcd"
            assert altered["run_id"] != result["run_id"]
        result_path.write_bytes(executor.canonical_json_bytes(altered))
        assert not mutations
        mutations.append(altered)
        return value

    monkeypatch.setattr(
        executor.ExecutorOperationalContext,
        "persist_public_result",
        persist_then_corrupt_private_result,
    )
    with pytest.raises(c.mod.NativeActionFailure) as failure:
        c.mod.execute(invocation)
    assert failure.value.code == "native-worker-result-invalid"
    assert len(mutations) == 1 and not c.errors and len(c.action_results) == 4
    problems, _, _ = c.mod.action_problems(
        "finalize",
        mutations[0],
        run_id=reconciliation["run_id"],
        reconciled=decision,
    )
    assert problems == [problem]
    assert (invocation.session_root / c.mod.SESSION_FILE).read_bytes() == pending["raw"]
    assert c.boot.checkpoint() == pending["checkpoint"]
    assert pending["value"]["state"]["uncertain"] and pending["value"]["state"]["blocked"]
    assert pending["value"]["state"]["next_allowed_actions"] == ["inspect"]
    assert c.state["completion"] == [] and c.state["steps"] == 5
    assert c.state["local_seal_checked"] and c.state["actual_emission_proof_admitted"]
    actual = c.action_results[-1]
    assert actual["action"] == "finalize" and actual["result"]["decision"] == decision
    assert actual["result"]["binding"] is True and actual["result"]["custody_errors"] == []
    assert [item["action"] for item in c.action_results] == [
        "start",
        "execute",
        "reconcile",
        "finalize",
    ]
    assert [item["steps"] for item in c.action_results] == [9, 5, 5, 5]
    assert len({item["cid"] for item in c.action_results}) == 4
    assert len({item["exchange"] for item in c.action_results}) == 4
    assert c.state["effects"] == ["create", "start"] * 4
    assert c.science.backend.accepted == 50 and all(token.released for token in c.science.tokens)
    assert c.transport.calls.count("RESERVE") == c.transport.calls.count("COMMIT_OPEN") == 1
    assert c.transport.calls.count("B3_CAS") == 1
    assert c.transport.calls.count("RETIRE_TERMINAL") == 0
    snapshot = c.boot.read_authority(c.h.authority.descriptor_ref)
    op = c.boot.apparatus["ex10_operational_context"]
    assert op.history.find_run(snapshot, reconciliation["run_id"])["lifecycle_state"] == "OPEN"
    assert all(child.poll() == 0 for child in c.children)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
def test_an_interrupted_finalize_resumes_once_and_a_finalized_run_never_again(
    native_factory_execution_chain_case, capsys
):
    """Actual SDK seal plus real public interruption leaves FINAL pending.

    MIGRATED_CORE_ONLY: the v1 STATUS-to-DECIDED-to-refinalize oracle is
    superseded by PhaseB443 and MASTER1204 and is explicitly not credited.
    The separate native completed-FINAL test covers finalized-never-again
    using an independent genuinely completed context, not pending repair.
    """
    from apps.experiments_app.tests import test_ex10_executor as helper

    kind = "interrupted"

    c = native_factory_execution_chain_case
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    for action in ("execute-official", "reconcile"):
        invocation = c.advance(action)
        assert c.mod.execute(invocation)["launcher_exit"] == 0 and not c.errors
    assert c.action_results[-1]["result"]["decision"] == "PASS"
    invocation = c.advance("finalize")
    c.state["hold_terminal_relay_for_public_abort"] = kind
    capsys.readouterr()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        code = c.mod.main(
            [
                "finalize",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
                c.mod.ACTIONS["finalize"].confirmation,
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    captured = capsys.readouterr()
    assert code == c.mod.EXIT_APPARATUS_ABORT == 5 and captured.out == ""
    assert captured.err == "S11_EX10_OFFICIAL_LAUNCHER_ABORT: " + kind + "\n"
    assert c.state["held_terminal_relay_at_actual_wait"]
    assert c.state.get("actual_terminal_abort_signals", []) == (
        [(os.getpid(), signal.SIGTERM)] if kind == "interrupted" else []
    )
    for thread in c.threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in c.threads)
    assert not c.errors and len(c.children) == len(c.action_results) == 4
    assert [a["action"] for a in c.action_results] == ["start", "execute", "reconcile", "finalize"]
    assert [a["steps"] for a in c.action_results] == [9, 5, 5, 5]
    assert all(child.poll() == 0 for child in c.children[:-1])
    assert c.children[-1].poll() is not None
    assert all(
        stream is None or stream.closed
        for stream in (
            c.children[-1].stdin,
            c.children[-1].stdout,
            c.children[-1].stderr,
        )
    )
    context, host = c.state["context"], c.state["host_channel"]
    assert context.finished and context.scientific_finished and context.step_authorized
    assert not context.consumed and context.channel.position == 5
    assert context.channel.closed and host.closed
    assert context.channel.read_fd is context.channel.write_fd is context.channel.error_fd is None
    assert host.read_fd is host.write_fd is host.error_fd is None
    assert c.state["publication"].store.resolver._closed
    assert c.state["publication"].store.resolver._fds == []
    actual = c.action_results[-1]["result"]
    run_id = actual["run_id"]
    assert actual["decision"] == "PASS" and actual["binding"] is True
    assert actual["lifecycle"] == "FINALIZED_PASS" and actual["custody_errors"] == []
    sdk = helper.executor.load_existing_state_readonly(c.state["store"], run_id)
    assert helper.executor.phase_of(sdk) is helper.executor.Phase.FINALIZED_PASS
    assert (
        _v2_verify_custody_without_normalizing(c.state["store"], run_id) == []
        and c.state["local_seal_checked"]
    )
    assert c.science.backend.accepted == 50 and all(t.released for t in c.science.tokens)
    pending = c.state["actual_terminal_abort_boundary"]
    current = c.boot.load(invocation)
    assert current._bytes == pending["b2_raw"] and c.boot.checkpoint() == pending["checkpoint"]
    assert c.h.checkpoint == pending["checkpoint_state"]
    assert _v2_private_tree_bytes(c.h.home) == pending["physical_home"]
    value = current.to_dict()
    assert value["official_run_id"] == run_id and value["state"]["known_phase"] == "RECONCILING"
    assert value["state"]["uncertain"] and value["state"]["blocked"]
    assert value["state"]["next_allowed_actions"] == ["inspect"]
    assert len(value["in_flight"]) == 1 and value["in_flight"][0]["action"] == "finalize"
    assert not c.factory._lock.locked() and c.state["completion"] == []
    assert c.transport.calls == pending["rpc"]
    assert (
        c.transport.calls.count("B3_CAS") == 1 and c.transport.calls.count("RETIRE_TERMINAL") == 0
    )
    op = c.boot.apparatus["ex10_operational_context"]
    snapshot = c.boot.read_authority(c.h.authority.descriptor_ref)
    assert op.history.find_run(snapshot, run_id)["lifecycle_state"] == "OPEN"
    # Retained worker and terminal SDK bytes are observations, never retry authority.
    for action, expected in [
        ("finalize", "native-intent-state-blocked"),
        ("status", "native-status-run-required"),
    ]:
        from dataclasses import replace

        retry = replace(
            c.c.invocation(action, confirmed=action == "finalize"), native_bootstrap=c.boot
        )
        effects, rpc, commands = (
            list(c.state["effects"]),
            list(c.transport.calls),
            list(c.c.state["commands"]),
        )
        with pytest.raises(c.mod.LauncherError, match="^" + expected + "$") as failure:
            c.mod.execute(retry)
        assert not isinstance(failure.value, c.mod.NativeActionFailure)
        assert (
            c.boot.load(retry)._bytes == pending["b2_raw"]
            and c.boot.checkpoint() == pending["checkpoint"]
        )
        assert _v2_private_tree_bytes(c.h.home) == pending["physical_home"]
        assert (
            c.state["effects"] == effects
            and c.transport.calls == rpc
            and c.c.state["commands"] == commands
        )
        assert len(c.children) == len(c.action_results) == 4 and c.state["completion"] == []

    _native_assert_retained_intent_actions_denied(
        c,
        capsys,
        [("finalize", "native-intent-state-blocked"), ("status", "native-status-run-required")],
    )


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_no_action_ever_chains_another(native_factory_execution_chain_case):
    # Preserve all five original actions; STATUS is one approved backend read.
    c = native_factory_execution_chain_case
    invocation = c.invocation
    for index, target in enumerate(("start-official", "execute-official", "reconcile", "finalize")):
        if index:
            invocation = c.advance(target)
        workers = len(c.children)
        actions = len(c.action_results)
        outcome = c.mod.execute(invocation)
        for thread in c.threads:
            thread.join(timeout=5)
        assert outcome["launcher_exit"] == 0 and not c.errors
        assert len(c.children) == workers + 1 and len(c.action_results) == actions + 1
        assert (
            c.action_results[-1]["action"]
            == {
                "start-official": "start",
                "execute-official": "execute",
                "reconcile": "reconcile",
                "finalize": "finalize",
            }[target]
        )
        assert c.action_results[-1]["steps"] == (9 if index == 0 else 5)
        assert c.state["completion"] == [False, True]
        assert all(child.poll() == 0 for child in c.children)
        assert all(not thread.is_alive() for thread in c.threads)
    reader = _v2_native_chain_status_reader(c)
    before = _v2_private_tree_bytes(c.h.home)
    checkpoint = c.h.checkpoint["value"]
    workers, actions, rpc, effects = (
        tuple(c.children),
        tuple(c.action_results),
        list(c.transport.calls),
        list(c.state["effects"]),
    )
    status = c.mod.execute(reader.invocation())
    assert status["launcher_exit"] == 0 and status["classification"] == "COHERENT"
    assert reader.state["commands"] == [reader.argv]
    assert reader.state["sdk_outputs"][0]["finalized"] is True
    assert reader.state["sdk_outputs"][0]["custody_errors"] == []
    assert tuple(c.children) == workers and tuple(c.action_results) == actions
    assert c.transport.calls == rpc and c.state["effects"] == effects
    assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint["value"] == checkpoint
    assert [x["action"] for x in c.action_results] == ["start", "execute", "reconcile", "finalize"]
    assert len({x["cid"] for x in c.action_results}) == 4
    assert len({x["exchange"] for x in c.action_results}) == 4
    assert c.science.backend.accepted == 50 and all(token.released for token in c.science.tokens)
    assert c.transport.calls.count("RESERVE") == c.transport.calls.count("COMMIT_OPEN") == 1
    assert (
        c.transport.calls.count("B3_CAS") == 2 and c.transport.calls.count("RETIRE_TERMINAL") == 1
    )


def test_there_is_no_option_to_choose_the_decision(checkout):
    # (74)
    assert all(a not in official.ACTIONS for a in ("force-pass", "force-fail", "retry"))
    with pytest.raises(base.UsageError):
        official.parse_args(["finalize", "--expected-git-sha", checkout.head, "--session-root",
                             "/x", "--confirm-finalization", "--decision", "PASS"])  # fmt: skip


# --- STATUS e INSPECT (76–83) -------------------------------------------------------------------


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_status_uses_the_bound_run_and_changes_nothing(native_factory_execution_chain_case):
    # Original bound STATUS and read-metadata oracle, actual START and SDK read.
    c = native_factory_execution_chain_case
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0
    for thread in c.threads:
        thread.join(timeout=5)
    assert all(not t.is_alive() for t in c.threads)
    assert all(child.poll() == 0 for child in c.children) and not c.errors
    reader = _v2_native_chain_status_reader(c)
    run = c.action_results[0]["result"]["run_id"]
    before = _v2_private_tree_bytes(c.h.home)
    b2 = (c.h.root / c.mod.SESSION_FILE).read_bytes()
    checkpoint = c.h.checkpoint["value"]
    children, actions, results = tuple(c.children), tuple(c.action_results), tuple(c.results)
    effects, rpc, completion = (
        list(c.state["effects"]),
        list(c.transport.calls),
        list(c.state["completion"]),
    )
    summary = c.mod.execute(reader.invocation())
    assert summary["launcher_exit"] == 0 and summary["classification"] == "COHERENT"
    assert summary["action"] == "status" and summary["official_run_id"] == run
    assert summary["problems"] == [] and summary["output_dir"] is None
    assert reader.state["commands"] == [reader.argv]
    assert reader.state["sdk_outputs"][0]["run_id"] == run
    assert reader.state["sdk_outputs"][0]["phase"] == "PREPARED"
    assert reader.state["sdk_outputs"][0]["custody_errors"] == []
    assert _v2_private_tree_bytes(c.h.home) == before
    assert (c.h.root / c.mod.SESSION_FILE).read_bytes() == b2
    assert c.h.checkpoint["value"] == checkpoint

    metadata = Path(c.state["store"].root) / "runs" / run / "metadata"
    old_stat = metadata.stat()
    changed = []

    def change_metadata_after_actual_read():
        assert not changed
        os.utime(metadata, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 1_000_000_000))
        changed.append(metadata.stat().st_mtime_ns)

    reader.state["after_read"] = change_metadata_after_actual_read
    with pytest.raises(c.mod.NativeActionFailure) as denied:
        c.mod.execute(reader.invocation())
    assert denied.value.code == "evidence-changed-by-read"
    assert changed == [old_stat.st_mtime_ns + 1_000_000_000]
    assert reader.state["commands"] == [reader.argv, reader.argv]
    assert reader.state["sdk_outputs"][1] == reader.state["sdk_outputs"][0]
    after = _v2_private_tree_bytes(c.h.home)
    assert set(before) == set(after)
    modified = {name for name in before if before[name] != after[name]}
    assert modified == {str(metadata.relative_to(c.h.home))}
    assert (c.h.root / c.mod.SESSION_FILE).read_bytes() == b2
    assert c.h.checkpoint["value"] == checkpoint
    assert (
        tuple(c.children) == children
        and tuple(c.action_results) == actions
        and tuple(c.results) == results
    )
    assert c.state["effects"] == effects and c.transport.calls == rpc
    assert c.state["completion"] == completion == [False, True]
    assert c.science.backend.accepted == 0 and not c.errors
    assert not reader.factory._lock.locked()


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
def test_status_without_a_bound_run_is_not_allowed(native_factory_resource_case, capsys):
    """Native unbound STATUS preserves validation3 and its existing native cause."""
    c = native_factory_resource_case
    invocation = c.invocation("status")
    initial = c.boot.load(invocation)
    assert initial.to_dict()["official_run_id"] is None
    assert "status" not in initial.to_dict()["state"]["next_allowed_actions"]
    before = _v2_private_tree_bytes(c.h.root)
    checkpoint = dict(c.h.checkpoint)
    commands, effects, approvals = (
        list(c.state["commands"]),
        list(c.state["effects"]),
        list(c.approvals),
    )
    attempts = list(c.factory._attempted)
    with pytest.raises(c.mod.LauncherError, match="^native-status-run-required$") as failure:
        c.mod.execute(invocation)
    assert not isinstance(failure.value, c.mod.NativeActionFailure)
    assert len(c.factory._attempted) == len(attempts) + 1
    assert c.factory._attempted[-1] is invocation and not c.factory._lock.locked()
    assert _v2_private_tree_bytes(c.h.root) == before and c.h.checkpoint == checkpoint
    assert c.state["commands"] == commands and c.state["effects"] == effects
    assert c.approvals == approvals
    capsys.readouterr()
    saved_handlers = {
        number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)
    }
    try:
        code = c.mod.main(
            [
                "status",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in saved_handlers.items():
            signal.signal(number, handler)
    output = capsys.readouterr()
    assert code == 3 and output.out == ""
    assert output.err == "S11_EX10_OFFICIAL_LAUNCHER_CONFIG: native-status-run-required\n"
    assert len(c.factory._attempted) == len(attempts) + 2 and not c.factory._lock.locked()
    assert c.factory._attempted[-1].action == "status"
    assert c.factory._attempted[-1].native_bootstrap is c.boot
    assert (invocation.session_root / c.mod.SESSION_FILE).read_bytes() == initial._bytes
    assert _v2_private_tree_bytes(c.h.root) == before and c.h.checkpoint == checkpoint
    assert c.state["commands"] == commands and c.state["effects"] == effects
    assert c.approvals == approvals


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_a_run_session_mismatch_blocks_for_good(
    native_factory_execution_chain_case, monkeypatch, capsys
):
    """Actual RECONCILING B2 vs faulted STATUS; v2 denies without persisting a block.

    ORIGINAL_V1_PERSISTED_STATUS_BLOCK_NOT_COVERED: PhaseB441 STATUS is read only.
    The SDK result is observed first; only its returned phase is corrupted.
    """
    import copy

    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_execution_chain_case
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0
    execute_invocation = c.advance("execute-official")
    assert c.mod.execute(execute_invocation)["launcher_exit"] == 0 and not c.errors
    for thread in c.threads:
        thread.join(timeout=5)
    reader = _v2_native_chain_status_reader(c)
    original_status = helper.executor.run_status
    authentic = []

    def read_then_fault_phase(store, run):
        payload = original_status(store, run)
        assert payload["phase"] == "RECONCILING" and payload["custody_errors"] == []
        authentic.append(copy.deepcopy(payload))
        faulted = copy.deepcopy(payload)
        faulted["phase"] = "PREPARED"
        return faulted

    monkeypatch.setattr(helper.executor, "run_status", read_then_fault_phase)
    invocation = reader.invocation()
    initial = reader.boot.load(invocation)
    assert initial.to_dict()["state"]["known_phase"] == "RECONCILING"
    assert not initial.to_dict()["state"]["blocked"] and not initial.to_dict()["in_flight"]
    before = _v2_private_tree_bytes(c.h.home)
    checkpoint = dict(c.h.checkpoint)
    effects, rpc, completion = (
        list(c.state["effects"]),
        list(c.transport.calls),
        list(c.state["completion"]),
    )
    children, actions = tuple(c.children), tuple(c.action_results)
    with pytest.raises(c.mod.NativeActionFailure, match="^session-run-mismatch$"):
        c.mod.execute(invocation)
    capsys.readouterr()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        code = c.mod.main(
            [
                "status",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
            ],
            environ=invocation.environ,
            native_bootstrap=reader.boot,
        )
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    captured = capsys.readouterr()
    assert code == c.mod.EXIT_LAUNCHER_FAILURE == 4 and captured.out == ""
    assert captured.err == "S11_EX10_OFFICIAL_LAUNCHER_FAILURE: session-run-mismatch\n"
    assert len(authentic) == 2 and authentic[0] == authentic[1]
    assert reader.state["commands"] == [reader.argv, reader.argv]
    for observed in reader.state["sdk_outputs"]:
        assert observed == dict(authentic[0], phase="PREPARED")
    assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint
    assert reader.boot.load(invocation)._bytes == initial._bytes
    assert c.state["effects"] == effects and c.transport.calls == rpc
    assert c.state["completion"] == completion == [False, True]
    assert tuple(c.children) == children and tuple(c.action_results) == actions
    assert c.science.backend.accepted == 50 and not c.errors and not reader.factory._lock.locked()


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_inspect_is_host_only_read_only_and_derives_the_next_action(
    native_factory_execution_chain_case,
):
    from dataclasses import replace

    c = native_factory_execution_chain_case
    started = c.mod.execute(c.invocation)
    assert started["launcher_exit"] == 0 and started["classification"] == "COHERENT"
    assert not c.errors and c.state["completion"] == [False, True]
    assert len(c.action_results) == 1 and c.action_results[0]["steps"] == 9
    assert c.action_results[0]["action"] == "start" and c.science.backend.accepted == 0
    for thread in c.threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in c.threads)
    assert all(child.poll() == 0 for child in c.children)
    invocation = replace(c.c.invocation("inspect"), native_bootstrap=c.boot)
    session_path = invocation.session_root / c.mod.SESSION_FILE
    before_session = session_path.read_bytes()
    checkpoint = dict(c.h.checkpoint)
    # Owned fixture tree has no links. Reading may change atime; this snapshot
    # deliberately proves bytes/mode/inode/mtime/ctime and the root's metadata.
    assert not any(path.is_symlink() for path in (c.h.home, *c.h.home.rglob("*")))
    before_tree = _v2_private_tree_bytes(c.h.home)
    attempts = list(c.factory._attempted)
    children, results, action_results = list(c.children), list(c.results), list(c.action_results)
    effects, phases, calls = list(c.state["effects"]), list(c.phases), list(c.transport.calls)
    run_id = c.action_results[0]["result"]["run_id"]
    summary = c.mod.execute(invocation)
    assert summary["classification"] == "COHERENT" and summary["launcher_exit"] == 0
    assert summary["action"] == "inspect" and summary["output_dir"] is None
    assert summary["problems"] == [] and summary["official_run_id"] == run_id
    assert summary["state"] == {
        "known_phase": "PREPARED",
        "uncertain": False,
        "blocked": False,
        "next_allowed_actions": [
            "abandon-official-pre-t0",
            "execute-official",
            "inspect",
            "status",
        ],
    }
    assert session_path.read_bytes() == before_session and c.h.checkpoint == checkpoint
    assert _v2_private_tree_bytes(c.h.home) == before_tree
    assert c.factory._attempted == attempts and not c.factory._lock.locked()
    assert c.children == children and c.results == results and c.action_results == action_results
    assert c.state["effects"] == effects and c.phases == phases and c.transport.calls == calls
    assert c.state["completion"] == [False, True] and not c.errors
    assert c.science.backend.accepted == 0 and all(child.poll() == 0 for child in c.children)


def test_unknown_lifecycle_states_are_failures(native_factory_resource_case, capsys):
    """Explicit STATUS component fault; this is not an authentic SDK output."""
    c = native_factory_resource_case
    c.seed_status()
    c.state["status"]["phase"] = "WEIRD"
    problems, phase, _ = c.mod.action_problems(
        "status", c.state["status"], run_id=RUN_ID, reconciled=None
    )
    assert problems == ["status:phase"] and phase == "WEIRD"
    invocation = c.invocation("status")
    before = _v2_private_tree_bytes(c.h.home)
    checkpoint = dict(c.h.checkpoint)
    initial = c.boot.load(invocation)
    with pytest.raises(c.mod.NativeActionFailure, match="^native-domain-output-invalid$"):
        c.mod.execute(invocation)
    capsys.readouterr()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        code = c.mod.main(
            [
                "status",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    captured = capsys.readouterr()
    assert code == c.mod.EXIT_LAUNCHER_FAILURE == 4 and captured.out == ""
    assert captured.err == "S11_EX10_OFFICIAL_LAUNCHER_FAILURE: native-domain-output-invalid\n"
    assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint
    assert c.boot.load(invocation)._bytes == initial._bytes
    assert len([a for a in c.state["commands"] if a[0] == "exec"]) == 2
    assert c.state["effects"] == [] and not c.factory._lock.locked()


# --- Limpieza --------------------------------------------------------------------------------


def test_cleanup_stack_is_down_only_without_volumes_images_or_prune(native_factory_resource_case):
    """Qualified exact native cleanup; ORIGINAL_V1_COMPOSE_DOWN_FOOTPRINT_NOT_COVERED."""
    c = native_factory_resource_case
    ledger = c.h.authority.database.read_bytes()
    coordination_path = Path(c.resource["sidecar_store"].root) / c.resource["sidecar_name"]
    before_coordination = coordination_path.read_bytes()
    before_profile = c.h.profile_path.read_bytes()
    result = c.mod.execute(c.invocation("cleanup-stack", confirmed=True))
    assert result["launcher_exit"] == 0 and result["classification"] == "COHERENT"
    assert c.state["effects"] == [
        ("stop", "--time", "10", c.plan["container_id"]),
        ("rm", c.plan["container_id"]),
        ("network", "rm", c.network["id"]),
    ]
    assert not {"-v", "--volumes", "--rmi", "prune", "local", "down", "--remove-orphans"} & {
        token for command in c.state["commands"] for token in command
    }
    assert not c.state["containers"] and not c.state["networks"]
    completed = c.boot.load(c.invocation("inspect"))
    assert completed.to_dict()["history"][-1]["payload"]["event"] == "complete-cleanup-stack"
    assert completed.to_dict()["state"]["next_allowed_actions"] == ["inspect"]
    assert completed.to_dict()["in_flight"] == []
    assert c.h.checkpoint["value"] == (completed._expected_tip, completed._expected_count)
    assert c.h.authority.database.read_bytes() == ledger
    assert coordination_path.read_bytes() == before_coordination
    assert c.h.profile_path.read_bytes() == before_profile
    assert set(p.name for p in c.h.root.iterdir()) == {c.mod.SESSION_FILE}
    assert c.approvals.count("completion") == 2 and not c.factory._lock.locked()


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_cleanup_stack_is_not_allowed_with_an_open_run(native_factory_execution_chain_case, capsys):
    """Actual START OPEN binding denies before any cleanup provider/effect, public3."""
    from dataclasses import replace

    c = native_factory_execution_chain_case
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    for thread in c.threads:
        thread.join(timeout=5)
    assert all(child.poll() == 0 for child in c.children) and all(
        not t.is_alive() for t in c.threads
    )
    invocation = replace(c.c.invocation("cleanup-stack", confirmed=True), native_bootstrap=c.boot)
    initial = c.boot.load(invocation)
    state = initial.to_dict()["state"]
    assert (
        state["known_phase"] == "PREPARED" and "cleanup-stack" not in state["next_allowed_actions"]
    )
    assert not state["uncertain"] and not state["blocked"] and not initial.to_dict()["in_flight"]
    before = _v2_private_tree_bytes(c.h.home)
    checkpoint = dict(c.h.checkpoint)
    # Delegated resource metadata commands; cleanup provider itself is forbidden.
    # The exact plain validation cause proves that provider was never entered.
    commands = list(c.c.state["commands"])
    effects, rpc, completion = (
        list(c.state["effects"]),
        list(c.transport.calls),
        list(c.state["completion"]),
    )
    children, actions = tuple(c.children), tuple(c.action_results)
    with pytest.raises(c.mod.LauncherError, match="^native-intent-state-blocked$") as denied:
        c.mod.execute(invocation)
    assert not isinstance(denied.value, c.mod.NativeActionFailure)
    capsys.readouterr()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        code = c.mod.main(
            [
                "cleanup-stack",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
                c.mod.ACTIONS["cleanup-stack"].confirmation,
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    captured = capsys.readouterr()
    assert code == c.mod.EXIT_USAGE == 3 and captured.out == ""
    assert captured.err == "S11_EX10_OFFICIAL_LAUNCHER_CONFIG: native-intent-state-blocked\n"
    assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint
    assert c.boot.load(invocation)._bytes == initial._bytes
    assert c.c.state["commands"] == commands and c.state["effects"] == effects
    assert c.transport.calls == rpc and c.state["completion"] == completion == [False, True]
    assert tuple(c.children) == children and tuple(c.action_results) == actions
    assert c.science.backend.accepted == 0 and not c.errors and not c.factory._lock.locked()
    op = c.boot.apparatus["ex10_operational_context"]
    snapshot = c.boot.read_authority(c.h.authority.descriptor_ref)
    assert (
        op.history.find_run(snapshot, initial.to_dict()["official_run_id"])["lifecycle_state"]
        == "OPEN"
    )


# --- Seguridad (84–100) ---------------------------------------------------------------------------


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_secrets_never_reach_argv_outputs_or_the_session_record(
    native_factory_execution_chain_case, monkeypatch, capsys
):
    """Actual SDK chain; original v1 environment allowlist is explicitly excluded.

    Native runners and owned relay Popen are observed and delegated unchanged.
    Authorized private custody/key files are not public/session action outputs.
    """
    import base64
    import subprocess

    c = native_factory_execution_chain_case
    key = c.factory.custody_key
    values = [key.hex(), base64.b64encode(key).decode("ascii")]
    try:
        values.append(key.decode("utf-8"))
    except UnicodeDecodeError:
        pass
    runner = c.factory.runner
    original_init, original_run = runner.__init__, runner.run
    original_popen_init = subprocess.Popen.__init__
    commands, environments, clients = [], [], []

    def observe_init(instance, cwd, environment):
        if "S11_EX10_NATIVE_BOOTSTRAP" in environment:
            # Creation receives exactly this approved plan's service/bootstrap
            # environment, not the resource observer's minimal host environment.
            plan = c.state["plan"]
            expected = dict(plan["service_environment"])
            expected.update(
                PATH="/usr/local/bin:/usr/bin:/bin",
                PYTHONDONTWRITEBYTECODE="1",
                PYTHONPATH="/s11-full-source/backend",
                S11_EX10_NATIVE_BOOTSTRAP=plan["bootstrap_bytes"].decode("utf-8"),
                DOCKER_HOST="unix:///var/run/docker.sock",
                LC_ALL="C",
                HOME="/nonexistent",
            )
        else:
            expected = {
                "PATH": "/usr/bin:/bin",
                "LC_ALL": "C",
                "HOME": "/nonexistent",
                "DOCKER_HOST": "unix:///var/run/docker.sock",
            }
        environments.append((dict(environment), expected))
        return original_init(instance, cwd, environment)

    def observe_run(instance, argv, *, timeout):
        commands.append(tuple(argv))
        return original_run(instance, argv, timeout=timeout)

    def observe_owned_relay(child, argv, *args, **options):
        original_popen_init(child, argv, *args, **options)
        if list(argv[:3]) == [sys.executable, "-B", "-c"] and len(options.get("pass_fds", ())) == 3:
            clients.append((child.pid, tuple(argv), dict(options["env"])))

    monkeypatch.setattr(runner, "__init__", observe_init)
    monkeypatch.setattr(runner, "run", observe_run)
    # Preserve the exact Popen class checked by NativeWorkerAttachment.
    monkeypatch.setattr(subprocess.Popen, "__init__", observe_owned_relay)
    outcomes = [c.mod.execute(c.invocation)]
    for target in ("execute-official", "reconcile", "finalize"):
        outcomes.append(c.mod.execute(c.advance(target)))
    assert all(outcome["launcher_exit"] == 0 for outcome in outcomes) and not c.errors
    assert [x["action"] for x in c.action_results] == ["start", "execute", "reconcile", "finalize"]
    assert [x["steps"] for x in c.action_results] == [9, 5, 5, 5]
    assert {pid for pid, _, _ in clients} == {child.pid for child in c.children}
    assert len(clients) == len(c.children) == 4 and commands and environments
    assert all(child.poll() == 0 for child in c.children)
    assert all(environment == approved for environment, approved in environments)
    assert all(
        environment
        == {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"}
        for _, _, environment in clients
    )
    observed = [str(token) for command in commands for token in command]
    observed += [str(token) for _, argv, _ in clients for token in argv]
    observed += [value for environment, _ in environments for value in environment.values()]
    observed += [value for _, _, environment in clients for value in environment.values()]
    observed += [c.mod.base.canonical_bytes(outcome).decode() for outcome in outcomes]
    observed += [
        (c.h.root / relative).read_bytes().decode("utf-8", "replace")
        for relative, (raw, *_) in _v2_private_tree_bytes(c.h.root).items()
        if raw is not None
    ]
    captured = capsys.readouterr()
    observed += [captured.out, captured.err]
    assert not any(secret in text for secret in values for text in observed if len(secret) >= 8)
    final = c.boot.load(c.invocation)
    assert final.to_dict()["history"][-1]["payload"]["event"] == "complete-finalize"
    assert not final.to_dict()["in_flight"]
    assert c.h.checkpoint["value"] == (final._expected_tip, final._expected_count)
    assert c.science.backend.accepted == 50 and c.transport.calls.count("B3_CAS") == 2
    assert c.transport.calls.count("RETIRE_TERMINAL") == 1
    assert not c.factory._lock.locked()


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
def test_sensitive_stderr_is_redacted(native_factory_resource_case, monkeypatch, capsys, line):
    """Native STATUS component discards stderr; retained sanitizer marker separately.

    No authentic SDK output or v1 persisted status.stderr footprint is claimed.
    """
    c = native_factory_resource_case
    c.seed_status()
    runner = c.factory.runner
    original_run = runner.run
    observed = []

    def sensitive_completed(instance, argv, *, timeout):
        completed = original_run(instance, argv, timeout=timeout)
        if tuple(argv[:2]) == ("docker", "exec"):
            assert completed.returncode == 0
            observed.append(completed.stdout)
            return c.mod.Completed(completed.returncode, completed.stdout, line + b"\n")
        return completed

    monkeypatch.setattr(runner, "run", sensitive_completed)
    before = _v2_private_tree_bytes(c.h.home)
    checkpoint = dict(c.h.checkpoint)
    invocation = c.invocation("status")
    capsys.readouterr()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        code = c.mod.main(
            [
                "status",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    captured = capsys.readouterr()
    assert code == 0 and captured.err == "" and line.decode() not in captured.out
    summary = json.loads(captured.out)
    assert summary["classification"] == "COHERENT" and summary["output_dir"] is None
    assert observed == [c.mod.base.canonical_bytes(c.state["status"])]
    assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint
    assert not c.state["effects"] and not c.factory._lock.locked()
    # Retained pure component only; v2 created no stderr file or action history.
    sanitized, facts, refusals = c.mod.sanitize_stderr(line + b"\n", ())
    assert sanitized == "[redacted-line]\n" and line.decode() not in sanitized
    assert facts["redacted"] == 1 and facts["redacted_secret_lines"] == 0 and refusals == []


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_a_secret_in_the_command_output_is_a_failure_and_is_not_persisted(
    native_factory_execution_chain_case, monkeypatch, capsys
):
    """Actual SDK STATUS then stdout-only fault; opaque keys, escapes and duplicate keys."""
    import base64
    import copy

    c = native_factory_execution_chain_case
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    for thread in c.threads:
        thread.join(timeout=5)
    reader = _v2_native_chain_status_reader(c)
    before = _v2_private_tree_bytes(c.h.home)
    checkpoint = dict(c.h.checkpoint)
    effects, rpc = list(c.state["effects"]), list(c.transport.calls)
    initial = reader.boot.load(reader.invocation())
    assert c.mod.execute(reader.invocation())["launcher_exit"] == 0
    key = reader.factory.custody_key
    values = [("hex", key.hex()), ("base64", base64.b64encode(key).decode("ascii"))]
    try:
        values.append(("utf8", key.decode("utf-8")))
    except UnicodeDecodeError:
        pass
    assert not c.mod.base.secret_hits(values[0][1])
    variants = [(name + "-plain", value, False, False, False) for name, value in values]
    variants += [(name + "-escaped", value, True, False, False) for name, value in values]
    variants += [
        ("pattern-escaped", "Authorization: Bearer LOCAL-SYNTHETIC", True, False, False),
        ("duplicate-root-escaped", values[0][1], True, True, False),
        ("duplicate-nested-escaped", values[0][1], True, True, True),
    ]
    variant = {}
    faults = []
    runner = reader.factory.runner
    original_run = runner.run

    def fault_only_sdk_stdout(instance, argv, *, timeout):
        completed = original_run(instance, argv, timeout=timeout)
        if tuple(argv[:2]) != ("docker", "exec"):
            return completed
        authentic = json.loads(completed.stdout)
        assert authentic == reader.state["sdk_outputs"][-1]
        assert authentic["phase"] == "PREPARED" and authentic["custody_errors"] == []
        name, value, escaped, duplicate, nested = variant["value"]
        encoded_value = json.dumps(value, ensure_ascii=False)
        if escaped:
            # JSON encoding of the entire string, including ASCII, not a fake parser result.
            encoded_value = (
                '"'
                + "".join(
                    f"\\u{ord(ch):04x}"
                    if ord(ch) <= 0xFFFF
                    else json.dumps(ch, ensure_ascii=True)[1:-1]
                    for ch in value
                )
                + '"'
            )
        payload = copy.deepcopy(authentic)
        payload["artifacts"] = [value]
        assert (
            c.mod.action_problems("status", payload, run_id=authentic["run_id"], reconciled=None)[0]
            == []
        )
        raw = c.mod.base.canonical_bytes(payload).decode()
        literal = json.dumps(value, ensure_ascii=False)
        assert raw.count(literal) == 1
        raw = raw.replace(literal, encoded_value)
        if duplicate:
            if nested:
                artifacts = "[" + encoded_value + "]"
                assert raw.count(artifacts) == 1
                raw = raw.replace(artifacts, '[{"hidden":' + encoded_value + ',"hidden":""}]')
            else:
                raw = (
                    raw.rstrip()[:-1] + ',"artifacts":' + json.dumps(authentic["artifacts"]) + "}\n"
                )
        faults.append(name)
        return c.mod.Completed(completed.returncode, raw.encode(), completed.stderr)

    monkeypatch.setattr(runner, "run", fault_only_sdk_stdout)
    for item in variants:
        variant["value"] = item
        expected = "native-domain-output-invalid" if item[3] else "native-domain-output-secret"
        invocation = reader.invocation()
        with pytest.raises(c.mod.NativeActionFailure, match="^" + expected + "$"):
            c.mod.execute(invocation)
        capsys.readouterr()
        handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
        try:
            code = c.mod.main(
                [
                    "status",
                    "--expected-git-sha",
                    invocation.expected_git_sha,
                    "--session-root",
                    str(invocation.session_root),
                ],
                environ=invocation.environ,
                native_bootstrap=reader.boot,
            )
        finally:
            for number, handler in handlers.items():
                signal.signal(number, handler)
        captured = capsys.readouterr()
        assert code == c.mod.EXIT_LAUNCHER_FAILURE == 4 and captured.out == ""
        assert captured.err == "S11_EX10_OFFICIAL_LAUNCHER_FAILURE: " + expected + "\n"
        assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint
        assert reader.boot.load(invocation)._bytes == initial._bytes
        assert c.state["effects"] == effects and c.transport.calls == rpc
        assert not reader.factory._lock.locked() and not c.errors
    monkeypatch.setattr(runner, "run", original_run)
    assert c.mod.execute(reader.invocation())["launcher_exit"] == 0
    assert faults == [name for name, *_ in variants for _ in range(2)]
    assert len(reader.state["sdk_outputs"]) == 2 + len(variants) * 2
    assert all(output == reader.state["sdk_outputs"][0] for output in reader.state["sdk_outputs"])
    assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint
    assert len(c.action_results) == len(c.children) == 1 and c.children[0].poll() == 0
    assert c.science.backend.accepted == 0


def test_project_and_container_names_are_official_and_exact(v2_oneoff_control):
    """Qualified native component identities; original v1 -p/static name prefix excluded.

    This fixture seeds B2 intent and dummy bridge shape; it does not qualify SDK START.
    """
    c = v2_oneoff_control
    control = c.control
    assert control.profile.to_dict()["project"] == c.host.profile["project"]
    assert control.labels["com.docker.compose.project"] == c.host.profile["project"]
    assert (
        control.labels["s11.owner.session"]
        == c.host.invocation()
        .native_bootstrap.load(c.host.invocation("inspect"))
        .header["session_id"]
    )
    control.create()
    control.start()
    assert control.container_id == c.container_id and control.started
    create = next(argv for kind, argv in c.events if kind == "create")
    assert create[create.index("--name") + 1] == control.name
    labels = [create[i + 1] for i, token in enumerate(create) if token == "--label"]
    assert "com.docker.compose.project=" + c.host.profile["project"] in labels
    assert [argv for kind, argv in c.events if kind == "start"] == [
        ("/usr/bin/docker", "container", "start", c.container_id)
    ]


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


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_the_official_lifecycle_through_the_real_executor(
    native_factory_execution_chain_case,
):
    """Actual SDK PASS lifecycle and same-authority second START refusal2.

    The eligible second B2 initializes before terminal: v2 initialize after
    first-valid is correctly blocked. STATUS is genuinely readonly in v2,
    so no legacy mutable STATUS history event is asserted here.
    Approved deployment/PID1 and science harness remain synthetic.
    """
    from dataclasses import replace

    c = native_factory_execution_chain_case
    h, mod, original = c.h, c.mod, c.boot
    second_root = h.home / "independent-eligible-session"
    checkpoint = {"value": None, "publishes": 0}
    providers_called = []
    expected_approval = Path(json.loads(original.approval_reference_bytes)["path"]).read_bytes()

    def approve_second(**facts):
        assert h.approvals["allowed"]
        assert facts["profile"].to_dict() == h.profile
        assert facts["session_root"] == str(second_root)
        assert facts["source_revision"] == original.expected_git_sha
        assert facts["guard_identity"] == original.global_identity
        assert facts["authority_revision"]["authority_id"] == h.authority.descriptor["authority_id"]
        assert dict(facts["references"])["runtime_profile_approval_reference"] == expected_approval

    def read_checkpoint():
        return checkpoint["value"]

    def publish_checkpoint(expected, replacement):
        assert checkpoint["value"] == expected
        checkpoint["value"] = replacement
        checkpoint["publishes"] += 1

    def forbidden(**facts):
        providers_called.append(facts)
        raise AssertionError("binding refusal must precede every second-session provider")

    def forbidden_process(*args, **kwargs):
        raise AssertionError("binding refusal must not construct Docker or a worker process")

    factory = mod.NativeActionFactory(
        providers={name: forbidden for name in mod.NativeActionFactory._PROVIDERS},
        signer=c.factory.signer,
        custody_hmac_key=c.factory.custody_key,
        runner_factory=forbidden_process,
        process_factory=forbidden_process,
    )
    context = json.loads(original.session_context_bytes)
    context["parent"] = str(second_root)
    context["prior_session_roots"] = [*context["prior_session_roots"], str(h.root)]
    paths = json.loads(original.path_context_bytes)
    paths["prior_session_roots"] = [*paths["prior_session_roots"], str(h.root)]
    configuration = {
        name: getattr(original, name)
        for name in (
            "repo_root",
            "expected_git_sha",
            "profile_path",
            "global_store",
            "global_name",
            "global_identity",
            "read_authority",
        )
    }
    second = mod.NativeLauncherBootstrap(
        **configuration,
        path_context=paths,
        approval_reference=json.loads(original.approval_reference_bytes),
        session_context=context,
        approve=approve_second,
        read_checkpoint=read_checkpoint,
        publish_checkpoint=publish_checkpoint,
        action_factory=factory,
    )
    initial_invocation = replace(
        c.invocation,
        action="initialize-session",
        nonce=None,
        preflight_report=None,
        acknowledged=(),
        confirmed=False,
        session_root=second_root,
        native_bootstrap=second,
    )
    initialized = mod.execute(initial_invocation)
    assert initialized["launcher_exit"] == 0
    initial = second.load(initial_invocation)
    assert initial.to_dict()["official_run_id"] is None
    assert "start-official" in initial.to_dict()["state"]["next_allowed_actions"]
    assert checkpoint["publishes"] == 1 and not providers_called

    first = mod.execute(c.invocation)
    assert first["launcher_exit"] == 0 and not c.errors
    assert c.action_results[0]["steps"] == 9
    for thread in c.threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in c.threads)
    reader = _v2_native_chain_status_reader(c)
    before_read, checkpoint_read = _v2_private_tree_bytes(h.home), dict(h.checkpoint)
    effects_read, rpc_read = list(c.state["effects"]), list(c.transport.calls)
    children_read, actions_read = list(c.children), list(c.action_results)
    read = mod.execute(reader.invocation())
    assert read["launcher_exit"] == 0 and read["state"]["known_phase"] == "PREPARED"
    assert reader.state["commands"] == [reader.argv] and len(reader.state["sdk_outputs"]) == 1
    assert reader.state["sdk_outputs"][0]["run_id"] == first["official_run_id"]
    assert reader.state["sdk_outputs"][0]["custody_errors"] == []
    assert _v2_private_tree_bytes(h.home) == before_read and h.checkpoint == checkpoint_read
    assert c.state["effects"] == effects_read and c.transport.calls == rpc_read
    assert c.children == children_read and c.action_results == actions_read
    assert c.science.backend.accepted == 0
    for action in ("execute-official", "reconcile", "finalize"):
        invocation = c.advance(action)
        outcome = mod.execute(invocation)
        assert outcome["launcher_exit"] == 0 and not c.errors
        assert c.action_results[-1]["steps"] == 5
        assert c.state["completion"] == [False, True]
    terminal = c.action_results[-1]["result"]
    run_id = terminal["run_id"]
    assert terminal["decision"] == "PASS" and terminal["binding"] is True
    assert terminal["lifecycle"] == "FINALIZED_PASS" and terminal["custody_errors"] == []
    assert [item["action"] for item in c.action_results] == [
        "start",
        "execute",
        "reconcile",
        "finalize",
    ]
    assert [item["steps"] for item in c.action_results] == [9, 5, 5, 5]
    assert len(c.children) == 4
    final = c.boot.load(invocation)
    assert final.to_dict()["official_run_id"] == first["official_run_id"] == run_id
    assert final.to_dict()["state"]["known_phase"] == "FINALIZED_PASS"
    assert not final.to_dict()["in_flight"]
    assert [item["payload"]["event"] for item in final.to_dict()["history"]] == [
        "initialize-session",
        "intent-start-official",
        "complete-start-official",
        "intent-execute-official",
        "complete-execute-official",
        "intent-reconcile",
        "complete-reconcile",
        "intent-finalize",
        "complete-finalize",
    ]
    assert c.state["local_seal_checked"] and c.state["actual_emission_proof_admitted"]
    assert c.science.backend.accepted == 50 and all(token.released for token in c.science.tokens)
    assert c.state["store"].custody_hmac_key == factory.custody_key
    op = second.apparatus["ex10_operational_context"]
    snapshot = second.read_authority(h.authority.descriptor_ref)
    row = op.history.find_run(snapshot, run_id)
    assert row["lifecycle_state"] == "TERMINAL" and row["binding"] is True
    assert row["result"] == terminal["decision"]
    assert op.history.first_valid_status(snapshot)["status"] == terminal["decision"] + "_BINDING"
    assert c.transport.calls.count("RESERVE") == c.transport.calls.count("COMMIT_OPEN") == 1
    assert c.transport.calls.count("B3_CAS") == 2
    assert c.transport.calls.count("RETIRE_TERMINAL") == 1
    assert all(child.poll() == 0 for child in c.children)

    # ACK only the genuine historical ND carried by the unchanged fixture.
    acknowledged = tuple(
        run["run_id"]
        for run in op.history.list_official_runs(snapshot)
        if run["lifecycle_state"] == "TERMINAL" and run["result"] == "NOT_DEMONSTRATED"
    )
    assert run_id not in acknowledged
    attempt = replace(
        c.invocation, session_root=second_root, native_bootstrap=second, acknowledged=acknowledged
    )
    before = _v2_private_tree_bytes(h.home)
    checkpoint_before = dict(checkpoint)
    transport_before = list(c.transport.calls)
    actual = mod.execute(attempt)
    assert actual["classification"] == "REFUSED" and actual["launcher_exit"] == 2
    assert actual["problems"] == ["binding-run-exists:" + run_id]
    assert actual["output_dir"] is None and not providers_called
    assert _v2_private_tree_bytes(h.home) == before
    assert checkpoint == checkpoint_before and c.transport.calls == transport_before
    last = second.load(attempt)
    assert last._bytes == initial._bytes and last.to_dict()["official_run_id"] is None
    assert not last.to_dict()["in_flight"]
    assert "start-official" in last.to_dict()["state"]["next_allowed_actions"]


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_an_official_abandon_before_t0_through_the_real_executor(
    native_factory_execution_chain_case,
):
    """Actual native START9/abandon5, SDK preT0 ND and B2/B3/B4 terminal closure."""
    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_execution_chain_case
    started = c.mod.execute(c.invocation)
    assert started["launcher_exit"] == 0 and started["classification"] == "COHERENT"
    assert not c.errors and c.action_results[0]["steps"] == 9
    run_id = started["official_run_id"]
    invocation = c.advance("abandon-official-pre-t0")
    closed = c.mod.execute(invocation)
    assert closed["launcher_exit"] == 0 and closed["classification"] == "COHERENT"
    assert closed["state"]["known_phase"] == "ABANDONED_PRE_T0"
    assert [item["action"] for item in c.action_results] == ["start", "finalize"]
    assert [item["steps"] for item in c.action_results] == [9, 5]
    assert len(c.children) == 2 and all(child.poll() == 0 for child in c.children)
    result = c.results[-1]
    assert result["run_id"] == run_id and result["lifecycle"] == "ABANDONED_PRE_T0"
    assert result["decision"] == "NOT_DEMONSTRATED" and result["binding"] is False
    assert result["custody_errors"] == []
    assert c.science.backend.accepted == 0 and not c.errors
    assert c.state["local_seal_checked"] and c.state["completion"] == [False, True]
    assert c.state["store"].verify(run_id) == []
    state = helper.executor.load_existing_state_readonly(c.state["store"], run_id)
    assert helper.executor.phase_of(state) is helper.executor.Phase.ABANDONED_PRE_T0
    assert state.finalized and not state.has(helper.executor.ART_ARMED)
    assert not state.has(helper.executor.ART_RAW)
    assert c.transport.calls.count("RESERVE") == c.transport.calls.count("COMMIT_OPEN") == 1
    assert c.transport.calls.count("B3_CAS") == 2
    assert c.transport.calls.count("RETIRE_TERMINAL") == 1
    final = c.boot.load(invocation)
    assert final.header == c.c.initial.header
    assert final.to_dict()["official_run_id"] == run_id and not final.to_dict()["in_flight"]
    assert final.to_dict()["history"][-1]["payload"]["event"] == "complete-abandon-official-pre-t0"
    op = c.boot.apparatus["ex10_operational_context"]
    snapshot = c.boot.read_authority(c.h.authority.descriptor_ref)
    row = op.history.find_run(snapshot, run_id)
    assert row["lifecycle_state"] == "TERMINAL"
    assert row["result"] == "NOT_DEMONSTRATED" and row["binding"] is False


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
@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_a_dead_executor_in_started_or_emitting_can_be_declared_gone(
    native_factory_recovery_precondition_case, phase, left
):
    """Real SDK death/ND under the installed exact prior-instance verifier.

    The deployment observations and closed component B2 projection are explicit
    synthetic boundaries. Legacy compose-run mounts/meta and interrupted Factory
    intent recovery are not covered by this native route.
    """
    c = native_factory_recovery_precondition_case
    prior = _native_exact_prior_observer_precondition(c, phase=phase, left=left)
    _native_install_action_observer(c, prior.approve)
    _assert_native_exact_prior_declaration_completed(c, prior)
    assert len(prior.observations) == 1
    observation = prior.observations[0]
    assert observation["query_id"] == prior.previous["component_process"].container_id
    assert observation["namespace_complete"] is True
    assert observation["state"] == (left or "absent")
    assert observation["matched_ids"] == ([] if left is None else [observation["query_id"]])


@pytest.mark.parametrize("state", sorted(official.ACTIVE_CONTAINER_STATES))
@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_a_still_active_executor_container_is_refused(
    native_factory_recovery_precondition_case, capsys, state
):
    """Exact retained CID active: validation3, no new intent/worker effects.

    Real SDK START and child137 use an explicit closed B2 component precondition;
    the independent exact deployment observer remains synthetic, not Docker/PID1.
    """
    from dataclasses import replace

    c = native_factory_recovery_precondition_case
    invocation, approve, observations = _native_known_active_original_precondition(c, state)
    _native_install_action_observer(c, approve)
    before = _native_known_active_snapshot(c)
    capsys.readouterr()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        code = c.mod.main(
            [
                "declare-executor-gone",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
                c.mod.ACTIONS["declare-executor-gone"].confirmation,
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    captured = capsys.readouterr()
    assert observations and observations[-1]["State"]["Status"] == state
    assert code == 3 and captured.out == ""
    assert captured.err == "S11_EX10_OFFICIAL_LAUNCHER_CONFIG: executor-still-active\n"
    _assert_native_known_active_unchanged(c, before)
    # An independent invocation preserves the same negative API code. This is
    # not a retry of an intent: neither admission wrote nor launched anything.
    denied = replace(invocation)
    with pytest.raises(c.mod.LauncherError, match="^executor-still-active$") as failure:
        c.mod.execute(denied)
    assert not isinstance(failure.value, c.mod.NativeActionFailure)
    assert len(observations) == 2
    _assert_native_known_active_unchanged(c, before)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_a_container_with_a_similar_name_is_not_the_executor(
    native_factory_recovery_precondition_case,
):
    """Keep original foreign similar-name/exited case at the installed observer seam."""
    c = native_factory_recovery_precondition_case
    prior = _native_exact_prior_observer_precondition(
        c, phase="STARTED", left=None, foreign="similar-name-exited"
    )
    _native_install_action_observer(c, prior.approve)
    _assert_native_exact_prior_declaration_completed(c, prior)
    assert prior.foreign["Name"] == prior.previous_name + "-x"
    assert prior.foreign["Id"] != prior.previous["component_process"].container_id
    assert prior.observations == [
        {
            "query_id": prior.previous["component_process"].container_id,
            "namespace_complete": True,
            "matched_ids": [],
            "state": "absent",
        }
    ]
    assert json.loads(prior.path.read_bytes()) == [prior.foreign]


@pytest.mark.parametrize("phase", ["PREPARED", "DRAINING", "RECONCILING", "STOPPED_PRE_T0"])
@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_declare_executor_gone_is_refused_outside_started_or_emitting(
    request,
    monkeypatch,
    capsys,
    phase,
    v2_native_host,
):
    from apps.experiments_app.tests import test_ex10_executor as helper

    c = request.getfixturevalue(
        "native_factory_recovery_precondition_case"
        if phase == "DRAINING"
        else "native_factory_execution_chain_case"
    )
    started = c.mod.execute(c.invocation)
    assert started["launcher_exit"] == 0 and not c.errors
    run_id = c.action_results[0]["result"]["run_id"]
    if phase == "DRAINING":
        # Authentic SDK SINGLE5/RAW/exit137; its closed B2 projection is the
        # explicit component fixture, never resolution of an aborted Factory intent.
        condition = c.prepare_precondition("drain")
        assert condition["phase"] == phase and condition["actual_exit137"]
        assert condition["host_single5_completed"]
    elif phase != "PREPARED":
        invocation = c.advance("execute-official")
        if phase == "STOPPED_PRE_T0":
            original_persist = helper.executor._persist
            interrupted = []

            def interrupt_after_armed(store, actual_run, artifact_type, payload, **kwargs):
                record = original_persist(store, actual_run, artifact_type, payload, **kwargs)
                if artifact_type == helper.executor.ART_ARMED:
                    assert not interrupted and store is c.state["store"] and actual_run == run_id
                    assert payload["requests_prepared"] == helper.ex10.TOTAL_REQUESTS
                    interrupted.append(record)
                    raise KeyboardInterrupt
                return record

            monkeypatch.setattr(helper.executor, "_persist", interrupt_after_armed)
        outcome = c.mod.execute(invocation)
        assert outcome["launcher_exit"] == 0 and not c.errors
        assert c.action_results[-1]["result"]["phase"] == phase
        if phase == "STOPPED_PRE_T0":
            assert len(interrupted) == 1 and c.science.backend.accepted == 0
    actual = helper.executor.load_existing_state_readonly(c.state["store"], run_id)
    assert str(helper.executor.phase_of(actual)) == phase
    current = c.boot.load(_v2_native_gone_negative_invocation(c)).to_dict()
    assert current["official_run_id"] == run_id and current["in_flight"] == []
    assert current["state"]["known_phase"] == phase
    assert not current["state"]["uncertain"] and not current["state"]["blocked"]
    assert "declare-executor-gone" not in current["state"]["next_allowed_actions"]
    _v2_native_assert_gone_blocked_without_effects(c, capsys)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_declare_executor_gone_needs_an_executed_bound_run(
    native_factory_execution_chain_case,
    capsys,
):
    c = native_factory_execution_chain_case
    current = c.boot.load(_v2_native_gone_negative_invocation(c)).to_dict()
    assert current["official_run_id"] is None and current["in_flight"] == []
    _v2_native_assert_gone_blocked_without_effects(c, capsys)
    started = c.mod.execute(c.invocation)
    assert started["launcher_exit"] == 0 and not c.errors
    assert [item["action"] for item in c.action_results] == ["start"]
    current = c.boot.load(_v2_native_gone_negative_invocation(c)).to_dict()
    assert current["official_run_id"] == c.action_results[0]["result"]["run_id"]
    assert current["state"]["known_phase"] == "PREPARED" and current["in_flight"] == []
    _v2_native_assert_gone_blocked_without_effects(c, capsys)


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


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_declare_executor_gone_refuses_another_head_or_a_dirty_tree(
    native_factory_recovery_precondition_case,
):
    c = native_factory_recovery_precondition_case
    started = c.mod.execute(c.invocation)
    assert started["launcher_exit"] == 0 and not c.errors
    condition = c.prepare_precondition("declare-executor-gone")
    # Actual dead SDK component and explicit closed B2 fixture projection only;
    # this test never attempts DECLARE or repairs an interrupted Factory intent.
    assert condition["phase"] == "STARTED" and condition["actual_exit137"]
    invocation = _v2_native_gone_negative_invocation(c)
    current = c.boot.load(invocation).to_dict()
    assert "declare-executor-gone" in current["state"]["next_allowed_actions"]
    before, checkpoint = _v2_private_tree_bytes(c.h.home), dict(c.h.checkpoint)
    attempts, rpc = list(c.factory._attempted), list(c.transport.calls)
    effects, commands = list(c.state["effects"]), list(c.c.state["commands"])
    children, results, actions = list(c.children), list(c.results), list(c.action_results)
    repo, expected = invocation.repo_root, invocation.expected_git_sha
    ref = _v2_source_git(repo, "symbolic-ref", "HEAD")
    tree = _v2_source_git(repo, "rev-parse", "HEAD^{tree}")
    changed = None
    try:
        _v2_source_git(repo, "commit", "-q", "--allow-empty", "-m", "LOCAL TEST DECLARE HEAD fault")
        changed = _v2_source_git(repo, "rev-parse", "HEAD")
        assert changed != expected and _v2_source_git(repo, "rev-parse", "HEAD^{tree}") == tree
        with pytest.raises(c.mod.LauncherError, match="^head-mismatch$"):
            c.mod.execute(invocation)
        assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint
    finally:
        if changed is not None:
            _v2_source_git(repo, "update-ref", ref, expected, changed)
    assert _v2_source_git(repo, "rev-parse", "HEAD") == expected
    dirty = repo / "infra/docker-compose.yml"
    original = dirty.read_bytes()
    try:
        dirty.write_bytes(original + b"\n# LOCAL TEST DECLARE dirty tree\n")
        with pytest.raises(c.mod.LauncherError, match="^working-tree-dirty$"):
            c.mod.execute(invocation)
        assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint
    finally:
        dirty.write_bytes(original)
    assert c.factory._attempted == attempts and not c.factory._lock.locked()
    assert c.transport.calls == rpc and c.state["effects"] == effects
    assert c.c.state["commands"] == commands
    assert c.children == children and c.results == results and c.action_results == actions
    assert invocation.expected_git_sha == c.boot.expected_git_sha == expected


@pytest.mark.parametrize("what", ["history", "execute-output"])
def test_declare_executor_gone_refuses_a_tampered_session(
    native_factory_recorded_output_case, what
):
    # Registered native output component, not an SDK EXEC recovery trace.
    # Validation of actual modified B2 bytes/registered output precedes admission.
    x = native_factory_recorded_output_case
    c = x.c
    invocation = c.invocation("declare-executor-gone", confirmed=True)
    assert type(c.factory) is c.mod.NativeActionFactory
    assert c.mod.execute(c.invocation("inspect"))["launcher_exit"] == 0
    path = c.h.root / c.mod.SESSION_FILE
    if what == "history":
        data = json.loads(path.read_bytes())
        data["history"][-1]["payload"]["event"] = "complete-reconcile"
        path.write_bytes(c.mod.base.canonical_bytes(data))
        expected = "native-session-history"
    else:
        x.output.write_bytes(b"LOCAL TEST changed registered DECLARE output\n")
        expected = "native-action-output-changed"
    before, checkpoint = _v2_private_tree_bytes(c.h.home), dict(c.h.checkpoint)
    attempts, approvals = list(c.factory._attempted), list(c.approvals)
    commands, effects = list(c.state["commands"]), list(c.state["effects"])
    with pytest.raises(c.mod.SessionTampered, match="^" + expected + "$"):
        c.mod.execute(invocation)
    assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint
    assert c.factory._attempted == attempts and not c.factory._lock.locked()
    assert (
        c.approvals == approvals
        and c.state["commands"] == commands
        and c.state["effects"] == effects
    )


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
@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_an_incoherent_declare_output_does_not_advance_the_session(
    native_factory_recovery_precondition_case,
    monkeypatch,
    payload,
    problem,
):
    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_recovery_precondition_case
    started = c.mod.execute(c.invocation)
    assert started["launcher_exit"] == 0 and not c.errors
    previous = c.prepare_precondition("declare-executor-gone", phase="EMITTING")
    assert previous["actual_exit137"] and previous["host_single5_completed"]
    assert previous["actual_emission_proof_admitted_in_child"]
    invocation = c.advance("declare-executor-gone")
    executor = helper.executor
    original_persist = executor.ExecutorOperationalContext.persist_public_result
    pending = {}
    mutations = []

    def persist_then_corrupt_private_result(context, store, result):
        value = original_persist(context, store, result)
        assert context is c.state["context"] and context.action == "finalize"
        assert context.finished and context.scientific_finished and context.step_authorized
        assert context.consumed is False
        assert context.channel.position == 5 and result["lifecycle"] == "NOT_DEMONSTRATED"
        run_id = c.action_results[0]["result"]["run_id"]
        assert result["run_id"] == run_id == context.proposal["run_id"]
        result_path = store.root / ("executor-result." + context.channel.exchange_id + ".json")
        assert result_path.read_bytes() == executor.canonical_json_bytes(result)
        checkpoint = c.boot.checkpoint()
        codec = c.boot.apparatus["ex10_session_v2"]
        current = codec.load_official_session(
            invocation.session_root / c.mod.SESSION_FILE,
            context=c.boot.context(),
            profile=c.h.profile,
            expected_tip=checkpoint[0],
            expected_count=checkpoint[1],
        )
        pending.update(raw=current._bytes, value=current.to_dict(), checkpoint=checkpoint)
        assert pending["value"]["official_run_id"] == run_id
        assert pending["value"]["state"]["known_phase"] == "EMITTING"
        assert pending["value"]["in_flight"][0]["action"] == "declare-executor-gone"
        altered = dict(result)
        # Keep actual SDK payload and mutate only the original case's delta.
        changes = {
            key: value
            for key, value in payload.items()
            if key not in GONE_OK or GONE_OK[key] != value
        }
        assert changes and problem in {
            "run-id-differs",
            "executor-gone:lifecycle",
            "executor-gone:binding",
            "executor-gone:reasons",
            "payload-keys",
        }
        altered.update(changes)
        result_path.write_bytes(executor.canonical_json_bytes(altered))
        assert not mutations
        mutations.append(altered)
        return value

    monkeypatch.setattr(
        executor.ExecutorOperationalContext,
        "persist_public_result",
        persist_then_corrupt_private_result,
    )
    with pytest.raises(c.mod.NativeActionFailure) as failure:
        c.mod.execute(invocation)
    assert failure.value.code == "native-worker-result-invalid"
    assert len(mutations) == 1 and not c.errors and len(c.action_results) == 2
    problems, _, _ = c.mod.action_problems(
        "declare-executor-gone",
        mutations[0],
        run_id=pending["value"]["official_run_id"],
        reconciled=None,
    )
    assert problems == [problem]
    assert (invocation.session_root / c.mod.SESSION_FILE).read_bytes() == pending["raw"]
    assert c.boot.checkpoint() == pending["checkpoint"]
    assert pending["value"]["state"]["uncertain"] and pending["value"]["state"]["blocked"]
    assert pending["value"]["state"]["next_allowed_actions"] == ["inspect"]
    assert c.state["completion"] == [] and c.state["steps"] == 5
    assert [item["action"] for item in c.action_results] == ["start", "finalize"]
    assert c.state["effects"] == ["create", "start", "create", "start"]
    assert previous["actual_emission_proof_admitted_in_child"] and c.science.backend.accepted == 0
    assert all(token.released for token in c.science.tokens)
    assert c.transport.calls.count("RESERVE") == c.transport.calls.count("COMMIT_OPEN") == 1
    assert c.transport.calls.count("B3_CAS") == 1
    assert c.transport.calls.count("RETIRE_TERMINAL") == 0
    snapshot = c.boot.read_authority(c.h.authority.descriptor_ref)
    op = c.boot.apparatus["ex10_operational_context"]
    assert (
        op.history.find_run(snapshot, pending["value"]["official_run_id"])["lifecycle_state"]
        == "OPEN"
    )
    assert all(child.poll() == 0 for child in c.children)
    from dataclasses import replace

    for thread in c.threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in c.threads)
    before_tree = _v2_private_tree_bytes(c.h.home)
    before_checkpoint = dict(c.h.checkpoint)
    before_effects, before_calls = list(c.state["effects"]), list(c.transport.calls)
    before_children, before_results = list(c.children), list(c.action_results)
    before_attempts = len(c.factory._attempted)
    for index, (action, cause) in enumerate(
        (
            ("declare-executor-gone", "native-intent-state-blocked"),
            ("status", "native-status-run-required"),
        ),
        start=1,
    ):
        denied = replace(c.c.invocation(action, confirmed=True), native_bootstrap=c.boot)
        with pytest.raises(c.mod.LauncherError) as failure:
            c.mod.execute(denied)
        assert not isinstance(failure.value, c.mod.NativeActionFailure)
        assert failure.value.code == cause
        assert len(c.factory._attempted) == before_attempts + index
        assert c.factory._attempted[-1] is denied and not c.factory._lock.locked()
        assert _v2_private_tree_bytes(c.h.home) == before_tree
        assert c.h.checkpoint == before_checkpoint
        assert c.state["effects"] == before_effects and c.transport.calls == before_calls
        assert c.children == before_children and c.action_results == before_results
        assert c.state["completion"] == [] and c.science.backend.accepted == 0
        assert not c.errors and all(child.poll() == 0 for child in c.children)


def _v2_native_prepare_original_scientific_refusal(c, *, seed_local_prior=False):
    """Real SDK component premise; never clear/project a Factory intent."""
    retained_local_prior = None
    if seed_local_prior:
        from apps.experiments_app.tests import test_ex10_executor as helper

        executor = helper.executor
        evidence = Path(c.h.profile["host_roots"]["evidence"]["path"])
        store = helper.EvidenceStore(
            evidence,
            custody_hmac_key=c.factory.custody_key,
            publication_verification_key=c.factory.signer.public_key().public_bytes_raw(),
            rolling_checksums=False,
        )
        probes = executor.ExecutorProbes(
            preflight=lambda options: helper.preflight_report(
                git_sha=c.boot.expected_git_sha,
                checked_at=c.c.now["utc"],
            ),
            dataset=helper.ex10.load_dataset,
            provider=lambda: [],
            sources=lambda root: [],
            fingerprint=executor.executor_fingerprint,
            now=lambda: c.c.now["utc"],
        )
        local = executor.start_run(
            store,
            git_sha=c.boot.expected_git_sha,
            source_root=c.boot.repo_root,
            nonce="native-refusal-prior-integrity",
            evidence_mode=helper.LOCAL,
            timezone="UTC",
            probes=probes,
        )
        executor.finalize_run(
            store,
            run_id=local["run_id"],
            git_sha=c.boot.expected_git_sha,
            abandon_reason="synthetic-pre-t0-stop",
            probes=probes,
        )
        prior = executor.prior_ex10_runs(store)
        assert len(prior) == 1 and prior[0].finalized and not prior[0].problems
        assert prior[0].run_id == local["run_id"] and prior[0].evidence_mode == helper.LOCAL
        assert _v2_verify_custody_without_normalizing(store, local["run_id"]) == []
        prior_root = store.root / "runs" / local["run_id"]
        retained_local_prior = {
            "run_id": local["run_id"],
            "root": prior_root,
            "tree": _v2_private_tree_bytes(prior_root),
        }
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    previous = c.prepare_precondition("declare-executor-gone")
    assert previous["actual_exit137"] and previous["host_single5_completed"]
    assert previous["phase"] == "STARTED" and not previous["child"].is_alive()
    sealed = c.prepare_refusal_seal()
    if retained_local_prior is not None:
        assert retained_local_prior["run_id"] != sealed["run_id"]
        assert _v2_private_tree_bytes(retained_local_prior["root"]) == retained_local_prior["tree"]
        assert (
            _v2_verify_custody_without_normalizing(c.store(), retained_local_prior["run_id"]) == []
        )
        sealed["retained_local_prior"] = retained_local_prior
    assert sealed["actual_sdk_sealed"] and sealed["host_single5_completed"]
    assert sealed["child"].exitcode == 0 and not sealed["child"].is_alive()
    assert c.boot.load(c.invocation)._bytes == sealed["pending_before"]
    assert c.boot.checkpoint() == sealed["checkpoint_before"]
    value = c.boot.load(c.invocation).to_dict()
    assert value["in_flight"] == [] and value["state"]["known_phase"] == "STARTED"
    assert "declare-executor-gone" in value["state"]["next_allowed_actions"]
    assert not value["state"]["uncertain"] and not value["state"]["blocked"]

    invocation = c.advance("declare-executor-gone")
    c.state["allow_original_scientific_refusal"] = True
    return invocation, sealed


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_a_refused_declare_keeps_its_exit_code(native_factory_recovery_precondition_case, capsys):
    """Real SDK sealed-run refusal after SINGLE5; exact pending is retained.

    Mechanical installed fixture only: actual SDK custody and host protocol,
    synthetic Docker/PID1 boundary. No interrupted Factory intent recovery or
    literal legacy v1 footprint equivalence is asserted by component setup.
    """
    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_recovery_precondition_case
    invocation, sealed = _v2_native_prepare_original_scientific_refusal(c)
    attempts = len(c.factory._attempted)
    capsys.readouterr()
    handlers = {n: signal.getsignal(n) for n in (signal.SIGTERM, signal.SIGHUP)}
    try:
        code = c.mod.main(
            [
                "declare-executor-gone",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
                c.mod.ACTIONS["declare-executor-gone"].confirmation,
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    output = capsys.readouterr()
    assert code == c.mod.EXIT_TERMINAL == 2 and output.err == ""
    summary = json.loads(output.out)
    assert summary["classification"] == "REFUSED" and summary["launcher_exit"] == 2
    assert len(c.factory._attempted) == attempts + 1
    for thread in c.threads:
        thread.join(timeout=5)
    assert not c.errors and all(not t.is_alive() for t in c.threads)
    assert all(child.poll() == 0 for child in c.children)
    assert not c.factory._lock.locked() and c.state["completion"] == []
    assert len(c.children) == 2 and len(c.action_results) == len(c.results) == 1
    assert c.action_results[0]["steps"] == 9
    refusal = c.state["actual_scientific_refusal"]
    assert type(refusal["error"]) is helper.executor.Ex10Refusal
    assert refusal["error"].terminal and refusal["error"].reasons == ("run-finalized",)
    assert json.loads(refusal["raw"]) == {
        "refused": True,
        "terminal": True,
        "reasons": ["run-finalized"],
    }
    assert refusal["private"].read_bytes() == refusal["raw"]
    assert c.state["scientific_exit_code"] == 2 and c.state["steps"] == 5
    context, channel = c.state["context"], c.state["host_channel"]
    assert context.finished and context.step_authorized
    assert not context.scientific_finished and not context.terminal_prepared
    assert context.channel.closed and context.channel.position == 5
    assert context.channel.read_fd is context.channel.write_fd is context.channel.error_fd is None
    assert channel.closed and channel.position == 5
    assert channel.read_fd is channel.write_fd is channel.error_fd is None
    assert c.state["publication"].store.resolver._closed
    assert c.state["publication"].store.resolver._fds == []
    assert c.state["effects"] == ["create", "start", "create", "start"]
    pending = c.boot.load(invocation)
    assert pending._bytes == refusal["pending"]
    assert (
        c.boot.checkpoint() == refusal["checkpoint"]
        and c.h.checkpoint == refusal["checkpoint_state"]
    )
    assert c.h.authority.database.read_bytes() == refusal["ledger"] == sealed["ledger_before"]
    assert (
        (Path(c.c.resource["sidecar_store"].root) / c.c.resource["sidecar_name"]).read_bytes()
        == refusal["sidecar"]
        == sealed["sidecar_before"]
    )
    assert _v2_private_tree_bytes(c.h.home) == refusal["home"]
    value = pending.to_dict()
    assert value["official_run_id"] == sealed["run_id"]
    assert value["state"]["known_phase"] == "STARTED"
    assert value["state"]["uncertain"] and value["state"]["blocked"]
    assert value["state"]["next_allowed_actions"] == ["inspect"]
    assert len(value["in_flight"]) == 1
    assert value["in_flight"][0]["action"] == "declare-executor-gone"
    assert value["history"] == json.loads(refusal["pending"])["history"]
    assert c.transport.calls == refusal["rpc"]
    assert c.transport.calls.count("B3_CAS") == 1
    assert c.transport.calls.count("RETIRE_TERMINAL") == 0
    existing = helper.executor.load_existing_state_readonly(c.store(), sealed["run_id"])
    assert (
        existing.finalized
        and _v2_verify_custody_without_normalizing(c.store(), sealed["run_id"]) == []
    )
    assert _v2_private_tree_bytes(existing.run_root) == sealed["sealed_run"]
    op = c.boot.apparatus["ex10_operational_context"]
    assert (
        op.history.find_run(c.boot.read_authority(c.h.authority.descriptor_ref), sealed["run_id"])[
            "lifecycle_state"
        ]
        == "OPEN"
    )
    assert c.science.backend.accepted == 0
    # API and CLI deny a second DECLARE without new science/worker/terminal IO.
    _v2_native_assert_gone_blocked_without_effects(c, capsys)


@pytest.mark.parametrize(
    ("fault", "v2_native_host"),
    [
        pytest.param(
            "admission-result-same-bytes-replaced",
            "typed-b4-historic-run",
            id="admission-result-same-bytes-replaced",
        ),
        pytest.param(
            "last-checkpoint-installation",
            "typed-b4-historic-run",
            id="last-checkpoint-installation",
        ),
        pytest.param(
            "last-checkpoint-ready-reference",
            "typed-b4-historic-run",
            id="last-checkpoint-ready-reference",
        ),
        pytest.param(
            "last-checkpoint-published-envelope",
            "typed-b4-historic-run",
            id="last-checkpoint-published-envelope",
        ),
        pytest.param(
            "last-checkpoint-prior-file", "typed-b4-historic-run", id="last-checkpoint-prior-file"
        ),
        pytest.param(
            "post-lock-prepared-provider", "typed-b4-historic-run", id="post-lock-prepared-provider"
        ),
        pytest.param(
            "post-lock-prepared-process", "typed-b4-historic-run", id="post-lock-prepared-process"
        ),
        pytest.param(
            "post-lock-ready-reference", "typed-b4-historic-run", id="post-lock-ready-reference"
        ),
        pytest.param(
            "post-lock-original-host-deadline",
            "typed-b4-historic-run",
            id="post-lock-original-host-deadline",
        ),
        pytest.param(
            "client-close-pending-mirror", "typed-b4-historic-run", id="client-close-pending-mirror"
        ),
        pytest.param(
            "post-lock-deadline-mirror", "typed-b4-historic-run", id="post-lock-deadline-mirror"
        ),
        pytest.param(
            "post-lock-result-same-bytes-replaced",
            "typed-b4-historic-run",
            id="post-lock-result-same-bytes-replaced",
        ),
    ],
    indirect=["v2_native_host"],
)
def test_v2_native_original_refusal_rejects_late_context_faults(
    native_factory_recovery_precondition_case, monkeypatch, capsys, fault
):
    """Genuine SDK negative followed by one concrete late fault; no B2 grant.

    Constructor/cleanup wrappers observe and delegate actual implementations.
    Checkpoint/approval remain the original admitted callbacks with a timed
    fault added. No validator, SDK refusal or private payload is fabricated.
    Staged RED requires the concrete reviewed predecessor and SDK512; a missing
    API/import, unreached injection or earlier unrelated failure is not causal.
    """
    from types import MappingProxyType

    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_recovery_precondition_case
    invocation, sealed = _v2_native_prepare_original_scientific_refusal(
        c,
        seed_local_prior=fault == "last-checkpoint-prior-file",
    )
    probe = {"observer": None, "released": False, "fired": []}
    actual_init = c.mod.NativeScientificRefusalObservation.__init__

    def capture_init(observation, **kwargs):
        actual_init(observation, **kwargs)
        assert probe["observer"] is None
        probe["observer"] = observation

    monkeypatch.setattr(c.mod.NativeScientificRefusalObservation, "__init__", capture_init)

    def change_file_metadata(path):
        assert path.is_file() and not path.is_symlink()
        before, raw = path.lstat(), path.read_bytes()
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1), follow_symlinks=False)
        after = path.lstat()
        assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
        assert after.st_mtime_ns == before.st_mtime_ns + 1 and path.read_bytes() == raw
        return {"path": path, "before": before, "after": after, "raw": raw}

    def replace_same_bytes(path):
        assert path.is_file() and not path.is_symlink()
        before, raw = path.lstat(), path.read_bytes()
        replacement = path.with_name(path.name + ".test-owned-replacement")
        with replacement.open("xb") as stream:
            stream.write(raw)
        replacement.chmod(0o600)
        os.replace(replacement, path)
        after = path.lstat()
        assert path.read_bytes() == raw and after.st_ino != before.st_ino
        assert stat.S_IMODE(after.st_mode) == 0o600 and after.st_nlink == 1
        return {"path": path, "before": before, "after": after, "raw": raw}

    def inject(point):
        assert not probe["fired"]
        observation = probe["observer"]
        assert observation is not None
        actual = c.state["actual_scientific_refusal"]
        assert type(actual["error"]) is helper.executor.Ex10Refusal
        assert actual["error"].terminal and actual["error"].reasons == ("run-finalized",)
        assert c.state["scientific_exit_code"] == 2
        context = c.state["context"]
        assert context.finished and context.step_authorized and not context.scientific_finished
        assert context.channel.closed and context.channel.position == 5
        session_path = c.h.root / c.mod.SESSION_FILE
        session_stamp = c.boot.apparatus["ex10_prior_custody"]._PriorFDReader._stamp(
            session_path.lstat()
        )
        record = {"point": point, "session_stamp": session_stamp}
        if fault.endswith("result-same-bytes-replaced"):
            record.update(replace_same_bytes(actual["private"]))
        elif fault == "last-checkpoint-installation":
            original = c.factory.process_factory
            c.factory.process_factory = lambda *args, **kwargs: original(*args, **kwargs)
            assert c.factory.process_factory is not original
        elif fault.endswith("ready-reference"):
            reference = c.state["start_envelope_configuration"]["ready_state"]["report_reference"]
            record.update(change_file_metadata(Path(reference["path"])))
        elif fault == "last-checkpoint-published-envelope":
            record.update(
                replace_same_bytes(Path(c.state["publication"].store.root) / "envelope.json")
            )
        elif fault == "last-checkpoint-prior-file":
            retained = sealed["retained_local_prior"]
            prior = retained["root"]
            assert prior.is_dir() and prior.name == retained["run_id"] != sealed["run_id"]
            assert _v2_private_tree_bytes(prior) == retained["tree"]
            path = next(path for path in sorted(prior.rglob("*")) if path.is_file())
            record.update(change_file_metadata(path))
            record["prior_run_id"] = retained["run_id"]
        elif fault == "post-lock-prepared-provider":
            configuration = c.state["start_envelope_configuration"]
            original = configuration["compose_observer"]
            configuration["compose_observer"] = lambda: original()
            assert configuration["compose_observer"] is not original
        elif fault == "post-lock-prepared-process":
            process = c.state["prepared"].process
            before = process.identity_digest
            replacement = ("0" if before[0] != "0" else "1") + before[1:]
            object.__setattr__(process, "identity_digest", replacement)
            assert process.identity_digest != before
        elif fault == "post-lock-original-host-deadline":
            assert probe["released"] and c.c.now["mono"] < observation.host_deadline
            c.c.now["mono"] = observation.host_deadline + 1
            assert c.c.now["mono"] > observation.host_deadline
        elif fault == "client-close-pending-mirror":
            original = observation.pending
            observation.pending = bytes(bytearray(original))
            assert observation.pending == original and observation.pending is not original
        elif fault == "post-lock-deadline-mirror":
            original = observation.host_deadline
            observation.host_deadline = original + 3600
            assert observation.host_deadline > original and c.c.now["mono"] < original
        else:
            raise AssertionError("unknown concrete refusal fault")
        assert session_path.read_bytes() == actual["pending"]
        assert (
            c.boot.apparatus["ex10_prior_custody"]._PriorFDReader._stamp(session_path.lstat())
            == session_stamp
        )
        probe["fired"].append(record)

    if fault == "admission-result-same-bytes-replaced":
        providers = dict(c.factory.providers)
        original_approval = providers["approve_creation"]

        def approve(**facts):
            accepted = original_approval(**facts)
            observation = probe["observer"]
            if observation is not None and observation.attempted and not probe["fired"]:
                assert observation.original is None
                inject("original-admission-callback")
            return accepted

        providers["approve_creation"] = approve
        c.factory.providers = MappingProxyType(providers)

    original_checkpoint = c.boot.read_checkpoint

    def checkpoint():
        value = original_checkpoint()
        if fault.startswith("last-checkpoint-") and probe["released"] and not probe["fired"]:
            # Select the actual final CP observation, after finished IO, instead
            # of injecting into initial admission or repeated retained checks.
            caller = sys._getframe(1)
            parent_frame = caller.f_back
            if (
                caller.f_code.co_name == "checkpoint_only"
                and parent_frame.f_code.co_name == "finish"
            ):
                inject("last-original-checkpoint-after-finished-io")
        return value

    if fault.startswith("last-checkpoint-"):
        object.__setattr__(c.boot, "read_checkpoint", checkpoint)

    actual_lock = c.factory._lock

    class ObservedLock:
        def acquire(self, *, blocking):
            return actual_lock.acquire(blocking=blocking)

        def locked(self):
            return actual_lock.locked()

        def release(self):
            actual_lock.release()
            assert not actual_lock.locked() and not probe["released"]
            probe["released"] = True
            if fault.startswith("post-lock-"):
                inject("actual-factory-lock-released")

    c.factory._lock = ObservedLock()
    if fault == "client-close-pending-mirror":
        actual_reap = c.mod.NativeWorkerAttachment._reap

        def reap(process):
            result = actual_reap(process)
            if probe["observer"] is not None and not probe["fired"]:
                assert process is c.children[-1] and process.poll() == 0
                assert c.factory._lock.locked() and not probe["released"]
                assert type(probe["observer"].signal) is c.mod._NativeScientificRefusal
                inject("actual-owned-client-reaped-before-handler")
            return result

        monkeypatch.setattr(c.mod.NativeWorkerAttachment, "_reap", staticmethod(reap))

    capsys.readouterr()
    handlers = {n: signal.getsignal(n) for n in (signal.SIGTERM, signal.SIGHUP)}
    try:
        code = c.mod.main(
            [
                "declare-executor-gone",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
                c.mod.ACTIONS["declare-executor-gone"].confirmation,
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    output = capsys.readouterr()
    assert output.out == "" and len(probe["fired"]) == 1 and probe["released"]
    if fault == "post-lock-original-host-deadline":
        assert code == c.mod.EXIT_APPARATUS_ABORT == 5
        assert output.err == "S11_EX10_OFFICIAL_LAUNCHER_ABORT: timeout\n"
    else:
        assert code == c.mod.EXIT_LAUNCHER_FAILURE == 4
        assert output.err.startswith("S11_EX10_OFFICIAL_LAUNCHER_FAILURE: ")
        assert "INTERNAL_ERROR" not in output.err and "LAUNCHER_CONFIG" not in output.err
    for thread in c.threads:
        thread.join(timeout=5)
    assert not c.errors and all(not t.is_alive() for t in c.threads)
    assert len(c.children) == 2 and all(child.poll() == 0 for child in c.children)
    assert len(c.results) == len(c.action_results) == 1
    assert c.state["completion"] == [] and not c.factory._lock.locked()
    assert c.state["publication"].store.resolver._closed
    assert c.state["publication"].store.resolver._fds == []
    assert all(
        stream is None or stream.closed
        for child in c.children
        for stream in (child.stdin, child.stdout, child.stderr)
    )
    actual = c.state["actual_scientific_refusal"]
    session_path = c.h.root / c.mod.SESSION_FILE
    assert session_path.read_bytes() == actual["pending"]
    assert (
        c.boot.apparatus["ex10_prior_custody"]._PriorFDReader._stamp(session_path.lstat())
        == probe["fired"][0]["session_stamp"]
    )
    assert c.h.checkpoint == actual["checkpoint_state"]
    assert original_checkpoint() == actual["checkpoint"]
    value = json.loads(actual["pending"])
    assert value["official_run_id"] == sealed["run_id"]
    assert value["state"]["known_phase"] == "STARTED"
    assert value["state"]["uncertain"] and value["state"]["blocked"]
    assert value["state"]["next_allowed_actions"] == ["inspect"]
    assert (
        len(value["in_flight"]) == 1 and value["in_flight"][0]["action"] == "declare-executor-gone"
    )
    assert c.h.authority.database.read_bytes() == actual["ledger"] == sealed["ledger_before"]
    assert (
        (Path(c.c.resource["sidecar_store"].root) / c.c.resource["sidecar_name"]).read_bytes()
        == actual["sidecar"]
        == sealed["sidecar_before"]
    )
    assert c.transport.calls == actual["rpc"]
    assert (
        c.transport.calls.count("B3_CAS") == 1 and c.transport.calls.count("RETIRE_TERMINAL") == 0
    )
    assert c.state["effects"] == ["create", "start", "create", "start"]
    assert c.science.backend.accepted == 0
    existing = helper.executor.load_existing_state_readonly(c.store(), sealed["run_id"])
    assert (
        existing.finalized
        and _v2_verify_custody_without_normalizing(c.store(), sealed["run_id"]) == []
    )
    assert _v2_private_tree_bytes(existing.run_root) == sealed["sealed_run"]

    if fault == "last-checkpoint-prior-file":
        retained = sealed["retained_local_prior"]
        record = probe["fired"][0]
        assert record["prior_run_id"] == retained["run_id"]
        mutated = str(record["path"].relative_to(retained["root"]))
        after = _v2_private_tree_bytes(retained["root"])
        assert after.keys() == retained["tree"].keys()
        for name, before in retained["tree"].items():
            if name == mutated:
                assert after[name][:3] == before[:3]
                assert after[name][3] == before[3] + 1
            else:
                assert after[name] == before


@pytest.mark.parametrize(
    ("kind", "v2_native_host"),
    [
        pytest.param("timeout", "typed-b4-historic-run", id="timeout"),
        pytest.param("interrupted", "typed-b4-historic-run", id="interrupted"),
    ],
    indirect=["v2_native_host"],
)
def test_declare_executor_gone_aborts_without_retry(
    native_factory_recovery_precondition_case, capsys, kind
):
    """Actual SDK DECLARE + live owned relay abort5; keep uncertainty, no retry.

    SDK local ND is retained; B3 retirement never occurs. This does not recover
    an interrupted Factory intent or cover the legacy v1 rm-f footprint.
    """
    import fcntl

    c = native_factory_recovery_precondition_case
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    previous = c.prepare_precondition("declare-executor-gone")
    assert previous["actual_exit137"] and previous["host_single5_completed"]
    invocation = c.advance("declare-executor-gone")
    c.state["hold_declared_relay_for_public_abort"] = kind
    capsys.readouterr()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        code = c.mod.main(
            [
                "declare-executor-gone",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
                c.mod.ACTIONS["declare-executor-gone"].confirmation,
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    captured = capsys.readouterr()
    assert code == c.mod.EXIT_APPARATUS_ABORT == 5 and captured.out == ""
    assert captured.err == "S11_EX10_OFFICIAL_LAUNCHER_ABORT: " + kind + "\n"
    assert all(signal.getsignal(number) is handler for number, handler in handlers.items())
    assert c.state["held_declared_relay_at_worker_wait"] is True
    if kind == "interrupted":
        assert c.state["actual_declared_abort_signals"] == [(os.getpid(), signal.SIGTERM)]
    else:
        assert c.state.get("actual_declared_abort_signals", []) == []
    for thread in c.threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in c.threads)
    assert not c.errors and len(c.action_results) == len(c.children) == 2
    assert c.children[0].poll() == 0 and c.children[-1].poll() is not None
    assert all(
        stream is None or stream.closed
        for stream in (
            c.children[-1].stdin,
            c.children[-1].stdout,
            c.children[-1].stderr,
        )
    )
    context = c.state["context"]
    host = c.state["host_channel"]
    assert context.finished and context.scientific_finished and context.step_authorized
    assert context.consumed is False and context.channel.position == 5
    assert (
        context.channel.closed
        and context.channel.read_fd is context.channel.write_fd is context.channel.error_fd is None
    )
    assert host.closed and host.read_fd is host.write_fd is host.error_fd is None
    assert c.state["publication"].store.resolver._closed
    assert c.state["publication"].store.resolver._fds == []
    assert c.action_results[-1]["result"]["lifecycle"] == "NOT_DEMONSTRATED"
    assert c.science.backend.accepted == 0 and all(token.released for token in c.science.tokens)
    assert not c.factory._lock.locked() and c.state["completion"] == []
    pending = c.state["actual_declared_abort_pending_boundary"]
    current = c.boot.load(invocation)
    assert current._bytes == pending["b2_raw"]
    assert (
        c.boot.checkpoint() == pending["checkpoint"]
        and c.h.checkpoint == pending["checkpoint_state"]
    )
    assert _v2_private_tree_bytes(c.h.home) == pending["physical_home"]
    value = current.to_dict()
    assert value["official_run_id"] == c.action_results[0]["result"]["run_id"]
    assert value["state"]["known_phase"] == "STARTED"
    assert value["state"]["uncertain"] and value["state"]["blocked"]
    assert value["state"]["next_allowed_actions"] == ["inspect"]
    assert (
        len(value["in_flight"]) == 1 and value["in_flight"][0]["action"] == "declare-executor-gone"
    )
    assert c.state["effects"] == ["create", "start", "create", "start"]
    assert c.state["running"] and c.state["worker_retired"] is False
    assert c.transport.calls == pending["rpc"]
    assert (
        c.transport.calls.count("B3_CAS") == 1 and c.transport.calls.count("RETIRE_TERMINAL") == 0
    )
    op = c.boot.apparatus["ex10_operational_context"]
    assert (
        op.history.find_run(
            c.boot.read_authority(c.h.authority.descriptor_ref), value["official_run_id"]
        )["lifecycle_state"]
        == "OPEN"
    )
    # SDK custody lock is actually free after client/actor cleanup. Acquiring it
    # in this test does not discharge the retained native operational intent.
    fd = os.open(
        Path(c.state["store"].root) / ".s11-custody.lock",
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    assert c.results[-1]["decision"] == "NOT_DEMONSTRATED"
    assert c.results[-1]["binding"] is False and c.results[-1]["custody_errors"] == []
    # Public/API known uncertainty guard preserves the pending attempt.
    _v2_native_assert_gone_blocked_without_effects(c, capsys)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_declare_executor_gone_runs_once_and_never_chains(
    native_factory_recovery_precondition_case, capsys
):
    """One real SDK terminal declaration; later known-state admission is plain validation3."""
    from dataclasses import replace

    c = native_factory_recovery_precondition_case
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    previous = c.prepare_precondition("declare-executor-gone", phase="EMITTING")
    assert previous["actual_exit137"] and previous["host_single5_completed"]
    assert previous["actual_emission_proof_admitted_in_child"]
    invocation = c.advance("declare-executor-gone")
    closed = c.mod.execute(invocation)
    assert closed["launcher_exit"] == 0 and closed["classification"] == "COHERENT"
    assert closed["state"]["known_phase"] == "NOT_DEMONSTRATED"
    assert [item["action"] for item in c.action_results] == ["start", "finalize"]
    assert [item["steps"] for item in c.action_results] == [9, 5]
    assert c.results[-1]["decision"] == "NOT_DEMONSTRATED"
    assert c.results[-1]["binding"] is False and c.results[-1]["custody_errors"] == []
    assert c.state["exact_previous_scientific_death_approved"]
    final = c.boot.load(invocation)
    assert final.to_dict()["in_flight"] == []
    assert final.to_dict()["history"][-1]["payload"]["event"] == "complete-declare-executor-gone"
    assert c.state["completion"] == [False, True]
    assert c.science.backend.accepted == 0 and not c.errors
    assert c.transport.calls.count("B3_CAS") == 2
    assert c.transport.calls.count("RETIRE_TERMINAL") == 1
    before, checkpoint = _v2_private_tree_bytes(c.h.home), dict(c.h.checkpoint)
    effects, rpc, commands = (
        list(c.state["effects"]),
        list(c.transport.calls),
        list(c.c.state["commands"]),
    )
    children, results, actions = list(c.children), list(c.results), list(c.action_results)
    attempts = len(c.factory._attempted)
    for index, action in enumerate(
        ("declare-executor-gone", "execute-official", "drain", "finalize"), 1
    ):
        denied_invocation = replace(c.c.invocation(action, confirmed=True), native_bootstrap=c.boot)
        with pytest.raises(c.mod.LauncherError, match="^native-intent-state-blocked$") as denied:
            c.mod.execute(denied_invocation)
        assert not isinstance(denied.value, c.mod.NativeActionFailure)
        capsys.readouterr()
        handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
        try:
            code = c.mod.main(
                [
                    action,
                    "--expected-git-sha",
                    invocation.expected_git_sha,
                    "--session-root",
                    str(invocation.session_root),
                    c.mod.ACTIONS[action].confirmation,
                ],
                environ=invocation.environ,
                native_bootstrap=c.boot,
            )
        finally:
            for number, handler in handlers.items():
                signal.signal(number, handler)
        captured = capsys.readouterr()
        assert code == 3 and captured.out == ""
        assert captured.err == "S11_EX10_OFFICIAL_LAUNCHER_CONFIG: native-intent-state-blocked\n"
        assert len(c.factory._attempted) == attempts + index * 2 and not c.factory._lock.locked()
        assert c.boot.load(denied_invocation)._bytes == final._bytes
        assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint
        assert c.state["effects"] == effects and c.transport.calls == rpc
        assert c.c.state["commands"] == commands
        assert c.children == children and c.results == results and c.action_results == actions
    assert all(child.poll() == 0 for child in c.children)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_declare_executor_gone_keeps_secrets_out_of_argv_and_outputs(
    native_factory_recovery_precondition_case, monkeypatch, capsys
):
    """Actual SDK START/DECLARE privacy; excludes original v1 mount/env-allowlist footprints.

    Native runners and owned relay Popen are observed and delegated unchanged.
    Authorized private custody/key files are not public/session action outputs.
    """
    import base64
    import subprocess

    c = native_factory_recovery_precondition_case
    key = c.factory.custody_key
    values = [key.hex(), base64.b64encode(key).decode("ascii")]
    try:
        values.append(key.decode("utf-8"))
    except UnicodeDecodeError:
        pass
    runner = c.factory.runner
    original_init, original_run = runner.__init__, runner.run
    original_popen_init = subprocess.Popen.__init__
    commands, environments, clients = [], [], []

    def observe_init(instance, cwd, environment):
        if "S11_EX10_NATIVE_BOOTSTRAP" in environment:
            # Creation receives exactly this approved plan's service/bootstrap
            # environment, not the resource observer's minimal host environment.
            plan = c.state["plan"]
            expected = dict(plan["service_environment"])
            expected.update(
                PATH="/usr/local/bin:/usr/bin:/bin",
                PYTHONDONTWRITEBYTECODE="1",
                PYTHONPATH="/s11-full-source/backend",
                S11_EX10_NATIVE_BOOTSTRAP=plan["bootstrap_bytes"].decode("utf-8"),
                DOCKER_HOST="unix:///var/run/docker.sock",
                LC_ALL="C",
                HOME="/nonexistent",
            )
        else:
            expected = {
                "PATH": "/usr/bin:/bin",
                "LC_ALL": "C",
                "HOME": "/nonexistent",
                "DOCKER_HOST": "unix:///var/run/docker.sock",
            }
        environments.append((dict(environment), expected))
        return original_init(instance, cwd, environment)

    def observe_run(instance, argv, *, timeout):
        commands.append(tuple(argv))
        return original_run(instance, argv, timeout=timeout)

    def observe_owned_relay(child, argv, *args, **options):
        original_popen_init(child, argv, *args, **options)
        if list(argv[:3]) == [sys.executable, "-B", "-c"] and len(options.get("pass_fds", ())) == 3:
            clients.append((child.pid, tuple(argv), dict(options["env"])))

    monkeypatch.setattr(runner, "__init__", observe_init)
    monkeypatch.setattr(runner, "run", observe_run)
    # Preserve the exact Popen class checked by NativeWorkerAttachment.
    monkeypatch.setattr(subprocess.Popen, "__init__", observe_owned_relay)
    outcomes = [c.mod.execute(c.invocation)]
    assert outcomes[0]["launcher_exit"] == 0 and not c.errors
    precondition = c.prepare_precondition("declare-executor-gone")
    assert precondition["actual_exit137"] and precondition["host_single5_completed"]
    outcomes.append(c.mod.execute(c.advance("declare-executor-gone")))
    assert all(outcome["launcher_exit"] == 0 for outcome in outcomes) and not c.errors
    assert [x["action"] for x in c.action_results] == ["start", "finalize"]
    assert [x["steps"] for x in c.action_results] == [9, 5]
    assert {pid for pid, _, _ in clients} == {child.pid for child in c.children}
    assert len(clients) == len(c.children) == 2 and commands and environments
    assert all(child.poll() == 0 for child in c.children)
    assert all(environment == approved for environment, approved in environments)
    assert all(
        environment
        == {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"}
        for _, _, environment in clients
    )
    observed = [str(token) for command in commands for token in command]
    observed += [str(token) for _, argv, _ in clients for token in argv]
    observed += [value for environment, _ in environments for value in environment.values()]
    observed += [value for _, _, environment in clients for value in environment.values()]
    observed += [c.mod.base.canonical_bytes(outcome).decode() for outcome in outcomes]
    observed += [
        (c.h.root / relative).read_bytes().decode("utf-8", "replace")
        for relative, (raw, *_) in _v2_private_tree_bytes(c.h.root).items()
        if raw is not None
    ]
    captured = capsys.readouterr()
    observed += [captured.out, captured.err]
    assert not any(secret in text for secret in values for text in observed if len(secret) >= 8)
    final = c.boot.load(c.invocation)
    assert final.to_dict()["history"][-1]["payload"]["event"] == "complete-declare-executor-gone"
    assert not final.to_dict()["in_flight"]
    assert c.h.checkpoint["value"] == (final._expected_tip, final._expected_count)
    assert c.science.backend.accepted == 0 and c.transport.calls.count("B3_CAS") == 2
    assert c.transport.calls.count("RETIRE_TERMINAL") == 1
    assert not c.factory._lock.locked()


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
def test_a_launcher_that_died_during_execute_leaves_an_in_flight_record(
    native_factory_execution_chain_case, capsys
):
    """Real handled SIGTERM retains the exact pre-dispatch native EXEC intent.

    MIGRATED_CORE_ONLY: actual SDK START/EXEC and public interruption, not OS
    launcher-process death. The v1 test raised in-process KeyboardInterrupt.
    STATUS-unlock-to-DECLARE is superseded by PhaseB443 and MASTER1204; this
    case proves that those actions cannot release the retained native intent.
    Worker preservation is not executor-gone or action-retry authority.
    """
    import fcntl
    from types import MappingProxyType

    kind = "interrupted"

    c = native_factory_execution_chain_case
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    invocation = c.advance("execute-official")
    c.state["hold_relay_retirement_for_public_abort"] = kind
    before_create, creation_admissions = {}, []
    original_creation = c.factory.providers["approve_creation"]

    def observe_original_intent_before_create(**facts):
        # Delegate every creation, attachment and binding approval. Keep the
        # first original snapshot; later reads cannot adopt a changed intent.
        approved = original_creation(**facts)
        assert approved is None
        checkpoint = c.boot.checkpoint()
        codec = c.boot.apparatus["ex10_session_v2"]
        pending = codec.load_official_session(
            invocation.session_root / c.mod.SESSION_FILE,
            context=c.boot.context(),
            profile=c.h.profile,
            expected_tip=checkpoint[0],
            expected_count=checkpoint[1],
        )
        value = pending.to_dict()
        (record,) = value["in_flight"]
        assert record["action"] == "execute-official"
        assert record["container_name"] == c.state["plan"]["name"]
        assert record["output_dir"] == "actions/" + c.state["cfg"]["exchange_id"]
        assert value["official_run_id"] == c.action_results[0]["result"]["run_id"]
        assert value["state"]["uncertain"] and value["state"]["blocked"]
        actions = tuple(item["action"] for item in c.action_results)
        assert 1 <= len(c.children) <= 2
        assert 1 <= len(actions) <= len(c.children)
        assert actions == ("start", "execute")[: len(actions)]
        assert c.children[0].poll() == 0
        if not before_create:
            assert len(c.children) == len(actions) == 1
            assert c.state["effects"] == ["create", "start"]
            before_create.update(raw=pending._bytes, checkpoint=checkpoint, record=dict(record))
        else:
            assert pending._bytes == before_create["raw"]
            assert checkpoint == before_create["checkpoint"]
            assert record == before_create["record"]
            if len(creation_admissions) < 3:
                assert len(c.children) == len(actions) == 1
                assert c.state["effects"] == ["create", "start", "create"]
            else:
                # The same retained intent is approved again after start for
                # attachment construction, owned pipes and worker bindings.
                assert c.state["effects"] == ["create", "start", "create", "start"]
        creation_admissions.append(tuple(c.state["effects"]))
        return approved

    providers = dict(c.factory.providers)
    providers["approve_creation"] = observe_original_intent_before_create
    c.factory.providers = MappingProxyType(providers)
    capsys.readouterr()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        code = c.mod.main(
            [
                "execute-official",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
                c.mod.ACTIONS["execute-official"].confirmation,
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    captured = capsys.readouterr()
    assert code == c.mod.EXIT_APPARATUS_ABORT == 5 and captured.out == ""
    assert captured.err == "S11_EX10_OFFICIAL_LAUNCHER_ABORT: " + kind + "\n"
    assert all(signal.getsignal(number) is handler for number, handler in handlers.items())
    assert c.state["held_relay_at_actual_worker_wait"] is True
    if kind == "interrupted":
        assert c.state["actual_public_abort_signals"] == [(os.getpid(), signal.SIGTERM)]
    else:
        assert c.state.get("actual_public_abort_signals", []) == []
    for thread in c.threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in c.threads)
    assert not c.errors and len(c.action_results) == len(c.children) == 2
    assert c.children[0].poll() == 0 and c.children[-1].poll() is not None
    assert all(
        stream is None or stream.closed
        for stream in (
            c.children[-1].stdin,
            c.children[-1].stdout,
            c.children[-1].stderr,
        )
    )
    context = c.state["context"]
    host = c.state["host_channel"]
    assert context.finished and context.scientific_finished and context.step_authorized
    assert context.consumed is False and context.channel.position == 5
    assert (
        context.channel.closed
        and context.channel.read_fd is context.channel.write_fd is context.channel.error_fd is None
    )
    assert host.closed and host.read_fd is host.write_fd is host.error_fd is None
    assert c.state["publication"].store.resolver._closed
    assert c.state["publication"].store.resolver._fds == []
    assert c.action_results[-1]["result"]["phase"] == "RECONCILING"
    assert c.science.backend.accepted == 50 and all(token.released for token in c.science.tokens)
    assert not c.factory._lock.locked() and c.state["completion"] == []
    pending = c.state["actual_public_abort_pending_boundary"]
    current = c.boot.load(invocation)
    assert current._bytes == pending["b2_raw"]
    assert (
        c.boot.checkpoint() == pending["checkpoint"]
        and c.h.checkpoint == pending["checkpoint_state"]
    )
    assert _v2_private_tree_bytes(c.h.home) == pending["physical_home"]
    value = current.to_dict()
    assert value["official_run_id"] == c.action_results[0]["result"]["run_id"]
    assert value["state"]["known_phase"] == "PREPARED"
    assert value["state"]["uncertain"] and value["state"]["blocked"]
    assert value["state"]["next_allowed_actions"] == ["inspect"]
    assert len(value["in_flight"]) == 1 and value["in_flight"][0]["action"] == "execute-official"
    assert c.state["effects"] == ["create", "start", "create", "start"]
    assert c.state["running"] and c.state["worker_retired"] is False
    assert c.transport.calls == pending["rpc"]
    assert (
        c.transport.calls.count("B3_CAS") == 1 and c.transport.calls.count("RETIRE_TERMINAL") == 0
    )
    op = c.boot.apparatus["ex10_operational_context"]
    assert (
        op.history.find_run(
            c.boot.read_authority(c.h.authority.descriptor_ref), value["official_run_id"]
        )["lifecycle_state"]
        == "OPEN"
    )
    # SDK custody lock is actually free after client/actor cleanup. Acquiring it
    # in this test does not discharge the retained native operational intent.
    fd = os.open(
        Path(c.state["store"].root) / ".s11-custody.lock",
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)

    assert creation_admissions[:3] == [
        ("create", "start"),
        ("create", "start", "create"),
        ("create", "start", "create"),
    ]
    # The attachment constructor admits once and its first _check approves
    # again. Further original approvals retain identical B2/CP and effects.
    assert len(creation_admissions) >= 5
    assert all(
        effects == ("create", "start", "create", "start") for effects in creation_admissions[3:]
    )
    assert before_create and current._bytes == before_create["raw"]
    assert c.boot.checkpoint() == before_create["checkpoint"]
    assert value["in_flight"] == [before_create["record"]]
    assert [item["action"] for item in c.action_results] == ["start", "execute"]
    assert [item["steps"] for item in c.action_results] == [9, 5]
    _native_assert_retained_intent_actions_denied(
        c,
        capsys,
        [
            ("execute-official", "native-intent-state-blocked"),
            ("status", "native-status-run-required"),
            ("declare-executor-gone", "native-intent-state-blocked"),
        ],
    )


@pytest.mark.parametrize(
    ("phase", "v2_native_host"),
    [
        pytest.param("STARTED", "typed-b4-historic-run", id="STARTED"),
        pytest.param("EMITTING", "typed-b4-historic-run", id="EMITTING"),
    ],
    indirect=["v2_native_host"],
)
def test_declare_executor_gone_through_the_real_executor(
    native_factory_recovery_precondition_case, phase
):
    """Actual SDK death137 and ND use B2 as an explicit component precondition.

    They do not claim Factory intent recovery.
    """
    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_recovery_precondition_case
    first = c.mod.execute(c.invocation)
    assert first["launcher_exit"] == 0 and not c.errors
    assert c.action_results[0]["steps"] == 9
    assert c.state["completion"] == [False, True]
    previous = c.prepare_precondition("declare-executor-gone", phase=phase)
    assert previous["actual_exit137"] and previous["host_single5_completed"]
    assert previous["child"].exitcode == 137 and not previous["child"].is_alive()
    assert previous["phase"] == phase
    if phase == "EMITTING":
        assert previous["actual_emission_proof_admitted_in_child"] is True
    invocation = c.advance("declare-executor-gone")
    before = c.boot.load(invocation)
    assert before.to_dict()["in_flight"] == []
    assert (
        before.to_dict()["history"][-1]["payload"]["event"]
        == "fixture-precondition-declare-executor-gone"
    )
    counts = len(c.children), len(c.action_results)
    closed = c.mod.execute(invocation)
    assert closed["launcher_exit"] == 0 and closed["classification"] == "COHERENT"
    assert closed["state"]["known_phase"] == "NOT_DEMONSTRATED"
    assert (len(c.children), len(c.action_results)) == (counts[0] + 1, counts[1] + 1)
    actual = c.action_results[-1]
    assert actual["action"] == "finalize" and actual["steps"] == 5
    payload = actual["result"]
    assert payload["decision"] == "NOT_DEMONSTRATED" and payload["binding"] is False
    assert payload["custody_errors"] == []
    reason = {
        "STARTED": "emission-interrupted:before-arming",
        "EMITTING": "emission-interrupted:raw-missing",
    }[phase]
    assert reason in payload["reasons"]
    run_id = previous["run_id"]
    assert closed["official_run_id"] == run_id
    assert c.store().verify(run_id) == []
    sdk_state = helper.executor.load_existing_state_readonly(c.store(), run_id)
    assert helper.executor.phase_of(sdk_state) is helper.executor.Phase.NOT_DEMONSTRATED
    assert sdk_state.finalized
    assert not c.boot.load(invocation).to_dict()["in_flight"]
    assert c.state["completion"][-1] is True and not c.errors
    assert all(child.poll() == 0 for child in c.children)


def test_v2_bare_import_does_not_execute_unselected_sibling(tmp_path):
    """Importing API data must not execute sibling bytes before source selection."""
    from types import ModuleType

    scripts = tmp_path / "repo" / "backend" / "scripts"
    scripts.mkdir(parents=True)
    marker = tmp_path / "unselected-source-executed"
    sibling = scripts / "s11_ex10_preflight_launcher.py"
    original = SCRIPT.with_name(sibling.name).read_bytes()
    # Prefix after __future__ keeps the real sibling syntactically executable.
    original = original.replace(
        b"from __future__ import annotations",
        b"from __future__ import annotations\n"
        + (
            "from pathlib import Path as _TestPath\n_TestPath("
            + repr(str(marker))
            + ").write_text('unselected')"
        ).encode(),
        1,
    )
    sibling.write_bytes(original)
    path = scripts / SCRIPT.name
    path.write_bytes(SCRIPT.read_bytes())
    name = "s11_official_data_only_test_" + secrets.token_hex(8)
    fresh = ModuleType(name)
    fresh.__file__ = str(path)
    prior = sys.modules.pop("s11_ex10_preflight_launcher", None)
    sys.modules[name] = fresh
    try:
        exec(compile(path.read_bytes(), str(path), "exec"), fresh.__dict__)
        assert not marker.exists(), "Unselected sibling executed during bare API import"
        assert "django" not in fresh.__dict__
    finally:
        sys.modules.pop(name, None)
        sys.modules.pop("s11_ex10_preflight_launcher", None)
        if prior is not None:
            sys.modules["s11_ex10_preflight_launcher"] = prior


# Source checks use a clean, disposable copy with a synthetic fixture-only commit.
# Neither the implementation clone nor its history is changed by these tests.
_V2_SOURCE_BASE = "backend/scripts/s11_ex10_preflight_launcher.py"
_V2_SOURCE_OFFICIAL = "backend/scripts/s11_ex10_official_launcher.py"


def _v2_source_git(repo, *arguments):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    result = subprocess.run(
        ("/usr/bin/git", "--no-optional-locks", "-C", str(repo), *arguments),
        env=env,
        capture_output=True,
        check=True,
    )
    return result.stdout.decode().strip()


@pytest.fixture
def v2_source_checkout(tmp_path):
    source = SCRIPT.parents[2]
    repo = tmp_path / "source-checkout"
    subprocess.run(
        (
            "/usr/bin/git",
            "clone",
            "--local",
            "--no-hardlinks",
            "--no-checkout",
            str(source),
            str(repo),
        ),
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": "/nonexistent",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=True,
    )
    observed = repo.lstat()
    created_root_identity = (observed.st_dev, observed.st_ino, observed.st_uid)
    _v2_source_git(repo, "checkout", "-q", "-B", "main", "HEAD")
    paths = (
        subprocess.run(
            (
                "/usr/bin/git",
                "--no-optional-locks",
                "-C",
                str(source),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ),
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .split("\0")
    )
    for relative in paths:
        if not relative or not (source / relative).is_file():
            continue
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
    _v2_source_git(repo, "add", "--all")
    _v2_source_git(repo, "commit", "-q", "-m", "LOCAL TEST disposable source snapshot")
    expected = _v2_source_git(repo, "rev-parse", "HEAD")
    name = "s11_v2_source_fixture_" + secrets.token_hex(8)
    spec = importlib.util.spec_from_file_location(name, repo / _V2_SOURCE_OFFICIAL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    try:
        yield repo, expected, module
    finally:
        sys.modules.pop(name, None)
        for name, item in tuple(sys.modules.items()):
            if name.startswith(("_s11_native_", "_s11_base_")) and str(
                getattr(item, "__file__", "")
            ).startswith(str(repo) + "/"):
                sys.modules.pop(name, None)
        if os.path.lexists(repo):
            observed = repo.lstat()
            if (
                stat.S_ISDIR(observed.st_mode)
                and (observed.st_dev, observed.st_ino, observed.st_uid) == created_root_identity
            ):
                # Owned disposable fixture only; never follow a replaced root.
                shutil.rmtree(repo)


def _v2_marker_base(repo):
    marker = repo.parent / "sibling-executed.marker"
    content = (repo / _V2_SOURCE_BASE).read_bytes()
    content = content.replace(
        b"from __future__ import annotations",
        b"from __future__ import annotations\n"
        + (
            "from pathlib import Path as _V2TestPath\n_V2TestPath("
            + repr(str(marker))
            + ").write_text('selected')"
        ).encode(),
        1,
    )
    (repo / _V2_SOURCE_BASE).write_bytes(content)
    return marker


def test_v2_source_wrong_expected_sha_rejects_before_sibling_execution(v2_source_checkout):
    repo, expected, module = v2_source_checkout
    marker = _v2_marker_base(repo)
    _v2_source_git(repo, "add", "--", _V2_SOURCE_BASE)
    _v2_source_git(repo, "commit", "-q", "-m", "synthetic changed source")
    with pytest.raises(ValueError, match="^head-mismatch$"):
        module._bootstrap_verified_base(repo, expected)
    assert not marker.exists()


def test_v2_source_dirty_sibling_rejects_before_execution(v2_source_checkout):
    repo, expected, module = v2_source_checkout
    marker = _v2_marker_base(repo)
    with pytest.raises(ValueError, match="^launcher-source-modified$"):
        module._bootstrap_verified_base(repo, expected)
    assert not marker.exists()


def test_v2_source_exact_selected_captured_sibling_executes(v2_source_checkout):
    repo, expected, module = v2_source_checkout
    marker = _v2_marker_base(repo)
    _v2_source_git(repo, "add", "--", _V2_SOURCE_BASE)
    _v2_source_git(repo, "commit", "-q", "-m", "synthetic explicitly selected source")
    selected = _v2_source_git(repo, "rev-parse", "HEAD")
    result = module._bootstrap_verified_base(repo, selected)
    assert result.__file__ == str(repo / _V2_SOURCE_BASE)
    assert marker.read_text() == "selected"


def test_v2_source_foreign_global_module_is_never_adopted(v2_source_checkout):
    from types import ModuleType

    repo, expected, module = v2_source_checkout
    name = "s11_ex10_preflight_launcher"
    previous = sys.modules.get(name)
    foreign = ModuleType(name)
    foreign.marker = "foreign"
    sys.modules[name] = foreign
    try:
        result = module._bootstrap_verified_base(repo, expected)
        assert result is not foreign and not hasattr(result, "marker")
        assert result.__file__ == str(repo / _V2_SOURCE_BASE)
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def test_v2_source_replace_ref_cannot_reselect_expected_source(v2_source_checkout):
    repo, expected, module = v2_source_checkout
    marker = _v2_marker_base(repo)
    _v2_source_git(repo, "add", "--", _V2_SOURCE_BASE)
    _v2_source_git(repo, "commit", "-q", "-m", "synthetic replacement source")
    replacement = _v2_source_git(repo, "rev-parse", "HEAD")
    _v2_source_git(repo, "replace", expected, replacement)
    _v2_source_git(repo, "update-ref", "HEAD", expected)
    assert _v2_source_git(repo, "rev-parse", "HEAD") == expected
    actual = (repo / _V2_SOURCE_BASE).read_bytes()
    blob = hashlib.sha1(b"blob " + str(len(actual)).encode() + b"\0" + actual).hexdigest()
    # The ordinary lookup is demonstrably substituted; the W09 helper refuses it.
    assert _v2_source_git(repo, "rev-parse", expected + ":" + _V2_SOURCE_BASE) == blob
    with pytest.raises(ValueError, match="^launcher-source-modified$"):
        module._bootstrap_verified_base(repo, expected)
    assert not marker.exists()


def test_v2_source_caller_path_shim_cannot_verify_source(v2_source_checkout, monkeypatch):
    repo, expected, module = v2_source_checkout
    shim = repo.parent / "shim"
    shim.mkdir()
    marker = shim / "git-executed.marker"
    binary = shim / "git"
    binary.write_text("#!/bin/sh\n/usr/bin/touch " + str(marker) + "\nexit 0\n")
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", str(shim))
    facts = module._fixed_verify_repository(repo, expected)
    assert facts.head == expected and facts.root == str(repo)
    assert not marker.exists()


def test_v2_source_native_full_closure_import_is_deferred_and_exact(v2_source_checkout):
    repo, expected, module = v2_source_checkout
    loaded = module._load_native_apparatus(repo, expected)
    # Independent explicit catalogue includes the shared prior-custody reader.
    required = {
        "custody_policy",
        "ex10_authority_backend",
        "ex10_authority_sqlite",
        "ex10_authority_witness",
        "ex10_bridge_protocol",
        "ex10_deployment_envelope",
        "ex10_governance_certificate",
        "ex10_history_authority",
        "ex10_open_evidence",
        "ex10_operational_context",
        "ex10_prior_custody",
        "ex10_runtime_profile",
        "ex10_session_v2",
        "ex10_start_coordination",
        "manifest",
    }
    transitives = {
        "canonical",
        "constants",
        "evidence",
        "ex10_load",
        "ex10_witness_service",
        "redaction",
    }
    assert set(loaded) == required
    package = next(iter(loaded.values())).__package__
    children = {k for k in sys.modules if k.startswith(package + ".")}
    assert children == {package + "." + name for name in required | transitives}
    assert not any(k.endswith(".ex10_executor") for k in children)
    for name, value in loaded.items():
        assert value.__file__ == str(repo / ("backend/apps/experiments_app/" + name + ".py"))


def test_v2_source_native_private_cache_rejects_foreign_object(v2_source_checkout):
    from types import ModuleType

    repo, expected, module = v2_source_checkout
    loaded = module._load_native_apparatus(repo, expected)
    original = loaded["ex10_runtime_profile"]
    package = sys.modules[original.__package__]
    qualified = original.__name__
    foreign = ModuleType(qualified)
    foreign.__file__ = original.__file__
    foreign._s11_verified_source_sha256 = original._s11_verified_source_sha256
    sys.modules[qualified] = foreign
    package.ex10_runtime_profile = foreign
    try:
        with pytest.raises(module.LauncherError, match="^native-source-integrity$"):
            module._load_native_apparatus(repo, expected)
    finally:
        sys.modules[qualified] = original
        package.ex10_runtime_profile = original


def test_v2_source_git_invalid_utf8_has_fixed_code(v2_source_checkout):
    repo, expected, module = v2_source_checkout
    fd = os.open(os.fsencode(repo) + b"/invalid-\xff", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    _v2_source_git(repo, "add", "--all")
    _v2_source_git(repo, "commit", "-q", "-m", "synthetic nonUTF8 filename")
    selected = _v2_source_git(repo, "rev-parse", "HEAD")
    with pytest.raises(module.LauncherError) as error:
        module._fixed_verify_repository(repo, selected)
    assert error.value.code == "git-output-invalid-utf8"


def test_v2_source_concurrent_first_bind_never_changes_selector(v2_source_checkout, monkeypatch):
    """Two genuinely qualified revisions cannot both select one launcher instance."""
    import threading

    repo, first_sha, module = v2_source_checkout
    first_qualified = threading.Event()
    release_first = threading.Event()
    original = module._bootstrap_verified_base
    accepted, refused, unexpected = [], [], []

    def controlled_qualification(root, expected):
        result = original(root, expected)
        if expected == first_sha:
            first_qualified.set()
            if not release_first.wait(20):
                raise AssertionError("Timed out waiting for controlled source qualification")
        return result

    monkeypatch.setattr(module, "_bootstrap_verified_base", controlled_qualification)

    def bind(expected):
        try:
            result = module.base.bind(repo, expected)
            accepted.append((expected, result))
        except module.LauncherError as exc:
            refused.append(exc.code)
        except BaseException as exc:
            unexpected.append(type(exc).__name__)

    first = threading.Thread(target=bind, args=(first_sha,))
    second = None
    first.start()
    try:
        assert first_qualified.wait(20)
        # A real new fixture commit, with both launcher files unchanged.
        (repo / "selector-race-fixture.txt").write_text("LOCAL TEST second revision\n")
        _v2_source_git(repo, "add", "--", "selector-race-fixture.txt")
        _v2_source_git(repo, "commit", "-q", "-m", "LOCAL TEST second selected revision")
        second_sha = _v2_source_git(repo, "rev-parse", "HEAD")
        assert second_sha != first_sha
        second = threading.Thread(target=bind, args=(second_sha,))
        second.start()
        second.join(20)
        assert not second.is_alive(), "Concurrent selector did not refuse promptly"
    finally:
        release_first.set()
        first.join(20)
        if second is not None:
            second.join(20)
    assert not first.is_alive()
    assert not unexpected
    assert len(accepted) == 1, "Two different revisions selected the same launcher instance"
    assert refused == ["launcher-busy"]
    assert module.base._qualified_selector == (str(repo), first_sha)
    assert module.base._qualified_module is accepted[0][1]


def _v2_fixture_legacy_session(repo, expected, module):
    root = repo.parent / "legacy-session"
    root.mkdir(mode=0o700)
    (root / "actions").mkdir(mode=0o700)
    # All paths are in this test's disposable checkout, never live/historical var.
    for name in module.OFFICIAL_ROOTS:
        (repo / "var" / name).mkdir(mode=0o700, parents=True)
    manifest = module.new_session_manifest(
        session_id="s11-ex10-official-session-0123456789abcdef",
        created_at="2026-10-04T00:00:00.000Z",
        git_sha=expected,
        branch="main",
        repo_root=repo,
        integrity=module.verify_launcher_integrity(repo, expected),
    )
    target = root / module.SESSION_FILE
    target.write_bytes(module.base.canonical_bytes(manifest))
    target.chmod(0o600)
    return root


def _v2_private_tree_bytes(root):
    return {
        str(path.relative_to(root)): (
            path.read_bytes() if path.is_file() else None,
            stat.S_IMODE(path.lstat().st_mode),
            path.lstat().st_ino,
            path.lstat().st_mtime_ns,
            path.lstat().st_ctime_ns,
        )
        for path in (root, *sorted(root.rglob("*")))
    }


@pytest.mark.parametrize(
    "action", sorted(set(official.ACTIONS) - {"initialize-session", "inspect"})
)
def test_v2_dispatch_never_mutates_legacy_session(v2_source_checkout, action):
    """Every operational action, including status, rejects v1 before any output."""
    repo, expected, module = v2_source_checkout
    root = _v2_fixture_legacy_session(repo, expected, module)
    before = _v2_private_tree_bytes(root)
    values = {}
    if action == "start-official":
        report = repo.parent / "unused-preflight.json"
        report.write_bytes(b"{}\n")
        report.chmod(0o600)
        values.update(nonce="nonce-read-only-v1", preflight_report=report)
    invocation = module.Invocation(
        action=action,
        expected_git_sha=expected,
        repo_root=repo,
        session_root=root,
        environ={},
        confirmed=module.ACTIONS[action].confirmation is not None,
        **values,
    )
    with pytest.raises(module.LauncherError, match="^v1-read-only$"):
        module.execute(invocation)
    assert _v2_private_tree_bytes(root) == before


def test_v2_initialize_without_fixed_host_bootstrap_does_not_create_v1(v2_source_checkout):
    """No legacy/default bootstrap may create an operator session."""
    repo, expected, module = v2_source_checkout
    for name in module.OFFICIAL_ROOTS:
        (repo / "var" / name).mkdir(mode=0o700, parents=True)
    root = repo.parent / "new-session"
    invocation = module.Invocation(
        action="initialize-session",
        expected_git_sha=expected,
        repo_root=repo,
        session_root=root,
        environ={},
    )
    with pytest.raises(module.LauncherError, match="^native-launcher-bootstrap-required$"):
        module.execute(invocation)
    assert not root.exists()


def test_v2_legacy_inspect_remains_byte_readonly(v2_source_checkout):
    repo, expected, module = v2_source_checkout
    root = _v2_fixture_legacy_session(repo, expected, module)
    before = _v2_private_tree_bytes(root)
    result = module.execute(
        module.Invocation(
            action="inspect",
            expected_git_sha=expected,
            repo_root=repo,
            session_root=root,
            environ={},
        )
    )
    assert result["launcher_exit"] == 0
    assert result["classification"] == "COHERENT"
    assert _v2_private_tree_bytes(root) == before


def test_v2_source_checkout_releases_its_owned_clone(tmp_path):
    driver = v2_source_checkout.__wrapped__(tmp_path)
    repo, _, _ = next(driver)
    assert repo.is_dir() and (repo / ".git").is_dir()
    driver.close()
    assert not os.path.lexists(repo)


def test_v2_source_checkout_never_deletes_a_replaced_root(tmp_path):
    driver = v2_source_checkout.__wrapped__(tmp_path)
    repo, _, _ = next(driver)
    original = repo.with_name("retained-owned-clone")
    repo.rename(original)
    repo.mkdir(mode=0o700)
    marker = repo / "foreign.marker"
    marker.write_bytes(b"LOCAL TEST unrelated replacement\n")
    driver.close()
    assert marker.read_bytes() == b"LOCAL TEST unrelated replacement\n"
    assert original.is_dir()
    # Both paths were created by this test; the fixture correctly deleted neither.
    shutil.rmtree(original)
    shutil.rmtree(repo)


@pytest.fixture
def v2_native_host(v2_source_checkout, request, monkeypatch):
    """Actual B1/B2/B3 and POSIX guards; only external approvals/keys are synthetic."""
    import ast
    import tempfile

    repo, expected, module = v2_source_checkout
    apparatus = module._load_native_apparatus(repo, expected)
    rp = apparatus["ex10_runtime_profile"]
    sql = apparatus["ex10_authority_sqlite"]
    durable = apparatus["ex10_authority_backend"]
    history = apparatus["ex10_history_authority"]
    name = "s11_w09_history_fixture_" + secrets.token_hex(8)
    spec = importlib.util.spec_from_file_location(
        name, SCRIPT.parent.parent / "apps/experiments_app/tests/test_ex10_history_authority.py"
    )
    helper = importlib.util.module_from_spec(spec)
    sys.modules[name] = helper
    spec.loader.exec_module(helper)
    helper.sql, helper.wit, helper.history = (sql, apparatus["ex10_authority_witness"], history)
    variant = getattr(request, "param", None)
    fixture_clock = None
    if variant == "worker-result-fixed-clock":
        # One coherent clock before any snapshot/context is constructed. Keep the
        # original signed 30-second TTL; do not refresh or renew retained authority.
        fixture_now = datetime.now(UTC)

        def fixture_clock():
            return fixture_now

        monkeypatch.setattr(
            helper,
            "timestamp",
            lambda offset=0: (fixture_now + timedelta(seconds=offset)).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
        )
        monkeypatch.setattr(history, "_now", lambda: helper.timestamp())
    store = None
    with tempfile.TemporaryDirectory(
        prefix="w09-native-", dir=os.environ["S11_B2_TEST_BASE"]
    ) as temporary:
        home = Path(temporary)
        home.chmod(0o700)

        def directory(name):
            path = home / name
            path.mkdir(mode=0o700)
            return path

        def private(name, raw):
            path = home / name
            path.write_bytes(raw)
            path.chmod(0o600)
            return path

        try:
            # Optional full B4 protocol fixture only: sign the correct synthetic
            # UUIDv5 baseline before any B3 material or database is provisioned.
            # Existing fixtures retain their original independent B3 baseline.
            if variant == "typed-b4-historic-run":
                helper.RUN_ID = "s11-22222222-3333-5555-8888-666666666666"
            elif variant == "typed-b4-legacy-ack-other":
                helper.RUN_ID = OTHER_RUN
            elif variant == "typed-b4-legacy-ack-run1":
                helper.RUN_ID = HISTORICAL_RUN_1
            kinds = () if variant == "typed-b4-empty-history" else ("ND",)
            authority = helper.Fixture(directory("authority"), kinds=kinds)
            descriptor = private("descriptor.json", sql.canonical_bytes(authority.descriptor))
            authority.descriptor_ref["path"] = str(descriptor)
            refs = {}
            for field in rp.REFERENCE_FIELDS:
                path = private(field + ".json", sql.canonical_bytes({"fixture": field}))
                refs[field] = {
                    "path": str(path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "schema_version": "s11-ex10-synthetic-reference-v1",
                }
            refs["historical_lifecycle_authority_reference"] = dict(authority.descriptor_ref)
            override = private("override.yml", b"services: {}\n")
            approval_path = private("profile-approval.json", b'{"approved":"synthetic"}\n')
            approval = {
                "path": str(approval_path),
                "sha256": hashlib.sha256(approval_path.read_bytes()).hexdigest(),
                "schema_version": "s11-ex10-synthetic-profile-approval-v1",
            }
            owner = {"uid": os.getuid(), "gid": os.getgid()}
            compose = [
                repo / "infra" / name for name in ("docker-compose.yml", "docker-compose.s11.yml")
            ]
            profile = {
                "schema_version": rp.SCHEMA_VERSION,
                "profile_id": "w09-synthetic-profile",
                "approved_at": "2026-10-04T00:00:00.000000Z",
                "source_revision": expected,
                "project": "w09-synthetic-project",
                "compose_files": [
                    {
                        "kind": "tracked",
                        "path": str(path),
                        "git_path": "infra/" + path.name,
                        "git_blob": module._fixed_source_git(
                            repo, "rev-parse", "HEAD:infra/" + path.name
                        ).strip(),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                    for path in compose
                ]
                + [
                    {
                        "kind": "external_override",
                        "path": str(override),
                        "git_path": None,
                        "git_blob": None,
                        "sha256": hashlib.sha256(override.read_bytes()).hexdigest(),
                    }
                ],
                "override_path": str(override),
                "override_sha256": hashlib.sha256(override.read_bytes()).hexdigest(),
                "source_host_root": str(repo),
                "source_container_target": "/s11-full-source",
                "source_read_only": True,
                "host_roots": {
                    role: {
                        "path": str(directory(role)),
                        "owner_uid": owner["uid"],
                        "owner_gid": owner["gid"],
                        "directory_mode": "0700",
                        "artifact_mode": "0600",
                    }
                    for role in rp.ROLES
                },
                "fixed_targets": dict(rp.FIXED_TARGETS),
                "expected_owner": owner,
                "approved_modes": {"directory": "0700", "artifact": "0600"},
                "masks": [
                    {
                        "relative_path": "var",
                        "kind": "directory",
                        "source_path": str(directory("mask")),
                        "read_only": True,
                    },
                    {
                        "relative_path": ".env",
                        "kind": "dev_null_file",
                        "source_path": "/dev/null",
                        "read_only": True,
                    },
                ],
                "expected_services": ["db", "redis", "backend", "worker"],
                "beat_policy": "disabled",
                **refs,
            }
            profile_path = private(
                "runtime-profile.json", rp.canonical_runtime_profile_bytes(profile)
            )
            preflight_tree = ast.parse(
                (repo / "backend/apps/experiments_app/ex10_preflight.py").read_bytes()
            )
            sources = next(
                ast.literal_eval(node.value)
                for node in preflight_tree.body
                if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "RUNNING_SOURCES" for t in node.targets)
            )
            path_context = {
                "repo_root": str(repo),
                "historical_roots": [],
                "prior_session_roots": [],
                "running_sources": list(sources),
                "approved_reference_schemas": {
                    key: [refs[key]["schema_version"]] for key in rp.REFERENCE_FIELDS
                },
            }
            session_root = home / "new-session"
            session_context = {
                "parent": str(session_root),
                "owner_uid": owner["uid"],
                "owner_gid": owner["gid"],
                "repo_root": str(repo),
                "historical_roots": [],
                "prior_session_roots": [],
            }
            guard_root = directory("global-guard")
            guard = guard_root / "global.lock"
            guard.write_bytes(b"")
            guard.chmod(0o600)

            def identity(path):
                info = path.stat()
                return durable.FileIdentity(
                    info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)
                )

            store = durable.PrivateStore(str(guard_root), identity(guard_root))
            checkpoint = {"value": None, "publishes": 0}
            approvals = {"allowed": True, "calls": 0}

            def approve(**facts):
                assert approvals["allowed"], "synthetic approval withdrawn"
                assert facts["profile"].to_dict() == profile
                assert facts["session_root"] == str(session_root)
                assert facts["source_revision"] == expected
                assert facts["guard_identity"] == identity(guard)
                assert facts["authority_revision"]["authority_id"] == helper.AUTH_ID
                assert (
                    dict(facts["references"])["runtime_profile_approval_reference"]
                    == approval_path.read_bytes()
                )
                approvals["calls"] += 1
                if approvals.get("hook") is not None:
                    return approvals["hook"](facts)

            def read_authority(reference):
                assert reference == authority.descriptor_ref
                return authority.verify()

            def read_checkpoint():
                if checkpoint.get("read_hook") is not None:
                    checkpoint["read_hook"]()
                return checkpoint["value"]

            def publish(expected_checkpoint, replacement):
                assert checkpoint["value"] == expected_checkpoint
                if checkpoint.get("lose_publication"):
                    raise OSError("LOCAL TEST checkpoint publication loss")
                checkpoint["value"] = replacement
                checkpoint["publishes"] += 1

            def bootstrap():
                return module.NativeLauncherBootstrap(
                    repo_root=repo,
                    expected_git_sha=expected,
                    profile_path=profile_path,
                    path_context=path_context,
                    approval_reference=approval,
                    session_context=session_context,
                    global_store=store,
                    global_name="global.lock",
                    global_identity=identity(guard),
                    approve=approve,
                    read_authority=read_authority,
                    read_checkpoint=read_checkpoint,
                    publish_checkpoint=publish,
                )

            def invocation(action="initialize-session", **changes):
                values = dict(
                    action=action,
                    expected_git_sha=expected,
                    repo_root=repo,
                    session_root=session_root,
                    environ={},
                    native_bootstrap=bootstrap(),
                )
                if fixture_clock is not None:
                    values["clock"] = fixture_clock
                values.update(changes)
                return module.Invocation(**values)

            yield SimpleNamespace(
                module=module,
                apparatus=apparatus,
                profile=profile,
                profile_path=profile_path,
                root=session_root,
                home=home,
                authority=authority,
                approvals=approvals,
                checkpoint=checkpoint,
                store=store,
                guard=guard,
                invocation=invocation,
            )
        finally:
            if store is not None:
                store.resolver.close()
            sys.modules.pop(name, None)
            for key in tuple(sys.modules):
                if key.startswith("b3_contract_fixture_" + name):
                    sys.modules.pop(key, None)


def test_v2_native_initialize_and_inspect_actual_b1_b2_b3(v2_native_host):
    host = v2_native_host
    result = host.module.execute(host.invocation())
    assert result["classification"] == "COHERENT" and result["launcher_exit"] == 0
    raw = (host.root / host.module.SESSION_FILE).read_bytes()
    value = json.loads(raw)
    codec = host.apparatus["ex10_session_v2"]
    assert value["schema_version"] == codec.SESSION_V2_SCHEMA
    assert len(codec.HEADER_KEYS) == 27
    assert value["state"]["next_allowed_actions"] == [
        "cleanup-stack",
        "inspect",
        "start-official",
    ]
    assert host.checkpoint["value"] == (value["history"][-1]["chain"], 1)
    before = _v2_private_tree_bytes(host.home)
    inspected = host.module.execute(host.invocation("inspect"))
    assert inspected["classification"] == "COHERENT"
    assert _v2_private_tree_bytes(host.home) == before
    assert (host.root / host.module.SESSION_FILE).read_bytes() == raw
    assert host.checkpoint["publishes"] == 1


def test_v2_native_initialize_requires_external_approval_before_creation(v2_native_host):
    host = v2_native_host
    host.approvals["allowed"] = False
    with pytest.raises(host.module.LauncherError, match="native-host-approval-required"):
        host.module.execute(host.invocation())
    assert not os.path.lexists(host.root) and host.checkpoint["publishes"] == 0


@pytest.mark.parametrize("fault", ["missing", "truncated", "unknown"])
def test_v2_native_inspect_requires_independent_checkpoint(v2_native_host, fault):
    host = v2_native_host
    host.module.execute(host.invocation())
    if fault == "missing":
        host.checkpoint["value"] = None
    elif fault == "truncated":
        codec = host.apparatus["ex10_session_v2"]
        path = host.root / host.module.SESSION_FILE
        initial = codec.new_official_session_v2(
            {key: json.loads(path.read_bytes())[key] for key in codec.HEADER_KEYS},
            profile=host.profile,
        )
        path.write_bytes(initial._bytes)
    else:
        host.checkpoint["value"] = ("f" * 64, 999)
    before = _v2_private_tree_bytes(host.home)
    with pytest.raises(host.module.LauncherError):
        host.module.execute(host.invocation("inspect"))
    assert _v2_private_tree_bytes(host.home) == before


def test_v2_native_initialize_does_not_adopt_existing_directory(v2_native_host):
    host = v2_native_host
    host.root.mkdir(mode=0o700)
    marker = host.root / "unrelated"
    marker.write_bytes(b"private unrelated data")
    before = _v2_private_tree_bytes(host.home)
    with pytest.raises(host.module.LauncherError):
        host.module.execute(host.invocation())
    assert _v2_private_tree_bytes(host.home) == before


def test_v2_native_inspect_rechecks_record_after_external_approval(v2_native_host):
    host = v2_native_host
    host.module.execute(host.invocation())
    codec = host.apparatus["ex10_session_v2"]
    target = host.root / host.module.SESSION_FILE

    def replace_record(facts):
        if facts["session"] is not None:
            previous = facts["session"]
            payload = dict(previous.to_dict()["history"][-1]["payload"])
            payload["event"] = "LOCAL-TEST-external-replacement"
            changed = codec.append_history_entry_v2(previous, payload)
            target.write_bytes(changed._bytes)
            host.approvals["hook"] = None

    host.approvals["hook"] = replace_record
    with pytest.raises(host.module.LauncherError):
        host.module.execute(host.invocation("inspect"))
    assert (
        json.loads(target.read_bytes())["history"][-1]["payload"]["event"]
        == "LOCAL-TEST-external-replacement"
    )
    assert host.checkpoint["publishes"] == 1


def test_v2_native_initialize_lost_checkpoint_never_retries_or_removes_record(v2_native_host):
    host = v2_native_host
    host.checkpoint["lose_publication"] = True
    with pytest.raises(host.module.LauncherError, match="native-write-uncertain"):
        host.module.execute(host.invocation())
    assert host.root.is_dir() and (host.root / host.module.SESSION_FILE).is_file()
    before = _v2_private_tree_bytes(host.home)
    with pytest.raises(host.module.LauncherError):
        host.module.execute(host.invocation())
    with pytest.raises(host.module.LauncherError):
        host.module.execute(host.invocation("inspect"))
    assert _v2_private_tree_bytes(host.home) == before
    assert host.checkpoint["value"] is None and host.checkpoint["publishes"] == 0


def test_v2_native_initialize_requires_actual_global_guard_before_create(v2_native_host):
    host = v2_native_host
    with host.invocation().native_bootstrap.writer():
        with pytest.raises(host.module.LauncherBusy):
            host.module.execute(host.invocation())
        assert not host.root.exists() and host.checkpoint["publishes"] == 0


@pytest.mark.parametrize("answer", [True, False])
def test_v2_native_boolean_is_not_external_approval(v2_native_host, answer):
    host = v2_native_host
    host.approvals["hook"] = lambda facts: answer
    with pytest.raises(host.module.LauncherError, match="native-host-approval-required"):
        host.module.execute(host.invocation())
    assert not host.root.exists()


def test_v2_native_unverified_complete_history_is_not_authority(v2_native_host):
    host = v2_native_host
    sql = host.apparatus["ex10_authority_sqlite"]
    history = host.apparatus["ex10_history_authority"]
    host.authority.verify = lambda: history.AuthoritySnapshot(
        sql.canonical_bytes(host.authority.material())
    )
    with pytest.raises(host.module.LauncherError, match="native-authority-HISTORY_INVALID"):
        host.module.execute(host.invocation())
    assert not host.root.exists()


def test_v2_native_inspect_rejects_replaced_profile_bound_root(v2_native_host):
    host = v2_native_host
    host.module.execute(host.invocation())
    evidence = Path(host.profile["host_roots"]["evidence"]["path"])
    retained = evidence.with_name("original-evidence-root")
    evidence.rename(retained)
    evidence.mkdir(mode=0o700)
    before = _v2_private_tree_bytes(host.home)
    with pytest.raises(host.module.LauncherError, match="native-session-binding-changed"):
        host.module.execute(host.invocation("inspect"))
    assert _v2_private_tree_bytes(host.home) == before


def test_v2_native_rechecks_override_after_external_approval(v2_native_host):
    host = v2_native_host
    host.module.execute(host.invocation())
    override = Path(host.profile["override_path"])

    def replace_override(facts):
        if facts["session"] is not None:
            override.write_bytes(b"services: {foreign: {}}\n")
            host.approvals["hook"] = None

    host.approvals["hook"] = replace_override
    with pytest.raises(host.module.LauncherError):
        host.module.execute(host.invocation("inspect"))
    assert override.read_bytes() == b"services: {foreign: {}}\n"
    assert host.checkpoint["publishes"] == 1


def test_v2_native_never_adopts_root_replaced_after_mkdir(v2_native_host, monkeypatch):
    host = v2_native_host
    original_fsync = host.module.os.fsync
    parent_info = host.root.parent.stat()
    retained_parent = host.home / "already-existing-retention-directory"
    retained_parent.mkdir(mode=0o700)
    retained = retained_parent / "original-created-root"
    replaced = []

    def fsync(fd):
        original_fsync(fd)
        info = os.fstat(fd)
        if (
            not replaced
            and host.root.exists()
            and (info.st_dev, info.st_ino) == (parent_info.st_dev, parent_info.st_ino)
        ):
            replaced.append(True)
            host.root.rename(retained)
            host.root.mkdir(mode=0o700)
            marker = host.root / "foreign.marker"
            marker.write_bytes(b"LOCAL TEST unrelated replacement")
            marker.chmod(0o600)

    monkeypatch.setattr(host.module.os, "fsync", fsync)
    with pytest.raises(host.module.LauncherError):
        host.module.execute(host.invocation())
    assert replaced == [True]
    assert sorted(p.name for p in host.root.iterdir()) == ["foreign.marker"]
    assert list(retained.iterdir()) == []
    assert host.checkpoint["value"] is None


def _v2_replace_global_guard(host):
    retained = host.guard.with_name("retained-global.lock")
    host.guard.rename(retained)
    host.guard.write_bytes(b"")
    host.guard.chmod(0o600)


def test_v2_native_checks_guard_after_clock_before_record_create(v2_native_host):
    host = v2_native_host

    def clock():
        _v2_replace_global_guard(host)
        return datetime.now(UTC)

    with pytest.raises(host.module.LauncherError):
        host.module.execute(host.invocation(clock=clock))
    assert host.root.is_dir()
    assert not (host.root / host.module.SESSION_FILE).exists()
    assert host.checkpoint["value"] is None


def test_v2_native_checks_guard_before_independent_checkpoint_publication(
    v2_native_host, monkeypatch
):
    host = v2_native_host
    codec = host.apparatus["ex10_session_v2"]
    update = codec.update_official_session_v2

    def update_and_replace_guard(*args, **kwargs):
        result = update(*args, **kwargs)
        _v2_replace_global_guard(host)
        return result

    monkeypatch.setattr(codec, "update_official_session_v2", update_and_replace_guard)
    with pytest.raises(host.module.LauncherError):
        host.module.execute(host.invocation())
    assert (host.root / host.module.SESSION_FILE).is_file()
    assert host.checkpoint["value"] is None and host.checkpoint["publishes"] == 0


def test_v2_native_parent_lease_allows_only_its_one_created_child(v2_native_host, monkeypatch):
    host = v2_native_host
    original_fsync = host.module.os.fsync
    parent_info = host.root.parent.stat()
    changed = []

    def fsync(fd):
        original_fsync(fd)
        info = os.fstat(fd)
        if (
            not changed
            and host.root.exists()
            and (info.st_dev, info.st_ino) == (parent_info.st_dev, parent_info.st_ino)
        ):
            changed.append(True)
            host.root.with_name("unapproved-concurrent-child").mkdir(mode=0o700)

    monkeypatch.setattr(host.module.os, "fsync", fsync)
    with pytest.raises(host.module.LauncherError, match="native-session-parent-changed"):
        host.module.execute(host.invocation())
    assert changed == [True]
    assert not (host.root / host.module.SESSION_FILE).exists()
    assert host.checkpoint["value"] is None


def test_v2_native_last_session_check_cannot_authorize_unfenced_create(v2_native_host, monkeypatch):
    import contextlib

    host = v2_native_host
    actual = host.module._native_session_lock
    changed = []

    @contextlib.contextmanager
    def controlled_last_check(*args, **kwargs):
        with actual(*args, **kwargs) as check:

            def checked():
                check()
                if not changed:
                    changed.append(True)
                    _v2_replace_global_guard(host)

            yield checked

    monkeypatch.setattr(host.module, "_native_session_lock", controlled_last_check)
    with pytest.raises(host.module.LauncherError):
        host.module.execute(host.invocation())
    assert changed == [True]
    assert not (host.root / host.module.SESSION_FILE).exists()
    assert host.checkpoint["value"] is None


def test_v2_native_inspect_requires_unexpired_authority_after_checkpoint_read(
    v2_native_host, monkeypatch
):
    host = v2_native_host
    host.module.execute(host.invocation())
    history = host.apparatus["ex10_history_authority"]
    clock = {"now": datetime.now(UTC)}
    monkeypatch.setattr(history, "_now", lambda: clock["now"].strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    reads = []

    def late_checkpoint():
        reads.append(True)
        if len(reads) == 2:
            clock["now"] += timedelta(seconds=40)

    host.checkpoint["read_hook"] = late_checkpoint
    before = _v2_private_tree_bytes(host.home)
    with pytest.raises(host.module.LauncherError, match="native-authority-EXPIRED_OBSERVATION"):
        host.module.execute(host.invocation("inspect"))
    assert len(reads) == 2
    assert _v2_private_tree_bytes(host.home) == before


@pytest.fixture
def v2_oneoff_control(v2_native_host):
    """Real qualified source/B1/B2/B3; Docker observations are synthetic unit inputs."""
    import copy
    import uuid

    host = v2_native_host
    host.module.execute(host.invocation())
    bootstrap = host.invocation().native_bootstrap
    codec = host.apparatus["ex10_session_v2"]
    session = bootstrap.load(host.invocation("inspect"))
    name = "s11-ex10-owned-" + secrets.token_hex(8)
    action = "start-official"
    payload = dict(session.to_dict()["history"][-1]["payload"])
    payload["event"] = "intent-" + action
    payload["in_flight"] = [
        {
            "action": action,
            "container_name": name,
            "output_dir": "actions/owned-intent",
            "started_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "after_seq": session._expected_count,
        }
    ]
    payload["state"] = {
        "known_phase": None,
        "uncertain": True,
        "blocked": True,
        "next_allowed_actions": ["inspect"],
    }
    replacement = codec.append_history_entry_v2(session, payload)
    with bootstrap.writer() as writer:
        with host.module._native_session_lock(bootstrap, host.root) as current:
            current()
            writer.check()
            replacement = codec.update_official_session_v2(
                host.root / host.module.SESSION_FILE,
                session,
                replacement,
                context=bootstrap.context(),
                profile=host.profile,
                caller_serialized=True,
            )
            writer.check()
            bootstrap.publish((session._expected_tip, session._expected_count), replacement)
    profile_sha = host.apparatus["ex10_runtime_profile"].runtime_profile_sha256(host.profile)
    cfg = {
        "action": "start",
        "exchange_id": str(uuid.uuid4()),
        "profile_sha256": profile_sha,
        "header_sha256": replacement.to_dict()["header_sha256"],
        "bridge_pin": {},
        "witness_pin": {},
        "b3_pins": [],
        "genesis_digest": "a" * 64,
        "genesis_binding": {},
        "authority_revision": 0,
        "request_payload": {},
        "expected_reservation": None,
        "host_root_binding_sha256": "b" * 64,
        "source_hashes": {},
        "staging_name": "application-preflight." + str(uuid.uuid4()) + ".json",
        "command_options": {
            "expected_git_sha": host.profile["source_revision"],
            "source_root": "/s11-full-source",
            "nonce": "c" * 64,
            "acknowledge_not_demonstrated": [],
        },
        "authorization_reference": {},
    }
    cfg_raw = host.module.base.canonical_bytes(cfg)
    container_id, image, network = "1" * 64, "sha256:" + "2" * 64, "3" * 64
    supplied = {
        "DJANGO_SETTINGS_MODULE": "config.settings.test",
        "SYNTHETIC_PRIVATE_VALUE": "LOCAL-TEST-secret-not-in-argv",
    }
    events = []
    observations = {
        "alter": None,
        "fail_create": False,
        "lose_create_response": False,
        "allow": True,
        "phase": "created",
        "worker_code": 0,
    }
    holder = {}

    class FakeDocker:
        def __init__(self, cwd, env):
            self.cwd, self.env = cwd, env

        def run(self, argv, *, timeout):
            assert timeout > 0
            if argv[:3] == ("/usr/bin/docker", "network", "inspect"):
                assert argv[3] == network
                events.append(("network-inspect", tuple(argv)))
                return host.module.Completed(
                    0,
                    host.module.base.canonical_bytes(
                        [
                            {
                                "Id": network,
                                "Name": observations.get("network_name", "synthetic-network"),
                            }
                        ]
                    ),
                    b"",
                )
            assert argv[:2] == ("/usr/bin/docker", "container")
            operation = argv[2]
            events.append((operation, tuple(argv)))
            if operation == "create":
                if observations["fail_create"]:
                    return host.module.Completed(1, b"", b"LOCAL TEST private diagnostic")
                raw = (
                    b"unusable lost response"
                    if observations["lose_create_response"]
                    else (container_id + "\n").encode()
                )
                return host.module.Completed(0, raw, b"")
            if operation == "start":
                assert argv == ("/usr/bin/docker", "container", "start", container_id)
                observations["phase"] = "running"
                return host.module.Completed(0, (container_id + "\n").encode(), b"")
            assert operation == "inspect" and argv[-1] == container_id
            control = holder["control"]
            phase = observations["phase"]
            running = phase == "running"
            value = {
                "Id": container_id,
                "Name": "/" + name,
                "Image": image,
                "State": {
                    "Status": phase,
                    "Running": running,
                    "Restarting": False,
                    "Pid": 2345 if running else 0,
                    "ExitCode": observations["worker_code"],
                    "OOMKilled": False,
                },
                "Config": {
                    "User": f"{os.getuid()}:{os.getgid()}",
                    "Labels": dict(control.labels),
                    "Env": [
                        key + "=" + value for key, value in control.container_environment.items()
                    ],
                    "Entrypoint": ["/usr/local/bin/python"],
                    "Cmd": ["-B", "/app/manage.py", "s11_ex10", "bridge"],
                    "WorkingDir": "/s11-full-source/backend",
                    "Healthcheck": {"Test": ["NONE"]},
                },
                "HostConfig": {
                    "ReadonlyRootfs": True,
                    "Privileged": False,
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges"],
                    "RestartPolicy": {"Name": "no"},
                    "PortBindings": {},
                    "PublishAllPorts": False,
                    "Binds": None,
                    "VolumesFrom": None,
                    "Devices": None,
                    "DeviceRequests": None,
                    "PidMode": "",
                    "UsernsMode": "",
                },
                "Mounts": [
                    {"Type": "bind", "Source": source, "Destination": target, "RW": not read_only}
                    for source, target, read_only in control.mounts
                ],
                "NetworkSettings": {"Networks": {"synthetic-network": {"NetworkID": network}}},
            }
            if observations["alter"] is not None:
                observations["alter"](value)
            return host.module.Completed(
                0, host.module.base.canonical_bytes([copy.deepcopy(value)]), b""
            )

    def approve(**facts):
        assert observations["allow"], "LOCAL TEST approval refused"
        assert facts["session"].to_dict() == replacement.to_dict()
        assert (
            facts["name"] == name
            and facts["image_digest"] == image
            and facts["network_id"] == network
        )
        assert facts["bootstrap_bytes"] == cfg_raw

    invocation = host.invocation(
        action, confirmed=True, nonce="c" * 64, preflight_report=host.profile_path
    )
    control = host.module.NativeOneOffDocker(
        invocation=invocation,
        bootstrap=bootstrap,
        name=name,
        image_digest=image,
        network_id=network,
        bootstrap_bytes=cfg_raw,
        service_environment=supplied,
        owner_labels={"s11.owner.session": replacement.header["session_id"]},
        approve_creation=approve,
        runner_factory=FakeDocker,
    )
    holder["control"] = control
    yield SimpleNamespace(
        host=host,
        control=control,
        events=events,
        observations=observations,
        container_id=container_id,
        secret=supplied["SYNTHETIC_PRIVATE_VALUE"],
    )


def test_v2_oneoff_create_inspect_then_start_exact_id(v2_oneoff_control):
    case = v2_oneoff_control
    assert case.control.create() == case.container_id
    assert not any(kind == "start" for kind, _ in case.events)
    case.control.start()
    assert [kind for kind, _ in case.events] == ["create", "inspect", "start", "inspect"]
    assert case.control.worker_exit() is None
    case.observations["phase"] = "exited"
    assert case.control.worker_exit() == 0
    assert not any("run" in argv or "rm" in argv for _, argv in case.events)
    assert case.secret not in repr([argv for _, argv in case.events])
    assert all("--rm" not in argv and "--env-file" not in argv for _, argv in case.events)


@pytest.mark.parametrize(
    "fault",
    [
        "foreign-id",
        "foreign-name",
        "owner-label",
        "root-alias",
        "writable-source",
        "extra-mount",
        "foreign-network",
        "privileged",
    ],
)
def test_v2_oneoff_wrong_instance_or_mount_never_starts(v2_oneoff_control, fault):
    case = v2_oneoff_control

    def alter(value):
        if fault == "foreign-id":
            value["Id"] = "9" * 64
        elif fault == "foreign-name":
            value["Name"] = "/same-project-foreign-resource"
        elif fault == "owner-label":
            value["Config"]["Labels"]["s11.owner.session"] = "foreign-session"
        elif fault == "root-alias":
            value["Mounts"][1]["Source"] = value["Mounts"][0]["Source"]
        elif fault == "writable-source":
            value["Mounts"][0]["RW"] = True
        elif fault == "extra-mount":
            value["Mounts"].append(
                {
                    "Type": "bind",
                    "Source": "/synthetic-unapproved",
                    "Destination": "/foreign",
                    "RW": False,
                }
            )
        elif fault == "foreign-network":
            value["NetworkSettings"]["Networks"]["synthetic-network"]["NetworkID"] = "8" * 64
        else:
            value["HostConfig"]["Privileged"] = True

    case.observations["alter"] = alter
    case.control.create()
    with pytest.raises(case.host.module.LauncherError):
        case.control.start()
    assert [kind for kind, _ in case.events] == ["create", "inspect"]


@pytest.mark.parametrize("fault", ["command-failure", "lost-response"])
def test_v2_oneoff_create_failure_has_no_retry_adoption_or_cleanup(v2_oneoff_control, fault):
    case = v2_oneoff_control
    case.observations["fail_create"] = fault == "command-failure"
    case.observations["lose_create_response"] = fault == "lost-response"
    with pytest.raises(case.host.module.LauncherError):
        case.control.create()
    with pytest.raises(case.host.module.LauncherError):
        case.control.create()
    assert [kind for kind, _ in case.events] == ["create"]
    assert case.control.container_id is None


def test_v2_oneoff_requires_external_creation_approval(v2_oneoff_control):
    case = v2_oneoff_control
    case.observations["allow"] = False
    with pytest.raises(case.host.module.LauncherError):
        case.control.create()
    assert case.events == []


def test_v2_oneoff_revalidates_start_approval_and_remote_worker_exit(v2_oneoff_control):
    case = v2_oneoff_control
    case.control.create()
    case.observations["allow"] = False
    with pytest.raises(case.host.module.LauncherError):
        case.control.start()
    assert not any(kind == "start" for kind, _ in case.events)
    case.observations["allow"] = True
    case.control.start()
    assert case.control.worker_exit() is None
    case.observations["phase"] = "exited"
    case.observations["worker_code"] = 3
    assert case.control.worker_exit() == 3
    assert case.control.worker_exit() != 0


@pytest.mark.parametrize("fault", ["checkpoint", "expiry", "approval"])
def test_v2_oneoff_start_checks_after_inspect(v2_oneoff_control, monkeypatch, fault):
    case = v2_oneoff_control
    case.control.create()
    history = case.host.apparatus["ex10_history_authority"]
    clock = {"now": datetime.now(UTC)}
    monkeypatch.setattr(history, "_now", lambda: clock["now"].strftime("%Y-%m-%dT%H:%M:%S.%fZ"))

    def alter(value):
        if value["State"]["Status"] != "created":
            return
        if fault == "checkpoint":
            tip, count = case.host.checkpoint["value"]
            case.host.checkpoint["value"] = (tip, count + 1)
        elif fault == "expiry":
            clock["now"] += timedelta(seconds=40)
        else:
            case.observations["allow"] = False

    case.observations["alter"] = alter
    expected = {
        "checkpoint": (case.host.apparatus["ex10_session_v2"].OfficialSessionError, "^history$"),
        "expiry": (
            case.host.apparatus["ex10_authority_sqlite"].AuthorityError,
            "^EXPIRED_OBSERVATION$",
        ),
        "approval": (case.host.module.LauncherError, "^native-oneoff-approval-required$"),
    }
    error, code = expected[fault]
    with pytest.raises(error, match=code):
        case.control.start()
    assert not any(kind == "start" for kind, _ in case.events)


def test_v2_oneoff_added_capability_never_starts(v2_oneoff_control):
    case = v2_oneoff_control
    case.control.create()
    case.observations["alter"] = lambda value: value["HostConfig"].update(CapAdd=["SYS_ADMIN"])
    with pytest.raises(case.host.module.LauncherError):
        case.control.start()
    assert not any(kind == "start" for kind, _ in case.events)


def test_v2_oneoff_start_concurrent_calls_issue_one_mutation(v2_oneoff_control):
    import threading

    case = v2_oneoff_control
    case.control.create()
    observed, release = threading.Event(), threading.Event()
    errors = []
    owner = {}

    def pause(value):
        if threading.get_ident() == owner.get("ident") and value["State"]["Status"] == "created":
            observed.set()
            assert release.wait(20), "LOCAL TEST owner not released"

    case.observations["alter"] = pause

    def start_owner():
        owner["ident"] = threading.get_ident()
        try:
            case.control.start()
        except Exception as exc:
            errors.append(exc)

    first = threading.Thread(target=start_owner)
    first.start()
    try:
        assert observed.wait(20), "LOCAL TEST owner did not reach inspection"
        with pytest.raises(case.host.module.LauncherBusy):
            case.control.start()
    finally:
        release.set()
        first.join(20)
    assert not first.is_alive()
    assert errors == []
    assert sum(kind == "start" for kind, _ in case.events) == 1


@pytest.fixture
def v2_action_intent(v2_native_host):
    host = v2_native_host
    host.module.execute(host.invocation())
    invocation = host.invocation(
        "start-official", confirmed=True, nonce="c" * 64, preflight_report=host.profile_path
    )
    bootstrap = invocation.native_bootstrap
    original = bootstrap.load(invocation)
    observed = {"allow": True, "calls": 0}

    def approve(**facts):
        assert observed["allow"], "LOCAL TEST independent action approval denied"
        assert facts["session"]._bytes == original._bytes
        assert facts["action"] == "start-official"
        assert facts["container_name"] == "s11-owned-intent-test"
        assert facts["output_dir"] == "actions/owned-intent-test"
        observed["calls"] += 1
        if observed.get("hook") is not None:
            observed["hook"]()

    intent = host.module.NativeActionIntent(
        invocation=invocation,
        bootstrap=bootstrap,
        container_name="s11-owned-intent-test",
        output_dir="actions/owned-intent-test",
        approve_action=approve,
    )
    yield SimpleNamespace(
        host=host,
        invocation=invocation,
        bootstrap=bootstrap,
        original=original,
        intent=intent,
        observed=observed,
    )


def test_v2_intent_actual_global_writer_busy_preserves_owner_and_pending_state(v2_action_intent):
    case = v2_action_intent
    before = _v2_private_tree_bytes(case.host.home)
    checkpoint = dict(case.host.checkpoint)
    with case.bootstrap.writer() as owner:
        owner_fd = owner.fd
        with pytest.raises(case.host.module.LauncherBusy, match="^launcher-busy$"):
            case.intent.begin()
        assert owner.fd == owner_fd
        owner.check()
        assert _v2_private_tree_bytes(case.host.home) == before
        assert case.host.checkpoint == checkpoint
        assert case.observed["calls"] == 0
        assert case.intent.attempted and not case.intent._mutation_lock.locked()
        with pytest.raises(
            case.host.module.LauncherError, match="^native-intent-second-attempt-denied$"
        ):
            case.intent.begin()
        owner.check()
    assert _v2_private_tree_bytes(case.host.home) == before
    assert case.host.checkpoint == checkpoint and case.observed["calls"] == 0


def test_v2_intent_later_authority_busy_is_not_global_acquisition_busy(v2_action_intent):
    case = v2_action_intent
    sql = case.bootstrap.apparatus["ex10_authority_sqlite"]
    before = _v2_private_tree_bytes(case.host.home)
    checkpoint = dict(case.host.checkpoint)

    def late_authority_failure():
        raise sql.AuthorityError("LOCK_BUSY")

    case.observed["hook"] = late_authority_failure
    with pytest.raises(
        case.host.module.LauncherError, match="^native-intent-approval-required$"
    ) as denied:
        case.intent.begin()
    assert not isinstance(denied.value, case.host.module.LauncherBusy)
    assert case.observed["calls"] == 1
    assert _v2_private_tree_bytes(case.host.home) == before
    assert case.host.checkpoint == checkpoint
    assert case.intent.attempted and not case.intent._mutation_lock.locked()
    with case.bootstrap.writer() as writer:
        writer.check()


def test_v2_intent_persists_actual_chain_checkpoint_and_stable_header(v2_action_intent):
    case = v2_action_intent
    replacement = case.intent.begin()
    value = replacement.to_dict()
    assert replacement.header == case.original.header
    assert replacement._expected_count == case.original._expected_count + 1
    assert case.host.checkpoint["value"] == (replacement._expected_tip, replacement._expected_count)
    assert value["in_flight"][0]["action"] == "start-official"
    assert value["in_flight"][0]["after_seq"] == case.original._expected_count
    assert value["state"] == {
        "known_phase": None,
        "uncertain": True,
        "blocked": True,
        "next_allowed_actions": ["inspect"],
    }
    assert case.bootstrap.load(case.invocation)._bytes == replacement._bytes
    assert case.host.checkpoint["publishes"] == 2
    with pytest.raises(case.host.module.LauncherError, match="native-intent-second-attempt-denied"):
        case.intent.begin()
    assert case.host.checkpoint["publishes"] == 2


def test_v2_intent_external_approval_denial_is_read_only(v2_action_intent):
    case = v2_action_intent
    before = _v2_private_tree_bytes(case.host.home)
    case.observed["allow"] = False
    with pytest.raises(case.host.module.LauncherError, match="native-intent-approval-required"):
        case.intent.begin()
    assert _v2_private_tree_bytes(case.host.home) == before


def test_v2_intent_checkpoint_loss_retains_inflight_without_retry(v2_action_intent):
    case = v2_action_intent
    case.host.checkpoint["lose_publication"] = True
    with pytest.raises(case.host.module.LauncherError, match="^native-write-uncertain$"):
        case.intent.begin()
    raw = (case.host.root / case.host.module.SESSION_FILE).read_bytes()
    value = json.loads(raw)
    assert value["in_flight"] and value["state"]["uncertain"]
    assert case.host.checkpoint["value"] == (
        case.original._expected_tip,
        case.original._expected_count,
    )
    with pytest.raises(case.host.module.LauncherError, match="native-intent-second-attempt-denied"):
        case.intent.begin()
    assert (case.host.root / case.host.module.SESSION_FILE).read_bytes() == raw


def test_v2_intent_approval_cannot_replace_session(v2_action_intent):
    case = v2_action_intent
    path = case.host.root / case.host.module.SESSION_FILE
    original = path.read_bytes()
    case.observed["hook"] = lambda: path.write_bytes(original + b"\n")
    with pytest.raises(case.host.module.LauncherError, match="native-intent-record-changed"):
        case.intent.begin()
    assert path.read_bytes() == original + b"\n"
    assert case.host.checkpoint["publishes"] == 1


def test_v2_intent_checks_actual_guard_after_clock(v2_action_intent):
    case = v2_action_intent

    def clock():
        held = case.host.guard.with_name("held-original.lock")
        case.host.guard.rename(held)
        case.host.guard.write_bytes(b"")
        case.host.guard.chmod(0o600)
        return datetime.now(UTC)

    case.invocation.clock = clock
    raw = (case.host.root / case.host.module.SESSION_FILE).read_bytes()
    with pytest.raises(case.host.apparatus["ex10_authority_sqlite"].AuthorityError):
        case.intent.begin()
    assert (case.host.root / case.host.module.SESSION_FILE).read_bytes() == raw
    assert case.host.checkpoint["publishes"] == 1


def test_v2_intent_existing_pending_attempt_blocks_another_instance(v2_action_intent):
    case = v2_action_intent
    case.intent.begin()
    other = case.host.module.NativeActionIntent(
        invocation=case.invocation,
        bootstrap=case.bootstrap,
        container_name="s11-another-intent",
        output_dir="actions/another-intent",
        approve_action=lambda **facts: None,
    )
    before = _v2_private_tree_bytes(case.host.home)
    with pytest.raises(case.host.module.LauncherError, match="native-intent-state-blocked"):
        other.begin()
    assert _v2_private_tree_bytes(case.host.home) == before


def test_v2_intent_requires_exact_confirmation_before_fences(v2_action_intent):
    case = v2_action_intent
    case.invocation.confirmed = False
    before = _v2_private_tree_bytes(case.host.home)
    with pytest.raises(case.host.module.UsageError):
        case.intent.begin()
    assert _v2_private_tree_bytes(case.host.home) == before


@pytest.mark.parametrize("boundary", ["update", "publish", "return", "exit"])
def test_v2_intent_expiry_after_last_session_io(v2_action_intent, monkeypatch, boundary):
    import contextlib

    case = v2_action_intent
    module = case.host.module
    history = case.host.apparatus["ex10_history_authority"]
    clock = {"now": datetime.now(UTC)}
    monkeypatch.setattr(history, "_now", lambda: clock["now"].strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    real_lock = module._native_session_lock
    calls = []
    target = {"update": 1, "publish": 2, "return": 3, "exit": 4}[boundary]

    @contextlib.contextmanager
    def observed_lock(*args, **kwargs):
        with real_lock(*args, **kwargs) as current:

            def checked():
                current()
                calls.append(True)
                if len(calls) == target:
                    clock["now"] += timedelta(seconds=40)

            yield checked
        if boundary == "exit":
            clock["now"] += timedelta(seconds=40)

    monkeypatch.setattr(module, "_native_session_lock", observed_lock)
    with pytest.raises(
        case.host.apparatus["ex10_authority_sqlite"].AuthorityError, match="^EXPIRED_OBSERVATION$"
    ):
        case.intent.begin()
    value = json.loads((case.host.root / module.SESSION_FILE).read_bytes())
    if boundary == "update":
        assert value == case.original.to_dict()
    else:
        assert value["in_flight"] and value["state"]["uncertain"]
    if boundary in {"return", "exit"}:
        assert case.host.checkpoint["publishes"] == 2
    else:
        assert case.host.checkpoint["value"] == (
            case.original._expected_tip,
            case.original._expected_count,
        )
        assert case.host.checkpoint["publishes"] == 1


def test_v2_intent_is_durable_before_oneoff_create(v2_action_intent):
    import uuid

    case = v2_action_intent
    pending = case.intent.begin()
    profile = case.host.apparatus["ex10_runtime_profile"]
    cfg = {
        "action": "start",
        "exchange_id": str(uuid.uuid4()),
        "profile_sha256": profile.runtime_profile_sha256(case.host.profile),
        "header_sha256": pending.to_dict()["header_sha256"],
        "bridge_pin": {},
        "witness_pin": {},
        "b3_pins": [],
        "genesis_digest": "a" * 64,
        "genesis_binding": {},
        "authority_revision": 0,
        "request_payload": {},
        "expected_reservation": None,
        "host_root_binding_sha256": "b" * 64,
        "source_hashes": {},
        "staging_name": "application-preflight." + str(uuid.uuid4()) + ".json",
        "command_options": {
            "expected_git_sha": case.invocation.expected_git_sha,
            "source_root": "/s11-full-source",
            "nonce": case.invocation.nonce,
            "acknowledge_not_demonstrated": [],
        },
        "authorization_reference": {},
    }
    # This unit boundary deliberately supplies synthetic opaque trust bodies;
    # it proves durable intent before create, never a native worker permission.
    seen = []
    cid = "4" * 64

    class DockerObservation:
        def __init__(self, cwd, env):
            assert cwd == case.invocation.repo_root

        def run(self, argv, *, timeout):
            seen.append(argv)
            assert argv[:3] == ("/usr/bin/docker", "container", "create")
            assert case.bootstrap.load(case.invocation)._bytes == pending._bytes
            assert case.host.checkpoint["value"] == (pending._expected_tip, pending._expected_count)
            return case.host.module.Completed(0, (cid + "\n").encode(), b"")

    def approve_creation(**facts):
        assert facts["session"]._bytes == pending._bytes
        assert facts["name"] == case.intent.name
        assert facts["bootstrap_bytes"] == case.host.module.base.canonical_bytes(cfg)

    oneoff = case.host.module.NativeOneOffDocker(
        invocation=case.invocation,
        bootstrap=case.bootstrap,
        name=case.intent.name,
        image_digest="sha256:" + "5" * 64,
        network_id="6" * 64,
        bootstrap_bytes=case.host.module.base.canonical_bytes(cfg),
        service_environment={},
        owner_labels={"s11.owner.session": pending.header["session_id"]},
        approve_creation=approve_creation,
        runner_factory=DockerObservation,
    )
    assert oneoff.create() == cid
    with pytest.raises(case.host.module.LauncherError, match="native-oneoff-second-create-denied"):
        oneoff.create()
    assert len(seen) == 1
    assert case.bootstrap.load(case.invocation)._bytes == pending._bytes


@pytest.fixture
def v2_exclusive_proof_publication(v2_action_intent):
    """Actual private namespace/B1/B2/B3; payload is data-only, not scientific permission."""
    import uuid

    case = v2_action_intent
    case.intent.begin()
    evidence = Path(case.host.profile["host_roots"]["evidence"]["path"])
    operational = evidence / ".s11-operational"
    operational.mkdir(mode=0o700)
    exchange_id = str(uuid.uuid4())
    directory = operational / exchange_id
    directory.mkdir(mode=0o700)
    durable = case.host.apparatus["ex10_authority_backend"]
    info = directory.stat()
    identity = durable.FileIdentity(info.st_dev, info.st_ino, info.st_uid, 0o700)
    store = durable.PrivateStore(str(directory), identity)
    try:
        publisher = case.host.module.NativeExclusiveProofPublication(
            invocation=case.invocation,
            bootstrap=case.bootstrap,
            store=store,
            exchange_id=exchange_id,
        )
        yield SimpleNamespace(
            case=case,
            directory=directory,
            store=store,
            publisher=publisher,
            exchange_id=exchange_id,
            raw=b'{"fixture":"LOCAL TEST publication bytes only"}\n',
        )
    finally:
        store.close()


def test_v2_proof_publication_complete_bytes_atomic_destination(
    v2_exclusive_proof_publication, monkeypatch
):
    x = v2_exclusive_proof_publication
    destination = x.directory / "emission-material.json"
    write = os.write
    observations = []

    def partial(fd, raw):
        assert not destination.exists()
        result = write(fd, raw[:7])
        observations.append(result)
        return result

    monkeypatch.setattr(x.case.host.module.os, "write", partial)
    x.publisher.publish("emission-material", x.raw)
    assert len(observations) > 1
    assert destination.read_bytes() == x.raw
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert destination.stat().st_nlink == 1
    assert not tuple(x.directory.glob(".s11-proof-*.tmp"))
    with pytest.raises(
        x.case.host.module.LauncherError, match="native-proof-second-attempt-denied"
    ):
        x.publisher.publish("emission-material", x.raw)


def test_v2_proof_publication_existing_destination_keeps_original(v2_exclusive_proof_publication):
    x = v2_exclusive_proof_publication
    destination = x.directory / "emission-material.json"
    destination.write_bytes(b'{"existing":"must be retained"}\n')
    destination.chmod(0o600)
    before = destination.stat()
    with pytest.raises(x.case.host.module.LauncherError, match="native-proof-publication-conflict"):
        x.publisher.publish("emission-material", x.raw)
    after = destination.stat()
    assert destination.read_bytes() == b'{"existing":"must be retained"}\n'
    assert (before.st_ino, before.st_ctime_ns, before.st_mtime_ns) == (
        after.st_ino,
        after.st_ctime_ns,
        after.st_mtime_ns,
    )
    stage = tuple(x.directory.glob(".s11-proof-*.tmp"))
    assert len(stage) == 1 and stage[0].read_bytes() == x.raw


def test_v2_proof_publication_partial_failure_retains_stage_without_retry(
    v2_exclusive_proof_publication, monkeypatch
):
    x = v2_exclusive_proof_publication
    genuine, count = os.write, [0]

    def interrupted(fd, raw):
        count[0] += 1
        if count[0] == 1:
            return genuine(fd, raw[:5])
        raise OSError("LOCAL TEST injected write failure")

    monkeypatch.setattr(x.case.host.module.os, "write", interrupted)
    with pytest.raises(
        x.case.host.module.LauncherError, match="native-proof-publication-uncertain"
    ):
        x.publisher.publish("emission-material", x.raw)
    assert not (x.directory / "emission-material.json").exists()
    stage = tuple(x.directory.glob(".s11-proof-*.tmp"))
    assert len(stage) == 1 and stage[0].read_bytes() == x.raw[:5]
    with pytest.raises(
        x.case.host.module.LauncherError, match="native-proof-second-attempt-denied"
    ):
        x.publisher.publish("emission-material", x.raw)
    assert count[0] == 2


@pytest.mark.parametrize("raw", [b"{} \\n", b"", b"{", "{}"])
def test_v2_proof_publication_invalid_bytes_have_no_storage_effect(
    v2_exclusive_proof_publication, raw
):
    x = v2_exclusive_proof_publication
    with pytest.raises(x.case.host.module.LauncherError, match="native-proof-data-invalid"):
        x.publisher.publish("emission-head", raw)
    assert tuple(x.directory.iterdir()) == ()


@pytest.mark.parametrize("role", ["../header", "preflight", "repair", "emission-head.json", None])
def test_v2_proof_publication_closed_roles(v2_exclusive_proof_publication, role):
    x = v2_exclusive_proof_publication
    with pytest.raises(x.case.host.module.LauncherError, match="native-proof-role-invalid"):
        x.publisher.publish(role, x.raw)
    assert tuple(x.directory.iterdir()) == ()


def test_v2_proof_publication_stage_replacement_preserves_birth_identity(
    v2_exclusive_proof_publication, monkeypatch
):
    x = v2_exclusive_proof_publication
    genuine = x.publisher.recheck
    checks = [0]

    def replaced():
        genuine()
        checks[0] += 1
        if checks[0] == 2:
            (stage,) = x.directory.glob(".s11-proof-*.tmp")
            raw = stage.read_bytes()
            stage.rename(x.directory / "retained-original.tmp")
            stage.write_bytes(raw)
            stage.chmod(0o600)

    monkeypatch.setattr(x.publisher, "recheck", replaced)
    with pytest.raises(x.case.host.module.LauncherError, match="native-proof-publication-changed"):
        x.publisher.publish("emission-material", x.raw)
    assert not (x.directory / "emission-material.json").exists()
    assert (x.directory / "retained-original.tmp").read_bytes() == x.raw


def test_v2_proof_publication_directory_fsync_failure_keeps_published_file(
    v2_exclusive_proof_publication, monkeypatch
):
    x = v2_exclusive_proof_publication
    genuine = os.fsync

    def uncertain(fd):
        if fd == x.store.fd:
            raise OSError("LOCAL TEST directory durability unknown after rename")
        return genuine(fd)

    monkeypatch.setattr(x.case.host.module.os, "fsync", uncertain)
    with pytest.raises(
        x.case.host.module.LauncherError, match="native-proof-publication-uncertain"
    ):
        x.publisher.publish("emission-material", x.raw)
    assert (x.directory / "emission-material.json").read_bytes() == x.raw
    assert not tuple(x.directory.glob(".s11-proof-*.tmp"))
    with pytest.raises(
        x.case.host.module.LauncherError, match="native-proof-second-attempt-denied"
    ):
        x.publisher.publish("emission-material", x.raw)


def test_v2_proof_publication_unsupported_rename_has_no_fallback(
    v2_exclusive_proof_publication, monkeypatch
):
    x = v2_exclusive_proof_publication
    calls = []

    def unsupported(*args):
        calls.append(args)
        x.publisher.ctypes.set_errno(38)
        return -1

    monkeypatch.setattr(x.publisher, "rename", unsupported)
    with pytest.raises(
        x.case.host.module.LauncherError, match="native-proof-publication-uncertain"
    ):
        x.publisher.publish("emission-material", x.raw)
    assert len(calls) == 1 and calls[0][-1] == 1
    assert not (x.directory / "emission-material.json").exists()
    (stage,) = x.directory.glob(".s11-proof-*.tmp")
    assert stage.read_bytes() == x.raw
    with pytest.raises(
        x.case.host.module.LauncherError, match="native-proof-second-attempt-denied"
    ):
        x.publisher.publish("emission-material", x.raw)
    assert len(calls) == 1


def test_v2_proof_publication_symlink_destination_never_followed(v2_exclusive_proof_publication):
    x = v2_exclusive_proof_publication
    marker = x.directory / "retained-marker.json"
    marker.write_bytes(b"retained marker\n")
    marker.chmod(0o600)
    target = x.directory / "emission-material.json"
    target.symlink_to(marker.name)
    before = marker.stat()
    with pytest.raises(x.case.host.module.LauncherError, match="native-proof-publication-conflict"):
        x.publisher.publish("emission-material", x.raw)
    assert target.is_symlink() and os.readlink(target) == marker.name
    assert marker.read_bytes() == b"retained marker\n"
    assert (marker.stat().st_ino, marker.stat().st_ctime_ns) == (before.st_ino, before.st_ctime_ns)


def test_v2_proof_publication_two_instances_cannot_adopt_or_replace(v2_exclusive_proof_publication):
    x = v2_exclusive_proof_publication
    other = x.case.host.module.NativeExclusiveProofPublication(
        invocation=x.case.invocation,
        bootstrap=x.case.bootstrap,
        store=x.store,
        exchange_id=x.exchange_id,
    )
    x.publisher.publish("emission-material", x.raw)
    target = x.directory / "emission-material.json"
    before = target.stat()
    with pytest.raises(x.case.host.module.LauncherError, match="native-proof-publication-conflict"):
        other.publish("emission-material", x.raw)
    assert target.read_bytes() == x.raw
    assert (target.stat().st_ino, target.stat().st_ctime_ns) == (before.st_ino, before.st_ctime_ns)
    (stage,) = x.directory.glob(".s11-proof-*.tmp")
    assert stage.read_bytes() == x.raw


def test_v2_proof_publication_concurrent_same_role_rejected_before_second_stage(
    v2_exclusive_proof_publication, monkeypatch
):
    import threading

    x = v2_exclusive_proof_publication
    reached, release = threading.Event(), threading.Event()
    genuine = x.publisher.rename
    results = []

    def delayed(*args):
        reached.set()
        assert release.wait(15), "LOCAL TEST held rename not released"
        return genuine(*args)

    monkeypatch.setattr(x.publisher, "rename", delayed)

    def first():
        try:
            x.publisher.publish("emission-material", x.raw)
            results.append("published")
        except BaseException as exc:
            results.append(exc)

    thread = threading.Thread(target=first)
    thread.start()
    try:
        assert reached.wait(15), "LOCAL TEST first publication did not reach rename"
        stages = tuple(x.directory.glob(".s11-proof-*.tmp"))
        assert len(stages) == 1 and stages[0].read_bytes() == x.raw
        with pytest.raises(x.case.host.module.LauncherBusy):
            x.publisher.publish("emission-material", x.raw)
        assert tuple(x.directory.glob(".s11-proof-*.tmp")) == stages
    finally:
        release.set()
        thread.join(20)
    assert not thread.is_alive() and results == ["published"]
    assert (x.directory / "emission-material.json").read_bytes() == x.raw


def test_v2_proof_publication_destination_replaced_during_final_fsync(
    v2_exclusive_proof_publication, monkeypatch
):
    x = v2_exclusive_proof_publication
    genuine = os.fsync

    def replaced(fd):
        result = genuine(fd)
        if fd == x.store.fd:
            target = x.directory / "emission-material.json"
            target.rename(x.directory / "retained-published.json")
            target.write_bytes(x.raw)
            target.chmod(0o600)
        return result

    monkeypatch.setattr(x.case.host.module.os, "fsync", replaced)
    with pytest.raises(x.case.host.module.LauncherError, match="native-proof-publication-changed"):
        x.publisher.publish("emission-material", x.raw)
    assert (x.directory / "retained-published.json").read_bytes() == x.raw
    assert (x.directory / "emission-material.json").read_bytes() == x.raw


@pytest.mark.parametrize("boundary", ["constructor", "publication"])
def test_v2_proof_publication_operational_parent_must_remain_private(
    v2_exclusive_proof_publication, boundary
):
    x = v2_exclusive_proof_publication
    parent = x.directory.parent
    parent.chmod(0o755)
    try:
        with pytest.raises(
            x.case.host.module.LauncherError, match="native-proof-namespace-changed"
        ):
            if boundary == "constructor":
                x.case.host.module.NativeExclusiveProofPublication(
                    invocation=x.case.invocation,
                    bootstrap=x.case.bootstrap,
                    store=x.store,
                    exchange_id=x.exchange_id,
                )
            else:
                x.publisher.publish("emission-material", x.raw)
        assert tuple(x.directory.iterdir()) == ()
    finally:
        parent.chmod(0o700)


def test_v2_proof_publication_birth_mapping_after_last_write(
    v2_exclusive_proof_publication, monkeypatch
):
    x = v2_exclusive_proof_publication
    genuine = os.write
    replaced = []

    def replaced_mapping(fd, raw):
        count = genuine(fd, raw)
        if count == len(raw) and not replaced:
            (stage,) = x.directory.glob(".s11-proof-*.tmp")
            stage.rename(x.directory / "retained-birth-inode.tmp")
            stage.write_bytes(x.raw)
            stage.chmod(0o600)
            replaced.append(stage)
        return count

    monkeypatch.setattr(x.case.host.module.os, "write", replaced_mapping)
    with pytest.raises(x.case.host.module.LauncherError, match="native-proof-publication-changed"):
        x.publisher.publish("emission-material", x.raw)
    assert replaced and not (x.directory / "emission-material.json").exists()
    assert (x.directory / "retained-birth-inode.tmp").read_bytes() == x.raw


def test_v2_proof_publication_no_visible_partial_bytes_at_write_boundary(
    v2_exclusive_proof_publication, monkeypatch
):
    x = v2_exclusive_proof_publication
    genuine = os.write
    target = x.directory / "emission-material.json"
    visible, errors = [], []

    def observe(fd, raw):
        visible.append(target.read_bytes() if target.exists() else None)
        count = genuine(fd, raw[:7])
        visible.append(target.read_bytes() if target.exists() else None)
        return count

    monkeypatch.setattr(x.case.host.module.os, "write", observe)
    try:
        x.publisher.publish("emission-material", x.raw)
    except x.case.host.module.LauncherError as error:
        errors.append(error.code)
    assert visible and all(raw is None for raw in visible), "Incomplete proof visible during write"
    assert errors == []
    assert target.read_bytes() == x.raw


def test_v2_proof_publication_actual_executor_reader_consumes_exact_profile(
    v2_exclusive_proof_publication,
):
    """Actual fixed reader/storage integration; reading profile grants no scientific permission."""
    from apps.experiments_app import ex10_executor as executor

    x = v2_exclusive_proof_publication
    authority = x.case.host.authority
    sql = x.case.host.apparatus["ex10_authority_sqlite"]
    raw = x.case.host.profile_path.read_bytes()
    x.publisher.publish("profile", raw)
    reader = executor.ExecutorHostProofs(
        root=x.case.host.profile["host_roots"]["evidence"]["path"],
        exchange_id=x.exchange_id,
        b3_pins=[authority.pin.record],
        genesis_digest=sql.digest(authority.receipt),
        genesis_binding=authority.receipt["genesis"],
        clock=lambda: datetime.now(UTC),
    )
    value, copied = reader.read("profile")
    assert copied == raw and value == x.case.host.profile
    assert value["schema_version"] == executor.profile_codec.SCHEMA_VERSION


@pytest.fixture
def v2_bridge_proof_signer():
    """Actual frozen 9/5-frame apparatus with fixed synthetic signing providers."""
    from apps.experiments_app.tests import test_ex10_executor as helper

    with helper.v2_native_host_material() as fixture:
        rig = helper.v2_native_start_setup(
            fixture, helper.dataset.__wrapped__(), client_proofs=True
        )
        mod, a = fixture.module, fixture.rig.a
        source = rig.broker.sources
        assert rig.client_proof_bindings is not None
        proof = rig.client_proof_bindings.proof
        directory = rig.store.root / ".s11-operational" / proof.exchange_id
        directory.parent.mkdir(mode=0o700, exist_ok=True)
        directory.mkdir(mode=0o700)
        private = mod.op.durable.PrivateStore(str(directory), mod.h.identity(directory))
        a.stores["w09-signer-proof-" + proof.exchange_id] = private
        copied, signed = [], []

        def publish(role, raw):
            # Fixed test construction, actual durable store. Production must
            # select NativeExclusiveProofPublication explicitly in its factory.
            assert private.create_exact(str(directory / (role + ".json")), raw) is None
            copied.append(role)

        class RecordingKey:
            def public_key(self):
                return a.bridge.public_key()

            def sign(self, data):
                signed.append(data)
                return a.bridge.sign(data)

        signer = official.NativeBridgeProofSigner(
            apparatus={"ex10_operational_context": mod.op},
            signer=RecordingKey(),
            bridge_pin=a.bridge_pin,
            action="start",
            exchange_id=rig.host.exchange_id,
            expected_run_id=rig.context.proposal["run_id"],
            admitted=fixture.native.qualifier.qualify(),
            publish_role=publish,
        )
        new = mod.op.NativeHostSources(
            session_qualifier=source.sessions,
            registry=source.registry,
            policy=source.policy,
            authority_reader=source.reader,
            historical_contexts=source.histories,
            artifact_store=source.artifacts,
            certificate_store=source.certificates,
            publisher=source.publisher,
            executor_fingerprint=source.fingerprint,
            signer=signer,
            bridge_pin=source.pin,
        )
        new.verify_registry(canonical_binding=a.journal.binding, journal=a.journal)
        a.journal.sources = new
        fixture.source.source = new
        signer.bind_once(sources=new, journal=a.journal)
        rig.broker = mod.op.NativeBridgeBroker(
            journal=a.journal,
            session_qualifier=fixture.native.qualifier,
            signer=signer,
            host_sources=new,
            holder_admitter=lambda **kw: a.holder.public_key(),
        )
        value = SimpleNamespace(
            rig=rig,
            fixture=fixture,
            helper=helper,
            signer=signer,
            proof=proof,
            copied=copied,
            signed=signed,
            directory=directory,
            private=private,
        )
        try:
            yield value
        finally:
            rig.context.channel.close()
            rig.host.close()


def test_v2_proof_signer_actual_nine_frames_without_frame_replacement(v2_bridge_proof_signer):
    x = v2_bridge_proof_signer
    mod, a = x.fixture.module, x.fixture.rig.a
    assert x.rig.broker._frame.__func__ is mod.op.NativeBridgeBroker._frame
    result, errors, responses = x.helper.v2_run_native_host(
        x.rig, lambda: x.helper.v2_start_call(x.rig)
    )
    assert errors == [] and len(responses) == 1
    assert result["run_id"] == x.rig.context.proposal["run_id"]
    assert x.rig.context.finished and x.rig.context.channel.position == 9
    assert a.transport.calls.count("RESERVE") == 1
    assert x.rig.broker.sources.publisher.transport.calls.count("B3_CAS") == 1
    assert a.transport.calls.count("COMMIT_OPEN") == 1
    assert x.copied == [
        "base-material",
        "base-head",
        "commit-material",
        "commit-head",
        "commit-coordination",
        "commit-receipt",
    ]
    kinds = [
        next(k for k, domain in mod.wire.DOMAINS.items() if raw.startswith(domain))
        for raw in x.signed
    ]
    assert kinds == [
        "host_challenge",
        "capability",
        "open_one_permit",
        "response",
        "capability",
        "response",
    ]


def v2_signer_challenge_preimage(x, **changes):
    mod, a = x.fixture.module, x.fixture.rig.a
    if x.signer.sources.channel is None:
        x.signer.sources.begin_exchange(channel=x.rig.host)
    now = a.journal._now()
    value = {
        "schema_version": "s11-ex10-b4-host-challenge-v1",
        **x.signer._fixed(),
        "challenge": "a" * 64,
        "issued_at": mod.op.coordination._utc(now),
        "expires_at": mod.op.coordination._utc(now + timedelta(seconds=120)),
        **changes,
        "signature": "0" * 128,
    }
    parsed = mod.wire._parse(
        "host_challenge", mod.op.sql.canonical_bytes(value), maximum=mod.wire.MAX_RESPONSE_BYTES
    )
    return mod.wire.signing_preimage(parsed)


@pytest.mark.parametrize(
    "field,value",
    [
        ("container_identity_digest", "e" * 64),
        ("deployment_verification_sha256", "e" * 64),
        ("session_header_sha256", "e" * 64),
        ("runtime_profile_sha256", "e" * 64),
        ("source_revision", "e" * 40),
        ("exchange_id", "00000000-0000-4000-8000-000000000001"),
    ],
)
def test_v2_proof_signer_exact_admitted_challenge_before_publication(
    v2_bridge_proof_signer, field, value
):
    x = v2_bridge_proof_signer
    preimage = v2_signer_challenge_preimage(x, **{field: value})
    with pytest.raises(official.LauncherError, match="native-proof"):
        x.signer.sign(preimage)
    assert x.copied == [] and x.signed == []
    with pytest.raises(official.LauncherError, match="native-proof-signer-consumed"):
        x.signer.sign(v2_signer_challenge_preimage(x))
    assert x.copied == [] and x.signed == []


@pytest.mark.parametrize("domain", ["request", "reservation_receipt", "coordination_head"])
def test_v2_proof_signer_rejects_foreign_signing_domain(v2_bridge_proof_signer, domain):
    x = v2_bridge_proof_signer
    preimage = x.fixture.module.wire.DOMAINS[domain] + b"{}"
    with pytest.raises(official.LauncherError, match="native-proof-signing-domain"):
        x.signer.sign(preimage)
    assert x.copied == [] and x.signed == [] and x.signer.failed


def test_v2_proof_signer_duplicate_challenge_is_consumed(v2_bridge_proof_signer):
    x = v2_bridge_proof_signer
    preimage = v2_signer_challenge_preimage(x)
    signature = x.signer.sign(preimage)
    x.signer.public_key().verify(signature, preimage)
    assert x.copied == ["base-material", "base-head"] and len(x.signed) == 1
    with pytest.raises(official.LauncherError, match="native-proof-signing-order"):
        x.signer.sign(preimage)
    assert len(x.signed) == 1 and len(x.copied) == 2


def test_v2_proof_signer_bind_cannot_be_repeated(v2_bridge_proof_signer):
    x = v2_bridge_proof_signer
    with pytest.raises(official.LauncherError, match="native-proof-signer-bind-once"):
        x.signer.bind_once(sources=x.signer.sources, journal=x.signer.journal)
    assert x.signed == [] and x.copied == []


def test_v2_proof_signer_publication_error_retains_files_and_denies_retry(
    v2_bridge_proof_signer, monkeypatch
):
    x = v2_bridge_proof_signer
    publish = x.signer.publish_role

    def uncertain(role, raw):
        publish(role, raw)
        raise OSError("synthetic publication uncertainty")

    monkeypatch.setattr(x.signer, "publish_role", uncertain)
    preimage = v2_signer_challenge_preimage(x)
    with pytest.raises(OSError, match="synthetic publication uncertainty"):
        x.signer.sign(preimage)
    assert x.copied == ["base-material"] and x.signed == []
    assert (x.directory / "base-material.json").is_file()
    with pytest.raises(official.LauncherError, match="native-proof-signer-consumed"):
        x.signer.sign(preimage)
    assert x.copied == ["base-material"] and x.signed == []


@pytest.mark.parametrize("failure", ["wrong-signature", "deadline-after-sign"])
def test_v2_proof_signer_real_signature_and_original_final_deadline(
    v2_bridge_proof_signer, monkeypatch, failure
):
    x = v2_bridge_proof_signer
    sign = x.signer.key.sign

    def changed(data):
        signature = sign(data)
        if failure == "wrong-signature":
            return b"\x00" * 64
        x.rig.host.deadline = x.helper.time.monotonic() - 1
        return signature

    monkeypatch.setattr(x.signer.key, "sign", changed)
    preimage = v2_signer_challenge_preimage(x)
    if failure == "wrong-signature":
        from cryptography.exceptions import InvalidSignature

        expected, match = InvalidSignature, None
    else:
        expected, match = x.fixture.module.op.OperationalError, "EXCHANGE_EXPIRED"
    with pytest.raises(expected, match=match):
        x.signer.sign(preimage)
    assert len(x.signed) == 1 and x.copied == ["base-material", "base-head"]
    assert x.signer.failed and x.signer.index == 0


def test_v2_proof_signer_actual_single_five_frames_without_frame_replacement(
    v2_bridge_proof_signer,
):
    x = v2_bridge_proof_signer
    _, errors, responses = x.helper.v2_run_native_host(x.rig, lambda: x.helper.v2_start_call(x.rig))
    assert not errors and len(responses) == 1
    single, copied, signer, new = v2_signed_single_setup(x)
    mod, a = x.fixture.module, x.fixture.rig.a
    assert single.broker._frame.__func__ is mod.op.NativeBridgeBroker._frame
    try:
        _, responses = x.helper.v2_run_single_host(
            single,
            lambda: single.context.authorize_step(
                single.store,
                action="execute",
                run_id=x.rig.context.proposal["run_id"],
                git_sha=x.rig.context.proposal["git_sha"],
                phase="PREPARED",
            ),
        )
        assert len(responses) == 1 and single.context.finished
        assert single.context.channel.position == 5 and signer.index == 3
        assert copied == ["action-material", "action-head", "action-coordination", "action-receipt"]
        assert a.transport.calls.count("RESERVE") == 1
        assert a.transport.calls.count("COMMIT_OPEN") == 1
        assert new.publisher.transport.calls.count("B3_CAS") == 1
    finally:
        single.host.close()
        single.context.channel.close()


def test_v2_proof_signer_rechecks_actual_profile_after_key_provider(
    v2_bridge_proof_signer, monkeypatch
):
    x = v2_bridge_proof_signer
    sign = x.signer.key.sign
    profile = Path(x.fixture.native.qualifier.registry.reference("profile")["path"])

    def change_profile(data):
        signature = sign(data)
        profile.chmod(0o640)
        return signature

    monkeypatch.setattr(x.signer.key, "sign", change_profile)
    preimage = v2_signer_challenge_preimage(x)
    try:
        with pytest.raises(x.fixture.module.op.OperationalError):
            x.signer.sign(preimage)
        assert len(x.signed) == 1 and x.signer.failed and x.signer.index == 0
    finally:
        profile.chmod(0o600)


def test_v2_proof_signer_retains_base_head_expiry_until_reserve_capability(
    v2_bridge_proof_signer, monkeypatch
):
    x = v2_bridge_proof_signer
    wire, a = x.fixture.module.wire, x.fixture.rig.a
    sign = x.signer.key.sign
    returned = []
    original_sign = x.signer.sign

    def key_delay(data):
        signature = sign(data)
        if data.startswith(wire.DOMAINS["capability"]):
            head = json.loads((x.directory / "base-head.json").read_bytes())
            expired = wire._time(head["expires_at"]) + timedelta(seconds=1)
            monkeypatch.setattr(a.journal, "clock", lambda: expired)
        return signature

    def observe_return(data):
        signature = original_sign(data)
        if data.startswith(wire.DOMAINS["capability"]):
            returned.append(data)
        return signature

    monkeypatch.setattr(x.signer.key, "sign", key_delay)
    monkeypatch.setattr(x.signer, "sign", observe_return)

    def operation():
        with pytest.raises(x.helper.executor.ExecutorBridgeError):
            x.helper.v2_start_call(x.rig)

    _, errors, responses = x.helper.v2_run_native_host(x.rig, operation)
    assert errors and not responses
    assert returned == [], "Expired original base-head authorized reserve capability"
    assert a.transport.calls.count("RESERVE") == 0
    assert x.copied == ["base-material", "base-head"] and x.signer.failed


def test_v2_proof_signer_rechecks_actual_policy_after_key_provider(
    v2_bridge_proof_signer, monkeypatch
):
    x = v2_bridge_proof_signer
    sign = x.signer.key.sign
    policy = Path(x.signer.sources.policy.registry.reference("policy")["path"])

    def change_policy(data):
        signature = sign(data)
        policy.chmod(0o640)
        return signature

    monkeypatch.setattr(x.signer.key, "sign", change_policy)
    preimage = v2_signer_challenge_preimage(x)
    try:
        with pytest.raises(x.fixture.module.op.OperationalError):
            x.signer.sign(preimage)
        assert len(x.signed) == 1 and x.signer.failed and x.signer.index == 0
    finally:
        policy.chmod(0o600)


def test_v2_proof_signer_bind_serializes_actual_provider_io(v2_bridge_proof_signer, monkeypatch):
    import threading

    x = v2_bridge_proof_signer
    mod, a, old = x.fixture.module, x.fixture.rig.a, x.signer.sources
    signer = official.NativeBridgeProofSigner(
        apparatus={"ex10_operational_context": mod.op},
        signer=x.signer.key,
        bridge_pin=a.bridge_pin,
        action="start",
        exchange_id=x.rig.host.exchange_id,
        expected_run_id=x.rig.context.proposal["run_id"],
        admitted=x.signer.admitted,
        publish_role=x.signer.publish_role,
    )
    new = mod.op.NativeHostSources(
        session_qualifier=old.sessions,
        registry=old.registry,
        policy=old.policy,
        authority_reader=old.reader,
        historical_contexts=old.histories,
        artifact_store=old.artifacts,
        certificate_store=old.certificates,
        publisher=old.publisher,
        executor_fingerprint=old.fingerprint,
        signer=signer,
        bridge_pin=old.pin,
    )
    new.verify_registry(canonical_binding=a.journal.binding, journal=a.journal)
    a.journal.sources = new
    entered, release = threading.Event(), threading.Event()
    postcheck = new.policy.postcheck
    results, errors = [], []

    def delayed(native):
        postcheck(native)
        entered.set()
        assert release.wait(timeout=10), "Synthetic binder release timed out"

    def first():
        try:
            results.append(signer.bind_once(sources=new, journal=a.journal))
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(new.policy, "postcheck", delayed)
    thread = threading.Thread(target=first)
    thread.start()
    try:
        assert entered.wait(timeout=10)
        with pytest.raises(official.LauncherError, match="native-proof-signer-busy"):
            signer.bind_once(sources=new, journal=a.journal)
    finally:
        release.set()
        thread.join(timeout=10)
        a.journal.sources = old
    assert not thread.is_alive() and errors == [] and results == [None]
    assert signer.sources is new and x.signed == [] and x.copied == []


def test_v2_proof_signer_deadline_after_last_real_pipe_guard(v2_bridge_proof_signer, monkeypatch):
    x = v2_bridge_proof_signer
    preimage = v2_signer_challenge_preimage(x)

    def delayed_guard():
        if x.signed:
            x.rig.host.deadline = x.helper.time.monotonic() - 1
        return None

    monkeypatch.setattr(x.rig.host, "guard", delayed_guard)
    with pytest.raises(official.LauncherError, match="native-proof-deadline"):
        x.signer.sign(preimage)
    assert len(x.signed) == 1 and x.signer.failed and x.signer.index == 0


def v2_signed_single_setup(x):
    single = x.helper.v2_native_single_setup(x.rig, action="execute", client_proofs=True)
    mod, a, source = x.fixture.module, x.fixture.rig.a, single.broker.sources
    assert single.client_proof_bindings is not None
    proof = single.client_proof_bindings.proof
    directory = single.store.root / ".s11-operational" / proof.exchange_id
    directory.mkdir(mode=0o700)
    private = mod.op.durable.PrivateStore(str(directory), mod.h.identity(directory))
    a.stores["w09-signer-single-" + proof.exchange_id] = private
    copied = []

    def publish(role, raw):
        assert private.create_exact(str(directory / (role + ".json")), raw) is None
        copied.append(role)

    signer = official.NativeBridgeProofSigner(
        apparatus={"ex10_operational_context": mod.op},
        signer=a.bridge,
        bridge_pin=a.bridge_pin,
        action="execute",
        exchange_id=single.host.exchange_id,
        expected_run_id=x.rig.context.proposal["run_id"],
        admitted=x.fixture.native.qualifier.qualify(),
        publish_role=publish,
    )
    new = mod.op.NativeHostSources(
        session_qualifier=source.sessions,
        registry=source.registry,
        policy=source.policy,
        authority_reader=source.reader,
        historical_contexts=source.histories,
        artifact_store=source.artifacts,
        certificate_store=source.certificates,
        publisher=source.publisher,
        executor_fingerprint=source.fingerprint,
        signer=signer,
        bridge_pin=source.pin,
    )
    new.verify_registry(canonical_binding=a.journal.binding, journal=a.journal)
    a.journal.sources = new
    signer.bind_once(sources=new, journal=a.journal)
    single.broker = mod.op.NativeBridgeBroker(
        journal=a.journal,
        session_qualifier=x.fixture.native.qualifier,
        signer=signer,
        host_sources=new,
        holder_admitter=single.broker.admit,
    )
    return single, copied, signer, new


@pytest.fixture
def v2_armed_custody_reader(v2_bridge_proof_signer):
    """Real synthetic START9/SINGLE5 and existing signed ARMED checkpoint."""
    import uuid
    from dataclasses import replace

    from apps.experiments_app import custody_policy

    x = v2_bridge_proof_signer
    _, errors, responses = x.helper.v2_run_native_host(x.rig, lambda: x.helper.v2_start_call(x.rig))
    assert not errors and len(responses) == 1
    single, _, _, _ = v2_signed_single_setup(x)
    run_id = x.rig.context.proposal["run_id"]
    harness = x.helper.make_harness("w09-armed-" + uuid.uuid4().hex, x.helper.dataset.__wrapped__())
    try:
        _, responses = x.helper.v2_run_single_host(
            single,
            lambda: single.context.authorize_step(
                single.store,
                action="execute",
                run_id=run_id,
                git_sha=x.rig.context.proposal["git_sha"],
                phase="PREPARED",
            ),
        )
        assert len(responses) == 1 and single.context.finished and single.host.closed
        probes = replace(
            harness.probes,
            now=x.fixture.rig.a.now,
            fingerprint=lambda: x.fixture.rig.sample["context"].executor_fingerprint,
        )
        x.helper.executor._checkpoint_started(
            single.store,
            run_id=run_id,
            git_sha=x.rig.context.proposal["git_sha"],
            probes=probes,
            dataset=harness.dataset,
            step="execute",
            source="s11_ex10.execute",
            operational_context=single.context,
        )
        x.helper.executor._arm_emission(
            single.store,
            run_id,
            x.rig.context.proposal["git_sha"],
            probes,
            harness.dataset,
            operational_context=single.context,
        )
        state = x.helper.executor.load_existing_state_readonly(single.store, run_id)
        assert state.has(x.helper.executor.ART_ARMED)
        assert x.helper.executor.phase_of(state) is x.helper.executor.Phase.EMITTING
        reader = official.NativeArmedArtifactReader(
            apparatus={
                "ex10_operational_context": x.fixture.module.op,
                "custody_policy": custody_policy,
            },
            admitted=x.fixture.native.qualifier.qualify(),
            run_id=run_id,
            custody_hmac_key=single.store.custody_hmac_key,
            deadline=x.helper.time.monotonic() + 900,
        )
        yield SimpleNamespace(x=x, single=single, reader=reader, state=state)
    finally:
        single.context.channel.close()
        single.host.close()
        x.helper.close_harness(harness)


def test_v2_armed_reader_existing_hmac_marker_without_writes(v2_armed_custody_reader, monkeypatch):
    x = v2_armed_custody_reader

    def forbidden(*args, **kwargs):
        raise AssertionError("ARMED reader invoked a mutating EvidenceStore API")

    for name in ("initialize_run", "records", "verify"):
        monkeypatch.setattr(x.single.store, name, forbidden)
    before = x.x.helper.tree(x.single.store.root)
    with x.reader.capture() as observation:
        assert observation is not None
        raw, recheck = observation
        assert json.loads(raw) == x.state.payload(x.x.helper.executor.ART_ARMED)
        assert recheck() is None
    assert x.x.helper.tree(x.single.store.root) == before


def test_v2_native_readonly_custody_module_is_exact_selected_source(v2_source_checkout):
    repo, expected, module = v2_source_checkout
    loaded = module._load_native_apparatus(repo, expected)
    assert "custody_policy" in loaded, "Custody HMAC module missing from verified host closure"
    assert loaded["custody_policy"].__file__ == str(
        repo / "backend/apps/experiments_app/custody_policy.py"
    )
    assert "django" not in loaded["custody_policy"].__dict__


@pytest.mark.parametrize("fault", ["index-hmac", "record-hmac", "artifact-bytes", "boolean-count"])
def test_v2_armed_reader_authentication_and_closed_existing_marker(v2_armed_custody_reader, fault):
    x = v2_armed_custody_reader
    sql, custody = x.x.fixture.module.op.sql, x.reader.custody
    root, key = x.single.store.root, x.single.store.custody_hmac_key
    index, signature = root / x.reader.index, root / x.reader.signature
    artifact = root / x.reader.artifact
    if fault == "index-hmac":
        signature.write_bytes(b"0" * 64 + b"\n")
    elif fault == "artifact-bytes":
        artifact.write_bytes(artifact.read_bytes().replace(b'"EX-10"', b'"EX-11"'))
    else:
        records = [json.loads(line) for line in index.read_bytes().splitlines()]
        selected = next(r for r in records if r["artifact_type"] == "ex10-emission-armed")
        if fault == "record-hmac":
            selected["custody_signature"] = "0" * 64
        else:
            value = json.loads(artifact.read_bytes())
            value["tokens_verified"] = True
            raw = sql.canonical_bytes(value)
            artifact.write_bytes(raw)
            selected["size_bytes"] = len(raw)
            selected["sha256"] = hashlib.sha256(raw).hexdigest()
            selected["custody_signature"] = custody.create_custody_record_signature(
                selected,
                custody_hmac_key=key,
            )
        raw = b"".join(sql.canonical_bytes(r) for r in records)
        index.write_bytes(raw)
        signature.write_bytes(
            (custody.create_custody_index_signature(raw, custody_hmac_key=key) + "\n").encode()
        )
    expected = (
        custody.CustodyPolicyError
        if fault in ("index-hmac", "record-hmac")
        else official.LauncherError
    )
    with pytest.raises(expected):
        with x.reader.capture():
            pass


def test_v2_armed_reader_same_bytes_new_artifact_inode_rejected(v2_armed_custody_reader):
    x = v2_armed_custody_reader
    with x.reader.capture() as observation:
        raw, recheck = observation
        artifact = x.single.store.root / x.reader.artifact
        replacement = artifact.with_name("synthetic-same-marker.json")
        replacement.write_bytes(raw)
        replacement.chmod(0o600)
        os.replace(replacement, artifact)
        with pytest.raises(official.LauncherError, match="native-armed-lease"):
            recheck()


def test_v2_armed_reader_releases_shared_lock_and_accepts_authenticated_append(
    v2_armed_custody_reader,
):
    x = v2_armed_custody_reader
    from apps.experiments_app.constants import ArtifactKind, Classification

    with x.reader.capture() as observation:
        raw, recheck = observation
        fd = os.open(x.single.store.root / ".s11-custody.lock", os.O_RDONLY | os.O_NOFOLLOW)
        try:
            # Independent writer descriptor proves no custody lock crosses yield.
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        signature = x.single.store.root / x.reader.signature
        old_inode = signature.stat().st_ino
        x.single.store.persist_json(
            run_id=x.reader.run_id,
            scenario_id="EX-10",
            artifact_type="w09-availability-test",
            kind=ArtifactKind.METADATA,
            classification=Classification.INTERNAL,
            filename="w09-availability-test.json",
            payload={"availability_test_only": True},
            source="w09-local-test",
        )
        assert signature.stat().st_ino != old_inode
        assert recheck() is None
        assert (x.single.store.root / x.reader.artifact).read_bytes() == raw


def test_v2_armed_reader_validly_resigned_inventory_cannot_truncate(v2_armed_custody_reader):
    x = v2_armed_custody_reader
    with x.reader.capture() as observation:
        _, recheck = observation
        index = x.single.store.root / x.reader.index
        lines = index.read_bytes().splitlines(keepends=True)
        assert len(lines) > 1
        assert json.loads(lines[-1])["artifact_type"] == "ex10-emission-armed"
        raw = b"".join(lines[1:])
        index.write_bytes(raw)
        signature = x.reader.custody.create_custody_index_signature(
            raw, custody_hmac_key=x.reader.key
        )
        (x.single.store.root / x.reader.signature).write_bytes((signature + "\n").encode())
        with pytest.raises(official.LauncherError, match="native-armed-inventory-prefix"):
            recheck()


def test_v2_armed_reader_retains_directory_mapping(v2_armed_custody_reader):
    x = v2_armed_custody_reader
    with x.reader.capture() as observation:
        _, recheck = observation
        metadata = x.single.store.root / "runs" / x.reader.run_id / "metadata"
        original = metadata.with_name("metadata-original")
        metadata.rename(original)
        shutil.copytree(original, metadata)
        with pytest.raises(official.LauncherError, match="native-armed-lease"):
            recheck()


def test_v2_armed_reader_requires_existing_custody_lock(v2_armed_custody_reader):
    x = v2_armed_custody_reader
    (x.single.store.root / ".s11-custody.lock").unlink()
    with pytest.raises(FileNotFoundError):
        with x.reader.capture():
            pass
    assert not (x.single.store.root / ".s11-custody.lock").exists()


def test_v2_armed_reader_busy_shared_lock_is_only_absence(v2_armed_custody_reader):
    x = v2_armed_custody_reader
    fd = os.open(x.single.store.root / ".s11-custody.lock", os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with x.reader.capture() as observation:
            assert observation is None
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    with x.reader.capture() as observation:
        assert observation is not None and observation[1]() is None


def test_v2_armed_reader_absolute_deadline_after_real_inventory_io(
    v2_armed_custody_reader, monkeypatch
):
    x = v2_armed_custody_reader
    with x.reader.capture() as observation:
        _, recheck = observation
        inventory = x.reader._inventory

        def slow(*args):
            result = inventory(*args)
            monkeypatch.setattr(x.x.helper.time, "monotonic", lambda: x.reader.deadline + 1)
            return result

        monkeypatch.setattr(x.reader, "_inventory", slow)
        with pytest.raises(official.LauncherError, match="native-armed-deadline"):
            recheck()


@pytest.mark.parametrize("boundary", ["after-flock", "after-inventory"])
def test_v2_armed_reader_lock_mapping_stable_through_locked_read(
    v2_armed_custody_reader, monkeypatch, boundary
):
    x = v2_armed_custody_reader
    lock_path = x.single.store.root / ".s11-custody.lock"
    changed = []

    def replace_lock():
        if not changed:
            replacement = lock_path.with_name("synthetic-lock-replacement")
            replacement.write_bytes(b"")
            replacement.chmod(0o600)
            os.replace(replacement, lock_path)
            changed.append(True)

    if boundary == "after-flock":
        flock = fcntl.flock

        def altered(fd, operation):
            result = flock(fd, operation)
            if operation == fcntl.LOCK_SH | fcntl.LOCK_NB:
                replace_lock()
            return result

        monkeypatch.setattr(fcntl, "flock", altered)
    else:
        inventory = x.reader._inventory

        def altered(*args):
            result = inventory(*args)
            replace_lock()
            return result

        monkeypatch.setattr(x.reader, "_inventory", altered)
    with pytest.raises(official.LauncherError, match="native-armed-custody-lock"):
        with x.reader.capture():
            pass
    assert changed == [True]


def test_v2_armed_reader_fifo_lock_cannot_block_open(v2_armed_custody_reader):
    import multiprocessing

    x = v2_armed_custody_reader
    lock_path = x.single.store.root / ".s11-custody.lock"
    lock_path.unlink()
    os.mkfifo(lock_path, mode=0o600)
    context = multiprocessing.get_context("fork")
    reader, writer = context.Pipe(duplex=False)

    def child():
        reader.close()
        try:
            with x.reader.capture():
                writer.send("unexpected-success")
        except official.LauncherError as error:
            writer.send(str(error))
        except BaseException as error:
            writer.send("unexpected-" + type(error).__name__)
        finally:
            writer.close()

    process = context.Process(target=child)
    process.start()
    writer.close()
    try:
        process.join(timeout=2)
        blocked = process.is_alive()
        result = reader.recv() if reader.poll() else None
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)
        reader.close()
    assert not process.is_alive()
    assert not blocked, "FIFO custody lock blocked readonly open"
    assert result == "native-armed-custody-lock"


@pytest.mark.parametrize("boundary", ["capture", "recheck", "busy"])
def test_v2_armed_reader_original_deadline_after_actual_unlock(
    v2_armed_custody_reader, monkeypatch, boundary
):
    x = v2_armed_custody_reader
    flock = fcntl.flock

    def delayed_unlock(fd, operation):
        result = flock(fd, operation)
        if operation == fcntl.LOCK_UN:
            monkeypatch.setattr(x.x.helper.time, "monotonic", lambda: x.reader.deadline + 1)
        return result

    if boundary == "busy":
        fd = os.open(x.single.store.root / ".s11-custody.lock", os.O_RDONLY | os.O_NOFOLLOW)
        flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def delayed_busy(target, operation):
            try:
                return flock(target, operation)
            except BlockingIOError:
                monkeypatch.setattr(x.x.helper.time, "monotonic", lambda: x.reader.deadline + 1)
                raise

        monkeypatch.setattr(fcntl, "flock", delayed_busy)
        try:
            with pytest.raises(official.LauncherError, match="native-armed-deadline"):
                with x.reader.capture():
                    pass
        finally:
            flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    elif boundary == "capture":
        monkeypatch.setattr(fcntl, "flock", delayed_unlock)
        with pytest.raises(official.LauncherError, match="native-armed-deadline"):
            with x.reader.capture():
                pass
    else:
        with x.reader.capture() as observation:
            monkeypatch.setattr(fcntl, "flock", delayed_unlock)
            with pytest.raises(official.LauncherError, match="native-armed-deadline"):
                observation[1]()


def test_v2_proof_signer_retains_exact_existing_signed_transcript(v2_bridge_proof_signer):
    x = v2_bridge_proof_signer
    _, errors, responses = x.helper.v2_run_native_host(x.rig, lambda: x.helper.v2_start_call(x.rig))
    assert not errors and len(responses) == 1
    assert x.signer.signed_challenge == x.rig.context.host_challenge.canonical
    assert x.signer.signed_response == responses[0]
    assert x.signer.index == 6 and x.rig.host.position == 9


@pytest.fixture
def v2_emission_publication(v2_bridge_proof_signer, request):
    """Actual SINGLE/ARMED/B3GOV/B4 with a fixed synthetic deployment provider.

    Closed B5 material comes from the actual B5 synthetic fixture; its context
    is independently bound to this native fixture. OS/deployment approval stays
    an explicit synthetic provider, not a real factory or operational claim.
    """
    import importlib.util
    import uuid
    from dataclasses import replace

    from apps.experiments_app import custody_policy

    x = v2_bridge_proof_signer
    _, errors, responses = x.helper.v2_run_native_host(x.rig, lambda: x.helper.v2_start_call(x.rig))
    assert not errors and len(responses) == 1
    single, _, signer, sources = v2_signed_single_setup(x)
    op, a = x.fixture.module.op, x.fixture.rig.a
    harness = x.helper.make_harness(
        "w09-emission-" + uuid.uuid4().hex, x.helper.dataset.__wrapped__()
    )
    b5_path = Path(x.helper.__file__).with_name("test_ex10_deployment_envelope.py")
    spec = importlib.util.spec_from_file_location(
        "w09_emission_b5_shape_" + uuid.uuid4().hex, b5_path
    )
    b5_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b5_module)
    b5_generator = b5_module.rig.__wrapped__()
    b5_rig = next(b5_generator)
    try:
        envelope = b5_module.b5.EnvelopeQualifier(**b5_rig.options).build().to_dict()
        assert envelope["result"] == "PASS"
        native = x.fixture.native.qualifier.qualify()
        profile = native.profile.to_dict()
        envelope_now = a.now()
        envelope.update(
            checked_at=op.coordination._utc(envelope_now),
            expires_at=op.coordination._utc(
                envelope_now + timedelta(seconds=getattr(request, "param", 900))
            ),
            expected_git_sha=profile["source_revision"],
            runtime_profile_sha256=op.sql.parse_canonical(native.profile_reference)["sha256"],
            session_id=native.session.header["session_id"],
            session_header_sha256=op.sql.parse_canonical(native.header_reference)["sha256"],
            project=profile["project"],
            host_roots=profile["host_roots"],
            deployment_verification_reference=profile["deployment_verification_reference"],
            application_preflight_sha256=sources.registry.reference("preflight")["sha256"],
        )
        envelope_raw = op.sql.canonical_bytes(envelope)
        b5_module.b5.parse_envelope_bytes(envelope_raw)
        old = sources.registry.bindings["envelope"]
        path = Path(op.sql.parse_canonical(old.reference)["path"]).with_name(
            "w09-synthetic-emission-envelope.json"
        )
        path.write_bytes(envelope_raw)
        path.chmod(0o600)
        ref = op._artifact_reference(str(path), envelope_raw, b5_module.b5.SCHEMA_VERSION)
        binding = op.HostArtifactBinding("envelope", op.sql.canonical_bytes(ref), old.store)
        sources.registry = op.HostArtifactRegistry(
            tuple(b for name, b in sources.registry.bindings.items() if name != "envelope")
            + (binding,)
        )
        verified = []
        historical_envelope_provider = sources.policy.envelope

        def approved_synthetic_deployment(**kw):
            if kw["canonical_envelope"] == b"{}\n":
                return historical_envelope_provider(**kw)
            assert kw["canonical_envelope"] == envelope_raw
            assert kw["reference"] == op.sql.canonical_bytes(ref)
            assert kw["native"].profile._bytes == native.profile._bytes
            assert kw["native"].session._bytes == native.session._bytes
            assert kw["native"].process == native.process
            assert kw["snapshot"].authority_uncertainty is False
            assert 0 <= (a.now() - op.wire._time(kw["issued_at"])).total_seconds() <= 5
            value = b5_module.b5.parse_envelope_bytes(kw["canonical_envelope"]).to_dict()
            assert (
                op.wire._time(value["checked_at"]) <= a.now() < op.wire._time(value["expires_at"])
            )
            verified.append("fixed-synthetic-deployment-qualified")

        sources.policy.envelope = approved_synthetic_deployment
        reader = official.NativeArmedArtifactReader(
            apparatus={
                "ex10_operational_context": op,
                "custody_policy": custody_policy,
                "ex10_deployment_envelope": b5_module.b5,
            },
            admitted=native,
            run_id=x.rig.context.proposal["run_id"],
            custody_hmac_key=single.store.custody_hmac_key,
            deadline=x.helper.time.monotonic() + 900,
        )
        directory = single.store.root / ".s11-operational" / single.host.exchange_id
        private = op.durable.PrivateStore(str(directory), x.fixture.module.h.identity(directory))
        a.stores["w09-emission-private-" + uuid.uuid4().hex] = private
        copied = []

        def publish(role, raw):
            private.create_exact(str(directory / (role + ".json")), raw)
            copied.append(role)

        component = official.NativeEmissionProofPublication(
            broker=single.broker, reader=reader, publish_role=publish
        )
        single.broker = component
        probes = replace(
            harness.probes,
            now=a.now,
            fingerprint=lambda: x.fixture.rig.sample["context"].executor_fingerprint,
        )

        def arm():
            run_id = x.rig.context.proposal["run_id"]
            single.context.authorize_step(
                single.store,
                action="execute",
                run_id=run_id,
                git_sha=x.rig.context.proposal["git_sha"],
                phase="PREPARED",
            )
            x.helper.executor._checkpoint_started(
                single.store,
                run_id=run_id,
                git_sha=x.rig.context.proposal["git_sha"],
                probes=probes,
                dataset=harness.dataset,
                step="execute",
                source="s11_ex10.execute",
                operational_context=single.context,
            )
            x.helper.executor._arm_emission(
                single.store,
                run_id,
                x.rig.context.proposal["git_sha"],
                probes,
                harness.dataset,
                operational_context=single.context,
            )
            return run_id

        yield SimpleNamespace(
            x=x,
            single=single,
            reader=reader,
            component=component,
            signer=signer,
            sources=sources,
            copied=copied,
            verified=verified,
            directory=directory,
            private=private,
            arm=arm,
            envelope=envelope,
        )
    finally:
        single.context.channel.close()
        single.host.close()
        x.helper.close_harness(harness)
        b5_generator.close()


def test_v2_emission_publication_actual_single_then_armed_no_extra_rpc(v2_emission_publication):
    x = v2_emission_publication
    run_id, responses = x.x.helper.v2_run_single_host(x.single, x.arm)
    assert len(responses) == 1 and x.single.context.finished
    assert x.single.host.closed and x.single.host.position == 5 and x.signer.index == 3
    assert x.signer.signed_challenge == x.single.context.host_challenge.canonical
    assert responses[0] == x.signer.signed_response
    assert x.copied == [
        "emission-material",
        "emission-head",
        "emission-coordination",
        "emission-receipt",
    ]
    assert x.verified == ["fixed-synthetic-deployment-qualified"] * 2
    assert x.sources.publisher.transport.calls.count("B3_CAS") == 1
    assert x.x.fixture.rig.a.transport.calls.count("RESERVE") == 1
    assert x.x.fixture.rig.a.transport.calls.count("COMMIT_OPEN") == 1
    for role in ("emission-head", "emission-coordination"):
        head = json.loads((x.directory / (role + ".json")).read_bytes())
        assert head["challenge"] == x.single.context.host_challenge.to_dict()["challenge"]
        armed = json.loads((x.single.store.root / x.reader.artifact).read_bytes())
        assert x.reader.op.wire._time(head["issued_at"]) >= x.reader.op.wire._time(
            armed["recorded_at"]
        )
    proof = x.single.context.guard.__self__
    proof.current(context=x.single.context, action="execute", run_id=run_id, phase="BARRIER")
    proof.check_current_freshness(context=x.single.context)
    assert x.component.used and not x.component.failed


def test_v2_emission_publication_rechecks_actual_deployment_lease_after_publication(
    v2_emission_publication,
):
    x = v2_emission_publication
    approval = x.directory / "synthetic-current-deployment-lease.json"
    approval.write_bytes(b'{"result":"PASS"}\n')
    approval.chmod(0o600)
    envelope = x.sources.policy.envelope
    publish = x.component.publish_role
    qualified = []

    def approved_current_deployment(**kw):
        if kw["canonical_envelope"] != b"{}\n":
            info = approval.stat()
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise x.reader.op.OperationalError("ENVELOPE_INVALID")
            assert approval.read_bytes() == b'{"result":"PASS"}\n'
            qualified.append("actual-current-deployment-lease")
        return envelope(**kw)

    def changed(role, raw):
        result = publish(role, raw)
        if role == "emission-receipt":
            approval.chmod(0o640)
        return result

    x.sources.policy.envelope = approved_current_deployment
    x.component.publish_role = changed
    with pytest.raises(AssertionError, match="ENVELOPE_INVALID"):
        x.x.helper.v2_run_single_host(x.single, x.arm)
    assert qualified == ["actual-current-deployment-lease"]
    assert len(x.copied) == 4 and x.component.failed


def test_v2_emission_publication_rejects_actual_extra_stdout_before_eof(
    v2_emission_publication, monkeypatch
):
    x = v2_emission_publication
    finish = x.single.context.channel.finish

    def extra_stdout():
        assert os.write(x.single.context.channel.write_fd, b"synthetic-trailing-byte") > 0
        return finish()

    monkeypatch.setattr(x.single.context.channel, "finish", extra_stdout)
    with pytest.raises(AssertionError, match="HOST_CONTEXT_INVALID"):
        x.x.helper.v2_run_single_host(x.single, x.arm)
    assert x.copied == [] and x.component.failed and x.component.used


@pytest.mark.parametrize("fault", ["before-armed", "wrong-nonce", "wrong-signature"])
def test_v2_emission_publication_original_nonce_and_post_armed_head(
    v2_emission_publication, monkeypatch, fault
):
    x = v2_emission_publication
    transport = x.sources.publisher.transport
    head_b3 = transport.head_b3
    op = x.reader.op
    corrupted = []

    def head(challenge):
        value = head_b3(challenge)
        if (x.single.store.root / x.reader.artifact).exists() and challenge == x.signer.challenge[
            "challenge"
        ]:
            fields = {k: v for k, v in value.items() if k != "signature"}
            if fault == "before-armed":
                armed = json.loads((x.single.store.root / x.reader.artifact).read_bytes())
                issued = op.wire._time(armed["recorded_at"]) - timedelta(seconds=1)
                fields.update(
                    issued_at=op.coordination._utc(issued),
                    expires_at=op.coordination._utc(issued + timedelta(seconds=30)),
                )
            elif fault == "wrong-nonce":
                fields["challenge"] = "f" * 64
            value = op.history.witness._sign_current_head(fields, x.x.fixture.rig.a.f.key)
            if fault == "wrong-signature":
                value["signature"] = "0" * 128
            corrupted.append(fault)
        return value

    monkeypatch.setattr(transport, "head_b3", head)
    with pytest.raises(AssertionError):
        x.x.helper.v2_run_single_host(x.single, x.arm)
    assert corrupted == [fault] and x.copied == [] and x.component.failed


def test_v2_emission_publication_partial_failure_is_retained_and_no_retry(v2_emission_publication):
    x = v2_emission_publication
    publish = x.component.publish_role

    def partial(role, raw):
        if role == "emission-coordination":
            raise official.LauncherError("synthetic-emission-publication-stopped")
        return publish(role, raw)

    x.component.publish_role = partial
    with pytest.raises(AssertionError, match="synthetic-emission-publication-stopped"):
        x.x.helper.v2_run_single_host(x.single, x.arm)
    assert x.copied == ["emission-material", "emission-head"] and x.component.failed
    before = {p.name: p.read_bytes() for p in x.directory.iterdir() if p.is_file()}
    with pytest.raises(official.LauncherError, match="native-emission-consumed"):
        x.component.serve_single(x.single.host)
    assert before == {p.name: p.read_bytes() for p in x.directory.iterdir() if p.is_file()}


def test_v2_emission_publication_retains_armed_inode_after_last_publication(
    v2_emission_publication,
):
    x = v2_emission_publication
    publish = x.component.publish_role

    def replaced(role, raw):
        result = publish(role, raw)
        if role == "emission-receipt":
            artifact = x.single.store.root / x.reader.artifact
            replacement = artifact.with_name("synthetic-new-emission-marker.json")
            replacement.write_bytes(artifact.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, artifact)
        return result

    x.component.publish_role = replaced
    with pytest.raises(AssertionError, match="native-armed-lease"):
        x.x.helper.v2_run_single_host(x.single, x.arm)
    assert len(x.copied) == 4 and x.component.failed


def v2_emission_delayed_final_custody_check(x, monkeypatch, boundary):
    from contextlib import contextmanager

    capture = x.reader.capture
    delayed = []

    @contextmanager
    def delayed_capture():
        with capture() as observation:
            if observation is None:
                yield None
                return
            raw, actual_recheck = observation

            def recheck():
                result = actual_recheck()
                if len(x.copied) == 4 and not delayed:
                    delayed.append(boundary)
                    if boundary == "absolute":
                        monkeypatch.setattr(
                            x.x.helper.time, "monotonic", lambda: x.reader.deadline + 1
                        )
                    else:
                        head = json.loads((x.directory / "emission-head.json").read_bytes())
                        value = head if boundary == "head" else x.envelope
                        instant = x.reader.op.wire._time(value["expires_at"]) + timedelta(
                            microseconds=1
                        )
                        monkeypatch.setattr(x.component.journal, "_now", lambda: instant)
                return result

            yield raw, recheck

    monkeypatch.setattr(x.reader, "capture", delayed_capture)
    return delayed


@pytest.mark.parametrize("boundary", ["head", "absolute"])
def test_v2_emission_publication_original_clock_after_last_actual_io(
    v2_emission_publication, monkeypatch, boundary
):
    x = v2_emission_publication
    delayed = v2_emission_delayed_final_custody_check(x, monkeypatch, boundary)
    expected = "native-emission-head-stale" if boundary == "head" else "native-emission-deadline"
    with pytest.raises(AssertionError, match=expected):
        x.x.helper.v2_run_single_host(x.single, x.arm)
    assert delayed == [boundary] and x.component.failed and len(x.copied) == 4


@pytest.mark.parametrize("v2_emission_publication", [30], indirect=True)
def test_v2_emission_publication_b5_expiry_after_last_actual_io(
    v2_emission_publication, monkeypatch
):
    x = v2_emission_publication
    delayed = v2_emission_delayed_final_custody_check(x, monkeypatch, "envelope")
    with pytest.raises(AssertionError, match="native-emission-envelope-stale"):
        x.x.helper.v2_run_single_host(x.single, x.arm)
    assert delayed == ["envelope"] and x.component.failed and len(x.copied) == 4


def test_v2_emission_publication_consumed_without_second_exchange(v2_emission_publication):
    x = v2_emission_publication
    _, responses = x.x.helper.v2_run_single_host(x.single, x.arm)
    assert len(responses) == 1 and not x.component.failed
    copied = list(x.copied)
    with pytest.raises(official.LauncherError, match="native-emission-consumed"):
        x.component.serve_single(x.single.host)
    assert x.copied == copied and x.single.host.position == 5


@pytest.fixture
def v2_worker_result_observation(v2_oneoff_control):
    """Actual native B1/B2/B3, synthetic exact Docker exit and existing private JSON."""
    case = v2_oneoff_control
    control, mod = case.control, case.host.module
    control.create()
    control.start()
    run_id = "s11-00000000-0000-4000-8000-000000000031"
    payload = {
        "run_id": run_id,
        "scenario_id": "EX-10",
        "evidence_mode": mod.EVIDENCE_MODE,
        "phase": "PREPARED",
        "manifest_sha256": "a" * 64,
        "start_sha256": "b" * 64,
        "preflight_sha256": "c" * 64,
    }
    root = Path(control.profile.to_dict()["host_roots"]["evidence"]["path"])
    path = root / ("executor-result." + control.exchange_id + ".json")
    raw = control.bootstrap.apparatus["ex10_authority_sqlite"].canonical_bytes(payload)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        assert os.write(fd, raw) == len(raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    observer = mod.NativeWorkerResultObservation(control=control, expected_run_id=run_id)
    yield SimpleNamespace(
        case=case, observer=observer, root=root, path=path, raw=raw, payload=payload
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"deadline": {"deadline_utc_ns": 1_780_913_360_000_000_000}},
        {"criteria": {"throughput": 50 / 60, "latency_p99_ms": 0.25}},
        {"deadline": None, "polls": 0},
    ],
    ids=["utc-nanoseconds", "finite-floats", "no-t0"],
)
def test_native_public_result_preserves_scientific_json(payload):
    from apps.experiments_app.canonical import canonical_json_bytes

    raw = canonical_json_bytes(payload)
    assert official._native_parse_public_result(raw, maximum=len(raw)) == payload
    assert official.base.canonical_bytes(payload) == raw


@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1,"x":2}\n',
        b'{"nested":{"x":1,"x":2}}\n',
        b'{"x": NaN}\n',
        b'{"x":Infinity}\n',
        b'{"x":1e400}\n',
        b'{"x":1} ',
        b'{ "x":1}\n',
        b'{"x":-0}\n',
        b"[]\n",
        b'{"x":"\xff"}\n',
        b"",
    ],
    ids=[
        "duplicate",
        "nested-duplicate",
        "nan",
        "infinity",
        "overflow",
        "trailing-space",
        "whitespace",
        "negative-zero",
        "non-object",
        "utf8",
        "empty",
    ],
)
def test_native_public_result_rejects_invalid_or_noncanonical_bytes(raw):
    with pytest.raises((ValueError, UnicodeError)):
        official._native_parse_public_result(raw, maximum=1024)


def test_native_public_result_bound_is_exact():
    raw = b'{"x":1}\n'
    with pytest.raises(ValueError):
        official._native_parse_public_result(raw, maximum=len(raw) - 1)
    with pytest.raises(ValueError):
        official._native_parse_public_result(raw.decode(), maximum=1024)
    assert official._native_parse_public_result(raw, maximum=len(raw)) == {"x": 1}


def test_v2_worker_result_real_pid1_exit_before_private_json(v2_worker_result_observation):
    x = v2_worker_result_observation
    before = _v2_private_tree_bytes(x.case.host.home)
    with x.observer.capture() as result:
        assert result is None, "Docker client exit0 and private file cannot stand in for PID1"
    x.case.observations["phase"] = "exited"
    with x.observer.capture() as result:
        raw, payload, recheck = result
        assert raw == x.raw and payload == x.payload
        assert recheck() is None
    assert _v2_private_tree_bytes(x.case.host.home) == before
    assert x.case.host.checkpoint["value"] is not None
    session = x.case.control.bootstrap.load(x.case.control.invocation).to_dict()
    assert len(session["in_flight"]) == 1 and session["state"]["uncertain"] is True
    assert not any(kind in {"rm", "kill", "stop", "start"} for kind, _ in x.case.events[4:])


@pytest.mark.parametrize("code", [3, -1])
def test_v2_worker_result_nonzero_pid1_retains_intent(v2_worker_result_observation, code):
    x = v2_worker_result_observation
    x.case.observations.update(phase="exited", worker_code=code)
    before = _v2_private_tree_bytes(x.case.host.home)
    with pytest.raises(x.case.host.module.LauncherError, match="native-worker-exit-unsuccessful"):
        with x.observer.capture():
            pytest.fail("Nonzero PID1 offered a private success result")
    assert _v2_private_tree_bytes(x.case.host.home) == before and x.path.read_bytes() == x.raw


@pytest.mark.parametrize(
    "fault",
    [
        "wrong-run",
        "wrong-phase",
        "duplicate-json",
        "noncanonical",
        "oversize",
        "public-mode",
        "hardlink",
        "symlink",
    ],
)
def test_v2_worker_result_rejects_unqualified_private_result(v2_worker_result_observation, fault):
    x = v2_worker_result_observation
    x.case.observations["phase"] = "exited"
    value = dict(x.payload)
    if fault == "wrong-run":
        value["run_id"] = "s11-00000000-0000-4000-8000-000000000032"
    elif fault == "wrong-phase":
        value["phase"] = "EMITTING"
    if fault in {"wrong-run", "wrong-phase"}:
        x.path.write_bytes(x.case.host.module.base.canonical_bytes(value))
    elif fault == "duplicate-json":
        x.path.write_bytes(x.raw.replace(b"{", b'{"run_id":"forged",', 1))
    elif fault == "noncanonical":
        x.path.write_bytes(json.dumps(value).encode() + b"\n")
    elif fault == "oversize":
        op = x.case.control.bootstrap.apparatus["ex10_operational_context"]
        x.path.write_bytes(b"x" * (op.wire.MAX_REQUEST_BYTES + 1))
    elif fault == "public-mode":
        x.path.chmod(0o640)
    elif fault == "hardlink":
        os.link(x.path, x.path.with_suffix(".retained-link"))
    else:
        target = x.path.with_suffix(".retained-original")
        x.path.rename(target)
        x.path.symlink_to(target)
    before = _v2_private_tree_bytes(x.case.host.home)
    with pytest.raises(x.case.host.module.LauncherError):
        with x.observer.capture():
            pytest.fail("Unqualified private result was exposed")
    assert _v2_private_tree_bytes(x.case.host.home) == before


def test_v2_worker_result_retains_same_bytes_inode(v2_worker_result_observation):
    x = v2_worker_result_observation
    x.case.observations["phase"] = "exited"
    with pytest.raises(
        x.case.host.module.LauncherError, match="native-worker-result-(lease|context)"
    ):
        with x.observer.capture() as (_, _, recheck):
            old = x.path.with_suffix(".retained-original")
            x.path.rename(old)
            x.path.write_bytes(x.raw)
            x.path.chmod(0o600)
            assert x.path.stat().st_ino != old.stat().st_ino
            with pytest.raises(
                x.case.host.module.LauncherError, match="native-worker-result-lease"
            ):
                recheck()


def test_v2_worker_result_rechecks_actual_docker_owner_after_read(
    v2_worker_result_observation, monkeypatch
):
    x = v2_worker_result_observation
    x.case.observations["phase"] = "exited"
    evidence = x.case.control.bootstrap.apparatus["ex10_operational_context"].evidence
    actual = evidence.ConfinedArtifactResolver._read
    calls = []

    def read(*args, **kwargs):
        result = actual(*args, **kwargs)
        calls.append(True)
        x.case.observations["alter"] = lambda v: v["Config"]["Labels"].update(
            {"s11.owner.session": "foreign"}
        )
        return result

    monkeypatch.setattr(evidence.ConfinedArtifactResolver, "_read", read)
    with pytest.raises(x.case.host.module.LauncherError, match="native-oneoff-observation-invalid"):
        with x.observer.capture():
            pytest.fail("Changed Docker owner after real file IO was accepted")
    assert calls == [True]


def test_v2_worker_result_original_authority_after_last_real_io(
    v2_worker_result_observation, monkeypatch
):
    x = v2_worker_result_observation
    x.case.observations["phase"] = "exited"
    history = x.case.control.bootstrap.apparatus["ex10_history_authority"]
    clock = {"now": datetime.now(UTC)}
    monkeypatch.setattr(history, "_now", lambda: clock["now"].strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    actual = x.case.control.worker_exit
    calls = []

    def exit_after_io():
        result = actual()
        calls.append(True)
        if len(calls) == 2:
            clock["now"] += timedelta(seconds=40)
        return result

    monkeypatch.setattr(x.case.control, "worker_exit", exit_after_io)
    with pytest.raises(Exception, match="EXPIRED_OBSERVATION"):
        with x.observer.capture():
            pytest.fail("Last Docker IO renewed the original authority observation")
    assert len(calls) == 2


def test_v2_worker_result_original_authority_after_actual_fd_cleanup(
    v2_worker_result_observation, monkeypatch
):
    x = v2_worker_result_observation
    x.case.observations["phase"] = "exited"
    history = x.case.control.bootstrap.apparatus["ex10_history_authority"]
    clock = {"now": datetime.now(UTC)}
    monkeypatch.setattr(history, "_now", lambda: clock["now"].strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    actual = os.close
    identity = (x.path.stat().st_dev, x.path.stat().st_ino)
    closed = []

    def close(fd):
        info = os.fstat(fd)
        result = actual(fd)
        if (info.st_dev, info.st_ino) == identity:
            closed.append(True)
            clock["now"] += timedelta(seconds=40)
        return result

    monkeypatch.setattr(x.case.host.module.os, "close", close)
    with pytest.raises(Exception, match="EXPIRED_OBSERVATION"):
        with x.observer.capture() as (_, value, _):
            assert value == x.payload
    assert closed == [True]


def test_v2_worker_result_actual_executor_private_writer_format(
    v2_worker_result_observation, v2_bridge_proof_signer
):
    """Real frozen START9 writer bytes cross the result codec; contexts remain independent."""
    x, signed = v2_worker_result_observation, v2_bridge_proof_signer
    result, errors, _ = signed.helper.v2_run_native_host(
        signed.rig, lambda: signed.helper.v2_start_call(signed.rig)
    )
    assert errors == [] and signed.rig.context.finished and signed.rig.context.scientific_finished
    signed.rig.context.persist_public_result(signed.rig.store, result)
    path = signed.rig.store.root / (
        "executor-result." + signed.rig.context.channel.exchange_id + ".json"
    )
    raw = path.read_bytes()
    assert raw == signed.helper.executor.canonical_json_bytes(result)
    # Copy only authenticated producer bytes into this synthetic one-off's
    # private fixture. This qualifies the existing format, not cross-profile
    # authority, same-worker bridge completion or the missing factory linkage.
    x.path.write_bytes(raw)
    x.case.observations["phase"] = "exited"
    observer = x.case.host.module.NativeWorkerResultObservation(
        control=x.case.control, expected_run_id=result["run_id"]
    )
    with observer.capture() as (observed, value, recheck):
        assert observed == raw and value == result and recheck() is None
    assert len(x.case.control.bootstrap.load(x.case.control.invocation).to_dict()["in_flight"]) == 1


@pytest.fixture
def v2_terminal_artifact_reader(monkeypatch):
    """Real frozen START9/SINGLE5 and new sealed ND; no HTTP/scientific emission."""
    from apps.experiments_app import custody_policy, manifest
    from apps.experiments_app.tests import test_ex10_executor as helper

    with helper.v2_native_host_material() as fixture:
        started = helper.v2_native_start_setup(
            fixture, helper.dataset.__wrapped__(), client_proofs=True
        )
        helper.v2_client_proof_start_setup(started, monkeypatch)
        single = None
        try:
            _, errors, replies = helper.v2_run_native_host(
                started, lambda: helper.v2_start_call(started)
            )
            assert errors == [] and len(replies) == 1
            single = helper.v2_native_single_setup(started, action="finalize", client_proofs=True)
            helper.v2_client_proof_single_setup(single, monkeypatch)

            def prepare(*, context, store, run_id, result):
                assert (
                    context is single.context
                    and store is single.store
                    and run_id == result["run_id"]
                )
                state = helper.executor.load_existing_state_readonly(store, run_id)
                assert state is not None and state.finalized
                helper.executor.validate_finalized_state(
                    state.run_root, state.records, custody_hmac_key=store.custody_hmac_key
                )

            single.context.prepare_terminal = prepare
            result, replies = helper.v2_run_single_host(
                single,
                lambda: helper.executor.finalize_run(
                    single.store,
                    run_id=single.context.proposal["run_id"],
                    git_sha=single.context.proposal["git_sha"],
                    abandon_reason="synthetic-terminal-readonly-no-t0",
                    probes=started.probes,
                    operational_context=single.context,
                ),
            )
            assert (
                len(replies) == 1
                and single.context.terminal_prepared
                and single.context.scientific_finished
            )
            single.context.persist_public_result(single.store, result)
            state = helper.executor.load_existing_state_readonly(single.store, result["run_id"])
            assert state.finalized and result["decision"] == "NOT_DEMONSTRATED"
            raw = helper.executor.canonical_json_bytes(result)
            apparatus = {
                "ex10_operational_context": fixture.module.op,
                "custody_policy": custody_policy,
                "manifest": manifest,
            }
            reader = official.NativeTerminalArtifactReader(
                apparatus=apparatus,
                admitted=fixture.native.qualifier.qualify(),
                action="abandon-official-pre-t0",
                expected_run_id=result["run_id"],
                result_bytes=raw,
                custody_hmac_key=single.store.custody_hmac_key,
                deadline=helper.time.monotonic() + 30,
            )
            yield SimpleNamespace(
                reader=reader,
                fixture=fixture,
                helper=helper,
                started=started,
                single=single,
                state=state,
                result=result,
                raw=raw,
                root=single.store.root,
                key=single.store.custody_hmac_key,
                custody=custody_policy,
                manifest=manifest,
            )
        finally:
            if single is not None:
                single.context.channel.close()
                single.host.close()
            started.context.channel.close()
            started.host.close()


def test_v2_terminal_reader_actual_new_seal_branch_readonly(
    v2_terminal_artifact_reader, monkeypatch
):
    x = v2_terminal_artifact_reader
    before = _v2_private_tree_bytes(x.root)
    actual = x.helper.executor.validate_finalized_state(
        x.state.run_root, x.state.records, custody_hmac_key=x.key
    )
    final = x.state.record(x.helper.executor.ART_MANIFEST_FINAL)
    path = x.state.run_root / final["relative_path"]

    def deny(*args, **kwargs):
        pytest.fail("Terminal read invoked a custody mutation or science evaluation")

    for method in [
        "initialize_run",
        "records",
        "verify",
        "persist_json",
        "finalize_with_json_artifact",
    ]:
        monkeypatch.setattr(x.helper.executor.EvidenceStore, method, deny)
    with x.reader.capture() as (reference, result, recheck):
        assert result == x.result and recheck() is None
        assert reference == {
            "path": str(path),
            "sha256": final["sha256"],
            "schema_version": json.loads(path.read_bytes())["schema_version"],
        }
        assert actual == {
            "artifact_id": final["artifact_id"],
            "finished_at": json.loads(path.read_bytes())["finished_at"],
            "manifest_sha256": reference["sha256"],
            "run_id": result["run_id"],
        }
    assert _v2_private_tree_bytes(x.root) == before
    source = x.single.broker.sources
    snapshot = source.read_authority()
    assert (
        x.fixture.module.op.history.find_run(snapshot, x.result["run_id"])["lifecycle_state"]
        == "OPEN"
    )
    assert x.fixture.rig.a.journal.read()["active"] is not None


def v2_terminal_resigned_decision(x, **updates):
    """Synthetic custody key resigns existing artifacts, isolating semantic codec guards."""
    import copy

    records = copy.deepcopy(x.state.records)
    decision = next(r for r in records if r["artifact_type"] == "ex10-decision")
    final = next(r for r in records if r["artifact_type"] == "run-manifest-final")
    path = x.state.run_root / decision["relative_path"]
    value = json.loads(path.read_bytes())
    value.update(updates)
    raw = x.helper.executor.canonical_json_bytes(value)
    path.write_bytes(raw)
    decision.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
    path = x.state.run_root / final["relative_path"]
    manifest = json.loads(path.read_bytes())
    manifest["artifact_references"] = [
        {k: r[k] for k in ("artifact_id", "relative_path", "sha256")}
        for r in records
        if r is not final
    ]
    raw = x.helper.executor.canonical_json_bytes(manifest)
    path.write_bytes(raw)
    final.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
    for record in [decision, final]:
        record["custody_signature"] = x.custody.create_custody_record_signature(
            record, custody_hmac_key=x.key
        )
    raw = b"".join(x.helper.executor.canonical_json_bytes(record) for record in records)
    index = x.root / x.reader.reader.index
    index.write_bytes(raw)
    (x.root / x.reader.reader.signature).write_text(
        x.custody.create_custody_index_signature(raw, custody_hmac_key=x.key) + "\n"
    )
    path = x.state.run_root / ".s11-finalized"
    seal = json.loads(path.read_bytes())
    seal["payload"]["manifest_sha256"] = final["sha256"]
    seal["signature"] = x.custody.create_custody_seal_signature(
        x.helper.executor.canonical_json_bytes(seal["payload"]), custody_hmac_key=x.key
    )
    path.write_bytes(x.helper.executor.canonical_json_bytes(seal))
    result = dict(x.result, decision_sha256=decision["sha256"], manifest_sha256=final["sha256"])
    # All signatures/hashes and the existing new-seal branch are valid; only
    # authenticated decision metadata is malformed. No validator is patched.
    assert (
        x.helper.executor.validate_finalized_state(
            x.state.run_root, records, custody_hmac_key=x.key
        )
        == seal["payload"]
    )
    return official.NativeTerminalArtifactReader(
        apparatus=x.reader.apparatus,
        admitted=x.reader.admitted,
        action=x.reader.action,
        expected_run_id=x.result["run_id"],
        result_bytes=x.helper.executor.canonical_json_bytes(result),
        custody_hmac_key=x.key,
        deadline=x.reader.deadline,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("binding", 0),
        ("schema_version", "foreign-decision-v1"),
        ("protocol_id", "foreign-protocol"),
        ("methodology_version", "foreign-methodology"),
        ("scenario_id", "EX-09"),
        ("decision_reference", "foreign-criterion-reference"),
    ],
    ids=["numeric-bool", "schema", "protocol", "methodology", "scenario", "decision-reference"],
)
def test_v2_terminal_reader_decision_codec_binding(v2_terminal_artifact_reader, field, value):
    x = v2_terminal_artifact_reader
    reader = v2_terminal_resigned_decision(x, **{field: value})
    with pytest.raises(official.LauncherError, match="native-terminal-decision"):
        with reader.capture():
            pytest.fail("Malformed authenticated decision metadata was accepted")


@pytest.mark.parametrize(
    "fault",
    [
        "index-hmac",
        "record-hmac",
        "decision-bytes",
        "seal-signature",
        "seal-wrong-run",
        "seal-extra-field",
        "missing-seal",
    ],
)
def test_v2_terminal_reader_required_authentication(v2_terminal_artifact_reader, fault):
    x = v2_terminal_artifact_reader
    seal = x.state.run_root / ".s11-finalized"
    index = x.root / x.reader.reader.index
    if fault == "index-hmac":
        (x.root / x.reader.reader.signature).write_bytes(b"0" * 64 + b"\n")
    elif fault == "record-hmac":
        records = [json.loads(line) for line in index.read_bytes().splitlines()]
        records[-1]["custody_signature"] = "0" * 64
        raw = b"".join(x.helper.executor.canonical_json_bytes(record) for record in records)
        index.write_bytes(raw)
        (x.root / x.reader.reader.signature).write_text(
            x.custody.create_custody_index_signature(raw, custody_hmac_key=x.key) + "\n"
        )
    elif fault == "decision-bytes":
        record = x.state.record(x.helper.executor.ART_DECISION)
        (x.state.run_root / record["relative_path"]).write_bytes(b"{}\n")
    elif fault == "missing-seal":
        seal.rename(seal.with_suffix(".retained-original"))
    else:
        value = json.loads(seal.read_bytes())
        if fault == "seal-signature":
            value["signature"] = "0" * 64
        else:
            if fault == "seal-wrong-run":
                value["payload"]["run_id"] = "s11-00000000-0000-5000-8000-000000000001"
            else:
                value["payload"]["extra"] = "unapproved-field"
            value["signature"] = x.custody.create_custody_seal_signature(
                x.helper.executor.canonical_json_bytes(value["payload"]), custody_hmac_key=x.key
            )
        seal.write_bytes(x.helper.executor.canonical_json_bytes(value))
    before = _v2_private_tree_bytes(x.root)
    with pytest.raises(official.LauncherError):
        with x.reader.capture():
            pytest.fail("Required terminal authentication was omitted")
    assert _v2_private_tree_bytes(x.root) == before


def test_v2_terminal_reader_retains_same_bytes_seal_inode(v2_terminal_artifact_reader):
    x = v2_terminal_artifact_reader
    with pytest.raises(official.LauncherError):
        with x.reader.capture() as (_, _, recheck):
            path = x.state.run_root / ".s11-finalized"
            original = path.with_suffix(".retained-original")
            raw = path.read_bytes()
            path.rename(original)
            path.write_bytes(raw)
            path.chmod(0o600)
            assert path.stat().st_ino != original.stat().st_ino
            recheck()


@pytest.mark.parametrize("stage", ["unlock", "lock-close", "final-fd"])
def test_v2_terminal_reader_original_deadline_after_real_io(
    v2_terminal_artifact_reader, monkeypatch, stage
):
    x = v2_terminal_artifact_reader
    import time

    actual_unlock, actual_close = fcntl.flock, os.close
    expired = []
    selected = (
        x.state.run_root / ".s11-finalized" if stage == "final-fd" else x.root / ".s11-custody.lock"
    )
    info = selected.stat()
    identity = info.st_dev, info.st_ino

    def expire():
        expired.append(True)
        monkeypatch.setattr(time, "monotonic", lambda: x.reader.deadline + 1)

    def unlock(fd, mode):
        actual_unlock(fd, mode)
        if stage == "unlock" and mode == fcntl.LOCK_UN and not expired:
            expire()

    def close(fd):
        info = os.fstat(fd)
        actual_close(fd)
        if stage != "unlock" and (info.st_dev, info.st_ino) == identity and not expired:
            expire()

    monkeypatch.setattr(fcntl, "flock", unlock)
    monkeypatch.setattr(os, "close", close)
    with pytest.raises(official.LauncherError, match="native-terminal-deadline"):
        with x.reader.capture():
            pass
    assert expired == [True]


def test_v2_terminal_reader_releases_custody_before_publication_io(v2_terminal_artifact_reader):
    x = v2_terminal_artifact_reader
    with x.reader.capture() as (_, _, recheck):
        fd = os.open(x.root / ".s11-custody.lock", os.O_RDONLY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        assert recheck() is None


def test_v2_terminal_reader_decision_plan_matches_manifest(v2_terminal_artifact_reader):
    x = v2_terminal_artifact_reader
    reader = v2_terminal_resigned_decision(x, plan_sha256="e" * 64)
    with pytest.raises(official.LauncherError):
        with reader.capture():
            pytest.fail("Authenticated decision plan differed from sealed execution identity")


@pytest.fixture
def v2_terminal_reader_nd_recorded_custody_error(monkeypatch, request):
    """Damage synthetic payload at the first real post-SINGLE custody verification."""
    from apps.experiments_app.tests import test_ex10_executor as helper

    actual = helper.executor.EvidenceStore.verify
    damaged = []

    def verify(store, run_id):
        if not damaged:
            record = store.persist_json(
                run_id=run_id,
                scenario_id="EX-10",
                artifact_type="ex10-synthetic-retained-metadata",
                kind=helper.executor.ArtifactKind.METADATA,
                classification=helper.executor.Classification.INTERNAL,
                filename="synthetic-retained-metadata.json",
                payload={"synthetic_fixture": True},
                source="synthetic-terminal-fixture",
            )
            path = store.root / "runs" / run_id / record.relative_path
            path.write_bytes(helper.executor.canonical_json_bytes({"synthetic_fixture": False}))
            damaged.append(True)
        return actual(store, run_id)

    # Keep real verification and its errors. Inject damage only when the real
    # finalizer reaches verification after its authenticated SINGLE5 exchange.
    monkeypatch.setattr(helper.executor.EvidenceStore, "verify", verify)
    x = request.getfixturevalue("v2_terminal_artifact_reader")
    assert damaged == [True] and x.result["custody_errors"]
    assert x.result["decision"] == "NOT_DEMONSTRATED"
    return x


def test_v2_terminal_reader_nd_preserves_previously_recorded_custody_errors(
    v2_terminal_reader_nd_recorded_custody_error,
):
    x = v2_terminal_reader_nd_recorded_custody_error
    before = _v2_private_tree_bytes(x.root)
    with x.reader.capture() as (_, result, recheck):
        assert result == x.result and result["custody_errors"] and recheck() is None
    assert _v2_private_tree_bytes(x.root) == before


def v2_terminal_authenticated_binding_format(x, decision):
    """Pure signed format fixture; PASS/FAIL here never claims a scientific run."""
    import copy

    records = copy.deepcopy(x.state.records)
    dr = next(r for r in records if r["artifact_type"] == "ex10-decision")
    fr = next(r for r in records if r["artifact_type"] == "run-manifest-final")
    result = dict(
        x.result,
        decision=decision,
        binding=True,
        lifecycle="FINALIZED_" + decision,
        reasons=[],
        manifest_status="COMPLETED",
        custody_errors=[],
    )
    path = x.state.run_root / dr["relative_path"]
    value = json.loads(path.read_bytes())
    value.update(
        {k: result[k] for k in ("decision", "binding", "lifecycle", "reasons", "manifest_status")}
    )
    raw = x.helper.executor.canonical_json_bytes(value)
    path.write_bytes(raw)
    dr.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
    path = x.state.run_root / fr["relative_path"]
    value = json.loads(path.read_bytes())
    value.update(status="COMPLETED", invalidity_reason=None)
    value["artifact_references"] = [
        {k: r[k] for k in ("artifact_id", "relative_path", "sha256")}
        for r in records
        if r is not fr
    ]
    raw = x.helper.executor.canonical_json_bytes(value)
    path.write_bytes(raw)
    fr.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
    for record in (dr, fr):
        record["custody_signature"] = x.custody.create_custody_record_signature(
            record, custody_hmac_key=x.key
        )
    raw = b"".join(x.helper.executor.canonical_json_bytes(record) for record in records)
    (x.root / x.reader.reader.index).write_bytes(raw)
    (x.root / x.reader.reader.signature).write_text(
        x.custody.create_custody_index_signature(raw, custody_hmac_key=x.key) + "\n"
    )
    path = x.state.run_root / ".s11-finalized"
    seal = json.loads(path.read_bytes())
    seal["payload"]["manifest_sha256"] = fr["sha256"]
    seal["signature"] = x.custody.create_custody_seal_signature(
        x.helper.executor.canonical_json_bytes(seal["payload"]), custody_hmac_key=x.key
    )
    path.write_bytes(x.helper.executor.canonical_json_bytes(seal))
    result.update(decision_sha256=dr["sha256"], manifest_sha256=fr["sha256"])
    assert (
        x.helper.executor.validate_finalized_state(
            x.state.run_root, records, custody_hmac_key=x.key
        )
        == seal["payload"]
    )
    return official.NativeTerminalArtifactReader(
        apparatus=x.reader.apparatus,
        admitted=x.reader.admitted,
        action="finalize",
        expected_run_id=result["run_id"],
        result_bytes=x.helper.executor.canonical_json_bytes(result),
        custody_hmac_key=x.key,
        deadline=x.reader.deadline,
        reconciled=decision,
    ), result


@pytest.mark.parametrize("decision", ["PASS", "FAIL"])
def test_v2_terminal_reader_binding_format_uses_completed_manifest(
    v2_terminal_artifact_reader, decision
):
    reader, expected = v2_terminal_authenticated_binding_format(
        v2_terminal_artifact_reader, decision
    )
    before = _v2_private_tree_bytes(v2_terminal_artifact_reader.root)
    with reader.capture() as (_, result, recheck):
        assert result == expected and result["binding"] is True
        assert result["manifest_status"] == "COMPLETED" and recheck() is None
    assert _v2_private_tree_bytes(v2_terminal_artifact_reader.root) == before


def v2_signed_terminal_single_setup(x):
    single = x.helper.v2_native_single_setup(x.rig, action="finalize", client_proofs=True)
    mod, a, source = x.fixture.module, x.fixture.rig.a, single.broker.sources
    assert single.client_proof_bindings is not None
    proof = single.client_proof_bindings.proof
    directory = single.store.root / ".s11-operational" / proof.exchange_id
    directory.mkdir(mode=0o700)
    private = mod.op.durable.PrivateStore(str(directory), mod.h.identity(directory))
    a.stores["w09-signer-single-" + proof.exchange_id] = private
    copied = []

    def publish(role, raw):
        assert private.create_exact(str(directory / (role + ".json")), raw) is None
        copied.append(role)

    signer = official.NativeBridgeProofSigner(
        apparatus={"ex10_operational_context": mod.op},
        signer=a.bridge,
        bridge_pin=a.bridge_pin,
        action="finalize",
        exchange_id=single.host.exchange_id,
        expected_run_id=x.rig.context.proposal["run_id"],
        admitted=x.fixture.native.qualifier.qualify(),
        publish_role=publish,
    )
    new = mod.op.NativeHostSources(
        session_qualifier=source.sessions,
        registry=source.registry,
        policy=source.policy,
        authority_reader=source.reader,
        historical_contexts=source.histories,
        artifact_store=source.artifacts,
        certificate_store=source.certificates,
        publisher=source.publisher,
        executor_fingerprint=source.fingerprint,
        signer=signer,
        bridge_pin=source.pin,
    )
    new.verify_registry(canonical_binding=a.journal.binding, journal=a.journal)
    a.journal.sources = new
    signer.bind_once(sources=new, journal=a.journal)
    single.broker = mod.op.NativeBridgeBroker(
        journal=a.journal,
        session_qualifier=x.fixture.native.qualifier,
        signer=signer,
        host_sources=new,
        holder_admitter=single.broker.admit,
    )
    return single, copied, signer, new


@pytest.fixture
def v2_lifecycle_terminal(v2_bridge_proof_signer):
    """Actual frozen START9/SINGLE5/newseal and native signer; no Docker exit claim."""
    from apps.experiments_app import custody_policy, manifest

    x = v2_bridge_proof_signer
    _, errors, responses = x.helper.v2_run_native_host(x.rig, lambda: x.helper.v2_start_call(x.rig))
    assert not errors and len(responses) == 1
    single, copied, signer, source = v2_signed_terminal_single_setup(x)

    def prepare(*, context, store, run_id, result):
        assert context is single.context and store is single.store and run_id == result["run_id"]
        state = x.helper.executor.load_existing_state_readonly(store, run_id)
        assert state is not None and state.finalized
        x.helper.executor.validate_finalized_state(
            state.run_root, state.records, custody_hmac_key=store.custody_hmac_key
        )

    single.context.prepare_terminal = prepare
    try:
        result, replies = x.helper.v2_run_single_host(
            single,
            lambda: x.helper.executor.finalize_run(
                single.store,
                run_id=x.rig.context.proposal["run_id"],
                git_sha=x.rig.context.proposal["git_sha"],
                abandon_reason="synthetic-native-terminal-pre-t0",
                probes=x.rig.probes,
                operational_context=single.context,
            ),
        )
        assert (
            len(replies) == 1
            and single.context.finished
            and single.host.closed
            and signer.index == 3
        )
        assert signer.signed_response == replies[0]
        single.context.persist_public_result(single.store, result)
        raw = x.helper.executor.canonical_json_bytes(result)
        reader = official.NativeTerminalArtifactReader(
            apparatus={
                "ex10_operational_context": x.fixture.module.op,
                "custody_policy": custody_policy,
                "manifest": manifest,
            },
            admitted=signer.admitted,
            action="abandon-official-pre-t0",
            expected_run_id=result["run_id"],
            result_bytes=raw,
            custody_hmac_key=single.store.custody_hmac_key,
            deadline=x.helper.time.monotonic() + 30,
        )
        calls, observations = [], {"approval": True, "worker": True}

        def approve_terminal(**kw):
            kw["writer"].check()
            assert kw["action"] == reader.action and kw["admitted"] is signer.admitted
            assert kw["record"]["run_id"] == result["run_id"] and kw["result"] == result
            calls.append("approved-action")
            if not observations["approval"]:
                raise RuntimeError("synthetic existing action approval denied")

        def verify_worker(**kw):
            # Fixed synthetic consumer: actual finished local client and actual
            # private writer bytes. The later real factory must also qualify
            # FixedDockerAdapter.finish_client and actual one-off PID1 exit0.
            assert kw["result_bytes"] == raw and single.context.finished and single.host.closed
            path = single.store.root / ("executor-result." + signer.exchange + ".json")
            assert path.read_bytes() == raw
            calls.append("finished-worker-provider")
            if not observations["worker"]:
                raise RuntimeError("synthetic worker output qualification denied")

        yield SimpleNamespace(
            x=x,
            single=single,
            source=source,
            signer=signer,
            reader=reader,
            result=result,
            raw=raw,
            calls=calls,
            observations=observations,
            approve=approve_terminal,
            worker=verify_worker,
            copied=copied,
        )
    finally:
        single.context.channel.close()
        single.host.close()


def test_v2_lifecycle_terminal_actual_b3_publication_and_retire(v2_lifecycle_terminal, monkeypatch):
    x = v2_lifecycle_terminal
    op = x.x.fixture.module.op
    before = _v2_private_tree_bytes(x.single.store.root)
    prior = x.source.read_authority()
    assert op.history.find_run(prior, x.result["run_id"])["lifecycle_state"] == "OPEN"
    lifecycle = official.NativeLifecycleTerminalPublisher(
        broker=x.single.broker, reader=x.reader, approve_terminal=x.approve, verify_worker=x.worker
    )
    x.source.lifecycle = lifecycle
    actual = lifecycle.qualify_terminal
    diagnostics = []

    def qualified(**kwargs):
        try:
            return actual(**kwargs)
        except BaseException as error:
            import traceback

            diagnostics.append(
                {
                    "type": type(error).__name__,
                    "code": getattr(error, "code", None),
                    "locations": [
                        (f.name, f.lineno) for f in traceback.extract_tb(error.__traceback__)
                    ],
                }
            )
            raise

    monkeypatch.setattr(lifecycle, "qualify_terminal", qualified)
    try:
        snapshot, retired = lifecycle.retire()
    except Exception as error:
        error.add_note("Synthetic lifecycle diagnostic (no values): " + repr(diagnostics))
        raise
    assert op.history.find_run(snapshot, x.result["run_id"])["result"] == "NOT_DEMONSTRATED"
    assert (
        op.history.current_revision(snapshot)["revision"]
        == op.history.current_revision(prior)["revision"] + 1
    )
    state = x.source.journal.read()
    assert state["active"] is None and state["witness_active"] is None
    assert retired["terminal_witness_reference"] is not None
    assert _v2_private_tree_bytes(x.single.store.root) == before
    assert x.calls.count("approved-action") >= 2 and x.calls.count("finished-worker-provider") >= 2
    assert lifecycle.verification_count == 2
    with pytest.raises(official.LauncherError, match="native-terminal-publication-consumed"):
        lifecycle.retire()


@pytest.mark.parametrize("fault", ["deadline", "approval"])
def test_v2_lifecycle_terminal_no_cas_after_actual_candidate_io(
    v2_lifecycle_terminal, monkeypatch, fault
):
    import time

    x = v2_lifecycle_terminal
    publisher = x.source.publisher
    lifecycle = official.NativeLifecycleTerminalPublisher(
        broker=x.single.broker, reader=x.reader, approve_terminal=x.approve, verify_worker=x.worker
    )
    x.source.lifecycle = lifecycle
    before = publisher.transport.calls.count("B3_CAS")
    actual = publisher.candidates.create_exact
    changed = []

    def created(path, raw):
        result = actual(path, raw)
        if path.endswith("/" + lifecycle.publication_id + ".json"):
            changed.append(True)
            if fault == "deadline":
                monkeypatch.setattr(time, "monotonic", lambda: lifecycle.reader.deadline + 1)
            else:
                x.observations["approval"] = False
        return result

    monkeypatch.setattr(publisher.candidates, "create_exact", created)
    with pytest.raises(official.LauncherError):
        lifecycle.retire()
    assert changed == [True], "Actual candidate IO was not reached"
    assert (
        publisher.transport.calls.count("B3_CAS") == before
    ), "Terminal CAS dispatched after actual candidate IO invalidated admission"
    with pytest.raises(official.LauncherError, match="native-terminal-publication-consumed"):
        lifecycle.retire()
    assert publisher.transport.calls.count("B3_CAS") == before


def test_v2_lifecycle_terminal_rejects_foreign_physical_ledger(v2_lifecycle_terminal):
    import uuid

    x, op = v2_lifecycle_terminal, v2_lifecycle_terminal.x.fixture.module.op
    old = x.source.publisher
    directory = Path(old.ledger.root).parent / ("synthetic-foreign-ledger-" + uuid.uuid4().hex)
    directory.mkdir(mode=0o700)
    store = op.durable.PrivateStore(str(directory), x.x.fixture.module.h.identity(directory))
    x.x.fixture.rig.a.stores["w09-foreign-ledger"] = store
    raw = old.ledger.read(old.ledger.root + "/" + old.name)
    name = "foreign.sqlite3"
    store.create_exact(str(directory / name), raw)
    identity = x.x.fixture.module.h.identity(directory / name)
    with op.durable.existing_database(
        old.ledger, old.name, old.identity, schema_check=op.sql._verify_sql_schema
    ) as con:
        original = op.sql._read_state(con)
    with op.durable.existing_database(
        store, name, identity, schema_check=op.sql._verify_sql_schema
    ) as con:
        assert op.sql._read_state(con) == original
    x.source.publisher = op.NativeOpenPublisher(
        ledger_store=store,
        ledger_name=name,
        ledger_identity=identity,
        candidate_store=old.candidates,
        floor=old.floor,
        transport=old.transport,
        signer_pin=old.pin,
        clock=old.clock,
        clock_health=old.health,
    )
    before = old.transport.calls.count("B3_CAS")
    candidates = _v2_private_tree_bytes(Path(old.candidates.root))
    foreign = _v2_private_tree_bytes(directory)
    with pytest.raises(official.LauncherError):
        lifecycle = official.NativeLifecycleTerminalPublisher(
            broker=x.single.broker,
            reader=x.reader,
            approve_terminal=x.approve,
            verify_worker=x.worker,
        )
        x.source.lifecycle = lifecycle
        lifecycle.retire()
    assert (
        old.transport.calls.count("B3_CAS") == before
    ), "Foreign physical ledger received terminal CAS"
    assert (
        _v2_private_tree_bytes(Path(old.candidates.root)) == candidates
    ), "Foreign physical ledger received candidate writes"
    assert (
        _v2_private_tree_bytes(directory) == foreign
    ), "Foreign physical ledger received intent or SQL writes"


def test_v2_lifecycle_terminal_no_retire_after_actual_b4_head_io(
    v2_lifecycle_terminal, monkeypatch
):
    import time

    x = v2_lifecycle_terminal
    journal = x.source.journal
    lifecycle = official.NativeLifecycleTerminalPublisher(
        broker=x.single.broker, reader=x.reader, approve_terminal=x.approve, verify_worker=x.worker
    )
    x.source.lifecycle = lifecycle
    remote = journal.transport
    before = remote.calls.count("RETIRE_TERMINAL")
    actual = journal.fresh_head
    changed = []

    def observed(state):
        result = actual(state)
        if lifecycle.published is not None and not changed:
            changed.append(True)
            monkeypatch.setattr(time, "monotonic", lambda: lifecycle.reader.deadline + 1)
        return result

    monkeypatch.setattr(journal, "fresh_head", observed)
    with pytest.raises(official.LauncherError):
        lifecycle.retire()
    assert changed == [True], "Actual postpublication B4 head IO was not reached"
    assert (
        remote.calls.count("RETIRE_TERMINAL") == before
    ), "RETIRE dispatched after actual B4 head IO expired original admission"
    with pytest.raises(official.LauncherError, match="native-terminal-publication-consumed"):
        lifecycle.retire()


def test_v2_lifecycle_terminal_retains_exact_signed_provenance(v2_lifecycle_terminal):
    x = v2_lifecycle_terminal
    lifecycle = official.NativeLifecycleTerminalPublisher(
        broker=x.single.broker, reader=x.reader, approve_terminal=x.approve, verify_worker=x.worker
    )
    x.source.lifecycle = lifecycle
    lifecycle.provenance["nonce"] = "f" * 64
    with pytest.raises(official.LauncherError):
        lifecycle.retire()
        pytest.fail("Unauthenticated provenance copy was persisted as publication capability")


@pytest.mark.parametrize("provider", ["action", "worker"])
@pytest.mark.parametrize("fault", ["refusal", "boolean"])
def test_v2_lifecycle_terminal_independent_provider_required(
    v2_lifecycle_terminal, provider, fault
):
    x = v2_lifecycle_terminal
    actual = x.approve if provider == "action" else x.worker
    reached = []

    def denied(**kwargs):
        actual(**kwargs)
        reached.append(True)
        if fault == "refusal":
            raise RuntimeError("Synthetic fixed independent provider refusal")
        return True

    lifecycle = official.NativeLifecycleTerminalPublisher(
        broker=x.single.broker,
        reader=x.reader,
        approve_terminal=denied if provider == "action" else x.approve,
        verify_worker=denied if provider == "worker" else x.worker,
    )
    x.source.lifecycle = lifecycle
    b3, b4 = lifecycle.remote_b3, lifecycle.remote_b4
    before = b3.calls.count("B3_CAS"), b4.calls.count("RETIRE_TERMINAL")
    artifacts = _v2_private_tree_bytes(x.single.store.root)
    with pytest.raises(official.LauncherError):
        lifecycle.retire()
        pytest.fail("Missing independent terminal provider was accepted")
    assert reached == [True], "Actual fixed provider was not invoked"
    assert (b3.calls.count("B3_CAS"), b4.calls.count("RETIRE_TERMINAL")) == before
    assert _v2_private_tree_bytes(x.single.store.root) == artifacts
    with pytest.raises(official.LauncherError, match="native-terminal-publication-consumed"):
        lifecycle.retire()


@pytest.mark.parametrize("fault", ["signature", "request"])
def test_v2_lifecycle_terminal_authenticated_transcript_required(v2_lifecycle_terminal, fault):
    import uuid

    x = v2_lifecycle_terminal
    op = x.x.fixture.module.op
    value = op.wire.parse_response_bytes(x.signer.signed_response).to_dict()
    if fault == "signature":
        value["signature"] = "0" * 128
    else:
        value["request_id"] = str(uuid.uuid4())
    # Change both cached representations so the exact-copy guard passes;
    # the actual wire signature and approved-request binding must still deny.
    x.signer.signed_response = op.sql.canonical_bytes(value)
    before = x.source.publisher.transport.calls.count("B3_CAS")
    with pytest.raises(official.LauncherError):
        lifecycle = official.NativeLifecycleTerminalPublisher(
            broker=x.single.broker,
            reader=x.reader,
            approve_terminal=x.approve,
            verify_worker=x.worker,
        )
        x.source.lifecycle = lifecycle
        lifecycle.retire()
        pytest.fail("Unauthenticated terminal transcript was accepted")
    assert x.source.publisher.transport.calls.count("B3_CAS") == before


@pytest.mark.parametrize("phase", [0, 1])
def test_v2_lifecycle_terminal_exact_candidate_each_verification(
    v2_lifecycle_terminal, monkeypatch, phase
):
    x = v2_lifecycle_terminal
    op = x.x.fixture.module.op
    lifecycle = official.NativeLifecycleTerminalPublisher(
        broker=x.single.broker,
        reader=x.reader,
        approve_terminal=x.approve,
        verify_worker=x.worker,
    )
    x.source.lifecycle = lifecycle
    actual = op.history._publication
    reached = []

    def publication(*args, **kwargs):
        verifier = kwargs["verifier"]

        class AlteredCandidate:
            def verify(self, **values):
                if lifecycle.verification_count == phase:
                    candidate = op.sql.parse_canonical(values["candidate_state"])
                    candidate["official_runs"][-1]["source_revision"] = "f" * 40
                    values["candidate_state"] = op.sql.canonical_bytes(candidate)
                    reached.append(True)
                return verifier.verify(**values)

        kwargs["verifier"] = AlteredCandidate()
        return actual(*args, **kwargs)

    monkeypatch.setattr(op.history, "_publication", publication)
    b3, b4 = lifecycle.remote_b3, lifecycle.remote_b4
    before = b3.calls.count("B3_CAS"), b4.calls.count("RETIRE_TERMINAL")
    with pytest.raises(official.LauncherError):
        lifecycle.retire()
        pytest.fail("Altered terminal candidate was accepted by the actual verifier")
    assert reached == [True], "Actual requested verification phase was not reached"
    assert b3.calls.count("B3_CAS") == before[0] + phase
    assert b4.calls.count("RETIRE_TERMINAL") == before[1]
    state = lifecycle.journal.read()
    assert state["active"] is not None and lifecycle.published is None
    with pytest.raises(official.LauncherError, match="native-terminal-publication-consumed"):
        lifecycle.retire()


@pytest.mark.parametrize("provider", ["action", "worker"])
def test_v2_lifecycle_terminal_provider_revocation_after_b4_io(
    v2_lifecycle_terminal, monkeypatch, provider
):
    x = v2_lifecycle_terminal
    lifecycle = official.NativeLifecycleTerminalPublisher(
        broker=x.single.broker,
        reader=x.reader,
        approve_terminal=x.approve,
        verify_worker=x.worker,
    )
    x.source.lifecycle = lifecycle
    actual = lifecycle.journal.fresh_head
    reached = []

    def observed(state):
        result = actual(state)
        if lifecycle.published is not None and not reached:
            reached.append(True)
            x.observations["approval" if provider == "action" else "worker"] = False
        return result

    monkeypatch.setattr(lifecycle.journal, "fresh_head", observed)
    b3, b4 = lifecycle.remote_b3, lifecycle.remote_b4
    before = b3.calls.count("B3_CAS"), b4.calls.count("RETIRE_TERMINAL")
    with pytest.raises(official.LauncherError):
        lifecycle.retire()
        pytest.fail("RETIRE accepted a revoked independent terminal provider")
    assert reached == [True], "Actual postpublication B4 IO was not reached"
    assert b3.calls.count("B3_CAS") == before[0] + 1
    assert b4.calls.count("RETIRE_TERMINAL") == before[1]
    assert lifecycle.journal.read()["active"] is not None
    with pytest.raises(official.LauncherError, match="native-terminal-publication-consumed"):
        lifecycle.retire()


def test_v2_lifecycle_terminal_retained_seal_after_actual_cas(v2_lifecycle_terminal, monkeypatch):
    x = v2_lifecycle_terminal
    state = x.x.helper.executor.load_existing_state_readonly(x.single.store, x.result["run_id"])
    path = state.run_root / ".s11-finalized"
    lifecycle = official.NativeLifecycleTerminalPublisher(
        broker=x.single.broker,
        reader=x.reader,
        approve_terminal=x.approve,
        verify_worker=x.worker,
    )
    x.source.lifecycle = lifecycle
    actual = lifecycle.remote_b3.publish_b3
    reached = []

    def published(*args, **kwargs):
        reply = actual(*args, **kwargs)
        raw, identity = path.read_bytes(), path.stat().st_ino
        path.rename(path.with_name("synthetic-retained-original-seal"))
        path.write_bytes(raw)
        path.chmod(0o600)
        assert path.stat().st_ino != identity
        reached.append(True)
        return reply

    monkeypatch.setattr(lifecycle.remote_b3, "publish_b3", published)
    before = lifecycle.remote_b4.calls.count("RETIRE_TERMINAL")
    with pytest.raises(official.LauncherError):
        lifecycle.retire()
        pytest.fail("Replaced retained terminal seal was accepted after actual CAS")
    assert reached == [True], "Actual terminal CAS was not reached"
    assert lifecycle.remote_b4.calls.count("RETIRE_TERMINAL") == before
    assert lifecycle.journal.read()["active"] is not None
    with pytest.raises(official.LauncherError, match="native-terminal-publication-consumed"):
        lifecycle.retire()


def test_v2_lifecycle_terminal_original_b3_after_last_fd_close(v2_lifecycle_terminal, monkeypatch):
    x = v2_lifecycle_terminal
    state = x.x.helper.executor.load_existing_state_readonly(x.single.store, x.result["run_id"])
    info = (state.run_root / ".s11-finalized").stat()
    target = info.st_dev, info.st_ino
    lifecycle = official.NativeLifecycleTerminalPublisher(
        broker=x.single.broker,
        reader=x.reader,
        approve_terminal=x.approve,
        verify_worker=x.worker,
    )
    x.source.lifecycle = lifecycle
    actual = os.close
    reached = []

    def closed(fd):
        info = os.fstat(fd)
        result = actual(fd)
        if (info.st_dev, info.st_ino) == target and lifecycle.published is not None and not reached:
            reached.append(True)
            # Only the original B3 UTC lease expires; custody monotonic deadline
            # stays current, so it cannot mask the final original-B3 guard.
            expiry = lifecycle.op.sql.utc_time(lifecycle.original.expires_at) + timedelta(seconds=1)
            clock = lifecycle.op.coordination._utc(expiry)
            monkeypatch.setattr(lifecycle.op.history, "_now", lambda: clock)
        return result

    monkeypatch.setattr(os, "close", closed)
    before = lifecycle.remote_b4.calls.count("RETIRE_TERMINAL")
    with pytest.raises(official.LauncherError):
        lifecycle.retire()
        pytest.fail("Original B3 expiry after actual final FD close was ignored")
    assert reached == [True], "Actual retained seal FD cleanup was not reached"
    assert lifecycle.remote_b4.calls.count("RETIRE_TERMINAL") == before + 1
    assert lifecycle.journal.read()["active"] is None
    with pytest.raises(official.LauncherError, match="native-terminal-publication-consumed"):
        lifecycle.retire()


def test_v2_terminal_reader_retains_initial_result_bytes(v2_terminal_artifact_reader):
    x = v2_terminal_artifact_reader
    with pytest.raises(official.LauncherError):
        with x.reader.capture() as (_, result, recheck):
            result["reasons"] = ["Synthetic postcapture mutable input"]
            x.reader.result_bytes = x.helper.executor.canonical_json_bytes(result)
            recheck()
        pytest.fail("Captured terminal result lost its original byte binding")


def test_v2_lifecycle_terminal_signed_response_matches_approved_request(v2_lifecycle_terminal):
    import uuid

    x = v2_lifecycle_terminal
    op = x.x.fixture.module.op
    value = op.wire.parse_response_bytes(x.signer.signed_response).to_dict()
    value["request_id"] = str(uuid.uuid4())
    value["response_digest"] = op.wire.response_digest(
        op.wire.parse_response_bytes(op.sql.canonical_bytes(value))
    )
    value["signature"] = x.x.fixture.rig.a.bridge.sign(
        op.wire.signing_preimage(op.wire.parse_response_bytes(op.sql.canonical_bytes(value)))
    ).hex()
    parsed = op.wire.parse_response_bytes(op.sql.canonical_bytes(value))
    op.wire._verify_signature("response", parsed.to_dict(), x.signer.pin.public_key)
    assert parsed.to_dict()["response_digest"] == op.wire.response_digest(parsed)
    x.signer.signed_response = parsed.canonical
    before = x.source.publisher.transport.calls.count("B3_CAS")
    with pytest.raises(official.LauncherError):
        lifecycle = official.NativeLifecycleTerminalPublisher(
            broker=x.single.broker,
            reader=x.reader,
            approve_terminal=x.approve,
            verify_worker=x.worker,
        )
        x.source.lifecycle = lifecycle
        lifecycle.retire()
        pytest.fail("Validly signed response for a different approved request was accepted")
    assert x.source.publisher.transport.calls.count("B3_CAS") == before


@pytest.fixture
def v2_session_action_commit(v2_action_intent):
    """B2 transaction only: actual source/profile/intent, synthetic exact host approval.

    This fixture does not claim a completed scientific START, native broker,
    Docker PID1, global RUN_OPENED or operational permission. The fixed host
    completion verifier of the full factory must prove those independently.
    """
    x = v2_action_intent
    pending = x.intent.begin()
    codec = x.host.apparatus["ex10_session_v2"]
    result = {
        "run_id": RUN_ID,
        "scenario_id": "EX-10",
        "evidence_mode": "OFFICIAL",
        "phase": "PREPARED",
        "manifest_sha256": "a" * 64,
        "start_sha256": "b" * 64,
        "preflight_sha256": "c" * 64,
    }
    raw = x.host.module.base.canonical_bytes(result)
    payload = dict(pending.to_dict()["history"][-1]["payload"])
    payload.update(
        event="complete-start-official",
        official_run_id=RUN_ID,
        preflight_report_sha256="d" * 64,
        operational_envelope_sha256="e" * 64,
        in_flight=[],
        state={
            "known_phase": "PREPARED",
            "uncertain": False,
            "blocked": False,
            "next_allowed_actions": [
                "abandon-official-pre-t0",
                "execute-official",
                "inspect",
                "status",
            ],
        },
    )
    proposal = codec.append_history_entry_v2(pending, payload)
    events, controls = [], {"allow": True, "hook": None}

    def qualified(**facts):
        facts["writer"].check()
        assert controls["allow"], "Synthetic independent completion approval refused"
        assert facts["action"] == x.invocation.action
        assert facts["pending_bytes"] == pending._bytes
        assert facts["proposal_bytes"] == proposal._bytes
        assert facts["result_bytes"] == raw
        current = x.bootstrap.load(x.invocation)
        assert current._bytes == (proposal._bytes if facts["written"] else pending._bytes)
        events.append("qualified-after" if facts["written"] else "qualified-before")
        if controls["hook"] is not None:
            return controls["hook"](facts)

    yield SimpleNamespace(
        x=x,
        pending=pending,
        proposal=proposal,
        result=result,
        raw=raw,
        payload=payload,
        qualified=qualified,
        events=events,
        controls=controls,
    )


def test_v2_action_commit_exact_single_entry_and_checkpoint(v2_session_action_commit):
    x = v2_session_action_commit
    commit = x.x.host.module.NativeSessionActionCommit(
        invocation=x.x.invocation,
        bootstrap=x.x.bootstrap,
        pending=x.pending,
        proposal=x.proposal,
        result_bytes=x.raw,
        qualify_completion=x.qualified,
    )
    completed = commit.commit()
    assert completed._bytes == x.proposal._bytes
    assert completed.header == x.pending.header
    assert completed._expected_count == x.pending._expected_count + 1
    assert x.x.bootstrap.load(x.x.invocation)._bytes == completed._bytes
    assert x.x.host.checkpoint["value"] == (completed._expected_tip, completed._expected_count)
    assert completed.to_dict()["in_flight"] == []
    assert x.events == ["qualified-before", "qualified-after"]
    with pytest.raises(x.x.host.module.LauncherError, match="native-action-commit-consumed"):
        commit.commit()


def test_v2_action_commit_start_result_matches_proposed_attachment(v2_session_action_commit):
    """Pure proposed bytes only; this grants neither another START nor an ACK."""
    x = v2_session_action_commit
    codec = x.x.host.apparatus["ex10_session_v2"]
    payload = dict(x.pending.to_dict()["history"][-1]["payload"])
    payload["official_run_id"] = OTHER_RUN
    pending = codec.append_history_entry_v2(x.pending, payload)
    proposal = codec.append_history_entry_v2(pending, x.payload)
    before = _v2_private_tree_bytes(x.x.host.home)
    commit = x.x.host.module.NativeSessionActionCommit(
        invocation=x.x.invocation,
        bootstrap=x.x.bootstrap,
        pending=pending,
        proposal=proposal,
        result_bytes=x.raw,
        qualify_completion=x.qualified,
    )
    assert commit._validate()[1]._bytes == proposal._bytes
    assert x.events == [] and _v2_private_tree_bytes(x.x.host.home) == before


def v2_action_commit(x, *, pending=None, proposal=None, result_bytes=None):
    return x.x.host.module.NativeSessionActionCommit(
        invocation=x.x.invocation,
        bootstrap=x.x.bootstrap,
        pending=x.pending if pending is None else pending,
        proposal=x.proposal if proposal is None else proposal,
        result_bytes=x.raw if result_bytes is None else result_bytes,
        qualify_completion=x.qualified,
    )


@pytest.mark.parametrize("fault", ["extra-entry", "header", "prefix", "run", "event", "phase"])
def test_v2_action_commit_rejects_unrelated_proposal(v2_session_action_commit, fault):
    x = v2_session_action_commit
    codec = x.x.host.apparatus["ex10_session_v2"]
    if fault == "extra-entry":
        proposal = codec.append_history_entry_v2(x.proposal, x.payload)
    elif fault in {"header", "prefix"}:
        header = dict(x.pending.header)
        if fault == "header":
            header["created_at"] = (
                datetime.fromisoformat(header["created_at"].replace("Z", "+00:00"))
                + timedelta(seconds=1)
            ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        proposal = codec.new_official_session_v2(header, profile=x.x.host.profile)
        for i, entry in enumerate(x.pending.to_dict()["history"]):
            payload = dict(entry["payload"])
            if fault == "prefix" and i == 0:
                payload["event"] = "synthetic-unrelated-prefix"
            proposal = codec.append_history_entry_v2(proposal, payload)
        proposal = codec.append_history_entry_v2(proposal, x.payload)
    else:
        payload = dict(x.payload)
        if fault == "run":
            payload["official_run_id"] = OTHER_RUN
        elif fault == "event":
            payload["event"] = "complete-execute-official"
        else:
            payload["state"] = dict(payload["state"], known_phase="RECONCILING")
        proposal = codec.append_history_entry_v2(x.pending, payload)
    before = _v2_private_tree_bytes(x.x.host.home)
    with pytest.raises(x.x.host.module.LauncherError):
        v2_action_commit(x, proposal=proposal)
        pytest.fail("Unrelated B2 completion proposal was accepted as valid")
    assert _v2_private_tree_bytes(x.x.host.home) == before and x.events == []


@pytest.mark.parametrize("written", [False, True])
@pytest.mark.parametrize("fault", ["refusal", "boolean"])
def test_v2_action_commit_independent_verifier_before_and_after(
    v2_session_action_commit, written, fault
):
    x = v2_session_action_commit

    def denied(facts):
        if facts["written"] == written:
            if fault == "refusal":
                raise RuntimeError("Synthetic independent completion denial")
            return True
        return None

    x.controls["hook"] = denied
    before = _v2_private_tree_bytes(x.x.host.home)
    checkpoint = x.x.host.checkpoint["value"]
    commit = v2_action_commit(x)
    with pytest.raises(x.x.host.module.LauncherError):
        commit.commit()
        pytest.fail("Missing independent B2 completion verifier was accepted")
    if written:
        # Preserve the actually written session and published checkpoint. A
        # denied final qualification never becomes a public success or retry.
        assert (x.x.host.root / x.x.host.module.SESSION_FILE).read_bytes() == x.proposal._bytes
        assert x.x.host.checkpoint["value"] == (
            x.proposal._expected_tip,
            x.proposal._expected_count,
        )
    else:
        assert _v2_private_tree_bytes(x.x.host.home) == before
        assert x.x.host.checkpoint["value"] == checkpoint
    with pytest.raises(x.x.host.module.LauncherError, match="native-action-commit-consumed"):
        commit.commit()


def test_v2_action_commit_state_advisory_requires_independent_approval(v2_session_action_commit):
    x = v2_session_action_commit
    codec = x.x.host.apparatus["ex10_session_v2"]
    payload = dict(x.payload)
    payload["state"] = dict(payload["state"], next_allowed_actions=["start-official"])
    proposal = codec.append_history_entry_v2(x.pending, payload)
    before = _v2_private_tree_bytes(x.x.host.home)
    with pytest.raises(x.x.host.module.LauncherError):
        v2_action_commit(x, proposal=proposal).commit()
        pytest.fail("Unapproved next actions were committed")
    assert _v2_private_tree_bytes(x.x.host.home) == before


def test_v2_action_commit_reloads_pending_after_actual_provider_write(v2_session_action_commit):
    x = v2_session_action_commit
    codec = x.x.host.apparatus["ex10_session_v2"]
    payload = dict(x.payload, event="synthetic-unapproved-replacement")
    unrelated = codec.append_history_entry_v2(x.pending, payload)
    changed = []
    checkpoint = x.x.host.checkpoint["value"]

    def replaced(facts):
        assert facts["written"] is False
        changed.append(True)
        codec.update_official_session_v2(
            x.x.host.root / x.x.host.module.SESSION_FILE,
            x.pending,
            unrelated,
            context=x.x.bootstrap.context(),
            profile=x.x.host.profile,
            caller_serialized=True,
        )

    x.controls["hook"] = replaced
    commit = v2_action_commit(x)
    with pytest.raises(x.x.host.module.LauncherError):
        commit.commit()
        pytest.fail("Changed pending session was accepted after provider IO")
    assert changed == [True]
    assert (x.x.host.root / x.x.host.module.SESSION_FILE).read_bytes() == unrelated._bytes
    assert x.x.host.checkpoint["value"] == checkpoint
    with pytest.raises(x.x.host.module.LauncherError, match="native-action-commit-consumed"):
        commit.commit()


def test_v2_action_commit_retains_original_bytes_after_provider_mutation(v2_session_action_commit):
    x = v2_session_action_commit
    commit = v2_action_commit(x)
    before = _v2_private_tree_bytes(x.x.host.home)
    changed = []

    def mutated(facts):
        assert facts["written"] is False
        changed.append(True)
        result = dict(x.result, manifest_sha256="f" * 64)
        commit.result_bytes = x.x.host.module.base.canonical_bytes(result)

    x.controls["hook"] = mutated
    with pytest.raises(x.x.host.module.LauncherError):
        commit.commit()
        pytest.fail("Completion bytes were replaced after independent qualification")
    assert changed == [True] and _v2_private_tree_bytes(x.x.host.home) == before


def test_v2_action_commit_checkpoint_loss_keeps_written_record(v2_session_action_commit):
    x = v2_session_action_commit
    before = x.x.host.checkpoint["value"]
    x.x.host.checkpoint["lose_publication"] = True
    commit = v2_action_commit(x)
    with pytest.raises(x.x.host.module.LauncherError, match="native-write-uncertain"):
        commit.commit()
    assert (x.x.host.root / x.x.host.module.SESSION_FILE).read_bytes() == x.proposal._bytes
    assert x.x.host.checkpoint["value"] == before
    assert x.events == ["qualified-before"]
    with pytest.raises(x.x.host.module.LauncherError, match="native-action-commit-consumed"):
        commit.commit()


def test_v2_action_commit_original_authority_after_actual_global_unlock(
    v2_session_action_commit, monkeypatch
):
    x = v2_session_action_commit
    history = x.x.host.apparatus["ex10_history_authority"]
    snapshots = []
    actual_read = x.x.host.authority.verify

    def observed():
        snapshot = actual_read()
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(x.x.host.authority, "verify", observed)
    info = x.x.host.guard.stat()
    identity = info.st_dev, info.st_ino
    actual = fcntl.flock
    changed = []

    def unlocked(fd, operation):
        info = os.fstat(fd)
        result = actual(fd, operation)
        if operation == fcntl.LOCK_UN and (info.st_dev, info.st_ino) == identity:
            changed.append(True)
            expiry = max(snapshot.expires_at for snapshot in snapshots)
            clock = (
                datetime.fromisoformat(expiry.replace("Z", "+00:00")) + timedelta(seconds=1)
            ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            monkeypatch.setattr(history, "_now", lambda: clock)
        return result

    monkeypatch.setattr(fcntl, "flock", unlocked)
    commit = v2_action_commit(x)
    with pytest.raises(x.x.host.module.LauncherError):
        commit.commit()
        pytest.fail("Original B3 expiry after actual writer unlock was ignored")
    assert changed == [True], "Actual final global unlock was not reached"
    assert (x.x.host.root / x.x.host.module.SESSION_FILE).read_bytes() == x.proposal._bytes
    assert x.x.host.checkpoint["value"] == (x.proposal._expected_tip, x.proposal._expected_count)
    with pytest.raises(x.x.host.module.LauncherError, match="native-action-commit-consumed"):
        commit.commit()


def test_v2_action_commit_preserves_typed_global_busy(v2_session_action_commit):
    x = v2_session_action_commit
    before = _v2_private_tree_bytes(x.x.host.home)
    commit = v2_action_commit(x)
    with x.x.bootstrap.writer():
        with pytest.raises(x.x.host.module.LauncherBusy):
            commit.commit()
    assert x.events == [] and _v2_private_tree_bytes(x.x.host.home) == before
    with pytest.raises(x.x.host.module.LauncherError, match="native-action-commit-consumed"):
        commit.commit()


def test_v2_action_commit_independent_checkpoint_changes_during_provider(v2_session_action_commit):
    x = v2_session_action_commit
    before = (x.x.host.root / x.x.host.module.SESSION_FILE).read_bytes()
    observed = []
    corrupt = ("f" * 64, x.pending._expected_count)

    def changed(facts):
        assert facts["written"] is False
        observed.append(True)
        x.x.host.checkpoint["value"] = corrupt

    x.controls["hook"] = changed
    commit = v2_action_commit(x)
    with pytest.raises(x.x.host.module.LauncherError):
        commit.commit()
        pytest.fail("Changed independent checkpoint was ignored after provider IO")
    assert observed == [True]
    assert (
        (x.x.host.root / x.x.host.module.SESSION_FILE).read_bytes() == before
    ), "B2 was written after independent checkpoint changed during provider IO"
    assert x.x.host.checkpoint["value"] == corrupt
    with pytest.raises(x.x.host.module.LauncherError, match="native-action-commit-consumed"):
        commit.commit()


def test_v2_action_commit_original_authority_after_actual_b2_write(
    v2_session_action_commit, monkeypatch
):
    x = v2_session_action_commit
    codec = x.x.host.apparatus["ex10_session_v2"]
    history = x.x.host.apparatus["ex10_history_authority"]
    snapshots = []
    actual_read = x.x.host.authority.verify
    actual_write = codec.update_official_session_v2
    changed = []
    checkpoint = x.x.host.checkpoint["value"]

    def observed():
        snapshot = actual_read()
        snapshots.append(snapshot)
        return snapshot

    def written(*args, **kwargs):
        value = actual_write(*args, **kwargs)
        expiry = max(snapshot.expires_at for snapshot in snapshots)
        clock = (
            datetime.fromisoformat(expiry.replace("Z", "+00:00")) + timedelta(seconds=1)
        ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        changed.append(True)
        monkeypatch.setattr(history, "_now", lambda: clock)
        return value

    monkeypatch.setattr(x.x.host.authority, "verify", observed)
    monkeypatch.setattr(codec, "update_official_session_v2", written)
    commit = v2_action_commit(x)
    with pytest.raises(x.x.host.module.LauncherError):
        commit.commit()
        pytest.fail("Original B3 expiry after actual B2 write was ignored")
    assert changed == [True] and x.events == ["qualified-before"]
    assert (x.x.host.root / x.x.host.module.SESSION_FILE).read_bytes() == x.proposal._bytes
    assert (
        x.x.host.checkpoint["value"] == checkpoint
    ), "Checkpoint was published after original B3 expiry at actual B2 IO"
    with pytest.raises(x.x.host.module.LauncherError, match="native-action-commit-consumed"):
        commit.commit()


def test_v2_action_commit_original_authority_after_final_global_fd_close(
    v2_session_action_commit, monkeypatch
):
    x = v2_session_action_commit
    history = x.x.host.apparatus["ex10_history_authority"]
    snapshots = []
    actual_read = x.x.host.authority.verify
    actual_unlock, actual_close = fcntl.flock, os.close
    info = x.x.host.guard.stat()
    identity = info.st_dev, info.st_ino
    released, changed = [], []

    def observed():
        snapshot = actual_read()
        snapshots.append(snapshot)
        return snapshot

    def unlocked(fd, operation):
        info = os.fstat(fd)
        result = actual_unlock(fd, operation)
        if operation == fcntl.LOCK_UN and (info.st_dev, info.st_ino) == identity:
            released.append(True)
        return result

    def closed(fd):
        info = os.fstat(fd)
        result = actual_close(fd)
        if released and (info.st_dev, info.st_ino) == identity and not changed:
            expiry = max(snapshot.expires_at for snapshot in snapshots)
            clock = (
                datetime.fromisoformat(expiry.replace("Z", "+00:00")) + timedelta(seconds=1)
            ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            changed.append(True)
            monkeypatch.setattr(history, "_now", lambda: clock)
        return result

    monkeypatch.setattr(x.x.host.authority, "verify", observed)
    monkeypatch.setattr(fcntl, "flock", unlocked)
    monkeypatch.setattr(os, "close", closed)
    commit = v2_action_commit(x)
    with pytest.raises(x.x.host.module.LauncherError):
        commit.commit()
        pytest.fail("Original B3 expiry after actual final global FD close was ignored")
    assert released == changed == [True], "Actual final global FD close was not reached"
    assert (x.x.host.root / x.x.host.module.SESSION_FILE).read_bytes() == x.proposal._bytes
    assert x.x.host.checkpoint["value"] == (x.proposal._expected_tip, x.proposal._expected_count)
    with pytest.raises(x.x.host.module.LauncherError, match="native-action-commit-consumed"):
        commit.commit()


def v2_worker_completion_transaction(x, recheck):
    """Same actual host/profile/B2; synthetic PID1 and fixed transaction approval.

    This is the immediate retained-result consumer, not full factory, START
    authorization, native broker, scientific evidence or RUN_OPENED closure.
    """
    control = x.case.control
    bootstrap, invocation = control.bootstrap, control.invocation
    codec = bootstrap.apparatus["ex10_session_v2"]
    pending = bootstrap.load(invocation)
    payload = dict(pending.to_dict()["history"][-1]["payload"])
    payload.update(
        event="complete-" + control.action,
        official_run_id=x.payload["run_id"],
        preflight_report_sha256="d" * 64,
        operational_envelope_sha256="e" * 64,
        in_flight=[],
        state={
            "known_phase": "PREPARED",
            "uncertain": False,
            "blocked": False,
            "next_allowed_actions": [
                "abandon-official-pre-t0",
                "execute-official",
                "inspect",
                "status",
            ],
        },
    )
    proposal = codec.append_history_entry_v2(pending, payload)
    events = []

    def qualified(**facts):
        facts["writer"].check()
        assert facts["action"] == control.action
        assert facts["profile"].to_dict() == control.profile.to_dict()
        assert facts["pending_bytes"] == pending._bytes
        assert facts["proposal_bytes"] == proposal._bytes
        assert facts["result_bytes"] == x.raw
        current = bootstrap.load(invocation)
        assert current._bytes == (proposal._bytes if facts["written"] else pending._bytes)
        if facts["written"]:
            # Retained process/FD/original authority only. The exact current
            # B2 transition and full action closure require separate approval.
            recheck.check_retained()
        else:
            recheck()
        events.append("after" if facts["written"] else "before")

    commit = x.case.host.module.NativeSessionActionCommit(
        invocation=invocation,
        bootstrap=bootstrap,
        pending=pending,
        proposal=proposal,
        result_bytes=x.raw,
        qualify_completion=qualified,
    )
    return commit, pending, proposal, events


@pytest.mark.parametrize("v2_native_host", ["worker-result-fixed-clock"], indirect=True)
def test_v2_worker_result_exact_completed_b2_transition(v2_worker_result_observation):
    from dataclasses import replace

    x = v2_worker_result_observation
    control = x.case.control
    # The legacy unit fixture uses equivalent separately built bootstrap
    # objects. This new transaction consumer binds one exact host graph.
    control.invocation = replace(control.invocation, native_bootstrap=control.bootstrap)
    x.case.observations["phase"] = "exited"
    with x.observer.capture() as observed:
        raw, value, recheck = observed
        assert raw == x.raw and value == x.payload
        commit, pending, proposal, events = v2_worker_completion_transaction(x, recheck)
        with pytest.raises(x.case.host.module.LauncherError, match="completion-receipt-required"):
            commit.completed_observation()
        completed = commit.commit()
        assert recheck.bind_completed(commit) is None
        assert completed._bytes == proposal._bytes
        assert completed._expected_count == pending._expected_count + 1
        assert events == ["before", "after"]
        assert recheck() is None
    assert x.case.control.bootstrap.load(x.case.control.invocation)._bytes == proposal._bytes


def v2_worker_completion_ready(x):
    from dataclasses import replace

    control = x.case.control
    control.invocation = replace(control.invocation, native_bootstrap=control.bootstrap)
    x.case.observations["phase"] = "exited"


@pytest.mark.parametrize("fault", ["provider-after", "checkpoint", "final-fd-expiry"])
def test_v2_action_commit_failed_operation_has_no_completion_receipt(
    v2_session_action_commit, monkeypatch, fault
):
    x = v2_session_action_commit
    if fault == "provider-after":

        def denied(facts):
            if facts["written"]:
                raise RuntimeError("Synthetic post-write completion denied")

        x.controls["hook"] = denied
    elif fault == "checkpoint":
        x.x.host.checkpoint["lose_publication"] = True
    else:
        history = x.x.host.apparatus["ex10_history_authority"]
        snapshots = []
        actual_read, actual_close, actual_unlock = x.x.host.authority.verify, os.close, fcntl.flock
        released, changed = [], []
        info = x.x.host.guard.stat()
        identity = info.st_dev, info.st_ino

        def observed():
            snapshot = actual_read()
            snapshots.append(snapshot)
            return snapshot

        def unlocked(fd, operation):
            info = os.fstat(fd)
            result = actual_unlock(fd, operation)
            if operation == fcntl.LOCK_UN and (info.st_dev, info.st_ino) == identity:
                released.append(True)
            return result

        def closed(fd):
            info = os.fstat(fd)
            result = actual_close(fd)
            if released and not changed and (info.st_dev, info.st_ino) == identity:
                changed.append(True)
                expiry = max(snapshot.expires_at for snapshot in snapshots)
                clock = (
                    datetime.fromisoformat(expiry.replace("Z", "+00:00")) + timedelta(seconds=1)
                ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                monkeypatch.setattr(history, "_now", lambda: clock)
            return result

        monkeypatch.setattr(x.x.host.authority, "verify", observed)
        monkeypatch.setattr(os, "close", closed)
        monkeypatch.setattr(fcntl, "flock", unlocked)
    commit = v2_action_commit(x)
    with pytest.raises(x.x.host.module.LauncherError):
        commit.commit()
    assert commit.used
    if fault == "final-fd-expiry":
        assert released == changed == [True], "Actual final global FD close was not reached"
    assert (x.x.host.root / x.x.host.module.SESSION_FILE).read_bytes() == x.proposal._bytes
    with pytest.raises(x.x.host.module.LauncherError, match="completion-receipt-required"):
        commit.completed_observation()
        pytest.fail("Failed consumed operation exposed a completed observation receipt")


@pytest.mark.parametrize("fault", ["type", "unfinished"])
def test_v2_worker_completion_denies_unfinished_or_unrelated_receipt(
    v2_worker_result_observation, fault
):
    x = v2_worker_result_observation
    v2_worker_completion_ready(x)
    before = _v2_private_tree_bytes(x.case.host.home)
    with pytest.raises(x.case.host.module.LauncherError):
        with x.observer.capture() as (_, _, recheck):
            commit, _, _, _ = v2_worker_completion_transaction(x, recheck)
            recheck.bind_completed(object() if fault == "type" else commit)
            pytest.fail("Absent or unfinished completion receipt was accepted")
    assert _v2_private_tree_bytes(x.case.host.home) == before


def test_v2_worker_completion_denies_equivalent_foreign_invocation(v2_worker_result_observation):
    from dataclasses import replace

    x = v2_worker_result_observation
    v2_worker_completion_ready(x)
    with pytest.raises(x.case.host.module.LauncherError, match="completion-binding"):
        with x.observer.capture() as (_, _, recheck):
            commit, _, proposal, _ = v2_worker_completion_transaction(x, recheck)
            commit.invocation = replace(commit.invocation)
            commit.commit()
            assert commit.completed_observation()[4] == proposal._bytes
            recheck.bind_completed(commit)
            pytest.fail("Equivalent foreign invocation completion was adopted")


@pytest.mark.parametrize("fault", ["checkpoint", "coherent-replacement"])
def test_v2_worker_completion_never_adopts_later_b2(v2_worker_result_observation, fault):
    x = v2_worker_result_observation
    v2_worker_completion_ready(x)
    with pytest.raises(x.case.host.module.LauncherError):
        with x.observer.capture() as (_, _, recheck):
            commit, _, proposal, _ = v2_worker_completion_transaction(x, recheck)
            commit.commit()
            recheck.bind_completed(commit)
            if fault == "checkpoint":
                x.case.host.checkpoint["value"] = ("f" * 64, proposal._expected_count)
            else:
                control = x.case.control
                codec = control.bootstrap.apparatus["ex10_session_v2"]
                payload = dict(
                    proposal.to_dict()["history"][-1]["payload"], event="synthetic-other-completion"
                )
                unrelated = codec.append_history_entry_v2(proposal, payload)
                codec.update_official_session_v2(
                    control.invocation.session_root / x.case.host.module.SESSION_FILE,
                    proposal,
                    unrelated,
                    context=control.bootstrap.context(),
                    profile=control.profile,
                    caller_serialized=True,
                )
                control.bootstrap.publish(
                    (proposal._expected_tip, proposal._expected_count), unrelated
                )
            recheck()
            pytest.fail("Later B2 or checkpoint was adopted by retained observation")


@pytest.mark.parametrize("fault", ["same-bytes-inode", "result-copy"])
def test_v2_worker_completion_retains_original_result(v2_worker_result_observation, fault):
    x = v2_worker_result_observation
    v2_worker_completion_ready(x)
    with pytest.raises(x.case.host.module.LauncherError):
        with x.observer.capture() as (_, value, recheck):
            commit, _, _, _ = v2_worker_completion_transaction(x, recheck)
            commit.commit()
            recheck.bind_completed(commit)
            if fault == "same-bytes-inode":
                old = x.path.with_suffix(".retained-original")
                x.path.rename(old)
                x.path.write_bytes(x.raw)
                x.path.chmod(0o600)
                assert x.path.stat().st_ino != old.stat().st_ino
            else:
                value["manifest_sha256"] = "f" * 64
            recheck()
            pytest.fail("Completed observation lost its original private result binding")


def test_v2_worker_completion_original_authority_after_actual_bind_io(
    v2_worker_result_observation, monkeypatch
):
    x = v2_worker_result_observation
    v2_worker_completion_ready(x)
    history = x.case.control.bootstrap.apparatus["ex10_history_authority"]
    snapshots = []
    actual_read = x.case.host.authority.verify

    def observed():
        snapshot = actual_read()
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(x.case.host.authority, "verify", observed)
    with pytest.raises(Exception, match="EXPIRED_OBSERVATION"):
        with x.observer.capture() as (_, _, recheck):
            commit, _, _, _ = v2_worker_completion_transaction(x, recheck)
            commit.commit()
            actual = x.case.control.worker_exit
            calls = []

            def expired():
                code = actual()
                calls.append(True)
                expiry = max(snapshot.expires_at for snapshot in snapshots)
                clock = (
                    datetime.fromisoformat(expiry.replace("Z", "+00:00")) + timedelta(seconds=1)
                ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                monkeypatch.setattr(history, "_now", lambda: clock)
                return code

            monkeypatch.setattr(x.case.control, "worker_exit", expired)
            recheck.bind_completed(commit)
            pytest.fail("Actual bind IO refreshed original worker authority")
    assert calls == [True]


def test_v2_worker_completion_bind_is_once(v2_worker_result_observation):
    x = v2_worker_result_observation
    v2_worker_completion_ready(x)
    with pytest.raises(x.case.host.module.LauncherError, match="completion-bound"):
        with x.observer.capture() as (_, _, recheck):
            commit, _, _, _ = v2_worker_completion_transaction(x, recheck)
            commit.commit()
            recheck.bind_completed(commit)
            recheck.bind_completed(commit)
            pytest.fail("Completed observation accepted a second binding")


def test_v2_worker_observation_scope_owner_active_and_nonreentrant(
    v2_worker_result_observation, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor

    x = v2_worker_result_observation
    v2_worker_completion_ready(x)
    with x.observer.capture() as (_, _, recheck):
        before = list(x.case.events)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(x.case.host.module.LauncherError, match="observation-scope"):
                pool.submit(recheck.check_retained).result()
        assert x.case.events == before
        actual = x.case.control.worker_exit
        denied = []

        def nested():
            with pytest.raises(x.case.host.module.LauncherBusy):
                recheck.check_retained()
            denied.append(True)
            return actual()

        with monkeypatch.context() as patch:
            patch.setattr(x.case.control, "worker_exit", nested)
            assert recheck.check_retained() is None
        assert denied == [True]
    before = _v2_private_tree_bytes(x.case.host.home)
    for closure in (recheck, recheck.check_retained, lambda: recheck.bind_completed(object())):
        with pytest.raises(x.case.host.module.LauncherError, match="observation-scope"):
            closure()
            pytest.fail("Closed observation closure accepted use outside its capture")
    assert _v2_private_tree_bytes(x.case.host.home) == before


def test_v2_completion_receipt_source_lease_after_actual_checkpoint_provider(
    v2_session_action_commit,
):
    x = v2_session_action_commit
    commit = v2_action_commit(x)
    commit.commit()
    observed = []

    def changed():
        observed.append(True)
        if len(observed) == 3:
            # Actual third read is the independent CP callback after load's
            # final source recheck; retain exact bytes, inode and CP/B2.
            x.x.host.profile_path.chmod(0o640)

    x.x.host.checkpoint["read_hook"] = changed
    with pytest.raises(x.x.host.module.LauncherError):
        commit.completed_observation()
        pytest.fail(
            "Completion receipt returned after actual checkpoint IO "
            "invalidated original source lease"
        )
    assert observed == [
        True,
        True,
        True,
    ], "Actual final independent checkpoint callback was not reached"
    assert stat.S_IMODE(x.x.host.profile_path.stat().st_mode) == 0o640
    assert (x.x.host.root / x.x.host.module.SESSION_FILE).read_bytes() == x.proposal._bytes
    assert x.x.host.checkpoint["value"] == (x.proposal._expected_tip, x.proposal._expected_count)


def v2_start_with_worker_identity(x, *, start_nonce=None):
    """Compose a signer before the worker-generated proposal has been received."""
    mod, a = x.fixture.module, x.fixture.rig.a
    prior = x.signer
    signer = official.NativeBridgeProofSigner(
        apparatus={"ex10_operational_context": mod.op},
        signer=prior.key,
        bridge_pin=prior.pin,
        action="start",
        exchange_id=prior.exchange,
        expected_run_id=None,
        admitted=prior.admitted,
        publish_role=prior.publish_role,
        start_nonce=start_nonce or x.rig.context.proposal["execution_nonce"],
    )
    old = prior.sources
    sources = mod.op.NativeHostSources(
        session_qualifier=old.sessions,
        registry=old.registry,
        policy=old.policy,
        authority_reader=old.reader,
        historical_contexts=old.histories,
        artifact_store=old.artifacts,
        certificate_store=old.certificates,
        publisher=old.publisher,
        executor_fingerprint=old.fingerprint,
        signer=signer,
        bridge_pin=old.pin,
    )
    sources.verify_registry(canonical_binding=a.journal.binding, journal=a.journal)
    a.journal.sources = sources
    x.fixture.source.source = sources
    signer.bind_once(sources=sources, journal=a.journal)
    x.rig.broker = mod.op.NativeBridgeBroker(
        journal=a.journal,
        session_qualifier=old.sessions,
        signer=signer,
        host_sources=sources,
        holder_admitter=lambda **kw: a.holder.public_key(),
    )
    x.signer = signer
    return signer, sources


def test_v2_start_identity_bound_from_actual_qualified_worker_proposal(v2_bridge_proof_signer):
    x = v2_bridge_proof_signer
    signer, source = v2_start_with_worker_identity(x)
    assert signer.run_id is None and source.approvals == {}
    result, errors, responses = x.helper.v2_run_native_host(
        x.rig, lambda: x.helper.v2_start_call(x.rig)
    )
    assert errors == [] and len(responses) == 1
    assert result["run_id"] == signer.run_id == x.rig.context.proposal["run_id"]
    assert signer.index == 6 and not signer.failed
    assert x.rig.host.position == x.rig.context.channel.position == 9
    assert x.fixture.rig.a.transport.calls.count("RESERVE") == 1
    assert source.publisher.transport.calls.count("B3_CAS") == 1
    assert x.fixture.rig.a.transport.calls.count("COMMIT_OPEN") == 1
    assert x.copied == [
        "base-material",
        "base-head",
        "commit-material",
        "commit-head",
        "commit-coordination",
        "commit-receipt",
    ]


@pytest.mark.parametrize("failure", ["installed-nonce", "missing-approval", "foreign-context"])
def test_v2_start_identity_never_binds_unqualified_proposal(v2_bridge_proof_signer, failure):
    from dataclasses import replace

    x = v2_bridge_proof_signer
    nonce = "w09-foreign-installed-nonce" if failure == "installed-nonce" else None
    signer, source = v2_start_with_worker_identity(x, start_nonce=nonce)
    approve = source.approve_reserve

    def altered(**facts):
        approval = approve(**facts)
        digest = hashlib.sha256(approval.canonical_body).hexdigest()
        if failure == "missing-approval":
            source.approvals.pop(digest)
        elif failure == "foreign-context":
            approval = replace(
                approval,
                context=replace(
                    approval.context, expected_run_id="s11-00000000-0000-5000-8000-000000000001"
                ),
            )
            source.approvals[digest] = approval
        return approval

    source.approve_reserve = altered
    with pytest.raises(
        (x.helper.executor.ExecutorBridgeError, x.helper.executor.wire.BridgeProtocolError)
    ):
        x.helper.v2_run_native_host(x.rig, lambda: x.helper.v2_start_call(x.rig))
    errors, responses = x.rig.host_errors, []
    assert len(errors) == 1 and type(errors[0]) is x.fixture.module.op.OperationalError
    assert str(errors[0]) == "HOST_CONTEXT_INVALID"
    assert errors and responses == []
    assert signer.failed and signer.index == 1 and signer.run_id is None
    assert len(x.signed) == 1 and x.copied == ["base-material", "base-head"]
    assert "RESERVE" not in x.fixture.rig.a.transport.calls
    with pytest.raises(official.LauncherError, match="native-proof-signer-consumed"):
        signer.sign(x.signed[0])


def test_v2_start_identity_retained_after_actual_signing_provider(v2_bridge_proof_signer):
    x = v2_bridge_proof_signer
    signer, source = v2_start_with_worker_identity(x)
    actual = signer.key.sign
    changed = []

    def altered(data):
        signature = actual(data)
        if signer.index == 1:
            signer.run_id = "00000000-0000-4000-8000-000000000001"
            changed.append(True)
        return signature

    signer.key.sign = altered
    with pytest.raises(
        (x.helper.executor.ExecutorBridgeError, x.helper.executor.wire.BridgeProtocolError)
    ):
        x.helper.v2_run_native_host(x.rig, lambda: x.helper.v2_start_call(x.rig))
    errors, responses = x.rig.host_errors, []
    assert len(errors) == 1 and type(errors[0]) is x.fixture.module.op.OperationalError
    assert str(errors[0]) == "HOST_CONTEXT_INVALID"
    assert changed == [True] and errors and responses == []
    assert signer.failed and signer.index == 1
    assert len(x.signed) == 2 and "RESERVE" not in x.fixture.rig.a.transport.calls
    assert source.publisher.transport.calls.count("B3_CAS") == 0


@pytest.mark.parametrize("failure", ["body-copy", "identity-copy", "record-copy"])
def test_v2_start_identity_cannot_rebind_after_first_capability(v2_bridge_proof_signer, failure):
    from dataclasses import replace

    x = v2_bridge_proof_signer
    signer, source = v2_start_with_worker_identity(x)
    actual = signer.key.sign
    changed = []

    def altered(data):
        signature = actual(data)
        if signer.index == 1:
            approval = signer.approved
            original = approval["reserve-start"]
            if failure == "body-copy":
                replacement = replace(original)
            elif failure == "identity-copy":
                replacement = replace(
                    original,
                    context=replace(
                        original.context, expected_run_id="00000000-0000-4000-8000-000000000001"
                    ),
                )
            else:
                record = x.fixture.module.op.sql.parse_canonical(original.reservation_record)
                record["request_id"] = "00000000-0000-4000-8000-000000000001"
                replacement = replace(
                    original, reservation_record=x.fixture.module.op.sql.canonical_bytes(record)
                )
            digest = hashlib.sha256(original.canonical_body).hexdigest()
            source.approvals[digest] = replacement
            changed.append(True)
        return signature

    signer.key.sign = altered
    with pytest.raises(
        (x.helper.executor.ExecutorBridgeError, x.helper.executor.wire.BridgeProtocolError)
    ):
        x.helper.v2_run_native_host(x.rig, lambda: x.helper.v2_start_call(x.rig))
    errors, responses = x.rig.host_errors, []
    assert len(errors) == 1 and type(errors[0]) is x.fixture.module.op.OperationalError
    assert str(errors[0]) == "HOST_CONTEXT_INVALID"
    assert changed == [True] and errors and responses == []
    assert signer.failed and signer.index == 1
    assert len(x.signed) == 2 and "RESERVE" not in x.fixture.rig.a.transport.calls


@pytest.mark.parametrize("action,nonce", [("execute", "w09-installed-nonce"), ("start", None)])
def test_v2_unknown_run_identity_is_only_start_with_installed_nonce(
    v2_bridge_proof_signer, action, nonce
):
    x = v2_bridge_proof_signer
    prior = x.signer
    with pytest.raises(official.LauncherError, match="native-proof-signer-context"):
        official.NativeBridgeProofSigner(
            apparatus=prior.apparatus,
            signer=prior.key,
            bridge_pin=prior.pin,
            action=action,
            exchange_id=prior.exchange,
            expected_run_id=None,
            admitted=prior.admitted,
            publish_role=prior.publish_role,
            start_nonce=nonce,
        )
    assert x.signed == [] and x.copied == []


@pytest.fixture
def v2_worker_attachment(v2_oneoff_control, monkeypatch):
    """Actual owned POSIX client pipes; Docker/process approval stays synthetic.

    Identity below tests the mechanical handoff only. Full B5/preimage/holder
    qualification belongs to the fixed factory and is not attested by this fixture.
    """
    import subprocess
    import time
    from dataclasses import replace

    case = v2_oneoff_control
    control = case.control
    control.invocation = replace(control.invocation, native_bootstrap=control.bootstrap)
    control.create()
    control.start()
    mod = case.host.module
    op = control.bootstrap.apparatus["ex10_operational_context"]
    # Mechanical cold Git/FD IO is isolated from wall duration using a fixed
    # synthetic UTC context. Both witness observation and admission clock use
    # it; actual signatures, 30s expiry and original-snapshot guards remain.
    # This fixture makes no live-clock or production performance claim.
    clock = {"now": datetime.now(UTC)}
    authority = case.host.authority
    original_observation = authority.observation

    def utc():
        return clock["now"].strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def signed_observation(state=None):
        observation = original_observation(state)
        head = {k: v for k, v in observation["head"].items() if k != "signature"}
        head["issued_at"] = utc()
        head["expires_at"] = (clock["now"] + timedelta(seconds=30)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        observation["head"] = op.history.witness._sign_current_head(head, authority.key)
        observation["now"] = utc()
        return observation

    monkeypatch.setattr(authority, "observation", signed_observation)
    monkeypatch.setattr(op.history, "_now", utc)
    ready, calls, children = {"deadline": None, "reads": 0}, [], []

    def read_ready():
        ready["reads"] += 1
        return ready["deadline"]

    def launch(argv, **options):
        assert argv == (
            "/usr/bin/docker",
            "exec",
            "--interactive",
            control.container_id,
            "/usr/local/bin/python",
            "-B",
            "/app/manage.py",
            "s11_ex10",
            "bridge",
        )
        assert options == {
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
        calls.append((argv, options))
        child = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-c",
                ready.get("peer_code", "import sys; sys.stdin.buffer.read()"),
            ],
            **options,
        )
        children.append(child)
        return child

    attachment = mod.NativeWorkerAttachment(
        control=control, read_ready_deadline=read_ready, process_factory=launch
    )
    identity = op.ExecutorProcessIdentity(control.container_id, "a" * 64, "b" * 64)
    observed = []

    def observer():
        observed.append(True)
        return identity

    x = SimpleNamespace(
        case=case,
        attachment=attachment,
        ready=ready,
        calls=calls,
        children=children,
        clock=clock,
        identity=identity,
        observer=observer,
        observed=observed,
        time=time,
        op=op,
    )
    try:
        yield x
    finally:
        attachment.abort_client()
        assert all(p.poll() is not None for p in children)


def test_v2_worker_attachment_one_real_client_late_existing_adapter_and_eof(v2_worker_attachment):
    x = v2_worker_attachment
    before = _v2_private_tree_bytes(x.case.host.home)
    assert x.attachment.create() is None
    assert x.ready["reads"] == 0 and x.observed == []
    child = x.children[0]
    originals = tuple(s.fileno() for s in (child.stdin, child.stdout, child.stderr))
    x.ready["deadline"] = x.time.monotonic() + 120
    adapter, channel = x.attachment.admit(identity=x.identity, observer=x.observer)
    try:
        assert type(adapter) is x.op.FixedDockerAdapter and type(channel) is x.op.PipeFrameChannel
        assert adapter.process is child and len(x.calls) == 1 and x.ready["reads"] == 1
        assert child.stdin.closed and child.stdout.closed and child.stderr.closed
        assert channel.exchange == "START" and channel.exchange_id == x.case.control.exchange_id
        assert channel.position == 0 and not channel.dispatched
        assert channel.deadline <= x.ready["deadline"]
        assert len(set(channel.identities.values())) == 3
        assert all(fd not in originals and not os.get_inheritable(fd) for fd in channel.identities)
        assert x.case.control.worker_exit() is None, "local client cannot attest PID1 exit"
    finally:
        channel.close()
    adapter.finish_client()
    assert child.returncode == 0 and x.case.control.worker_exit() is None
    assert _v2_private_tree_bytes(x.case.host.home) == before
    pending = x.case.control.bootstrap.load(x.case.control.invocation).to_dict()
    assert len(pending["in_flight"]) == 1 and pending["state"]["uncertain"] is True


def test_v2_worker_attachment_cannot_respawn(v2_worker_attachment):
    x = v2_worker_attachment
    x.attachment.create()
    with pytest.raises(x.case.host.module.LauncherError, match="native-attachment-second-create"):
        x.attachment.create()
    assert len(x.calls) == 1 and x.children[0].poll() is not None
    assert not any(kind in {"rm", "kill", "stop"} for kind, _ in x.case.events)


@pytest.mark.parametrize("fault", ["absent-ready", "foreign-process", "changed-container"])
def test_v2_worker_attachment_never_admits_unqualified_ready(v2_worker_attachment, fault):
    x = v2_worker_attachment
    x.attachment.create()
    x.ready["deadline"] = x.time.monotonic() + 120
    identity = x.identity
    if fault == "absent-ready":
        x.ready["deadline"] = None
    elif fault == "foreign-process":
        identity = x.op.ExecutorProcessIdentity("f" * 64, "a" * 64, "b" * 64)
    else:
        x.case.control.container_id = "f" * 64
    with pytest.raises(x.case.host.module.LauncherError):
        x.attachment.admit(identity=identity, observer=x.observer)
        pytest.fail("Unqualified preparation acquired an actual frame channel")
    assert x.attachment.adapter is None and len(x.calls) == 1
    assert x.children[0].poll() is not None and not x.attachment.transferred
    with pytest.raises(
        x.case.host.module.LauncherError, match="native-attachment-second-admission"
    ):
        x.attachment.admit(identity=x.identity, observer=x.observer)
    assert len(x.calls) == 1


def test_v2_worker_attachment_retains_actual_pipe_mapping(v2_worker_attachment):
    x = v2_worker_attachment
    x.attachment.create()
    x.ready["deadline"] = x.time.monotonic() + 120
    fd = os.open("/dev/null", os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.dup2(fd, x.children[0].stdout.fileno(), inheritable=False)
    finally:
        os.close(fd)
    with pytest.raises(x.case.host.module.LauncherError, match="native-attachment-private-pipe"):
        x.attachment.admit(identity=x.identity, observer=x.observer)
    assert x.attachment.adapter is None and len(x.calls) == 1
    assert x.children[0].poll() is not None


def test_v2_worker_attachment_original_preparation_deadline(v2_worker_attachment, monkeypatch):
    x = v2_worker_attachment
    x.attachment.create()
    x.ready["deadline"] = x.attachment.deadline + 120
    monkeypatch.setattr(x.time, "monotonic", lambda: x.attachment.deadline + 1)
    with pytest.raises(
        x.case.host.module.LauncherError, match="native-attachment-preparation-deadline"
    ):
        x.attachment.admit(identity=x.identity, observer=x.observer)
    assert x.ready["reads"] == 0 and len(x.calls) == 1
    assert x.children[0].poll() is not None


def test_v2_worker_attachment_original_ready_deadline_after_observer_io(
    v2_worker_attachment, monkeypatch
):
    x = v2_worker_attachment
    x.attachment.create()
    before = _v2_private_tree_bytes(x.case.host.home)
    x.ready["deadline"] = x.time.monotonic() + 120
    original = x.ready["deadline"]

    def late():
        x.ready["deadline"] = original + 120
        monkeypatch.setattr(x.time, "monotonic", lambda: original + 1)
        return x.identity

    with pytest.raises(x.op.OperationalError, match="PROCESS_IDENTITY_CHANGED"):
        x.attachment.admit(identity=x.identity, observer=late)
    assert x.ready["reads"] == 1 and len(x.calls) == 1
    assert x.children[0].poll() is not None and not x.attachment.transferred
    assert _v2_private_tree_bytes(x.case.host.home) == before


def test_v2_worker_attachment_one_handoff_and_fixed_arguments(v2_worker_attachment):
    x = v2_worker_attachment
    x.attachment.create()
    x.ready["deadline"] = x.time.monotonic() + 120
    adapter, channel = x.attachment.admit(identity=x.identity, observer=x.observer)
    try:
        with pytest.raises(x.case.host.module.LauncherError, match="native-attachment-handoff"):
            adapter.factory(adapter.argv, **x.attachment._process_options())
        with pytest.raises(x.op.OperationalError, match="SECOND_OPERATION_DENIED"):
            adapter.open(exchange="START", exchange_id=x.case.control.exchange_id)
        assert len(x.calls) == 1 and adapter.process is x.children[0]
    finally:
        channel.close()
    adapter.finish_client()


def test_v2_worker_attachment_pipe_lease_after_actual_deployment_observer(v2_worker_attachment):
    x = v2_worker_attachment
    x.attachment.create()
    x.ready["deadline"] = x.time.monotonic() + 120
    count, replacement = [], []

    def replace_pipe():
        count.append(True)
        if len(count) == 3:
            read_fd, write_fd = os.pipe()
            os.dup2(read_fd, x.children[0].stdout.fileno(), inheritable=False)
            os.close(read_fd)
            replacement.append(write_fd)
        return x.identity

    channel = None
    try:
        try:
            _, channel = x.attachment.admit(identity=x.identity, observer=replace_pipe)
        except (x.case.host.module.LauncherError, x.op.OperationalError):
            pass
        assert len(count) >= 3 and replacement, "observer did not execute actual FD replacement"
        assert channel is None, "Original client pipe inode was replaced during provider IO"
        assert len(x.calls) == 1
    finally:
        if channel is not None:
            channel.close()
        for fd in replacement:
            os.close(fd)


def test_v2_worker_attachment_second_instance_cannot_spawn(v2_worker_attachment):
    x = v2_worker_attachment
    x.attachment.create()
    try:
        second = x.case.host.module.NativeWorkerAttachment(
            control=x.case.control,
            read_ready_deadline=lambda: None,
            process_factory=x.attachment.factory,
        )
        second.create()
    except x.case.host.module.LauncherError:
        second = None
    try:
        assert len(x.calls) == 1, "Same owned intent created two scientific worker clients"
    finally:
        if second is not None:
            second.abort_client()


def test_v2_worker_attachment_checkpoint_after_actual_inspect_before_spawn(v2_worker_attachment):
    x = v2_worker_attachment
    actual = x.case.control._inspect
    changed = []

    def inspect(**kwargs):
        result = actual(**kwargs)
        if not changed:
            tip, count = x.case.host.checkpoint["value"]
            assert tip != "f" * 64
            x.case.host.checkpoint["value"] = ("f" * 64, count)
            changed.append(True)
        return result

    x.case.control._inspect = inspect
    codec = x.case.control.bootstrap.apparatus["ex10_session_v2"]
    with pytest.raises((x.case.host.module.LauncherError, codec.OfficialSessionError)):
        x.attachment.create()
    assert changed == [True]
    assert len(x.calls) == 0, "Changed independent checkpoint admitted a Docker worker"


def test_v2_worker_attachment_original_signed_authority_after_real_client_creation(
    v2_worker_attachment,
):
    x = v2_worker_attachment
    actual = x.attachment.factory

    def expire(argv, **options):
        process = actual(argv, **options)
        x.clock["now"] += timedelta(seconds=31)
        return process

    x.attachment.factory = expire
    with pytest.raises(x.op.sql.AuthorityError, match="EXPIRED_OBSERVATION"):
        x.attachment.create()
    assert len(x.calls) == 1 and x.children[0].poll() is not None
    assert x.ready["reads"] == 0 and not x.attachment.transferred


def test_v2_worker_attachment_retains_transferred_pipe_after_actual_observer(v2_worker_attachment):
    x = v2_worker_attachment
    x.attachment.create()
    x.ready["deadline"] = x.time.monotonic() + 120
    held, replaced = {}, []

    def observer():
        if "channel" in held and not replaced:
            read_fd, write_fd = os.pipe()
            os.dup2(read_fd, held["channel"].read_fd, inheritable=False)
            os.close(read_fd)
            replaced.append(write_fd)
        return x.identity

    _, channel = x.attachment.admit(identity=x.identity, observer=observer)
    held["channel"] = channel
    refused = False
    try:
        try:
            channel._check()
        except x.op.OperationalError:
            refused = True
        assert replaced, "Actual retained FD mapping was not replaced by observer"
        assert refused, "Transferred pipe replacement was accepted after actual observer IO"
        assert len(x.calls) == 1
    finally:
        channel.close()
        for fd in replaced:
            os.close(fd)


@pytest.mark.parametrize("early_stderr", [False, True])
def test_v2_worker_attachment_actual_complete_frames_and_dual_eof(
    v2_worker_attachment, monkeypatch, early_stderr
):
    x = v2_worker_attachment
    spec = importlib.util.spec_from_file_location(
        "w09_attachment_frame_builder", Path(__file__).with_name("test_ex10_bridge_protocol.py")
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    frames = []
    for position in range(1, 10):
        schema = x.op.wire.schema_definitions()["handshake_frame"]["oneOf"][position - 1]
        value = builder.fill(schema)
        value["exchange_id"] = x.case.control.exchange_id
        if "exchange_id" in value["body"]:
            value["body"]["exchange_id"] = x.case.control.exchange_id
        frames.append(x.op.wire.parse_handshake_frame_bytes(builder.wire(value)))
    encoded = [x.op.wire.encode_frame(frame).hex() for frame in frames]
    x.ready["peer_code"] = (
        "import os,sys\n"
        + ("os.close(2)\n" if early_stderr else "")
        + "frames="
        + repr(encoded)
        + "\n"
        + "for index,data in enumerate(frames):\n"
        + " raw=bytes.fromhex(data)\n"
        + " if index%2==0:\n"
        + "  assert sys.stdin.buffer.read(len(raw))==raw\n"
        + " else:\n"
        + "  sys.stdout.buffer.write(raw);sys.stdout.buffer.flush()\n"
        + "assert sys.stdin.buffer.read(1)==b''\n"
    )
    x.attachment.create()
    x.ready["deadline"] = x.time.monotonic() + 120
    adapter, channel = x.attachment.admit(identity=x.identity, observer=x.observer)
    # This component proves real framing/FD/EOF behavior using a fixed synthetic
    # monotonic instant. Deadline expiry has separate actual-boundary cases;
    # this is neither a live-clock performance nor scientific permission claim.
    instant = x.time.monotonic()
    monkeypatch.setattr(x.time, "monotonic", lambda: instant)
    try:
        for position, frame in enumerate(frames, 1):
            if position % 2:
                channel.send(frame.canonical)
            else:
                assert channel.receive().canonical == frame.canonical
        assert channel.position == 9 and not channel.dispatched
        channel.finish()
        assert channel.closed and channel.identities == {}
        assert len(channel.retired_identities) == 3
        adapter.finish_client()
        assert x.children[0].returncode == 0 and len(x.calls) == 1
        assert x.case.control.worker_exit() is None
    finally:
        channel.close()


def test_v2_worker_attachment_last_checkpoint_provider_before_spawn(v2_worker_attachment):
    x = v2_worker_attachment
    reads, changed, approved = [], [], []
    original_approve = x.case.control.approve

    def approve(**facts):
        result = original_approve(**facts)
        approved.append(True)
        return result

    x.case.control.approve = approve
    path = x.case.host.root / x.case.host.module.SESSION_FILE

    def invalidate():
        reads.append(True)
        if approved and not changed:
            path.chmod(0o640)
            changed.append(True)

    x.case.host.checkpoint["read_hook"] = invalidate
    codec = x.case.control.bootstrap.apparatus["ex10_session_v2"]
    try:
        with pytest.raises((x.case.host.module.LauncherError, codec.OfficialSessionError)):
            x.attachment.create()
        assert changed == [True], "Last CP provider did not invalidate the actual B2 file"
        assert len(x.calls) == 0, "Last CP provider invalidated B2 before a client was created"
    finally:
        path.chmod(0o600)
        x.case.host.checkpoint.pop("read_hook", None)
        x.case.control.approve = original_approve


def test_v2_worker_attachment_creation_approval_after_inspect_before_spawn(v2_worker_attachment):
    x = v2_worker_attachment
    actual = x.case.control._inspect
    changed = []

    def withdraw(**kwargs):
        result = actual(**kwargs)
        x.case.observations["allow"] = False
        changed.append(True)
        return result

    x.case.control._inspect = withdraw
    with pytest.raises(x.case.host.module.LauncherError, match="native-oneoff-approval-required"):
        x.attachment.create()
    assert changed == [True]
    assert len(x.calls) == 0, "Withdrawn independent approval admitted a worker client"


def test_v2_worker_attachment_observer_cannot_forge_diagnostic_eof(v2_worker_attachment):
    x = v2_worker_attachment
    x.attachment.create()
    x.ready["deadline"] = x.time.monotonic() + 120
    held, changed = {}, []

    def observer():
        if "channel" in held and not changed:
            channel = held["channel"]
            fd = channel.error_fd
            channel.identities.pop(fd)
            os.close(fd)
            channel.error_fd = None
            changed.append(True)
        return x.identity

    _, channel = x.attachment.admit(identity=x.identity, observer=observer)
    held["channel"] = channel
    refused = False
    try:
        try:
            channel._check()
        except x.op.OperationalError:
            refused = True
        assert changed == [True], "Observer did not mutate the actual diagnostic FD"
        assert refused, "Observer supplied an invented diagnostic EOF during guard IO"
    finally:
        channel.close()


@pytest.fixture
def native_policy_preparation_case():
    """Actual disposable native policy resources; selection itself grants nothing."""
    from apps.experiments_app.tests import test_ex10_executor as helper

    with helper.v2_native_host_material() as fixture:
        x = fixture.rig
        op = fixture.module.op
        plan = op.NativePolicyResourcePlan.from_journal(policy=x.host_policy, journal=x.a.journal)
        policy_binding = x.host_policy.registry.bindings["policy"]
        reference = op.sql.parse_canonical(policy_binding.reference)
        selected = x.store_file(
            "factory-operator-authorization",
            op.sql.canonical_bytes({"fixture": "independently-installed-operator-approval"}),
            "s11-ex10-synthetic-approval-v1",
        )
        authorization = op.HostArtifactBinding("authorization", selected.reference, selected.store)
        calls = {"resolver": 0, "current": 0, "approval": 0}
        original_approval = x.policy_options["approval_verifier"]

        def approve(**kw):
            original_approval(**kw)
            assert kw["canonical_authorization"] == selected.store.read(
                reference_authorization["path"]
            )
            assert kw["authorization_reference"] == selected.reference
            assert kw["reference"] != selected.reference
            calls["approval"] += 1

        reference_authorization = op.sql.parse_canonical(selected.reference)

        def current():
            calls["current"] += 1
            x.n.qualifier.qualify()
            plan.check()

        def resolve(**kw):
            calls["resolver"] += 1
            assert kw["canonical_facts"] == x.host_policy.registry.read("policy")
            assert type(kw["native"]) is op.QualifiedHostSession
            return policy_binding

        registry = op.HostArtifactRegistry(
            tuple(v for k, v in x.host_policy.registry.bindings.items() if k != "policy")
        )
        options = dict(
            apparatus={"ex10_operational_context": op},
            session_qualifier=x.n.qualifier,
            registry=registry,
            authorization_binding=authorization,
            resource_plan=plan,
            policy_store=policy_binding.store,
            policy_name=Path(reference["path"]).name,
            policy_schema_version=reference["schema_version"],
            approved_root_binding_sha256=x.host_policy.root_binding,
            approval_verifier=approve,
            envelope_verifier=x.policy_options["envelope_verifier"],
            required_preflight_checks=x.policy_options["required_preflight_checks"],
            resolve_policy_binding=resolve,
            check_current=current,
            preparation_deadline=helper.executor.time.monotonic() + 900,
            authority_snapshot=fixture.source.source.reader(),
        )
        yield SimpleNamespace(
            fixture=fixture, x=x, op=op, options=options, binding=policy_binding, calls=calls
        )


def test_v2_native_policy_preparation_selects_exact_existing_policy(native_policy_preparation_case):
    c = native_policy_preparation_case
    policy = official._prepare_native_policy(**c.options)
    assert type(policy) is c.op.NativeHostPolicy
    assert policy.reference == c.x.host_policy.reference
    assert policy.operator_authorization_reference != policy.reference
    assert policy._resource_plan is c.options["resource_plan"]
    assert c.calls == {"resolver": 1, "current": 3, "approval": 0}
    assert policy.qualify(native=c.x.n.qualifier.qualify(), journal=c.x.a.journal) is None
    assert c.calls["approval"] == 1


def test_v2_native_policy_preparation_last_current_io_must_be_rechecked(
    native_policy_preparation_case,
):
    c = native_policy_preparation_case
    current = c.options["check_current"]
    path = Path(c.op.sql.parse_canonical(c.binding.reference)["path"])
    original = path.read_bytes()

    def changed():
        current()
        if c.calls["current"] == 3:
            path.write_bytes(c.op.sql.canonical_bytes({"substituted": "same-inode-last-provider"}))

    try:
        with pytest.raises(official.LauncherError, match="^native-policy-preparation-"):
            official._prepare_native_policy(**dict(c.options, check_current=changed))
        assert c.calls["current"] == 3
        assert c.calls["resolver"] == 1 and c.calls["approval"] == 0
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize("change", ["store", "locator", "schema", "name", "bytes", "exception"])
def test_v2_native_policy_preparation_resolver_cannot_admit_other_binding(
    native_policy_preparation_case, change
):
    c = native_policy_preparation_case
    resolve = c.options["resolve_policy_binding"]
    original = c.op.sql.parse_canonical(c.binding.reference)
    duplicate = c.op.durable.PrivateStore(c.binding.store.root, c.binding.store.identity)
    path = Path(original["path"])
    raw = path.read_bytes()

    def altered(**kw):
        selected = resolve(**kw)
        reference = dict(original)
        store, name = selected.store, selected.name
        if change == "exception":
            raise RuntimeError("LOCAL TEST private resolver token")
        if change == "store":
            store = duplicate
        elif change == "locator":
            reference["path"] = selected.store.root + "/unapproved-policy.json"
        elif change == "schema":
            reference["schema_version"] = "unapproved-schema"
        elif change == "name":
            name = "authorization"
        else:
            path.write_bytes(c.op.sql.canonical_bytes({"substituted": "existing-policy"}))
            reference["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return c.op.HostArtifactBinding(name, c.op.sql.canonical_bytes(reference), store)

    try:
        with pytest.raises(official.LauncherError, match="^native-policy-preparation-") as caught:
            official._prepare_native_policy(**dict(c.options, resolve_policy_binding=altered))
        assert "private resolver token" not in str(caught.value)
        assert c.calls["resolver"] == 1 and c.calls["approval"] == 0
    finally:
        path.write_bytes(raw)
        duplicate.close()


@pytest.mark.parametrize(
    "change", ["authorization", "approval", "native", "store-fd", "floor-pins"]
)
def test_v2_native_policy_preparation_rechecks_original_inputs_after_resolver(
    native_policy_preparation_case, change
):
    c = native_policy_preparation_case
    resolve = c.options["resolve_policy_binding"]
    plan = c.options["resource_plan"]
    policy_store = c.options["policy_store"]
    original_fd = policy_store.fd
    duplicate = os.dup(original_fd)
    original_count = c.options["session_qualifier"].expected_count
    original_pins = plan.floor.pins
    saved = []

    def altered(**kw):
        result = resolve(**kw)
        if change in ("authorization", "approval"):
            binding = (
                c.options["authorization_binding"]
                if change == "authorization"
                else c.options["registry"].bindings["writer_closure"]
            )
            path = Path(c.op.sql.parse_canonical(binding.reference)["path"])
            saved.append((path, path.read_bytes()))
            path.write_bytes(c.op.sql.canonical_bytes({"substituted": change}))
        elif change == "native":
            c.options["session_qualifier"].expected_count = original_count + 1
        elif change == "store-fd":
            policy_store.fd = duplicate
            policy_store.check()
        else:
            before = plan.floor.current()
            plan.floor.pins = (*original_pins, original_pins[0])
            assert plan.floor.current() == before
        return result

    try:
        with pytest.raises(official.LauncherError, match="^native-policy-preparation-"):
            official._prepare_native_policy(**dict(c.options, resolve_policy_binding=altered))
        assert c.calls["resolver"] == 1 and c.calls["approval"] == 0
    finally:
        for path, raw in saved:
            path.write_bytes(raw)
        c.options["session_qualifier"].expected_count = original_count
        policy_store.fd = original_fd
        plan.floor.pins = original_pins
        os.close(duplicate)


@pytest.mark.parametrize("change", ["deadline", "authority-expiry"])
def test_v2_native_policy_preparation_original_budget_and_authority_are_not_refreshed(
    native_policy_preparation_case, monkeypatch, change
):
    import time

    c = native_policy_preparation_case
    current = c.options["check_current"]
    retained = c.options["authority_snapshot"]
    before = c.op.history.current_revision(retained)

    def expire():
        current()
        if c.calls["current"] == 3:
            if change == "deadline":
                monkeypatch.setattr(
                    time, "monotonic", lambda: c.options["preparation_deadline"] + 1
                )
            else:
                monkeypatch.setattr(c.op.history, "_now", lambda: retained.expires_at)

    with pytest.raises(official.LauncherError, match="^native-policy-preparation-"):
        official._prepare_native_policy(**dict(c.options, check_current=expire))
    assert c.calls == {"resolver": 1, "current": 3, "approval": 0}
    assert before["expires_at"] == retained.expires_at


def test_v2_native_policy_preparation_missing_pair_approval_still_denies(
    native_policy_preparation_case,
):
    c = native_policy_preparation_case
    policy = official._prepare_native_policy(
        **dict(c.options, approval_verifier=lambda **kw: False)
    )
    assert c.calls["resolver"] == 1 and c.calls["approval"] == 0
    with pytest.raises(c.op.OperationalError, match="^HOST_APPROVAL_REQUIRED$"):
        policy.qualify(native=c.x.n.qualifier.qualify(), journal=c.x.a.journal)


def test_v2_native_policy_preparation_real_source_and_journal_consumer(
    native_policy_preparation_case,
):
    c = native_policy_preparation_case
    policy = official._prepare_native_policy(**c.options)
    old = c.fixture.source.source
    plan = c.options["resource_plan"]
    bindings = tuple(v for k, v in old.registry.bindings.items() if k != "authorization")
    sources = c.op.NativeHostSources(
        session_qualifier=c.options["session_qualifier"],
        registry=c.op.HostArtifactRegistry(bindings + (c.options["authorization_binding"],)),
        policy=policy,
        authority_reader=old.reader,
        historical_contexts=old.histories,
        artifact_store=old.artifacts,
        certificate_store=old.certificates,
        publisher=old.publisher,
        executor_fingerprint=old.fingerprint,
        signer=old.signer,
        bridge_pin=plan.bridge_pin,
    )
    journal = c.op.coordination.CoordinationJournal(
        store=plan.sidecar_store,
        name=plan.sidecar_name,
        identity=plan.sidecar_identity,
        registry_binding=plan.registry_binding,
        authority_id=plan.authority_id,
        authority_epoch=plan.authority_epoch,
        receipt_store=plan.receipt_store,
        floor=plan.floor,
        transport=plan.transport,
        bridge_pin=plan.bridge_pin,
        host_sources=sources,
        clock=plan.clock,
        clock_health=plan.clock_health,
        lock_store=plan.lock_store,
        lock_name=plan.lock_name,
        lock_identity=plan.lock_identity,
    )
    assert sources.journal is journal and sources.policy is policy
    assert plan.matches(policy=policy, journal=journal) is None
    assert sources.authorization_reference == c.options["authorization_binding"].reference
    assert journal.read() == c.x.a.journal.read()
    assert c.calls["approval"] >= 1
    assert c.x.a.transport.calls == [], "Policy preparation unexpectedly dispatched transport"


@pytest.mark.parametrize("change", ["deadline", "authority-expiry"])
def test_v2_native_policy_preparation_final_resource_io_keeps_original_time_fences(
    native_policy_preparation_case, monkeypatch, change
):
    import inspect
    import time

    c = native_policy_preparation_case
    store = c.options["registry"].bindings["coordinator_registry"].store
    original_check = store.check
    retained = c.options["authority_snapshot"]
    changed = []

    def delayed_check():
        original_check()
        caller = inspect.currentframe().f_back
        try:
            # Delay real resource I/O only in the final direct guard. The
            # earlier guards run inside current(), before the final rereads.
            if (
                not changed
                and c.calls["current"] == 3
                and caller.f_code.co_name == "guard"
                and caller.f_back.f_code.co_name == "_prepare_native_policy"
            ):
                changed.append("after-actual-final-resource-check")
                if change == "deadline":
                    monkeypatch.setattr(
                        time, "monotonic", lambda: c.options["preparation_deadline"] + 1
                    )
                else:
                    monkeypatch.setattr(c.op.history, "_now", lambda: retained.expires_at)
        finally:
            del caller

    monkeypatch.setattr(store, "check", delayed_check)
    with pytest.raises(official.LauncherError, match="^native-policy-preparation-"):
        official._prepare_native_policy(**c.options)
    assert changed == ["after-actual-final-resource-check"]
    assert c.calls == {"resolver": 1, "current": 3, "approval": 0}


def test_v2_native_policy_preparation_requalifies_native_after_last_provider(
    native_policy_preparation_case,
):
    c = native_policy_preparation_case
    current = c.options["check_current"]
    sessions = c.options["session_qualifier"]
    original = sessions.expected_count
    changed = []

    def late_native():
        current()
        if c.calls["current"] == 3:
            sessions.expected_count = original + 1
            changed.append("original-provider-then-changed-native-count")

    try:
        with pytest.raises(official.LauncherError, match="^native-policy-preparation-"):
            official._prepare_native_policy(**dict(c.options, check_current=late_native))
        assert changed == ["original-provider-then-changed-native-count"]
        assert c.calls == {"resolver": 1, "current": 3, "approval": 0}
    finally:
        sessions.expected_count = original


@pytest.fixture
def native_process_commitment_case(monkeypatch):
    """Actual B5 codec/build; opaque synthetic Env pins/payload grant no permission."""
    import uuid

    from apps.experiments_app.tests import test_ex10_executor as helper

    generator = helper.v2_attached_b5_fixture.__wrapped__(monkeypatch)
    rig = next(generator)
    try:
        cfg = {
            **rig.cfg,
            "action": "start",
            "exchange_id": str(uuid.uuid4()),
            "bridge_pin": {"fixture": "independently-installed"},
            "witness_pin": {"fixture": "independently-installed"},
            "b3_pins": [{"fixture": "independently-installed"}],
            "genesis_digest": "a" * 64,
            "genesis_binding": {"fixture": "no-authority-creation"},
            "authority_revision": 0,
            "request_payload": {"fixture": "complete-original-payload"},
            "expected_reservation": None,
            "host_root_binding_sha256": "b" * 64,
            "staging_name": "application-preflight.json",
            "command_options": {"fixture": "original-command-options"},
            "authorization_reference": {"fixture": "independent-operator-approval"},
        }
        initial = official.base.canonical_bytes(cfg)
        options = dict(
            apparatus={
                "ex10_runtime_profile": helper.executor.profile_codec,
                "ex10_deployment_envelope": helper.executor.deployment_envelope,
            },
            bootstrap_bytes=initial,
            profile=rig.profile,
            container_id="a" * 64,
            envelope_bytes=rig.raw,
        )
        yield SimpleNamespace(rig=rig, cfg=cfg, options=options, helper=helper)
    finally:
        generator.close()


def test_v2_native_host_process_commitments_equal_actual_executor(native_process_commitment_case):
    c = native_process_commitment_case
    raw = dict(c.options)
    host = official._native_process_commitments(**raw)
    worker = c.helper.executor._native_process_identity(
        cfg=c.cfg,
        profile=raw["profile"],
        container_id=raw["container_id"],
        initial=raw["bootstrap_bytes"],
        envelope=raw["envelope_bytes"],
    )
    assert host == worker and set(host) == {
        "container_id",
        "identity_digest",
        "deployment_verification_sha256",
    }
    assert raw["bootstrap_bytes"] == official.base.canonical_bytes(c.cfg)


@pytest.mark.parametrize("change", ["authorization", "payload", "sources", "container", "envelope"])
def test_v2_native_host_process_commitments_retain_complete_preimages(
    native_process_commitment_case, change
):
    import copy

    c = native_process_commitment_case
    original = official._native_process_commitments(**c.options)
    cfg, options = copy.deepcopy(c.cfg), dict(c.options)
    if change == "authorization":
        cfg["authorization_reference"]["fixture"] = "different-original-operator-approval"
    elif change == "payload":
        cfg["request_payload"]["fixture"] = "different-original-coordination-payload"
    elif change == "sources":
        cfg["source_hashes"][next(iter(cfg["source_hashes"]))] = "e" * 64
    elif change == "container":
        options["container_id"] = "d" * 64
    else:
        envelope = json.loads(options["envelope_bytes"])
        envelope["application_preflight_sha256"] = "e" * 64
        options["envelope_bytes"] = official.base.canonical_bytes(envelope)
    options["bootstrap_bytes"] = official.base.canonical_bytes(cfg)
    projected = official._native_process_commitments(**options)
    worker = c.helper.executor._native_process_identity(
        cfg=cfg,
        profile=options["profile"],
        container_id=options["container_id"],
        initial=options["bootstrap_bytes"],
        envelope=options["envelope_bytes"],
    )
    assert projected == worker
    assert projected["deployment_verification_sha256"] != original["deployment_verification_sha256"]
    assert (projected["identity_digest"] == original["identity_digest"]) == (change == "envelope")


@pytest.mark.parametrize(
    "change", ["extra", "omit", "noncanonical", "profile", "header", "source", "cid", "b5"]
)
def test_v2_native_host_process_commitments_deny_incoherent_bytes(
    native_process_commitment_case, change
):
    import copy

    c = native_process_commitment_case
    cfg, options = copy.deepcopy(c.cfg), dict(c.options)
    if change == "extra":
        cfg["process_identity"] = {"fixture": "caller-supplied-identity"}
    elif change == "omit":
        cfg.pop("authorization_reference")
    elif change == "profile":
        cfg["profile_sha256"] = "f" * 64
    elif change == "header":
        cfg["header_sha256"] = "f" * 64
    elif change == "source":
        cfg["source_hashes"][next(iter(cfg["source_hashes"]))] = True
    elif change == "cid":
        options["container_id"] = "short-id"
    elif change == "b5":
        options["envelope_bytes"] += b" "
    options["bootstrap_bytes"] = official.base.canonical_bytes(cfg)
    if change == "noncanonical":
        options["bootstrap_bytes"] += b" "
    with pytest.raises(official.LauncherError, match="^native-process-commitment-invalid$"):
        official._native_process_commitments(**options)


@pytest.fixture
def native_bridge_composition_case(native_policy_preparation_case):
    """One real native B1/B2/B3/B4 namespace; only host approvals/keys are synthetic."""
    c = native_policy_preparation_case
    source = c.fixture.source.source
    publisher = source.publisher
    source_bindings = tuple(
        binding for name, binding in source.registry.bindings.items() if name != "authorization"
    ) + (c.options["authorization_binding"],)
    published = []

    def publish(role, raw):
        published.append((role, raw))
        raise AssertionError("constructor must never publish protocol roles")

    def holder(**kw):
        assert kw["native"].process == c.x.n.options["process"]
        return c.x.a.holder.public_key()

    options = dict(
        apparatus=c.options["apparatus"],
        session_bindings=tuple(c.x.n.options["registry"].bindings.values()),
        session_configuration={k: v for k, v in c.x.n.options.items() if k != "registry"},
        policy_configuration={
            "bindings": tuple(c.options["registry"].bindings.values()),
            **{
                key: c.options[key]
                for key in (
                    "authorization_binding",
                    "policy_store",
                    "policy_name",
                    "policy_schema_version",
                    "approved_root_binding_sha256",
                    "approval_verifier",
                    "envelope_verifier",
                    "required_preflight_checks",
                    "resolve_policy_binding",
                )
            },
        },
        publisher_configuration={
            "ledger_store": publisher.ledger,
            "ledger_name": publisher.name,
            "ledger_identity": publisher.identity,
            "candidate_store": publisher.candidates,
            "floor": publisher.floor,
            "transport": publisher.transport,
            "signer_pin": publisher.pin,
            "clock": publisher.clock,
            "clock_health": publisher.health,
        },
        source_configuration={
            "bindings": source_bindings,
            "authority_reader": source.reader,
            "historical_contexts": source.histories,
            "artifact_store": source.artifacts,
            "certificate_store": source.certificates,
            "executor_fingerprint": source.fingerprint,
            "live_owner": source.owner,
            "uncertainty_verifier": source.uncertainty,
            "recovery_sources": source.recovery,
            "lifecycle_sources": source.lifecycle,
        },
        resource_plan=c.options["resource_plan"],
        signer=c.x.a.bridge,
        action="start",
        exchange_id=c.fixture.source.host.exchange_id,
        expected_run_id=c.fixture.source.body["payload"]["run_identity"]["run_id"],
        publish_role=publish,
        holder_admitter=holder,
        check_current=c.options["check_current"],
        preparation_deadline=c.options["preparation_deadline"],
        authority_snapshot=c.options["authority_snapshot"],
    )
    yield SimpleNamespace(c=c, op=c.op, options=options, published=published, source=source)


def test_v2_native_bridge_composition_builds_actual_shared_graph(native_bridge_composition_case):
    x = native_bridge_composition_case
    before = x.c.x.a.journal.read()
    calls = tuple(x.c.x.a.transport.calls)
    graph = official._compose_native_bridge(**x.options)
    assert type(graph) is official.NativeBridgeGraph
    assert type(graph.sessions) is x.op.NativeSessionQualifier
    assert type(graph.policy) is x.op.NativeHostPolicy
    assert type(graph.publisher) is x.op.NativeOpenPublisher
    assert type(graph.sources) is x.op.NativeHostSources
    assert type(graph.journal) is x.op.coordination.CoordinationJournal
    assert type(graph.signer) is official.NativeBridgeProofSigner
    assert type(graph.broker) is x.op.NativeBridgeBroker
    assert graph.sessions is not x.c.x.n.qualifier
    assert graph.policy is not x.c.x.host_policy
    assert graph.publisher is not x.source.publisher
    assert graph.sources is not x.source
    assert graph.journal is not x.c.x.a.journal
    assert (
        graph.policy.sessions is graph.sources.sessions is graph.broker.sessions is graph.sessions
    )
    assert graph.sources.policy is graph.policy
    assert graph.sources.publisher is graph.publisher
    assert graph.sources.journal is graph.broker.journal is graph.signer.journal is graph.journal
    assert graph.journal.sources is graph.broker.sources is graph.signer.sources is graph.sources
    assert graph.sources.signer is graph.broker.signer is graph.signer
    assert graph.sources.authorization_reference == x.c.options["authorization_binding"].reference
    assert graph.policy.operator_authorization_reference != graph.policy.reference
    assert graph.journal.read() == before and tuple(x.c.x.a.transport.calls) == calls
    assert x.published == [] and not graph.broker.used and graph.signer.index == 0
    assert graph.check_retained() is None
    assert all(
        os.fstat(store.fd)
        for store in (
            graph.publisher.ledger,
            graph.publisher.candidates,
            graph.publisher.floor.store,
            graph.sources.artifacts,
            graph.sources.certificates,
        )
    )


@pytest.mark.parametrize(
    "group",
    [
        "session_configuration",
        "policy_configuration",
        "publisher_configuration",
        "source_configuration",
    ],
)
def test_v2_native_bridge_composition_accepts_only_closed_raw_configuration(
    native_bridge_composition_case, group
):
    x = native_bridge_composition_case
    options = dict(x.options)
    options[group] = {**options[group], "preassembled_component": x.source}
    with pytest.raises(official.LauncherError, match="^native-bridge-composition-denied$"):
        official._compose_native_bridge(**options)
    assert x.published == [] and x.c.calls == {"resolver": 0, "current": 0, "approval": 0}


@pytest.mark.parametrize("value", ["qualifier", "registry", "journal", "sources"])
def test_v2_native_bridge_composition_rejects_preassembled_inputs(
    native_bridge_composition_case, value
):
    x = native_bridge_composition_case
    selected = {
        "qualifier": x.c.x.n.qualifier,
        "registry": x.c.x.n.registry,
        "journal": x.c.x.a.journal,
        "sources": x.source,
    }[value]
    with pytest.raises(official.LauncherError, match="^native-bridge-composition-denied$"):
        official._compose_native_bridge(**dict(x.options, session_bindings=selected))
    assert x.published == [] and x.c.calls == {"resolver": 0, "current": 0, "approval": 0}


def test_v2_native_bridge_composition_actual_journal_requires_pair_approval(
    native_bridge_composition_case,
):
    x = native_bridge_composition_case
    options = dict(x.options)
    seen = []

    def reject(**kw):
        seen.append(kw["journal"])
        assert type(kw["journal"]) is x.op.coordination.CoordinationJournal
        return False

    options["policy_configuration"] = {
        **options["policy_configuration"],
        "approval_verifier": reject,
    }
    before = x.c.x.a.journal.read()
    with pytest.raises(official.LauncherError, match="^native-bridge-composition-denied$"):
        official._compose_native_bridge(**options)
    assert len(seen) == 1 and seen[0] is not x.c.x.a.journal
    assert x.c.x.a.journal.read() == before and x.published == []


@pytest.mark.parametrize(
    "change",
    [
        "policy-callback",
        "session-observer",
        "signer-exchange",
        "signer-action",
        "signer-run",
        "signer-pin",
    ],
)
def test_v2_native_bridge_composition_last_approval_cannot_reselect_graph(
    native_bridge_composition_case, change
):
    x = native_bridge_composition_case
    original = x.options["policy_configuration"]["approval_verifier"]
    altered = []

    def late(**kw):
        original(**kw)
        source = kw["journal"].sources
        signer = source.signer
        if signer.journal is not None:
            altered.append(change)
            if change == "policy-callback":
                source.policy.approve = lambda **kw: None
            elif change == "session-observer":
                source.sessions.observe = lambda **kw: x.c.x.n.options["process"]
            elif change == "signer-exchange":
                signer.exchange = "00000000-0000-0000-0000-000000000000"
            elif change == "signer-action":
                signer.action = "drain"
            elif change == "signer-run":
                signer.run_id = OTHER_RUN
            else:
                # The constructor-installed holder callback is not invoked in
                # assembly, so mutate the fixed signer's original bridge pin.
                signer.pin = x.op.wire.PublicKeyPin(
                    signer.pin.key_id, signer.pin.key_epoch, signer.pin.public_key
                )

    options = dict(x.options)
    options["policy_configuration"] = {**options["policy_configuration"], "approval_verifier": late}
    with pytest.raises(official.LauncherError, match="^native-bridge-composition-denied$"):
        official._compose_native_bridge(**options)
    assert altered and set(altered) == {change} and x.published == []


@pytest.mark.parametrize("change", ["deadline", "authority-expiry"])
def test_v2_native_bridge_composition_last_resource_io_keeps_original_fences(
    native_bridge_composition_case, monkeypatch, change
):
    import inspect
    import time

    x = native_bridge_composition_case
    store = x.options["publisher_configuration"]["candidate_store"]
    original = store.check
    altered = []

    def delayed():
        original()
        caller = inspect.currentframe().f_back
        try:
            if (
                not altered
                and caller.f_code.co_name == "guard"
                and caller.f_back.f_code.co_name == "_compose_native_bridge"
                and caller.f_locals["graph"] is not None
            ):
                altered.append("after-real-final-store-IO")
                if change == "deadline":
                    monkeypatch.setattr(
                        time, "monotonic", lambda: x.options["preparation_deadline"] + 1
                    )
                else:
                    retained = x.options["authority_snapshot"]
                    monkeypatch.setattr(x.op.history, "_now", lambda: retained.expires_at)
        finally:
            del caller

    monkeypatch.setattr(store, "check", delayed)
    with pytest.raises(official.LauncherError, match="^native-bridge-composition-denied$"):
        official._compose_native_bridge(**x.options)
    assert altered == ["after-real-final-store-IO"] and x.published == []


@pytest.mark.parametrize("change", ["store-fd", "floor-pins", "floor-genesis", "authorization"])
def test_v2_native_bridge_composition_history_io_cannot_replace_original_resources(
    native_bridge_composition_case, change
):
    x = native_bridge_composition_case
    options = dict(x.options)
    original = options["source_configuration"]["historical_contexts"]
    floor = options["publisher_configuration"]["floor"]
    store = options["publisher_configuration"]["candidate_store"]
    old_fd, old_pins, old_genesis = store.fd, floor.pins, floor.genesis_digest
    authorization = x.c.options["authorization_binding"]
    path = Path(x.op.sql.parse_canonical(authorization.reference)["path"])
    raw = path.read_bytes()
    duplicate = os.dup(old_fd)
    altered = []

    def late(**kw):
        result = original(**kw)
        altered.append(change)
        if change == "store-fd":
            store.fd = duplicate
        elif change == "floor-pins":
            floor.pins = floor.pins + floor.pins
        elif change == "floor-genesis":
            floor.genesis_digest = "f" * 64
        else:
            path.write_bytes(x.op.sql.canonical_bytes({"changed": "same-inode-original-A"}))
        return result

    options["source_configuration"] = {
        **options["source_configuration"],
        "historical_contexts": late,
    }
    try:
        with pytest.raises(official.LauncherError, match="^native-bridge-composition-denied$"):
            official._compose_native_bridge(**options)
        assert altered == [change] and x.published == []
    finally:
        store.fd, floor.pins, floor.genesis_digest = old_fd, old_pins, old_genesis
        path.write_bytes(raw)
        os.close(duplicate)


def test_v2_native_bridge_composition_original_authorization_guard_cannot_be_replaced(
    native_bridge_composition_case,
):
    x = native_bridge_composition_case
    options = dict(x.options)
    original_history = options["source_configuration"]["historical_contexts"]
    original_binding = x.c.options["authorization_binding"]
    duplicate = x.op.durable.PrivateStore(
        original_binding.store.root, original_binding.store.identity
    )
    replacement_binding = x.op.HostArtifactBinding(
        original_binding.name, original_binding.reference, duplicate
    )
    replacement = x.op.HostArtifactRegistry((replacement_binding,))
    altered = []

    def history(**kw):
        result = original_history(**kw)
        policy = kw["journal"].sources.policy
        assert (
            policy._authorization_registry.bindings["authorization"].store is original_binding.store
        )
        assert replacement.read("authorization") == original_binding.store.read(
            x.op.sql.parse_canonical(original_binding.reference)["path"]
        )
        policy._authorization_registry = replacement
        policy._authorization_guard = lambda: replacement
        altered.append("actual-registry-equivalent-reference-distinct-store-handle")
        return result

    options["source_configuration"] = {
        **options["source_configuration"],
        "historical_contexts": history,
    }
    try:
        with pytest.raises(official.LauncherError, match="^native-bridge-composition-denied$"):
            official._compose_native_bridge(**options)
        assert altered == ["actual-registry-equivalent-reference-distinct-store-handle"]
        assert x.published == []
    finally:
        duplicate.close()


def test_v2_native_policy_preparation_missing_policy_cannot_be_created_by_resolver(
    native_policy_preparation_case,
):
    c = native_policy_preparation_case
    path = Path(c.op.sql.parse_canonical(c.binding.reference)["path"])
    original = path.read_bytes()
    created = []

    def create(**kw):
        c.calls["resolver"] += 1
        assert not path.exists()
        path.write_bytes(kw["canonical_facts"])
        path.chmod(0o600)
        created.append("resolver-created-canonical-facts-in-approved-locator")
        reference = c.op.sql.parse_canonical(c.binding.reference)
        reference["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return c.op.HostArtifactBinding(
            "policy", c.op.sql.canonical_bytes(reference), c.binding.store
        )

    path.unlink()
    try:
        with pytest.raises(official.LauncherError, match="^native-policy-preparation-"):
            official._prepare_native_policy(**dict(c.options, resolve_policy_binding=create))
        assert created == [] and c.calls["resolver"] == 0 and not path.exists()
    finally:
        path.write_bytes(original)
        path.chmod(0o600)


@pytest.mark.parametrize("provider", ["resolver", "last-current"])
def test_v2_native_policy_preparation_retains_original_policy_inode(
    native_policy_preparation_case, provider
):
    c = native_policy_preparation_case
    path = Path(c.op.sql.parse_canonical(c.binding.reference)["path"])
    original = path.read_bytes()
    inode = path.stat().st_ino
    replacement = path.with_name("same-policy-raw-new-inode.json")
    changed = []
    current = c.options["check_current"]
    resolve = c.options["resolve_policy_binding"]

    def swap():
        replacement.write_bytes(original)
        replacement.chmod(0o600)
        os.replace(replacement, path)
        assert path.read_bytes() == original and path.stat().st_ino != inode
        changed.append(provider)

    def replace_resolver(**kw):
        result = resolve(**kw)
        swap()
        return result

    def replace_current():
        current()
        if c.calls["current"] == 3:
            swap()

    options = dict(c.options)
    options["resolve_policy_binding" if provider == "resolver" else "check_current"] = (
        replace_resolver if provider == "resolver" else replace_current
    )
    try:
        with pytest.raises(official.LauncherError, match="^native-policy-preparation-"):
            official._prepare_native_policy(**options)
        assert changed == [provider] and c.calls["resolver"] == 1
        assert c.calls["approval"] == 0
    finally:
        path.write_bytes(original)
        path.chmod(0o600)
        if replacement.exists():
            replacement.unlink()


def test_v2_native_bridge_composition_retains_policy_inode_through_last_approval(
    native_bridge_composition_case,
):
    x = native_bridge_composition_case
    original = x.options["policy_configuration"]["approval_verifier"]
    path = Path(x.op.sql.parse_canonical(x.c.binding.reference)["path"])
    raw = path.read_bytes()
    inode = path.stat().st_ino
    temporary = path.with_name("same-policy-post-helper-approval.json")
    changed = []

    def late(**kw):
        original(**kw)
        if kw["journal"].sources.signer.journal is not None and not changed:
            temporary.write_bytes(raw)
            temporary.chmod(0o600)
            os.replace(temporary, path)
            assert path.read_bytes() == raw and path.stat().st_ino != inode
            changed.append("same-bytes-new-policy-inode-after-helper-return")

    options = dict(x.options)
    options["policy_configuration"] = {**options["policy_configuration"], "approval_verifier": late}
    try:
        with pytest.raises(official.LauncherError, match="^native-bridge-composition-denied$"):
            official._compose_native_bridge(**options)
        assert changed == ["same-bytes-new-policy-inode-after-helper-return"]
        assert x.published == []
    finally:
        path.write_bytes(raw)
        path.chmod(0o600)
        if temporary.exists():
            temporary.unlink()


@pytest.mark.parametrize("outcome", ["success", "resolver-failure", "wrong-facts"])
def test_v2_native_policy_preparation_closes_owned_file_borrows_no_descriptors(
    native_policy_preparation_case, monkeypatch, outcome
):
    import inspect

    c = native_policy_preparation_case
    store = c.options["policy_store"]
    original_open = store.open
    owned = []
    path = Path(c.op.sql.parse_canonical(c.binding.reference)["path"])
    raw = path.read_bytes()

    def track(name, **kw):
        fd = original_open(name, **kw)
        caller = inspect.currentframe().f_back
        try:
            if (
                caller.f_code.co_name == "_prepare_native_policy"
                and name == c.options["policy_name"]
            ):
                owned.append(fd)
        finally:
            del caller
        return fd

    def reject(**kw):
        c.calls["resolver"] += 1
        raise RuntimeError("LOCAL TEST resolver rejection")

    monkeypatch.setattr(store, "open", track)
    options = dict(c.options)
    if outcome == "resolver-failure":
        options["resolve_policy_binding"] = reject
    elif outcome == "wrong-facts":
        path.write_bytes(c.op.sql.canonical_bytes({"unapproved": "different-existing-facts"}))
    try:
        if outcome == "success":
            assert type(official._prepare_native_policy(**options)) is c.op.NativeHostPolicy
        else:
            with pytest.raises(official.LauncherError, match="^native-policy-preparation-"):
                official._prepare_native_policy(**options)
        assert len(owned) == 1
        with pytest.raises(OSError) as closed:
            os.fstat(owned[0])
        assert closed.value.errno == 9
        assert stat.S_ISDIR(os.fstat(store.fd).st_mode)
        assert c.calls["resolver"] == (0 if outcome == "wrong-facts" else 1)
        assert c.calls["approval"] == 0
    finally:
        path.write_bytes(raw)
        path.chmod(0o600)


@pytest.mark.parametrize("fence", ["deadline", "authority-expiry"])
def test_v2_native_policy_preparation_original_fences_after_owned_file_close(
    native_policy_preparation_case, monkeypatch, fence
):
    import inspect
    import time

    c = native_policy_preparation_case
    original_close = os.close
    closed = []

    def expire(fd):
        caller = inspect.currentframe().f_back
        try:
            own = (
                caller.f_code.co_name == "_prepare_native_policy"
                and caller.f_locals.get("policy_fd") == fd
                and caller.f_locals.get("returning_policy") is True
            )
        finally:
            del caller
        original_close(fd)
        if own:
            closed.append(fd)
            if fence == "deadline":
                monkeypatch.setattr(
                    time, "monotonic", lambda: c.options["preparation_deadline"] + 1
                )
            else:
                retained = c.options["authority_snapshot"]
                monkeypatch.setattr(c.op.history, "_now", lambda: retained.expires_at)

    monkeypatch.setattr(os, "close", expire)
    with pytest.raises(official.LauncherError, match="^native-policy-preparation-changed$"):
        official._prepare_native_policy(**c.options)
    assert len(closed) == 1 and c.calls == {"resolver": 1, "current": 3, "approval": 0}
    with pytest.raises(OSError) as released:
        os.fstat(closed[0])
    assert released.value.errno == 9
    assert stat.S_ISDIR(os.fstat(c.options["policy_store"].fd).st_mode)


def v2_native_bridge_composition_start9(native_bridge_composition_case):
    """Consume the internally composed core using actual client and nine frames.

    Approvals, keys, process/B5 providers and publication remain explicitly
    synthetic. This proves core integration, not the outer Docker factory.
    """
    from apps.experiments_app.tests import test_ex10_executor as helper

    x = native_bridge_composition_case
    fixture, mod, a = x.c.fixture, x.c.fixture.module, x.c.x.a
    request = mod.wire.parse_request_bytes(fixture.rig.raw).to_dict()
    authorization = x.op.sql.parse_canonical(x.c.options["authorization_binding"].reference)
    request["authorization_reference"] = authorization
    request["capability"]["authorization_reference"] = authorization.copy()
    fixture.rig.raw = mod.builder.signed_request(request, a.bridge, a.holder)
    rig = helper.v2_native_start_setup(fixture, helper.dataset.__wrapped__(), client_proofs=True)
    directory = rig.store.root / ".s11-operational" / rig.host.exchange_id
    directory.parent.mkdir(mode=0o700, exist_ok=True)
    directory.mkdir(mode=0o700)
    private = x.op.durable.PrivateStore(str(directory), mod.h.identity(directory))
    a.stores["w09-composed-start-" + rig.host.exchange_id] = private
    copied = []

    def publish(role, raw):
        assert private.create_exact(str(directory / (role + ".json")), raw) is None
        copied.append(role)

    def holder(*, channel, process, proposal, role):
        assert channel is rig.host and role == "SESSION_EXECUTOR"
        assert process == x.c.x.n.options["process"]
        assert proposal.to_dict()["exchange_id"] == rig.host.exchange_id
        return a.holder.public_key()

    source = fixture.source.source
    bindings = tuple(
        b for name, b in source.registry.bindings.items() if name != "authorization"
    ) + (x.c.options["authorization_binding"],)
    options = dict(
        x.options,
        expected_run_id=None,
        start_nonce=rig.context.proposal["execution_nonce"],
        publish_role=publish,
        holder_admitter=holder,
        source_configuration={**x.options["source_configuration"], "bindings": bindings},
    )
    try:
        graph = official._compose_native_bridge(**options)
        assert graph.signer.index == 0 and graph.signer.run_id is None and copied == []
        # Only adapt test ownership slots. All runtime core objects above were
        # constructed internally; no production qualifier or validator is patched.
        a.journal = graph.journal
        fixture.source.source = graph.sources
        rig.broker = graph.broker
        result, errors, responses = helper.v2_run_native_host(
            rig, lambda: helper.v2_start_call(rig)
        )
        assert errors == [] and len(responses) == 1
        assert result["run_id"] == rig.context.proposal["run_id"] == graph.signer.run_id
        assert rig.host.position == rig.context.channel.position == 9
        assert rig.context.finished and rig.context.consumed and rig.context.scientific_finished
        assert graph.signer.index == 6 and not graph.signer.failed
        record = graph.journal.read()["latest"][rig.context.reservation["reservation_id"]]
        assert record["state"] == "COMMITTED"
        assert record["operator_authorization_reference"] == authorization
        assert record["coordination_policy_reference"] == graph.policy.reference
        assert authorization != graph.policy.reference
        assert a.transport.calls.count("RESERVE") == a.transport.calls.count("COMMIT_OPEN") == 1
        assert graph.publisher.transport.calls.count("B3_CAS") == 1
        assert len(rig.store.records(result["run_id"])) == 4
        assert any(
            r["run_id"] == result["run_id"]
            for r in x.op.history.list_official_runs(graph.sources.read_authority())
        )
        assert copied == [
            "base-material",
            "base-head",
            "commit-material",
            "commit-head",
            "commit-coordination",
            "commit-receipt",
        ]
        return SimpleNamespace(graph=graph, rig=rig, options=options, x=x, helper=helper)
    finally:
        rig.context.channel.close()
        rig.host.close()


@pytest.mark.parametrize("exchange", ["START", "START_THEN_SINGLE"])
def test_v2_native_bridge_composition_actual_start9_and_single5(
    native_bridge_composition_case, exchange
):
    """Real SDK/frame/history consumers; outer process/B5 providers synthetic."""
    started = v2_native_bridge_composition_start9(native_bridge_composition_case)
    if exchange == "START":
        return
    x, helper, fixture = started.x, started.helper, started.x.c.fixture
    a = fixture.rig.a
    single = helper.v2_native_single_setup(started.rig, action="execute", client_proofs=True)
    original = single.broker.sources
    directory = single.store.root / ".s11-operational" / single.host.exchange_id
    directory.mkdir(mode=0o700)
    private = x.op.durable.PrivateStore(str(directory), fixture.module.h.identity(directory))
    a.stores["w09-composed-single-" + single.host.exchange_id] = private
    copied = []

    def publish(role, raw):
        assert private.create_exact(str(directory / (role + ".json")), raw) is None
        copied.append(role)

    def holder(*, channel, process, proposal, role):
        assert channel is single.host and role == "SESSION_EXECUTOR"
        assert process == x.c.x.n.options["process"]
        assert proposal.to_dict()["exchange_id"] == single.host.exchange_id
        return single.context.holder.public_key()

    options = dict(
        started.options,
        action="execute",
        exchange_id=single.host.exchange_id,
        expected_run_id=started.rig.context.proposal["run_id"],
        start_nonce=None,
        holder_admitter=holder,
        publish_role=publish,
        authority_snapshot=original.read_authority(),
        source_configuration={
            **started.options["source_configuration"],
            "bindings": tuple(original.registry.bindings.values()),
            "historical_contexts": original.histories,
        },
    )
    try:
        # The helper prepares only disposable pipes, payload and raw history/PF
        # primitives. Its assembled broker/Sources are discarded before serving.
        graph = official._compose_native_bridge(**options)
        a.journal = graph.journal
        fixture.source.source = graph.sources
        single.broker = graph.broker
        _, responses = helper.v2_run_single_host(
            single,
            lambda: single.context.authorize_step(
                single.store,
                action="execute",
                run_id=started.rig.context.proposal["run_id"],
                git_sha=started.rig.context.proposal["git_sha"],
                phase="PREPARED",
            ),
        )
        assert len(responses) == 1 and single.context.finished
        assert single.context.step_authorized and single.context.preflight is None
        assert single.host.position == single.context.channel.position == 5
        assert graph.signer.index == 3 and not graph.signer.failed
        assert copied == ["action-material", "action-head", "action-coordination", "action-receipt"]
        assert a.transport.calls.count("RESERVE") == a.transport.calls.count("COMMIT_OPEN") == 1
        assert graph.publisher.transport.calls.count("B3_CAS") == 1
        assert graph.sources.policy is graph.policy and graph.signer.journal is graph.journal
        current = graph.sources.read_authority()
        assert x.op.history.find_run(current, graph.signer.run_id)["lifecycle_state"] == "OPEN"
    finally:
        single.host.close()
        single.context.channel.close()


@pytest.fixture
def native_proof_namespace_case(v2_action_intent):
    import time
    import uuid

    c = v2_action_intent
    c.intent.begin()
    root = Path(c.host.profile["host_roots"]["evidence"]["path"])
    options = dict(
        invocation=c.invocation,
        bootstrap=c.bootstrap,
        exchange_id=str(uuid.uuid4()),
        preparation_deadline=time.monotonic() + 900,
        authority_snapshot=c.bootstrap.read_authority(
            c.host.profile["historical_lifecycle_authority_reference"]
        ),
    )
    return SimpleNamespace(c=c, root=root, options=options, module=c.host.module)


@pytest.mark.parametrize("existing_parent", [False, True])
def test_v2_native_proof_namespace_actual_publication_and_reader(
    native_proof_namespace_case, existing_parent
):
    from apps.experiments_app import ex10_executor as executor

    x = native_proof_namespace_case
    parent = x.root / ".s11-operational"
    if existing_parent:
        parent.mkdir(mode=0o700)
    directory = parent / x.options["exchange_id"]
    borrowed = x.c.bootstrap.global_store.fd
    before = x.c.bootstrap.load(x.c.invocation)._bytes
    with x.module._create_native_proof_publication(**x.options) as (publisher, current):
        assert type(publisher) is x.module.NativeExclusiveProofPublication
        assert directory.stat().st_mode & 0o777 == 0o700
        assert current() is None
        owned = tuple(publisher.store.resolver._fds)
        raw = x.c.host.profile_path.read_bytes()
        publisher.publish("profile", raw)
        authority, sql = x.c.host.authority, x.c.host.apparatus["ex10_authority_sqlite"]
        reader = executor.ExecutorHostProofs(
            root=str(x.root),
            exchange_id=x.options["exchange_id"],
            b3_pins=[authority.pin.record],
            genesis_digest=sql.digest(authority.receipt),
            genesis_binding=authority.receipt["genesis"],
            clock=lambda: datetime.now(UTC),
        )
        value, copied = reader.read("profile")
        assert copied == raw and value == x.c.host.profile
        assert current() is None
    assert (directory / "profile.json").read_bytes() == raw
    assert x.c.bootstrap.load(x.c.invocation)._bytes == before
    assert stat.S_ISDIR(os.fstat(borrowed).st_mode)
    for fd in owned:
        with pytest.raises(OSError) as closed:
            os.fstat(fd)
        assert closed.value.errno == 9


def test_v2_native_proof_namespace_existing_exchange_never_adopted(native_proof_namespace_case):
    x = native_proof_namespace_case
    parent = x.root / ".s11-operational"
    parent.mkdir(mode=0o700)
    directory = parent / x.options["exchange_id"]
    directory.mkdir(mode=0o700)
    marker = directory / "unowned.json"
    marker.write_bytes(b'{"foreign":"existing-exchange"}\n')
    marker.chmod(0o600)
    before = _v2_private_tree_bytes(x.root)
    with pytest.raises(x.module.LauncherError, match="^native-proof-namespace-uncertain$"):
        with x.module._create_native_proof_publication(**x.options):
            pytest.fail("Existing exchange adopted")
    assert _v2_private_tree_bytes(x.root) == before


def test_v2_native_proof_namespace_caller_failure_retains_files(native_proof_namespace_case):
    x = native_proof_namespace_case
    with pytest.raises(x.module.LauncherError, match="^native-proof-namespace-uncertain$"):
        with x.module._create_native_proof_publication(**x.options) as (publisher, _):
            owned = tuple(publisher.store.resolver._fds)
            publisher.publish("profile", x.c.host.profile_path.read_bytes())
            raise RuntimeError("LOCAL TEST later factory failed")
    directory = x.root / ".s11-operational" / x.options["exchange_id"]
    assert (directory / "profile.json").read_bytes() == x.c.host.profile_path.read_bytes()
    for fd in owned:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("bad", ["../foreign", "00000000-0000-0000-0000-00000000000A", "", None])
def test_v2_native_proof_namespace_rejects_noncanonical_exchange(native_proof_namespace_case, bad):
    x = native_proof_namespace_case
    before = _v2_private_tree_bytes(x.root)
    with pytest.raises(x.module.LauncherError, match="^native-proof-namespace-uncertain$"):
        with x.module._create_native_proof_publication(**dict(x.options, exchange_id=bad)):
            pytest.fail("Noncanonical exchange accepted")
    assert _v2_private_tree_bytes(x.root) == before


@pytest.mark.parametrize("kind", ["mode", "symlink"])
def test_v2_native_proof_namespace_existing_parent_must_be_private(
    native_proof_namespace_case, kind
):
    x = native_proof_namespace_case
    parent = x.root / ".s11-operational"
    if kind == "mode":
        parent.mkdir(mode=0o750)
        parent.chmod(0o750)
        assert stat.S_IMODE(parent.stat().st_mode) == 0o750
    else:
        parent.symlink_to(x.root, target_is_directory=True)
    before = _v2_private_tree_bytes(x.root)
    with pytest.raises(x.module.LauncherError, match="^native-proof-namespace-uncertain$"):
        with x.module._create_native_proof_publication(**x.options):
            pytest.fail("Unqualified parent accepted")
    assert _v2_private_tree_bytes(x.root) == before


@pytest.mark.parametrize("boundary", ["parent", "exchange"])
def test_v2_native_proof_namespace_birth_identity_before_fsync(
    native_proof_namespace_case, monkeypatch, boundary
):
    x = native_proof_namespace_case
    parent = x.root / ".s11-operational"
    directory = parent / x.options["exchange_id"]
    if boundary == "exchange":
        parent.mkdir(mode=0o700)
    destination = parent if boundary == "parent" else directory
    moved = destination.with_name(destination.name + "-original-created")
    real_fsync = os.fsync
    changed = []

    def replace(fd):
        real_fsync(fd)
        target = x.root if boundary == "parent" else parent
        info = os.fstat(fd)
        if (
            not changed
            and destination.exists()
            and (info.st_dev, info.st_ino) == (target.stat().st_dev, target.stat().st_ino)
        ):
            inode = destination.stat().st_ino
            destination.rename(moved)
            destination.mkdir(mode=0o700)
            assert destination.stat().st_ino != inode and moved.stat().st_ino == inode
            changed.append(boundary)

    monkeypatch.setattr(os, "fsync", replace)
    with pytest.raises(x.module.LauncherError, match="^native-proof-namespace-uncertain$"):
        with x.module._create_native_proof_publication(**x.options):
            pass
    assert changed == [boundary]
    assert moved.is_dir() and destination.is_dir()


@pytest.mark.parametrize("field", ["store-fd", "resolver-fds"])
def test_v2_native_proof_namespace_cleanup_only_original_owned_fds(
    native_proof_namespace_case, field
):
    import inspect

    x = native_proof_namespace_case
    original_owned = []
    duplicate = None
    try:
        with pytest.raises(x.module.LauncherError, match="^native-proof-namespace-uncertain$"):
            with x.module._create_native_proof_publication(**x.options) as (publisher, current):
                # Test observation only: capture all actual internally owned
                # resolver descriptors from the returned closure's scope.
                owned = inspect.getclosurevars(current).nonlocals["owned"]
                original_owned = [fd for row in owned for fd, _, _ in row[-1]]
                if field == "store-fd":
                    duplicate = os.dup(publisher.store.fd)
                    publisher.store.fd = duplicate
                else:
                    publisher.store.resolver._fds = ()
                current()
        assert original_owned
        for fd in original_owned:
            with pytest.raises(OSError) as closed:
                os.fstat(fd)
            assert closed.value.errno == 9
        assert stat.S_ISDIR(os.fstat(x.c.bootstrap.global_store.fd).st_mode)
        if duplicate is not None:
            assert stat.S_ISDIR(os.fstat(duplicate).st_mode)
    finally:
        if duplicate is not None:
            os.close(duplicate)


@pytest.mark.parametrize("fence", ["deadline", "authority-expiry"])
def test_v2_native_proof_namespace_original_fences_after_owned_close(
    native_proof_namespace_case, monkeypatch, fence
):
    import inspect
    import time

    x = native_proof_namespace_case
    real_close = os.close
    changed = []

    def close(fd):
        caller = inspect.currentframe().f_back
        try:
            final = (
                caller.f_code.co_name == "_create_native_proof_publication"
                and caller.f_locals.get("completed") is True
            )
        finally:
            del caller
        real_close(fd)
        if final and not changed:
            changed.append(fence)
            if fence == "deadline":
                monkeypatch.setattr(
                    time, "monotonic", lambda: x.options["preparation_deadline"] + 1
                )
            else:
                history = x.c.host.apparatus["ex10_history_authority"]
                monkeypatch.setattr(
                    history, "_now", lambda: x.options["authority_snapshot"].expires_at
                )

    monkeypatch.setattr(os, "close", close)
    with pytest.raises(x.module.LauncherError, match="^native-proof-namespace-uncertain$"):
        with x.module._create_native_proof_publication(**x.options) as (_, current):
            assert current() is None
    assert changed == [fence]


def test_v2_native_proof_namespace_capture_failure_closes_owned_ancestors(
    native_proof_namespace_case, monkeypatch
):
    import inspect

    x = native_proof_namespace_case
    real_fstat = os.fstat
    opened = []

    def fail_capture(fd):
        info = real_fstat(fd)
        caller = inspect.currentframe().f_back
        try:
            if (
                not opened
                and caller.f_code.co_name == "<genexpr>"
                and caller.f_back.f_code.co_name == "open_store"
            ):
                store = caller.f_back.f_locals["store"]
                opened.append((store.resolver, tuple(store.resolver._fds)))
                raise OSError(5, "LOCAL TEST initial owned descriptor capture failed")
        finally:
            del caller
        return info

    monkeypatch.setattr(os, "fstat", fail_capture)
    try:
        with pytest.raises(x.module.LauncherError, match="^native-proof-namespace-uncertain$"):
            with x.module._create_native_proof_publication(**x.options):
                pytest.fail("Failed ownership capture returned")
        assert len(opened) == 1
        for fd in opened[0][1]:
            with pytest.raises(OSError) as closed:
                real_fstat(fd)
            assert closed.value.errno == 9
    finally:
        # Disposable fault injection retains only newly opened descriptors;
        # release test-owned leftovers after a failing leak assertion too.
        for resolver, fds in opened:
            for fd in fds:
                try:
                    real_fstat(fd)
                except OSError:
                    continue
                os.close(fd)
            resolver._fds = []
            resolver._closed = True


def test_v2_native_proof_namespace_original_bootstrap_authority_after_close(
    native_proof_namespace_case, monkeypatch
):
    """Two independently genuine B3 observations retain both original expiries."""
    import inspect

    x = native_proof_namespace_case
    a = x.c.host.authority
    history = x.c.host.apparatus["ex10_history_authority"]
    sql = x.c.host.apparatus["ex10_authority_sqlite"]
    witness = x.c.host.apparatus["ex10_authority_witness"]
    observation = a.observation()
    fields = {k: v for k, v in observation["head"].items() if k != "signature"}
    fields["expires_at"] = (sql.utc_time(fields["issued_at"]) + timedelta(seconds=5)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    observation["head"] = witness._sign_current_head(fields, a.key)
    clock = {"value": observation["now"]}
    monkeypatch.setattr(history, "_now", lambda: clock["value"])
    short = a.verify(observation=observation)
    assert short.expires_at < x.options["authority_snapshot"].expires_at

    def read_short(reference):
        assert reference == a.descriptor_ref
        return short

    original = x.c.bootstrap
    bootstrap = x.module.NativeLauncherBootstrap(
        repo_root=original.repo_root,
        expected_git_sha=original.expected_git_sha,
        profile_path=original.profile_path,
        path_context=json.loads(original.path_context_bytes),
        approval_reference=json.loads(original.approval_reference_bytes),
        session_context=json.loads(original.session_context_bytes),
        global_store=original.global_store,
        global_name=original.global_name,
        global_identity=original.global_identity,
        approve=original.approve,
        read_authority=read_short,
        read_checkpoint=original.read_checkpoint,
        publish_checkpoint=original.publish_checkpoint,
    )
    x.c.invocation.native_bootstrap = bootstrap
    options = dict(x.options, bootstrap=bootstrap)
    real_close = os.close
    expired = []

    def expire(fd):
        caller = inspect.currentframe().f_back
        try:
            final = (
                caller.f_code.co_name == "_create_native_proof_publication"
                and caller.f_locals.get("completed") is True
            )
        finally:
            del caller
        real_close(fd)
        if final and not expired:
            expired.append("original-bootstrap-observation-only")
            clock["value"] = short.expires_at

    monkeypatch.setattr(os, "close", expire)
    with pytest.raises(x.module.LauncherError, match="^native-proof-namespace-uncertain$"):
        with x.module._create_native_proof_publication(**options) as (_, current):
            assert current() is None
    assert expired == ["original-bootstrap-observation-only"]
    assert history.current_revision(x.options["authority_snapshot"])
    with pytest.raises(sql.AuthorityError, match="^EXPIRED_OBSERVATION$"):
        history.current_revision(short)


@pytest.fixture
def native_worker_bindings_case(v2_oneoff_control, monkeypatch):
    """Actual native components; external approvals/Docker/PF actor are synthetic."""
    import ast
    import copy
    import subprocess
    import time
    from dataclasses import replace

    c = v2_oneoff_control
    h, mod, old = c.host, c.host.module, c.control
    bootstrap = old.bootstrap
    apparatus = bootstrap.apparatus
    b5, op = apparatus["ex10_deployment_envelope"], apparatus["ex10_operational_context"]
    now = {"value": datetime.now(UTC), "monotonic": time.monotonic()}
    # Mechanical fixtures advance an explicit synthetic clock. Real Git/FS
    # guards still execute; wall-clock operational qualification is W20.
    monkeypatch.setattr(time, "monotonic", lambda: now["monotonic"])
    authority = h.authority
    original_observation = authority.observation

    def utc():
        return now["value"].strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def observation(state=None):
        value = original_observation(state)
        head = {k: v for k, v in value["head"].items() if k != "signature"}
        head["issued_at"] = utc()
        head["expires_at"] = (now["value"] + timedelta(seconds=30)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        value["head"] = op.history.witness._sign_current_head(head, authority.key)
        value["now"] = utc()
        return value

    monkeypatch.setattr(authority, "observation", observation)
    monkeypatch.setattr(op.history, "_now", utc)
    source = h.module._capture_native_source(
        bootstrap.repo_root / "backend/apps/experiments_app/ex10_preflight.py"
    )[0]
    inventory = next(
        ast.literal_eval(n.value)
        for n in ast.parse(source).body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "RUNNING_SOURCES" for t in n.targets)
    )
    sources = {
        name: hashlib.sha256((bootstrap.repo_root / name).read_bytes()).hexdigest()
        for name in (*inventory, "backend/apps/experiments_app/ex10_prepare.py")
    }
    cfg = json.loads(old.bootstrap_bytes)
    cfg["source_hashes"] = sources
    initial = mod.base.canonical_bytes(cfg)
    inv = replace(old.invocation, native_bootstrap=bootstrap)
    pending = bootstrap.load(inv)
    observed = {"hook": None, "calls": 0, "expected_namespace_abort": False}
    holder = {}

    class ObservedDocker:
        def __init__(self, cwd, environment):
            assert cwd == bootstrap.repo_root
            self.environment = dict(environment)

        def run(self, argv, *, timeout):
            result = old.runner.run(argv, timeout=timeout)
            if argv[2] != "inspect":
                return result
            value = json.loads(result.stdout)
            value[0]["Config"]["Env"] = [
                key + "=" + value for key, value in holder["control"].container_environment.items()
            ]
            return mod.Completed(0, mod.base.canonical_bytes(value), b"")

    def creation(**facts):
        assert facts["session"]._bytes == pending._bytes
        assert facts["profile"].to_dict() == h.profile
        assert facts["bootstrap_bytes"] == initial
        assert facts["name"] == old.name and facts["image_digest"] == old.image_digest
        assert facts["network_id"] == old.network_id
        assert facts["mounts"] == old.mounts and facts["labels"] == old.labels

    control = mod.NativeOneOffDocker(
        invocation=inv,
        bootstrap=bootstrap,
        name=old.name,
        image_digest=old.image_digest,
        network_id=old.network_id,
        bootstrap_bytes=initial,
        service_environment={
            "DJANGO_SETTINGS_MODULE": "config.settings.test",
            "SYNTHETIC_PRIVATE_VALUE": c.secret,
        },
        owner_labels={"s11.owner.session": pending.header["session_id"]},
        approve_creation=creation,
        runner_factory=ObservedDocker,
    )
    holder["control"] = control
    control.create()
    control.start()
    ready = {"deadline": None, "reads": 0}
    children = []

    def read_ready():
        ready["reads"] += 1
        return ready["deadline"]

    def launch(argv, **options):
        assert argv[3] == control.container_id
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", "import sys; sys.stdin.buffer.read()"], **options
        )
        children.append(child)
        return child

    attachment = mod.NativeWorkerAttachment(
        control=control, read_ready_deadline=read_ready, process_factory=launch
    )
    attachment.create()
    snapshot = authority.verify()

    def reference(path, value, schema):
        raw = mod.base.canonical_bytes(value)
        path.write_bytes(raw)
        path.chmod(0o600)
        return {
            "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "schema_version": schema,
        }

    profile_ref = {
        "path": str(bootstrap.profile_path),
        "sha256": cfg["profile_sha256"],
        "schema_version": apparatus["ex10_runtime_profile"].SCHEMA_VERSION,
    }
    header_ref = reference(
        h.home / "worker-header.json", pending.header, pending.header["schema_version"]
    )
    checks = frozenset(
        {
            "repository_source_complete",
            "protocol_identity",
            "no_side_effects",
            "ready_state:fixture_actors",
        }
    )
    pf = {
        "schema_version": b5.evidence.ARTIFACT_SCHEMAS["preflight_reference"],
        "protocol_version": b5.ex10_load.PROTOCOL_ID,
        "methodology_version": b5.ex10_load.METHODOLOGY_VERSION,
        "scenario_id": "EX-10",
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
        "expected_git_sha": h.profile["source_revision"],
        "git_sha": h.profile["source_revision"],
        "checked_at": utc(),
        "checks": [
            {
                "check": n,
                "result": "PASS",
                "expected": True,
                "observed_sanitized": True,
                "detail_code": None,
            }
            for n in sorted(checks)
        ],
    }
    pf_ref = reference(
        Path(h.profile["host_roots"]["evidence"]["path"]) / cfg["staging_name"],
        pf,
        pf["schema_version"],
    )
    head_ref = reference(
        h.home / "worker-authority.json",
        observation()["head"],
        "s11-ex10-synthetic-signed-head-reference-v1",
    )
    ready_ref = reference(
        h.home / "worker-ready.json",
        {"fixture_only_zero_reports": True},
        "s11-ex10-synthetic-ready-v1",
    )
    ready_state = {
        "report_reference": ready_ref,
        "checks": [{"check": "zero_reports", "result": "PASS"}],
    }
    containers, mounts = [], []
    compose = {"name": h.profile["project"], "services": {}}
    for service in h.profile["expected_services"]:
        cid = hashlib.sha256(("sut-" + service).encode()).hexdigest()
        image = "sha256:" + hashlib.sha256(("sut-" + service).encode()).hexdigest()
        item = {
            "container_id": cid,
            "service": service,
            "role": service,
            "image_digest": image,
            "running": True,
            "mounts": [],
        }
        if service in ("backend", "worker"):
            for source, target, readonly in control.mounts:
                mount = {
                    "container_id": cid,
                    "type": "bind",
                    "source_canonical": source,
                    "destination": target,
                    "read_only": readonly,
                    "ownership_reference": h.profile["deployment_verification_reference"],
                }
                item["mounts"].append(mount)
                mounts.append(mount)
        containers.append(item)
        compose["services"][service] = {
            "image": service + ":synthetic",
            "volumes": [
                {
                    "type": m["type"],
                    "source": m["source_canonical"],
                    "target": m["destination"],
                    "read_only": m["read_only"],
                }
                for m in item["mounts"]
            ],
        }

    def approved(**facts):
        if "profile" in facts:
            assert facts["profile"].to_dict() == h.profile
        if "header" in facts:
            assert facts["header"] == pending.header
        observed["calls"] += 1
        if observed["hook"] is not None:
            return observed["hook"](facts)

    def runtime_source(container_id, relative_path):
        assert container_id in {i["container_id"] for i in containers}
        assert relative_path in inventory
        return (bootstrap.repo_root / relative_path).read_bytes()

    options = {
        "profile_reference": profile_ref,
        "header_reference": header_ref,
        "application_preflight_reference": pf_ref,
        "historical_authority_view_reference": head_ref,
        "path_context": {
            **json.loads(bootstrap.path_context_bytes),
            "running_sources": [*inventory, "backend/apps/experiments_app/ex10_prepare.py"],
        },
        "running_sources": {name: sources[name] for name in inventory},
        "host_operational_sources": {
            "backend/apps/experiments_app/ex10_prepare.py": sources[
                "backend/apps/experiments_app/ex10_prepare.py"
            ]
        },
        "container_identities": [{k: i[k] for k in b5.CONTAINER_FIELDS} for i in containers],
        "mounts": mounts,
        "compose_projection": b5.sanitized_compose_projection(compose),
        "ready_state": ready_state,
        "application_required_checks": checks,
        "compose_observer": lambda: copy.deepcopy(compose),
        "container_observer": lambda: copy.deepcopy(containers),
        "running_source_reader": runtime_source,
        "bootstrap_verifier": approved,
        "source_verifier": approved,
        "session_verifier": approved,
        "authority_verifier": approved,
        "ready_verifier": approved,
        "clock": lambda: now["value"],
    }
    try:
        with mod._create_native_proof_publication(
            invocation=inv,
            bootstrap=bootstrap,
            exchange_id=cfg["exchange_id"],
            preparation_deadline=attachment.deadline,
            authority_snapshot=snapshot,
        ) as (publication, namespace_current):
            publication.publish("profile", control.profile._bytes)
            publication.publish("header", mod.base.canonical_bytes(pending.header))

            def current():
                if attachment.transferred:
                    attachment._check()
                else:
                    attachment._retained()
                namespace_current()

            arguments = dict(
                control=control,
                publication=publication,
                envelope_configuration=options,
                check_current=current,
                preparation_deadline=attachment.deadline,
                authority_snapshot=snapshot,
            )
            yield SimpleNamespace(
                case=c,
                h=h,
                mod=mod,
                control=control,
                attachment=attachment,
                publication=publication,
                arguments=arguments,
                options=options,
                observed=observed,
                sources=sources,
                pending=pending,
                cfg=cfg,
                ready=ready,
                now=now,
                snapshot=snapshot,
                containers=containers,
                pf_ref=pf_ref,
                b5=b5,
                op=op,
                time=time,
            )
        if observed["expected_namespace_abort"]:
            raise AssertionError("original profile mutation must deny namespace teardown")
    except mod.LauncherError as error:
        if (
            not observed["expected_namespace_abort"]
            or error.code != "native-proof-namespace-uncertain"
        ):
            raise
    finally:
        attachment.abort_client()
        assert all(p.poll() is not None for p in children)


def test_v2_native_worker_bindings_actual_control_b5_publication_and_adapter(
    native_worker_bindings_case,
):
    c = native_worker_bindings_case
    before = c.time.monotonic()
    result = c.mod._prepare_native_worker_bindings(**c.arguments)
    assert type(result) is c.mod.NativePreparedWorkerBindings
    assert type(result.process) is c.op.ExecutorProcessIdentity
    assert result.process.container_id == c.control.container_id
    assert before < result.ready_deadline <= c.attachment.deadline
    envelope = c.b5.parse_envelope_bytes(result.envelope_bytes).to_dict()
    assert (
        envelope["result"] == "PASS" and envelope["session_header_sha256"] == c.cfg["header_sha256"]
    )
    backend = next(
        item for item in envelope["container_identities"] if item["service"] == "backend"
    )
    assert backend["container_id"] != result.process.container_id
    assert set(c.options["host_operational_sources"]).isdisjoint(c.options["running_sources"])
    assert (
        c.publication.store.read(c.publication.store.root + "/process.json") == result.process_bytes
    )
    assert (
        c.publication.store.read(c.publication.store.root + "/envelope.json")
        == result.envelope_bytes
    )
    assert result.check_retained() is None
    c.ready["deadline"] = result.ready_deadline

    def observer():
        result.check_retained()
        return result.process

    adapter, channel = c.attachment.admit(identity=result.process, observer=observer)
    assert adapter.identity == result.process
    assert c.ready["reads"] == 1
    assert c.attachment.transferred
    channel.close()
    assert c.control.started and c.case.observations["phase"] == "running"


@pytest.mark.parametrize("change", ["container"])
def test_v2_native_worker_bindings_retains_original_after_b5_provider(
    native_worker_bindings_case, monkeypatch, change
):
    c = native_worker_bindings_case
    changed = []

    def late(facts):
        if changed or "ready_state" not in facts:
            return
        changed.append(change)
        if change == "container":
            c.control.container_id = "f" * 64
        elif change == "header":
            path = Path(c.options["header_reference"]["path"])
            path.write_bytes(path.read_bytes() + b" ")
        elif change == "source":
            path = c.control.bootstrap.repo_root / "backend/apps/experiments_app/ex10_prepare.py"
            path.write_bytes(path.read_bytes() + b"\n# changed fixture source\n")
        elif change == "authority-expiry":
            monkeypatch.setattr(c.op.history, "_now", lambda: c.snapshot.expires_at)
        else:
            monkeypatch.setattr(c.time, "monotonic", lambda: c.attachment.deadline + 1)

    c.observed["hook"] = late
    with pytest.raises(c.mod.LauncherError, match="^native-worker-bindings-uncertain$"):
        c.mod._prepare_native_worker_bindings(**c.arguments)
    assert changed == [change]
    assert "process" not in c.publication.attempted and "envelope" not in c.publication.attempted
    c.control.container_id = c.case.container_id


def test_v2_native_worker_bindings_never_adopts_post_publish_inode(native_worker_bindings_case):
    c = native_worker_bindings_case
    original = c.arguments["check_current"]
    changed = []

    def replace_published():
        original()
        path = Path(c.publication.store.root) / "process.json"
        if path.exists() and not changed:
            old_inode = path.stat().st_ino
            temporary = path.with_name("same-bytes-replacement.json")
            temporary.write_bytes(path.read_bytes())
            temporary.chmod(0o600)
            os.replace(temporary, path)
            assert path.stat().st_ino != old_inode
            changed.append("original-publication-replaced")

    with pytest.raises(c.mod.LauncherError, match="^native-worker-bindings-uncertain$"):
        c.mod._prepare_native_worker_bindings(**dict(c.arguments, check_current=replace_published))
    assert changed == ["original-publication-replaced"]


def test_v2_native_worker_bindings_final_checkpoint_io_cannot_change_profile(
    native_worker_bindings_case,
):
    import inspect

    c = native_worker_bindings_case
    path = c.control.bootstrap.profile_path
    both = []
    changed = []

    def late_checkpoint():
        caller = inspect.currentframe().f_back.f_back.f_back
        try:
            if caller.f_code.co_name == "retained" and "envelope" in c.publication.attempted:
                both.append(True)
                if len(both) == 3:
                    path.chmod(0o640)
                    changed.append("after-final-provider-before-return")
        finally:
            del caller

    c.h.checkpoint["read_hook"] = late_checkpoint
    try:
        with pytest.raises(c.mod.LauncherError, match="^native-worker-bindings-uncertain$"):
            c.mod._prepare_native_worker_bindings(**c.arguments)
        assert changed == ["after-final-provider-before-return"]
        # chmod restoration cannot restore ctime; the original invocation must
        # remain aborted at namespace teardown instead of pretending coherence.
        c.observed["expected_namespace_abort"] = True
    finally:
        c.h.checkpoint.pop("read_hook", None)
        path.chmod(0o600)


def test_v2_native_worker_bindings_expired_b5_after_last_provider_is_denied(
    native_worker_bindings_case,
):
    c = native_worker_bindings_case
    path = Path(c.pf_ref["path"])
    pf = json.loads(path.read_bytes())
    pf["checked_at"] = (c.now["value"] - timedelta(seconds=890)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    raw = c.mod.base.canonical_bytes(pf)
    path.write_bytes(raw)
    c.pf_ref["sha256"] = hashlib.sha256(raw).hexdigest()
    original = c.arguments["check_current"]
    instant = c.now["value"]
    both = []

    def late_time():
        original()
        if "envelope" in c.publication.attempted:
            both.append(True)
            if len(both) == 3:
                c.now["value"] = instant + timedelta(seconds=11)
                c.now["monotonic"] += 11
                assert c.op.history.current_revision(c.snapshot)["authority_id"]

    try:
        with pytest.raises(c.mod.LauncherError, match="^native-worker-bindings-uncertain$"):
            c.mod._prepare_native_worker_bindings(**dict(c.arguments, check_current=late_time))
        assert len(both) == 3
    finally:
        c.now["value"] = instant


@pytest.fixture
def native_factory_gate_case():
    """Installation gates only; no approval, store, key provisioning or Docker."""
    attempted = []

    def forbidden(**facts):
        attempted.append(facts)
        raise AssertionError("uninstalled factory must not call a provider")

    providers = {name: forbidden for name in official.NativeActionFactory._PROVIDERS}
    factory = official.NativeActionFactory(
        providers=providers,
        signer=object(),
        custody_hmac_key=b"SYNTHETIC-IN-MEMORY-ONLY" + b"x" * 32,
        runner_factory=forbidden,
        process_factory=forbidden,
    )
    return SimpleNamespace(factory=factory, attempted=attempted, providers=providers)


@pytest.mark.parametrize("action", sorted(official.ACTIONS))
def test_native_factory_all11_missing_installation_denies_before_provider_or_effect(
    native_factory_gate_case, action
):
    case = native_factory_gate_case
    invocation = official.Invocation(
        action=action,
        expected_git_sha="a" * 40,
        repo_root=Path("/unread-synthetic-checkout"),
        session_root=Path("/unread-synthetic-session"),
        environ={"S11_ACTION_FACTORY": "untrusted-request-selector"},
        confirmed=True,
    )
    with pytest.raises(official.LauncherError, match="^native-factory-installation-required$"):
        case.factory.execute(invocation)
    assert case.attempted == []
    assert case.factory._attempted == [invocation]
    assert not case.factory._lock.locked()


def test_native_factory_same_invocation_consumed_and_distinct_object_remains_distinct(
    native_factory_gate_case,
):
    from dataclasses import replace

    case = native_factory_gate_case
    invocation = official.Invocation(
        action="status",
        expected_git_sha="a" * 40,
        repo_root=Path("/unread-synthetic-checkout"),
        session_root=Path("/unread-synthetic-session"),
        environ={},
    )
    with pytest.raises(official.LauncherError, match="^native-factory-installation-required$"):
        case.factory.execute(invocation)
    with pytest.raises(official.LauncherError, match="^native-factory-attempt-consumed$"):
        case.factory.execute(invocation)
    distinct = replace(invocation)
    assert distinct == invocation and distinct is not invocation
    with pytest.raises(official.LauncherError, match="^native-factory-installation-required$"):
        case.factory.execute(distinct)
    assert len(case.factory._attempted) == 2
    assert case.factory._attempted[0] is invocation and case.factory._attempted[1] is distinct
    assert case.attempted == [] and not case.factory._lock.locked()


def test_native_factory_busy_does_not_consume_invocation_or_release_other_owner(
    native_factory_gate_case,
):
    case = native_factory_gate_case
    invocation = official.Invocation(
        action="status",
        expected_git_sha="a" * 40,
        repo_root=Path("/unread-synthetic-checkout"),
        session_root=Path("/unread-synthetic-session"),
        environ={},
    )
    assert case.factory._lock.acquire(blocking=False)
    try:
        with pytest.raises(official.LauncherBusy):
            case.factory.execute(invocation)
        assert case.factory._attempted == [] and case.factory._lock.locked()
    finally:
        case.factory._lock.release()
    assert case.attempted == []


def test_native_factory_copies_provider_map_and_denies_external_map_assignment(
    native_factory_gate_case,
):
    case = native_factory_gate_case
    original = dict(case.factory.providers)
    case.providers.clear()
    assert dict(case.factory.providers) == original
    with pytest.raises(TypeError):
        case.factory.providers["prepare_oneoff"] = lambda **facts: True
    assert case.attempted == []


@pytest.mark.parametrize("change", ["missing", "extra", "noncallable", "mapping"])
def test_native_factory_raw_provider_installation_is_closed(native_factory_gate_case, change):
    case = native_factory_gate_case
    providers = dict(case.factory.providers)
    if change == "missing":
        providers.pop("prepare_oneoff")
    elif change == "extra":
        providers["request_chosen_trust"] = lambda: True
    elif change == "noncallable":
        providers["approve_action"] = True
    else:
        from types import MappingProxyType

        providers = MappingProxyType(providers)
    with pytest.raises(official.LauncherError, match="^native-factory-installation-invalid$"):
        official.NativeActionFactory(
            providers=providers,
            signer=case.factory.signer,
            custody_hmac_key=case.factory.custody_key,
        )
    assert case.attempted == []


@pytest.mark.parametrize("key", [b"", b"x" * 31, "x" * 32, bytearray(b"x" * 32), None])
def test_native_factory_custody_key_installation_rejects_invalid_material(
    native_factory_gate_case, key
):
    case = native_factory_gate_case
    with pytest.raises(official.LauncherError, match="^native-factory-installation-invalid$"):
        official.NativeActionFactory(
            providers=dict(case.factory.providers), signer=case.factory.signer, custody_hmac_key=key
        )
    assert case.attempted == []


@pytest.fixture
def native_factory_resource_case(v2_native_host, monkeypatch, request):
    """Actual installed factory/B1/B2/B3/B4, synthetic Docker boundary and clock.

    Tests of status seed a closed B2 record explicitly; they do not certify a
    START. Cleanup uses actual empty coordination and signed witness heads.
    No synthetic callbacks replace product qualifiers, writer guards or gates.
    """
    import contextlib
    import copy
    import time
    from dataclasses import replace

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    h, mod = v2_native_host, v2_native_host.module
    boot = h.invocation().native_bootstrap
    op = boot.apparatus["ex10_operational_context"]
    sql, durable, coordination = op.sql, op.durable, op.coordination
    now = {"utc": datetime.now(UTC), "mono": time.monotonic()}
    monkeypatch.setattr(time, "monotonic", lambda: now["mono"])
    monkeypatch.setattr(op.history, "_now", lambda: coordination._utc(now["utc"]))
    service_name = op.__package__ + ".ex10_witness_service"
    # Already captured and verified transitively by coordination. Replacing
    # this module would correctly trip the host's immutable source closure.
    service = sys.modules[service_name]
    service_source = boot.repo_root / "backend/apps/experiments_app/ex10_witness_service.py"
    assert (
        service._s11_verified_source_sha256
        == hashlib.sha256(service_source.read_bytes()).hexdigest()
    )
    assert service.PrivateStore is durable.PrivateStore
    stores = []
    owned_stores = contextlib.ExitStack()
    request.addfinalizer(owned_stores.close)

    def identity(path):
        s = path.stat()
        return durable.FileIdentity(s.st_dev, s.st_ino, s.st_uid, stat.S_IMODE(s.st_mode))

    def store(name):
        path = h.home / name
        path.mkdir(mode=0o700)
        value = durable.PrivateStore(str(path), identity(path))
        stores.append(value)
        owned_stores.callback(value.close)
        return value

    credential = object()

    def authorize(*, operation, canonical_body, credentials):
        assert credentials is credential
        assert type(operation) is str
        sql.parse_canonical(canonical_body)

    def no_recovery(**facts):
        raise AssertionError("resource fixture never admits manual recovery")

    # Honor the actual test-only provisioning domain. Initial schema/state are
    # provisioned there, then copied exclusively into this disposable fixture's
    # fixed descriptor location before admission; no product guard is patched.
    provisioning = h.home / "s11-b3-test-resource-provisioning"
    provisioning.mkdir(mode=0o700)
    db = sql._create_test_database(provisioning / "ledger.sqlite3")
    try:
        db.execute("BEGIN")
        sql._insert_rows(db, h.authority.state)
        db.commit()
    finally:
        db.close()
    ledger = durable.PrivateStore(str(h.authority.root), identity(h.authority.root))
    stores.append(ledger)
    owned_stores.callback(ledger.close)
    ledger.create_exact(str(h.authority.database), (provisioning / "ledger.sqlite3").read_bytes())
    witness_store, sidecar, receipt_store, floor_store = (
        store(name)
        for name in (
            "resource-witness",
            "resource-coordination",
            "resource-receipts",
            "resource-floor",
        )
    )
    signer, witness_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    bridge_pin = op.wire.PublicKeyPin("synthetic-resource-bridge", 1, signer.public_key())
    witness_pin = op.wire.PublicKeyPin("synthetic-resource-witness", 1, witness_key.public_key())
    auth = SimpleNamespace(verify=authorize)
    witness_id = service._provision_witness_database(
        store=witness_store,
        name="witness.sqlite3",
        receipts=[h.authority.receipt],
        trust_pins=[h.authority.pin],
        expected_genesis_digest=sql.digest(h.authority.receipt),
        expected_genesis_binding=h.authority.receipt["genesis"],
        authorizer=auth,
        credentials=credential,
    )
    remote = service.WitnessService(
        store=witness_store,
        name="witness.sqlite3",
        identity=witness_id,
        authority_id=h.authority.descriptor["authority_id"],
        authority_epoch=1,
        genesis_digest=sql.digest(h.authority.receipt),
        genesis_binding=h.authority.receipt["genesis"],
        b3_pins=[h.authority.pin],
        b3_signer_pin=h.authority.pin,
        b3_signer=h.authority.key,
        b4_pin=witness_pin,
        b4_pins=[witness_pin],
        b4_signer=witness_key,
        authorizer=auth,
        recovery_authorizer=SimpleNamespace(verify=no_recovery),
        clock=lambda: now["utc"],
        clock_health=lambda: True,
    )
    reads = []

    def read_authority(reference):
        assert reference == h.authority.descriptor_ref
        challenge = hashlib.sha256(("factory-resource-" + str(len(reads))).encode()).hexdigest()
        reads.append(challenge)
        observed = {
            "head": remote.head_b3(challenge, credentials=credential),
            "challenge": challenge,
            "now": coordination._utc(now["utc"]),
            "request_elapsed_seconds": 0,
            "clock_healthy": True,
        }
        return h.authority.verify(
            state=sql._load_database(h.authority.database), observation=observed
        )

    def install(**changes):
        configuration = {
            name: getattr(boot, name)
            for name in (
                "repo_root",
                "expected_git_sha",
                "profile_path",
                "global_store",
                "global_name",
                "global_identity",
                "approve",
                "read_authority",
                "read_checkpoint",
                "publish_checkpoint",
                "action_factory",
            )
        }
        configuration.update(
            path_context=json.loads(boot.path_context_bytes),
            approval_reference=json.loads(boot.approval_reference_bytes),
            session_context=json.loads(boot.session_context_bytes),
        )
        configuration.update(changes)
        return mod.NativeLauncherBootstrap(**configuration)

    boot = install(read_authority=read_authority)
    binding = sql.canonical_bytes(
        {
            "namespace": "S11C3-EX10",
            "authority_id": h.authority.descriptor["authority_id"],
            "authority_epoch": 1,
            "lock_inode": boot.global_identity.inode,
            "coordination_inode": sidecar.identity.inode,
        }
    )
    head = remote.head_b4("0" * 64, credentials=credential)
    floor_store.create_exact(
        floor_store.root + "/head-00000000000000000000.json", sql.canonical_bytes(head)
    )
    floor = coordination.CoordinationFloor(
        floor_store,
        authority_id=h.authority.descriptor["authority_id"],
        authority_epoch=1,
        witness_pins=(witness_pin,),
    )
    sidecar_id = coordination._provision_coordination_database(
        store=sidecar,
        name="coord.sqlite3",
        registry_binding=binding,
        authority_id=h.authority.descriptor["authority_id"],
        authority_epoch=1,
        authorizer=auth,
        credentials=credential,
    )
    transport = SimpleNamespace(
        head=lambda challenge: remote.head_b4(challenge, credentials=credential)
    )
    # Additional raw witness operations for the joined full-factory fixture.
    # Resource-only tests still use the original read-only transport above.
    full_calls = []

    def reserve(raw, *, expected_sequence, expected_digest):
        full_calls.append("RESERVE")
        return remote.reserve(
            raw,
            expected_sequence=expected_sequence,
            expected_receipt_digest=expected_digest,
            credentials=credential,
        )

    def transition(**facts):
        full_calls.append(facts["transition"])
        return remote.transition(**facts, credentials=credential)

    def publish_b3(payload, expected_previous):
        full_calls.append("B3_CAS")
        return remote.publish_b3(payload, expected_previous, credentials=credential)

    full_transport = SimpleNamespace(
        head=transport.head,
        reserve=reserve,
        transition=transition,
        publish_b3=publish_b3,
        head_b3=lambda challenge: remote.head_b3(challenge, credentials=credential),
        calls=full_calls,
    )
    resource = dict(
        authority_id=h.authority.descriptor["authority_id"],
        authority_epoch=1,
        registry_binding=binding,
        lock_store=boot.global_store,
        lock_name=boot.global_name,
        lock_identity=boot.global_identity,
        sidecar_store=sidecar,
        sidecar_name="coord.sqlite3",
        sidecar_identity=sidecar_id,
        ledger_store=ledger,
        ledger_name=h.authority.database.name,
        ledger_identity=identity(h.authority.database),
        receipt_store=receipt_store,
        floor=floor,
        transport=transport,
        bridge_pin=bridge_pin,
        clock=lambda: now["utc"],
        clock_health=lambda: True,
    )
    cid, nid = (
        hashlib.sha256(b"resource-container").hexdigest(),
        hashlib.sha256(b"resource-network").hexdigest(),
    )
    project = h.profile["project"]
    plan = {
        "container_id": cid,
        "container_name": "synthetic-owned-backend",
        "image_digest": "sha256:" + "2" * 64,
        "labels": {
            "com.docker.compose.project": project,
            "s11.owner.session": "synthetic-approved-owner",
        },
        "mounts": [
            ["bind", h.profile["source_host_root"], h.profile["source_container_target"], False]
        ],
        "network_id": nid,
        "environment": {"S11_FIXTURE": "synthetic"},
    }
    network = {
        "id": nid,
        "name": "synthetic-private-network",
        "labels": {"com.docker.compose.project": project},
    }
    state = {
        "containers": {cid: "running"},
        "networks": {nid},
        "foreign_container": False,
        "foreign_network": False,
        "image": plan["image_digest"],
        "hook": None,
        "command_failure": None,
        "status": status_payload("PREPARED"),
        "effects": [],
        "commands": [],
    }
    approvals = []

    class DockerBoundary:
        def __init__(self, cwd, environment):
            assert cwd == boot.repo_root and environment["HOME"] == "/nonexistent"

        def run(self, argv, *, timeout):
            assert argv[0] == "docker" and timeout > 0
            args = tuple(argv[1:])
            state["commands"].append(args)
            if args[0] == "ps":
                values = set(state["containers"])
                if state["foreign_container"]:
                    values.add("f" * 64)
                raw = ("\n".join(sorted(values)) + ("\n" if values else "")).encode()
            elif args[:2] == ("network", "ls"):
                values = set(state["networks"])
                if state["foreign_network"]:
                    values.add("e" * 64)
                raw = ("\n".join(sorted(values)) + ("\n" if values else "")).encode()
            elif args[0] == "inspect":
                assert args == ("inspect", cid) and cid in state["containers"]
                raw = sql.canonical_bytes(
                    [
                        {
                            "Id": cid,
                            "Name": "/" + plan["container_name"],
                            "Image": state["image"],
                            "State": {"Status": state["containers"][cid]},
                            "Config": {
                                "Labels": plan["labels"],
                                "Env": [k + "=" + v for k, v in plan["environment"].items()],
                            },
                            "Mounts": [
                                {"Type": m[0], "Source": m[1], "Destination": m[2], "RW": m[3]}
                                for m in plan["mounts"]
                            ],
                            "NetworkSettings": {"Networks": {network["name"]: {"NetworkID": nid}}},
                        }
                    ]
                )
            elif args[:2] == ("network", "inspect"):
                assert args == ("network", "inspect", nid) and nid in state["networks"]
                raw = sql.canonical_bytes(
                    [
                        {
                            "Id": nid,
                            "Name": network["name"],
                            "Labels": network["labels"],
                            "Containers": {k: {} for k in state["containers"]},
                        }
                    ]
                )
            elif args[0] == "exec":
                assert args == (
                    "exec",
                    cid,
                    "/usr/local/bin/python",
                    "-B",
                    "/app/manage.py",
                    "s11_ex10",
                    "status",
                    "--run-id",
                    RUN_ID,
                )
                raw = sql.canonical_bytes(state["status"])
            elif args[0] == "stop":
                assert args == ("stop", "--time", "10", cid)
                state["effects"].append(args)
                state["containers"][cid] = "exited"
                raw = (cid + "\n").encode()
            elif args[0] == "rm":
                assert args == ("rm", cid) and state["containers"][cid] == "exited"
                state["effects"].append(args)
                del state["containers"][cid]
                raw = (cid + "\n").encode()
            else:
                assert args == ("network", "rm", nid) and not state["containers"]
                state["effects"].append(args)
                state["networks"].remove(nid)
                raw = (nid + "\n").encode()
            if state["command_failure"] == args[0]:
                return mod.Completed(1, b"", b"synthetic private failure")
            if args[0] == "inspect" and state.get("after_inspect") is not None:
                state["after_inspect"]()
            return mod.Completed(0, raw, b"")

    def forbidden(**facts):
        raise AssertionError("resource action must not prepare a scientific worker")

    def cleanup_plan(**facts):
        assert facts["profile"].to_dict() == h.profile
        return {
            "containers": [copy.deepcopy(plan)],
            "networks": [copy.deepcopy(network)],
            "resource_configuration": resource,
        }

    def domain_plan(**facts):
        assert facts["session"].to_dict()["official_run_id"] == RUN_ID
        return {"container": copy.deepcopy(plan)}

    def approve(**facts):
        if "resource_plan" in facts:
            assert facts["resource_plan"].lock_store is boot.global_store
            assert facts["resource_plan"].registry_binding == binding
        assert facts["profile"].to_dict() == h.profile
        approvals.append(facts.get("stage", "domain"))
        if state["hook"] is not None:
            state["hook"](facts)

    providers = {name: forbidden for name in mod.NativeActionFactory._PROVIDERS}
    providers.update(
        prepare_cleanup=cleanup_plan,
        approve_cleanup=approve,
        prepare_domain=domain_plan,
        approve_domain=approve,
    )
    factory = mod.NativeActionFactory(
        providers=providers,
        signer=signer,
        custody_hmac_key=b"synthetic-resource-only-" + b"x" * 32,
        runner_factory=DockerBoundary,
    )
    boot = install(action_factory=factory)

    def invocation(action="initialize-session", **changes):
        return replace(
            h.invocation(action, **changes), native_bootstrap=boot, clock=lambda: now["utc"]
        )

    try:
        result = mod.execute(invocation())
        assert result["launcher_exit"] == 0
        session = boot.load(invocation("inspect"))

        def seed_status():
            # Explicit physical component fixture. B2/status payload remain
            # synthetic: this does not qualify an SDK run or recover originals.
            evidence_root = Path(h.profile["host_roots"]["evidence"]["path"])
            runs = evidence_root / "runs"
            runs.mkdir(mode=0o700)
            run_root = runs / RUN_ID
            run_root.mkdir(mode=0o700)
            metadata = run_root / "metadata"
            metadata.mkdir(mode=0o700)
            for path, raw in (
                (evidence_root / ".s11-custody.lock", b""),
                (
                    metadata / "synthetic-component-fixture.json",
                    b'{"fixture":"STATUS physical component only"}\n',
                ),
            ):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            codec = boot.apparatus["ex10_session_v2"]
            payload = dict(session.to_dict()["history"][-1]["payload"])
            payload.update(
                event="complete-start-official",
                official_run_id=RUN_ID,
                preflight_report_sha256="a" * 64,
                operational_envelope_sha256="b" * 64,
                state={
                    "known_phase": "PREPARED",
                    "uncertain": False,
                    "blocked": False,
                    "next_allowed_actions": [
                        "abandon-official-pre-t0",
                        "execute-official",
                        "inspect",
                        "status",
                    ],
                },
            )
            proposal = codec.append_history_entry_v2(session, payload)
            with boot.writer() as writer, mod._native_session_lock(boot, h.root) as current:
                writer.check()
                current()
                actual = codec.update_official_session_v2(
                    h.root / mod.SESSION_FILE,
                    session,
                    proposal,
                    context=boot.context(),
                    profile=h.profile,
                    caller_serialized=True,
                )
                boot.publish((session._expected_tip, session._expected_count), actual)
                writer.check()
                current()

        yield SimpleNamespace(
            h=h,
            mod=mod,
            boot=boot,
            factory=factory,
            invocation=invocation,
            state=state,
            approvals=approvals,
            resource=resource,
            remote=remote,
            now=now,
            plan=plan,
            network=network,
            seed_status=seed_status,
            initial=session,
            full_transport=full_transport,
            install=install,
        )
    finally:
        owned_stores.close()


@pytest.fixture
def native_factory_recorded_output_case(native_factory_resource_case):
    """Explicit closed B2 fixture plus actual output writer; no scientific START."""
    from uuid import uuid4

    c = native_factory_resource_case
    c.seed_status()
    codec = c.boot.apparatus["ex10_session_v2"]
    inv = c.invocation("inspect")
    session = c.boot.load(inv)
    _, *_, original = c.boot.qualify(inv, session=session)
    raw = c.mod.base.canonical_bytes(c.state["status"])
    exchange = str(uuid4())
    with c.boot.writer() as writer, c.mod._native_session_lock(c.boot, c.h.root) as current:
        with c.mod._native_record_action_output(
            bootstrap=c.boot,
            invocation=inv,
            exchange_id=exchange,
            result_bytes=raw,
            check_current=original,
            check_authority_current=original.check_authority_current,
        ) as (relative, output_current):
            writer.check()
            current()
            payload = dict(session.to_dict()["history"][-1]["payload"])
            payload.update(
                event="complete-status", output_sha256={relative: hashlib.sha256(raw).hexdigest()}
            )
            proposal = codec.append_history_entry_v2(session, payload)
            actual = codec.update_official_session_v2(
                c.h.root / c.mod.SESSION_FILE,
                session,
                proposal,
                context=c.boot.context(),
                profile=c.h.profile,
                caller_serialized=True,
            )
            c.boot.publish((session._expected_tip, session._expected_count), actual)
            writer.check()
            current()
            output_current()
    assert actual.header == c.initial.header and actual._expected_count == 3
    assert c.mod.execute(c.invocation("inspect"))["launcher_exit"] == 0
    yield SimpleNamespace(c=c, output=c.h.root / relative, session=actual)


def _native_recorded_output_pending_intent(c, exchange):
    """Actual B2 intent only, with independently fixed synthetic action approval."""
    inv = c.invocation("execute-official", confirmed=True)
    original = c.boot.load(inv)
    output_dir = "actions/" + exchange
    approved = []

    def approve(**facts):
        assert facts["action"] == "execute-official"
        assert facts["session"]._bytes == original._bytes
        assert facts["profile"].to_dict() == c.h.profile
        assert facts["container_name"] == "s11-owned-output-inventory-test"
        assert facts["output_dir"] == output_dir
        approved.append(True)

    intent = c.mod.NativeActionIntent(
        invocation=inv,
        bootstrap=c.boot,
        container_name="s11-owned-output-inventory-test",
        output_dir=output_dir,
        approve_action=approve,
    )
    pending = intent.begin()
    assert approved == [True]
    assert pending.to_dict()["state"]["uncertain"] and pending.to_dict()["state"]["blocked"]
    assert pending.to_dict()["state"]["next_allowed_actions"] == ["inspect"]
    _, *_, original = c.boot.qualify(inv, session=pending)
    return inv, pending, original


def test_native_completed_output_inventory_preserves_pending_directory(
    native_factory_recorded_output_case,
):
    from uuid import uuid4

    c = native_factory_recorded_output_case.c
    exchange = str(uuid4())
    inv, pending, original = _native_recorded_output_pending_intent(c, exchange)
    with c.mod._native_record_action_output(
        bootstrap=c.boot,
        invocation=inv,
        exchange_id=exchange,
        result_bytes=b'{"fixture":"pending-result-not-completed"}\n',
        check_current=original,
        check_authority_current=original.check_authority_current,
    ) as (relative, current):
        before = _v2_private_tree_bytes(c.h.home)
        checkpoint = dict(c.h.checkpoint)
        assert c.mod.execute(c.invocation("inspect"))["launcher_exit"] == 0
        assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint
        assert (c.h.root / relative).is_file()
        assert c.boot.load(inv)._bytes == pending._bytes
        current()
    assert (c.h.root / relative).is_file() and c.h.checkpoint == checkpoint


def test_native_live_output_inventory_denies_extra_before_completion(
    native_factory_recorded_output_case,
):
    from uuid import uuid4

    c = native_factory_recorded_output_case.c
    exchange = str(uuid4())
    inv, pending, original = _native_recorded_output_pending_intent(c, exchange)
    checkpoint = dict(c.h.checkpoint)
    with pytest.raises(c.mod.LauncherError, match="^native-action-output-changed$"):
        with c.mod._native_record_action_output(
            bootstrap=c.boot,
            invocation=inv,
            exchange_id=exchange,
            result_bytes=b'{"fixture":"pending-result-not-completed"}\n',
            check_current=original,
            check_authority_current=original.check_authority_current,
        ) as (relative, current):
            extra = (c.h.root / relative).parent / "extra.txt"
            extra.write_bytes(b"LOCAL TEST unregistered before completion\n")
            extra.chmod(0o600)
            current()
    assert extra.is_file() and (c.h.root / relative).is_file()
    assert c.h.checkpoint == checkpoint and c.boot.load(inv)._bytes == pending._bytes
    assert c.state["effects"] == []


def test_native_output_inventory_union_preserves_codec_paths_and_prior_hashes(
    native_factory_recorded_output_case,
):
    c = native_factory_recorded_output_case.c
    codec = c.boot.apparatus["ex10_session_v2"]
    x = native_factory_recorded_output_case
    relative = str(x.output.relative_to(c.h.root))
    additional = x.output.with_name("additional.json")
    structural_parent_output = c.h.root / "notes.json"
    for path in (additional, structural_parent_output):
        assert not path.exists()
        path.write_bytes(c.mod.base.canonical_bytes({"fixture": path.name}))
        path.chmod(0o600)

    def append_outputs(mapping):
        inv = c.invocation("inspect")
        session = c.boot.load(inv)
        payload = dict(session.to_dict()["history"][-1]["payload"])
        payload.update(event="complete-status", output_sha256=mapping)
        proposal = codec.append_history_entry_v2(session, payload)
        with c.boot.writer() as writer, c.mod._native_session_lock(c.boot, c.h.root) as current:
            writer.check()
            current()
            actual = codec.update_official_session_v2(
                c.h.root / c.mod.SESSION_FILE,
                session,
                proposal,
                context=c.boot.context(),
                profile=c.h.profile,
                caller_serialized=True,
            )
            c.boot.publish((session._expected_tip, session._expected_count), actual)
            writer.check()
            current()
        return actual

    mapping = {
        str(path.relative_to(c.h.root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (additional, structural_parent_output)
    }
    actual = append_outputs(mapping)
    assert actual.header == x.session.header
    before = _v2_private_tree_bytes(c.h.home)
    checkpoint = dict(c.h.checkpoint)
    assert c.mod.execute(c.invocation("inspect"))["launcher_exit"] == 0
    assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint
    # A newer entry cannot replace the committed hash of an earlier output.
    x.output.write_bytes(b"LOCAL TEST altered earlier output\n")
    append_outputs({relative: hashlib.sha256(x.output.read_bytes()).hexdigest()})
    before = _v2_private_tree_bytes(c.h.home)
    checkpoint = dict(c.h.checkpoint)
    with pytest.raises(c.mod.SessionTampered, match="^native-action-output-changed$"):
        c.mod.execute(c.invocation("inspect"))
    assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint


def test_native_factory_cleanup_actual_signed_empty_coordination_exact_effects(
    native_factory_resource_case,
):
    c = native_factory_resource_case
    ledger = c.h.authority.database.read_bytes()
    coordination_path = Path(c.resource["sidecar_store"].root) / c.resource["sidecar_name"]
    before_coordination = coordination_path.read_bytes()
    before_profile = c.h.profile_path.read_bytes()
    result = c.mod.execute(c.invocation("cleanup-stack", confirmed=True))
    assert result["launcher_exit"] == 0 and result["classification"] == "COHERENT"
    assert c.state["effects"] == [
        ("stop", "--time", "10", c.plan["container_id"]),
        ("rm", c.plan["container_id"]),
        ("network", "rm", c.network["id"]),
    ]
    assert not c.state["containers"] and not c.state["networks"]
    completed = c.boot.load(c.invocation("inspect"))
    assert completed.to_dict()["history"][-1]["payload"]["event"] == "complete-cleanup-stack"
    assert completed.to_dict()["state"]["next_allowed_actions"] == ["inspect"]
    assert completed.to_dict()["in_flight"] == []
    assert c.h.checkpoint["value"] == (completed._expected_tip, completed._expected_count)
    assert c.h.authority.database.read_bytes() == ledger
    assert coordination_path.read_bytes() == before_coordination
    assert c.h.profile_path.read_bytes() == before_profile
    assert set(p.name for p in c.h.root.iterdir()) == {c.mod.SESSION_FILE}
    assert c.approvals.count("completion") == 2 and not c.factory._lock.locked()


@pytest.mark.parametrize(
    "fault",
    [
        "foreign-container",
        "foreign-network",
        "foreign-network-last-approval",
        "metadata-last-approval",
        "expired-last-approval",
        "remove-response-loss",
    ],
)
def test_native_factory_cleanup_foreign_late_changes_and_loss_keep_uncertain_intent(
    native_factory_resource_case, fault
):
    c = native_factory_resource_case
    if fault == "foreign-container":
        c.state["foreign_container"] = True
    elif fault == "foreign-network":
        c.state["foreign_network"] = True
    elif fault == "remove-response-loss":
        c.state["command_failure"] = "rm"
    else:

        def hook(facts):
            if facts.get("stage") == "stop":
                if fault == "foreign-network-last-approval":
                    c.state["foreign_network"] = True
                elif fault == "metadata-last-approval":
                    c.state["image"] = "sha256:" + "f" * 64
                else:
                    c.now["utc"] += timedelta(seconds=31)
                    c.now["mono"] += 31

        c.state["hook"] = hook
    with pytest.raises(c.mod.NativeActionFailure):
        c.mod.execute(c.invocation("cleanup-stack", confirmed=True))
    raw = json.loads((c.h.root / c.mod.SESSION_FILE).read_bytes())
    assert raw["history"][-1]["payload"]["event"] == "intent-cleanup-stack"
    assert raw["state"]["uncertain"] and raw["state"]["blocked"] and len(raw["in_flight"]) == 1
    assert raw["state"]["next_allowed_actions"] == ["inspect"]
    if fault == "remove-response-loss":
        assert [x[0] for x in c.state["effects"]] == ["stop", "rm"]
        assert c.network["id"] in c.state["networks"]
    else:
        assert c.state["effects"] == []
    assert not c.factory._lock.locked()


def test_native_factory_status_actual_installed_factory_readonly(native_factory_resource_case):
    c = native_factory_resource_case
    c.seed_status()
    before = _v2_private_tree_bytes(c.h.root)
    checkpoint = c.h.checkpoint["value"]
    ledger = c.h.authority.database.read_bytes()
    result = c.mod.execute(c.invocation("status"))
    assert result["launcher_exit"] == 0 and result["classification"] == "COHERENT"
    assert _v2_private_tree_bytes(c.h.root) == before
    assert c.h.checkpoint["value"] == checkpoint and c.h.authority.database.read_bytes() == ledger
    assert len([a for a in c.state["commands"] if a[0] == "exec"]) == 1
    assert c.approvals == ["domain", "domain"] and c.state["effects"] == []


@pytest.mark.parametrize(
    "fault",
    [
        "phase-mismatch",
        "foreign-run",
        "status-command-failure",
        "metadata-last-approval",
        "metadata-last-bootstrap-provider",
        "expiry-last-metadata-io",
    ],
)
def test_native_factory_status_mismatch_and_last_observation_denied_without_writes(
    native_factory_resource_case, fault
):
    c = native_factory_resource_case
    c.seed_status()
    before = _v2_private_tree_bytes(c.h.root)
    checkpoint = c.h.checkpoint["value"]
    if fault == "phase-mismatch":
        c.state["status"]["phase"] = "RECONCILING"
    elif fault == "foreign-run":
        c.state["status"]["run_id"] = OTHER_RUN
    elif fault == "status-command-failure":
        c.state["command_failure"] = "exec"
    else:

        def hook(facts):
            if "result" in facts:
                if fault == "metadata-last-approval":
                    c.state["image"] = "sha256:" + "f" * 64
                elif fault == "metadata-last-bootstrap-provider":

                    def change_bootstrap(facts):
                        c.state["image"] = "sha256:" + "f" * 64

                    c.h.approvals["hook"] = change_bootstrap
                else:

                    def expire():
                        c.now["utc"] += timedelta(seconds=31)
                        c.now["mono"] += 31

                    c.state["after_inspect"] = expire

        c.state["hook"] = hook
    with pytest.raises(c.mod.NativeActionFailure):
        c.mod.execute(c.invocation("status"))
    assert _v2_private_tree_bytes(c.h.root) == before and c.h.checkpoint["value"] == checkpoint
    assert c.state["effects"] == [] and not c.factory._lock.locked()


@pytest.mark.parametrize("action", ["status", "cleanup-stack"])
def test_native_factory_resource_original_success_fence_after_mutex_release(
    native_factory_resource_case, action
):
    c = native_factory_resource_case
    if action == "status":
        c.seed_status()
    original = c.factory._lock
    released = []

    class ReleaseBoundary:
        def acquire(self, *, blocking):
            return original.acquire(blocking=blocking)

        def release(self):
            original.release()
            released.append(True)
            c.now["utc"] += timedelta(seconds=31)
            c.now["mono"] += 31

    # Installed before the attempt; delegates the actual original mutex. This
    # checks the retained success fence after release, not scientific admission.
    c.factory._lock = ReleaseBoundary()
    with pytest.raises(
        c.mod.NativeActionFailure, match="^native-factory-final-observation-expired$"
    ):
        c.mod.execute(c.invocation(action, confirmed=action == "cleanup-stack"))
    assert released == [True] and not original.locked()
    if action == "status":
        assert c.state["effects"] == []
    else:
        # Real completed cleanup remains completed; expiry never rewinds B2 or
        # retries a retired resource even though the final green is denied.
        value = json.loads((c.h.root / c.mod.SESSION_FILE).read_bytes())
        assert value["history"][-1]["payload"]["event"] == "complete-cleanup-stack"
        assert len(c.state["effects"]) == 3


@pytest.fixture
def native_factory_full_case(native_factory_resource_case, request, monkeypatch):
    """Full installed host factory, actual protocol/custody/GOV/B3/B4/B2.

    External approval, Docker observations, PF probes and PID1 admission are
    synthetic. An actual owned Popen relays private pipes to an actual executor
    context in an owned thread. This is mechanical integration, not real Docker
    deployment, OS PID1 qualification, official START or a performance result.
    """
    import ast
    import contextlib
    import copy
    import subprocess
    import threading
    import time
    import traceback
    from dataclasses import replace
    from uuid import uuid4

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_resource_case
    h, mod, boot = c.h, c.mod, c.boot
    app = boot.apparatus
    op, b5, codec = (
        app["ex10_operational_context"],
        app["ex10_deployment_envelope"],
        app["ex10_session_v2"],
    )
    executor = helper.executor
    sql, durable = op.sql, op.durable
    owned = contextlib.ExitStack()
    request.addfinalizer(owned.close)
    children, threads, errors, results, phases = [], [], [], [], []
    captured = {}
    action = "start"
    launcher_action = "start-official"
    action_results = []
    original_qualify_open = op.NativeHostSources.qualify_open

    def capture_original_governance(source, **facts):
        result = original_qualify_open(source, **facts)
        assert len(source.catalog) == 1
        captured.update(source.catalog)
        return result

    monkeypatch.setattr(op.NativeHostSources, "qualify_open", capture_original_governance)
    stop = threading.Event()
    actor_done = threading.Event()
    publications_ready = threading.Event()
    preflight_ready = threading.Event()
    real_deadline = time.perf_counter() + 21200
    state = {
        "control": None,
        "cfg": None,
        "plan": None,
        "created": False,
        "running": False,
        "effects": [],
        "prepared": None,
        "context": None,
        "store": None,
        "steps": None,
        "completion": [],
    }

    def identity(path):
        s = path.stat()
        return durable.FileIdentity(s.st_dev, s.st_ino, s.st_uid, stat.S_IMODE(s.st_mode))

    stores = {}

    def retain(path):
        path = Path(path)
        key = str(path)
        if key not in stores:
            value = durable.PrivateStore(key, identity(path))
            stores[key] = value
            owned.callback(value.close)
        return stores[key]

    def directory(name):
        path = h.home / name
        path.mkdir(mode=0o700)
        return retain(path)

    def binding(name, path, raw, schema, *, create=True):
        path = Path(path)
        store = retain(path.parent)
        if create:
            store.create_exact(str(path), raw)
        else:
            assert store.read(str(path)) == raw
        ref = {
            "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "schema_version": schema,
        }
        return op.HostArtifactBinding(name, sql.canonical_bytes(ref), store)

    def new_binding(name, value, schema="s11-ex10-synthetic-full-host-v1"):
        return binding(
            name, h.home / ("full-" + name + ".json"), sql.canonical_bytes(value), schema
        )

    def reference(b):
        return sql.parse_canonical(b.reference)

    evidence = retain(Path(h.profile["host_roots"]["evidence"]["path"]))
    certificate_store = directory("full-certificates")
    candidate_store = directory("full-b3-candidates")
    monkeypatch.setattr(
        h.authority,
        "read_payload",
        _native_fixture_candidate_payload_reader(h.authority, candidate_store, sql),
    )
    b3_floor_store = directory("full-b3-floor")
    b3_floor_store.create_exact(
        b3_floor_store.root + "/floor-00000000000000000000.json",
        sql.canonical_bytes(h.authority.receipt),
    )
    b3_floor = durable.DurableFloor(
        b3_floor_store,
        [h.authority.pin],
        sql.digest(h.authority.receipt),
        h.authority.receipt["genesis"],
    )
    authorization = new_binding("authorization", {"approved": "synthetic-independent-A"})
    policy_store = directory("full-policy")
    policy_schema = "s11-ex10-synthetic-full-policy-v1"
    root_binding = hashlib.sha256(
        sql.canonical_bytes(c.initial.header["path_identity_binding"])
    ).hexdigest()
    host_bindings = {
        name: new_binding(name, {"synthetic_approval": name})
        for name in (
            "host_identity",
            "bridge_approval",
            "witness_transport",
            "writer_closure",
            "recovery_policy",
        )
    }
    host_bindings["coordinator_registry"] = binding(
        "coordinator_registry",
        h.home / "full-coordinator-registry.json",
        c.resource["registry_binding"],
        "s11-ex10-synthetic-coordinator-reference-v1",
    )
    base_receipt = binding(
        "base_receipt",
        h.home / "full-base-receipt.json",
        sql.canonical_bytes(h.authority.receipt),
        h.authority.receipt["witness_schema_version"],
    )
    inventory = next(
        ast.literal_eval(n.value)
        for n in ast.parse(
            (boot.repo_root / "backend/apps/experiments_app/ex10_preflight.py").read_bytes()
        ).body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "RUNNING_SOURCES" for t in n.targets)
    )
    sources = {
        p: hashlib.sha256((boot.repo_root / p).read_bytes()).hexdigest()
        for p in (*inventory, "backend/apps/experiments_app/ex10_prepare.py")
    }
    required = frozenset(n["check"] for n in helper.preflight_report()["checks"])
    holder_key = Ed25519PrivateKey.generate()
    cid = hashlib.sha256(b"full-factory-oneoff").hexdigest()
    image = "sha256:" + hashlib.sha256(b"full-factory-image").hexdigest()
    network = hashlib.sha256(b"full-factory-network").hexdigest()
    key = c.factory.custody_key
    fingerprint = executor.executor_fingerprint()
    assert (
        fingerprint
        == hashlib.sha256(
            sql.canonical_bytes({p: sources[p] for p in executor.EXECUTOR_SOURCES})
        ).hexdigest()
    )
    transport = c.full_transport

    def wait_for(predicate):
        while not predicate():
            if stop.is_set() or time.perf_counter() >= real_deadline:
                raise AssertionError("owned full factory fixture watchdog")
            time.sleep(0.02)

    def approved(**facts):
        if "profile" in facts:
            assert facts["profile"].to_dict() == h.profile
        if "header" in facts:
            assert facts["header"] == c.initial.header

    evidence_path = Path(evidence.root)
    exchange = str(uuid4())
    staging = "application-preflight." + str(uuid4()) + ".json"
    initial_snapshot = boot.read_authority(h.authority.descriptor_ref)
    acknowledgments = []
    for run in op.history.list_official_runs(initial_snapshot):
        target = op.history.verify_ack_target(initial_snapshot, run["run_id"])
        if target["recognizable"]:
            acknowledgments.append(
                {
                    "run_id": run["run_id"],
                    "authority_revision": target["revision"],
                    "terminal_evidence_reference": target["evidence_reference"],
                }
            )
    acknowledgments.sort(key=lambda item: item["run_id"])
    acknowledged = tuple(item["run_id"] for item in acknowledgments)

    def prepare_plan(**facts):
        current_session = facts["session"]
        assert current_session.header == c.initial.header
        state["session"] = current_session
        revision = op.history.current_revision(boot.read_authority(h.authority.descriptor_ref))

        def pin(p):
            return {
                "key_id": p.key_id,
                "key_epoch": p.key_epoch,
                "public_key_hex": p.public_key.public_bytes_raw().hex(),
            }

        cfg = {
            "action": action,
            "exchange_id": exchange,
            "profile_sha256": hashlib.sha256(boot.profile_path.read_bytes()).hexdigest(),
            "header_sha256": c.initial.to_dict()["header_sha256"],
            "bridge_pin": pin(c.resource["bridge_pin"]),
            "witness_pin": pin(c.resource["floor"].pins[0]),
            "b3_pins": [h.authority.pin.record],
            "genesis_digest": sql.digest(h.authority.receipt),
            "genesis_binding": h.authority.receipt["genesis"],
            "authority_revision": revision["revision"],
            "request_payload": {
                "expected_head": {
                    **{
                        k: revision[k]
                        for k in ("authority_id", "revision", "ledger_digest", "witness_digest")
                    },
                    "authority_epoch": revision["epoch"],
                    "receipt_reference": reference(base_receipt),
                },
                "acknowledgments": acknowledgments,
                "deployment_verification_reference": h.profile["deployment_verification_reference"],
                "descriptor_reference": h.authority.descriptor_ref,
                "profile_reference": {
                    "path": str(boot.profile_path),
                    "sha256": hashlib.sha256(boot.profile_path.read_bytes()).hexdigest(),
                    "schema_version": h.profile["schema_version"],
                },
                "session_reference": {
                    "path": str(h.home / "full-header.json"),
                    "sha256": c.initial.to_dict()["header_sha256"],
                    "schema_version": "s11-ex10-session-v2-header-projection-v1",
                },
            },
            "expected_reservation": state.get("original_reservation"),
            "host_root_binding_sha256": root_binding,
            "source_hashes": sources,
            "staging_name": staging,
            "command_options": {
                "expected_git_sha": boot.expected_git_sha,
                "source_root": "/s11-full-source",
                "nonce": "d" * 64,
                "acknowledge_not_demonstrated": list(acknowledged),
            },
            "authorization_reference": reference(authorization),
        }
        plan = {
            "name": "s11-synthetic-full-" + exchange,
            "image_digest": image,
            "network_id": network,
            "bootstrap_bytes": sql.canonical_bytes(cfg),
            "service_environment": {"DJANGO_SETTINGS_MODULE": "config.settings.test"},
            "owner_labels": {"s11.owner.session": c.initial.header["session_id"]},
        }
        if action == "start":
            assert facts["invocation"].preflight_report == evidence_path / staging
        else:
            record = state["original_reservation"]
            cfg["request_payload"] = {
                "reservation_id": record["reservation_id"],
                "original_request_id": record["request_id"],
            }
            cfg["staging_name"] = None
            cfg["command_options"] = {
                "expected_git_sha": boot.expected_git_sha,
                "run_id": record["run_id"],
                "abandon_reason": mod.ABANDON_REASON,
                "declare_executor_gone": False,
            }
            plan["bootstrap_bytes"] = sql.canonical_bytes(cfg)
        # Schema-only check before expensive host/OS preparation. This dummy
        # envelope reference is never passed to the factory or actual exchange;
        # the SDK binds the actual B5 bytes after their real publication.
        if action == "start":
            shape = {
                **cfg["request_payload"],
                "operational_envelope_reference": {
                    "path": str(evidence_path / ".s11-operational" / exchange / "envelope.json"),
                    "sha256": "0" * 64,
                    "schema_version": b5.SCHEMA_VERSION,
                },
                "preflight_reference": {
                    "path": str(evidence_path / staging),
                    "sha256": "0" * 64,
                    "schema_version": executor.ex10_preflight.SCHEMA_VERSION,
                },
                "run_identity": executor.propose_run(
                    started_at=c.now["utc"], git_sha=boot.expected_git_sha, nonce="d" * 64
                ),
            }
            executor.wire._validate(shape, executor.wire._SCHEMA_034)
        else:
            assert executor._native_initial_payload(cfg) == cfg["request_payload"]
        state.update(cfg=cfg, plan=plan)
        return plan

    class DockerBoundary:
        def __init__(self, cwd, environment):
            assert cwd == boot.repo_root
            self.environment = dict(environment)

        def run(self, argv, *, timeout):
            assert argv[:2] == ("/usr/bin/docker", "container") and timeout > 0
            action = argv[2]
            if action == "create":
                assert not state["created"]
                state["created"] = True
                state["effects"].append("create")
                environment = {}
                labels = {}
                mounts = []
                args = list(argv)
                for index, value in enumerate(args):
                    if value == "--env":
                        name = args[index + 1]
                        assert "=" not in name and name in self.environment
                        environment[name] = self.environment[name]
                    elif value == "--label":
                        k, v = args[index + 1].split("=", 1)
                        labels[k] = v
                    elif value == "--mount":
                        parts = dict(
                            x.split("=", 1) if "=" in x else (x, True)
                            for x in args[index + 1].split(",")
                        )
                        mounts.append(
                            {
                                "Type": parts["type"],
                                "Source": parts["source"],
                                "Destination": parts["target"],
                                "RW": not parts.get("readonly", False),
                            }
                        )
                state.update(environment=environment, labels=labels, mounts=mounts)
                return mod.Completed(0, (cid + "\n").encode(), b"")
            if action == "start":
                assert (
                    argv == ("/usr/bin/docker", "container", "start", cid)
                    and state["created"]
                    and not state["running"]
                )
                state["running"] = True
                state["effects"].append("start")
                return mod.Completed(0, (cid + "\n").encode(), b"")
            assert action == "inspect" and argv[-1] == cid
            running = state["running"] and not state.get("worker_retired", False)
            value = {
                "Id": cid,
                "Name": "/" + state["plan"]["name"],
                "Image": image,
                "State": {
                    "Status": "running"
                    if running
                    else ("exited" if state["running"] else "created"),
                    "Running": running,
                    "Restarting": False,
                    "Pid": 2345 if running else 0,
                    "ExitCode": 1 if errors else 0,
                    "OOMKilled": False,
                },
                "Config": {
                    "User": f"{os.getuid()}:{os.getgid()}",
                    "Labels": state["labels"],
                    "Env": [k + "=" + v for k, v in state["environment"].items()],
                    "Entrypoint": ["/usr/local/bin/python"],
                    "Cmd": ["-B", "/app/manage.py", "s11_ex10", "bridge"],
                    "WorkingDir": "/s11-full-source/backend",
                    "Healthcheck": {"Test": ["NONE"]},
                },
                "HostConfig": {
                    "ReadonlyRootfs": True,
                    "Privileged": False,
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges"],
                    "RestartPolicy": {"Name": "no"},
                    "PortBindings": {},
                    "PublishAllPorts": False,
                    "Binds": None,
                    "VolumesFrom": None,
                    "Devices": None,
                    "DeviceRequests": None,
                    "PidMode": "",
                    "UsernsMode": "",
                },
                "Mounts": state["mounts"],
                "NetworkSettings": {"Networks": {"synthetic-full-network": {"NetworkID": network}}},
            }
            return mod.Completed(0, sql.canonical_bytes([value]), b"")

    def prepare_envelope(**facts):
        state["control"] = facts["control"]
        phases.append("prepare_envelope")
        if action != "start":
            assert facts["pending"].header == c.initial.header
            return {
                "configuration": state["start_envelope_configuration"],
                "attached_bytes": state["start_envelope_bytes"],
            }
        wait_for(lambda: preflight_ready.is_set() or bool(errors))
        assert not errors, errors
        cfg = state["cfg"]
        control = facts["control"]
        pending = facts["pending"]
        profile_ref = {
            "path": str(boot.profile_path),
            "sha256": cfg["profile_sha256"],
            "schema_version": app["ex10_runtime_profile"].SCHEMA_VERSION,
        }
        header_ref = reference(
            binding(
                "header",
                h.home / "full-header.json",
                codec.canonical_session_header_bytes(pending.header),
                "s11-ex10-session-v2-header-projection-v1",
            )
        )
        pf_raw = evidence.read(str(evidence_path / staging))
        pf = sql.parse_canonical(pf_raw)
        pf_ref = {
            "path": str(evidence_path / staging),
            "sha256": hashlib.sha256(pf_raw).hexdigest(),
            "schema_version": pf["schema_version"],
        }
        head_ref = reference(
            binding(
                "authority_view",
                h.home / "full-authority-view.json",
                boot.read_authority(h.authority.descriptor_ref).canonical_current_head,
                "s11-ex10-synthetic-head-reference-v1",
            )
        )
        ready_ref = reference(new_binding("ready", {"synthetic_zero_reports": True}))
        containers, mounts = [], []
        compose = {"name": h.profile["project"], "services": {}}
        for service in h.profile["expected_services"]:
            sut = hashlib.sha256(("sut-full-" + service).encode()).hexdigest()
            item = {
                "container_id": sut,
                "service": service,
                "role": service,
                "image_digest": "sha256:" + hashlib.sha256(service.encode()).hexdigest(),
                "running": True,
                "mounts": [],
            }
            if service in ("backend", "worker"):
                for source, target, readonly in control.mounts:
                    m = {
                        "container_id": sut,
                        "type": "bind",
                        "source_canonical": source,
                        "destination": target,
                        "read_only": readonly,
                        "ownership_reference": h.profile["deployment_verification_reference"],
                    }
                    item["mounts"].append(m)
                    mounts.append(m)
            containers.append(item)
            compose["services"][service] = {
                "image": service + ":synthetic",
                "volumes": [
                    {
                        "type": m["type"],
                        "source": m["source_canonical"],
                        "target": m["destination"],
                        "read_only": m["read_only"],
                    }
                    for m in item["mounts"]
                ],
            }

        def running_source(container_id, relative_path):
            assert (
                container_id in {i["container_id"] for i in containers}
                and relative_path in inventory
            )
            return (boot.repo_root / relative_path).read_bytes()

        configuration = {
            "profile_reference": profile_ref,
            "header_reference": header_ref,
            "application_preflight_reference": pf_ref,
            "historical_authority_view_reference": head_ref,
            "path_context": {
                **json.loads(boot.path_context_bytes),
                "running_sources": list(sources),
            },
            "running_sources": {p: sources[p] for p in inventory},
            "host_operational_sources": {
                "backend/apps/experiments_app/ex10_prepare.py": sources[
                    "backend/apps/experiments_app/ex10_prepare.py"
                ]
            },
            "container_identities": [{k: i[k] for k in b5.CONTAINER_FIELDS} for i in containers],
            "mounts": mounts,
            "compose_projection": b5.sanitized_compose_projection(compose),
            "ready_state": {
                "report_reference": ready_ref,
                "checks": [{"check": "zero_reports", "result": "PASS"}],
            },
            "application_required_checks": required,
            "compose_observer": lambda: copy.deepcopy(compose),
            "container_observer": lambda: copy.deepcopy(containers),
            "running_source_reader": running_source,
            "bootstrap_verifier": approved,
            "source_verifier": approved,
            "session_verifier": approved,
            "authority_verifier": approved,
            "ready_verifier": approved,
            "clock": lambda: c.now["utc"],
        }
        state["envelope_configuration"] = configuration
        return {"configuration": configuration, "attached_bytes": None}

    def prepare_bridge(**facts):
        phases.append("prepare_bridge")
        prepared = facts["prepared"]
        pending = facts["pending"]
        publication = facts["publication"]
        state["prepared"] = prepared
        profile_binding = binding(
            "profile",
            boot.profile_path,
            boot.profile_path.read_bytes(),
            h.profile["schema_version"],
            create=False,
        )
        assert (h.root / mod.SESSION_FILE).read_bytes() == pending._bytes
        session_binding = binding(
            "session",
            h.home / ("full-approved-pending-session." + exchange + ".json"),
            pending._bytes,
            codec.SESSION_V2_SCHEMA,
        )
        header_binding = binding(
            "header",
            h.home / "full-header.json",
            codec.canonical_session_header_bytes(pending.header),
            "s11-ex10-session-v2-header-projection-v1",
            create=False,
        )
        session_bindings = [profile_binding, session_binding, header_binding]
        for name in app["ex10_runtime_profile"].REFERENCE_FIELDS:
            ref = h.profile[name]
            session_bindings.append(
                binding(
                    name,
                    Path(ref["path"]),
                    Path(ref["path"]).read_bytes(),
                    ref["schema_version"],
                    create=False,
                )
            )
        ref = pending.header["runtime_profile_approval_reference"]
        session_bindings.append(
            binding(
                "runtime_profile_approval_reference",
                Path(ref["path"]),
                Path(ref["path"]).read_bytes(),
                ref["schema_version"],
                create=False,
            )
        )
        envelope_binding = binding(
            "envelope",
            Path(publication.store.root) / "envelope.json",
            prepared.envelope_bytes,
            b5.SCHEMA_VERSION,
            create=False,
        )
        pf_ref = state["envelope_configuration"]["application_preflight_reference"]
        preflight_binding = binding(
            "preflight",
            Path(pf_ref["path"]),
            Path(pf_ref["path"]).read_bytes(),
            pf_ref["schema_version"],
            create=False,
        )
        descriptor = h.authority.descriptor_ref
        facts15 = {
            "namespace": "S11C3-EX10",
            "authority_id": c.resource["authority_id"],
            "authority_epoch": 1,
            "descriptor_reference": descriptor,
            "approved_host_identity_reference": reference(host_bindings["host_identity"]),
            "coordinator_registry_reference": reference(host_bindings["coordinator_registry"]),
            "lock_file_identity": op._file_identity(c.resource["lock_identity"]),
            "sidecar_identity": op._file_identity(c.resource["sidecar_identity"]),
            "ledger_identity": op._file_identity(c.resource["ledger_identity"]),
            "bridge_key_public_approval_reference": reference(host_bindings["bridge_approval"]),
            "independent_witness_transport_reference": reference(
                host_bindings["witness_transport"]
            ),
            "writer_closure_reference": reference(host_bindings["writer_closure"]),
            "approved_executor_entrypoint_identity": {
                "container_id": cid,
                "identity_digest": prepared.process.identity_digest,
                "argv": ["/usr/local/bin/python", "-B", "/app/manage.py", "s11_ex10", "bridge"],
            },
            "normal_operation_allowlist": sorted(op.NORMAL_OPERATIONS),
            "recovery_operator_approval_policy": reference(host_bindings["recovery_policy"]),
        }
        policy_binding = binding(
            "policy",
            Path(policy_store.root) / ("policy." + exchange + ".json"),
            sql.canonical_bytes(facts15),
            policy_schema,
        )

        def resolve(**kw):
            assert kw["canonical_facts"] == sql.canonical_bytes(facts15)
            return policy_binding

        def policy_approve(**kw):
            assert kw["canonical_authorization"] == sql.canonical_bytes(
                {"approved": "synthetic-independent-A"}
            )
            assert kw["authorization_reference"] == authorization.reference

        def envelope_verify(**kw):
            assert kw["canonical_envelope"] == prepared.envelope_bytes
            assert kw["reference"] == envelope_binding.reference
            assert (
                envelope_binding.store.read(reference(envelope_binding)["path"])
                == prepared.envelope_bytes
            )
            # This provider approves immutable B5 bytes. Factory-owned live and
            # completed process observations remain mandatory in product code.

        def session_approve(**kw):
            assert kw["profile"].to_dict() == h.profile
            assert kw["session"].header == pending.header

        session_cfg = {
            "path_context": sql.canonical_bytes(json.loads(boot.path_context_bytes)),
            "source_revision": boot.expected_git_sha,
            "launcher_blobs": sql.canonical_bytes(pending.header["launcher_blobs"]),
            "expected_tip": pending._expected_tip,
            "expected_count": pending._expected_count,
            "process": prepared.process,
            "approval_verifier": session_approve,
            "deployment_observer": lambda **kw: prepared.process,
        }
        policy_cfg = {
            "bindings": tuple(host_bindings.values()),
            "authorization_binding": authorization,
            "policy_store": policy_store,
            "policy_name": "policy." + exchange + ".json",
            "policy_schema_version": policy_schema,
            "approved_root_binding_sha256": root_binding,
            "approval_verifier": policy_approve,
            "envelope_verifier": envelope_verify,
            "required_preflight_checks": required,
            "resolve_policy_binding": resolve,
        }
        publisher_cfg = {
            "ledger_store": c.resource["ledger_store"],
            "ledger_name": c.resource["ledger_name"],
            "ledger_identity": c.resource["ledger_identity"],
            "candidate_store": candidate_store,
            "floor": b3_floor,
            "transport": transport,
            "signer_pin": h.authority.pin,
            "clock": lambda: c.now["utc"],
            "clock_health": lambda: True,
        }
        historical = state.get("historical", op.NativeGovernanceHistory(()))
        source_cfg = {
            "bindings": (
                authorization,
                envelope_binding,
                preflight_binding,
                base_receipt,
            ),
            "authority_reader": lambda: boot.read_authority(descriptor),
            "historical_contexts": lambda **kw: historical,
            "artifact_store": evidence,
            "certificate_store": certificate_store,
            "executor_fingerprint": fingerprint,
            "live_owner": None,
            "uncertainty_verifier": None,
            "recovery_sources": None,
            "lifecycle_sources": None,
        }
        publications_ready.set()
        return {
            "session_bindings": tuple(session_bindings),
            "session_configuration": session_cfg,
            "policy_configuration": policy_cfg,
            "publisher_configuration": publisher_cfg,
            "source_configuration": source_cfg,
            "resource_configuration": dict(c.resource, transport=transport),
            "reconciled": None,
        }

    def verify_local_seal(*, context, store, run_id, result):
        assert action == "finalize" and context is state["context"]
        assert run_id == result["run_id"]
        existing = executor.load_existing_state_readonly(store, run_id)
        assert existing is not None and existing.finalized
        executor.validate_finalized_state(existing.run_root, existing.records, custody_hmac_key=key)
        state["local_seal_checked"] = True

    def historical_original():
        assert len(captured) == 1
        reference_id, original = next(iter(captured.items()))
        sessions = op.NativeSessionQualifier(
            registry=original.sessions.registry,
            path_context=original.sessions.path_context,
            source_revision=original.sessions.source_revision,
            launcher_blobs=original.sessions.launcher_blobs,
            expected_tip=original.sessions.expected_tip,
            expected_count=original.sessions.expected_count,
            process=original.sessions.process,
            approval_verifier=original.sessions.approve,
            deployment_observer=lambda **kw: original.sessions.process,
        )
        original_policy = original.policy
        historical_policy = op.NativeHostPolicy(
            registry=original_policy.registry,
            ledger_store=original_policy.ledger,
            ledger_name=original_policy.name,
            ledger_identity=original_policy.identity,
            session_qualifier=sessions,
            approved_root_binding_sha256=original_policy.root_binding,
            approval_verifier=original_policy.approve,
            envelope_verifier=original_policy.envelope,
            required_preflight_checks=original_policy.checks,
            authorization_binding=authorization,
        )
        assert historical_policy.reference == original_policy.reference
        qualifier = op.NativeGovernanceQualifier(
            session_qualifier=sessions,
            registry=original.registry,
            certificate_store=original.store,
            bridge_pin=original.pin,
            holder_public_key=original.holder,
            reservation_id=original.reservation_id,
            executor_fingerprint=original.fingerprint,
            required_preflight_checks=original.checks,
            protocol=original.protocol,
            methodology=original.methodology,
            host_policy=historical_policy,
        )
        return op.NativeGovernanceHistory(((reference_id, qualifier),))

    def advance_to_abandon():
        nonlocal action, launcher_action, exchange, staging, cid, holder_key, base_receipt
        assert action == "start" and len(results) == 1 and state["completion"] == [False, True]
        assert all(child.poll() == 0 for child in children)
        for thread in threads:
            thread.join(timeout=5)
        assert not any(thread.is_alive() for thread in threads)
        original_session = boot.load(invocation)
        state["original_reservation"] = state["context"].reservation
        assert state["original_reservation"]["state"] == "COMMITTED"
        state["historical"] = historical_original()
        state["start_envelope_configuration"] = state["envelope_configuration"]
        state["start_envelope_bytes"] = state["prepared"].envelope_bytes
        revision = op.history.current_revision(boot.read_authority(h.authority.descriptor_ref))
        receipt_path = b3_floor._path(revision["revision"])
        receipt_bytes = b3_floor.store.read(receipt_path)
        base_receipt = op.HostArtifactBinding(
            "base_receipt",
            sql.canonical_bytes(
                {
                    "path": receipt_path,
                    "sha256": hashlib.sha256(receipt_bytes).hexdigest(),
                    "schema_version": sql.parse_canonical(receipt_bytes)["witness_schema_version"],
                }
            ),
            b3_floor.store,
        )
        action, launcher_action = "finalize", "abandon-official-pre-t0"
        exchange = str(uuid4())
        staging = None
        cid = hashlib.sha256(("full-factory-terminal-" + exchange).encode()).hexdigest()
        holder_key = Ed25519PrivateKey.generate()
        actor_done.clear()
        publications_ready.clear()
        preflight_ready.clear()
        state.update(
            control=None,
            cfg=None,
            plan=None,
            created=False,
            running=False,
            prepared=None,
            context=None,
            steps=None,
            completion=[],
            worker_retired=False,
            host_channel=None,
        )
        result = replace(c.invocation(launcher_action, confirmed=True), native_bootstrap=boot)
        assert boot.load(result)._bytes == original_session._bytes
        return result

    # Client setup and real relay are defined below; no assembled core is passed
    # to the installed factory.
    relay_exit = []
    relay_code = r"""
import os, select, sys, time
reader, writer, exit_reader = map(int,sys.argv[1:])
sources={0:writer,reader:1}
deadline=time.monotonic()+21200
while sources and time.monotonic()<deadline:
    ready,_,_=select.select(list(sources),[],[],1)
    for fd in ready:
        raw=os.read(fd,65536)
        target=sources[fd]
        if raw:
            view=memoryview(raw)
            while view:
                n=os.write(target,view)
                view=view[n:]
        else:
            del sources[fd]
            os.close(target)
            if target==1:
                os.close(2)
if sources:
    sys.exit(3)
while time.monotonic()<deadline:
    ready,_,_=select.select([exit_reader],[],[],1)
    if ready:
        raw=os.read(exit_reader,1)
        sys.exit(0 if raw==b'0' else 4)
sys.exit(5)
"""

    def actor(read_fd, write_fd):
        channel = None
        try:
            cfg = state["cfg"]
            store = helper.EvidenceStore(
                evidence_path,
                custody_hmac_key=key,
                publication_verification_key=c.factory.signer.public_key().public_bytes_raw(),
                rolling_checksums=False,
            )
            state["store"] = store
            probes = executor.ExecutorProbes(
                preflight=lambda options: helper.preflight_report(
                    git_sha=boot.expected_git_sha, checked_at=c.now["utc"]
                ),
                dataset=helper.ex10.load_dataset,
                provider=lambda: [],
                sources=lambda root: [],
                fingerprint=lambda: fingerprint,
                now=lambda: c.now["utc"],
            )
            if action == "start":
                proposal, raw = executor._prepare_native_start(
                    probes=probes,
                    git_sha=boot.expected_git_sha,
                    source_root=boot.repo_root,
                    nonce="d" * 64,
                )
                evidence.create_exact(str(evidence_path / staging), raw)
                state["start_preflight"] = raw
                preflight_ready.set()
            else:
                raw = state["start_preflight"]
                proposal = state["original_reservation"]["run_identity"]
            wait_for(lambda: publications_ready.is_set())
            proof = executor.ExecutorHostProofs(
                root=str(evidence_path),
                exchange_id=exchange,
                b3_pins=[h.authority.pin.record],
                genesis_digest=sql.digest(h.authority.receipt),
                genesis_binding=h.authority.receipt["genesis"],
                clock=lambda: c.now["utc"],
            )
            process, _ = proof.read("process")
            _, envelope = proof.read("envelope")
            client_profile = executor.profile_codec.parse_runtime_profile_bytes(
                boot.profile_path.read_bytes()
            )
            executor._native_envelope_bindings(
                envelope,
                profile=client_profile,
                header=c.initial.header,
                cfg=cfg,
                preflight=raw,
                now=c.now["utc"],
                fresh_required=action == "start",
            )
            assert process == executor._native_process_identity(
                cfg=cfg,
                profile=client_profile,
                container_id=cid,
                initial=state["plan"]["bootstrap_bytes"],
                envelope=envelope,
            )
            fixed = {
                "exchange_id": exchange,
                "authority_id": c.resource["authority_id"],
                "authority_epoch": 1,
                "authorization_reference": reference(authorization),
                "bridge_key_id": cfg["bridge_pin"]["key_id"],
                "bridge_key_epoch": cfg["bridge_pin"]["key_epoch"],
                "container_identity_digest": process["identity_digest"],
                "deployment_verification_sha256": process["deployment_verification_sha256"],
                "session_id": c.initial.header["session_id"],
                "session_header_sha256": cfg["header_sha256"],
                "runtime_profile_sha256": cfg["profile_sha256"],
                "source_revision": boot.expected_git_sha,
                "project": h.profile["project"],
            }

            def admit(**kw):
                assert kw["context"].owner == (os.getpid(), threading.get_ident())
                assert kw["context"].holder is holder_key and kw["action"] == action
                assert process["container_id"] == cid and state["running"] and not stop.is_set()
                assert boot.profile_path.read_bytes() == client_profile._bytes

            channel = executor.ExecutorBridgeChannel(
                read_fd=read_fd,
                write_fd=write_fd,
                exchange="START" if action == "start" else "SINGLE_RPC",
                exchange_id=exchange,
                budget=120 if action == "start" else 30,
            )
            os.close(read_fd)
            os.close(write_fd)
            context = executor.ExecutorOperationalContext(
                action=action,
                channel=channel,
                bridge_pin=executor.wire.PublicKeyPin(
                    c.resource["bridge_pin"].key_id,
                    c.resource["bridge_pin"].key_epoch,
                    c.resource["bridge_pin"].public_key,
                ),
                witness_pin=executor.wire.PublicKeyPin(
                    c.resource["floor"].pins[0].key_id,
                    c.resource["floor"].pins[0].key_epoch,
                    c.resource["floor"].pins[0].public_key,
                ),
                holder_key=holder_key,
                expected_challenge=fixed,
                request_payload=executor._native_prepared_payload(
                    cfg=cfg, profile=client_profile, envelope=envelope
                ),
                evidence_root=str(evidence_path),
                host_evidence_root=str(evidence_path),
                staging_name=staging if action == "start" else None,
                expected_reservation=state.get("original_reservation"),
                authority_revision=cfg["authority_revision"],
                host_root_binding_sha256=root_binding,
                native_admitter=admit,
                current_guard=proof.current,
                common_prior_reader=proof.common_prior,
                common_prior_verifier=proof.verify_common,
                commit_verifier=proof.verify_commit,
                terminal_preparer=verify_local_seal,
                clock=lambda: c.now["utc"],
            )
            context.preflight, context.proposal, context.store, context.prepared = (
                raw,
                proposal,
                store,
                True,
            )
            state["context"] = context
            if action == "start":
                result = executor.start_run(
                    store,
                    git_sha=boot.expected_git_sha,
                    source_root=boot.repo_root,
                    nonce="d" * 64,
                    evidence_mode=executor.OFFICIAL_MODE,
                    timezone="UTC",
                    acknowledged_not_demonstrated=acknowledged,
                    probes=probes,
                    operational_context=context,
                )
            else:
                result = executor.finalize_run(
                    store,
                    run_id=proposal["run_id"],
                    git_sha=boot.expected_git_sha,
                    abandon_reason=mod.ABANDON_REASON,
                    probes=probes,
                    operational_context=context,
                )
            context.persist_public_result(store, result)
            results.append(result)
            action_results.append(
                {
                    "action": action,
                    "cid": cid,
                    "exchange": exchange,
                    "context": context,
                    "result": result,
                    "steps": channel.position,
                }
            )
            state["steps"] = channel.position
        except BaseException as error:
            errors.append(error)
            state["actor_error_trace"] = traceback.format_exception(
                type(error), error, error.__traceback__
            )
        finally:
            if channel is not None:
                channel.close()
            else:
                for fd in (read_fd, write_fd):
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            actor_done.set()

    def launch(argv, **options):
        assert argv == (
            "/usr/bin/docker",
            "exec",
            "--interactive",
            cid,
            "/usr/local/bin/python",
            "-B",
            "/app/manage.py",
            "s11_ex10",
            "bridge",
        )
        private_fds = []
        try:
            client_read, relay_write = os.pipe()
            private_fds.extend((client_read, relay_write))
            relay_read, client_write = os.pipe()
            private_fds.extend((relay_read, client_write))
            exit_read, exit_write = os.pipe()
            private_fds.extend((exit_read, exit_write))
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    relay_code,
                    str(relay_read),
                    str(relay_write),
                    str(exit_read),
                ],
                pass_fds=(relay_read, relay_write, exit_read),
                **options,
            )
            children.append(child)
            for fd in (relay_write, relay_read, exit_read):
                os.close(fd)
                private_fds.remove(fd)
            relay_exit.append(exit_write)
            private_fds.remove(exit_write)
            thread = threading.Thread(target=actor, args=(client_read, client_write), daemon=True)
            threads.append(thread)
            thread.start()
            private_fds.remove(client_read)
            private_fds.remove(client_write)
            return child
        finally:
            for fd in private_fds:
                try:
                    os.close(fd)
                except OSError:
                    pass

    original_begin_exchange = op.NativeHostSources.begin_exchange

    def observe_host_exchange(source, *, channel):
        # Keep every original host admission/guard. This only observes the
        # actual HOST channel that the product installs for this exchange.
        result = original_begin_exchange(source, channel=channel)
        state["host_channel"] = source.channel
        return result

    monkeypatch.setattr(op.NativeHostSources, "begin_exchange", observe_host_exchange)

    class OwnedWorkerDocker(DockerBoundary):
        def run(self, argv, *, timeout):
            # The synthetic PID1 wrapper retires only after actual client work
            # and the host's owned preparation descriptors have been closed.
            publication = state.get("publication")
            if actor_done.is_set() and publication is not None:
                try:
                    info = os.fstat(publication.store.fd)
                    retired = (info.st_dev, info.st_ino) != state["publication_fd_identity"]
                except (OSError, TypeError):
                    retired = True
                host_channel = state.get("host_channel")
                host_eof = (
                    type(host_channel) is op.PipeFrameChannel
                    and host_channel.role == "HOST"
                    and host_channel.exchange == "SINGLE_RPC"
                    and host_channel.exchange_id == exchange
                    and host_channel.position == len(host_channel.steps) == 5
                    and host_channel.closed
                    and host_channel.read_fd
                    is host_channel.write_fd
                    is host_channel.error_fd
                    is None
                )
                if (action == "start" and retired) or (action != "start" and host_eof):
                    # The actor's local EOF precedes the host's final guards.
                    # Terminal keeps PID1 running through actual HOST EOF; its
                    # proof namespace remains retained until B3/B4 RETIRE.
                    state["worker_retired"] = True
                    if relay_exit:
                        os.write(relay_exit[0], b"0" if not errors else b"1")
                        os.close(relay_exit.pop())
            return super().run(argv, timeout=timeout)

    def raw_bridge(**facts):
        state["publication"] = facts["publication"]
        info = os.fstat(facts["publication"].store.fd)
        state["publication_fd_identity"] = (info.st_dev, info.st_ino)
        return prepare_bridge(**facts)

    def holder(**facts):
        assert facts["process"].container_id == cid and facts["role"] == "SESSION_EXECUTOR"
        assert (
            facts["proposal"].to_dict()["holder_public_key_hex"]
            == holder_key.public_key().public_bytes_raw().hex()
        )
        return holder_key.public_key()

    def completion(**facts):
        assert facts["action"] == launcher_action
        assert facts["result_bytes"] == sql.canonical_bytes(results[-1])
        if action == "start":
            assert facts["terminal"] is None
        else:
            snapshot, retired = facts["terminal"]
            assert (
                op.history.find_run(snapshot, results[-1]["run_id"])["lifecycle_state"]
                == "TERMINAL"
            )
            assert retired["terminal_witness_reference"] is not None
        assert type(facts["response"]) is bytes
        assert op.wire.parse_response_bytes(facts["response"]).to_dict()["status"] == "OK"
        state["completion"].append(facts["written"])

    def forbidden(**facts):
        raise AssertionError("full START must not route domain/cleanup/terminal")

    providers = {name: forbidden for name in mod.NativeActionFactory._PROVIDERS}
    providers.update(
        prepare_oneoff=prepare_plan,
        approve_action=approved,
        approve_creation=approved,
        prepare_envelope=prepare_envelope,
        prepare_bridge=raw_bridge,
        holder_admitter=holder,
        approve_completion=completion,
        approve_terminal=approved,
    )
    factory = mod.NativeActionFactory(
        providers=providers,
        signer=c.factory.signer,
        custody_hmac_key=key,
        runner_factory=OwnedWorkerDocker,
        process_factory=launch,
    )
    boot = c.install(action_factory=factory)
    invocation = replace(
        c.invocation(
            "start-official",
            confirmed=True,
            nonce="d" * 64,
            acknowledged=acknowledged,
            preflight_report=evidence_path / staging,
        ),
        native_bootstrap=boot,
    )
    try:
        yield SimpleNamespace(
            c=c,
            h=h,
            mod=mod,
            boot=boot,
            factory=factory,
            invocation=invocation,
            state=state,
            errors=errors,
            results=results,
            children=children,
            threads=threads,
            phases=phases,
            b3_floor=b3_floor,
            transport=transport,
            advance=advance_to_abandon,
            action_results=action_results,
        )
    finally:
        stop.set()
        context = state["context"]
        if context is not None:
            context.channel.close()
        for fd in relay_exit:
            try:
                os.close(fd)
            except OSError:
                pass
        relay_exit.clear()
        for child in children:
            mod.NativeWorkerAttachment._reap(child)
        for thread in threads:
            thread.join(timeout=5)
        assert all(not thread.is_alive() for thread in threads)
        assert all(child.poll() is not None for child in children)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_v2_native_factory_full_start_actual_protocol_gov_b3_b4_b2(native_factory_full_case):
    c = native_factory_full_case
    try:
        result = c.mod.execute(c.invocation)
    except Exception as error:
        import traceback

        # Suppressed rendering (raise from None) retains __context__. Observe
        # actual exceptions without replacing any host guard or callback.
        chain, seen = [], set()
        cursor = error
        while cursor is not None and id(cursor) not in seen and len(chain) < 16:
            seen.add(id(cursor))
            chain.append(
                "".join(
                    traceback.format_exception(
                        type(cursor), cursor, cursor.__traceback__, chain=False
                    )
                )
            )
            cursor = cursor.__cause__ or cursor.__context__
        for thread in c.threads:
            thread.join(timeout=5)
        pytest.fail(
            f"full START failed: {type(error).__name__}:{error}; phases={c.phases}; "
            f"actor_errors={[(type(e).__name__, str(e)) for e in c.errors]}; "
            f"actor_trace={c.state.get('actor_error_trace', [])}; host_error_chain={chain}"
        )
    assert not c.errors and len(c.results) == 1
    assert result["launcher_exit"] == 0
    assert c.state["effects"] == ["create", "start"]
    assert c.state["steps"] == 9
    assert c.state["completion"] == [False, True]
    context = c.state["context"]
    scientific = c.results[0]
    assert context.finished and context.scientific_finished and context.consumed
    assert scientific["run_id"] == context.proposal["run_id"]
    assert len(c.state["store"].records(scientific["run_id"])) == 4
    assert all(child.poll() == 0 for child in c.children)
    session = c.boot.load(c.invocation)
    assert session.to_dict()["official_run_id"] == scientific["run_id"]
    assert session.to_dict()["history"][-1]["payload"]["event"] == "complete-start-official"
    assert session.to_dict()["history"][-1]["payload"]["in_flight"] == []
    assert session.to_dict()["state"]["known_phase"] == "PREPARED"
    op = c.boot.apparatus["ex10_operational_context"]
    snapshot = c.boot.read_authority(c.h.authority.descriptor_ref)
    assert op.history.find_run(snapshot, scientific["run_id"])["lifecycle_state"] == "OPEN"
    assert op.history.current_revision(snapshot)["revision"] == 1
    assert c.b3_floor.current().revision == 1
    terminal_invocation = c.advance()
    terminal = c.mod.execute(terminal_invocation)
    assert terminal["launcher_exit"] == 0 and not c.errors
    assert len(c.action_results) == 2
    first, last = c.action_results
    assert first["cid"] != last["cid"] and first["exchange"] != last["exchange"]
    assert last["steps"] == 5 and last["context"].terminal_prepared
    assert last["result"]["decision"] == "NOT_DEMONSTRATED" and not last["result"]["binding"]
    assert c.state["local_seal_checked"] and c.state["completion"] == [False, True]
    assert c.transport.calls.count("RESERVE") == c.transport.calls.count("COMMIT_OPEN") == 1
    assert c.transport.calls.count("B3_CAS") == 2
    assert c.transport.calls.count("RETIRE_TERMINAL") == 1
    final = c.boot.load(terminal_invocation)
    assert final.header == c.c.initial.header
    assert final.to_dict()["official_run_id"] == first["result"]["run_id"]
    assert final.to_dict()["history"][-1]["payload"]["event"] == "complete-abandon-official-pre-t0"
    assert final.to_dict()["history"][-1]["payload"]["in_flight"] == []
    assert all(child.poll() == 0 for child in c.children)


def test_v2_native_source_capture_active_evidence_root_accepts_actual_sdk_run_creation(
    v2_native_host,
):
    from apps.experiments_app.evidence import EvidenceStore

    host = v2_native_host
    root = Path(host.profile["host_roots"]["evidence"]["path"])
    preflight = root / "synthetic-application-preflight.json"
    preflight.write_bytes(b'{"synthetic":true}\n')
    preflight.chmod(0o600)
    before = host.module._capture_native_source(preflight)
    nlink = root.stat().st_nlink
    assert not (root / "runs").exists()
    store = EvidenceStore(
        root,
        custody_hmac_key=b"synthetic-test-only-lease-key-0000",
        publication_verification_key=b"v" * 32,
        rolling_checksums=False,
    )
    store.initialize_run("s11-11111111-2222-4444-8888-555555555555")
    assert root.stat().st_nlink == nlink + 1
    after = host.module._capture_native_source(preflight)
    assert after[0] == before[0] and after[1][1] == before[1][1]
    assert (
        after == before
    ), "SDK writes to an active evidence root must retain the original PF lease"


@pytest.mark.parametrize(
    "fault",
    [
        "ancestor-swap",
        "ancestor-mode",
        "same-bytes-file-swap",
        "file-content",
        "file-hardlink",
        "file-symlink",
        "ancestor-symlink",
        "file-fifo",
    ],
)
def test_v2_native_source_capture_retains_os_and_file_security_boundaries(
    v2_source_checkout, fault
):
    _, _, module = v2_source_checkout
    parent = module.Path(module.__file__).parents[2].parent / "capture-evidence"
    parent.mkdir(mode=0o700)
    inputs = parent / "inputs"
    inputs.mkdir(mode=0o700)
    source = inputs / "preflight.json"
    raw = b'{"synthetic":true}\n'
    source.write_bytes(raw)
    source.chmod(0o600)
    before = module._capture_native_source(source)
    if fault == "ancestor-swap":
        retained = parent.with_name("retained-evidence")
        parent.rename(retained)
        parent.mkdir(mode=0o700)
        (retained / "inputs").rename(inputs)
    elif fault == "ancestor-mode":
        parent.chmod(0o750)
    elif fault == "same-bytes-file-swap":
        source.rename(source.with_suffix(".retained"))
        source.write_bytes(raw)
        source.chmod(0o600)
    elif fault == "file-content":
        source.write_bytes(b'{"synthetic":false}\n')
    elif fault == "file-hardlink":
        os.link(source, source.with_suffix(".hardlink"))
    elif fault == "file-symlink":
        retained = source.with_suffix(".retained")
        source.rename(retained)
        source.symlink_to(retained)
    elif fault == "ancestor-symlink":
        retained = parent.with_name("retained-evidence")
        parent.rename(retained)
        parent.symlink_to(retained, target_is_directory=True)
    else:
        source.unlink()
        os.mkfifo(source, mode=0o600)
    if fault in {"file-hardlink", "file-symlink", "ancestor-symlink", "file-fifo"}:
        with pytest.raises(ValueError, match="native-source-integrity"):
            module._capture_native_source(source)
    else:
        after = module._capture_native_source(source)
        if fault in {"ancestor-swap", "ancestor-mode"}:
            assert before[0] == after[0] and before[1][1] == after[1][1]
        assert after != before, "Original retained bytes/OS identities must detect substitution"


@pytest.mark.parametrize("state", ["clean", "dirty"])
def test_v2_fixed_source_qualifier_releases_only_owned_ephemeral_modules(v2_source_checkout, state):
    from dataclasses import asdict

    repo, expected, module = v2_source_checkout
    module.base.bind(repo, expected)
    bound = module.base._qualified_module
    assert sys.modules[bound.__name__] is bound
    before = {name: value for name, value in sys.modules.items() if name.startswith("_s11_base_")}
    if state == "dirty":
        (repo / "synthetic-untracked-marker").write_bytes(b"LOCAL TEST dirty source boundary\n")
    for _ in range(3):
        if state == "dirty":
            with pytest.raises(module.LauncherError, match="^working-tree-dirty$"):
                module._fixed_verify_repository(repo, expected)
        else:
            facts = module._fixed_verify_repository(repo, expected)
            assert facts.root == str(repo) and facts.head == expected
            assert facts.to_json()["head"] == expected and asdict(facts)["head"] == expected
    after = {name: value for name, value in sys.modules.items() if name.startswith("_s11_base_")}
    assert after == before, "Repeated source guards must release their own ephemeral modules"
    assert sys.modules[bound.__name__] is module.base._qualified_module is bound
    runner = module.DockerRunner(repo, {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"})
    assert runner._module is bound and type(runner._runner) is bound.DockerRunner


def _terminal_fixed_policy(case):
    helper = sys.modules[__name__]
    op = case.x.fixture.module.op
    old = case.source.policy
    journal = case.source.journal
    raw_remote = journal.transport
    assert old._resource_plan is None
    journal.transport = helper.official.NativeTerminalDispatchTransport(raw_remote)
    plan = op.NativePolicyResourcePlan.from_journal(policy=old, journal=journal)
    authorization = old._authorization_guard()
    policy = op.NativeHostPolicy(
        registry=old.registry,
        ledger_store=old.ledger,
        ledger_name=old.name,
        ledger_identity=old.identity,
        session_qualifier=old.sessions,
        approved_root_binding_sha256=old.root_binding,
        approval_verifier=old.approve,
        envelope_verifier=old.envelope,
        required_preflight_checks=old.checks,
        authorization_binding=(
            None if authorization is None else authorization.bindings["authorization"]
        ),
        resource_plan=plan,
    )
    admitted = case.signer.admitted
    assert policy.reference == old.reference
    assert policy.operator_authorization_reference == old.operator_authorization_reference
    assert policy.facts(native=admitted, journal=journal) == old.facts(
        native=admitted, journal=journal
    )
    policy.qualify(native=admitted, journal=journal)
    plan.matches(policy=policy, journal=journal)
    case.source.policy = policy
    return helper, op, journal, raw_remote, plan


def _terminal_fixed_publisher(helper, case):
    return helper.official.NativeLifecycleTerminalPublisher(
        broker=case.single.broker,
        reader=case.reader,
        approve_terminal=case.approve,
        verify_worker=case.worker,
    )


def test_v2_terminal_fixed_router_binds_only_actual_owner_and_retires(v2_lifecycle_terminal):
    case = v2_lifecycle_terminal
    helper, op, journal, remote, plan = _terminal_fixed_policy(case)
    router = helper.official.NativeTerminalLifecycleSource()
    case.source.lifecycle = router
    first, other = _terminal_fixed_publisher(helper, case), _terminal_fixed_publisher(helper, case)
    original_transport = journal.transport
    before_b3 = case.source.publisher.transport.calls.count("B3_CAS")
    before_b4 = remote.calls.count("RETIRE_TERMINAL")
    router.bind_once(first)
    assert router.is_bound_to(first) and not router.is_bound_to(other)
    with pytest.raises(helper.official.LauncherError, match="native-terminal-lifecycle-binding"):
        router.bind_once(other)
    with pytest.raises(helper.official.LauncherError):
        other.retire()
    assert case.source.publisher.transport.calls.count("B3_CAS") == before_b3
    assert remote.calls.count("RETIRE_TERMINAL") == before_b4
    snapshot, retired = first.retire()
    assert op.history.find_run(snapshot, case.result["run_id"])["lifecycle_state"] == "TERMINAL"
    assert retired["terminal_witness_reference"] is not None
    assert journal.read()["active"] is None
    assert journal.transport is original_transport and case.source.lifecycle is router
    plan.matches(policy=case.source.policy, journal=journal)
    assert case.source.publisher.transport.calls.count("B3_CAS") == before_b3 + 1
    assert remote.calls.count("RETIRE_TERMINAL") == before_b4 + 1
    with pytest.raises(helper.official.LauncherError, match="native-terminal-publication-consumed"):
        first.retire()
    assert remote.calls.count("RETIRE_TERMINAL") == before_b4 + 1


@pytest.mark.parametrize("fault", ["last-writer-b3-utc", "last-head-mono", "last-head-approval"])
def test_v2_terminal_fixed_dispatch_denies_invalidated_original_admission(
    v2_lifecycle_terminal, monkeypatch, fault
):
    case = v2_lifecycle_terminal
    helper, op, journal, remote, plan = _terminal_fixed_policy(case)
    terminal = _terminal_fixed_publisher(helper, case)
    case.source.lifecycle = terminal
    original_transport = journal.transport
    b3 = case.source.publisher.transport
    before_b3, before_b4 = b3.calls.count("B3_CAS"), remote.calls.count("RETIRE_TERMINAL")
    reached = []
    original_clock = op.history._now
    if fault == "last-writer-b3-utc":
        actual = op.coordination.HeldWriter.check

        def checked(writer):
            result = actual(writer)
            frame = sys._getframe(1)
            try:
                parent = frame.f_back
                at_dispatch = (
                    frame.f_code.co_qualname.endswith(
                        "NativeTerminalDispatchTransport.__init__." "<locals>.arm.<locals>.check"
                    )
                    and parent is not None
                    and parent.f_code.co_qualname.endswith(
                        "NativeTerminalDispatchTransport.__init__.<locals>.transition"
                    )
                )
            finally:
                del frame
            if at_dispatch and not reached:
                reached.append(True)
                expiry = max(
                    op.sql.utc_time(terminal.original.expires_at),
                    op.sql.utc_time(terminal.published.expires_at),
                ) + timedelta(seconds=1)
                stamp = op.coordination._utc(expiry)
                monkeypatch.setattr(op.history, "_now", lambda: stamp)
            return result

        monkeypatch.setattr(op.coordination.HeldWriter, "check", checked)
    else:
        actual = journal.fresh_head

        def observed(state):
            result = actual(state)
            if terminal.published is not None and not reached:
                reached.append(True)
                if fault == "last-head-mono":
                    monkeypatch.setattr(time, "monotonic", lambda: terminal.reader.deadline + 1)
                else:
                    case.observations["approval"] = False
            return result

        monkeypatch.setattr(journal, "fresh_head", observed)
    with pytest.raises(helper.official.LauncherError):
        terminal.retire()
    assert reached == [True], "Actual final IO injection was not reached"
    assert b3.calls.count("B3_CAS") == before_b3 + 1
    assert remote.calls.count("RETIRE_TERMINAL") == before_b4
    assert journal.transport is original_transport
    with pytest.raises(helper.official.LauncherError, match="native-terminal-publication-consumed"):
        terminal.retire()
    assert remote.calls.count("RETIRE_TERMINAL") == before_b4
    # Only restore the injected UTC observation for passive local-state checks.
    # No operational retry or new approval is performed.
    if fault == "last-writer-b3-utc":
        monkeypatch.setattr(op.history, "_now", original_clock)
    if fault == "last-head-approval":
        case.observations["approval"] = True
    assert journal._local_state()["active"] is not None
    plan.matches(policy=case.source.policy, journal=journal)


def test_v2_terminal_fixed_dispatch_reply_loss_never_resends_or_releases(
    v2_lifecycle_terminal, monkeypatch
):
    case = v2_lifecycle_terminal
    raw_remote = case.source.journal.transport
    actual = raw_remote.transition
    sent = []

    def lose_reply(**kwargs):
        result = actual(**kwargs)
        if kwargs["transition"] == "RETIRE_TERMINAL":
            sent.append(dict(kwargs))
            raise OSError("synthetic lost retirement reply")
        return result

    monkeypatch.setattr(raw_remote, "transition", lose_reply)
    helper, op, journal, remote, plan = _terminal_fixed_policy(case)
    terminal = _terminal_fixed_publisher(helper, case)
    case.source.lifecycle = terminal
    original_transport = journal.transport
    before_b4 = remote.calls.count("RETIRE_TERMINAL")
    with pytest.raises(helper.official.LauncherError):
        terminal.retire()
    assert len(sent) == 1 and remote.calls.count("RETIRE_TERMINAL") == before_b4 + 1
    state = journal._local_state()
    reservation = op.sql.parse_canonical(terminal.record)["reservation_id"]
    assert state["active"] == reservation
    assert state["latest"][reservation]["state"] == "COMMITTED"
    assert terminal.retire_id in state["actions"]
    with pytest.raises(helper.official.LauncherError, match="native-terminal-publication-consumed"):
        terminal.retire()
    with pytest.raises(helper.official.LauncherError, match="native-terminal-retirement-operation"):
        original_transport.transition(**sent[0])
    assert remote.calls.count("RETIRE_TERMINAL") == before_b4 + 1
    assert (
        journal.transport is original_transport and journal._local_state()["active"] == reservation
    )
    plan.matches(policy=case.source.policy, journal=journal)


@pytest.mark.parametrize("action", sorted(official.ACTIONS))
@pytest.mark.parametrize("bootstrap_kind", ["mapping", "foreign-object"])
def test_native_factory_all11_foreign_installation_denies_before_provider_or_effect(
    native_factory_gate_case, action, bootstrap_kind
):
    case = native_factory_gate_case
    bootstrap = (
        {"action_factory": case.factory, "approved": True}
        if bootstrap_kind == "mapping"
        else object()
    )
    invocation = official.Invocation(
        action=action,
        expected_git_sha="a" * 40,
        repo_root=Path("/unread-synthetic-checkout"),
        session_root=Path("/unread-synthetic-session"),
        environ={"S11_ACTION_FACTORY": "untrusted-request-selector"},
        confirmed=True,
        native_bootstrap=bootstrap,
    )
    with pytest.raises(official.LauncherError, match="^native-factory-installation-required$"):
        case.factory.execute(invocation)
    assert case.attempted == []
    assert case.factory._attempted == [invocation]
    assert not case.factory._lock.locked()


@pytest.fixture
def native_factory_execution_chain_case(native_factory_resource_case, request, monkeypatch):
    """Full installed host factory, actual protocol/custody/GOV/B3/B4/B2.

    External approval, Docker observations, PF probes and PID1 admission are
    synthetic. An actual owned Popen relays private pipes to an actual executor
    context in an owned thread. This is mechanical integration, not real Docker
    deployment, OS PID1 qualification, official START or a performance result.
    """
    import ast
    import contextlib
    import copy
    import subprocess
    import threading
    import time
    import traceback
    from dataclasses import replace
    from uuid import uuid4

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_resource_case
    h, mod, boot = c.h, c.mod, c.boot
    app = boot.apparatus
    op, b5, codec = (
        app["ex10_operational_context"],
        app["ex10_deployment_envelope"],
        app["ex10_session_v2"],
    )
    executor = helper.executor
    science = helper.make_harness(request.node.name + "-owned-sut", helper.ex10.load_dataset())
    request.addfinalizer(lambda: helper.close_harness(science))
    # Existing scientific test providers use an owned loopback HTTP backend and
    # the unchanged dataset/FakeAI/protocol. This is never a deployment result.
    sql, durable = op.sql, op.durable
    owned = contextlib.ExitStack()
    request.addfinalizer(owned.close)
    children, threads, errors, results, phases = [], [], [], [], []
    captured = {}
    action = "start"
    launcher_action = "start-official"
    action_results = []
    original_qualify_open = op.NativeHostSources.qualify_open

    def capture_original_governance(source, **facts):
        result = original_qualify_open(source, **facts)
        assert len(source.catalog) == 1
        captured.update(source.catalog)
        return result

    monkeypatch.setattr(op.NativeHostSources, "qualify_open", capture_original_governance)
    stop = threading.Event()
    actor_done = threading.Event()
    publications_ready = threading.Event()
    preflight_ready = threading.Event()
    real_deadline = time.perf_counter() + 21200
    state = {
        "control": None,
        "cfg": None,
        "plan": None,
        "created": False,
        "running": False,
        "effects": [],
        "prepared": None,
        "context": None,
        "store": None,
        "steps": None,
        "completion": [],
    }

    def identity(path):
        s = path.stat()
        return durable.FileIdentity(s.st_dev, s.st_ino, s.st_uid, stat.S_IMODE(s.st_mode))

    stores = {}

    def retain(path):
        path = Path(path)
        key = str(path)
        if key not in stores:
            value = durable.PrivateStore(key, identity(path))
            stores[key] = value
            owned.callback(value.close)
        return stores[key]

    def directory(name):
        path = h.home / name
        path.mkdir(mode=0o700)
        return retain(path)

    def binding(name, path, raw, schema, *, create=True):
        path = Path(path)
        store = retain(path.parent)
        if create:
            store.create_exact(str(path), raw)
        else:
            assert store.read(str(path)) == raw
        ref = {
            "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "schema_version": schema,
        }
        return op.HostArtifactBinding(name, sql.canonical_bytes(ref), store)

    def new_binding(name, value, schema="s11-ex10-synthetic-full-host-v1"):
        return binding(
            name, h.home / ("full-" + name + ".json"), sql.canonical_bytes(value), schema
        )

    def reference(b):
        return sql.parse_canonical(b.reference)

    evidence = retain(Path(h.profile["host_roots"]["evidence"]["path"]))
    certificate_store = directory("full-certificates")
    candidate_store = directory("full-b3-candidates")
    monkeypatch.setattr(
        h.authority,
        "read_payload",
        _native_fixture_candidate_payload_reader(h.authority, candidate_store, sql),
    )
    b3_floor_store = directory("full-b3-floor")
    b3_floor_store.create_exact(
        b3_floor_store.root + "/floor-00000000000000000000.json",
        sql.canonical_bytes(h.authority.receipt),
    )
    b3_floor = durable.DurableFloor(
        b3_floor_store,
        [h.authority.pin],
        sql.digest(h.authority.receipt),
        h.authority.receipt["genesis"],
    )
    authorization = new_binding("authorization", {"approved": "synthetic-independent-A"})
    policy_store = directory("full-policy")
    policy_schema = "s11-ex10-synthetic-full-policy-v1"
    root_binding = hashlib.sha256(
        sql.canonical_bytes(c.initial.header["path_identity_binding"])
    ).hexdigest()
    host_bindings = {
        name: new_binding(name, {"synthetic_approval": name})
        for name in (
            "host_identity",
            "bridge_approval",
            "witness_transport",
            "writer_closure",
            "recovery_policy",
        )
    }
    host_bindings["coordinator_registry"] = binding(
        "coordinator_registry",
        h.home / "full-coordinator-registry.json",
        c.resource["registry_binding"],
        "s11-ex10-synthetic-coordinator-reference-v1",
    )
    base_receipt = binding(
        "base_receipt",
        h.home / "full-base-receipt.json",
        sql.canonical_bytes(h.authority.receipt),
        h.authority.receipt["witness_schema_version"],
    )
    inventory = next(
        ast.literal_eval(n.value)
        for n in ast.parse(
            (boot.repo_root / "backend/apps/experiments_app/ex10_preflight.py").read_bytes()
        ).body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "RUNNING_SOURCES" for t in n.targets)
    )
    sources = {
        p: hashlib.sha256((boot.repo_root / p).read_bytes()).hexdigest()
        for p in (*inventory, "backend/apps/experiments_app/ex10_prepare.py")
    }
    required = frozenset(n["check"] for n in helper.preflight_report()["checks"])
    holder_key = Ed25519PrivateKey.generate()
    cid = hashlib.sha256(b"full-factory-oneoff").hexdigest()
    image = "sha256:" + hashlib.sha256(b"full-factory-image").hexdigest()
    network = hashlib.sha256(b"full-factory-network").hexdigest()
    key = c.factory.custody_key
    fingerprint = executor.executor_fingerprint()
    assert (
        fingerprint
        == hashlib.sha256(
            sql.canonical_bytes({p: sources[p] for p in executor.EXECUTOR_SOURCES})
        ).hexdigest()
    )
    transport = c.full_transport

    def wait_for(predicate):
        while not predicate():
            if stop.is_set() or time.perf_counter() >= real_deadline:
                raise AssertionError("owned full factory fixture watchdog")
            time.sleep(0.02)

    def approved(**facts):
        if "profile" in facts:
            assert facts["profile"].to_dict() == h.profile
        if "header" in facts:
            assert facts["header"] == c.initial.header

    evidence_path = Path(evidence.root)
    exchange = str(uuid4())
    staging = "application-preflight." + str(uuid4()) + ".json"
    initial_snapshot = boot.read_authority(h.authority.descriptor_ref)
    acknowledgments = []
    for run in op.history.list_official_runs(initial_snapshot):
        target = op.history.verify_ack_target(initial_snapshot, run["run_id"])
        if target["recognizable"]:
            acknowledgments.append(
                {
                    "run_id": run["run_id"],
                    "authority_revision": target["revision"],
                    "terminal_evidence_reference": target["evidence_reference"],
                }
            )
    acknowledgments.sort(key=lambda item: item["run_id"])
    acknowledged = tuple(item["run_id"] for item in acknowledgments)

    def prepare_plan(**facts):
        current_session = facts["session"]
        assert current_session.header == c.initial.header
        state["session"] = current_session
        revision = op.history.current_revision(boot.read_authority(h.authority.descriptor_ref))

        def pin(p):
            return {
                "key_id": p.key_id,
                "key_epoch": p.key_epoch,
                "public_key_hex": p.public_key.public_bytes_raw().hex(),
            }

        cfg = {
            "action": action,
            "exchange_id": exchange,
            "profile_sha256": hashlib.sha256(boot.profile_path.read_bytes()).hexdigest(),
            "header_sha256": c.initial.to_dict()["header_sha256"],
            "bridge_pin": pin(c.resource["bridge_pin"]),
            "witness_pin": pin(c.resource["floor"].pins[0]),
            "b3_pins": [h.authority.pin.record],
            "genesis_digest": sql.digest(h.authority.receipt),
            "genesis_binding": h.authority.receipt["genesis"],
            "authority_revision": revision["revision"],
            "request_payload": {
                "expected_head": {
                    **{
                        k: revision[k]
                        for k in ("authority_id", "revision", "ledger_digest", "witness_digest")
                    },
                    "authority_epoch": revision["epoch"],
                    "receipt_reference": reference(base_receipt),
                },
                "acknowledgments": acknowledgments,
                "deployment_verification_reference": h.profile["deployment_verification_reference"],
                "descriptor_reference": h.authority.descriptor_ref,
                "profile_reference": {
                    "path": str(boot.profile_path),
                    "sha256": hashlib.sha256(boot.profile_path.read_bytes()).hexdigest(),
                    "schema_version": h.profile["schema_version"],
                },
                "session_reference": {
                    "path": str(h.home / "full-header.json"),
                    "sha256": c.initial.to_dict()["header_sha256"],
                    "schema_version": "s11-ex10-session-v2-header-projection-v1",
                },
            },
            "expected_reservation": state.get("original_reservation"),
            "host_root_binding_sha256": root_binding,
            "source_hashes": sources,
            "staging_name": staging,
            "command_options": {
                "expected_git_sha": boot.expected_git_sha,
                "source_root": "/s11-full-source",
                "nonce": "d" * 64,
                "acknowledge_not_demonstrated": list(acknowledged),
            },
            "authorization_reference": reference(authorization),
        }
        plan = {
            "name": "s11-synthetic-full-" + exchange,
            "image_digest": image,
            "network_id": network,
            "bootstrap_bytes": sql.canonical_bytes(cfg),
            "service_environment": {"DJANGO_SETTINGS_MODULE": "config.settings.test"},
            "owner_labels": {"s11.owner.session": c.initial.header["session_id"]},
        }
        if action == "start":
            assert facts["invocation"].preflight_report == evidence_path / staging
        else:
            record = state["original_reservation"]
            cfg["request_payload"] = {
                "reservation_id": record["reservation_id"],
                "original_request_id": record["request_id"],
            }
            cfg["staging_name"] = None
            cfg["command_options"] = {
                "expected_git_sha": boot.expected_git_sha,
                "run_id": record["run_id"],
            }
            if action == "finalize":
                cfg["command_options"].update(
                    abandon_reason=(
                        mod.ABANDON_REASON if launcher_action == "abandon-official-pre-t0" else None
                    ),
                    declare_executor_gone=False,
                )
            plan["bootstrap_bytes"] = sql.canonical_bytes(cfg)
        # Schema-only check before expensive host/OS preparation. This dummy
        # envelope reference is never passed to the factory or actual exchange;
        # the SDK binds the actual B5 bytes after their real publication.
        if action == "start":
            shape = {
                **cfg["request_payload"],
                "operational_envelope_reference": {
                    "path": str(evidence_path / ".s11-operational" / exchange / "envelope.json"),
                    "sha256": "0" * 64,
                    "schema_version": b5.SCHEMA_VERSION,
                },
                "preflight_reference": {
                    "path": str(evidence_path / staging),
                    "sha256": "0" * 64,
                    "schema_version": executor.ex10_preflight.SCHEMA_VERSION,
                },
                "run_identity": executor.propose_run(
                    started_at=c.now["utc"], git_sha=boot.expected_git_sha, nonce="d" * 64
                ),
            }
            executor.wire._validate(shape, executor.wire._SCHEMA_034)
        else:
            assert executor._native_initial_payload(cfg) == cfg["request_payload"]
        state.update(cfg=cfg, plan=plan)
        return plan

    class DockerBoundary:
        def __init__(self, cwd, environment):
            assert cwd == boot.repo_root
            self.environment = dict(environment)

        def run(self, argv, *, timeout):
            assert argv[:2] == ("/usr/bin/docker", "container") and timeout > 0
            action = argv[2]
            if action == "create":
                assert not state["created"]
                state["created"] = True
                state["effects"].append("create")
                environment = {}
                labels = {}
                mounts = []
                args = list(argv)
                for index, value in enumerate(args):
                    if value == "--env":
                        name = args[index + 1]
                        assert "=" not in name and name in self.environment
                        environment[name] = self.environment[name]
                    elif value == "--label":
                        k, v = args[index + 1].split("=", 1)
                        labels[k] = v
                    elif value == "--mount":
                        parts = dict(
                            x.split("=", 1) if "=" in x else (x, True)
                            for x in args[index + 1].split(",")
                        )
                        mounts.append(
                            {
                                "Type": parts["type"],
                                "Source": parts["source"],
                                "Destination": parts["target"],
                                "RW": not parts.get("readonly", False),
                            }
                        )
                state.update(environment=environment, labels=labels, mounts=mounts)
                return mod.Completed(0, (cid + "\n").encode(), b"")
            if action == "start":
                assert (
                    argv == ("/usr/bin/docker", "container", "start", cid)
                    and state["created"]
                    and not state["running"]
                )
                state["running"] = True
                state["effects"].append("start")
                return mod.Completed(0, (cid + "\n").encode(), b"")
            assert action == "inspect" and argv[-1] == cid
            running = state["running"] and not state.get("worker_retired", False)
            value = {
                "Id": cid,
                "Name": "/" + state["plan"]["name"],
                "Image": image,
                "State": {
                    "Status": "running"
                    if running
                    else ("exited" if state["running"] else "created"),
                    "Running": running,
                    "Restarting": False,
                    "Pid": 2345 if running else 0,
                    "ExitCode": state.get("refusal_relay_observed_exit", 1 if errors else 0),
                    "OOMKilled": False,
                },
                "Config": {
                    "User": f"{os.getuid()}:{os.getgid()}",
                    "Labels": state["labels"],
                    "Env": [k + "=" + v for k, v in state["environment"].items()],
                    "Entrypoint": ["/usr/local/bin/python"],
                    "Cmd": ["-B", "/app/manage.py", "s11_ex10", "bridge"],
                    "WorkingDir": "/s11-full-source/backend",
                    "Healthcheck": {"Test": ["NONE"]},
                },
                "HostConfig": {
                    "ReadonlyRootfs": True,
                    "Privileged": False,
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges"],
                    "RestartPolicy": {"Name": "no"},
                    "PortBindings": {},
                    "PublishAllPorts": False,
                    "Binds": None,
                    "VolumesFrom": None,
                    "Devices": None,
                    "DeviceRequests": None,
                    "PidMode": "",
                    "UsernsMode": "",
                },
                "Mounts": state["mounts"],
                "NetworkSettings": {"Networks": {"synthetic-full-network": {"NetworkID": network}}},
            }
            return mod.Completed(0, sql.canonical_bytes([value]), b"")

    def prepare_envelope(**facts):
        state["control"] = facts["control"]
        phases.append("prepare_envelope")
        if action != "start":
            assert facts["pending"].header == c.initial.header
            return {
                "configuration": state["start_envelope_configuration"],
                "attached_bytes": state["start_envelope_bytes"],
            }
        wait_for(lambda: preflight_ready.is_set() or bool(errors))
        assert not errors, errors
        cfg = state["cfg"]
        control = facts["control"]
        pending = facts["pending"]
        profile_ref = {
            "path": str(boot.profile_path),
            "sha256": cfg["profile_sha256"],
            "schema_version": app["ex10_runtime_profile"].SCHEMA_VERSION,
        }
        header_ref = reference(
            binding(
                "header",
                h.home / "full-header.json",
                codec.canonical_session_header_bytes(pending.header),
                "s11-ex10-session-v2-header-projection-v1",
            )
        )
        pf_raw = evidence.read(str(evidence_path / staging))
        pf = sql.parse_canonical(pf_raw)
        pf_ref = {
            "path": str(evidence_path / staging),
            "sha256": hashlib.sha256(pf_raw).hexdigest(),
            "schema_version": pf["schema_version"],
        }
        head_ref = reference(
            binding(
                "authority_view",
                h.home / "full-authority-view.json",
                boot.read_authority(h.authority.descriptor_ref).canonical_current_head,
                "s11-ex10-synthetic-head-reference-v1",
            )
        )
        ready_ref = reference(new_binding("ready", {"synthetic_zero_reports": True}))
        containers, mounts = [], []
        compose = {"name": h.profile["project"], "services": {}}
        for service in h.profile["expected_services"]:
            sut = hashlib.sha256(("sut-full-" + service).encode()).hexdigest()
            item = {
                "container_id": sut,
                "service": service,
                "role": service,
                "image_digest": "sha256:" + hashlib.sha256(service.encode()).hexdigest(),
                "running": True,
                "mounts": [],
            }
            if service in ("backend", "worker"):
                for source, target, readonly in control.mounts:
                    m = {
                        "container_id": sut,
                        "type": "bind",
                        "source_canonical": source,
                        "destination": target,
                        "read_only": readonly,
                        "ownership_reference": h.profile["deployment_verification_reference"],
                    }
                    item["mounts"].append(m)
                    mounts.append(m)
            containers.append(item)
            compose["services"][service] = {
                "image": service + ":synthetic",
                "volumes": [
                    {
                        "type": m["type"],
                        "source": m["source_canonical"],
                        "target": m["destination"],
                        "read_only": m["read_only"],
                    }
                    for m in item["mounts"]
                ],
            }

        def running_source(container_id, relative_path):
            assert (
                container_id in {i["container_id"] for i in containers}
                and relative_path in inventory
            )
            return (boot.repo_root / relative_path).read_bytes()

        configuration = {
            "profile_reference": profile_ref,
            "header_reference": header_ref,
            "application_preflight_reference": pf_ref,
            "historical_authority_view_reference": head_ref,
            "path_context": {
                **json.loads(boot.path_context_bytes),
                "running_sources": list(sources),
            },
            "running_sources": {p: sources[p] for p in inventory},
            "host_operational_sources": {
                "backend/apps/experiments_app/ex10_prepare.py": sources[
                    "backend/apps/experiments_app/ex10_prepare.py"
                ]
            },
            "container_identities": [{k: i[k] for k in b5.CONTAINER_FIELDS} for i in containers],
            "mounts": mounts,
            "compose_projection": b5.sanitized_compose_projection(compose),
            "ready_state": {
                "report_reference": ready_ref,
                "checks": [{"check": "zero_reports", "result": "PASS"}],
            },
            "application_required_checks": required,
            "compose_observer": lambda: copy.deepcopy(compose),
            "container_observer": lambda: copy.deepcopy(containers),
            "running_source_reader": running_source,
            "bootstrap_verifier": approved,
            "source_verifier": approved,
            "session_verifier": approved,
            "authority_verifier": approved,
            "ready_verifier": approved,
            "clock": lambda: c.now["utc"],
        }
        state["envelope_configuration"] = configuration
        return {"configuration": configuration, "attached_bytes": None}

    def prepare_bridge(**facts):
        phases.append("prepare_bridge")
        prepared = facts["prepared"]
        pending = facts["pending"]
        publication = facts["publication"]
        state["prepared"] = prepared
        profile_binding = binding(
            "profile",
            boot.profile_path,
            boot.profile_path.read_bytes(),
            h.profile["schema_version"],
            create=False,
        )
        assert (h.root / mod.SESSION_FILE).read_bytes() == pending._bytes
        session_binding = binding(
            "session",
            h.home / ("full-approved-pending-session." + exchange + ".json"),
            pending._bytes,
            codec.SESSION_V2_SCHEMA,
        )
        header_binding = binding(
            "header",
            h.home / "full-header.json",
            codec.canonical_session_header_bytes(pending.header),
            "s11-ex10-session-v2-header-projection-v1",
            create=False,
        )
        session_bindings = [profile_binding, session_binding, header_binding]
        for name in app["ex10_runtime_profile"].REFERENCE_FIELDS:
            ref = h.profile[name]
            session_bindings.append(
                binding(
                    name,
                    Path(ref["path"]),
                    Path(ref["path"]).read_bytes(),
                    ref["schema_version"],
                    create=False,
                )
            )
        ref = pending.header["runtime_profile_approval_reference"]
        session_bindings.append(
            binding(
                "runtime_profile_approval_reference",
                Path(ref["path"]),
                Path(ref["path"]).read_bytes(),
                ref["schema_version"],
                create=False,
            )
        )
        envelope_binding = binding(
            "envelope",
            Path(publication.store.root) / "envelope.json",
            prepared.envelope_bytes,
            b5.SCHEMA_VERSION,
            create=False,
        )
        pf_ref = state["envelope_configuration"]["application_preflight_reference"]
        preflight_binding = binding(
            "preflight",
            Path(pf_ref["path"]),
            Path(pf_ref["path"]).read_bytes(),
            pf_ref["schema_version"],
            create=False,
        )
        descriptor = h.authority.descriptor_ref
        facts15 = {
            "namespace": "S11C3-EX10",
            "authority_id": c.resource["authority_id"],
            "authority_epoch": 1,
            "descriptor_reference": descriptor,
            "approved_host_identity_reference": reference(host_bindings["host_identity"]),
            "coordinator_registry_reference": reference(host_bindings["coordinator_registry"]),
            "lock_file_identity": op._file_identity(c.resource["lock_identity"]),
            "sidecar_identity": op._file_identity(c.resource["sidecar_identity"]),
            "ledger_identity": op._file_identity(c.resource["ledger_identity"]),
            "bridge_key_public_approval_reference": reference(host_bindings["bridge_approval"]),
            "independent_witness_transport_reference": reference(
                host_bindings["witness_transport"]
            ),
            "writer_closure_reference": reference(host_bindings["writer_closure"]),
            "approved_executor_entrypoint_identity": {
                "container_id": cid,
                "identity_digest": prepared.process.identity_digest,
                "argv": ["/usr/local/bin/python", "-B", "/app/manage.py", "s11_ex10", "bridge"],
            },
            "normal_operation_allowlist": sorted(op.NORMAL_OPERATIONS),
            "recovery_operator_approval_policy": reference(host_bindings["recovery_policy"]),
        }
        policy_binding = binding(
            "policy",
            Path(policy_store.root) / ("policy." + exchange + ".json"),
            sql.canonical_bytes(facts15),
            policy_schema,
        )

        def resolve(**kw):
            assert kw["canonical_facts"] == sql.canonical_bytes(facts15)
            return policy_binding

        def policy_approve(**kw):
            assert kw["canonical_authorization"] == sql.canonical_bytes(
                {"approved": "synthetic-independent-A"}
            )
            assert kw["authorization_reference"] == authorization.reference

        def envelope_verify(**kw):
            assert kw["canonical_envelope"] == prepared.envelope_bytes
            assert kw["reference"] == envelope_binding.reference
            assert (
                envelope_binding.store.read(reference(envelope_binding)["path"])
                == prepared.envelope_bytes
            )
            # This provider approves immutable B5 bytes. Factory-owned live and
            # completed process observations remain mandatory in product code.

        def session_approve(**kw):
            assert kw["profile"].to_dict() == h.profile
            assert kw["session"].header == pending.header

        session_cfg = {
            "path_context": sql.canonical_bytes(json.loads(boot.path_context_bytes)),
            "source_revision": boot.expected_git_sha,
            "launcher_blobs": sql.canonical_bytes(pending.header["launcher_blobs"]),
            "expected_tip": pending._expected_tip,
            "expected_count": pending._expected_count,
            "process": prepared.process,
            "approval_verifier": session_approve,
            "deployment_observer": lambda **kw: prepared.process,
        }
        policy_cfg = {
            "bindings": tuple(host_bindings.values()),
            "authorization_binding": authorization,
            "policy_store": policy_store,
            "policy_name": "policy." + exchange + ".json",
            "policy_schema_version": policy_schema,
            "approved_root_binding_sha256": root_binding,
            "approval_verifier": policy_approve,
            "envelope_verifier": envelope_verify,
            "required_preflight_checks": required,
            "resolve_policy_binding": resolve,
        }
        publisher_cfg = {
            "ledger_store": c.resource["ledger_store"],
            "ledger_name": c.resource["ledger_name"],
            "ledger_identity": c.resource["ledger_identity"],
            "candidate_store": candidate_store,
            "floor": b3_floor,
            "transport": transport,
            "signer_pin": h.authority.pin,
            "clock": lambda: c.now["utc"],
            "clock_health": lambda: True,
        }
        historical = state.get("historical", op.NativeGovernanceHistory(()))
        source_cfg = {
            "bindings": (
                authorization,
                envelope_binding,
                preflight_binding,
                base_receipt,
            ),
            "authority_reader": lambda: boot.read_authority(descriptor),
            "historical_contexts": lambda **kw: historical,
            "artifact_store": evidence,
            "certificate_store": certificate_store,
            "executor_fingerprint": fingerprint,
            "live_owner": None,
            "uncertainty_verifier": None,
            "recovery_sources": None,
            "lifecycle_sources": None,
        }
        if launcher_action == "finalize":
            assert action_results[-1]["action"] == "reconcile"
            assert results[-1]["provisional"] is True
            assert results[-1]["decision"] in ("PASS", "FAIL", "NOT_DEMONSTRATED")
        publications_ready.set()
        return {
            "session_bindings": tuple(session_bindings),
            "session_configuration": session_cfg,
            "policy_configuration": policy_cfg,
            "publisher_configuration": publisher_cfg,
            "source_configuration": source_cfg,
            "resource_configuration": dict(c.resource, transport=transport),
            "reconciled": (results[-1]["decision"] if launcher_action == "finalize" else None),
        }

    def verify_local_seal(*, context, store, run_id, result):
        assert action == "finalize" and context is state["context"]
        assert run_id == result["run_id"]
        existing = executor.load_existing_state_readonly(store, run_id)
        assert existing is not None and existing.finalized
        executor.validate_finalized_state(existing.run_root, existing.records, custody_hmac_key=key)
        state["local_seal_checked"] = True

    def historical_original():
        assert len(captured) == 1
        reference_id, original = next(iter(captured.items()))
        sessions = op.NativeSessionQualifier(
            registry=original.sessions.registry,
            path_context=original.sessions.path_context,
            source_revision=original.sessions.source_revision,
            launcher_blobs=original.sessions.launcher_blobs,
            expected_tip=original.sessions.expected_tip,
            expected_count=original.sessions.expected_count,
            process=original.sessions.process,
            approval_verifier=original.sessions.approve,
            deployment_observer=lambda **kw: original.sessions.process,
        )
        original_policy = original.policy
        historical_policy = op.NativeHostPolicy(
            registry=original_policy.registry,
            ledger_store=original_policy.ledger,
            ledger_name=original_policy.name,
            ledger_identity=original_policy.identity,
            session_qualifier=sessions,
            approved_root_binding_sha256=original_policy.root_binding,
            approval_verifier=original_policy.approve,
            envelope_verifier=original_policy.envelope,
            required_preflight_checks=original_policy.checks,
            authorization_binding=authorization,
        )
        assert historical_policy.reference == original_policy.reference
        qualifier = op.NativeGovernanceQualifier(
            session_qualifier=sessions,
            registry=original.registry,
            certificate_store=original.store,
            bridge_pin=original.pin,
            holder_public_key=original.holder,
            reservation_id=original.reservation_id,
            executor_fingerprint=original.fingerprint,
            required_preflight_checks=original.checks,
            protocol=original.protocol,
            methodology=original.methodology,
            host_policy=historical_policy,
        )
        return op.NativeGovernanceHistory(((reference_id, qualifier),))

    def advance_to_action(target):
        nonlocal action, launcher_action, exchange, staging, cid, holder_key, base_receipt
        assert target in {"execute-official", "reconcile", "finalize", "abandon-official-pre-t0"}
        assert len(results) == len(action_results) and state["completion"] == [False, True]
        assert all(child.poll() == 0 for child in children)
        for thread in threads:
            thread.join(timeout=5)
        assert not any(thread.is_alive() for thread in threads)
        original_session = boot.load(invocation)
        if action == "start":
            state["original_reservation"] = state["context"].reservation
            assert state["original_reservation"]["state"] == "COMMITTED"
            state["historical"] = historical_original()
            state["start_envelope_configuration"] = state["envelope_configuration"]
            state["start_envelope_bytes"] = state["prepared"].envelope_bytes
        revision = op.history.current_revision(boot.read_authority(h.authority.descriptor_ref))
        receipt_path = b3_floor._path(revision["revision"])
        receipt_bytes = b3_floor.store.read(receipt_path)
        base_receipt = op.HostArtifactBinding(
            "base_receipt",
            sql.canonical_bytes(
                {
                    "path": receipt_path,
                    "sha256": hashlib.sha256(receipt_bytes).hexdigest(),
                    "schema_version": sql.parse_canonical(receipt_bytes)["witness_schema_version"],
                }
            ),
            b3_floor.store,
        )
        launcher_action = target
        action = mod.NativeOneOffDocker._WORKER_ACTIONS[target]
        exchange = str(uuid4())
        staging = None
        cid = hashlib.sha256(("full-factory-terminal-" + exchange).encode()).hexdigest()
        holder_key = Ed25519PrivateKey.generate()
        actor_done.clear()
        publications_ready.clear()
        preflight_ready.clear()
        state.update(
            control=None,
            cfg=None,
            plan=None,
            created=False,
            running=False,
            prepared=None,
            context=None,
            steps=None,
            completion=[],
            worker_retired=False,
            host_channel=None,
        )
        result = replace(c.invocation(launcher_action, confirmed=True), native_bootstrap=boot)
        assert boot.load(result)._bytes == original_session._bytes
        return result

    # Client setup and real relay are defined below; no assembled core is passed
    # to the installed factory.
    relay_exit = []
    relay_code = r"""
import os, select, sys, time
reader, writer, exit_reader = map(int,sys.argv[1:])
sources={0:writer,reader:1}
deadline=time.monotonic()+21200
while sources and time.monotonic()<deadline:
    ready,_,_=select.select(list(sources),[],[],1)
    for fd in ready:
        raw=os.read(fd,65536)
        target=sources[fd]
        if raw:
            view=memoryview(raw)
            while view:
                n=os.write(target,view)
                view=view[n:]
        else:
            del sources[fd]
            os.close(target)
            if target==1:
                os.close(2)
if sources:
    sys.exit(3)
while time.monotonic()<deadline:
    ready,_,_=select.select([exit_reader],[],[],1)
    if ready:
        raw=os.read(exit_reader,1)
        sys.exit(0 if raw==b'0' else 4)
sys.exit(5)
"""

    def actor(read_fd, write_fd):
        channel = None
        try:
            cfg = state["cfg"]
            store = helper.EvidenceStore(
                evidence_path,
                custody_hmac_key=key,
                publication_verification_key=c.factory.signer.public_key().public_bytes_raw(),
                rolling_checksums=False,
            )
            state["store"] = store
            science.backend.store = store
            probes = replace(
                science.probes,
                preflight=lambda options: helper.preflight_report(
                    git_sha=boot.expected_git_sha, checked_at=c.now["utc"]
                ),
                fingerprint=lambda: fingerprint,
                now=lambda: c.now["utc"],
            )
            if action == "start":
                proposal, raw = executor._prepare_native_start(
                    probes=probes,
                    git_sha=boot.expected_git_sha,
                    source_root=boot.repo_root,
                    nonce="d" * 64,
                )
                evidence.create_exact(str(evidence_path / staging), raw)
                state["start_preflight"] = raw
                preflight_ready.set()
            else:
                raw = state["start_preflight"]
                proposal = state["original_reservation"]["run_identity"]
            wait_for(lambda: publications_ready.is_set())
            proof = executor.ExecutorHostProofs(
                root=str(evidence_path),
                exchange_id=exchange,
                b3_pins=[h.authority.pin.record],
                genesis_digest=sql.digest(h.authority.receipt),
                genesis_binding=h.authority.receipt["genesis"],
                clock=lambda: c.now["utc"],
            )
            process, _ = proof.read("process")
            _, envelope = proof.read("envelope")
            client_profile = executor.profile_codec.parse_runtime_profile_bytes(
                boot.profile_path.read_bytes()
            )
            executor._native_envelope_bindings(
                envelope,
                profile=client_profile,
                header=c.initial.header,
                cfg=cfg,
                preflight=raw,
                now=c.now["utc"],
                fresh_required=action in ("start", "execute"),
            )
            assert process == executor._native_process_identity(
                cfg=cfg,
                profile=client_profile,
                container_id=cid,
                initial=state["plan"]["bootstrap_bytes"],
                envelope=envelope,
            )
            fixed = {
                "exchange_id": exchange,
                "authority_id": c.resource["authority_id"],
                "authority_epoch": 1,
                "authorization_reference": reference(authorization),
                "bridge_key_id": cfg["bridge_pin"]["key_id"],
                "bridge_key_epoch": cfg["bridge_pin"]["key_epoch"],
                "container_identity_digest": process["identity_digest"],
                "deployment_verification_sha256": process["deployment_verification_sha256"],
                "session_id": c.initial.header["session_id"],
                "session_header_sha256": cfg["header_sha256"],
                "runtime_profile_sha256": cfg["profile_sha256"],
                "source_revision": boot.expected_git_sha,
                "project": h.profile["project"],
            }

            def admit(**kw):
                assert kw["context"].owner == (os.getpid(), threading.get_ident())
                assert kw["context"].holder is holder_key and kw["action"] == action
                assert process["container_id"] == cid and state["running"] and not stop.is_set()
                assert boot.profile_path.read_bytes() == client_profile._bytes

            channel = executor.ExecutorBridgeChannel(
                read_fd=read_fd,
                write_fd=write_fd,
                exchange="START" if action == "start" else "SINGLE_RPC",
                exchange_id=exchange,
                budget=120 if action == "start" else 30,
            )
            os.close(read_fd)
            os.close(write_fd)

            def client_current(**facts):
                boundary = action == "execute" and facts.get("phase") == "BARRIER"
                if boundary:
                    assert facts["context"] is state["context"]
                    assert facts["context"].finished and facts["context"].step_authorized
                    assert facts["context"].proposal["run_id"] == facts["run_id"]
                    retained = executor._native_wait_emission(
                        cfg=cfg,
                        proof=proof,
                        deadline=channel.deadline,
                        guard=lambda: admit(context=state["context"], action=action),
                    )
                proof.current(**facts)
                if boundary:
                    retained()
                    admit(context=state["context"], action=action)
                    executor._native_envelope_bindings(
                        envelope,
                        profile=client_profile,
                        header=c.initial.header,
                        cfg=cfg,
                        preflight=raw,
                        now=c.now["utc"],
                        fresh_required=True,
                    )
                    retained()
                    proof.check_current_freshness(
                        context=state["context"], envelope=sql.parse_canonical(envelope)
                    )
                    assert time.monotonic() < channel.deadline
                    state["actual_emission_proof_admitted"] = True

            context = executor.ExecutorOperationalContext(
                action=action,
                channel=channel,
                bridge_pin=executor.wire.PublicKeyPin(
                    c.resource["bridge_pin"].key_id,
                    c.resource["bridge_pin"].key_epoch,
                    c.resource["bridge_pin"].public_key,
                ),
                witness_pin=executor.wire.PublicKeyPin(
                    c.resource["floor"].pins[0].key_id,
                    c.resource["floor"].pins[0].key_epoch,
                    c.resource["floor"].pins[0].public_key,
                ),
                holder_key=holder_key,
                expected_challenge=fixed,
                request_payload=executor._native_prepared_payload(
                    cfg=cfg, profile=client_profile, envelope=envelope
                ),
                evidence_root=str(evidence_path),
                host_evidence_root=str(evidence_path),
                staging_name=staging if action == "start" else None,
                expected_reservation=state.get("original_reservation"),
                authority_revision=cfg["authority_revision"],
                host_root_binding_sha256=root_binding,
                native_admitter=admit,
                current_guard=client_current,
                common_prior_reader=proof.common_prior,
                common_prior_verifier=proof.verify_common,
                commit_verifier=proof.verify_commit,
                terminal_preparer=verify_local_seal,
                clock=lambda: c.now["utc"],
            )
            context.preflight, context.proposal, context.store, context.prepared = (
                raw,
                proposal,
                store,
                True,
            )
            state["context"] = context
            if action == "start":
                result = executor.start_run(
                    store,
                    git_sha=boot.expected_git_sha,
                    source_root=boot.repo_root,
                    nonce="d" * 64,
                    evidence_mode=executor.OFFICIAL_MODE,
                    timezone="UTC",
                    acknowledged_not_demonstrated=acknowledged,
                    probes=probes,
                    operational_context=context,
                )
            else:
                operation = {
                    "execute": executor.execute_run,
                    "reconcile": executor.reconcile_run,
                    "finalize": executor.finalize_run,
                }[action]
                result = operation(
                    store,
                    run_id=proposal["run_id"],
                    git_sha=boot.expected_git_sha,
                    probes=probes,
                    operational_context=context,
                    **(
                        {"abandon_reason": mod.ABANDON_REASON}
                        if launcher_action == "abandon-official-pre-t0"
                        else {}
                    ),
                )
            context.persist_public_result(store, result)
            results.append(result)
            action_results.append(
                {
                    "action": action,
                    "cid": cid,
                    "exchange": exchange,
                    "context": context,
                    "result": result,
                    "steps": channel.position,
                }
            )
            state["steps"] = channel.position
        except BaseException as error:
            errors.append(error)
            if state.get("retire_after_sdk_refusal"):
                assert action == "execute" and isinstance(error, executor.Ex10Refusal)
                assert error.reasons == ("ready-state",) and error.terminal is False
                state["sdk_refusal"] = error
            state["actor_error_trace"] = traceback.format_exception(
                type(error), error, error.__traceback__
            )
        finally:
            if channel is not None:
                channel.close()
            else:
                for fd in (read_fd, write_fd):
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            if state.get("sdk_refusal") is not None:
                assert channel is not None and channel.closed and channel.position == 5
                assert state["context"].finished and state["context"].step_authorized
                assert not state["context"].scientific_finished
                # Notification follows the actual SDK refusal and both real
                # client FD closes. Relay still consumes both EOFs before exit4.
                assert len(relay_exit) == 1
                os.write(relay_exit[0], b"1")
                os.close(relay_exit.pop())
            actor_done.set()

    def launch(argv, **options):
        assert argv == (
            "/usr/bin/docker",
            "exec",
            "--interactive",
            cid,
            "/usr/local/bin/python",
            "-B",
            "/app/manage.py",
            "s11_ex10",
            "bridge",
        )
        private_fds = []
        try:
            client_read, relay_write = os.pipe()
            private_fds.extend((client_read, relay_write))
            relay_read, client_write = os.pipe()
            private_fds.extend((relay_read, client_write))
            exit_read, exit_write = os.pipe()
            private_fds.extend((exit_read, exit_write))
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    relay_code,
                    str(relay_read),
                    str(relay_write),
                    str(exit_read),
                ],
                pass_fds=(relay_read, relay_write, exit_read),
                **options,
            )
            children.append(child)
            for fd in (relay_write, relay_read, exit_read):
                os.close(fd)
                private_fds.remove(fd)
            relay_exit.append(exit_write)
            private_fds.remove(exit_write)
            thread = threading.Thread(target=actor, args=(client_read, client_write), daemon=True)
            threads.append(thread)
            thread.start()
            private_fds.remove(client_read)
            private_fds.remove(client_write)
            return child
        finally:
            for fd in private_fds:
                try:
                    os.close(fd)
                except OSError:
                    pass

    original_begin_exchange = op.NativeHostSources.begin_exchange

    def observe_host_exchange(source, *, channel):
        # Keep every original host admission/guard. This only observes the
        # actual HOST channel that the product installs for this exchange.
        result = original_begin_exchange(source, channel=channel)
        state["host_channel"] = source.channel
        return result

    monkeypatch.setattr(op.NativeHostSources, "begin_exchange", observe_host_exchange)

    class OwnedWorkerDocker(DockerBoundary):
        def run(self, argv, *, timeout):
            # The synthetic PID1 wrapper retires only after actual client work
            # and the host's owned preparation descriptors have been closed.
            publication = state.get("publication")
            if actor_done.is_set() and state.get("sdk_refusal") is not None:
                actual_exit = children[-1].poll()
                if actual_exit is not None:
                    assert actual_exit == 4
                    state["refusal_relay_observed_exit"] = actual_exit
                    state["worker_retired"] = True
                return super().run(argv, timeout=timeout)
            if actor_done.is_set() and publication is not None:
                try:
                    info = os.fstat(publication.store.fd)
                    retired = (info.st_dev, info.st_ino) != state["publication_fd_identity"]
                except (OSError, TypeError):
                    retired = True
                host_channel = state.get("host_channel")
                host_eof = (
                    type(host_channel) is op.PipeFrameChannel
                    and host_channel.role == "HOST"
                    and host_channel.exchange == "SINGLE_RPC"
                    and host_channel.exchange_id == exchange
                    and host_channel.position == len(host_channel.steps) == 5
                    and host_channel.closed
                    and host_channel.read_fd
                    is host_channel.write_fd
                    is host_channel.error_fd
                    is None
                )
                if (
                    (action == "start" and retired)
                    or (action == "execute" and host_eof and retired)
                    or (action not in {"start", "execute"} and host_eof)
                ):
                    # Execute keeps PID1 through emission producer guards and
                    # the actual proof namespace FD close after its return.
                    # Terminal retains the existing actual HOST EOF criterion;
                    # its proof namespace remains until B3/B4 RETIRE.
                    terminal_abort = state.get("hold_terminal_relay_for_public_abort")
                    if terminal_abort is not None:
                        # Hold the genuine owned relay only after actual SDK
                        # FINAL and HOST SINGLE5/EOF, before HOST worker wait
                        # can reach TerminalArtifactReader/B3 RETIRE.
                        assert terminal_abort in {"timeout", "interrupted"}
                        assert action == "finalize" and host_eof and not errors
                        context = state["context"]
                        assert context.finished and context.scientific_finished
                        assert context.step_authorized and context.channel.position == 5
                        assert state["steps"] == 5 and not context.consumed
                        result = action_results[-1]["result"]
                        assert result["decision"] == "PASS" and result["binding"] is True
                        assert (
                            result["lifecycle"] == "FINALIZED_PASS"
                            and result["custody_errors"] == []
                        )
                        assert science.backend.accepted == 50 and children[-1].poll() is None
                        assert len(relay_exit) == 1 and not state.get(
                            "held_terminal_relay_at_actual_wait"
                        )
                        state["held_terminal_relay_at_actual_wait"] = True
                        codec = boot.apparatus["ex10_session_v2"]
                        checkpoint = boot.checkpoint()
                        pending = codec.load_official_session(
                            h.root / mod.SESSION_FILE,
                            context=boot.context(),
                            profile=h.profile,
                            expected_tip=checkpoint[0],
                            expected_count=checkpoint[1],
                        )
                        assert pending.to_dict()["state"]["known_phase"] == "RECONCILING"
                        assert pending.to_dict()["in_flight"][0]["action"] == "finalize"
                        assert transport.calls.count("B3_CAS") == 1
                        assert transport.calls.count("RETIRE_TERMINAL") == 0
                        state["actual_terminal_abort_boundary"] = {
                            "b2_raw": pending._bytes,
                            "checkpoint": checkpoint,
                            "checkpoint_state": dict(h.checkpoint),
                            "physical_home": _v2_private_tree_bytes(h.home),
                            "rpc": list(transport.calls),
                        }
                        if terminal_abort == "interrupted":
                            assert threading.current_thread() is threading.main_thread()
                            assert signal.getsignal(signal.SIGTERM) is mod.base._interrupt
                            assert signal.SIGTERM not in signal.pthread_sigmask(
                                signal.SIG_BLOCK, []
                            )
                            state["actual_terminal_abort_signals"] = [(os.getpid(), signal.SIGTERM)]
                            os.kill(os.getpid(), signal.SIGTERM)
                            raise AssertionError("original SIGTERM handler must interrupt")
                        # Expire the original invocation deadline; no renewal,
                        # invented Aborted, worker result or terminal decision.
                        c.now["mono"] += mod.ACTIONS["finalize"].timeout + 1
                        return super().run(argv, timeout=timeout)
                    abort_kind = state.get("hold_relay_retirement_for_public_abort")
                    if state.get("hold_relay_retirement_for_timeout") or abort_kind is not None:
                        assert action == "execute" and host_eof and retired and not errors
                        assert state["context"].scientific_finished and state["steps"] == 5
                        assert action_results[-1]["result"]["phase"] == "RECONCILING"
                        assert science.backend.accepted == 50 and children[-1].poll() is None
                        assert len(relay_exit) == 1 and not state.get(
                            "held_relay_at_actual_worker_wait"
                        )
                        state["held_relay_at_actual_worker_wait"] = True
                        if abort_kind is not None:
                            assert abort_kind in {"timeout", "interrupted"}
                            assert state["publication"].store.resolver._closed
                            codec = boot.apparatus["ex10_session_v2"]
                            checkpoint = boot.checkpoint()
                            pending = codec.load_official_session(
                                h.root / mod.SESSION_FILE,
                                context=boot.context(),
                                profile=h.profile,
                                expected_tip=checkpoint[0],
                                expected_count=checkpoint[1],
                            )
                            state["actual_public_abort_pending_boundary"] = {
                                "b2_raw": pending._bytes,
                                "checkpoint": checkpoint,
                                "checkpoint_state": dict(h.checkpoint),
                                "physical_home": _v2_private_tree_bytes(h.home),
                                "rpc": list(transport.calls),
                            }
                            if abort_kind == "interrupted":
                                assert threading.current_thread() is threading.main_thread()
                                assert signal.getsignal(signal.SIGTERM) is mod.base._interrupt
                                assert signal.SIGTERM not in signal.pthread_sigmask(
                                    signal.SIG_BLOCK, []
                                )
                                state["actual_public_abort_signals"] = [
                                    (os.getpid(), signal.SIGTERM)
                                ]
                                # Only the actual current owned test process,
                                # while original public main handler is installed.
                                os.kill(os.getpid(), signal.SIGTERM)
                                raise AssertionError("original SIGTERM handler must interrupt")
                        # Original host deadline genuinely expires at worker
                        # wait; no fabricated Aborted or replaced guard/result.
                        c.now["mono"] += mod.ACTIONS["execute-official"].timeout + 1
                        return super().run(argv, timeout=timeout)
                    state["worker_retired"] = True
                    if relay_exit:
                        os.write(relay_exit[0], b"0" if not errors else b"1")
                        os.close(relay_exit.pop())
            return super().run(argv, timeout=timeout)

    def raw_bridge(**facts):
        state["publication"] = facts["publication"]
        info = os.fstat(facts["publication"].store.fd)
        state["publication_fd_identity"] = (info.st_dev, info.st_ino)
        return prepare_bridge(**facts)

    def holder(**facts):
        assert facts["process"].container_id == cid and facts["role"] == "SESSION_EXECUTOR"
        assert (
            facts["proposal"].to_dict()["holder_public_key_hex"]
            == holder_key.public_key().public_bytes_raw().hex()
        )
        return holder_key.public_key()

    def completion(**facts):
        assert facts["action"] == launcher_action
        assert facts["result_bytes"] == mod.base.canonical_bytes(results[-1])
        if action != "finalize":
            assert facts["terminal"] is None
        else:
            snapshot, retired = facts["terminal"]
            assert (
                op.history.find_run(snapshot, results[-1]["run_id"])["lifecycle_state"]
                == "TERMINAL"
            )
            assert retired["terminal_witness_reference"] is not None
        assert type(facts["response"]) is bytes
        assert op.wire.parse_response_bytes(facts["response"]).to_dict()["status"] == "OK"
        state["completion"].append(facts["written"])

    def forbidden(**facts):
        raise AssertionError("full START must not route domain/cleanup/terminal")

    providers = {name: forbidden for name in mod.NativeActionFactory._PROVIDERS}
    providers.update(
        prepare_oneoff=prepare_plan,
        approve_action=approved,
        approve_creation=approved,
        prepare_envelope=prepare_envelope,
        prepare_bridge=raw_bridge,
        holder_admitter=holder,
        approve_completion=completion,
        approve_terminal=approved,
    )
    factory = mod.NativeActionFactory(
        providers=providers,
        signer=c.factory.signer,
        custody_hmac_key=key,
        runner_factory=OwnedWorkerDocker,
        process_factory=launch,
    )
    boot = c.install(action_factory=factory)
    invocation = replace(
        c.invocation(
            "start-official",
            confirmed=True,
            nonce="d" * 64,
            acknowledged=acknowledged,
            preflight_report=evidence_path / staging,
        ),
        native_bootstrap=boot,
    )
    try:
        yield SimpleNamespace(
            c=c,
            h=h,
            mod=mod,
            boot=boot,
            factory=factory,
            invocation=invocation,
            state=state,
            errors=errors,
            results=results,
            children=children,
            threads=threads,
            phases=phases,
            b3_floor=b3_floor,
            transport=transport,
            advance=advance_to_action,
            science=science,
            action_results=action_results,
        )
    finally:
        stop.set()
        context = state["context"]
        if context is not None:
            context.channel.close()
        for fd in relay_exit:
            try:
                os.close(fd)
            except OSError:
                pass
        relay_exit.clear()
        for child in children:
            mod.NativeWorkerAttachment._reap(child)
        for thread in threads:
            thread.join(timeout=5)
        assert all(not thread.is_alive() for thread in threads)
        assert all(child.poll() is not None for child in children)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_full_factory_start_execute_reconcile_finalize_owned_chain(
    native_factory_execution_chain_case,
):
    c = native_factory_execution_chain_case
    first = c.mod.execute(c.invocation)
    assert first["launcher_exit"] == 0 and not c.errors
    assert c.action_results[0]["steps"] == 9
    for target in ("execute-official", "reconcile", "finalize"):
        invocation = c.advance(target)
        outcome = c.mod.execute(invocation)
        assert outcome["launcher_exit"] == 0 and not c.errors
        assert c.action_results[-1]["steps"] == 5
        assert c.state["completion"] == [False, True]
    assert [x["action"] for x in c.action_results] == ["start", "execute", "reconcile", "finalize"]
    assert len({x["cid"] for x in c.action_results}) == 4
    assert len({x["exchange"] for x in c.action_results}) == 4
    assert c.state["actual_emission_proof_admitted"]
    assert c.science.backend.accepted == 50
    assert all(token.released for token in c.science.tokens)
    assert c.state["local_seal_checked"]
    assert c.transport.calls.count("RESERVE") == c.transport.calls.count("COMMIT_OPEN") == 1
    assert c.transport.calls.count("B3_CAS") == 2
    assert c.transport.calls.count("RETIRE_TERMINAL") == 1
    final = c.boot.load(invocation)
    assert final.header == c.c.initial.header
    assert final.to_dict()["official_run_id"] == c.action_results[0]["result"]["run_id"]
    assert final.to_dict()["history"][-1]["payload"]["event"] == "complete-finalize"
    assert final.to_dict()["history"][-1]["payload"]["in_flight"] == []
    assert all(child.poll() == 0 for child in c.children)


@pytest.fixture
def native_factory_recovery_precondition_case(native_factory_resource_case, request, monkeypatch):
    """Full installed host factory, actual protocol/custody/GOV/B3/B4/B2.

    External approval, Docker observations, PF probes and PID1 admission are
    synthetic. An actual owned Popen relays private pipes to an actual executor
    context in an owned thread. This is mechanical integration, not real Docker
    deployment, OS PID1 qualification, official START or a performance result.
    """
    import ast
    import contextlib
    import copy
    import multiprocessing
    import subprocess
    import threading
    import time
    import traceback
    from dataclasses import replace
    from uuid import uuid4

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_resource_case
    h, mod, boot = c.h, c.mod, c.boot
    app = boot.apparatus
    op, b5, codec = (
        app["ex10_operational_context"],
        app["ex10_deployment_envelope"],
        app["ex10_session_v2"],
    )
    executor = helper.executor
    science = helper.make_harness(request.node.name + "-owned-sut", helper.ex10.load_dataset())
    request.addfinalizer(lambda: helper.close_harness(science))
    # Existing scientific test providers use an owned loopback HTTP backend and
    # the unchanged dataset/FakeAI/protocol. This is never a deployment result.
    sql, durable = op.sql, op.durable
    owned = contextlib.ExitStack()
    request.addfinalizer(owned.close)
    children, threads, errors, results, phases = [], [], [], [], []
    captured = {}
    action = "start"
    launcher_action = "start-official"
    action_results = []
    original_qualify_open = op.NativeHostSources.qualify_open

    def capture_original_governance(source, **facts):
        result = original_qualify_open(source, **facts)
        assert len(source.catalog) == 1
        captured.update(source.catalog)
        return result

    monkeypatch.setattr(op.NativeHostSources, "qualify_open", capture_original_governance)
    stop = threading.Event()
    actor_done = threading.Event()
    publications_ready = threading.Event()
    preflight_ready = threading.Event()
    real_deadline = time.perf_counter() + 21200
    state = {
        "control": None,
        "cfg": None,
        "plan": None,
        "created": False,
        "running": False,
        "effects": [],
        "prepared": None,
        "context": None,
        "store": None,
        "steps": None,
        "completion": [],
    }

    def identity(path):
        s = path.stat()
        return durable.FileIdentity(s.st_dev, s.st_ino, s.st_uid, stat.S_IMODE(s.st_mode))

    stores = {}

    def retain(path):
        path = Path(path)
        key = str(path)
        if key not in stores:
            value = durable.PrivateStore(key, identity(path))
            stores[key] = value
            owned.callback(value.close)
        return stores[key]

    def directory(name):
        path = h.home / name
        path.mkdir(mode=0o700)
        return retain(path)

    def binding(name, path, raw, schema, *, create=True):
        path = Path(path)
        store = retain(path.parent)
        if create:
            store.create_exact(str(path), raw)
        else:
            assert store.read(str(path)) == raw
        ref = {
            "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "schema_version": schema,
        }
        return op.HostArtifactBinding(name, sql.canonical_bytes(ref), store)

    def new_binding(name, value, schema="s11-ex10-synthetic-full-host-v1"):
        return binding(
            name, h.home / ("full-" + name + ".json"), sql.canonical_bytes(value), schema
        )

    def reference(b):
        return sql.parse_canonical(b.reference)

    evidence = retain(Path(h.profile["host_roots"]["evidence"]["path"]))
    certificate_store = directory("full-certificates")
    candidate_store = directory("full-b3-candidates")
    monkeypatch.setattr(
        h.authority,
        "read_payload",
        _native_fixture_candidate_payload_reader(h.authority, candidate_store, sql),
    )
    b3_floor_store = directory("full-b3-floor")
    b3_floor_store.create_exact(
        b3_floor_store.root + "/floor-00000000000000000000.json",
        sql.canonical_bytes(h.authority.receipt),
    )
    b3_floor = durable.DurableFloor(
        b3_floor_store,
        [h.authority.pin],
        sql.digest(h.authority.receipt),
        h.authority.receipt["genesis"],
    )
    authorization = new_binding("authorization", {"approved": "synthetic-independent-A"})
    policy_store = directory("full-policy")
    policy_schema = "s11-ex10-synthetic-full-policy-v1"
    root_binding = hashlib.sha256(
        sql.canonical_bytes(c.initial.header["path_identity_binding"])
    ).hexdigest()
    host_bindings = {
        name: new_binding(name, {"synthetic_approval": name})
        for name in (
            "host_identity",
            "bridge_approval",
            "witness_transport",
            "writer_closure",
            "recovery_policy",
        )
    }
    host_bindings["coordinator_registry"] = binding(
        "coordinator_registry",
        h.home / "full-coordinator-registry.json",
        c.resource["registry_binding"],
        "s11-ex10-synthetic-coordinator-reference-v1",
    )
    base_receipt = binding(
        "base_receipt",
        h.home / "full-base-receipt.json",
        sql.canonical_bytes(h.authority.receipt),
        h.authority.receipt["witness_schema_version"],
    )
    inventory = next(
        ast.literal_eval(n.value)
        for n in ast.parse(
            (boot.repo_root / "backend/apps/experiments_app/ex10_preflight.py").read_bytes()
        ).body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "RUNNING_SOURCES" for t in n.targets)
    )
    sources = {
        p: hashlib.sha256((boot.repo_root / p).read_bytes()).hexdigest()
        for p in (*inventory, "backend/apps/experiments_app/ex10_prepare.py")
    }
    required = frozenset(n["check"] for n in helper.preflight_report()["checks"])
    holder_key = Ed25519PrivateKey.generate()
    cid = hashlib.sha256(b"full-factory-oneoff").hexdigest()
    image = "sha256:" + hashlib.sha256(b"full-factory-image").hexdigest()
    network = hashlib.sha256(b"full-factory-network").hexdigest()
    key = c.factory.custody_key
    fingerprint = executor.executor_fingerprint()
    assert (
        fingerprint
        == hashlib.sha256(
            sql.canonical_bytes({p: sources[p] for p in executor.EXECUTOR_SOURCES})
        ).hexdigest()
    )
    transport = c.full_transport

    def wait_for(predicate):
        while not predicate():
            if stop.is_set() or time.perf_counter() >= real_deadline:
                raise AssertionError("owned full factory fixture watchdog")
            time.sleep(0.02)

    component_children = []

    def approved(**facts):
        if launcher_action == "declare-executor-gone":
            previous = state["fixture_precondition"]
            assert previous["kind"] == "declare-executor-gone"
            child = previous["child"]
            assert child.pid == previous["pid"] and child.exitcode == 137
            assert not child.is_alive()
            assert previous["run_id"] == state["original_reservation"]["run_id"]
            assert previous["component_process"].container_id != cid
            state["exact_previous_scientific_death_approved"] = True
        if "profile" in facts:
            assert facts["profile"].to_dict() == h.profile
        if "header" in facts:
            assert facts["header"] == c.initial.header

    evidence_path = Path(evidence.root)
    exchange = str(uuid4())
    staging = "application-preflight." + str(uuid4()) + ".json"
    initial_snapshot = boot.read_authority(h.authority.descriptor_ref)
    acknowledgments = []
    for run in op.history.list_official_runs(initial_snapshot):
        target = op.history.verify_ack_target(initial_snapshot, run["run_id"])
        if target["recognizable"]:
            acknowledgments.append(
                {
                    "run_id": run["run_id"],
                    "authority_revision": target["revision"],
                    "terminal_evidence_reference": target["evidence_reference"],
                }
            )
    acknowledgments.sort(key=lambda item: item["run_id"])
    acknowledged = tuple(item["run_id"] for item in acknowledgments)

    def prepare_plan(**facts):
        current_session = facts["session"]
        assert current_session.header == c.initial.header
        state["session"] = current_session
        revision = op.history.current_revision(boot.read_authority(h.authority.descriptor_ref))

        def pin(p):
            return {
                "key_id": p.key_id,
                "key_epoch": p.key_epoch,
                "public_key_hex": p.public_key.public_bytes_raw().hex(),
            }

        cfg = {
            "action": action,
            "exchange_id": exchange,
            "profile_sha256": hashlib.sha256(boot.profile_path.read_bytes()).hexdigest(),
            "header_sha256": c.initial.to_dict()["header_sha256"],
            "bridge_pin": pin(c.resource["bridge_pin"]),
            "witness_pin": pin(c.resource["floor"].pins[0]),
            "b3_pins": [h.authority.pin.record],
            "genesis_digest": sql.digest(h.authority.receipt),
            "genesis_binding": h.authority.receipt["genesis"],
            "authority_revision": revision["revision"],
            "request_payload": {
                "expected_head": {
                    **{
                        k: revision[k]
                        for k in ("authority_id", "revision", "ledger_digest", "witness_digest")
                    },
                    "authority_epoch": revision["epoch"],
                    "receipt_reference": reference(base_receipt),
                },
                "acknowledgments": acknowledgments,
                "deployment_verification_reference": h.profile["deployment_verification_reference"],
                "descriptor_reference": h.authority.descriptor_ref,
                "profile_reference": {
                    "path": str(boot.profile_path),
                    "sha256": hashlib.sha256(boot.profile_path.read_bytes()).hexdigest(),
                    "schema_version": h.profile["schema_version"],
                },
                "session_reference": {
                    "path": str(h.home / "full-header.json"),
                    "sha256": c.initial.to_dict()["header_sha256"],
                    "schema_version": "s11-ex10-session-v2-header-projection-v1",
                },
            },
            "expected_reservation": state.get("original_reservation"),
            "host_root_binding_sha256": root_binding,
            "source_hashes": sources,
            "staging_name": staging,
            "command_options": {
                "expected_git_sha": boot.expected_git_sha,
                "source_root": "/s11-full-source",
                "nonce": "d" * 64,
                "acknowledge_not_demonstrated": list(acknowledged),
            },
            "authorization_reference": reference(authorization),
        }
        plan = {
            "name": "s11-synthetic-full-" + exchange,
            "image_digest": image,
            "network_id": network,
            "bootstrap_bytes": sql.canonical_bytes(cfg),
            "service_environment": {"DJANGO_SETTINGS_MODULE": "config.settings.test"},
            "owner_labels": {"s11.owner.session": c.initial.header["session_id"]},
        }
        if action == "start":
            assert facts["invocation"].preflight_report == evidence_path / staging
        else:
            record = state["original_reservation"]
            cfg["request_payload"] = {
                "reservation_id": record["reservation_id"],
                "original_request_id": record["request_id"],
            }
            cfg["staging_name"] = None
            cfg["command_options"] = {
                "expected_git_sha": boot.expected_git_sha,
                "run_id": record["run_id"],
            }
            if action == "finalize":
                cfg["command_options"].update(
                    abandon_reason=None,
                    declare_executor_gone=launcher_action == "declare-executor-gone",
                )
            plan["bootstrap_bytes"] = sql.canonical_bytes(cfg)
        # Schema-only check before expensive host/OS preparation. This dummy
        # envelope reference is never passed to the factory or actual exchange;
        # the SDK binds the actual B5 bytes after their real publication.
        if action == "start":
            shape = {
                **cfg["request_payload"],
                "operational_envelope_reference": {
                    "path": str(evidence_path / ".s11-operational" / exchange / "envelope.json"),
                    "sha256": "0" * 64,
                    "schema_version": b5.SCHEMA_VERSION,
                },
                "preflight_reference": {
                    "path": str(evidence_path / staging),
                    "sha256": "0" * 64,
                    "schema_version": executor.ex10_preflight.SCHEMA_VERSION,
                },
                "run_identity": executor.propose_run(
                    started_at=c.now["utc"], git_sha=boot.expected_git_sha, nonce="d" * 64
                ),
            }
            executor.wire._validate(shape, executor.wire._SCHEMA_034)
        else:
            assert executor._native_initial_payload(cfg) == cfg["request_payload"]
        state.update(cfg=cfg, plan=plan)
        return plan

    class DockerBoundary:
        def __init__(self, cwd, environment):
            assert cwd == boot.repo_root
            self.environment = dict(environment)

        def run(self, argv, *, timeout):
            assert argv[:2] == ("/usr/bin/docker", "container") and timeout > 0
            action = argv[2]
            if action == "create":
                assert not state["created"]
                state["created"] = True
                state["effects"].append("create")
                environment = {}
                labels = {}
                mounts = []
                args = list(argv)
                for index, value in enumerate(args):
                    if value == "--env":
                        name = args[index + 1]
                        assert "=" not in name and name in self.environment
                        environment[name] = self.environment[name]
                    elif value == "--label":
                        k, v = args[index + 1].split("=", 1)
                        labels[k] = v
                    elif value == "--mount":
                        parts = dict(
                            x.split("=", 1) if "=" in x else (x, True)
                            for x in args[index + 1].split(",")
                        )
                        mounts.append(
                            {
                                "Type": parts["type"],
                                "Source": parts["source"],
                                "Destination": parts["target"],
                                "RW": not parts.get("readonly", False),
                            }
                        )
                state.update(environment=environment, labels=labels, mounts=mounts)
                return mod.Completed(0, (cid + "\n").encode(), b"")
            if action == "start":
                assert (
                    argv == ("/usr/bin/docker", "container", "start", cid)
                    and state["created"]
                    and not state["running"]
                )
                state["running"] = True
                state["effects"].append("start")
                return mod.Completed(0, (cid + "\n").encode(), b"")
            assert action == "inspect" and argv[-1] == cid
            running = state["running"] and not state.get("worker_retired", False)
            value = {
                "Id": cid,
                "Name": "/" + state["plan"]["name"],
                "Image": image,
                "State": {
                    "Status": "running"
                    if running
                    else ("exited" if state["running"] else "created"),
                    "Running": running,
                    "Restarting": False,
                    "Pid": 2345 if running else 0,
                    "ExitCode": 1 if errors else state.get("scientific_exit_code", 0),
                    "OOMKilled": False,
                },
                "Config": {
                    "User": f"{os.getuid()}:{os.getgid()}",
                    "Labels": state["labels"],
                    "Env": [k + "=" + v for k, v in state["environment"].items()],
                    "Entrypoint": ["/usr/local/bin/python"],
                    "Cmd": ["-B", "/app/manage.py", "s11_ex10", "bridge"],
                    "WorkingDir": "/s11-full-source/backend",
                    "Healthcheck": {"Test": ["NONE"]},
                },
                "HostConfig": {
                    "ReadonlyRootfs": True,
                    "Privileged": False,
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges"],
                    "RestartPolicy": {"Name": "no"},
                    "PortBindings": {},
                    "PublishAllPorts": False,
                    "Binds": None,
                    "VolumesFrom": None,
                    "Devices": None,
                    "DeviceRequests": None,
                    "PidMode": "",
                    "UsernsMode": "",
                },
                "Mounts": state["mounts"],
                "NetworkSettings": {"Networks": {"synthetic-full-network": {"NetworkID": network}}},
            }
            return mod.Completed(0, sql.canonical_bytes([value]), b"")

    def prepare_envelope(**facts):
        state["control"] = facts["control"]
        phases.append("prepare_envelope")
        if action != "start":
            assert facts["pending"].header == c.initial.header
            return {
                "configuration": state["start_envelope_configuration"],
                "attached_bytes": state["start_envelope_bytes"],
            }
        wait_for(lambda: preflight_ready.is_set() or bool(errors))
        assert not errors, errors
        cfg = state["cfg"]
        control = facts["control"]
        pending = facts["pending"]
        profile_ref = {
            "path": str(boot.profile_path),
            "sha256": cfg["profile_sha256"],
            "schema_version": app["ex10_runtime_profile"].SCHEMA_VERSION,
        }
        header_ref = reference(
            binding(
                "header",
                h.home / "full-header.json",
                codec.canonical_session_header_bytes(pending.header),
                "s11-ex10-session-v2-header-projection-v1",
            )
        )
        pf_raw = evidence.read(str(evidence_path / staging))
        pf = sql.parse_canonical(pf_raw)
        pf_ref = {
            "path": str(evidence_path / staging),
            "sha256": hashlib.sha256(pf_raw).hexdigest(),
            "schema_version": pf["schema_version"],
        }
        head_ref = reference(
            binding(
                "authority_view",
                h.home / "full-authority-view.json",
                boot.read_authority(h.authority.descriptor_ref).canonical_current_head,
                "s11-ex10-synthetic-head-reference-v1",
            )
        )
        ready_ref = reference(new_binding("ready", {"synthetic_zero_reports": True}))
        containers, mounts = [], []
        compose = {"name": h.profile["project"], "services": {}}
        for service in h.profile["expected_services"]:
            sut = hashlib.sha256(("sut-full-" + service).encode()).hexdigest()
            item = {
                "container_id": sut,
                "service": service,
                "role": service,
                "image_digest": "sha256:" + hashlib.sha256(service.encode()).hexdigest(),
                "running": True,
                "mounts": [],
            }
            if service in ("backend", "worker"):
                for source, target, readonly in control.mounts:
                    m = {
                        "container_id": sut,
                        "type": "bind",
                        "source_canonical": source,
                        "destination": target,
                        "read_only": readonly,
                        "ownership_reference": h.profile["deployment_verification_reference"],
                    }
                    item["mounts"].append(m)
                    mounts.append(m)
            containers.append(item)
            compose["services"][service] = {
                "image": service + ":synthetic",
                "volumes": [
                    {
                        "type": m["type"],
                        "source": m["source_canonical"],
                        "target": m["destination"],
                        "read_only": m["read_only"],
                    }
                    for m in item["mounts"]
                ],
            }

        def running_source(container_id, relative_path):
            assert (
                container_id in {i["container_id"] for i in containers}
                and relative_path in inventory
            )
            return (boot.repo_root / relative_path).read_bytes()

        configuration = {
            "profile_reference": profile_ref,
            "header_reference": header_ref,
            "application_preflight_reference": pf_ref,
            "historical_authority_view_reference": head_ref,
            "path_context": {
                **json.loads(boot.path_context_bytes),
                "running_sources": list(sources),
            },
            "running_sources": {p: sources[p] for p in inventory},
            "host_operational_sources": {
                "backend/apps/experiments_app/ex10_prepare.py": sources[
                    "backend/apps/experiments_app/ex10_prepare.py"
                ]
            },
            "container_identities": [{k: i[k] for k in b5.CONTAINER_FIELDS} for i in containers],
            "mounts": mounts,
            "compose_projection": b5.sanitized_compose_projection(compose),
            "ready_state": {
                "report_reference": ready_ref,
                "checks": [{"check": "zero_reports", "result": "PASS"}],
            },
            "application_required_checks": required,
            "compose_observer": lambda: copy.deepcopy(compose),
            "container_observer": lambda: copy.deepcopy(containers),
            "running_source_reader": running_source,
            "bootstrap_verifier": approved,
            "source_verifier": approved,
            "session_verifier": approved,
            "authority_verifier": approved,
            "ready_verifier": approved,
            "clock": lambda: c.now["utc"],
        }
        state["envelope_configuration"] = configuration
        return {"configuration": configuration, "attached_bytes": None}

    def prepare_bridge(**facts):
        phases.append("prepare_bridge")
        prepared = facts["prepared"]
        pending = facts["pending"]
        publication = facts["publication"]
        state["prepared"] = prepared
        profile_binding = binding(
            "profile",
            boot.profile_path,
            boot.profile_path.read_bytes(),
            h.profile["schema_version"],
            create=False,
        )
        assert (h.root / mod.SESSION_FILE).read_bytes() == pending._bytes
        session_binding = binding(
            "session",
            h.home / ("full-approved-pending-session." + exchange + ".json"),
            pending._bytes,
            codec.SESSION_V2_SCHEMA,
        )
        header_binding = binding(
            "header",
            h.home / "full-header.json",
            codec.canonical_session_header_bytes(pending.header),
            "s11-ex10-session-v2-header-projection-v1",
            create=False,
        )
        session_bindings = [profile_binding, session_binding, header_binding]
        for name in app["ex10_runtime_profile"].REFERENCE_FIELDS:
            ref = h.profile[name]
            session_bindings.append(
                binding(
                    name,
                    Path(ref["path"]),
                    Path(ref["path"]).read_bytes(),
                    ref["schema_version"],
                    create=False,
                )
            )
        ref = pending.header["runtime_profile_approval_reference"]
        session_bindings.append(
            binding(
                "runtime_profile_approval_reference",
                Path(ref["path"]),
                Path(ref["path"]).read_bytes(),
                ref["schema_version"],
                create=False,
            )
        )
        envelope_binding = binding(
            "envelope",
            Path(publication.store.root) / "envelope.json",
            prepared.envelope_bytes,
            b5.SCHEMA_VERSION,
            create=False,
        )
        pf_ref = state["envelope_configuration"]["application_preflight_reference"]
        preflight_binding = binding(
            "preflight",
            Path(pf_ref["path"]),
            Path(pf_ref["path"]).read_bytes(),
            pf_ref["schema_version"],
            create=False,
        )
        descriptor = h.authority.descriptor_ref
        facts15 = {
            "namespace": "S11C3-EX10",
            "authority_id": c.resource["authority_id"],
            "authority_epoch": 1,
            "descriptor_reference": descriptor,
            "approved_host_identity_reference": reference(host_bindings["host_identity"]),
            "coordinator_registry_reference": reference(host_bindings["coordinator_registry"]),
            "lock_file_identity": op._file_identity(c.resource["lock_identity"]),
            "sidecar_identity": op._file_identity(c.resource["sidecar_identity"]),
            "ledger_identity": op._file_identity(c.resource["ledger_identity"]),
            "bridge_key_public_approval_reference": reference(host_bindings["bridge_approval"]),
            "independent_witness_transport_reference": reference(
                host_bindings["witness_transport"]
            ),
            "writer_closure_reference": reference(host_bindings["writer_closure"]),
            "approved_executor_entrypoint_identity": {
                "container_id": cid,
                "identity_digest": prepared.process.identity_digest,
                "argv": ["/usr/local/bin/python", "-B", "/app/manage.py", "s11_ex10", "bridge"],
            },
            "normal_operation_allowlist": sorted(op.NORMAL_OPERATIONS),
            "recovery_operator_approval_policy": reference(host_bindings["recovery_policy"]),
        }
        policy_binding = binding(
            "policy",
            Path(policy_store.root) / ("policy." + exchange + ".json"),
            sql.canonical_bytes(facts15),
            policy_schema,
        )

        def resolve(**kw):
            assert kw["canonical_facts"] == sql.canonical_bytes(facts15)
            return policy_binding

        def policy_approve(**kw):
            assert kw["canonical_authorization"] == sql.canonical_bytes(
                {"approved": "synthetic-independent-A"}
            )
            assert kw["authorization_reference"] == authorization.reference

        def envelope_verify(**kw):
            assert kw["canonical_envelope"] == prepared.envelope_bytes
            assert kw["reference"] == envelope_binding.reference
            assert (
                envelope_binding.store.read(reference(envelope_binding)["path"])
                == prepared.envelope_bytes
            )
            # This provider approves immutable B5 bytes. Factory-owned live and
            # completed process observations remain mandatory in product code.

        def session_approve(**kw):
            assert kw["profile"].to_dict() == h.profile
            assert kw["session"].header == pending.header

        session_cfg = {
            "path_context": sql.canonical_bytes(json.loads(boot.path_context_bytes)),
            "source_revision": boot.expected_git_sha,
            "launcher_blobs": sql.canonical_bytes(pending.header["launcher_blobs"]),
            "expected_tip": pending._expected_tip,
            "expected_count": pending._expected_count,
            "process": prepared.process,
            "approval_verifier": session_approve,
            "deployment_observer": lambda **kw: prepared.process,
        }
        policy_cfg = {
            "bindings": tuple(host_bindings.values()),
            "authorization_binding": authorization,
            "policy_store": policy_store,
            "policy_name": "policy." + exchange + ".json",
            "policy_schema_version": policy_schema,
            "approved_root_binding_sha256": root_binding,
            "approval_verifier": policy_approve,
            "envelope_verifier": envelope_verify,
            "required_preflight_checks": required,
            "resolve_policy_binding": resolve,
        }
        publisher_cfg = {
            "ledger_store": c.resource["ledger_store"],
            "ledger_name": c.resource["ledger_name"],
            "ledger_identity": c.resource["ledger_identity"],
            "candidate_store": candidate_store,
            "floor": b3_floor,
            "transport": transport,
            "signer_pin": h.authority.pin,
            "clock": lambda: c.now["utc"],
            "clock_health": lambda: True,
        }
        historical = state.get("historical", op.NativeGovernanceHistory(()))
        source_cfg = {
            "bindings": (
                authorization,
                envelope_binding,
                preflight_binding,
                base_receipt,
            ),
            "authority_reader": lambda: boot.read_authority(descriptor),
            "historical_contexts": lambda **kw: historical,
            "artifact_store": evidence,
            "certificate_store": certificate_store,
            "executor_fingerprint": fingerprint,
            "live_owner": None,
            "uncertainty_verifier": None,
            "recovery_sources": None,
            "lifecycle_sources": None,
        }
        if launcher_action == "finalize":
            assert action_results[-1]["action"] == "reconcile"
            assert results[-1]["provisional"] is True
            assert results[-1]["decision"] in ("PASS", "FAIL", "NOT_DEMONSTRATED")
        publications_ready.set()
        return {
            "session_bindings": tuple(session_bindings),
            "session_configuration": session_cfg,
            "policy_configuration": policy_cfg,
            "publisher_configuration": publisher_cfg,
            "source_configuration": source_cfg,
            "resource_configuration": dict(c.resource, transport=transport),
            "reconciled": (results[-1]["decision"] if launcher_action == "finalize" else None),
        }

    def verify_local_seal(*, context, store, run_id, result):
        assert action == "finalize" and context is state["context"]
        assert run_id == result["run_id"]
        existing = executor.load_existing_state_readonly(store, run_id)
        assert existing is not None and existing.finalized
        executor.validate_finalized_state(existing.run_root, existing.records, custody_hmac_key=key)
        state["local_seal_checked"] = True

    def historical_original():
        assert len(captured) == 1
        reference_id, original = next(iter(captured.items()))
        sessions = op.NativeSessionQualifier(
            registry=original.sessions.registry,
            path_context=original.sessions.path_context,
            source_revision=original.sessions.source_revision,
            launcher_blobs=original.sessions.launcher_blobs,
            expected_tip=original.sessions.expected_tip,
            expected_count=original.sessions.expected_count,
            process=original.sessions.process,
            approval_verifier=original.sessions.approve,
            deployment_observer=lambda **kw: original.sessions.process,
        )
        original_policy = original.policy
        historical_policy = op.NativeHostPolicy(
            registry=original_policy.registry,
            ledger_store=original_policy.ledger,
            ledger_name=original_policy.name,
            ledger_identity=original_policy.identity,
            session_qualifier=sessions,
            approved_root_binding_sha256=original_policy.root_binding,
            approval_verifier=original_policy.approve,
            envelope_verifier=original_policy.envelope,
            required_preflight_checks=original_policy.checks,
            authorization_binding=authorization,
        )
        assert historical_policy.reference == original_policy.reference
        qualifier = op.NativeGovernanceQualifier(
            session_qualifier=sessions,
            registry=original.registry,
            certificate_store=original.store,
            bridge_pin=original.pin,
            holder_public_key=original.holder,
            reservation_id=original.reservation_id,
            executor_fingerprint=original.fingerprint,
            required_preflight_checks=original.checks,
            protocol=original.protocol,
            methodology=original.methodology,
            host_policy=historical_policy,
        )
        return op.NativeGovernanceHistory(((reference_id, qualifier),))

    def advance_to_action(target):
        nonlocal action, launcher_action, exchange, staging, cid, holder_key, base_receipt
        assert target in {
            "execute-official",
            "drain",
            "reconcile",
            "finalize",
            "declare-executor-gone",
        }
        assert len(results) == len(action_results)
        if state.get("fixture_precondition") is None:
            assert state["completion"] == [False, True]
        else:
            assert state["fixture_precondition"]["host_single5_completed"]
            assert not boot.load(invocation).to_dict()["in_flight"]
        assert all(child.poll() == 0 for child in children)
        for thread in threads:
            thread.join(timeout=5)
        assert not any(thread.is_alive() for thread in threads)
        original_session = boot.load(invocation)
        if action == "start":
            state["original_reservation"] = state["context"].reservation
            assert state["original_reservation"]["state"] == "COMMITTED"
            state["historical"] = historical_original()
            state["start_envelope_configuration"] = state["envelope_configuration"]
            state["start_envelope_bytes"] = state["prepared"].envelope_bytes
        revision = op.history.current_revision(boot.read_authority(h.authority.descriptor_ref))
        receipt_path = b3_floor._path(revision["revision"])
        receipt_bytes = b3_floor.store.read(receipt_path)
        base_receipt = op.HostArtifactBinding(
            "base_receipt",
            sql.canonical_bytes(
                {
                    "path": receipt_path,
                    "sha256": hashlib.sha256(receipt_bytes).hexdigest(),
                    "schema_version": sql.parse_canonical(receipt_bytes)["witness_schema_version"],
                }
            ),
            b3_floor.store,
        )
        launcher_action = target
        action = mod.NativeOneOffDocker._WORKER_ACTIONS[target]
        exchange = str(uuid4())
        staging = None
        cid = hashlib.sha256(("full-factory-terminal-" + exchange).encode()).hexdigest()
        holder_key = Ed25519PrivateKey.generate()
        actor_done.clear()
        publications_ready.clear()
        preflight_ready.clear()
        state.update(
            control=None,
            cfg=None,
            plan=None,
            created=False,
            running=False,
            prepared=None,
            context=None,
            component_mode=None,
            steps=None,
            completion=[],
            worker_retired=False,
            scientific_exit_code=0,
            host_channel=None,
        )
        result = replace(c.invocation(launcher_action, confirmed=True), native_bootstrap=boot)
        assert boot.load(result)._bytes == original_session._bytes
        return result

    # Client setup and real relay are defined below; no assembled core is passed
    # to the installed factory.
    relay_exit = []
    relay_code = r"""
import os, select, sys, time
reader, writer, exit_reader = map(int,sys.argv[1:])
sources={0:writer,reader:1}
deadline=time.monotonic()+21200
while sources and time.monotonic()<deadline:
    ready,_,_=select.select(list(sources),[],[],1)
    for fd in ready:
        raw=os.read(fd,65536)
        target=sources[fd]
        if raw:
            view=memoryview(raw)
            while view:
                n=os.write(target,view)
                view=view[n:]
        else:
            del sources[fd]
            os.close(target)
            if target==1:
                os.close(2)
if sources:
    sys.exit(3)
while time.monotonic()<deadline:
    ready,_,_=select.select([exit_reader],[],[],1)
    if ready:
        raw=os.read(exit_reader,1)
        sys.exit(0 if raw==b'0' else 4)
sys.exit(5)
"""

    def actor(read_fd, write_fd):
        channel = None
        try:
            cfg = state["cfg"]
            store = helper.EvidenceStore(
                evidence_path,
                custody_hmac_key=key,
                publication_verification_key=c.factory.signer.public_key().public_bytes_raw(),
                rolling_checksums=False,
            )
            state["store"] = store
            science.backend.store = store
            probes = replace(
                science.probes,
                preflight=lambda options: helper.preflight_report(
                    git_sha=boot.expected_git_sha, checked_at=c.now["utc"]
                ),
                fingerprint=lambda: fingerprint,
                now=lambda: c.now["utc"],
            )
            component_kind = state.get("component_mode")
            if component_kind is not None and component_kind != "seal-declare":

                def component_crash():
                    assert state["component_crash_gate"].wait(timeout=21200)
                    raise SystemExit(137)

                if component_kind == "declare-executor-gone":
                    if state.get("component_phase", "STARTED") == "EMITTING":

                        class ComponentDiesAfterArmed:
                            def run(self):
                                assert state["actual_emission_proof_admitted"] is True
                                actual = executor.load_existing_state_readonly(
                                    store, state["original_reservation"]["run_id"]
                                )
                                assert executor.phase_of(actual) is executor.Phase.EMITTING
                                assert actual.has(executor.ART_EMISSION)
                                assert actual.has(executor.ART_ARMED)
                                assert not actual.has(executor.ART_RAW)
                                assert store.verify(actual.run_id) == []
                                state["component_emission_entered"].set()
                                component_crash()

                        probes = replace(
                            probes,
                            generator=lambda plan, prepared, transport: ComponentDiesAfterArmed(),
                        )
                    else:
                        probes = replace(probes, fixture_result=lambda specs: component_crash())
                else:
                    database = probes.database

                    def first_drain_crash():
                        current = executor.load_existing_state_readonly(
                            store, state["original_reservation"]["run_id"]
                        )
                        assert executor.phase_of(current) is executor.Phase.DRAINING
                        assert current.has(executor.ART_RAW) or current.has(
                            executor.ART_RAW_REJECTED
                        )
                        assert not current.has(executor.ART_DRAIN)
                        database()
                        component_crash()

                    probes = replace(probes, database=first_drain_crash)
            if action == "start":
                proposal, raw = executor._prepare_native_start(
                    probes=probes,
                    git_sha=boot.expected_git_sha,
                    source_root=boot.repo_root,
                    nonce="d" * 64,
                )
                evidence.create_exact(str(evidence_path / staging), raw)
                state["start_preflight"] = raw
                preflight_ready.set()
            else:
                raw = state["start_preflight"]
                proposal = state["original_reservation"]["run_identity"]
            wait_for(lambda: publications_ready.is_set())
            proof = executor.ExecutorHostProofs(
                root=str(evidence_path),
                exchange_id=exchange,
                b3_pins=[h.authority.pin.record],
                genesis_digest=sql.digest(h.authority.receipt),
                genesis_binding=h.authority.receipt["genesis"],
                clock=lambda: c.now["utc"],
            )
            process, _ = proof.read("process")
            _, envelope = proof.read("envelope")
            client_profile = executor.profile_codec.parse_runtime_profile_bytes(
                boot.profile_path.read_bytes()
            )
            executor._native_envelope_bindings(
                envelope,
                profile=client_profile,
                header=c.initial.header,
                cfg=cfg,
                preflight=raw,
                now=c.now["utc"],
                fresh_required=action in ("start", "execute"),
            )
            assert process == executor._native_process_identity(
                cfg=cfg,
                profile=client_profile,
                container_id=cid,
                initial=state["plan"]["bootstrap_bytes"],
                envelope=envelope,
            )
            fixed = {
                "exchange_id": exchange,
                "authority_id": c.resource["authority_id"],
                "authority_epoch": 1,
                "authorization_reference": reference(authorization),
                "bridge_key_id": cfg["bridge_pin"]["key_id"],
                "bridge_key_epoch": cfg["bridge_pin"]["key_epoch"],
                "container_identity_digest": process["identity_digest"],
                "deployment_verification_sha256": process["deployment_verification_sha256"],
                "session_id": c.initial.header["session_id"],
                "session_header_sha256": cfg["header_sha256"],
                "runtime_profile_sha256": cfg["profile_sha256"],
                "source_revision": boot.expected_git_sha,
                "project": h.profile["project"],
            }

            def admit(**kw):
                assert kw["context"].owner == (os.getpid(), threading.get_ident())
                assert kw["context"].holder is holder_key and kw["action"] == action
                assert process["container_id"] == cid and state["running"] and not stop.is_set()
                assert boot.profile_path.read_bytes() == client_profile._bytes

            channel = executor.ExecutorBridgeChannel(
                read_fd=read_fd,
                write_fd=write_fd,
                exchange="START" if action == "start" else "SINGLE_RPC",
                exchange_id=exchange,
                budget=120 if action == "start" else 30,
            )
            os.close(read_fd)
            os.close(write_fd)

            def client_current(**facts):
                boundary = action == "execute" and facts.get("phase") == "BARRIER"
                if boundary:
                    assert facts["context"] is state["context"]
                    assert facts["context"].finished and facts["context"].step_authorized
                    assert facts["context"].proposal["run_id"] == facts["run_id"]
                    retained = executor._native_wait_emission(
                        cfg=cfg,
                        proof=proof,
                        deadline=channel.deadline,
                        guard=lambda: admit(context=state["context"], action=action),
                    )
                proof.current(**facts)
                if boundary:
                    retained()
                    admit(context=state["context"], action=action)
                    executor._native_envelope_bindings(
                        envelope,
                        profile=client_profile,
                        header=c.initial.header,
                        cfg=cfg,
                        preflight=raw,
                        now=c.now["utc"],
                        fresh_required=True,
                    )
                    retained()
                    proof.check_current_freshness(
                        context=state["context"], envelope=sql.parse_canonical(envelope)
                    )
                    assert time.monotonic() < channel.deadline
                    state["actual_emission_proof_admitted"] = True

            context = executor.ExecutorOperationalContext(
                action=action,
                channel=channel,
                bridge_pin=executor.wire.PublicKeyPin(
                    c.resource["bridge_pin"].key_id,
                    c.resource["bridge_pin"].key_epoch,
                    c.resource["bridge_pin"].public_key,
                ),
                witness_pin=executor.wire.PublicKeyPin(
                    c.resource["floor"].pins[0].key_id,
                    c.resource["floor"].pins[0].key_epoch,
                    c.resource["floor"].pins[0].public_key,
                ),
                holder_key=holder_key,
                expected_challenge=fixed,
                request_payload=executor._native_prepared_payload(
                    cfg=cfg, profile=client_profile, envelope=envelope
                ),
                evidence_root=str(evidence_path),
                host_evidence_root=str(evidence_path),
                staging_name=staging if action == "start" else None,
                expected_reservation=state.get("original_reservation"),
                authority_revision=cfg["authority_revision"],
                host_root_binding_sha256=root_binding,
                native_admitter=admit,
                current_guard=client_current,
                common_prior_reader=proof.common_prior,
                common_prior_verifier=proof.verify_common,
                commit_verifier=proof.verify_commit,
                terminal_preparer=verify_local_seal,
                clock=lambda: c.now["utc"],
            )
            context.preflight, context.proposal, context.store, context.prepared = (
                raw,
                proposal,
                store,
                True,
            )
            state["context"] = context
            if action == "start":
                result = executor.start_run(
                    store,
                    git_sha=boot.expected_git_sha,
                    source_root=boot.repo_root,
                    nonce="d" * 64,
                    evidence_mode=executor.OFFICIAL_MODE,
                    timezone="UTC",
                    acknowledged_not_demonstrated=acknowledged,
                    probes=probes,
                    operational_context=context,
                )
            else:
                operation = {
                    "execute": executor.execute_run,
                    "drain": executor.run_drain_step,
                    "reconcile": executor.reconcile_run,
                    "finalize": executor.finalize_run,
                }[action]
                result = operation(
                    store,
                    run_id=proposal["run_id"],
                    git_sha=boot.expected_git_sha,
                    probes=probes,
                    operational_context=context,
                    **(
                        {"declare_executor_gone": True}
                        if launcher_action == "declare-executor-gone"
                        else {}
                    ),
                )
            context.persist_public_result(store, result)
            results.append(result)
            action_results.append(
                {
                    "action": action,
                    "cid": cid,
                    "exchange": exchange,
                    "context": context,
                    "result": result,
                    "steps": channel.position,
                }
            )
            state["steps"] = channel.position
        except executor.Ex10Refusal as error:
            # Actual finalize_run raised this refusal from the existing sealed
            # run. The synthetic deployment boundary models scientific2 only
            # after the real SDK producer qualifies/persists the private output.
            # This fixture does not claim real Docker/PID1 qualification.
            if not state.get("allow_original_scientific_refusal", False):
                errors.append(error)
                state["actor_error_trace"] = traceback.format_exception(
                    type(error), error, error.__traceback__
                )
            else:
                try:
                    assert launcher_action == "declare-executor-gone" and action == "finalize"
                    assert type(error) is executor.Ex10Refusal
                    assert error.terminal is True and error.reasons == ("run-finalized",)
                    assert state.get("component_mode") is None
                    assert context.finished and context.step_authorized
                    assert not context.scientific_finished and not context.terminal_prepared
                    assert channel.closed and channel.position == 5
                    assert channel.read_fd is channel.write_fd is channel.error_fd is None
                    context.persist_terminal_refusal(error, declare_executor_gone=True)
                    private = evidence_path / ("executor-result." + exchange + ".json")
                    state["actual_scientific_refusal"] = {
                        "error": error,
                        "private": private,
                        "raw": private.read_bytes(),
                        "pending": boot.load(state["control"].invocation)._bytes,
                        "checkpoint": boot.checkpoint(),
                        "checkpoint_state": dict(h.checkpoint),
                        "ledger": h.authority.database.read_bytes(),
                        "sidecar": (
                            Path(c.resource["sidecar_store"].root) / c.resource["sidecar_name"]
                        ).read_bytes(),
                        "home": _v2_private_tree_bytes(h.home),
                        "rpc": list(transport.calls),
                    }
                    state["steps"] = channel.position
                    state["scientific_exit_code"] = 2
                except BaseException as invalid:
                    errors.append(invalid)
                    state["actor_error_trace"] = traceback.format_exception(
                        type(invalid), invalid, invalid.__traceback__
                    )
        except BaseException as error:
            if state.get("component_mode") is not None and isinstance(error, SystemExit):
                assert error.code == 137
                # The multiprocessing child really exits137 after authentic
                # custody/native permission. No public result or B2 completion.
                raise
            errors.append(error)
            state["actor_error_trace"] = traceback.format_exception(
                type(error), error, error.__traceback__
            )
        finally:
            if channel is not None:
                channel.close()
            else:
                for fd in (read_fd, write_fd):
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            actor_done.set()

    def prepare_recovery_precondition(kind, *, phase="STARTED"):
        """Owned native component SINGLE5, never an interrupted Factory intent."""
        assert kind in {"drain", "declare-executor-gone", "seal-declare"}
        sealing = kind == "seal-declare"
        assert phase in {"STARTED", "EMITTING"}
        assert phase == "STARTED" or kind == "declare-executor-gone"
        previous_precondition = state.get("fixture_precondition")
        if sealing:
            assert previous_precondition["kind"] == "declare-executor-gone"
            assert previous_precondition["actual_exit137"]
            assert phase == previous_precondition["phase"] == "STARTED"
        component_invocation = advance_to_action(
            "declare-executor-gone" if sealing else "execute-official"
        )
        before = boot.load(component_invocation)
        assert before.to_dict()["in_flight"] == []
        journal_before = (
            Path(c.resource["sidecar_store"].root) / c.resource["sidecar_name"]
        ).read_bytes()
        ledger_before = h.authority.database.read_bytes()
        checkpoint_before, checkpoint_state_before = boot.checkpoint(), dict(h.checkpoint)
        prepare_plan(session=before, profile=h.profile, invocation=component_invocation)
        state["component_mode"] = kind
        state["running"] = True  # Explicit fixed synthetic deployment boundary.
        fork = multiprocessing.get_context("fork")
        state["component_phase"] = phase
        if phase == "EMITTING":
            state["actual_emission_proof_admitted"] = False
            state["component_emission_entered"] = fork.Event()
        gate = fork.Event()
        state["component_crash_gate"] = gate
        store = helper.EvidenceStore(
            evidence_path,
            custody_hmac_key=key,
            publication_verification_key=c.factory.signer.public_key().public_bytes_raw(),
            rolling_checksums=False,
        )
        science.backend.store = state["store"] = store
        profile = app["ex10_runtime_profile"].parse_runtime_profile_bytes(
            boot.profile_path.read_bytes()
        )
        envelope_bytes = state["start_envelope_bytes"]
        # Independently qualify the unchanged B5 attachment and retain its
        # original observations; the commitment projection is not authority.
        configuration = state["start_envelope_configuration"]
        qualifier = b5.EnvelopeQualifier(**configuration)
        qualifier.qualify_attached(envelope_bytes, fresh_required=not sealing)
        process_facts = mod._native_process_commitments(
            apparatus=boot.apparatus,
            bootstrap_bytes=state["plan"]["bootstrap_bytes"],
            profile=profile,
            container_id=cid,
            envelope_bytes=envelope_bytes,
        )
        process = op.ExecutorProcessIdentity(**process_facts)
        # Test-only two-field adapter for prepare_bridge, not a product worker
        # binding or a claim that Docker/PID1 was independently admitted.
        prepared = SimpleNamespace(process=process, envelope_bytes=envelope_bytes)
        snapshot = boot.read_authority(h.authority.descriptor_ref)
        deadline = c.now["mono"] + 30
        pipes = []
        child = None
        channel = None
        try:
            with mod._create_native_proof_publication(
                invocation=component_invocation,
                bootstrap=boot,
                exchange_id=exchange,
                preparation_deadline=deadline,
                authority_snapshot=snapshot,
            ) as (publication, namespace_current):
                publication.publish("profile", profile._bytes)
                publication.publish("header", codec.canonical_session_header_bytes(before.header))
                publication.publish("envelope", envelope_bytes)
                publication.publish("process", sql.canonical_bytes(process_facts))
                raw = prepare_bridge(prepared=prepared, pending=before, publication=publication)
                host_read, client_write = os.pipe()
                pipes.extend((host_read, client_write))
                client_read, host_write = os.pipe()
                pipes.extend((client_read, host_write))

                def component_actor():
                    os.close(host_read)
                    os.close(host_write)
                    actor(client_read, client_write)
                    if sealing:
                        assert not errors and state["local_seal_checked"] is True
                        assert (context_result := results[-1])
                        assert context_result["lifecycle"] == "NOT_DEMONSTRATED"
                        assert context_result["binding"] is False
                        assert state["context"].scientific_finished
                        assert state["steps"] == 5
                        # Keep this actual owned component alive through
                        # the last original HOST process/EOF guards.
                        assert gate.wait(timeout=21200)
                        return
                    raise AssertionError("component crash oracle was never reached")

                child = fork.Process(target=component_actor)
                component_children.append(child)
                child.start()
                os.close(client_read)
                os.close(client_write)
                pipes.remove(client_read)
                pipes.remove(client_write)

                def component_observer(**facts):
                    assert child.pid is not None and child.is_alive()
                    assert boot.load(component_invocation)._bytes == before._bytes
                    return process

                raw["session_configuration"]["deployment_observer"] = component_observer
                resources = op.NativePolicyResourcePlan(**raw["resource_configuration"])

                def component_current():
                    namespace_current()
                    resources.check()
                    assert component_observer() is process
                    op.history.current_revision(snapshot)

                graph = mod._compose_native_bridge(
                    apparatus=boot.apparatus,
                    session_bindings=raw["session_bindings"],
                    session_configuration=raw["session_configuration"],
                    policy_configuration=raw["policy_configuration"],
                    publisher_configuration=raw["publisher_configuration"],
                    source_configuration=raw["source_configuration"],
                    resource_plan=resources,
                    signer=c.factory.signer,
                    action="finalize" if sealing else "execute",
                    exchange_id=exchange,
                    expected_run_id=state["original_reservation"]["run_id"],
                    publish_role=publication.publish,
                    holder_admitter=holder,
                    check_current=component_current,
                    preparation_deadline=deadline,
                    authority_snapshot=snapshot,
                )
                channel = op.PipeFrameChannel(
                    read_fd=host_read,
                    write_fd=host_write,
                    exchange="SINGLE_RPC",
                    exchange_id=exchange,
                    budget=30,
                )
                os.close(host_read)
                os.close(host_write)
                pipes.clear()
                if kind == "drain" or phase == "EMITTING":
                    reader = mod.NativeArmedArtifactReader(
                        apparatus=boot.apparatus,
                        admitted=graph.signer.admitted,
                        run_id=state["original_reservation"]["run_id"],
                        custody_hmac_key=key,
                        deadline=deadline,
                    )
                    response = mod.NativeEmissionProofPublication(
                        broker=graph.broker, reader=reader, publish_role=publication.publish
                    ).serve_single(channel)
                else:
                    response = graph.broker.serve_single(channel)
                assert response == graph.signer.signed_response
                assert channel.closed and channel.position == 5
                namespace_current()
                gate.set()  # Release owned component only after authentic HOST SINGLE5 closes.
                child.join(timeout=21200)
                assert not child.is_alive() and child.exitcode == (0 if sealing else 137)
        finally:
            gate.set()
            if channel is not None:
                channel.close()
            for fd in pipes:
                os.close(fd)
            if child is not None and child.pid is not None and child.is_alive():
                child.terminate()
                child.join(timeout=5)
                if child.is_alive():
                    child.kill()
                    child.join(timeout=5)
                assert not child.is_alive()
        run_id = state["original_reservation"]["run_id"]
        existing = executor.load_existing_state_readonly(store, run_id)
        if sealing:
            # Separate actual SDK DECLARE seal, not a Factory FINAL intent.
            # Preserve the original admissible STARTED B2/CP/B3/sidecar exactly;
            # there is no deletion, projection or completion of any pending B2.
            assert (
                existing.finalized
                and executor.phase_of(existing) is executor.Phase.NOT_DEMONSTRATED
            )
            assert store.verify(run_id) == []
            executor.validate_finalized_state(
                existing.run_root, existing.records, custody_hmac_key=key
            )
            decision = existing.payload(executor.ART_DECISION)
            assert decision["lifecycle"] == "NOT_DEMONSTRATED" and decision["binding"] is False
            assert decision["reasons"] == ["emission-interrupted:before-arming"]
            assert science.backend.accepted == 0
            assert boot.load(component_invocation)._bytes == before._bytes
            assert (
                boot.checkpoint() == checkpoint_before and h.checkpoint == checkpoint_state_before
            )
            assert h.authority.database.read_bytes() == ledger_before
            assert (
                Path(c.resource["sidecar_store"].root) / c.resource["sidecar_name"]
            ).read_bytes() == journal_before
            assert state["fixture_precondition"] is previous_precondition
            assert (
                op.history.find_run(boot.read_authority(h.authority.descriptor_ref), run_id)[
                    "lifecycle_state"
                ]
                == "OPEN"
            )
            state["component_mode"] = None
            state["fixture_refusal_seal"] = {
                "run_id": run_id,
                "child": child,
                "actual_sdk_sealed": True,
                "host_single5_completed": True,
                "pending_before": before._bytes,
                "checkpoint_before": checkpoint_before,
                "ledger_before": ledger_before,
                "sidecar_before": journal_before,
                "sealed_run": _v2_private_tree_bytes(existing.run_root),
            }
            return state["fixture_refusal_seal"]
        expected_phase = executor.Phase.DRAINING if kind == "drain" else executor.Phase(phase)
        assert executor.phase_of(existing) is expected_phase
        assert store.verify(run_id) == [] and not existing.finalized
        assert not existing.has(executor.ART_DRAIN)
        if kind == "drain":
            assert existing.has(executor.ART_RAW)
            assert not existing.has(executor.ART_RAW_REJECTED)
        if kind == "declare-executor-gone":
            if phase == "EMITTING":
                assert state["component_emission_entered"].is_set()
                assert existing.has(executor.ART_EMISSION)
                assert existing.has(executor.ART_ARMED)
                assert not existing.has(executor.ART_RAW_REJECTED)
                assert not existing.has(executor.ART_EMISSION_ABORTED)
            else:
                assert not existing.has(executor.ART_ARMED)
            assert not existing.has(executor.ART_RAW)
            assert science.backend.accepted == 0
        assert boot.load(component_invocation)._bytes == before._bytes
        assert h.authority.database.read_bytes() == ledger_before
        assert (
            Path(c.resource["sidecar_store"].root) / c.resource["sidecar_name"]
        ).read_bytes() == journal_before
        payload = dict(before.to_dict()["history"][-1]["payload"])
        payload.update(
            event="fixture-precondition-" + kind,
            state={
                "known_phase": str(expected_phase),
                "uncertain": False,
                "blocked": False,
                "next_allowed_actions": [kind, "inspect", "status"],
            },
        )
        proposal = codec.append_history_entry_v2(before, payload)
        with boot.writer() as writer, mod._native_session_lock(boot, h.root) as current:
            writer.check()
            current()
            actual = codec.update_official_session_v2(
                h.root / mod.SESSION_FILE,
                before,
                proposal,
                context=boot.context(),
                profile=h.profile,
                caller_serialized=True,
            )
            boot.publish((before._expected_tip, before._expected_count), actual)
            writer.check()
            current()
        assert actual.header == before.header and actual.to_dict()["in_flight"] == []
        assert actual.to_dict()["official_run_id"] == run_id
        state["fixture_precondition"] = {
            "kind": kind,
            "child": child,
            "pid": child.pid,
            "component_process": process,
            "run_id": run_id,
            "host_single5_completed": True,
            "actual_exit137": True,
            "phase": str(expected_phase),
        }
        state["component_mode"] = None
        if phase == "EMITTING":
            state["fixture_precondition"]["actual_emission_proof_admitted_in_child"] = state[
                "component_emission_entered"
            ].is_set()
        return state["fixture_precondition"]

    def launch(argv, **options):
        assert argv == (
            "/usr/bin/docker",
            "exec",
            "--interactive",
            cid,
            "/usr/local/bin/python",
            "-B",
            "/app/manage.py",
            "s11_ex10",
            "bridge",
        )
        private_fds = []
        try:
            client_read, relay_write = os.pipe()
            private_fds.extend((client_read, relay_write))
            relay_read, client_write = os.pipe()
            private_fds.extend((relay_read, client_write))
            exit_read, exit_write = os.pipe()
            private_fds.extend((exit_read, exit_write))
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    relay_code,
                    str(relay_read),
                    str(relay_write),
                    str(exit_read),
                ],
                pass_fds=(relay_read, relay_write, exit_read),
                **options,
            )
            children.append(child)
            for fd in (relay_write, relay_read, exit_read):
                os.close(fd)
                private_fds.remove(fd)
            relay_exit.append(exit_write)
            private_fds.remove(exit_write)
            thread = threading.Thread(target=actor, args=(client_read, client_write), daemon=True)
            threads.append(thread)
            thread.start()
            private_fds.remove(client_read)
            private_fds.remove(client_write)
            return child
        finally:
            for fd in private_fds:
                try:
                    os.close(fd)
                except OSError:
                    pass

    original_begin_exchange = op.NativeHostSources.begin_exchange

    def observe_host_exchange(source, *, channel):
        # Keep every original host admission/guard. This only observes the
        # actual HOST channel that the product installs for this exchange.
        result = original_begin_exchange(source, channel=channel)
        state["host_channel"] = source.channel
        return result

    monkeypatch.setattr(op.NativeHostSources, "begin_exchange", observe_host_exchange)

    class OwnedWorkerDocker(DockerBoundary):
        def run(self, argv, *, timeout):
            # The synthetic PID1 wrapper retires only after actual client work
            # and the host's owned preparation descriptors have been closed.
            publication = state.get("publication")
            if actor_done.is_set() and publication is not None:
                try:
                    info = os.fstat(publication.store.fd)
                    retired = (info.st_dev, info.st_ino) != state["publication_fd_identity"]
                except (OSError, TypeError):
                    retired = True
                host_channel = state.get("host_channel")
                host_eof = (
                    type(host_channel) is op.PipeFrameChannel
                    and host_channel.role == "HOST"
                    and host_channel.exchange == "SINGLE_RPC"
                    and host_channel.exchange_id == exchange
                    and host_channel.position == len(host_channel.steps) == 5
                    and host_channel.closed
                    and host_channel.read_fd
                    is host_channel.write_fd
                    is host_channel.error_fd
                    is None
                )
                if (action == "start" and retired) or (action != "start" and host_eof):
                    # The actor's local EOF precedes the host's final guards.
                    # Terminal keeps PID1 running through actual HOST EOF; its
                    # proof namespace remains retained until B3/B4 RETIRE.
                    abort_kind = state.get("hold_declared_relay_for_public_abort")
                    if abort_kind is not None:
                        assert launcher_action == "declare-executor-gone" and action == "finalize"
                        assert abort_kind in {"timeout", "interrupted"} and host_eof and not errors
                        assert state["context"].scientific_finished and state["steps"] == 5
                        assert action_results[-1]["result"]["lifecycle"] == "NOT_DEMONSTRATED"
                        assert action_results[-1]["result"]["binding"] is False
                        assert science.backend.accepted == 0 and children[-1].poll() is None
                        assert len(relay_exit) == 1 and not state.get(
                            "held_declared_relay_at_worker_wait"
                        )
                        state["held_declared_relay_at_worker_wait"] = True
                        # Terminal namespace is retained through worker wait/B3 RETIRE.
                        # On abort the actual context manager closes only its owned FD;
                        # existing namespace directories and published bytes stay intact.
                        assert not state["publication"].store.resolver._closed
                        codec = boot.apparatus["ex10_session_v2"]
                        checkpoint = boot.checkpoint()
                        pending = codec.load_official_session(
                            h.root / mod.SESSION_FILE,
                            context=boot.context(),
                            profile=h.profile,
                            expected_tip=checkpoint[0],
                            expected_count=checkpoint[1],
                        )
                        state["actual_declared_abort_pending_boundary"] = {
                            "b2_raw": pending._bytes,
                            "checkpoint": checkpoint,
                            "checkpoint_state": dict(h.checkpoint),
                            "physical_home": _v2_private_tree_bytes(h.home),
                            "rpc": list(transport.calls),
                        }
                        if abort_kind == "interrupted":
                            assert threading.current_thread() is threading.main_thread()
                            assert signal.getsignal(signal.SIGTERM) is mod.base._interrupt
                            assert signal.SIGTERM not in signal.pthread_sigmask(
                                signal.SIG_BLOCK, []
                            )
                            state["actual_declared_abort_signals"] = [(os.getpid(), signal.SIGTERM)]
                            os.kill(os.getpid(), signal.SIGTERM)
                            raise AssertionError("original public SIGTERM handler must interrupt")
                        # Original action/host absolute budget expires; no replacement
                        # Aborted, patched result/guard or renewed deadline is admitted.
                        c.now["mono"] += mod.ACTIONS["declare-executor-gone"].timeout + 1
                        return super().run(argv, timeout=timeout)
                    state["worker_retired"] = True
                    if relay_exit:
                        os.write(relay_exit[0], b"0" if not errors else b"1")
                        os.close(relay_exit.pop())
            return super().run(argv, timeout=timeout)

    def raw_bridge(**facts):
        state["publication"] = facts["publication"]
        info = os.fstat(facts["publication"].store.fd)
        state["publication_fd_identity"] = (info.st_dev, info.st_ino)
        return prepare_bridge(**facts)

    def holder(**facts):
        assert facts["process"].container_id == cid and facts["role"] == "SESSION_EXECUTOR"
        assert (
            facts["proposal"].to_dict()["holder_public_key_hex"]
            == holder_key.public_key().public_bytes_raw().hex()
        )
        return holder_key.public_key()

    def completion(**facts):
        assert facts["action"] == launcher_action
        assert facts["result_bytes"] == mod.base.canonical_bytes(results[-1])
        if action != "finalize":
            assert facts["terminal"] is None
        else:
            snapshot, retired = facts["terminal"]
            assert (
                op.history.find_run(snapshot, results[-1]["run_id"])["lifecycle_state"]
                == "TERMINAL"
            )
            assert retired["terminal_witness_reference"] is not None
        assert type(facts["response"]) is bytes
        assert op.wire.parse_response_bytes(facts["response"]).to_dict()["status"] == "OK"
        state["completion"].append(facts["written"])

    def forbidden(**facts):
        raise AssertionError("full START must not route domain/cleanup/terminal")

    providers = {name: forbidden for name in mod.NativeActionFactory._PROVIDERS}
    providers.update(
        prepare_oneoff=prepare_plan,
        approve_action=approved,
        approve_creation=approved,
        prepare_envelope=prepare_envelope,
        prepare_bridge=raw_bridge,
        holder_admitter=holder,
        approve_completion=completion,
        approve_terminal=approved,
    )
    factory = mod.NativeActionFactory(
        providers=providers,
        signer=c.factory.signer,
        custody_hmac_key=key,
        runner_factory=OwnedWorkerDocker,
        process_factory=launch,
    )
    boot = c.install(action_factory=factory)
    invocation = replace(
        c.invocation(
            "start-official",
            confirmed=True,
            nonce="d" * 64,
            acknowledged=acknowledged,
            preflight_report=evidence_path / staging,
        ),
        native_bootstrap=boot,
    )
    try:
        yield SimpleNamespace(
            c=c,
            h=h,
            mod=mod,
            boot=boot,
            factory=factory,
            invocation=invocation,
            state=state,
            errors=errors,
            results=results,
            children=children,
            threads=threads,
            phases=phases,
            b3_floor=b3_floor,
            transport=transport,
            advance=advance_to_action,
            science=science,
            action_results=action_results,
            prepare_precondition=prepare_recovery_precondition,
            prepare_refusal_seal=lambda: prepare_recovery_precondition("seal-declare"),
            store=lambda: state["store"],
        )
    finally:
        stop.set()
        context = state["context"]
        if context is not None:
            context.channel.close()
        for fd in relay_exit:
            try:
                os.close(fd)
            except OSError:
                pass
        relay_exit.clear()
        for child in children:
            mod.NativeWorkerAttachment._reap(child)
        for thread in threads:
            thread.join(timeout=5)
        assert all(not thread.is_alive() for thread in threads)
        assert all(child.poll() is not None for child in children)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
@pytest.mark.parametrize("target", ["drain", "declare-executor-gone"])
def test_full_factory_recovery_from_authentic_sdk_component_precondition(
    native_factory_recovery_precondition_case, target
):
    c = native_factory_recovery_precondition_case
    first = c.mod.execute(c.invocation)
    assert first["launcher_exit"] == 0 and not c.errors
    assert c.action_results[0]["steps"] == 9
    precondition = c.prepare_precondition(target)
    assert precondition["actual_exit137"] and precondition["host_single5_completed"]
    invocation = c.advance(target)
    outcome = c.mod.execute(invocation)
    assert outcome["launcher_exit"] == 0 and not c.errors
    assert c.action_results[-1]["steps"] == 5
    if target == "drain":
        assert c.results[-1]["requests_sent_during_drain"] == 0
        assert c.science.backend.accepted == 50
        for action in ("reconcile", "finalize"):
            invocation = c.advance(action)
            outcome = c.mod.execute(invocation)
            assert outcome["launcher_exit"] == 0 and not c.errors
            assert c.action_results[-1]["steps"] == 5
    else:
        assert c.state["exact_previous_scientific_death_approved"]
        assert c.results[-1]["decision"] == "NOT_DEMONSTRATED"
        assert c.results[-1]["binding"] is False
        assert c.science.backend.accepted == 0
    assert c.state["local_seal_checked"]
    assert c.transport.calls.count("RESERVE") == c.transport.calls.count("COMMIT_OPEN") == 1
    assert c.transport.calls.count("B3_CAS") == 2
    assert c.transport.calls.count("RETIRE_TERMINAL") == 1
    final = c.boot.load(invocation)
    assert final.header == c.c.initial.header
    assert final.to_dict()["history"][-1]["payload"]["event"] == "complete-" + (
        "finalize" if target == "drain" else target
    )
    assert final.to_dict()["in_flight"] == []
    assert all(child.poll() == 0 for child in c.children)


def _native_fixture_candidate_payload_reader(authority, candidate_store, sql):
    """Explicit synthetic approval of exactly one extra actual private store.

    The original authority-root reader remains unchanged. Published B3 payloads
    in the separately owned candidate store use its actual retained no-follow,
    owner/mode/nlink/identity guards; no broader host path fallback is admitted.
    """
    original = authority.read_payload

    def read(path):
        sql.require(type(path) is str, "UNSAFE_PATH")
        if Path(path).parent == Path(candidate_store.root):
            return candidate_store.read(path)
        return original(path)

    return read


@pytest.mark.parametrize(
    "fault",
    ["candidate", "original", "foreign", "symlink", "hardlink", "mode", "ancestor-swap"],
)
def test_native_fixture_candidate_payload_reader_retains_owned_store_guards(v2_native_host, fault):
    h = v2_native_host
    durable, sql = h.apparatus["ex10_authority_backend"], h.apparatus["ex10_authority_sqlite"]
    path = h.home / "reader-candidate-store"
    path.mkdir(mode=0o700)
    info = path.stat()
    identity = durable.FileIdentity(
        info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)
    )
    store = durable.PrivateStore(str(path), identity)
    try:
        candidate = path / "payload.json"
        data = b'{"synthetic-approved-candidate":true}'
        store.create_exact(str(candidate), data)
        # Original fixture root rejects this legitimately separate candidate;
        # preserve that oracle, then qualify through the explicit private store.
        with pytest.raises(sql.AuthorityError, match="^UNSAFE_PATH$"):
            h.authority.read_payload(str(candidate))
        reader = _native_fixture_candidate_payload_reader(h.authority, store, sql)
        if fault == "candidate":
            assert reader(str(candidate)) == data
            return
        if fault == "original":
            source = h.authority.root / "reader-original.json"
            source.write_bytes(data)
            source.chmod(0o600)
            assert reader(str(source)) == h.authority.read_payload(str(source)) == data
            return
        if fault == "foreign":
            candidate = h.home / "reader-foreign.json"
            candidate.write_bytes(data)
            candidate.chmod(0o600)
        elif fault == "symlink":
            candidate.unlink()
            candidate.symlink_to(h.authority.database)
        elif fault == "hardlink":
            os.link(candidate, path / "alias.json")
        elif fault == "mode":
            candidate.chmod(0o644)
        else:
            path.rename(h.home / "reader-displaced-store")
            path.mkdir(mode=0o700)
            candidate.write_bytes(data)
            candidate.chmod(0o600)
        with pytest.raises(sql.AuthorityError):
            reader(str(candidate))
    finally:
        store.close()


@pytest.fixture
def native_start_admission_migration_case(v2_source_checkout, request, monkeypatch):
    """Use the actual joined native fixture while preserving the four original IDs."""
    from contextlib import ExitStack, contextmanager

    variants = {
        (
            "test_start_official_runs_start_in_official_mode_" "and_binds_the_run_id"
        ): "typed-b4-empty-history",
        "test_start_passes_only_exact_acknowledgements": "typed-b4-legacy-ack-other",
        (
            "test_start_forwards_and_records_the_exact_" "acknowledgement_of_run_1"
        ): "typed-b4-legacy-ack-run1",
        "test_start_without_acknowledgement_records_none": "typed-b4-empty-history",
    }
    with ExitStack() as owned:
        # Register fixture finalizers in the same stack as their owners. LIFO
        # closes actors/stores before the host and its checkout are removed.
        proxy = SimpleNamespace(param=variants[request.node.name], addfinalizer=owned.callback)
        host = owned.enter_context(
            contextmanager(v2_native_host.__wrapped__)(v2_source_checkout, proxy, monkeypatch)
        )
        resource = owned.enter_context(
            contextmanager(native_factory_resource_case.__wrapped__)(host, monkeypatch, proxy)
        )
        case = owned.enter_context(
            contextmanager(native_factory_full_case.__wrapped__)(resource, proxy, monkeypatch)
        )
        yield case


def _assert_native_start_admission_completed(c, expected_ack):
    """One real mechanical START; retain ACK custody and every prior authority row."""
    from apps.experiments_app.tests import test_ex10_executor as helper

    op = c.boot.apparatus["ex10_operational_context"]
    before = c.boot.read_authority(c.h.authority.descriptor_ref)
    historical = op.history.list_official_runs(before)
    assert tuple(sorted(run["run_id"] for run in historical)) == expected_ack
    assert c.invocation.acknowledged == expected_ack
    result = c.mod.execute(c.invocation)
    assert result["classification"] == "COHERENT" and result["launcher_exit"] == 0
    assert not c.errors and len(c.results) == len(c.action_results) == 1
    scientific = c.results[0]
    context = c.state["context"]
    assert c.state["effects"] == ["create", "start"] and c.state["steps"] == 9
    assert c.state["completion"] == [False, True]
    assert context.finished and context.scientific_finished and context.consumed
    assert scientific["run_id"] == context.proposal["run_id"]
    assert scientific["evidence_mode"] == "OFFICIAL" and scientific["phase"] == "PREPARED"
    cfg = c.state["cfg"]
    expected_arguments = [
        "start",
        "--operational-context-stdin",
        "--expected-git-sha",
        c.boot.expected_git_sha,
        "--source-root",
        "/s11-full-source",
        "--nonce",
        "d" * 64,
        "--evidence-mode",
        "OFFICIAL",
        "--confirm-new-run",
    ]
    for run_id in expected_ack:
        expected_arguments += ["--acknowledge-not-demonstrated", run_id]
    assert helper.executor._native_command_arguments(cfg) == expected_arguments
    assert cfg["command_options"]["acknowledge_not_demonstrated"] == list(expected_ack)
    rows = context.reserve_request.request.to_dict()["payload"]["acknowledgments"]
    assert tuple(row["run_id"] for row in rows) == expected_ack
    for row in rows:
        target = op.history.verify_ack_target(before, row["run_id"])
        assert target["recognizable"]
        assert row == {
            "run_id": row["run_id"],
            "authority_revision": target["revision"],
            "terminal_evidence_reference": target["evidence_reference"],
        }
    assert cfg["request_payload"]["acknowledgments"] == rows
    assert context.reservation["acknowledgment_digest"] == op.sql.digest(rows)
    assert context.permit.to_dict()["acknowledgment_digest"] == op.sql.digest(rows)
    store = c.state["store"]
    state = helper.executor.load_existing_state_readonly(store, scientific["run_id"])
    assert state is not None and not state.unreadable and len(state.records) == 4
    assert not state.finalized
    start_record = state.record(helper.executor.ART_START)
    start_raw = (state.run_root / start_record["relative_path"]).read_bytes()
    assert (
        hashlib.sha256(start_raw).hexdigest()
        == start_record["sha256"]
        == scientific["start_sha256"]
    )
    assert json.loads(start_raw)["acknowledged_not_demonstrated"] == list(expected_ack)
    assert json.loads(start_raw)["evidence_mode"] == "OFFICIAL"
    session = c.boot.load(c.invocation)
    value = session.to_dict()
    assert value["official_run_id"] == scientific["run_id"]
    assert value["state"] == {
        "known_phase": "PREPARED",
        "uncertain": False,
        "blocked": False,
        "next_allowed_actions": [
            "abandon-official-pre-t0",
            "execute-official",
            "inspect",
            "status",
        ],
    }
    assert (
        not value["in_flight"]
        and value["preflight_report_sha256"] == scientific["preflight_sha256"]
    )
    payload = value["history"][-1]["payload"]
    assert payload["event"] == "complete-start-official" and len(payload["output_sha256"]) == 1
    output, digest = next(iter(payload["output_sha256"].items()))
    raw = (c.h.root / output).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == digest and json.loads(raw) == scientific
    after = c.boot.read_authority(c.h.authority.descriptor_ref)
    for run in historical:
        assert op.history.find_run(after, run["run_id"]) == run
    assert len(op.history.list_official_runs(after)) == len(historical) + 1
    assert op.history.find_run(after, scientific["run_id"])["lifecycle_state"] == "OPEN"
    assert op.history.current_revision(after)["revision"] == c.b3_floor.current().revision == 1
    assert c.transport.calls.count("RESERVE") == c.transport.calls.count("COMMIT_OPEN") == 1
    assert c.transport.calls.count("B3_CAS") == 1 and "RETIRE_TERMINAL" not in c.transport.calls
    assert all(child.poll() == 0 for child in c.children)


def _assert_native_post_worker_prior_faults(c, monkeypatch, fault):
    """Assert the original causes after actual SDK completion and private result I/O.

    Every caller owns an independent installed fixture. The unknown returned ID
    is a negative boundary: it must retain the generic rejection. External keys,
    approval and Docker/PID1 observations are synthetic and never official START.
    """
    from apps.experiments_app.tests import test_ex10_executor as helper

    executor = helper.executor
    evidence = Path(c.h.profile["host_roots"]["evidence"]["path"])
    store = helper.EvidenceStore(
        evidence,
        custody_hmac_key=c.factory.custody_key,
        publication_verification_key=c.factory.signer.public_key().public_bytes_raw(),
        rolling_checksums=False,
    )
    probes = executor.ExecutorProbes(
        preflight=lambda options: helper.preflight_report(
            git_sha=c.boot.expected_git_sha,
            checked_at=c.c.now["utc"],
        ),
        dataset=helper.ex10.load_dataset,
        provider=lambda: [],
        sources=lambda root: [],
        fingerprint=executor.executor_fingerprint,
        now=lambda: c.c.now["utc"],
    )
    local = executor.start_run(
        store,
        git_sha=c.boot.expected_git_sha,
        source_root=c.boot.repo_root,
        nonce="native-prior-characterization",
        evidence_mode=helper.LOCAL,
        timezone="UTC",
        probes=probes,
    )
    executor.finalize_run(
        store,
        run_id=local["run_id"],
        git_sha=c.boot.expected_git_sha,
        abandon_reason="synthetic-pre-t0-stop",
        probes=probes,
    )
    prior = executor.prior_ex10_runs(store)
    assert len(prior) == 1 and prior[0].finalized and not prior[0].problems
    assert prior[0].run_id == local["run_id"] and prior[0].evidence_mode == helper.LOCAL
    retained = store.root / "runs" / local["run_id"]
    before = _v2_private_tree_bytes(retained)
    persist = executor.ExecutorOperationalContext.persist_public_result
    mutations = []
    pending = {}
    edges = []
    edge_faults = {
        "returned-existing-cleanup-failure",
        "returned-existing-result-replaced-after-cleanup",
        "returned-existing-checkpoint-after-final-source-close",
        "returned-existing-source-after-final-source-close",
        "returned-existing-host-expiry-after-final-source-close",
    }
    gate = {"armed": False, "pending_ordinal": 0, "active_pending": None}
    original_qualify = c.mod._NativeExistingRunDiagnostic.qualify
    original_close = c.boot.apparatus["ex10_operational_context"].PipeFrameChannel.close
    real_close = os.close

    def qualify_then_arm(diagnostic, *args):
        signal = original_qualify(diagnostic, *args)
        if signal is not None:
            assert signal is diagnostic.signal and type(signal) is c.mod._NativeExistingRunRefusal
            assert not gate["armed"]
            gate["armed"] = True
            exact = diagnostic.original.check_pending_exact

            def pending_scope():
                gate["pending_ordinal"] += 1
                gate["active_pending"] = gate["pending_ordinal"]
                try:
                    return exact()
                finally:
                    gate["active_pending"] = None

            diagnostic.original.check_pending_exact = pending_scope
        return signal

    def close_owned_channel(channel):
        original_close(channel)
        if (
            not gate["armed"]
            or edges
            or fault
            not in {
                "returned-existing-cleanup-failure",
                "returned-existing-result-replaced-after-cleanup",
            }
        ):
            return
        assert channel.closed
        edges.append(fault)
        if fault == "returned-existing-cleanup-failure":
            raise c.mod.LauncherError("synthetic-owned-cleanup-failure")
        path = pending["result_path"]
        raw, old_identity = path.read_bytes(), (path.stat().st_dev, path.stat().st_ino)
        replacement = path.with_name(path.name + ".owned-replacement")
        fd = os.open(replacement, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
        os.replace(replacement, path)
        assert path.read_bytes() == raw
        assert (path.stat().st_dev, path.stat().st_ino) != old_identity

    def close_then_late_fault(fd):
        try:
            target = os.readlink("/proc/self/fd/" + str(fd))
        except OSError:
            target = None
        real_close(fd)
        if not gate["armed"] or edges or gate["active_pending"] != 2 or target != str(c.h.guard):
            return
        assert fault in {
            "returned-existing-checkpoint-after-final-source-close",
            "returned-existing-source-after-final-source-close",
            "returned-existing-host-expiry-after-final-source-close",
        }
        edges.append(fault)
        if fault == "returned-existing-checkpoint-after-final-source-close":
            tip, count = c.h.checkpoint["value"]
            assert (tip, count) == pending["checkpoint"]
            c.h.checkpoint["value"] = (tip, count + 1)
        elif fault == "returned-existing-source-after-final-source-close":
            path = c.boot.repo_root / c.mod.LAUNCHER_PATH
            original = path.read_bytes()
            with path.open("ab") as stream:
                stream.write(b"\n# LOCAL TEST source altered after last qualifier read\n")
            assert path.read_bytes() != original
        else:
            unchanged_utc = c.c.now["utc"]
            c.c.now["mono"] += (
                c.invocation.timeout or c.mod.ACTIONS[c.invocation.action].timeout
            ) + 1
            assert c.c.now["utc"] == unchanged_utc

    if fault in edge_faults:
        monkeypatch.setattr(c.mod._NativeExistingRunDiagnostic, "qualify", qualify_then_arm)
        if fault in {
            "returned-existing-cleanup-failure",
            "returned-existing-result-replaced-after-cleanup",
        }:
            monkeypatch.setattr(
                c.boot.apparatus["ex10_operational_context"].PipeFrameChannel,
                "close",
                close_owned_channel,
            )
        else:
            monkeypatch.setattr(os, "close", close_then_late_fault)

    def publish_then_mutate(context, worker_store, result):
        # Preserve actual admission, SDK completion and private result creation.
        value = persist(context, worker_store, result)
        assert context is c.state["context"] and context.action == "start"
        assert context.finished and context.scientific_finished and context.consumed
        assert result["run_id"] == context.proposal["run_id"] != local["run_id"]
        assert _v2_private_tree_bytes(retained) == before and not mutations
        result_path = worker_store.root / (
            "executor-result." + context.channel.exchange_id + ".json"
        )
        original = result_path.read_bytes()
        assert original == executor.canonical_json_bytes(result)
        pending["result_path"] = result_path
        pending["checkpoint"] = c.boot.checkpoint()
        session_path = c.invocation.session_root / c.mod.SESSION_FILE
        codec = c.boot.apparatus["ex10_session_v2"]
        verified_pending = codec.load_official_session(
            session_path,
            context=c.boot.context(),
            profile=c.h.profile,
            expected_tip=pending["checkpoint"][0],
            expected_count=pending["checkpoint"][1],
        )
        pending["bytes"], pending["session"] = verified_pending._bytes, verified_pending.to_dict()
        if fault.startswith("returned-"):
            returned_run = (
                local["run_id"]
                if fault != "returned-unknown-run"
                else "s11-01234567-89ab-5cde-8f01-23456789abcd"
            )
            assert returned_run != result["run_id"]
            if fault == "returned-unknown-run":
                assert returned_run != local["run_id"]
            altered = {**result, "run_id": returned_run}
            if fault == "returned-existing-extra-problem":
                altered["scenario_id"] = "EX-09"
            result_path.write_bytes(executor.canonical_json_bytes(altered))
            assert _v2_private_tree_bytes(retained) == before
        else:
            marker = retained / "x"
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(b"x")
            after = _v2_private_tree_bytes(retained)
            assert set(after) == set(before) | {"x"}
            assert all(after[name] == facts for name, facts in before.items() if name != ".")
            assert after["."][1:3] == before["."][1:3]
            assert after["x"][0:2] == (b"x", 0o600)
            assert result_path.read_bytes() == original
        mutations.append((context.proposal["run_id"], result_path.read_bytes()))
        return value

    monkeypatch.setattr(
        executor.ExecutorOperationalContext,
        "persist_public_result",
        publish_then_mutate,
    )
    # Failure before the I/O mutation does not satisfy this characterization.
    failure_type = (
        c.mod.Aborted
        if fault == "returned-existing-host-expiry-after-final-source-close"
        else c.mod.NativeActionFailure
    )
    with pytest.raises(failure_type) as failure:
        c.mod.execute(c.invocation)
    assert len(mutations) == 1 and not c.errors and len(c.results) == 1
    actual_payload = json.loads(mutations[0][1])
    payload_problems, _, _ = c.mod.action_problems(
        "start-official",
        actual_payload,
        run_id=mutations[0][0],
        reconciled=None,
    )
    if fault.startswith("returned-"):
        expected_problems = ["run-id-differs"]
        if fault == "returned-existing-extra-problem":
            expected_problems.append("start:scenario_id")
        assert payload_problems == expected_problems
        checkpoint_error = "native-worker-result-intent-changed"
        expected_codes = {
            "returned-existing-run": "start:run-id-not-new",
            "returned-unknown-run": "native-worker-result-invalid",
            "returned-existing-extra-problem": "native-worker-result-invalid",
            "returned-existing-cleanup-failure": "synthetic-owned-cleanup-failure",
            "returned-existing-result-replaced-after-cleanup": "native-worker-result-lease",
            "returned-existing-checkpoint-after-final-source-close": checkpoint_error,
            "returned-existing-source-after-final-source-close": "native-host-input-changed",
        }
        if fault == "returned-existing-host-expiry-after-final-source-close":
            assert failure.value.kind == "timeout"
        else:
            assert failure.value.code == expected_codes[fault]
    else:
        assert payload_problems == []
        # Existing approved original cause: generic unrelated failures cannot
        # establish the missing post-worker prior-content guard.
        assert failure.value.code == "prior-run-changed"
    assert all(child.poll() == 0 for child in c.children)
    assert c.state["steps"] == 9 and c.state["completion"] == []
    assert c.state["effects"] == ["create", "start"]
    # Physical equality with the authentic pending B2 captured before the fault.
    # No new source/CP admission is claimed after those inputs deliberately fail.
    assert (c.invocation.session_root / c.mod.SESSION_FILE).read_bytes() == pending["bytes"]
    session = pending["session"]
    assert session["official_run_id"] is None and len(session["in_flight"]) == 1
    assert session["state"]["uncertain"] and session["state"]["blocked"]
    assert c.transport.calls.count("RESERVE") == 1
    assert c.transport.calls.count("COMMIT_OPEN") == 1
    assert c.transport.calls.count("B3_CAS") == 1
    # Authentic scientific OPEN is retained; a host rejection cannot undo it.
    op = c.boot.apparatus["ex10_operational_context"]
    snapshot = c.boot.read_authority(c.h.authority.descriptor_ref)
    assert op.history.find_run(snapshot, mutations[0][0])["lifecycle_state"] == "OPEN"
    assert c.transport.calls.count("RETIRE_TERMINAL") == 0
    assert edges == ([fault] if fault in edge_faults else [])
    if fault == "returned-existing-checkpoint-after-final-source-close":
        assert c.boot.checkpoint() != pending["checkpoint"]
    if fault in edge_faults:
        assert gate["armed"]


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
@pytest.mark.parametrize(
    "fault",
    [
        "returned-existing-run",
        "returned-unknown-run",
        "returned-existing-extra-problem",
        "returned-existing-cleanup-failure",
        "returned-existing-result-replaced-after-cleanup",
        "returned-existing-checkpoint-after-final-source-close",
        "returned-existing-source-after-final-source-close",
        "returned-existing-host-expiry-after-final-source-close",
        "prior-touched-after-new-run",
    ],
)
def test_v2_native_full_start_rejects_post_worker_prior_faults(
    native_factory_full_case, monkeypatch, fault
):
    """Qualify exact retained-prior causes and the unknown-ID rejection boundary."""
    _assert_native_post_worker_prior_faults(native_factory_full_case, monkeypatch, fault)


@pytest.mark.parametrize("case", ["nd-without-ack", "local-active", "local-corrupt"])
def test_v2_native_start_known_refusal_precedes_all_durable_effects(v2_native_host, case):
    """Real qualified B1/B2/B3 and private custody; no scientific worker is admitted.

    The opaque report is never consumed: these refusals precede worker preparation.
    This characterizes the native boundary; the four original public cases retain
    their separate recovery gate, including authenticated incoming binding history.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    h, mod = v2_native_host, v2_native_host.module
    report = h.home / "unconsumed-preflight.json"
    report.write_bytes(b"{}\n")
    report.chmod(0o600)
    mod.execute(h.invocation())
    original = h.invocation("inspect").native_bootstrap
    calls = []

    def forbidden(**facts):
        calls.append(facts)
        raise AssertionError("known refusal must precede every factory provider")

    def forbidden_runner(*args, **kwargs):
        raise AssertionError("known refusal must not construct a Docker runner")

    key = b"synthetic-refusal-private-custody-" + b"x" * 32
    factory = mod.NativeActionFactory(
        providers={name: forbidden for name in mod.NativeActionFactory._PROVIDERS},
        signer=Ed25519PrivateKey.generate(),
        custody_hmac_key=key,
        runner_factory=forbidden_runner,
        process_factory=forbidden_runner,
    )
    configuration = {
        name: getattr(original, name)
        for name in (
            "repo_root",
            "expected_git_sha",
            "profile_path",
            "global_store",
            "global_name",
            "global_identity",
            "approve",
            "read_authority",
            "read_checkpoint",
            "publish_checkpoint",
        )
    }
    boot = mod.NativeLauncherBootstrap(
        **configuration,
        path_context=json.loads(original.path_context_bytes),
        approval_reference=json.loads(original.approval_reference_bytes),
        session_context=json.loads(original.session_context_bytes),
        action_factory=factory,
    )
    prior_id = h.authority.state["official_runs"][0]["run_id"]
    if case == "nd-without-ack":
        expected = "not-demonstrated-unacknowledged:" + prior_id
    else:
        prior_id = OTHER_RUN
        namespace = h.apparatus["ex10_prior_custody"].__package__
        ev, constants = (sys.modules[namespace + "." + name] for name in ("evidence", "constants"))
        for name, loaded in (("evidence", ev), ("constants", constants)):
            path = boot.repo_root / "backend/apps/experiments_app" / (name + ".py")
            assert loaded.__file__ == str(path)
            assert (
                loaded._s11_verified_source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
            )
        root = Path(h.profile["host_roots"]["evidence"]["path"])
        store = ev.EvidenceStore(
            root,
            custody_hmac_key=key,
            publication_verification_key=Ed25519PrivateKey.generate()
            .public_key()
            .public_bytes_raw(),
        )
        store.persist_json(
            run_id=prior_id,
            scenario_id="EX-10",
            artifact_type="run-manifest-initial",
            kind=constants.ArtifactKind.METADATA,
            classification=constants.Classification.INTERNAL,
            filename="initial.json",
            payload={
                "scenario_id": "EX-10",
                "experiment_id": "S11C3-EX10-LOCAL-TEST-NON-EXPERIMENTAL",
            },
            source="synthetic actual-custody native refusal fixture",
        )
        if case == "local-corrupt":
            signature = root / "runs" / prior_id / "metadata/artifacts.jsonl.hmac"
            signature.write_bytes(b"0" * 64 + b"\n")
            expected = "prior-run-unverifiable:" + prior_id
        else:
            expected = "active-run:" + prior_id
    invocation = h.invocation(
        "start-official",
        confirmed=True,
        nonce="b" * 64,
        preflight_report=report,
        native_bootstrap=boot,
    )
    before = _v2_private_tree_bytes(h.home)
    checkpoint = dict(h.checkpoint)
    result = mod.execute(invocation)
    assert result["classification"] == "REFUSED" and result["launcher_exit"] == 1
    assert result["problems"] == [expected] and result["output_dir"] is None
    assert not calls and _v2_private_tree_bytes(h.home) == before
    assert h.checkpoint == checkpoint
    session = boot.load(h.invocation("inspect", native_bootstrap=boot))
    assert session.to_dict()["official_run_id"] is None
    assert not session.to_dict()["in_flight"]
    assert "start-official" in session.to_dict()["state"]["next_allowed_actions"]
    assert session.to_dict()["history"][-1]["payload"]["event"] == "initialize-session"


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_v2_native_eligible_second_session_refuses_actual_incoming_binding(
    native_factory_execution_chain_case,
):
    """A real fixture SDK result becomes binding after a second B2 initializes.

    Actual owned pipes/executor/SQLite/witness/GOV/B3/B4/custody are exercised.
    External approvals, Docker/PID1 observations and the existing scientific
    harness remain synthetic; this never qualifies an official deployment/run.
    """
    from dataclasses import replace

    c = native_factory_execution_chain_case
    h, mod, original = c.h, c.mod, c.boot
    second_root = h.home / "independent-eligible-session"
    checkpoint = {"value": None, "publishes": 0}
    providers_called = []
    expected_approval = Path(json.loads(original.approval_reference_bytes)["path"]).read_bytes()

    def approve_second(**facts):
        assert h.approvals["allowed"]
        assert facts["profile"].to_dict() == h.profile
        assert facts["session_root"] == str(second_root)
        assert facts["source_revision"] == original.expected_git_sha
        assert facts["guard_identity"] == original.global_identity
        assert facts["authority_revision"]["authority_id"] == h.authority.descriptor["authority_id"]
        assert dict(facts["references"])["runtime_profile_approval_reference"] == expected_approval

    def read_checkpoint():
        return checkpoint["value"]

    def publish_checkpoint(expected, replacement):
        assert checkpoint["value"] == expected
        checkpoint["value"] = replacement
        checkpoint["publishes"] += 1

    def forbidden(**facts):
        providers_called.append(facts)
        raise AssertionError("binding refusal must precede every second-session provider")

    def forbidden_process(*args, **kwargs):
        raise AssertionError("binding refusal must not construct Docker or a worker process")

    factory = mod.NativeActionFactory(
        providers={name: forbidden for name in mod.NativeActionFactory._PROVIDERS},
        signer=c.factory.signer,
        custody_hmac_key=c.factory.custody_key,
        runner_factory=forbidden_process,
        process_factory=forbidden_process,
    )
    context = json.loads(original.session_context_bytes)
    context["parent"] = str(second_root)
    context["prior_session_roots"] = [*context["prior_session_roots"], str(h.root)]
    paths = json.loads(original.path_context_bytes)
    paths["prior_session_roots"] = [*paths["prior_session_roots"], str(h.root)]
    configuration = {
        name: getattr(original, name)
        for name in (
            "repo_root",
            "expected_git_sha",
            "profile_path",
            "global_store",
            "global_name",
            "global_identity",
            "read_authority",
        )
    }
    second = mod.NativeLauncherBootstrap(
        **configuration,
        path_context=paths,
        approval_reference=json.loads(original.approval_reference_bytes),
        session_context=context,
        approve=approve_second,
        read_checkpoint=read_checkpoint,
        publish_checkpoint=publish_checkpoint,
        action_factory=factory,
    )
    initial_invocation = replace(
        c.invocation,
        action="initialize-session",
        nonce=None,
        preflight_report=None,
        acknowledged=(),
        confirmed=False,
        session_root=second_root,
        native_bootstrap=second,
    )
    initialized = mod.execute(initial_invocation)
    assert initialized["launcher_exit"] == 0
    initial = second.load(initial_invocation)
    assert initial.to_dict()["official_run_id"] is None
    assert "start-official" in initial.to_dict()["state"]["next_allowed_actions"]
    assert checkpoint["publishes"] == 1 and not providers_called

    first = mod.execute(c.invocation)
    assert first["launcher_exit"] == 0 and not c.errors
    for action in ("execute-official", "reconcile", "finalize"):
        invocation = c.advance(action)
        outcome = mod.execute(invocation)
        assert outcome["launcher_exit"] == 0 and not c.errors
        assert c.action_results[-1]["steps"] == 5
        assert c.state["completion"] == [False, True]
    terminal = c.action_results[-1]["result"]
    run_id = terminal["run_id"]
    assert terminal["decision"] in {"PASS", "FAIL"} and terminal["binding"] is True
    assert c.state["local_seal_checked"] and c.state["actual_emission_proof_admitted"]
    assert c.science.backend.accepted == 50 and all(token.released for token in c.science.tokens)
    assert c.state["store"].custody_hmac_key == factory.custody_key
    op = second.apparatus["ex10_operational_context"]
    snapshot = second.read_authority(h.authority.descriptor_ref)
    row = op.history.find_run(snapshot, run_id)
    assert row["lifecycle_state"] == "TERMINAL" and row["binding"] is True
    assert row["result"] == terminal["decision"]
    assert op.history.first_valid_status(snapshot)["status"] == terminal["decision"] + "_BINDING"
    assert c.transport.calls.count("RESERVE") == c.transport.calls.count("COMMIT_OPEN") == 1
    assert c.transport.calls.count("B3_CAS") == 2
    assert c.transport.calls.count("RETIRE_TERMINAL") == 1
    assert all(child.poll() == 0 for child in c.children)

    # ACK only the genuine historical ND carried by the unchanged fixture.
    acknowledged = tuple(
        run["run_id"]
        for run in op.history.list_official_runs(snapshot)
        if run["lifecycle_state"] == "TERMINAL" and run["result"] == "NOT_DEMONSTRATED"
    )
    assert run_id not in acknowledged
    attempt = replace(
        c.invocation, session_root=second_root, native_bootstrap=second, acknowledged=acknowledged
    )
    before = _v2_private_tree_bytes(h.home)
    checkpoint_before = dict(checkpoint)
    transport_before = list(c.transport.calls)
    actual = mod.execute(attempt)
    assert actual["classification"] == "REFUSED" and actual["launcher_exit"] == 2
    assert actual["problems"] == ["binding-run-exists:" + run_id]
    assert actual["output_dir"] is None and not providers_called
    assert _v2_private_tree_bytes(h.home) == before
    assert checkpoint == checkpoint_before and c.transport.calls == transport_before
    last = second.load(attempt)
    assert last._bytes == initial._bytes and last.to_dict()["official_run_id"] is None
    assert not last.to_dict()["in_flight"]
    assert "start-official" in last.to_dict()["state"]["next_allowed_actions"]


@pytest.mark.parametrize(
    "fault",
    [
        "identical-bytes-replacement",
        "parent-replacement",
        "restored-mtime",
        "hardlink",
        "symlink-parent",
    ],
)
def test_v2_native_original_source_lease_rejects_physical_substitution(tmp_path, fault):
    """Real OS leases reject replacements even when bytes or mtime match."""
    directory = tmp_path / "owned-source"
    directory.mkdir(mode=0o700)
    path = directory / "source.py"
    path.write_bytes(b"original\n")
    path.chmod(0o600)
    raw, lease = official._capture_native_source(path, error_type=official.LauncherError)
    assert official._native_check_source_lease(path, lease, code="test-source-changed") is None
    if fault == "identical-bytes-replacement":
        replacement = directory / "owned-replacement"
        replacement.write_bytes(raw)
        replacement.chmod(0o600)
        os.replace(replacement, path)
        assert path.read_bytes() == raw
    elif fault in {"parent-replacement", "symlink-parent"}:
        retained = tmp_path / "retained-original-parent"
        directory.rename(retained)
        if fault == "symlink-parent":
            directory.symlink_to(retained, target_is_directory=True)
        else:
            directory.mkdir(mode=0o700)
            path.write_bytes(raw)
            path.chmod(0o600)
        assert path.read_bytes() == raw
    elif fault == "restored-mtime":
        original = path.stat()
        path.write_bytes(b"modified\n")
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        assert path.stat().st_mtime_ns == original.st_mtime_ns
        assert path.stat().st_ctime_ns != original.st_ctime_ns
    else:
        os.link(path, directory / "owned-hardlink")
        assert path.stat().st_nlink == 2 and path.read_bytes() == raw
    with pytest.raises(official.LauncherError, match="^test-source-changed$"):
        official._native_check_source_lease(path, lease, code="test-source-changed")


def test_v2_native_bootstrap_retains_readonly_original_apparatus(v2_native_host):
    """An entry substitution cannot become a newly adopted native module."""
    h = v2_native_host
    bootstrap = h.invocation().native_bootstrap
    original = bootstrap.apparatus["ex10_runtime_profile"]
    with pytest.raises(TypeError):
        bootstrap.apparatus["ex10_runtime_profile"] = SimpleNamespace()
    assert bootstrap.apparatus["ex10_runtime_profile"] is original
    _, *_, recheck = bootstrap.qualify(h.invocation())
    assert recheck.check_source_leases_current() is None
    assert bootstrap.apparatus["ex10_runtime_profile"] is original


@pytest.mark.parametrize("action", ["execute-official", "status"])
@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
@pytest.mark.parametrize("fault", ["original-source-inode", "authority-expiry", "host-deadline"])
def test_v2_native_known_phase_refusal_preserves_original_guard_causes(
    native_factory_resource_case,
    monkeypatch,
    tmp_path,
    fault,
    action,
):
    """Faults after final qualification exercise actual earlyguard fences, not providers."""
    c = native_factory_resource_case
    invocation = c.invocation(action, confirmed=True)
    session_path = invocation.session_root / c.mod.SESSION_FILE
    original_session = session_path.read_bytes()
    checkpoint = dict(c.h.checkpoint)
    before = _v2_private_tree_bytes(c.h.root)
    commands, effects, approvals = (
        list(c.state["commands"]),
        list(c.state["effects"]),
        list(c.approvals),
    )
    history = c.boot.apparatus["ex10_history_authority"]
    coordination = c.boot.apparatus["ex10_operational_context"].coordination
    approval_start = c.h.approvals["calls"]
    initial_mono = c.now["mono"]
    deadline = initial_mono + (invocation.timeout or c.mod.ACTIONS[invocation.action].timeout)
    original_clock = history._now
    observed = {"armed": 0, "reads": 0, "injected": 0, "expiry": None}
    source = c.boot.repo_root / "backend/apps/experiments_app/ex10_prior_custody.py"
    original_source = source.read_bytes()
    original_stat = source.stat()
    replacement = tmp_path / "owned-byte-identical-source.py"

    def arm_on_final_factory_qualification(facts):
        # This test calls the actual installed Factory API directly. Its two
        # bootstrap.load qualifications precede its final qualify(session).
        if c.h.approvals["calls"] == approval_start + 3:
            assert observed["armed"] == 0
            observed["armed"] = 1
            observed["expiry"] = facts["authority_revision"]["expires_at"]

    def actual_clock_with_owned_fault():
        if not observed["armed"]:
            return original_clock()
        observed["reads"] += 1
        if observed["reads"] == 3:
            observed["injected"] += 1
            if fault == "original-source-inode":
                replacement.write_bytes(original_source)
                replacement.chmod(stat.S_IMODE(original_stat.st_mode))
                os.replace(replacement, source)
                changed = source.stat()
                assert source.read_bytes() == original_source
                assert changed.st_ino != original_stat.st_ino and changed.st_nlink == 1
                assert stat.S_IMODE(changed.st_mode) == stat.S_IMODE(original_stat.st_mode)
            elif fault == "authority-expiry":
                return observed["expiry"]
            else:
                c.now["mono"] = deadline
        return coordination._utc(c.now["utc"])

    c.h.approvals["hook"] = arm_on_final_factory_qualification
    monkeypatch.setattr(history, "_now", actual_clock_with_owned_fault)
    if fault == "original-source-inode":
        with pytest.raises(c.mod.LauncherError, match="^native-host-input-changed$") as failure:
            c.factory.execute(invocation)
        assert not isinstance(failure.value, c.mod.NativeActionFailure)
    elif fault == "authority-expiry":
        with pytest.raises(
            c.mod.NativeActionFailure, match="^native-factory-uncertain$"
        ) as failure:
            c.factory.execute(invocation)
        cause = failure.value.__context__
        assert type(cause) is history.sql.AuthorityError and cause.code == "EXPIRED_OBSERVATION"
    else:
        with pytest.raises(c.mod.Aborted) as failure:
            c.factory.execute(invocation)
        assert failure.value.kind == "timeout" and c.now["mono"] == deadline
    assert observed["armed"] == observed["injected"] == 1 and observed["reads"] == 3
    assert c.h.approvals["calls"] == approval_start + 3
    assert session_path.read_bytes() == original_session and c.h.checkpoint == checkpoint
    assert _v2_private_tree_bytes(c.h.root) == before
    assert c.state["commands"] == commands and c.state["effects"] == effects
    assert c.approvals == approvals and c.factory._attempted[-1] is invocation
    assert not c.factory._lock.locked()


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
def test_v2_native_eligible_action_rechecks_actual_competing_intent_before_creation(
    native_factory_full_case,
):
    from dataclasses import replace

    c = native_factory_full_case
    original_prepare = c.factory.providers["prepare_oneoff"]
    competitor = {}

    def prepare_then_begin_actual_competitor(**facts):
        plan = original_prepare(**facts)
        assert not competitor and facts["session"]._bytes == c.c.initial._bytes

        def independent_competitor_approval(**approved):
            assert approved["action"] == "start-official"
            assert approved["session"]._bytes == facts["session"]._bytes
            assert approved["container_name"] == "s11-owned-competing-intent"
            assert approved["output_dir"] == "actions/owned-competing-intent"
            competitor["approved"] = competitor.get("approved", 0) + 1

        intent = c.mod.NativeActionIntent(
            invocation=facts["invocation"],
            bootstrap=facts["invocation"].native_bootstrap,
            container_name="s11-owned-competing-intent",
            output_dir="actions/owned-competing-intent",
            approve_action=independent_competitor_approval,
        )
        pending = intent.begin()
        competitor.update(
            pending=pending,
            checkpoint=dict(c.h.checkpoint),
            private=_v2_private_tree_bytes(c.h.root),
            effects=list(c.state["effects"]),
            calls=list(c.transport.calls),
        )
        return plan

    providers = dict(c.factory.providers)
    providers["prepare_oneoff"] = prepare_then_begin_actual_competitor
    factory = c.mod.NativeActionFactory(
        providers=providers,
        signer=c.factory.signer,
        custody_hmac_key=c.factory.custody_key,
        runner_factory=c.factory.runner,
        process_factory=c.factory.process_factory,
    )
    boot = c.c.install(action_factory=factory)
    invocation = replace(c.invocation, native_bootstrap=boot)
    with pytest.raises(c.mod.NativeActionFailure, match="^native-intent-state-blocked$"):
        c.mod.execute(invocation)
    assert competitor["approved"] == 1
    pending = competitor["pending"]
    assert pending.to_dict()["in_flight"]
    assert (c.h.root / c.mod.SESSION_FILE).read_bytes() == pending._bytes
    assert c.h.checkpoint == competitor["checkpoint"]
    assert _v2_private_tree_bytes(c.h.root) == competitor["private"]
    assert c.state["effects"] == competitor["effects"] == []
    assert c.transport.calls == competitor["calls"]
    assert not c.state["created"] and not c.state["running"]
    assert c.state["completion"] == [] and not c.children and not c.action_results and not c.errors
    assert not factory._lock.locked() and factory._attempted == [invocation]


def _v2_native_chain_status_reader(c):
    """Actual SDK STATUS; only approved Docker metadata/process boundary synthetic."""
    import copy
    from dataclasses import replace

    from apps.experiments_app.tests import test_ex10_executor as helper

    assert all(child.poll() == 0 for child in c.children)
    assert all(not thread.is_alive() for thread in c.threads) and not c.errors
    run = c.boot.load(c.invocation).to_dict()["official_run_id"]
    assert run == c.action_results[0]["result"]["run_id"]
    store = c.state["store"]
    assert Path(store.root) == Path(c.h.profile["host_roots"]["evidence"]["path"])
    plan = copy.deepcopy(c.c.plan)
    argv = (
        "docker",
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
    state = {"commands": [], "sdk_outputs": [], "after_read": None}
    resource_runner = c.c.factory.runner

    class StatusDocker:
        def __init__(self, cwd, environment):
            self.metadata = resource_runner(cwd, environment)

        def run(self, command, *, timeout):
            if tuple(command[:2]) == ("docker", "exec"):
                assert tuple(command) == argv and timeout > 0
                state["commands"].append(tuple(command))
                payload = helper.executor.run_status(store, run)
                assert payload["run_id"] == run and payload["exists"] is True
                state["sdk_outputs"].append(copy.deepcopy(payload))
                if state["after_read"] is not None:
                    state["after_read"]()
                return c.mod.Completed(0, c.mod.base.canonical_bytes(payload), b"")
            return self.metadata.run(command, timeout=timeout)

    def domain_plan(**facts):
        assert facts["session"].to_dict()["official_run_id"] == run
        assert facts["profile"].to_dict() == c.h.profile
        return {"container": copy.deepcopy(plan)}

    providers = dict(c.factory.providers)
    providers.update(
        prepare_domain=domain_plan, approve_domain=c.c.factory.providers["approve_domain"]
    )
    factory = c.mod.NativeActionFactory(
        providers=providers,
        signer=c.factory.signer,
        custody_hmac_key=c.factory.custody_key,
        runner_factory=StatusDocker,
        process_factory=c.factory.process_factory,
    )
    boot = c.c.install(action_factory=factory)

    def invocation():
        return replace(c.c.invocation("status"), native_bootstrap=boot)

    return SimpleNamespace(
        factory=factory, boot=boot, invocation=invocation, state=state, argv=argv
    )


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
@pytest.mark.parametrize("fault", ["emfile", "eio", "proc-context", "deadline"])
def test_v2_native_status_final_read_errors_preserve_uncertainty_and_abort(
    native_factory_execution_chain_case, monkeypatch, fault
):
    import errno
    import fcntl

    c = native_factory_execution_chain_case
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0
    for thread in c.threads:
        thread.join(timeout=5)
    reader = _v2_native_chain_status_reader(c)
    before = _v2_private_tree_bytes(c.h.home)
    checkpoint = c.h.checkpoint["value"]
    original = c.mod._native_prior_tree_digest
    calls = []

    def descriptor_inventory():
        values = {}
        for name in os.listdir("/proc/self/fd"):
            try:
                info = os.fstat(int(name))
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise
            else:
                values[int(name)] = (info.st_dev, info.st_ino, info.st_mode)
        return values

    def fail_final_read(fd, *, check_current):
        calls.append(fd)
        if len(calls) == 2:
            assert len(reader.state["sdk_outputs"]) == 1
            if fault == "deadline":
                c.c.now["mono"] += c.mod.ACTIONS["status"].timeout + 1
                check_current()  # Actual unchanged host deadline; no fake abort.
                raise AssertionError("original deadline must deny")
            if fault == "proc-context":
                raise ValueError("prior-proc-context")
            raise OSError(errno.EMFILE if fault == "emfile" else errno.EIO, "owned read fault")
        assert len(calls) == 1
        return original(fd, check_current=check_current)

    monkeypatch.setattr(c.mod, "_native_prior_tree_digest", fail_final_read)
    descriptors = descriptor_inventory()
    expected = c.mod.Aborted if fault == "deadline" else c.mod.NativeActionFailure
    with pytest.raises(expected) as denied:
        c.mod.execute(reader.invocation())
    if fault == "deadline":
        assert denied.value.kind == "timeout"
    else:
        assert denied.value.code == "native-factory-final-observation-expired"
        assert denied.value.code != "evidence-changed-by-read"
    assert len(calls) == 2 and reader.state["commands"] == [reader.argv]
    assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint["value"] == checkpoint
    assert descriptor_inventory() == descriptors and not reader.factory._lock.locked()
    lock = Path(c.state["store"].root) / ".s11-custody.lock"
    fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    assert len(c.children) == len(c.action_results) == 1
    assert c.action_results[0]["steps"] == 9 and c.state["completion"] == [False, True]
    assert all(child.poll() == 0 for child in c.children) and not c.errors
    assert c.science.backend.accepted == 0


@pytest.mark.parametrize("fault", ["foreign-id", "foreign-name", "foreign-project"])
def test_v2_native_profile_project_name_and_id_reject_foreign_before_start(
    v2_oneoff_control, fault
):
    """Exact identity component guard; no SDK START or uncertainty recovery claim."""
    c = v2_oneoff_control
    c.control.create()
    before = _v2_private_tree_bytes(c.host.root)
    checkpoint = dict(c.host.checkpoint)

    def alter(actual):
        if fault == "foreign-id":
            actual["Id"] = "9" * 64
        elif fault == "foreign-name":
            actual["Name"] = "/same-project-foreign-resource"
        else:
            actual["Config"]["Labels"]["com.docker.compose.project"] = "s11-foreign-project"

    c.observations["alter"] = alter
    with pytest.raises(c.host.module.LauncherError, match="^native-oneoff-observation-invalid$"):
        c.control.start()
    assert [kind for kind, _ in c.events] == ["create", "inspect"]
    assert not c.control.start_attempted and not c.control.started
    assert _v2_private_tree_bytes(c.host.root) == before and c.host.checkpoint == checkpoint


def _v2_native_gone_negative_invocation(c):
    from dataclasses import replace

    return replace(c.c.invocation("declare-executor-gone", confirmed=True), native_bootstrap=c.boot)


def _v2_native_assert_gone_blocked_without_effects(c, capsys):
    """Known-phase admission only; no executor death or recovery qualification."""
    invocation = _v2_native_gone_negative_invocation(c)
    original = c.boot.load(invocation)
    before = _v2_private_tree_bytes(c.h.home)
    checkpoint = dict(c.h.checkpoint)
    commands, effects, rpc = (
        list(c.c.state["commands"]),
        list(c.state["effects"]),
        list(c.transport.calls),
    )
    children, results, actions = list(c.children), list(c.results), list(c.action_results)
    attempts = len(c.factory._attempted)
    with pytest.raises(c.mod.LauncherError, match="^native-intent-state-blocked$") as failure:
        c.mod.execute(invocation)
    assert not isinstance(failure.value, c.mod.NativeActionFailure)
    assert len(c.factory._attempted) == attempts + 1 and not c.factory._lock.locked()
    assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint
    assert c.boot.load(invocation)._bytes == original._bytes
    assert c.c.state["commands"] == commands and c.state["effects"] == effects
    assert c.transport.calls == rpc
    assert c.children == children and c.results == results and c.action_results == actions
    capsys.readouterr()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        code = c.mod.main(
            [
                "declare-executor-gone",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
                c.mod.ACTIONS["declare-executor-gone"].confirmation,
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    output = capsys.readouterr()
    assert code == 3 and output.out == ""
    assert output.err == "S11_EX10_OFFICIAL_LAUNCHER_CONFIG: native-intent-state-blocked\n"
    assert len(c.factory._attempted) == attempts + 2 and not c.factory._lock.locked()
    assert _v2_private_tree_bytes(c.h.home) == before and c.h.checkpoint == checkpoint
    assert c.boot.load(invocation)._bytes == original._bytes
    assert c.c.state["commands"] == commands and c.state["effects"] == effects
    assert c.transport.calls == rpc
    assert c.children == children and c.results == results and c.action_results == actions


@pytest.mark.parametrize(
    ("kind", "v2_native_host"),
    [
        pytest.param("timeout", "typed-b4-historic-run", id="timeout"),
        pytest.param("interrupted", "typed-b4-historic-run", id="interrupted"),
    ],
    indirect=["v2_native_host"],
)
def test_v2_native_declare_owned_close_error_overrides_actual_public_abort(
    native_factory_recovery_precondition_case, capsys, monkeypatch, kind
):
    """Actual SDK DECLARE + live owned relay abort5; keep uncertainty, no retry.

    SDK local ND is retained; B3 retirement never occurs. This does not recover
    an interrupted Factory intent or cover the legacy v1 rm-f footprint.
    """
    import fcntl
    import inspect

    real_close, real_fstat = os.close, os.fstat
    faults, owned = [], []

    def fail_one_actual_owned_close(fd):
        caller = inspect.currentframe().f_back
        try:
            namespace_close = caller.f_code.co_name == "_create_native_proof_publication"
            rows = caller.f_locals.get("owned", ()) if namespace_close else ()
        finally:
            del caller
        real_close(fd)
        if namespace_close and c.state.get("held_declared_relay_at_worker_wait") and not faults:
            owned.extend(item[0] for row in rows for item in row[-1])
            assert fd in owned
            faults.append(fd)
            raise OSError(5, "LOCAL TEST actual owned close reported I/O failure")

    c = native_factory_recovery_precondition_case
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    previous = c.prepare_precondition("declare-executor-gone")
    assert previous["actual_exit137"] and previous["host_single5_completed"]
    invocation = c.advance("declare-executor-gone")
    c.state["hold_declared_relay_for_public_abort"] = kind
    monkeypatch.setattr(os, "close", fail_one_actual_owned_close)
    capsys.readouterr()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        code = c.mod.main(
            [
                "declare-executor-gone",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
                c.mod.ACTIONS["declare-executor-gone"].confirmation,
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    captured = capsys.readouterr()
    assert code == c.mod.EXIT_LAUNCHER_FAILURE == 4 and captured.out == ""
    assert captured.err == "S11_EX10_OFFICIAL_LAUNCHER_FAILURE: native-proof-namespace-uncertain\n"
    assert len(faults) == 1 and owned
    for fd in owned:
        with pytest.raises(OSError) as closed_fd:
            real_fstat(fd)
        assert closed_fd.value.errno == 9
    monkeypatch.setattr(os, "close", real_close)
    assert all(signal.getsignal(number) is handler for number, handler in handlers.items())
    assert c.state["held_declared_relay_at_worker_wait"] is True
    if kind == "interrupted":
        assert c.state["actual_declared_abort_signals"] == [(os.getpid(), signal.SIGTERM)]
    else:
        assert c.state.get("actual_declared_abort_signals", []) == []
    for thread in c.threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in c.threads)
    assert not c.errors and len(c.action_results) == len(c.children) == 2
    assert c.children[0].poll() == 0 and c.children[-1].poll() is not None
    assert all(
        stream is None or stream.closed
        for stream in (
            c.children[-1].stdin,
            c.children[-1].stdout,
            c.children[-1].stderr,
        )
    )
    context = c.state["context"]
    host = c.state["host_channel"]
    assert context.finished and context.scientific_finished and context.step_authorized
    assert context.consumed is False and context.channel.position == 5
    assert (
        context.channel.closed
        and context.channel.read_fd is context.channel.write_fd is context.channel.error_fd is None
    )
    assert host.closed and host.read_fd is host.write_fd is host.error_fd is None
    assert c.state["publication"].store.resolver._closed
    assert c.state["publication"].store.resolver._fds == []
    assert c.action_results[-1]["result"]["lifecycle"] == "NOT_DEMONSTRATED"
    assert c.science.backend.accepted == 0 and all(token.released for token in c.science.tokens)
    assert not c.factory._lock.locked() and c.state["completion"] == []
    pending = c.state["actual_declared_abort_pending_boundary"]
    current = c.boot.load(invocation)
    assert current._bytes == pending["b2_raw"]
    assert (
        c.boot.checkpoint() == pending["checkpoint"]
        and c.h.checkpoint == pending["checkpoint_state"]
    )
    assert _v2_private_tree_bytes(c.h.home) == pending["physical_home"]
    value = current.to_dict()
    assert value["official_run_id"] == c.action_results[0]["result"]["run_id"]
    assert value["state"]["known_phase"] == "STARTED"
    assert value["state"]["uncertain"] and value["state"]["blocked"]
    assert value["state"]["next_allowed_actions"] == ["inspect"]
    assert (
        len(value["in_flight"]) == 1 and value["in_flight"][0]["action"] == "declare-executor-gone"
    )
    assert c.state["effects"] == ["create", "start", "create", "start"]
    assert c.state["running"] and c.state["worker_retired"] is False
    assert c.transport.calls == pending["rpc"]
    assert (
        c.transport.calls.count("B3_CAS") == 1 and c.transport.calls.count("RETIRE_TERMINAL") == 0
    )
    op = c.boot.apparatus["ex10_operational_context"]
    assert (
        op.history.find_run(
            c.boot.read_authority(c.h.authority.descriptor_ref), value["official_run_id"]
        )["lifecycle_state"]
        == "OPEN"
    )
    # SDK custody lock is actually free after client/actor cleanup. Acquiring it
    # in this test does not discharge the retained native operational intent.
    fd = os.open(
        Path(c.state["store"].root) / ".s11-custody.lock",
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    assert c.results[-1]["decision"] == "NOT_DEMONSTRATED"
    assert c.results[-1]["binding"] is False and c.results[-1]["custody_errors"] == []
    # Public/API known uncertainty guard preserves the pending attempt.
    _v2_native_assert_gone_blocked_without_effects(c, capsys)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
@pytest.mark.parametrize("kind", ["timeout", "interrupted"])
def test_v2_native_final_actual_sdk_abort_keeps_pending_and_never_retries(
    native_factory_execution_chain_case, capsys, kind
):
    """SDK seal cannot authorize B2 completion, STATUS unlock or FINAL retry.

    This is a new v2 regression. It does not credit the incompatible original
    v1 interrupted-finalize STATUS-to-DECIDED-to-refinalize oracle.
    """
    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_execution_chain_case
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    for action in ("execute-official", "reconcile"):
        invocation = c.advance(action)
        assert c.mod.execute(invocation)["launcher_exit"] == 0 and not c.errors
    assert c.action_results[-1]["result"]["decision"] == "PASS"
    invocation = c.advance("finalize")
    c.state["hold_terminal_relay_for_public_abort"] = kind
    capsys.readouterr()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        code = c.mod.main(
            [
                "finalize",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
                c.mod.ACTIONS["finalize"].confirmation,
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    captured = capsys.readouterr()
    assert code == c.mod.EXIT_APPARATUS_ABORT == 5 and captured.out == ""
    assert captured.err == "S11_EX10_OFFICIAL_LAUNCHER_ABORT: " + kind + "\n"
    assert c.state["held_terminal_relay_at_actual_wait"]
    assert c.state.get("actual_terminal_abort_signals", []) == (
        [(os.getpid(), signal.SIGTERM)] if kind == "interrupted" else []
    )
    for thread in c.threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in c.threads)
    assert not c.errors and len(c.children) == len(c.action_results) == 4
    assert [a["action"] for a in c.action_results] == ["start", "execute", "reconcile", "finalize"]
    assert [a["steps"] for a in c.action_results] == [9, 5, 5, 5]
    assert all(child.poll() == 0 for child in c.children[:-1])
    assert c.children[-1].poll() is not None
    assert all(
        stream is None or stream.closed
        for stream in (
            c.children[-1].stdin,
            c.children[-1].stdout,
            c.children[-1].stderr,
        )
    )
    context, host = c.state["context"], c.state["host_channel"]
    assert context.finished and context.scientific_finished and context.step_authorized
    assert not context.consumed and context.channel.position == 5
    assert context.channel.closed and host.closed
    assert context.channel.read_fd is context.channel.write_fd is context.channel.error_fd is None
    assert host.read_fd is host.write_fd is host.error_fd is None
    assert c.state["publication"].store.resolver._closed
    assert c.state["publication"].store.resolver._fds == []
    actual = c.action_results[-1]["result"]
    run_id = actual["run_id"]
    assert actual["decision"] == "PASS" and actual["binding"] is True
    assert actual["lifecycle"] == "FINALIZED_PASS" and actual["custody_errors"] == []
    sdk = helper.executor.load_existing_state_readonly(c.state["store"], run_id)
    assert helper.executor.phase_of(sdk) is helper.executor.Phase.FINALIZED_PASS
    assert (
        _v2_verify_custody_without_normalizing(c.state["store"], run_id) == []
        and c.state["local_seal_checked"]
    )
    assert c.science.backend.accepted == 50 and all(t.released for t in c.science.tokens)
    pending = c.state["actual_terminal_abort_boundary"]
    current = c.boot.load(invocation)
    assert current._bytes == pending["b2_raw"] and c.boot.checkpoint() == pending["checkpoint"]
    assert c.h.checkpoint == pending["checkpoint_state"]
    assert _v2_private_tree_bytes(c.h.home) == pending["physical_home"]
    value = current.to_dict()
    assert value["official_run_id"] == run_id and value["state"]["known_phase"] == "RECONCILING"
    assert value["state"]["uncertain"] and value["state"]["blocked"]
    assert value["state"]["next_allowed_actions"] == ["inspect"]
    assert len(value["in_flight"]) == 1 and value["in_flight"][0]["action"] == "finalize"
    assert not c.factory._lock.locked() and c.state["completion"] == []
    assert c.transport.calls == pending["rpc"]
    assert (
        c.transport.calls.count("B3_CAS") == 1 and c.transport.calls.count("RETIRE_TERMINAL") == 0
    )
    op = c.boot.apparatus["ex10_operational_context"]
    snapshot = c.boot.read_authority(c.h.authority.descriptor_ref)
    assert op.history.find_run(snapshot, run_id)["lifecycle_state"] == "OPEN"
    # Retained worker and terminal SDK bytes are observations, never retry authority.
    for action, expected in [
        ("finalize", "native-intent-state-blocked"),
        ("status", "native-status-run-required"),
    ]:
        from dataclasses import replace

        retry = replace(
            c.c.invocation(action, confirmed=action == "finalize"), native_bootstrap=c.boot
        )
        effects, rpc, commands = (
            list(c.state["effects"]),
            list(c.transport.calls),
            list(c.c.state["commands"]),
        )
        with pytest.raises(c.mod.LauncherError, match="^" + expected + "$") as failure:
            c.mod.execute(retry)
        assert not isinstance(failure.value, c.mod.NativeActionFailure)
        assert (
            c.boot.load(retry)._bytes == pending["b2_raw"]
            and c.boot.checkpoint() == pending["checkpoint"]
        )
        assert _v2_private_tree_bytes(c.h.home) == pending["physical_home"]
        assert (
            c.state["effects"] == effects
            and c.transport.calls == rpc
            and c.c.state["commands"] == commands
        )
        assert len(c.children) == len(c.action_results) == 4 and c.state["completion"] == []


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
def test_v2_native_final_rejects_prior_change_after_last_owned_inventory_close(
    native_factory_execution_chain_case, monkeypatch, capsys
):
    """Late physical mismatch rejects return while retaining completed B2/B3.

    Frame observation locates the final proof boundary; every digest and close
    delegates to the real implementation. No worker/result/phase is invented.
    """
    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_execution_chain_case
    executor = helper.executor
    store = helper.EvidenceStore(
        Path(c.h.profile["host_roots"]["evidence"]["path"]),
        custody_hmac_key=c.factory.custody_key,
        publication_verification_key=c.factory.signer.public_key().public_bytes_raw(),
        rolling_checksums=False,
    )
    probes = executor.ExecutorProbes(
        preflight=lambda options: helper.preflight_report(
            git_sha=c.boot.expected_git_sha,
            checked_at=c.c.now["utc"],
        ),
        dataset=helper.ex10.load_dataset,
        provider=lambda: [],
        sources=lambda root: [],
        fingerprint=executor.executor_fingerprint,
        now=lambda: c.c.now["utc"],
    )
    local = executor.start_run(
        store,
        git_sha=c.boot.expected_git_sha,
        source_root=c.boot.repo_root,
        nonce="native-prior-reconcile-preservation",
        evidence_mode=helper.LOCAL,
        timezone="UTC",
        probes=probes,
    )
    executor.finalize_run(
        store,
        run_id=local["run_id"],
        git_sha=c.boot.expected_git_sha,
        abandon_reason="synthetic-pre-t0-stop",
        probes=probes,
    )
    prior = executor.prior_ex10_runs(store)
    assert len(prior) == 1 and prior[0].finalized and not prior[0].problems
    assert prior[0].run_id == local["run_id"] and prior[0].evidence_mode == helper.LOCAL
    retained = store.root / "runs" / local["run_id"]
    before = _v2_private_tree_bytes(retained)
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    assert c.action_results[-1]["result"]["run_id"] != local["run_id"]
    assert _v2_private_tree_bytes(retained) == before
    for action in ("execute-official", "reconcile"):
        invocation = c.advance(action)
        assert c.mod.execute(invocation)["launcher_exit"] == 0 and not c.errors
        assert (
            _v2_private_tree_bytes(retained) == before
            and _v2_verify_custody_without_normalizing(store, local["run_id"]) == []
        )
    reconciliation = c.action_results[-1]["result"]
    assert reconciliation["decision"] == "PASS" and c.science.backend.accepted == 50
    invocation = c.advance("finalize")
    import sys

    original_digest, original_close = c.mod._native_prior_tree_digest, os.close
    evidence = Path(c.h.profile["host_roots"]["evidence"]["path"])
    root_info = evidence.stat()
    root_identity = root_info.st_dev, root_info.st_ino
    gate, boundary, mutations = {"armed": False, "fired": False}, {}, []
    custody_fds, observed_remaining_fds = set(), []

    def digest_then_arm_after_actual_final_completion(fd, *, check_current, metadata_lease=None):
        kwargs = {"check_current": check_current}
        if metadata_lease is not None:
            kwargs["metadata_lease"] = metadata_lease
        result = original_digest(fd, **kwargs)
        # Only the genuine final return fence, after actual B2 completion.
        frame = sys._getframe()
        final_return = False
        try:
            while frame is not None:
                if frame.f_code.co_name == "final_fence":
                    final_return = True
                    break
                frame = frame.f_back
        finally:
            del frame
        if final_return and c.state["context"].action == "finalize":
            assert c.state["completion"] == [False, True]
            assert c.action_results[-1]["result"]["decision"] == "PASS"
            gate["armed"] = True
        return result

    def close_owned_root_then_change_prior(fd):
        import errno

        selected = False
        remaining = set()
        if gate["armed"] and not gate["fired"]:
            # Observe actual ownership locals, without replacing a descriptor or
            # cleanup operation. check_fd has the same inode/path as root_fd,
            # but is an earlier revalidation FD and must never select the fault.
            frame = sys._getframe()
            inventory_root = None
            try:
                while frame is not None:
                    if frame.f_code.co_name == "_native_prior_custody_read":
                        custody_fds.update(
                            value
                            for name in ("metadata", "proc", "process", "descriptors", "lock_fd")
                            if type(value := frame.f_locals.get(name)) is int
                        )
                    elif frame.f_code.co_name == "_native_prior_start_inventory":
                        inventory_root = frame.f_locals.get("root_fd")
                        remaining.update(
                            value
                            for name in ("runs_fd", "run_fd", "check_fd")
                            if type(value := frame.f_locals.get(name)) is int
                        )
                    frame = frame.f_back
            finally:
                del frame
            if fd == inventory_root:
                facts = os.fstat(fd)
                target = os.readlink("/proc/self/fd/" + str(fd))
                assert (facts.st_dev, facts.st_ino) == root_identity and target == str(evidence)
                selected = True
                remaining.update(custody_fds)
                remaining.discard(fd)
                assert custody_fds and remaining
                for previous_fd in remaining:
                    try:
                        os.fstat(previous_fd)
                    except OSError as error:
                        assert error.errno == errno.EBADF
                    else:
                        raise AssertionError(
                            "inventory root must close after every other owned inventory FD"
                        )
        original_close(fd)
        if not selected:
            return
        try:
            os.fstat(fd)
        except OSError as error:
            assert error.errno == errno.EBADF
        else:
            raise AssertionError("actual final inventory root FD was not closed")
        observed_remaining_fds.append(tuple(sorted(remaining)))
        gate["fired"] = True
        current = c.boot.load(invocation)
        value = current.to_dict()
        assert value["state"]["known_phase"] == "FINALIZED_PASS" and not value["in_flight"]
        assert not value["state"]["uncertain"] and not value["state"]["blocked"]
        assert (
            c.transport.calls.count("B3_CAS") == 2
            and c.transport.calls.count("RETIRE_TERMINAL") == 1
        )
        assert _v2_private_tree_bytes(retained) == before and not mutations
        metadata = retained / "metadata"
        facts = metadata.stat()
        os.utime(metadata, ns=(facts.st_atime_ns, facts.st_mtime_ns + 1000000000))
        assert metadata.stat().st_mtime_ns != facts.st_mtime_ns
        boundary.update(
            b2_raw=current._bytes,
            checkpoint=c.boot.checkpoint(),
            physical_home=_v2_private_tree_bytes(c.h.home),
            rpc=list(c.transport.calls),
        )
        mutations.append((fd, root_identity, local["run_id"]))

    monkeypatch.setattr(
        c.mod, "_native_prior_tree_digest", digest_then_arm_after_actual_final_completion
    )
    monkeypatch.setattr(os, "close", close_owned_root_then_change_prior)
    capsys.readouterr()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        code = c.mod.main(
            [
                "finalize",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
                c.mod.ACTIONS["finalize"].confirmation,
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    captured = capsys.readouterr()
    # Premise checks precede the RED/GREEN oracle: an earlier failure is not
    # evidence of the actual last-owned-close mutation or its detection.
    assert gate == {"armed": True, "fired": True} and len(mutations) == 1
    assert len(observed_remaining_fds) == 1 and observed_remaining_fds[0]
    assert not c.errors and [item["steps"] for item in c.action_results] == [9, 5, 5, 5]
    assert code == c.mod.EXIT_LAUNCHER_FAILURE == 4 and captured.out == ""
    assert captured.err == "S11_EX10_OFFICIAL_LAUNCHER_FAILURE: prior-run-changed\n"
    assert all(child.poll() == 0 for child in c.children)
    assert c.state["completion"] == [False, True] and not c.factory._lock.locked()
    current = c.boot.load(invocation)
    assert current._bytes == boundary["b2_raw"] and c.boot.checkpoint() == boundary["checkpoint"]
    assert _v2_private_tree_bytes(c.h.home) == boundary["physical_home"]
    assert c.transport.calls == boundary["rpc"]
    run_id = current.to_dict()["official_run_id"]
    assert current.to_dict()["state"]["known_phase"] == "FINALIZED_PASS"
    assert not current.to_dict()["in_flight"]
    op = c.boot.apparatus["ex10_operational_context"]
    terminal = op.history.find_run(c.boot.read_authority(c.h.authority.descriptor_ref), run_id)
    assert terminal["lifecycle_state"] == "TERMINAL" and terminal["binding"] is True
    assert terminal["result"] == "PASS"
    assert (
        c.transport.calls.count("B3_CAS") == 2 and c.transport.calls.count("RETIRE_TERMINAL") == 1
    )
    sdk = executor.load_existing_state_readonly(store, run_id)
    assert executor.phase_of(sdk) is executor.Phase.FINALIZED_PASS and store.verify(run_id) == []
    assert c.science.backend.accepted == 50 and all(token.released for token in c.science.tokens)
    assert (
        _v2_private_tree_bytes(retained) != before
    )  # Preserve physical damage; never roll back completion.


def _native_known_active_original_precondition(c, state="running"):
    """Observe an exact retained prior identity at the installed synthetic seam."""
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    previous = c.prepare_precondition("declare-executor-gone", phase="STARTED")
    assert previous["actual_exit137"] and previous["host_single5_completed"]
    assert previous["child"].exitcode == 137 and not previous["child"].is_alive()
    previous_id = previous["component_process"].container_id
    previous_name = c.state["plan"]["name"]
    path = c.h.home / "installed-exact-prior-executor-observation.json"
    rows = [{"Id": previous_id, "Name": previous_name, "State": {"Status": state}}]
    with path.open("xb") as stream:
        stream.write(c.mod.base.canonical_bytes(rows))
    path.chmod(0o600)
    original_bytes = path.read_bytes()
    invocation = c.advance("declare-executor-gone")
    original = c.boot.load(invocation)
    installed = c.factory.providers["approve_action"]
    observations = []

    def observe_exact_prior(**facts):
        # The installed original provider checks actual SDK child identity/death
        # and its original run. Additional mechanical container observation may
        # deny that permission; a dead scientific child alone cannot prove its
        # Docker/PID1 container stopped. Never infer absence from read failure.
        assert installed(**facts) is None
        assert facts["action"] == "declare-executor-gone"
        assert facts["session"]._bytes == original._bytes
        assert path.read_bytes() == original_bytes
        observed = json.loads(path.read_bytes())
        exact = [row for row in observed if row["Id"] == previous_id]
        assert len(exact) == 1 and exact[0]["Name"] == previous_name
        assert exact[0]["State"]["Status"] in c.mod.ACTIVE_CONTAINER_STATES
        observations.append(exact[0])
        negative = getattr(c.mod, "NativeExecutorStillActive", None)
        if negative is None:
            # Actual RED on the prior API: installed verifier raises its known
            # negative, which the old launcher incorrectly converts to public4.
            # This branch supplies no fake result and cannot yield public3.
            raise c.mod.LauncherError("executor-still-active")
        return negative(
            session_sha256=hashlib.sha256(original._bytes).hexdigest(),
            action="declare-executor-gone",
            run_id=previous["run_id"],
            phase=previous["phase"],
            container_name=previous_name,
            container_id=previous_id,
            container_state=exact[0]["State"]["Status"],
        )

    return invocation, observe_exact_prior, observations


def _native_install_action_observer(c, approve, prepare=None):
    """Explicit installation before invocation; original raw providers retained."""
    from types import MappingProxyType

    providers = dict(c.factory.providers)
    providers["approve_action"] = approve
    if prepare is not None:
        providers["prepare_oneoff"] = prepare
    c.factory.providers = MappingProxyType(providers)


def _native_known_active_snapshot(c):
    return {
        "tree": _v2_private_tree_bytes(c.h.home),
        "checkpoint": dict(c.h.checkpoint),
        "effects": list(c.state["effects"]),
        "rpc": list(c.transport.calls),
        "children": list(c.children),
        "results": list(c.results),
        "actions": list(c.action_results),
        "commands": list(c.c.state["commands"]),
    }


def _assert_native_known_active_unchanged(c, before):
    assert _v2_private_tree_bytes(c.h.home) == before["tree"]
    assert c.h.checkpoint == before["checkpoint"]
    assert c.state["effects"] == before["effects"] and c.transport.calls == before["rpc"]
    assert c.c.state["commands"] == before["commands"]
    assert c.children == before["children"] and c.results == before["results"]
    assert c.action_results == before["actions"] and c.state["completion"] == []
    assert not c.factory._lock.locked() and not c.errors
    assert c.science.backend.accepted == 0 and all(child.poll() == 0 for child in c.children)
    for thread in c.threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in c.threads)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_v2_native_known_active_denial_rejects_every_unbound_or_untyped_return(
    native_factory_recovery_precondition_case,
):
    """Each bad binding has a fresh no-write invocation on one original context."""
    from dataclasses import replace

    c = native_factory_recovery_precondition_case
    invocation, approved, observations = _native_known_active_original_precondition(c)
    before = _native_known_active_snapshot(c)
    failures = []
    faults = [
        ("session_sha256", "0" * 64),
        ("action", "execute-official"),
        ("run_id", OTHER_RUN),
        ("phase", "EMITTING"),
        ("container_name", "INVALID/NAME"),
        ("container_id", "not-a-cid"),
        ("container_state", "exited"),
        *((name, None) for name in c.mod.NativeExecutorStillActive.__dataclass_fields__),
        ("return", True),
        ("return", {}),
        ("return", "executor-still-active"),
    ]
    for field, value in faults:

        def unbound(_field=field, _value=value, **facts):
            negative = approved(**facts)
            return _value if _field == "return" else replace(negative, **{_field: _value})

        _native_install_action_observer(c, unbound)
        with pytest.raises(c.mod.NativeActionFailure) as failure:
            c.mod.execute(replace(invocation))
        assert failure.value.code == "native-intent-approval-required"
        failures.append((field, value))
        _assert_native_known_active_unchanged(c, before)
    assert failures == faults and len(observations) == len(faults)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
def test_v2_native_known_active_denial_rejects_fabricated_thrown_and_other_intent_signals(
    native_factory_recovery_precondition_case,
):
    from dataclasses import replace

    c = native_factory_recovery_precondition_case
    invocation, approved, observations = _native_known_active_original_precondition(c)
    # Create a genuine private signal with another real no-write intent. That
    # signal is not the Factory's intent and must never authorize public3.
    foreign_intent = c.mod.NativeActionIntent(
        invocation=replace(invocation),
        bootstrap=c.boot,
        container_name="s11-owned-foreign-intent",
        output_dir="actions/foreign-intent",
        approve_action=approved,
    )
    before = _native_known_active_snapshot(c)
    with pytest.raises(c.mod._NativeIntentStillActive) as observed:
        foreign_intent.begin()
    foreign = observed.value
    assert foreign is foreign_intent._active_signal
    _assert_native_known_active_unchanged(c, before)
    fabricated = c.mod._NativeIntentStillActive(lambda: None)
    original_prepare = c.factory.providers["prepare_oneoff"]

    def fabricated_return(**facts):
        assert approved(**facts) is not None
        return fabricated

    def thrown_approval(**facts):
        assert approved(**facts) is not None
        raise fabricated

    def other_intent_provider(**facts):
        original_prepare(**facts)
        raise foreign

    for approve, prepare in [
        (fabricated_return, original_prepare),
        (thrown_approval, original_prepare),
        (approved, other_intent_provider),
    ]:
        _native_install_action_observer(c, approve, prepare)
        with pytest.raises(c.mod.NativeActionFailure) as failure:
            c.mod.execute(replace(invocation))
        assert failure.value.code == "native-intent-approval-required"
        _assert_native_known_active_unchanged(c, before)
    assert len(observations) == 3


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
@pytest.mark.parametrize(
    "fault",
    [
        "approval-b2-samebytes",
        "session-close-b2-samebytes",
        "approval-checkpoint",
        "session-close-checkpoint",
        "factory-release-checkpoint",
        "approval-reader-rebind",
        "final-reader-rebind",
        "factory-release-source-samebytes",
    ],
)
def test_v2_native_known_active_denial_preserves_original_post_io_context(
    native_factory_recovery_precondition_case,
    monkeypatch,
    fault,
):
    """Delegate real locks/releases; inject one physical/witness fault afterwards."""
    import contextlib
    from dataclasses import replace

    c = native_factory_recovery_precondition_case
    invocation, approved, observations = _native_known_active_original_precondition(c)
    session_path = invocation.session_root / c.mod.SESSION_FILE
    original_raw = session_path.read_bytes()
    original_inode = (session_path.stat().st_dev, session_path.stat().st_ino)
    checkpoint = dict(c.h.checkpoint)
    original_reader = c.boot.read_checkpoint
    gate, injected = {"approved": False, "released": False}, []

    def inject():
        assert not injected
        if "b2-samebytes" in fault:
            replacement = session_path.with_name("known-active-samebytes-replacement")
            with replacement.open("xb") as stream:
                stream.write(original_raw)
            replacement.chmod(0o600)
            os.replace(replacement, session_path)
            assert session_path.read_bytes() == original_raw
            assert (session_path.stat().st_dev, session_path.stat().st_ino) != original_inode
        elif fault.endswith("checkpoint"):
            assert c.h.checkpoint == checkpoint
            c.h.checkpoint["value"] = ("0" * 64, checkpoint["value"][1])
        elif fault.endswith("reader-rebind"):
            object.__setattr__(c.boot, "read_checkpoint", lambda: original_reader())
        else:
            source_path = c.boot.profile_path
            replacement = source_path.with_name("known-active-samebytes-source")
            source_raw = source_path.read_bytes()
            old_identity = (source_path.stat().st_dev, source_path.stat().st_ino)
            with replacement.open("xb") as stream:
                stream.write(source_raw)
            replacement.chmod(0o600)
            os.replace(replacement, source_path)
            assert source_path.read_bytes() == source_raw
            assert (source_path.stat().st_dev, source_path.stat().st_ino) != old_identity
        injected.append(fault)

    def approve_then_observe_fault(**facts):
        negative = approved(**facts)
        gate["approved"] = True
        if fault.startswith("approval-"):
            inject()
        return negative

    real_session_lock = c.mod._native_session_lock

    @contextlib.contextmanager
    def observe_actual_session_close(*args, **kwargs):
        with real_session_lock(*args, **kwargs) as current:
            yield current
        if gate["approved"] and fault.startswith("session-close-") and not injected:
            inject()

    monkeypatch.setattr(c.mod, "_native_session_lock", observe_actual_session_close)
    real_factory_lock = c.factory._lock

    class ObserveActualFactoryRelease:
        def acquire(self, *args, **kwargs):
            return real_factory_lock.acquire(*args, **kwargs)

        def locked(self):
            return real_factory_lock.locked()

        def release(self):
            real_factory_lock.release()
            assert not real_factory_lock.locked()
            gate["released"] = True
            if gate["approved"] and fault.startswith("factory-release-") and not injected:
                inject()

    c.factory._lock = ObserveActualFactoryRelease()
    if fault == "final-reader-rebind":

        def read_then_rebind():
            actual = original_reader()
            if gate["approved"] and gate["released"] and not injected:
                inject()
            return actual

        object.__setattr__(c.boot, "read_checkpoint", read_then_rebind)
    _native_install_action_observer(c, approve_then_observe_fault)
    before = _native_known_active_snapshot(c)
    with pytest.raises(c.mod.NativeActionFailure) as failure:
        c.mod.execute(replace(invocation))
    assert failure.value.code in {
        "native-intent-record-changed",
        "native-factory-final-observation-expired",
    }
    assert observations and injected == [fault] and gate["released"]
    assert session_path.read_bytes() == original_raw
    assert c.state["effects"] == before["effects"] and c.transport.calls == before["rpc"]
    assert c.children == before["children"] and c.action_results == before["actions"]
    assert c.state["completion"] == [] and c.science.backend.accepted == 0 and not c.errors
    assert not c.factory._lock.locked() and all(child.poll() == 0 for child in c.children)
    if fault.endswith("checkpoint"):
        assert c.h.checkpoint["value"] == ("0" * 64, checkpoint["value"][1])
        assert c.h.checkpoint != checkpoint
    else:
        assert c.h.checkpoint == checkpoint


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
@pytest.mark.parametrize(
    "fault",
    [
        "owned-session-close-eio",
        "original-authority-expired-after-release",
        "original-host-budget-expired-after-release",
    ],
)
def test_v2_native_known_active_denial_owned_close_and_original_expiry(
    native_factory_recovery_precondition_case,
    monkeypatch,
    fault,
):
    """Actual owned FD close and original clock budgets; no renewal or fake abort.

    The common UTC/monotonic clocks are the existing explicit synthetic clocks
    of this mechanical fixture. These cases do not qualify wall-clock runtime.
    """
    import errno
    from dataclasses import replace

    c = native_factory_recovery_precondition_case
    invocation, approved, observations = _native_known_active_original_precondition(c)
    original_clock = dict(c.c.now)
    gate, placements = {"approved": False}, []
    deadline = {}

    def approve_and_observe_original_deadline(**facts):
        negative = approved(**facts)
        frame = sys._getframe()
        try:
            while frame is not None:
                if frame.f_code.co_name == "execute" and frame.f_locals.get("self") is c.factory:
                    deadline["original"] = frame.f_locals["host_deadline"]
                    break
                frame = frame.f_back
        finally:
            del frame
        assert deadline["original"] == original_clock["mono"] + (
            invocation.timeout or c.mod.ACTIONS[invocation.action].timeout
        )
        gate["approved"] = True
        return negative

    real_close = os.close

    def observe_owned_session_close(fd):
        selected = False
        if gate["approved"] and fault == "owned-session-close-eio" and not placements:
            frame = sys._getframe()
            try:
                while frame is not None:
                    if (
                        frame.f_code.co_name == "_native_session_lock"
                        and frame.f_locals.get("bootstrap") is c.boot
                        and frame.f_locals.get("root") == c.h.root
                        and fd == frame.f_locals.get("fd")
                    ):
                        assert os.readlink("/proc/self/fd/" + str(fd)) == str(c.h.root)
                        selected = True
                        break
                    frame = frame.f_back
            finally:
                del frame
        real_close(fd)
        if selected:
            try:
                os.fstat(fd)
            except OSError as error:
                assert error.errno == errno.EBADF
            else:
                raise AssertionError("real owned session descriptor was not closed")
            placements.append((fault, fd))
            raise OSError(errno.EIO, "owned session close observation failed")

    monkeypatch.setattr(os, "close", observe_owned_session_close)
    real_factory_lock = c.factory._lock

    class ObserveActualFactoryRelease:
        def acquire(self, *args, **kwargs):
            return real_factory_lock.acquire(*args, **kwargs)

        def locked(self):
            return real_factory_lock.locked()

        def release(self):
            real_factory_lock.release()
            assert not real_factory_lock.locked()
            if gate["approved"] and fault.endswith("after-release"):
                assert not placements
                assert c.c.now == original_clock
                if fault.startswith("original-authority"):
                    c.c.now["utc"] += timedelta(seconds=60)
                else:
                    c.c.now["mono"] = deadline["original"] + 1
                placements.append((fault, None))

    c.factory._lock = ObserveActualFactoryRelease()
    _native_install_action_observer(c, approve_and_observe_original_deadline)
    before = _native_known_active_snapshot(c)
    expected = (
        c.mod.Aborted if fault.startswith("original-host-budget") else c.mod.NativeActionFailure
    )
    with pytest.raises(expected) as failure:
        c.mod.execute(replace(invocation))
    if fault == "owned-session-close-eio":
        assert failure.value.code == "native-factory-uncertain"
        # No authority/source clock is changed. Real reacquisition proves the
        # original global writer released after the delegated session close.
        with c.boot.writer() as writer:
            writer.check()
    elif fault.startswith("original-authority"):
        assert failure.value.code == "native-factory-final-observation-expired"
        assert c.c.now["utc"] == original_clock["utc"] + timedelta(seconds=60)
        assert c.c.now["mono"] == original_clock["mono"]
    else:
        assert failure.value.kind == "timeout"
        assert c.c.now["mono"] == deadline["original"] + 1
        assert c.c.now["utc"] == original_clock["utc"]
    assert len(observations) == len(placements) == 1
    assert placements[0][0] == fault
    _assert_native_known_active_unchanged(c, before)


def _native_exact_prior_observer_precondition(c, *, phase, left, foreign=None):
    """Retain exact SDK process provenance; mechanical deployment lookup is synthetic."""
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    assert c.action_results[0]["steps"] == 9 and c.state["completion"] == [False, True]
    previous = c.prepare_precondition("declare-executor-gone", phase=phase)
    assert previous["actual_exit137"] and previous["host_single5_completed"]
    assert previous["child"].exitcode == 137 and not previous["child"].is_alive()
    assert previous["phase"] == phase
    if phase == "EMITTING":
        assert previous["actual_emission_proof_admitted_in_child"]
    previous_id = previous["component_process"].container_id
    previous_name = c.state["plan"]["name"]
    rows = (
        []
        if left is None
        else [{"Id": previous_id, "Name": previous_name, "State": {"Status": left}}]
    )
    foreign_row = None
    if foreign is not None:
        assert foreign in {
            "similar-name-exited",
            "similar-name-active",
            "same-name-different-id-active",
        }
        foreign_row = {
            "Id": hashlib.sha256((previous_id + ":foreign").encode()).hexdigest(),
            "Name": previous_name if foreign.startswith("same-name") else previous_name + "-x",
            "State": {"Status": "exited" if foreign.endswith("exited") else "running"},
        }
        assert foreign_row["Id"] != previous_id
        rows.append(foreign_row)
    path = c.h.home / "installed-exact-prior-executor-terminal-observation.json"
    with path.open("xb") as stream:
        stream.write(c.mod.base.canonical_bytes(rows))
    path.chmod(0o600)
    original_bytes = path.read_bytes()
    original_stamp = (path.stat().st_ino, path.stat().st_mtime_ns, path.stat().st_ctime_ns)
    invocation = c.advance("declare-executor-gone")
    original = c.boot.load(invocation)
    assert original.to_dict()["official_run_id"] == previous["run_id"]
    assert original.to_dict()["in_flight"] == []
    assert original.to_dict()["state"]["known_phase"] == phase
    assert (
        original.to_dict()["history"][-1]["payload"]["event"]
        == "fixture-precondition-declare-executor-gone"
    )
    installed = c.factory.providers["approve_action"]
    observations = []

    def observe_exact_prior(**facts):
        # Delegation verifies the original actual SDK child death/provenance.
        # Successful exact lookup additionally proves known absence/exited/dead
        # in this synthetic namespace; an exception/unknown state never grants.
        assert installed(**facts) is None
        assert facts["action"] == "declare-executor-gone"
        assert facts["session"]._bytes == original._bytes
        raw = path.read_bytes()
        assert raw == original_bytes
        observed = json.loads(raw)
        exact = [row for row in observed if row["Id"] == previous_id]
        assert len(exact) <= 1
        if exact:
            assert exact[0]["Name"] == previous_name
            state = exact[0]["State"]["Status"]
            if state not in {"exited", "dead"}:
                raise c.mod.LauncherError("native-executor-death-required")
        else:
            state = "absent"
        observations.append(
            {
                "query_id": previous_id,
                "namespace_complete": True,
                "matched_ids": [row["Id"] for row in exact],
                "state": state,
            }
        )
        return None

    return SimpleNamespace(
        invocation=invocation,
        previous=previous,
        previous_name=previous_name,
        approve=observe_exact_prior,
        observations=observations,
        path=path,
        raw=original_bytes,
        stamp=original_stamp,
        foreign=foreign_row,
    )


def _assert_native_exact_prior_declaration_completed(c, prior):
    """Actual SINGLE5 SDK ND, custody, B2 completion and one B3 retirement."""
    from apps.experiments_app.tests import test_ex10_executor as helper

    before = c.boot.load(prior.invocation)
    assert before.to_dict()["state"]["next_allowed_actions"] == [
        "declare-executor-gone",
        "inspect",
        "status",
    ]
    closed = c.mod.execute(prior.invocation)
    assert closed["launcher_exit"] == 0 and closed["classification"] == "COHERENT"
    assert closed["state"]["known_phase"] == "NOT_DEMONSTRATED"
    assert closed["state"]["next_allowed_actions"] == ["cleanup-stack", "inspect", "status"]
    assert [row["action"] for row in c.action_results] == ["start", "finalize"]
    assert [row["steps"] for row in c.action_results] == [9, 5]
    assert len(c.children) == 2 and all(child.poll() == 0 for child in c.children)
    actual = c.action_results[-1]["result"]
    run_id = prior.previous["run_id"]
    assert actual["run_id"] == closed["official_run_id"] == run_id
    assert actual["decision"] == actual["lifecycle"] == "NOT_DEMONSTRATED"
    assert actual["binding"] is False and actual["custody_errors"] == []
    reason = {
        "STARTED": "emission-interrupted:before-arming",
        "EMITTING": "emission-interrupted:raw-missing",
    }[prior.previous["phase"]]
    assert reason in actual["reasons"]
    assert c.store().verify(run_id) == []
    existing = helper.executor.load_existing_state_readonly(c.store(), run_id)
    assert helper.executor.phase_of(existing) is helper.executor.Phase.NOT_DEMONSTRATED
    assert existing.finalized and not existing.has(helper.executor.ART_RAW)
    assert c.science.backend.accepted == 0 and all(token.released for token in c.science.tokens)
    assert c.state["exact_previous_scientific_death_approved"] and not c.errors
    assert c.state["effects"] == ["create", "start", "create", "start"]
    assert c.state["completion"] == [False, True] and c.state["local_seal_checked"]
    options = c.state["cfg"]["command_options"]
    assert options == {
        "expected_git_sha": prior.invocation.expected_git_sha,
        "run_id": run_id,
        "abandon_reason": None,
        "declare_executor_gone": True,
    }
    completed = c.boot.load(prior.invocation)
    assert completed.header == before.header and completed.to_dict()["in_flight"] == []
    assert (
        completed.to_dict()["history"][-1]["payload"]["event"] == "complete-declare-executor-gone"
    )
    assert c.transport.calls.count("RESERVE") == c.transport.calls.count("COMMIT_OPEN") == 1
    assert (
        c.transport.calls.count("B3_CAS") == 2 and c.transport.calls.count("RETIRE_TERMINAL") == 1
    )
    op = c.boot.apparatus["ex10_operational_context"]
    snapshot = c.boot.read_authority(c.h.authority.descriptor_ref)
    row = op.history.find_run(snapshot, run_id)
    assert row["lifecycle_state"] == "TERMINAL" and row["result"] == "NOT_DEMONSTRATED"
    assert row["binding"] is False
    assert prior.path.read_bytes() == prior.raw
    assert (
        prior.path.stat().st_ino,
        prior.path.stat().st_mtime_ns,
        prior.path.stat().st_ctime_ns,
    ) == prior.stamp
    for thread in c.threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in c.threads)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
@pytest.mark.parametrize("foreign", ["similar-name-active", "same-name-different-id-active"])
def test_v2_native_exact_prior_executor_observer_ignores_foreign_active_identity(
    native_factory_recovery_precondition_case,
    foreign,
):
    """Provider seam exact identity, not builtin Factory discovery or real Docker."""
    c = native_factory_recovery_precondition_case
    prior = _native_exact_prior_observer_precondition(
        c, phase="STARTED", left=None, foreign=foreign
    )
    _native_install_action_observer(c, prior.approve)
    _assert_native_exact_prior_declaration_completed(c, prior)
    assert len(prior.observations) == 1 and prior.observations[0]["matched_ids"] == []
    assert prior.observations[0]["query_id"] != prior.foreign["Id"]
    assert prior.foreign["State"]["Status"] == "running"
    assert json.loads(prior.path.read_bytes()) == [prior.foreign]


@pytest.mark.parametrize("v2_native_host", ["typed-b4-historic-run"], indirect=True)
@pytest.mark.parametrize("fault", ["unreadable", "unknown-state"])
def test_v2_native_exact_prior_executor_observation_error_is_not_absence(
    native_factory_recovery_precondition_case,
    fault,
):
    c = native_factory_recovery_precondition_case
    prior = _native_exact_prior_observer_precondition(
        c, phase="STARTED", left=None if fault == "unreadable" else "unknown"
    )
    _native_install_action_observer(c, prior.approve)
    if fault == "unreadable":
        prior.path.unlink()
    before = _native_known_active_snapshot(c)
    with pytest.raises(c.mod.NativeActionFailure, match="^native-intent-approval-required$"):
        c.mod.execute(prior.invocation)
    assert prior.observations == []
    _assert_native_known_active_unchanged(c, before)


def _native_assert_retained_intent_actions_denied(c, capsys, actions):
    """Observe API/CLI admission and inspect; never repair the retained intent."""
    from dataclasses import replace

    before = _native_known_active_snapshot(c)
    completion = list(c.state["completion"])
    accepted = c.science.backend.accepted
    initial = c.boot.load(c.invocation)

    def unchanged(invocation):
        assert _native_known_active_snapshot(c) == before
        assert c.state["completion"] == completion
        assert c.science.backend.accepted == accepted
        assert c.boot.load(invocation)._bytes == initial._bytes
        assert not c.factory._lock.locked() and not c.errors
        assert all(child.poll() is not None for child in c.children)

    for action, cause in actions:
        invocation = replace(
            c.c.invocation(action, confirmed=action != "status"), native_bootstrap=c.boot
        )
        with pytest.raises(c.mod.LauncherError, match="^" + cause + "$") as failure:
            c.mod.execute(invocation)
        assert not isinstance(failure.value, c.mod.NativeActionFailure)
        unchanged(invocation)
        argv = [
            action,
            "--expected-git-sha",
            invocation.expected_git_sha,
            "--session-root",
            str(invocation.session_root),
        ]
        confirmation = c.mod.ACTIONS[action].confirmation
        if confirmation is not None:
            argv.append(confirmation)
        capsys.readouterr()
        handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
        try:
            code = c.mod.main(argv, environ=invocation.environ, native_bootstrap=c.boot)
        finally:
            for number, handler in handlers.items():
                signal.signal(number, handler)
        output = capsys.readouterr()
        assert code == 3 and output.out == ""
        assert output.err == "S11_EX10_OFFICIAL_LAUNCHER_CONFIG: " + cause + "\n"
        unchanged(invocation)
    inspection = replace(c.c.invocation("inspect"), native_bootstrap=c.boot)
    assert c.mod.execute(inspection)["launcher_exit"] == 0
    unchanged(inspection)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
def test_v2_native_genuine_completed_final_is_never_invoked_again(
    native_factory_execution_chain_case, capsys
):
    """Independent completed context preserves the old finalized-never-again core."""
    from apps.experiments_app.tests import test_ex10_executor as helper

    c = native_factory_execution_chain_case
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    for action in ("execute-official", "reconcile", "finalize"):
        invocation = c.advance(action)
        assert c.mod.execute(invocation)["launcher_exit"] == 0 and not c.errors
    actual = c.action_results[-1]["result"]
    assert actual["decision"] == "PASS" and actual["binding"] is True
    assert actual["lifecycle"] == "FINALIZED_PASS" and actual["custody_errors"] == []
    run_id = actual["run_id"]
    sdk = helper.executor.load_existing_state_readonly(c.state["store"], run_id)
    assert helper.executor.phase_of(sdk) is helper.executor.Phase.FINALIZED_PASS
    assert c.state["local_seal_checked"] and c.state["store"].verify(run_id) == []
    value = c.boot.load(invocation).to_dict()
    assert value["official_run_id"] == run_id
    assert value["state"]["known_phase"] == "FINALIZED_PASS"
    assert not value["state"]["uncertain"] and not value["state"]["blocked"]
    assert value["in_flight"] == [] and "finalize" not in value["state"]["next_allowed_actions"]
    assert value["history"][-1]["payload"]["event"] == "complete-finalize"
    assert c.transport.calls.count("B3_CAS") == 2
    assert c.transport.calls.count("RETIRE_TERMINAL") == 1
    op = c.boot.apparatus["ex10_operational_context"]
    snapshot = c.boot.read_authority(c.h.authority.descriptor_ref)
    assert op.history.find_run(snapshot, run_id)["lifecycle_state"] == "TERMINAL"
    assert [row["action"] for row in c.action_results] == [
        "start",
        "execute",
        "reconcile",
        "finalize",
    ]
    assert [row["steps"] for row in c.action_results] == [9, 5, 5, 5]
    assert len(c.children) == 4 and all(child.poll() == 0 for child in c.children)
    assert c.state["completion"] == [False, True]
    assert c.science.backend.accepted == 50 and all(token.released for token in c.science.tokens)
    for thread in c.threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in c.threads)
    _native_assert_retained_intent_actions_denied(
        c,
        capsys,
        [("finalize", "native-intent-state-blocked")],
    )


def _native_current_exec_precondition_case(c, fault, *, monkeypatch=None, observation_fault=None):
    """Installed negative verifier reads fixed original PF and complete current input.

    Namespace/health observations are private synthetic test inputs, not a B5
    field, new grant, built-in Docker inspector or deployment qualification.
    """
    import re
    from types import MappingProxyType, SimpleNamespace

    from apps.experiments_app.tests import test_ex10_executor as helper

    initial_utc = c.c.now["utc"]
    if fault == "stale":
        assert monkeypatch is not None
        original_report = helper.preflight_report

        def original_near_expiry_report(**facts):
            # Change the existing synthetic producer input before report bytes
            # are created; never edit a custodied/signed report or its timestamps.
            assert facts["checked_at"] == initial_utc
            return original_report(**{**facts, "checked_at": initial_utc - timedelta(seconds=899)})

        monkeypatch.setattr(helper, "preflight_report", original_near_expiry_report)
    assert c.mod.execute(c.invocation)["launcher_exit"] == 0 and not c.errors
    assert [row["action"] for row in c.action_results] == ["start"]
    assert c.action_results[0]["steps"] == 9 and c.children[0].poll() == 0
    assert c.science.backend.accepted == 0
    run_id = c.action_results[0]["result"]["run_id"]
    start_cfg = c.state["envelope_configuration"]
    pf_ref = dict(start_cfg["application_preflight_reference"])
    pf_path = Path(pf_ref["path"])
    pf_raw, pf_lease = c.mod._capture_native_source(pf_path, error_type=c.mod.LauncherError)
    assert pf_raw == c.state["start_preflight"]
    assert hashlib.sha256(pf_raw).hexdigest() == pf_ref["sha256"]
    assert (
        c.mod.preflight_report_problems(
            pf_raw, git_sha=c.invocation.expected_git_sha, now=initial_utc
        )
        == []
    )
    checked_at = datetime.fromisoformat(json.loads(pf_raw)["checked_at"].replace("Z", "+00:00"))
    start_envelope = c.boot.apparatus["ex10_authority_sqlite"].parse_canonical(
        c.state["prepared"].envelope_bytes
    )
    original_envelope_expires = datetime.fromisoformat(
        start_envelope["expires_at"].replace("Z", "+00:00")
    )
    authority = c.boot.read_authority(c.h.authority.descriptor_ref)
    original_authority_expires = datetime.fromisoformat(authority.expires_at.replace("Z", "+00:00"))
    original_ids = {
        row["service"]: row["container_id"] for row in start_cfg["container_identities"]
    }
    assert set(original_ids) == set(c.h.profile["expected_services"])
    assert len(set(original_ids.values())) == len(original_ids)
    rows = [
        {
            "container_id": cid,
            "service": service,
            "state": "running",
            "oneoff": False,
            "healthy": True,
        }
        for service, cid in sorted(original_ids.items())
    ]
    if fault == "worker-missing":
        rows = [row for row in rows if row["service"] != "worker"]
    elif fault == "beat":
        rows.append(
            {
                "container_id": hashlib.sha256(b"LOCAL synthetic active beat").hexdigest(),
                "service": "beat",
                "state": "running",
                "oneoff": False,
                "healthy": True,
            }
        )
    elif fault == "oneoff":
        rows.append(
            {
                "container_id": hashlib.sha256(b"LOCAL synthetic extra backend oneoff").hexdigest(),
                "service": "backend",
                "state": "running",
                "oneoff": True,
                "healthy": True,
            }
        )
    elif fault == "db-unhealthy":
        for row in rows:
            if row["service"] == "db":
                row["healthy"] = False
    else:
        assert fault == "stale"
    namespace = {
        "query_complete": True,
        "profile_sha256": hashlib.sha256(c.boot.profile_path.read_bytes()).hexdigest(),
        "project": c.h.profile["project"],
        "containers": rows,
    }
    if observation_fault == "query-incomplete":
        namespace["query_complete"] = False
    elif observation_fault == "health-not-bool":
        next(row for row in rows if row["service"] == "db")["healthy"] = "unknown"
    else:
        assert observation_fault in {None, "unreadable"}
    input_path = c.h.home / "synthetic-complete-current-exec-namespace.json"
    input_raw = json.dumps(namespace, sort_keys=True, separators=(",", ":")).encode()
    with input_path.open("xb") as stream:
        stream.write(input_raw)
    input_path.chmod(0o600)
    original_input_raw, input_lease = c.mod._capture_native_source(
        input_path, error_type=c.mod.LauncherError
    )
    assert original_input_raw == input_raw
    if observation_fault == "unreadable":
        input_path.unlink()
    if fault == "stale":
        assert initial_utc - checked_at == timedelta(seconds=899)
        assert original_authority_expires > initial_utc + timedelta(seconds=2)
        c.c.now["utc"] += timedelta(seconds=2)
        # Validate the same original authority, without refreshing this snapshot.
        c.boot.apparatus["ex10_history_authority"].current_revision(authority)
        assert original_envelope_expires <= c.c.now["utc"]
    invocation = c.advance("execute-official")
    current = c.boot.load(invocation)
    assert current.to_dict()["official_run_id"] == run_id
    assert current.to_dict()["state"]["known_phase"] == "PREPARED"
    assert current.to_dict()["in_flight"] == []
    original_approve = c.factory.providers["approve_action"]
    original_prepare = c.factory.providers["prepare_oneoff"]
    reads, plans = [], []

    def planning(**facts):
        result = original_prepare(**facts)
        plans.append(result["name"])
        return result

    def current_preconditions(**facts):
        assert original_approve(**facts) is None
        assert facts["action"] == "execute-official"
        assert facts["session"]._bytes == current._bytes
        assert facts["session"].to_dict()["official_run_id"] == run_id
        raw, lease = c.mod._capture_native_source(pf_path, error_type=c.mod.LauncherError)
        assert raw == pf_raw and lease == pf_lease
        problems = c.mod.preflight_report_problems(
            raw, git_sha=invocation.expected_git_sha, now=c.c.now["utc"]
        )
        if problems:
            assert problems == ["preflight:stale"]
            reason = "preflight-report-not-acceptable"
        else:
            observed_raw, observed_lease = c.mod._capture_native_source(
                input_path, error_type=c.mod.LauncherError
            )
            assert observed_raw == input_raw and observed_lease == input_lease
            value = json.loads(observed_raw)
            if (
                set(value) != {"query_complete", "profile_sha256", "project", "containers"}
                or value["query_complete"] is not True
                or value["profile_sha256"] != hashlib.sha256(facts["profile"]._bytes).hexdigest()
                or value["project"] != c.h.profile["project"]
                or type(value["containers"]) is not list
            ):
                raise c.mod.LauncherError("LOCAL-current-namespace-unknown")
            observed, ids = [], set()
            for row in value["containers"]:
                if (
                    type(row) is not dict
                    or set(row) != {"container_id", "service", "state", "oneoff", "healthy"}
                    or type(row["container_id"]) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}", row["container_id"])
                    or row["container_id"] in ids
                    or type(row["service"]) is not str
                    or row["service"] not in set(original_ids) | {"beat"}
                    or type(row["state"]) is not str
                    or row["state"]
                    not in {"running", "created", "exited", "dead", "paused", "restarting"}
                    or type(row["oneoff"]) is not bool
                    or type(row["healthy"]) is not bool
                ):
                    raise c.mod.LauncherError("LOCAL-current-namespace-unknown")
                if row["service"] in original_ids and not row["oneoff"]:
                    assert row["container_id"] == original_ids[row["service"]]
                ids.add(row["container_id"])
                observed.append(
                    SimpleNamespace(
                        service=row["service"],
                        state=row["state"],
                        oneoff=row["oneoff"],
                        healthy=row["healthy"],
                    )
                )
            try:
                c.mod._preconditions(
                    c.mod.ACTIONS["execute-official"], SimpleNamespace(containers=observed)
                )
            except c.mod.LauncherError as negative:
                reason = negative.code
                assert reason in {
                    "web-worker-not-running",
                    "beat-running",
                    "oneoff-container-present",
                    "db-redis-not-healthy",
                }
            else:
                raise AssertionError("original negative must be observed, never manufacture None")
        assert c.state["effects"] == ["create", "start"]
        assert len(c.children) == len(c.action_results) == 1
        reads.append({"reason": reason, "pf_problems": problems, "run_id": run_id})
        kind = getattr(c.mod, "NativeExecutionPreconditionRefused", None)
        if kind is None:
            # Actual old installed verifier raises the known precondition cause;
            # old generic approval mapping must demonstrate4 versus original3.
            raise c.mod.LauncherError(reason)
        return kind(
            session_sha256=hashlib.sha256(current._bytes).hexdigest(),
            action="execute-official",
            run_id=run_id,
            phase="PREPARED",
            reason=reason,
        )

    providers = dict(c.factory.providers)
    providers.update(approve_action=current_preconditions, prepare_oneoff=planning)
    c.factory.providers = MappingProxyType(providers)
    return SimpleNamespace(
        invocation=invocation,
        reads=reads,
        plans=plans,
        namespace=namespace,
        report_age_at_start=initial_utc - checked_at,
        checked_at=checked_at,
        original_authority_expires=original_authority_expires,
        original_envelope_expires=original_envelope_expires,
        pf_path=pf_path,
        pf_raw=pf_raw,
        pf_lease=pf_lease,
    )


def _native_assert_current_exec_denial(c, capsys, observed, cause, expected_code):
    from dataclasses import replace

    before = _native_known_active_snapshot(c)
    original = c.boot.load(observed.invocation)
    before_plans = len(observed.plans)
    invocation = replace(
        c.c.invocation("execute-official", confirmed=True), native_bootstrap=c.boot
    )
    with pytest.raises(c.mod.LauncherError, match="^" + cause + "$") as failure:
        c.mod.execute(invocation)
    assert isinstance(failure.value, c.mod.NativeActionFailure) is (expected_code == 4)
    assert len(observed.plans) == before_plans + 1
    _assert_native_known_active_unchanged(c, before)
    assert c.boot.load(invocation)._bytes == original._bytes
    capsys.readouterr()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        code = c.mod.main(
            [
                "execute-official",
                "--expected-git-sha",
                invocation.expected_git_sha,
                "--session-root",
                str(invocation.session_root),
                c.mod.ACTIONS["execute-official"].confirmation,
            ],
            environ=invocation.environ,
            native_bootstrap=c.boot,
        )
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    output = capsys.readouterr()
    assert code == expected_code and output.out == ""
    prefix = (
        "S11_EX10_OFFICIAL_LAUNCHER_CONFIG: "
        if expected_code == 3
        else "S11_EX10_OFFICIAL_LAUNCHER_FAILURE: "
    )
    assert output.err == prefix + cause + "\n"
    assert len(observed.plans) == before_plans + 2
    _assert_native_known_active_unchanged(c, before)
    assert c.boot.load(invocation)._bytes == original._bytes
    raw, lease = c.mod._capture_native_source(observed.pf_path, error_type=c.mod.LauncherError)
    assert raw == observed.pf_raw and lease == observed.pf_lease


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
@pytest.mark.parametrize("fault", ["unreadable", "query-incomplete", "health-not-bool"])
def test_v2_native_exec_current_observation_unknown_is_not_a_known_negative(
    native_factory_execution_chain_case, capsys, fault
):
    c = native_factory_execution_chain_case
    observed = _native_current_exec_precondition_case(c, "worker-missing", observation_fault=fault)
    _native_assert_current_exec_denial(c, capsys, observed, "native-intent-approval-required", 4)
    assert observed.reads == []


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
def test_v2_native_exec_known_negative_rejects_unbound_untyped_and_unknown_reasons(
    native_factory_execution_chain_case, capsys
):
    from dataclasses import replace
    from types import MappingProxyType

    c = native_factory_execution_chain_case
    observed = _native_current_exec_precondition_case(c, "worker-missing")
    approved = c.factory.providers["approve_action"]
    perturbations = [
        ("session_sha256", "0" * 64),
        ("action", "declare-executor-gone"),
        ("run_id", "s11-01234567-89ab-5cde-8f01-23456789abcd"),
        ("phase", "EMITTING"),
        ("reason", "executor-still-active"),
        ("reason", "unknown"),
        ("reason", 3),
        ("phase", None),
    ]
    for field, value in perturbations:

        def corrupted(_field=field, _value=value, **facts):
            genuine_negative = approved(**facts)
            assert type(genuine_negative) is c.mod.NativeExecutionPreconditionRefused
            return replace(genuine_negative, **{_field: _value})

        providers = dict(c.factory.providers)
        providers["approve_action"] = corrupted
        c.factory.providers = MappingProxyType(providers)
        _native_assert_current_exec_denial(
            c, capsys, observed, "native-intent-approval-required", 4
        )
    for value in [True, {}, "untyped-negative"]:

        def untyped(_value=value, **facts):
            assert type(approved(**facts)) is c.mod.NativeExecutionPreconditionRefused
            return _value

        providers = dict(c.factory.providers)
        providers["approve_action"] = untyped
        c.factory.providers = MappingProxyType(providers)
        _native_assert_current_exec_denial(
            c, capsys, observed, "native-intent-approval-required", 4
        )

    def thrown(**facts):
        assert type(approved(**facts)) is c.mod.NativeExecutionPreconditionRefused
        raise c.mod.LauncherError("web-worker-not-running")

    providers = dict(c.factory.providers)
    providers["approve_action"] = thrown
    c.factory.providers = MappingProxyType(providers)
    _native_assert_current_exec_denial(c, capsys, observed, "native-intent-approval-required", 4)
    assert len(observed.reads) == 2 * (len(perturbations) + 4)


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
def test_v2_native_exec_negative_rejects_fabricated_thrown_and_other_intent_signals(
    native_factory_execution_chain_case,
):
    from dataclasses import replace

    c = native_factory_execution_chain_case
    case = _native_current_exec_precondition_case(c, "worker-missing")
    invocation = case.invocation
    approved, observations = c.factory.providers["approve_action"], case.reads
    # Create a genuine private signal with another real no-write intent. That
    # signal is not the Factory's intent and must never authorize public3.
    foreign_intent = c.mod.NativeActionIntent(
        invocation=replace(invocation),
        bootstrap=c.boot,
        container_name="s11-owned-foreign-intent",
        output_dir="actions/foreign-intent",
        approve_action=approved,
    )
    before = _native_known_active_snapshot(c)
    with pytest.raises(c.mod._NativeIntentExecutionRefused) as observed:
        foreign_intent.begin()
    foreign = observed.value
    assert foreign is foreign_intent._execution_signal
    _assert_native_known_active_unchanged(c, before)
    fabricated = c.mod._NativeIntentExecutionRefused(lambda: None, "web-worker-not-running")
    original_prepare = c.factory.providers["prepare_oneoff"]

    def fabricated_return(**facts):
        assert approved(**facts) is not None
        return fabricated

    def thrown_approval(**facts):
        assert approved(**facts) is not None
        raise fabricated

    def other_intent_provider(**facts):
        original_prepare(**facts)
        raise foreign

    for approve, prepare in [
        (fabricated_return, original_prepare),
        (thrown_approval, original_prepare),
        (approved, other_intent_provider),
    ]:
        _native_install_action_observer(c, approve, prepare)
        with pytest.raises(c.mod.NativeActionFailure) as failure:
            c.mod.execute(replace(invocation))
        assert failure.value.code == "native-intent-approval-required"
        _assert_native_known_active_unchanged(c, before)
    assert len(observations) == 3


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
@pytest.mark.parametrize(
    "fault",
    [
        "approval-b2-samebytes",
        "session-close-b2-samebytes",
        "approval-checkpoint",
        "session-close-checkpoint",
        "factory-release-checkpoint",
        "approval-reader-rebind",
        "final-reader-rebind",
        "factory-release-source-samebytes",
    ],
)
def test_v2_native_exec_negative_preserves_original_post_io_context(
    native_factory_execution_chain_case,
    monkeypatch,
    fault,
):
    """Delegate real locks/releases; inject one physical/witness fault afterwards."""
    import contextlib
    from dataclasses import replace

    c = native_factory_execution_chain_case
    case = _native_current_exec_precondition_case(c, "worker-missing")
    invocation = case.invocation
    approved, observations = c.factory.providers["approve_action"], case.reads
    session_path = invocation.session_root / c.mod.SESSION_FILE
    original_raw = session_path.read_bytes()
    original_inode = (session_path.stat().st_dev, session_path.stat().st_ino)
    checkpoint = dict(c.h.checkpoint)
    original_reader = c.boot.read_checkpoint
    gate, injected = {"approved": False, "released": False}, []

    def inject():
        assert not injected
        if "b2-samebytes" in fault:
            replacement = session_path.with_name("known-exec-negative-samebytes-replacement")
            with replacement.open("xb") as stream:
                stream.write(original_raw)
            replacement.chmod(0o600)
            os.replace(replacement, session_path)
            assert session_path.read_bytes() == original_raw
            assert (session_path.stat().st_dev, session_path.stat().st_ino) != original_inode
        elif fault.endswith("checkpoint"):
            assert c.h.checkpoint == checkpoint
            c.h.checkpoint["value"] = ("0" * 64, checkpoint["value"][1])
        elif fault.endswith("reader-rebind"):
            object.__setattr__(c.boot, "read_checkpoint", lambda: original_reader())
        else:
            source_path = c.boot.profile_path
            replacement = source_path.with_name("known-exec-negative-samebytes-source")
            source_raw = source_path.read_bytes()
            old_identity = (source_path.stat().st_dev, source_path.stat().st_ino)
            with replacement.open("xb") as stream:
                stream.write(source_raw)
            replacement.chmod(0o600)
            os.replace(replacement, source_path)
            assert source_path.read_bytes() == source_raw
            assert (source_path.stat().st_dev, source_path.stat().st_ino) != old_identity
        injected.append(fault)

    def approve_then_observe_fault(**facts):
        negative = approved(**facts)
        gate["approved"] = True
        if fault.startswith("approval-"):
            inject()
        return negative

    real_session_lock = c.mod._native_session_lock

    @contextlib.contextmanager
    def observe_actual_session_close(*args, **kwargs):
        with real_session_lock(*args, **kwargs) as current:
            yield current
        if gate["approved"] and fault.startswith("session-close-") and not injected:
            inject()

    monkeypatch.setattr(c.mod, "_native_session_lock", observe_actual_session_close)
    real_factory_lock = c.factory._lock

    class ObserveActualFactoryRelease:
        def acquire(self, *args, **kwargs):
            return real_factory_lock.acquire(*args, **kwargs)

        def locked(self):
            return real_factory_lock.locked()

        def release(self):
            real_factory_lock.release()
            assert not real_factory_lock.locked()
            gate["released"] = True
            if gate["approved"] and fault.startswith("factory-release-") and not injected:
                inject()

    c.factory._lock = ObserveActualFactoryRelease()
    if fault == "final-reader-rebind":

        def read_then_rebind():
            actual = original_reader()
            if gate["approved"] and gate["released"] and not injected:
                inject()
            return actual

        object.__setattr__(c.boot, "read_checkpoint", read_then_rebind)
    _native_install_action_observer(c, approve_then_observe_fault)
    before = _native_known_active_snapshot(c)
    with pytest.raises(c.mod.NativeActionFailure) as failure:
        c.mod.execute(replace(invocation))
    assert failure.value.code in {
        "native-intent-record-changed",
        "native-factory-final-observation-expired",
    }
    assert observations and injected == [fault] and gate["released"]
    assert session_path.read_bytes() == original_raw
    assert c.state["effects"] == before["effects"] and c.transport.calls == before["rpc"]
    assert c.children == before["children"] and c.action_results == before["actions"]
    assert c.state["completion"] == [] and c.science.backend.accepted == 0 and not c.errors
    assert not c.factory._lock.locked() and all(child.poll() == 0 for child in c.children)
    if fault.endswith("checkpoint"):
        assert c.h.checkpoint["value"] == ("0" * 64, checkpoint["value"][1])
        assert c.h.checkpoint != checkpoint
    else:
        assert c.h.checkpoint == checkpoint


@pytest.mark.parametrize("v2_native_host", ["typed-b4-empty-history"], indirect=True)
@pytest.mark.parametrize(
    "fault",
    [
        "owned-session-close-eio",
        "original-authority-expired-after-release",
        "original-host-budget-expired-after-release",
    ],
)
def test_v2_native_exec_negative_owned_close_and_original_expiry(
    native_factory_execution_chain_case,
    monkeypatch,
    fault,
):
    """Actual owned FD close and original clock budgets; no renewal or fake abort.

    The common UTC/monotonic clocks are the existing explicit synthetic clocks
    of this mechanical fixture. These cases do not qualify wall-clock runtime.
    """
    import errno
    from dataclasses import replace

    c = native_factory_execution_chain_case
    case = _native_current_exec_precondition_case(c, "worker-missing")
    invocation = case.invocation
    approved, observations = c.factory.providers["approve_action"], case.reads
    original_clock = dict(c.c.now)
    gate, placements = {"approved": False}, []
    deadline = {}

    def approve_and_observe_original_deadline(**facts):
        negative = approved(**facts)
        frame = sys._getframe()
        try:
            while frame is not None:
                if frame.f_code.co_name == "execute" and frame.f_locals.get("self") is c.factory:
                    deadline["original"] = frame.f_locals["host_deadline"]
                    break
                frame = frame.f_back
        finally:
            del frame
        assert deadline["original"] == original_clock["mono"] + (
            invocation.timeout or c.mod.ACTIONS[invocation.action].timeout
        )
        gate["approved"] = True
        return negative

    real_close = os.close

    def observe_owned_session_close(fd):
        selected = False
        if gate["approved"] and fault == "owned-session-close-eio" and not placements:
            frame = sys._getframe()
            try:
                while frame is not None:
                    if (
                        frame.f_code.co_name == "_native_session_lock"
                        and frame.f_locals.get("bootstrap") is c.boot
                        and frame.f_locals.get("root") == c.h.root
                        and fd == frame.f_locals.get("fd")
                    ):
                        assert os.readlink("/proc/self/fd/" + str(fd)) == str(c.h.root)
                        selected = True
                        break
                    frame = frame.f_back
            finally:
                del frame
        real_close(fd)
        if selected:
            try:
                os.fstat(fd)
            except OSError as error:
                assert error.errno == errno.EBADF
            else:
                raise AssertionError("real owned session descriptor was not closed")
            placements.append((fault, fd))
            raise OSError(errno.EIO, "owned session close observation failed")

    monkeypatch.setattr(os, "close", observe_owned_session_close)
    real_factory_lock = c.factory._lock

    class ObserveActualFactoryRelease:
        def acquire(self, *args, **kwargs):
            return real_factory_lock.acquire(*args, **kwargs)

        def locked(self):
            return real_factory_lock.locked()

        def release(self):
            real_factory_lock.release()
            assert not real_factory_lock.locked()
            if gate["approved"] and fault.endswith("after-release"):
                assert not placements
                assert c.c.now == original_clock
                if fault.startswith("original-authority"):
                    c.c.now["utc"] += timedelta(seconds=60)
                else:
                    c.c.now["mono"] = deadline["original"] + 1
                placements.append((fault, None))

    c.factory._lock = ObserveActualFactoryRelease()
    _native_install_action_observer(c, approve_and_observe_original_deadline)
    before = _native_known_active_snapshot(c)
    expected = (
        c.mod.Aborted if fault.startswith("original-host-budget") else c.mod.NativeActionFailure
    )
    with pytest.raises(expected) as failure:
        c.mod.execute(replace(invocation))
    if fault == "owned-session-close-eio":
        assert failure.value.code == "native-factory-uncertain"
        # No authority/source clock is changed. Real reacquisition proves the
        # original global writer released after the delegated session close.
        with c.boot.writer() as writer:
            writer.check()
    elif fault.startswith("original-authority"):
        assert failure.value.code == "native-factory-final-observation-expired"
        assert c.c.now["utc"] == original_clock["utc"] + timedelta(seconds=60)
        assert c.c.now["mono"] == original_clock["mono"]
    else:
        assert failure.value.kind == "timeout"
        assert c.c.now["mono"] == deadline["original"] + 1
        assert c.c.now["utc"] == original_clock["utc"]
    assert len(observations) == len(placements) == 1
    assert placements[0][0] == fault
    _assert_native_known_active_unchanged(c, before)


@pytest.mark.parametrize("fault", [None, "wrong-selector", "wrong-resource", "running-empty"])
def test_v2_oneoff_created_deferred_network_requires_real_identity(v2_oneoff_control, fault):
    case = v2_oneoff_control
    case.control.create()

    def deferred_network(value):
        value["NetworkSettings"]["Networks"]["synthetic-network"]["NetworkID"] = ""
        value["HostConfig"]["NetworkMode"] = (
            "4" * 64 if fault == "wrong-selector" else case.control.network_id
        )

    case.observations["alter"] = deferred_network
    if fault == "wrong-resource":
        case.observations["network_name"] = "foreign-network"
    if fault == "running-empty":
        case.observations["phase"] = "running"
    if fault is None:
        assert case.control._inspect(allowed={"created"})["Status"] == "created"
        assert [kind for kind, _ in case.events] == ["create", "inspect", "network-inspect"]
    else:
        with pytest.raises(
            case.host.module.LauncherError, match="native-oneoff-observation-invalid"
        ):
            case.control._inspect(allowed={"created", "running"})
    assert not any(kind == "start" for kind, _ in case.events)
