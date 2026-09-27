"""Verificador read-only de custodia S11 cerrada. LOCAL TEST / NON-EXPERIMENTAL.

Los fixtures se construyen con los escritores reales (`EvidenceStore`, `CustodyManager`,
`prepare/complete_custody_verification`, `destroy_expired_run`) en `.s11-test-artifacts/`, con
claves sintéticas. Ninguna prueba lee `var/s11-runs`, usa la clave real ni ejecuta Docker. Las
verificaciones corren con un espía que falla ante cualquier escritura y comparan el árbol byte a
byte antes y después.
"""

import builtins
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apps.experiments_app.canonical import canonical_json_bytes, sha256_bytes
from apps.experiments_app.constants import METHODOLOGY_VERSION, ArtifactKind, Classification
from apps.experiments_app.custody import (
    ATTESTED_SOURCE_PATHS,
    EXPECTED_BASELINE_SHA,
    CustodyManager,
    complete_custody_verification,
    prepare_custody_verification,
)
from apps.experiments_app.custody_policy import (
    PRIMARY_CUSTODIAN,
    create_custody_index_signature,
    create_custody_record_signature,
    create_custody_seal_signature,
    publication_public_key_from_private,
)
from apps.experiments_app.evidence import (
    EvidencePolicyError,
    EvidenceStore,
    validate_finalized_state,
)

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "s11_verify_closed_custody.py"
_SPEC = importlib.util.spec_from_file_location("s11_verify_closed_custody", SCRIPT)
verifier = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = verifier
# Cargar el script no deja bytecode en `backend/scripts/`, y su `sys.dont_write_bytecode` no
# se filtra al resto de la suite.
_BYTECODE_FLAG = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
    _SPEC.loader.exec_module(verifier)
finally:
    sys.dont_write_bytecode = _BYTECODE_FLAG

KEY_TEXT = "synthetic-custody-test-key-32-bytes-local"
KEY = KEY_TEXT.encode()
OTHER_KEY_TEXT = "another-synthetic-custody-key-32-bytes-x"
OTHER_KEY = OTHER_KEY_TEXT.encode()
PUBLICATION_KEY = publication_public_key_from_private(bytes(range(32)))
CREATED = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
FINISHED = CREATED + timedelta(hours=1)
RUN_A = "s11-00000000-0000-5000-8000-0000000000c1"
RUN_B = "s11-00000000-0000-5000-8000-0000000000c2"
OLD_RUN = "s11-00000000-0000-5000-8000-0000000000c3"
VERIFICATION = "s11-custody-20260926-p02p03-v9"
LEGACY = "s11-custody-20260921-p02p03-v1"
DOCUMENTED = verifier.Baseline(
    commit=None, anchors={}, documented_units=frozenset({RUN_A, RUN_B, VERIFICATION})
)
_DEFAULT = object()


# --- Construcción con los escritores reales -------------------------------------------


def _initial_payload(run_id, scenario_id):
    return {
        "artifact_references": [],
        "configuration": {},
        "dataset_id": "synthetic-test-v1",
        "dataset_sha256": "d" * 64,
        "environment": "isolated-experiment",
        "experiment_id": "S11-CLOSED-CUSTODY-TEST",
        "finished_at": None,
        "git_clean": True,
        "git_sha": "a" * 40,
        "invalidity_reason": None,
        "methodology_version": METHODOLOGY_VERSION,
        "run_id": run_id,
        "runtime": {"python": "test", "platform": "test"},
        "scenario_id": scenario_id,
        "schema_version": "s11-run-manifest-v1",
        "seed": 20260921,
        "started_at": "2026-09-21T12:00:00Z",
        "status": "PREPARED",
        "timezone": "America/Asuncion",
    }


def _store(primary, key=KEY):
    return EvidenceStore(
        primary,
        custody_hmac_key=key,
        publication_verification_key=PUBLICATION_KEY,
        clock=lambda: CREATED,
    )


def build_run(primary, run_id, *, scenario_id="EX-04", key=KEY):
    store = _store(primary, key)
    store.persist_json(
        run_id=run_id,
        scenario_id=scenario_id,
        artifact_type="run-manifest-initial",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="run-manifest.initial.json",
        payload=_initial_payload(run_id, scenario_id),
        source="test",
    )
    store.persist_json(
        run_id=run_id,
        scenario_id=scenario_id,
        artifact_type="source-export",
        kind=ArtifactKind.RAW,
        classification=Classification.EXPERIMENTAL,
        filename="source-export.json",
        payload={"synthetic": True, "rows": [1, 2, 3]},
        source="test",
    )
    store.append_jsonl(
        run_id=run_id, scenario_id=scenario_id, stream="http", payload={"event": "http.finished"}
    )
    store.register_existing(
        run_id=run_id,
        scenario_id=scenario_id,
        artifact_type="http-log",
        kind=ArtifactKind.LOG,
        classification=Classification.INTERNAL,
        filename="http.jsonl",
        source="test",
    )
    store.finalize_with_json_artifact(
        finished_at=FINISHED,
        run_id=run_id,
        scenario_id=scenario_id,
        artifact_type="run-manifest-final",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="run-manifest.final.json",
        payload={
            **_initial_payload(run_id, scenario_id),
            "artifact_references": [
                {
                    "artifact_id": record["artifact_id"],
                    "relative_path": record["relative_path"],
                    "sha256": record["sha256"],
                }
                for record in store.records(run_id)
            ],
            "finished_at": FINISHED.isoformat().replace("+00:00", "Z"),
            "status": "COMPLETED",
        },
        source="test",
    )
    return primary / "runs" / run_id


def build_verification(manager, verification_id):
    """Bundle con los mismos registros y enlaces que las verificaciones P02/P03 reales."""
    _, prepare_sha = manager.write_verification_record(
        verification_id,
        "prepare.json",
        {"phase": "PREPARED", "run_id": verification_id, "verification_id": verification_id},
    )
    _, intent_sha = manager.write_verification_record(
        verification_id,
        "destruction.intent.json",
        {"result": "PENDING", "run_id": verification_id, "verification_id": verification_id},
    )
    _, receipt_sha = manager.write_verification_record(
        verification_id,
        "destruction.json",
        {
            "intent_sha256": intent_sha,
            "remaining_artifact_ids": [],
            "result": "DESTROYED",
            "run_id": verification_id,
            "verification_id": verification_id,
        },
    )
    _, complete_sha = manager.write_verification_record(
        verification_id,
        "complete.json",
        {
            "destruction": {"intent_sha256": intent_sha, "receipt_sha256": receipt_sha},
            "phase": "COMPLETED",
            "prepare_evidence_sha256": prepare_sha,
            "source_snapshot": {"aggregate_sha256": "0" * 64},
            "verification_id": verification_id,
        },
    )
    manager.write_verification_record(
        verification_id,
        "attestation.json",
        {
            "complete_evidence_sha256": complete_sha,
            "phase": "ATTESTED",
            "verification_id": verification_id,
        },
    )
    return manager.primary_root / "custody-verifications" / verification_id


def _source_snapshot():
    files = {path: sha256_bytes(path.encode()) for path in ATTESTED_SOURCE_PATHS}
    return {
        "aggregate_sha256": sha256_bytes(canonical_json_bytes(files)),
        "baseline_sha": EXPECTED_BASELINE_SHA,
        "files": files,
    }


class Custody:
    def __init__(self, base):
        self.base = base
        self.primary = base / "s11-runs"
        self.backup = base / "s11-backup"
        self.restore = base / "s11-restore"

    def manager(self, key=KEY):
        return CustodyManager(
            self.primary,
            self.backup,
            self.restore,
            custody_hmac_key=key,
            publication_verification_key=PUBLICATION_KEY,
        )

    def roots(self):
        return {
            "primary_root": self.primary,
            "backup_root": self.backup,
            "restore_root": self.restore,
        }

    def run(self, run_id):
        return self.primary / "runs" / run_id

    def verification(self, verification_id=VERIFICATION):
        return self.primary / "custody-verifications" / verification_id

    def records(self, run_id):
        index = self.run(run_id) / "metadata/artifacts.jsonl"
        return [json.loads(line) for line in index.read_text().splitlines()]

    def final_manifest_sha(self, run_id):
        return next(
            r["sha256"] for r in self.records(run_id) if r["artifact_type"] == "run-manifest-final"
        )


@pytest.fixture
def custody(request):
    base = Path.cwd() / ".s11-test-artifacts" / f"closed-custody-{request.node.name}"
    _force_remove(base)
    tree = Custody(base)
    manager = tree.manager()
    manager.initialize_roots()
    build_run(tree.primary, RUN_A)
    build_run(tree.primary, RUN_B, scenario_id="EX-05")
    manager.backup_run(RUN_A, identity=PRIMARY_CUSTODIAN)
    manager.restore_run(RUN_A, identity=PRIMARY_CUSTODIAN)
    build_verification(manager, VERIFICATION)
    # Como en `var/s11-runs`: transitorios vacíos y control del harness fuera de custodia.
    (tree.primary / "custody-transactions").mkdir(mode=0o700)
    control = tree.primary / "control" / "harness"
    control.mkdir(parents=True)
    (control / f"{RUN_A}-EX-04.json").write_text("{}\n")
    yield tree
    _force_remove(base)


def _force_remove(path):
    if path.exists():
        for directory, _, _ in os.walk(path):
            os.chmod(directory, 0o700)
        shutil.rmtree(path)


def snapshot(root):
    state = {}
    for directory, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames + filenames):
            path = Path(directory) / name
            info = os.lstat(path)
            entry = (
                stat.S_IFMT(info.st_mode),
                info.st_mode & 0o7777,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )
            if stat.S_ISREG(info.st_mode):
                entry += (hashlib.sha256(path.read_bytes()).hexdigest(),)
            state[path.relative_to(root).as_posix()] = entry
    info = os.lstat(root)
    state["."] = (info.st_mode, info.st_mtime_ns, info.st_ctime_ns)
    return state


# --- Espía de escrituras --------------------------------------------------------------

_MUTATING_METHODS = {
    EvidenceStore: (
        "initialize_run",
        "persist_json",
        "persist_bytes",
        "append_jsonl",
        "register_existing",
        "finalize_verification_retention",
        "finalize_with_json_artifact",
        "recover_finalization",
        "records",
        "verify",
        "write_checksums",
        "write_index_signature",
        "mutation",
        "_mutation_lock",
    ),
    CustodyManager: (
        "__init__",
        "initialize_roots",
        "backup_run",
        "restore_run",
        "destroy_expired_run",
        "write_verification_record",
        "verify_verification_record",
        "_validated_records",
    ),
}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


class WriteSpy:
    """Se activa justo antes de verificar: cualquier escritura o mutación falla."""

    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch
        self.read_opens = 0

    def forbid(self, name):
        def refuse(*args, **kwargs):
            raise AssertionError(f"el verificador intentó escribir o mutar: {name}")

        return refuse

    def __call__(self):
        patch = self.monkeypatch.setattr
        real_open = builtins.open
        real_os_open = os.open

        def guarded_open(file, mode="r", *args, **kwargs):
            if set(str(mode)) & set("wax+"):
                raise AssertionError(f"open en escritura: {mode}")
            self.read_opens += 1
            return real_open(file, mode, *args, **kwargs)

        def guarded_os_open(path, flags, *args, **kwargs):
            if flags & _WRITE_FLAGS:
                raise AssertionError("os.open en escritura")
            self.read_opens += 1
            return real_os_open(path, flags, *args, **kwargs)

        patch(builtins, "open", guarded_open)
        patch(io, "open", guarded_open)
        patch(os, "open", guarded_os_open)
        for module, names in (
            (os, ("mkdir", "makedirs", "chmod", "fchmod", "replace", "rename", "remove")),
            (os, ("unlink", "rmdir", "utime", "truncate", "ftruncate", "symlink", "link")),
            (os, ("chown", "setxattr", "removexattr")),
            (fcntl, ("flock", "lockf")),
            (shutil, ("rmtree", "copytree", "copyfile", "copy2", "move")),
            (tempfile, ("mkstemp", "mkdtemp", "NamedTemporaryFile")),
        ):
            for name in names:
                patch(module, name, self.forbid(f"{module.__name__}.{name}"))
        for name in ("write_text", "write_bytes", "touch", "mkdir", "chmod", "unlink", "rename"):
            patch(Path, name, self.forbid(f"Path.{name}"))
        for cls, names in _MUTATING_METHODS.items():
            for name in names:
                patch(cls, name, self.forbid(f"{cls.__name__}.{name}"))
        return self


@pytest.fixture
def forbid_writes(custody):
    # Parches propios, no los de `monkeypatch`: el `autouse` de `backend/conftest.py` lo
    # desmonta después de `custody`. Depender de `custody` retira el espía antes de que
    # `custody` restaure permisos y limpie; no invertir este orden.
    with pytest.MonkeyPatch.context() as patcher:
        yield WriteSpy(patcher)


def verify(custody, *, key=KEY, baseline=_DEFAULT, **kwargs):
    return verifier.verify_custody(
        **custody.roots(),
        custody_key=key,
        publication_key=PUBLICATION_KEY,
        baseline=DOCUMENTED if baseline is _DEFAULT else baseline,
        **kwargs,
    )


def codes(report, unit_id=None, severity=None):
    found = set()
    for unit in report["units"]:
        if unit_id is None or unit["unit"] == unit_id:
            found |= {
                finding["code"]
                for finding in unit["findings"]
                if severity is None or finding["severity"] == severity
            }
    return found


def root_codes(report):
    return {finding["code"] for finding in report["root_findings"]}


def status_of(report, unit_id):
    return next(unit["status"] for unit in report["units"] if unit["unit"] == unit_id)


def _cli(custody, *extra, key=KEY_TEXT, env_extra=None, python_flags=()):
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        verifier.PUBLICATION_KEY_ENV: PUBLICATION_KEY.hex(),
    }
    if key is not None:
        environment[verifier.KEY_ENV] = key
    environment.update(env_extra or {})
    return subprocess.run(
        [
            sys.executable,
            *python_flags,
            str(SCRIPT),
            "--primary-root",
            str(custody.primary),
            "--backup-root",
            str(custody.backup),
            "--restore-root",
            str(custody.restore),
            *extra,
        ],
        capture_output=True,
        text=True,
        env={k: v for k, v in environment.items() if v is not None},
        timeout=120,
    )


def _assert_no_secret(*texts):
    for text in texts:
        for secret in (KEY_TEXT, KEY.hex(), OTHER_KEY_TEXT, OTHER_KEY.hex()):
            assert secret not in text


def _git_repo(path, text):
    path.mkdir(parents=True)
    (path / "docs").mkdir()
    (path / "docs" / "RECORD.md").write_text(text)
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        "HOME": str(path),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    for command in (["init", "-q"], ["add", "docs"], ["commit", "-qm", "baseline"]):
        subprocess.run(["git", "-C", str(path), *command], check=True, env=environment)
    head = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    return head.stdout.strip(), environment


def _documenting_repo(tmp_path, *extra_lines):
    lines = [f"Run `{RUN_A}`, run `{RUN_B}`, verificación `{VERIFICATION}`.", *extra_lines]
    commit, _ = _git_repo(tmp_path / "repo", "\n".join(lines) + "\n")
    return tmp_path / "repo", commit


def test_write_spy_blocks_mutations_until_the_test_ends(custody, forbid_writes):
    # Activo durante toda la prueba; la limpieza de `custody` (con `os.chmod`) sólo pasa
    # si el espía ya se retiró.
    forbid_writes()
    with pytest.raises(AssertionError, match="os.chmod"):
        os.chmod(custody.base, 0o700)
    with pytest.raises(AssertionError, match="open en escritura"):
        open(custody.base / "extra.txt", "w")
    assert not (custody.base / "extra.txt").exists()


# --- Evidencia válida: PASS sin cambiar un byte ---------------------------------------


def test_valid_closed_evidence_passes_without_changing_a_byte(custody, forbid_writes):
    before = snapshot(custody.base)
    forbid_writes()
    report = verify(custody)
    assert snapshot(custody.base) == before
    assert report["overall"] == "PASS"
    assert report["summary"] == {
        "fail": 0,
        "historical_anchored": 0,
        "historical_unanchored": 0,
        "incomplete": 0,
        "pass": 5,
        "units": 5,
    }
    assert {unit["unit"] for unit in report["units"]} == {
        f"primary:runs/{RUN_A}",
        f"primary:runs/{RUN_B}",
        f"backup:runs/{RUN_A}",
        f"restore:runs/{RUN_A}",
        f"primary:custody-verifications/{VERIFICATION}",
    }
    assert report["root_findings"] == []
    assert report["completeness_basis"] == ["git-documents"]
    assert report["uncovered"] == {"primary:control": {"files": 1}}
    assert report["custody_key_fingerprint"] == hashlib.sha256(KEY).hexdigest()[:16]
    assert report["verifier_sha256"] == hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    assert forbid_writes.read_opens > 0


def test_verifier_agrees_with_the_existing_read_only_validators(custody):
    run = custody.run(RUN_A)
    records = custody.records(RUN_A)
    assert validate_finalized_state(run, records, custody_hmac_key=KEY) is not None
    assert custody.manager()._validated_records(run) == records
    assert custody.manager().verify_verification_record(VERIFICATION, "complete.json")
    assert verify(custody)["overall"] == "PASS"


def test_verification_runs_on_a_read_only_filesystem(custody):
    if os.geteuid() == 0:
        pytest.skip("root ignora los permisos de sólo lectura")
    before = snapshot(custody.base)
    for directory, _dirnames, filenames in os.walk(custody.base):
        for name in filenames:
            os.chmod(Path(directory) / name, 0o400)
    for directory, _, _ in sorted(os.walk(custody.base), reverse=True):
        os.chmod(directory, 0o500)
    try:
        report = verify(custody)
    finally:
        for directory, _, _ in os.walk(custody.base):
            os.chmod(directory, 0o700)
    assert report["overall"] == "PASS"
    after = snapshot(custody.base)
    assert {k: (v[0], v[2], v[3], *v[5:]) for k, v in after.items() if k != "."} == {
        k: (v[0], v[2], v[3], *v[5:]) for k, v in before.items() if k != "."
    }


def test_cli_passes_under_its_guard_with_baseline_and_comparison(custody, tmp_path):
    repo, commit = _documenting_repo(tmp_path)
    before = snapshot(custody.base)
    first = _cli(
        custody,
        "--expect-fingerprint",
        hashlib.sha256(KEY).hexdigest()[:16],
        "--baseline-git-commit",
        commit,
        "--repo-root",
        str(repo),
    )
    assert first.returncode == 0, first.stdout
    report = json.loads(first.stdout)
    assert report["overall"] == "PASS"
    assert report["read_only_guard"] == "audit-hook"
    assert report["baseline"]["commit"] == commit
    previous = tmp_path / "previous.json"
    previous.write_text(first.stdout)
    second = _cli(
        custody,
        "--compare-report",
        str(previous),
        "--baseline-git-commit",
        commit,
        "--repo-root",
        str(repo),
    )
    assert second.returncode == 0, second.stdout
    compared = json.loads(second.stdout)
    assert compared["comparison"]["units_compared"] == 5
    assert compared["completeness_basis"] == ["git-documents", "compare-report"]
    assert compared["compare_report_sha256"] == sha256_bytes(canonical_json_bytes(report))
    assert snapshot(custody.base) == before
    _assert_no_secret(first.stdout, first.stderr, second.stdout, second.stderr)


def test_cli_refuses_to_run_without_python_b(custody):
    result = _cli(custody, env_extra={"PYTHONDONTWRITEBYTECODE": None})
    assert result.returncode == 3
    assert json.loads(result.stdout) == {"error": "requires-python-B", "overall": "ERROR"}


def test_cli_with_python_b_never_writes_bytecode(custody, tmp_path):
    prefix = tmp_path / "pycache"
    result = _cli(
        custody,
        env_extra={"PYTHONDONTWRITEBYTECODE": None, "PYTHONPYCACHEPREFIX": str(prefix)},
        python_flags=("-B",),
    )
    # Sin línea base el resultado es INCOMPLETE, pero la ejecución completa sí ocurrió.
    assert result.returncode == 2, result.stdout
    assert not prefix.exists() or not any(path.is_file() for path in prefix.rglob("*"))


def test_write_guard_blocks_every_kind_of_write(tmp_path):
    repo, _ = _git_repo(tmp_path / "repo", "x\n")
    probe = f"""
import fcntl, importlib.util, json, mmap, os, socket, sqlite3, subprocess, sys
spec = importlib.util.spec_from_file_location("v", {str(SCRIPT)!r})
module = importlib.util.module_from_spec(spec)
sys.modules["v"] = module
spec.loader.exec_module(module)
readable = open({str(SCRIPT)!r}, "rb")
module.install_write_guard()
target = {str(tmp_path / "x")!r}
git = module._GIT_EXECUTABLE
results = {{}}
for label, action in (
    ("open-w", lambda: open(target, "w")),
    ("open-a", lambda: open(target, "ab")),
    ("os-open-create", lambda: os.open(target, os.O_WRONLY | os.O_CREAT)),
    ("mkdir", lambda: os.mkdir(target + "-dir")),
    ("chmod", lambda: os.chmod({str(tmp_path)!r}, 0o700)),
    ("rename", lambda: os.rename({str(tmp_path)!r}, target + "-moved")),
    ("remove", lambda: os.remove(target)),
    ("flock", lambda: fcntl.flock(readable.fileno(), fcntl.LOCK_EX)),
    ("setxattr", lambda: os.setxattr({str(SCRIPT)!r}, "user.x", b"1")),
    ("sqlite", lambda: sqlite3.connect(target + ".db")),
    ("mmap", lambda: mmap.mmap(-1, 16)),
    ("socket", lambda: socket.create_connection(("127.0.0.1", 9))),
    ("subprocess", lambda: subprocess.run(["true"])),
    ("git-output", lambda: subprocess.run(
        [git, "-C", {str(repo)!r}, "show", "--output=" + target, "HEAD"]
    )),
):
    try:
        action()
        results[label] = "ALLOWED"
    except module.WriteAttemptError:
        results[label] = "BLOCKED"
    except Exception as exc:
        results[label] = type(exc).__name__
results["read"] = len(readable.read()) > 0
sys.stdout.write(json.dumps(results))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=60,
    )
    outcome = json.loads(result.stdout)
    assert outcome.pop("read") is True
    assert outcome == dict.fromkeys(outcome, "BLOCKED")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["repo"]


def test_write_spy_is_not_vacuous(custody, forbid_writes):
    handle = builtins.open(custody.run(RUN_A) / "SHA256SUMS", "rb")
    forbid_writes()
    for attempt in (
        lambda: (custody.base / "x").write_text("x"),
        lambda: open(custody.base / "y", "w"),  # noqa: SIM115
        lambda: os.open(custody.base / "z", os.O_WRONLY | os.O_CREAT),
        lambda: os.chmod(custody.base, 0o700),
        lambda: os.fchmod(handle.fileno(), 0o600),
        lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_EX),
        lambda: custody.manager(),
        lambda: _store(custody.primary).records(RUN_A),
    ):
        with pytest.raises(AssertionError):
            attempt()
    handle.close()
    assert not (custody.base / "x").exists()


# --- Fallo cerrado ----------------------------------------------------------------------


def _resign_run(run, key=KEY):
    """Simula a quien tiene una clave: re-firma registros, índice, sello y SHA256SUMS."""
    index = run / "metadata/artifacts.jsonl"
    records = [json.loads(line) for line in index.read_text().splitlines()]
    for record in records:
        record["sha256"] = hashlib.sha256((run / record["relative_path"]).read_bytes()).hexdigest()
        record["size_bytes"] = (run / record["relative_path"]).stat().st_size
        record["custody_signature"] = None
        record["custody_signature"] = create_custody_record_signature(record, custody_hmac_key=key)
    content = b"".join(canonical_json_bytes(record) for record in records)
    index.write_bytes(content)
    (run / "metadata/artifacts.jsonl.hmac").write_text(
        create_custody_index_signature(content, custody_hmac_key=key) + "\n"
    )
    final = next(r for r in records if r["artifact_type"] == "run-manifest-final")
    manifest = json.loads((run / final["relative_path"]).read_text())
    payload = {
        "artifact_id": final["artifact_id"],
        "finished_at": manifest["finished_at"],
        "manifest_sha256": final["sha256"],
        "run_id": final["run_id"],
    }
    (run / ".s11-finalized").write_bytes(
        canonical_json_bytes(
            {
                "payload": payload,
                "signature": create_custody_seal_signature(
                    canonical_json_bytes(payload), custody_hmac_key=key
                ),
            }
        )
    )
    _rewrite_checksums(run)


def _rewrite_checksums(run):
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(run).as_posix()}"
        for path in sorted(run.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS"
    ]
    (run / "SHA256SUMS").write_text("\n".join(lines) + "\n")


def _edit_manifest(run, change):
    manifest = run / "metadata/EX-05/run-manifest.final.json"
    payload = json.loads(manifest.read_text())
    change(payload)
    manifest.write_bytes(canonical_json_bytes(payload))


def test_altered_artifact_fails(custody, forbid_writes):
    target = custody.run(RUN_A) / "raw/EX-04/source-export.json"
    target.write_bytes(target.read_bytes().replace(b"1", b"9"))
    forbid_writes()
    report = verify(custody)
    assert report["overall"] == "FAIL"
    assert {"record-sha256", "checksums-mismatch"} <= codes(report, f"primary:runs/{RUN_A}")
    assert "copy-diverges-from-primary" in codes(report, f"backup:runs/{RUN_A}")


def test_injected_unindexed_file_fails_even_with_regenerated_checksums(custody, forbid_writes):
    run = custody.run(RUN_B)
    forged = run / "raw" / "EX-05" / "forged-extra-evidence.json"
    forged.write_text('{"forged": true}')
    os.chmod(forged, 0o600)
    _rewrite_checksums(run)
    forbid_writes()
    report = verify(custody)
    assert codes(report, f"primary:runs/{RUN_B}") == {"unindexed-file"}
    assert report["overall"] == "FAIL"


def test_altered_artifacts_index_fails(custody, forbid_writes):
    index = custody.run(RUN_B) / "metadata/artifacts.jsonl"
    lines = index.read_text().splitlines()
    record = json.loads(lines[1])
    record["classification"] = "INTERNAL"
    lines[1] = canonical_json_bytes(record).decode().rstrip("\n")
    index.write_text("\n".join(lines) + "\n")
    forbid_writes()
    report = verify(custody)
    assert report["overall"] == "FAIL"
    assert {"index-signature", "record-signature"} <= codes(report, f"primary:runs/{RUN_B}")


def test_wrong_index_hmac_fails(custody, forbid_writes):
    (custody.run(RUN_B) / "metadata/artifacts.jsonl.hmac").write_text("0" * 64 + "\n")
    forbid_writes()
    report = verify(custody)
    assert "index-signature" in codes(report, f"primary:runs/{RUN_B}")
    assert report["overall"] == "FAIL"


def _zero_first_digest(lines):
    return ["0" * 64 + lines[0][64:], *lines[1:]]


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (_zero_first_digest, "checksums-mismatch"),
        (lambda lines: lines[1:], "checksums-missing-entry"),
        (lambda lines: [*lines, "f" * 64 + "  raw/EX-05/ghost.json"], "checksums-extra-entry"),
        (lambda lines: [*lines, "not a checksum line"], "checksums-format"),
    ],
    ids=["digest", "missing", "extra", "format"],
)
def test_incorrect_sha256sums_fails(custody, forbid_writes, mutation, code):
    checksums = custody.run(RUN_B) / "SHA256SUMS"
    checksums.write_text("\n".join(mutation(checksums.read_text().splitlines())) + "\n")
    forbid_writes()
    report = verify(custody)
    assert code in codes(report, f"primary:runs/{RUN_B}")
    assert report["overall"] == "FAIL"


def test_altered_seal_fails(custody, forbid_writes):
    seal = custody.run(RUN_B) / ".s11-finalized"
    envelope = json.loads(seal.read_text())
    envelope["payload"]["finished_at"] = "2026-09-21T14:00:00Z"
    seal.write_bytes(canonical_json_bytes(envelope))
    forbid_writes()
    assert {"seal-signature", "seal-linkage", "checksums-mismatch"} <= codes(
        verify(custody), f"primary:runs/{RUN_B}"
    )


def test_removed_seal_fails(custody, forbid_writes):
    (custody.run(RUN_B) / ".s11-finalized").unlink()
    forbid_writes()
    assert {"seal-missing", "checksums-extra-entry"} <= codes(
        verify(custody), f"primary:runs/{RUN_B}"
    )


def test_forged_but_validly_signed_seal_still_fails_linkage(custody, forbid_writes):
    seal = custody.run(RUN_B) / ".s11-finalized"
    payload = {**json.loads(seal.read_text())["payload"], "manifest_sha256": "e" * 64}
    seal.write_bytes(
        canonical_json_bytes(
            {
                "payload": payload,
                "signature": create_custody_seal_signature(
                    canonical_json_bytes(payload), custody_hmac_key=KEY
                ),
            }
        )
    )
    _rewrite_checksums(custody.run(RUN_B))
    forbid_writes()
    assert codes(verify(custody), f"primary:runs/{RUN_B}") == {"seal-linkage"}


def _drop_last_reference(manifest):
    manifest["artifact_references"] = manifest["artifact_references"][:-1]


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (_drop_last_reference, "manifest-references"),
        (lambda manifest: manifest.update(git_sha="b" * 40), "manifest-provenance"),
        (lambda manifest: manifest.update(invalidity_reason="x"), "manifest-invalidity-reason"),
    ],
    ids=["references", "provenance", "invalidity"],
)
def test_corrupt_finalization_fails_even_when_resigned(custody, forbid_writes, change, code):
    run = custody.run(RUN_B)
    _edit_manifest(run, change)
    _resign_run(run)
    forbid_writes()
    assert codes(verify(custody), f"primary:runs/{RUN_B}") == {code}


def test_broken_retention_anchor_fails_even_when_resigned(custody, forbid_writes):
    run = custody.run(RUN_B)
    index = run / "metadata/artifacts.jsonl"
    records = [json.loads(line) for line in index.read_text().splitlines()]
    records[1]["expires_at"] = "2030-01-01T00:00:00Z"
    index.write_bytes(b"".join(canonical_json_bytes(record) for record in records))
    _resign_run(run)
    forbid_writes()
    assert codes(verify(custody), f"primary:runs/{RUN_B}") == {"retention"}


@pytest.mark.parametrize(
    "damage",
    [
        lambda record: record.update(artifact_id=["no", "hashable"]),
        lambda record: record.update(size_bytes=float("nan")),
        lambda record: record.update(sha256=None),
    ],
    ids=["unhashable-id", "nan", "null-sha"],
)
def test_malformed_records_fail_closed_without_crashing(custody, forbid_writes, damage):
    index = custody.run(RUN_B) / "metadata/artifacts.jsonl"
    lines = index.read_text().splitlines()
    record = json.loads(lines[1])
    damage(record)
    lines[1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    index.write_text("\n".join(lines) + "\n")
    forbid_writes()
    anchored = verifier.Baseline(None, {"0" * 64: ["docs/X.md"]}, DOCUMENTED.documented_units)
    report = verify(custody, baseline=anchored)
    found = codes(report, f"primary:runs/{RUN_B}")
    assert "record-types" in found
    assert "unexpected-structure" not in found
    assert report["overall"] == "FAIL"


def test_persisted_canary_secret_fails(custody, forbid_writes):
    run = custody.run(RUN_B)
    (run / "raw" / "EX-05" / "leak.json").write_text('{"x": "S11-CANARY-MUST-NOT-PERSIST"}')
    forbid_writes()
    assert "canary-secret-persisted" in codes(verify(custody), f"primary:runs/{RUN_B}")


def test_pending_transactions_fail(custody, forbid_writes):
    (custody.primary / "custody-transactions" / f"{VERIFICATION}.json").write_text("{}")
    (custody.primary / "finalization-transactions" / f"{RUN_B}.json").write_text("{}")
    forbid_writes()
    report = verify(custody)
    assert report["overall"] == "FAIL"
    assert {"pending-custody-transaction", "pending-finalization-journal"} <= root_codes(report)


def test_missing_indexed_file_fails(custody, forbid_writes):
    (custody.run(RUN_B) / "logs/EX-05/http.jsonl").unlink()
    forbid_writes()
    assert {"record-path", "checksums-extra-entry"} <= codes(
        verify(custody), f"primary:runs/{RUN_B}"
    )


def test_symlink_inside_evidence_fails(custody, forbid_writes):
    (custody.run(RUN_B) / "derived" / "link").symlink_to("/etc/hostname")
    forbid_writes()
    assert "symlink" in codes(verify(custody), f"primary:runs/{RUN_B}")


def test_unsafe_filename_fails(custody, forbid_writes):
    (custody.run(RUN_B) / "derived" / "name with spaces.json").write_text("{}")
    forbid_writes()
    assert "unsafe-filename" in codes(verify(custody), f"primary:runs/{RUN_B}")


def test_fifo_inside_evidence_fails_without_blocking(custody, forbid_writes):
    os.mkfifo(custody.run(RUN_B) / "derived" / "pipe")
    forbid_writes()
    assert "non-regular-entry" in codes(verify(custody), f"primary:runs/{RUN_B}")


def test_unexpected_root_entry_fails(custody, forbid_writes):
    (custody.primary / "stray").write_text("x")
    forbid_writes()
    assert "unexpected-root-entry" in root_codes(verify(custody))


def test_wrong_key_fails_every_authenticated_record(custody, forbid_writes):
    forbid_writes()
    report = verify(custody, key=OTHER_KEY)
    assert report["overall"] == "FAIL"
    assert {"index-signature", "record-signature", "seal-signature"} <= codes(
        report, f"primary:runs/{RUN_A}"
    )
    assert "record-signature" in codes(report, f"primary:custody-verifications/{VERIFICATION}")
    assert report["custody_key_fingerprint"] == hashlib.sha256(OTHER_KEY).hexdigest()[:16]


def test_expected_fingerprint_mismatch_stops_before_verifying(custody):
    result = _cli(
        custody, "--expect-fingerprint", hashlib.sha256(KEY).hexdigest()[:16], key=OTHER_KEY_TEXT
    )
    assert result.returncode == 3
    assert json.loads(result.stdout) == {
        "error": "custody-key-fingerprint-mismatch",
        "overall": "ERROR",
    }
    _assert_no_secret(result.stdout, result.stderr)


# --- Ilegible: INCOMPLETE, nunca FAIL ---------------------------------------------------


def _skip_if_root():
    if os.geteuid() == 0:
        pytest.skip("root lee sin permisos; la variante con monkeypatch cubre el caso")


def test_unreadable_file_makes_the_result_incomplete(custody):
    _skip_if_root()
    target = custody.run(RUN_B) / "raw/EX-05/source-export.json"
    os.chmod(target, 0o000)
    try:
        report = verify(custody)
        result = _cli(custody)
    finally:
        os.chmod(target, 0o600)
    assert report["overall"] == "INCOMPLETE"
    assert codes(report, f"primary:runs/{RUN_B}", severity="fail") == set()
    assert "unreadable-file" in codes(report, f"primary:runs/{RUN_B}")
    assert result.returncode == 2
    _assert_no_secret(result.stdout, result.stderr)


def test_unreadable_directory_inside_a_run_is_incomplete_not_fail(custody):
    _skip_if_root()
    target = custody.run(RUN_B) / "raw"
    os.chmod(target, 0o000)
    try:
        report = verify(custody)
    finally:
        os.chmod(target, 0o700)
    assert report["overall"] == "INCOMPLETE"
    assert codes(report, f"primary:runs/{RUN_B}", severity="fail") == set()
    assert {"unreadable-directory", "record-path", "checksums-extra-entry"} <= codes(
        report, f"primary:runs/{RUN_B}", severity="incomplete"
    )


def test_unreadable_verification_directory_is_incomplete(custody):
    _skip_if_root()
    os.chmod(custody.verification(), 0o000)
    try:
        report = verify(custody)
    finally:
        os.chmod(custody.verification(), 0o700)
    assert report["overall"] == "INCOMPLETE"
    assert "unreadable-directory" in codes(report, f"primary:custody-verifications/{VERIFICATION}")


@pytest.mark.parametrize("blocked", ["metadata/artifacts.jsonl", "metadata"], ids=["file", "dir"])
def test_unreadable_index_is_incomplete_not_unindexed_files(custody, blocked):
    _skip_if_root()
    target = custody.run(RUN_B) / blocked
    os.chmod(target, 0o000)
    try:
        report = verify(custody)
    finally:
        os.chmod(target, 0o700 if target.is_dir() else 0o600)
    assert report["overall"] == "INCOMPLETE"
    assert codes(report, f"primary:runs/{RUN_B}", severity="fail") == set()
    assert "unindexed-file" not in codes(report, f"primary:runs/{RUN_B}")


def test_cross_reference_to_an_unreadable_record_is_incomplete(custody):
    _skip_if_root()
    target = custody.verification() / "prepare.json"
    os.chmod(target, 0o000)
    try:
        report = verify(custody)
    finally:
        os.chmod(target, 0o600)
    unit = f"primary:custody-verifications/{VERIFICATION}"
    assert report["overall"] == "INCOMPLETE"
    assert codes(report, unit, severity="fail") == set()
    assert "cross-reference-unverifiable" in codes(report, unit, severity="incomplete")


def test_unreadable_file_in_a_copied_run_does_not_fail_the_copies(custody, monkeypatch):
    real = verifier._hash_and_scan
    blocked = (custody.run(RUN_A) / "raw/EX-04/source-export.json").resolve()

    def denied(path):
        if Path(path).resolve() == blocked:
            raise PermissionError(13, "denied")
        return real(path)

    monkeypatch.setattr(verifier, "_hash_and_scan", denied)
    report = verify(custody)
    assert report["overall"] == "INCOMPLETE"
    assert status_of(report, f"backup:runs/{RUN_A}") == "INCOMPLETE"
    assert "copy-comparison-incomplete" in codes(report, f"backup:runs/{RUN_A}")
    assert codes(report, severity="fail") == set()


# --- Copias --------------------------------------------------------------------------


def test_divergent_backup_and_restore_fail(custody, forbid_writes):
    backup_file = custody.backup / "runs" / RUN_A / "logs/EX-04/http.jsonl"
    backup_file.write_bytes(backup_file.read_bytes() + b"\n")
    forbid_writes()
    report = verify(custody)
    assert "copy-diverges-from-primary" in codes(report, f"backup:runs/{RUN_A}")
    assert "restore-diverges-from-backup" in codes(report, f"restore:runs/{RUN_A}")


def test_copy_without_primary_fails(custody):
    custody.manager().backup_run(RUN_B, identity=PRIMARY_CUSTODIAN)
    _force_remove(custody.run(RUN_B))
    report = verify(custody)
    assert "copy-without-primary" in codes(report, f"backup:runs/{RUN_B}")
    assert "documented-unit-missing" in root_codes(report)


# --- Completitud ------------------------------------------------------------------------


def test_without_a_completeness_basis_the_result_is_incomplete(custody, forbid_writes):
    forbid_writes()
    report = verify(custody, baseline=None)
    assert report["overall"] == "INCOMPLETE"
    assert "unit-completeness-unverified" in root_codes(report)


def test_deleted_documented_units_fail(custody):
    _force_remove(custody.verification())
    _force_remove(custody.run(RUN_B))
    report = verify(custody)
    assert report["overall"] == "FAIL"
    missing = {
        finding["subject"]
        for finding in report["root_findings"]
        if finding["code"] == "documented-unit-missing"
    }
    assert missing == {RUN_B, VERIFICATION}


def test_partially_deleted_bundle_fails(custody, forbid_writes):
    for name in ("complete.json", "attestation.json"):
        for suffix in ("", ".sha256", ".hmac", ".commit"):
            (custody.verification() / f"{name}{suffix}").unlink()
    forbid_writes()
    report = verify(custody)
    assert "bundle-set" in codes(report, f"primary:custody-verifications/{VERIFICATION}")
    assert report["overall"] == "FAIL"


def test_undocumented_units_are_reported(custody):
    build_run(custody.primary, OLD_RUN)
    report = verify(custody)
    assert report["overall"] == "PASS"
    assert report["documentation"]["units_not_documented"] == [OLD_RUN]


# --- Flujos reales de los escritores --------------------------------------------------


def test_real_p02_p03_verification_requires_its_attestation(custody):
    manager = custody.manager()
    verification_id = "s11-custody-20260926-p02p03-real"
    prepare_custody_verification(
        manager,
        verification_id=verification_id,
        clock=lambda: CREATED,
        execution_context={"hostname": "test-node", "pid": 100},
        source_snapshot=_source_snapshot(),
    )
    complete_custody_verification(
        custody.manager(),
        verification_id=verification_id,
        persistence_boundary="independent-process",
        execution_context={"hostname": "test-node", "pid": 101},
        source_snapshot=_source_snapshot(),
    )
    unit_id = f"primary:custody-verifications/{verification_id}"
    assert codes(verify(custody), unit_id) == {"bundle-set"}
    complete = custody.verification(verification_id) / "complete.json"
    manager.write_verification_record(
        verification_id,
        "attestation.json",
        {
            "complete_evidence_sha256": hashlib.sha256(complete.read_bytes()).hexdigest(),
            "phase": "ATTESTED",
            "verification_id": verification_id,
        },
    )
    report = verify(custody)
    assert status_of(report, unit_id) == "PASS"
    assert not any(unit["unit"].endswith(f"runs/{verification_id}") for unit in report["units"])
    assert report["overall"] == "PASS"


def test_real_retention_destruction_leaves_a_verifiable_sealed_run(custody):
    custody.manager().destroy_expired_run(
        RUN_B,
        verification_id="s11-retention-20261221-v1",
        identity=PRIMARY_CUSTODIAN,
        now=FINISHED + timedelta(days=91),
    )
    assert [record["classification"] for record in custody.records(RUN_B)] == ["EXPERIMENTAL"]
    report = verify(custody)
    assert status_of(report, f"primary:runs/{RUN_B}") == "PASS"
    assert status_of(report, "primary:custody-verifications/s11-retention-20261221-v1") == "PASS"
    assert report["overall"] == "PASS"


# --- Verificaciones custodiales -------------------------------------------------------


def test_tampered_verification_record_fails(custody, forbid_writes):
    target = custody.verification() / "complete.json"
    target.write_bytes(target.read_bytes().replace(b"COMPLETED", b"COMPLETEX"))
    forbid_writes()
    found = codes(verify(custody), f"primary:custody-verifications/{VERIFICATION}")
    assert {"sha256-mismatch", "commit-marker", "record-signature", "cross-reference"} <= found


@pytest.mark.parametrize(
    ("damage", "code"),
    [
        (lambda d: (d / "prepare.json.commit").unlink(), "bundle-incomplete"),
        (lambda d: (d / "prepare.json.hmac").write_text("1" * 64 + "\n"), "record-signature"),
        (lambda d: (d / "stray.txt").write_text("x"), "unexpected-file"),
        (lambda d: (d / "prepare.json.sha256").write_text("garbage\n"), "sha256-sidecar-format"),
    ],
    ids=["commit-missing", "hmac-wrong", "stray-file", "sidecar-format"],
)
def test_damaged_verification_bundle_fails(custody, forbid_writes, damage, code):
    damage(custody.verification())
    forbid_writes()
    assert code in codes(verify(custody), f"primary:custody-verifications/{VERIFICATION}")


def test_broken_cross_reference_fails_with_valid_signatures(custody):
    manager = custody.manager()
    other = "s11-custody-20260926-p02p03-other"
    manager.write_verification_record(
        other,
        "complete.json",
        {"phase": "COMPLETED", "prepare_evidence_sha256": "0" * 64, "verification_id": other},
    )
    manager.write_verification_record(
        other, "prepare.json", {"phase": "PREPARED", "verification_id": other}
    )
    assert codes(verify(custody), f"primary:custody-verifications/{other}") == {"cross-reference"}


def test_residual_canary_run_fails(custody):
    _store(custody.primary).persist_json(
        run_id=VERIFICATION,
        scenario_id="P02-P03",
        artifact_type="custody-canary",
        kind=ArtifactKind.RAW,
        classification=Classification.INTERNAL,
        filename="custody-canary.json",
        payload={"synthetic": True},
        source="test",
    )
    report = verify(custody)
    assert "canary-residual" in codes(report, f"primary:custody-verifications/{VERIFICATION}")


# --- Evidencia histórica declarada ----------------------------------------------------


def _legacy_verification(primary, verification_id, payload=None):
    directory = primary / "custody-verifications" / verification_id
    directory.mkdir(mode=0o700)
    content = canonical_json_bytes(
        {"phase": "COMPLETED", "verification_id": verification_id, **(payload or {})}
    )
    (directory / "complete.json").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    (directory / "complete.json.sha256").write_text(f"{digest}  complete.json\n")
    return digest


@pytest.fixture
def history(custody):
    build_run(custody.primary, OLD_RUN, key=OTHER_KEY)
    legacy_sha = _legacy_verification(
        custody.primary,
        LEGACY,
        {"run_id": OLD_RUN, "manifest_sha256": custody.final_manifest_sha(OLD_RUN)},
    )
    historical = frozenset({f"primary:runs/{OLD_RUN}", f"primary:custody-verifications/{LEGACY}"})
    return custody, historical, legacy_sha


def test_historical_mode_requires_a_baseline(history):
    custody, historical, _ = history
    with pytest.raises(verifier.VerifierConfigurationError, match="historical-requires-baseline"):
        verify(custody, baseline=None, historical=historical)


def test_unanchored_historical_evidence_never_yields_plain_pass(history):
    custody, historical, _ = history
    assert verify(custody)["overall"] == "FAIL"
    report = verify(custody, historical=historical)
    assert report["overall"] == "PASS_WITH_UNANCHORED_HISTORICAL"
    assert status_of(report, f"primary:runs/{OLD_RUN}") == "HISTORICAL_UNANCHORED"
    assert status_of(report, f"primary:custody-verifications/{LEGACY}") == "HISTORICAL_UNANCHORED"


def test_documented_record_anchors_historical_evidence_transitively(history):
    custody, historical, legacy_sha = history
    baseline = verifier.Baseline(None, {legacy_sha: ["docs/X.md"]}, DOCUMENTED.documented_units)
    report = verify(custody, historical=historical, baseline=baseline)
    assert status_of(report, f"primary:custody-verifications/{LEGACY}") == "HISTORICAL_ANCHORED"
    assert status_of(report, f"primary:runs/{OLD_RUN}") == "HISTORICAL_ANCHORED"
    assert report["baseline"]["units"][f"primary:runs/{OLD_RUN}"]["anchored"] is True
    assert report["overall"] == "PASS"


def test_keyless_forgery_of_a_historical_run_is_not_anchored(history):
    custody, historical, legacy_sha = history
    run = custody.run(OLD_RUN)
    target = run / "raw/EX-04/source-export.json"
    target.write_bytes(target.read_bytes().replace(b"1", b"7"))
    new_sha = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = run / "metadata/EX-04/run-manifest.final.json"
    payload = json.loads(manifest.read_text())
    for reference in payload["artifact_references"]:
        if reference["relative_path"] == "raw/EX-04/source-export.json":
            reference["sha256"] = new_sha
    manifest.write_bytes(canonical_json_bytes(payload))
    # Falsificación completa y coherente, hecha sin la clave (que en modo histórico no se exige).
    _resign_run(run, key=b"forger-does-not-have-the-real-key-at-all")
    baseline = verifier.Baseline(None, {legacy_sha: ["docs/X.md"]}, DOCUMENTED.documented_units)
    report = verify(custody, historical=historical, baseline=baseline)
    assert status_of(report, f"primary:runs/{OLD_RUN}") == "HISTORICAL_UNANCHORED"
    assert report["overall"] == "PASS_WITH_UNANCHORED_HISTORICAL"


def test_declaring_current_epoch_evidence_historical_fails(custody):
    report = verify(custody, historical=frozenset({f"primary:runs/{RUN_B}"}))
    assert "historical-declaration-invalid" in codes(report, f"primary:runs/{RUN_B}")
    assert report["overall"] == "FAIL"


def test_tampered_historical_evidence_still_fails(history):
    custody, historical, _ = history
    target = custody.run(OLD_RUN) / "raw/EX-04/source-export.json"
    target.write_bytes(target.read_bytes() + b" ")
    report = verify(custody, historical=historical)
    assert report["overall"] == "FAIL"
    assert "record-sha256" in codes(report, f"primary:runs/{OLD_RUN}")


def test_unknown_historical_unit_is_a_configuration_error(custody):
    with pytest.raises(verifier.VerifierConfigurationError, match="historical-unit-unknown"):
        verify(custody, historical=frozenset({"primary:runs/s11-does-not-exist"}))


def test_cli_exit_code_for_unanchored_history_is_four(history, tmp_path):
    custody, historical, _ = history
    repo, commit = _documenting_repo(tmp_path)
    arguments = ["--baseline-git-commit", commit, "--repo-root", str(repo)]
    for unit in sorted(historical):
        arguments += ["--historical", unit]
    result = _cli(custody, *arguments)
    assert result.returncode == 4, result.stdout
    assert json.loads(result.stdout)["overall"] == "PASS_WITH_UNANCHORED_HISTORICAL"


# --- Línea base -----------------------------------------------------------------------


def test_git_baseline_extracts_hashes_and_unit_ids(custody, tmp_path):
    final = custody.final_manifest_sha(RUN_A)
    repo, commit = _documenting_repo(tmp_path, f"Manifest final: `{final}`", "b" * 64)
    baseline = verifier.git_document_baseline(repo, commit)
    assert set(baseline.anchors) == {final, "b" * 64}
    assert baseline.anchors[final] == ["docs/RECORD.md"]
    assert baseline.documented_units == {RUN_A, RUN_B, VERIFICATION}
    report = verify(custody, baseline=baseline)
    stats = report["baseline"]["units"]
    assert stats[f"primary:runs/{RUN_A}"]["anchored"] is True
    assert stats[f"primary:runs/{RUN_A}"]["anchor_documents"] == ["docs/RECORD.md"]
    assert stats[f"primary:runs/{RUN_B}"]["anchored"] is False
    assert report["baseline"]["commit"] == commit
    assert report["overall"] == "PASS"


def test_git_baseline_rejects_bad_commits(tmp_path):
    with pytest.raises(verifier.VerifierConfigurationError, match="baseline-commit-format"):
        verifier.git_document_baseline(tmp_path, "HEAD")
    with pytest.raises(verifier.VerifierConfigurationError, match="baseline-git-unavailable"):
        verifier.git_document_baseline(tmp_path, "0" * 40)


def test_git_replace_objects_cannot_subvert_the_baseline(tmp_path):
    commit, environment = _git_repo(tmp_path / "repo", "a" * 64 + "\n")
    repo = str(tmp_path / "repo")

    def git(*arguments, **kwargs):
        return subprocess.run(
            ["git", "-C", repo, *arguments],
            capture_output=True,
            text=True,
            check=True,
            env=environment,
            **kwargs,
        ).stdout.strip()

    original = git("rev-parse", f"{commit}:docs/RECORD.md")
    replacement = git("hash-object", "-w", "--stdin", input="c" * 64 + "\n")
    git("replace", original, replacement)
    assert git("show", f"{commit}:docs/RECORD.md") == "c" * 64
    baseline = verifier.git_document_baseline(tmp_path / "repo", commit)
    assert set(baseline.anchors) == {"a" * 64}


def test_git_subprocess_is_hardened_and_never_receives_the_key(monkeypatch, tmp_path):
    seen = []

    def fake_run(command, **kwargs):
        seen.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setenv(verifier.KEY_ENV, KEY_TEXT)
    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    verifier.git_document_baseline(tmp_path, "0" * 40)
    assert seen
    for command, env in seen:
        assert command[0] == verifier._GIT_EXECUTABLE and os.path.isabs(command[0])
        assert command[2] == os.path.realpath(tmp_path)
        assert verifier.KEY_ENV not in env
        assert KEY_TEXT not in " ".join(env.values())
        assert env["GIT_NO_REPLACE_OBJECTS"] == "1" and env["GIT_CONFIG_NOSYSTEM"] == "1"
        # La excepción de propiedad se limita a este repositorio, nunca `*`.
        assert env["GIT_CONFIG_COUNT"] == "2"
        assert env["GIT_CONFIG_KEY_0"] == "safe.directory"
        assert env["GIT_CONFIG_VALUE_0"] == os.path.realpath(tmp_path)
        assert (env["GIT_CONFIG_KEY_1"], env["GIT_CONFIG_VALUE_1"]) == ("core.fsmonitor", "false")


def test_git_baseline_reads_a_repository_owned_by_another_uid(monkeypatch, tmp_path):
    # En Docker el verificador corre como root sobre el repositorio del operador.
    repo, commit = _documenting_repo(tmp_path, "a" * 64)
    link = tmp_path / "link"
    link.symlink_to(repo)
    real = verifier._git_environment

    def other_owner(root):
        return {**real(root), "GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}

    unscoped = {
        name: value
        for name, value in other_owner(os.path.realpath(repo)).items()
        if not name.startswith(("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    }
    refused = subprocess.run(
        [verifier._GIT_EXECUTABLE, "-C", str(repo), "show", f"{commit}:docs/RECORD.md"],
        capture_output=True,
        env=unscoped,
    )
    if refused.returncode == 0:
        pytest.skip("git sin GIT_TEST_ASSUME_DIFFERENT_OWNER")
    assert b"dubious ownership" in refused.stderr
    monkeypatch.setattr(verifier, "_git_environment", other_owner)
    for root in (repo, link):
        assert set(verifier.git_document_baseline(root, commit).anchors) == {"a" * 64}


def test_comparison_with_a_previous_report_detects_any_change(custody, forbid_writes):
    forbid_writes()
    baseline = verify(custody)
    assert verify(custody, compare_report=baseline)["overall"] == "PASS"
    edited = json.loads(json.dumps(baseline))
    unit = next(u for u in edited["units"] if u["unit"] == f"primary:runs/{RUN_B}")
    unit["inventory"]["SHA256SUMS"] = "0" * 64
    removed = next(u for u in edited["units"] if u["unit"] == f"primary:runs/{RUN_A}")
    removed["inventory"].pop("logs/EX-04/http.jsonl")
    edited["units"].append({"unit": "primary:runs/s11-gone", "inventory": {}})
    report = verify(custody, compare_report=edited)
    assert report["overall"] == "FAIL"
    assert "baseline-mismatch" in codes(report, f"primary:runs/{RUN_B}")
    assert "baseline-new-file" in codes(report, f"primary:runs/{RUN_A}")
    assert "baseline-unit-missing" in root_codes(report)


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("not json", "compare-report-unreadable"),
        ('{"schema_version": "x"}', "compare-report-schema"),
        ("[]", "compare-report-schema"),
    ],
    ids=["unreadable", "schema", "not-object"],
)
def test_cli_rejects_invalid_comparison_reports(custody, tmp_path, content, error):
    path = tmp_path / "report.json"
    path.write_text(content)
    result = _cli(custody, "--compare-report", str(path))
    assert result.returncode == 3
    assert json.loads(result.stdout)["error"] == error


# --- Secretos y CLI --------------------------------------------------------------------


def test_cli_rejects_the_key_as_an_argument_without_echoing_it(custody):
    result = _cli(custody, "--historical", KEY_TEXT)
    assert result.returncode == 3
    assert json.loads(result.stdout)["error"] == "key-in-arguments"
    _assert_no_secret(result.stdout, result.stderr)


@pytest.mark.parametrize(
    "arguments",
    [["--key", OTHER_KEY_TEXT], ["--help"], ["-h"], ["--compare", "x"]],
    ids=["unknown", "help", "h", "abbreviation"],
)
def test_cli_usage_errors_never_echo_arguments_nor_exit_zero(custody, arguments):
    result = _cli(custody, *arguments)
    assert result.returncode == 3
    assert json.loads(result.stdout) == {"error": "usage", "overall": "ERROR"}
    assert result.stderr == ""
    _assert_no_secret(result.stdout, result.stderr)


@pytest.mark.parametrize(
    ("key", "error"),
    [
        (None, "custody-key-missing"),
        ("Zq#9", "custody-key-too-short"),
        ("bad-\udcff-key-with-32-bytes-or-more", "custody-key-encoding"),
    ],
    # Sin el valor en el id: el nombre del nodo acaba en la ruta que recibe la CLI.
    ids=["missing", "too-short", "not-utf8"],
)
def test_cli_requires_a_proper_key_from_the_environment(custody, key, error):
    result = _cli(custody, key=key)
    assert result.returncode == 3
    assert json.loads(result.stdout)["error"] == error
    assert result.stderr == ""


def test_cli_failure_never_prints_the_key(custody):
    (custody.run(RUN_B) / "metadata/artifacts.jsonl.hmac").write_text("0" * 64 + "\n")
    result = _cli(custody)
    assert result.returncode == 1
    assert json.loads(result.stdout)["overall"] == "FAIL"
    _assert_no_secret(result.stdout, result.stderr)


def _run_in_process(custody, output):
    return verifier.run(
        [
            "--primary-root",
            str(custody.primary),
            "--backup-root",
            str(custody.backup),
            "--restore-root",
            str(custody.restore),
        ],
        {verifier.KEY_ENV: KEY_TEXT},
        output,
    )


def test_internal_errors_print_only_the_exception_type(monkeypatch, custody):
    def explode(**kwargs):
        raise ValueError(f"mensaje con la clave {KEY_TEXT}")

    monkeypatch.setattr(verifier, "verify_custody", explode)
    output = io.StringIO()
    assert _run_in_process(custody, output) == 3
    assert json.loads(output.getvalue()) == {
        "error": "internal",
        "overall": "ERROR",
        "type": "ValueError",
    }
    _assert_no_secret(output.getvalue())


def test_a_report_that_would_contain_the_key_is_replaced_and_exits_three(monkeypatch, custody):
    monkeypatch.setattr(
        verifier, "verify_custody", lambda **kwargs: {"overall": "PASS", "x": KEY_TEXT}
    )
    output = io.StringIO()
    assert _run_in_process(custody, output) == 3
    assert json.loads(output.getvalue()) == {
        "error": "report-would-contain-secret",
        "overall": "ERROR",
    }


def test_emit_detects_json_escaped_secrets():
    secret = 'clave"con\\caracteres-escapados-32-bytes'
    output = io.StringIO()
    assert verifier._emit(output, {"leak": secret}, (secret,)) is False
    assert secret not in output.getvalue()


def test_report_contains_only_the_fingerprint(custody):
    text = json.dumps(verify(custody))
    _assert_no_secret(text)
    assert hashlib.sha256(KEY).hexdigest()[:16] in text


def test_fixtures_never_touch_the_real_custody(custody):
    repository_var = (Path(__file__).resolve().parents[4] / "var").resolve()
    assert custody.base.resolve().is_relative_to((Path.cwd() / ".s11-test-artifacts").resolve())
    assert not custody.base.resolve().is_relative_to(repository_var)


def test_evidence_store_rejects_the_fixture_under_tmp():
    with pytest.raises(EvidencePolicyError, match="/tmp"):
        _store(Path("/tmp/s11-verifier-test"))
