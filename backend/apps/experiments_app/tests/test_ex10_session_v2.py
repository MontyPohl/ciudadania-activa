"""B2-only synthetic codecs and record I/O. No operational launcher action or run."""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
import stat
import sys
import tempfile
import types
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "b2_contract_fixture"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(APP)]
sys.modules[PACKAGE_NAME] = package
PROFILE_SPEC = importlib.util.spec_from_file_location(
    PACKAGE_NAME + ".ex10_runtime_profile", APP / "ex10_runtime_profile.py"
)
assert PROFILE_SPEC is not None and PROFILE_SPEC.loader is not None
rp = importlib.util.module_from_spec(PROFILE_SPEC)
sys.modules[PROFILE_SPEC.name] = rp
PROFILE_SPEC.loader.exec_module(rp)
MODULE_PATH = Path(os.environ.get("S11_B2_MODULE_PATH", str(APP / "ex10_session_v2.py")))
SPEC = importlib.util.spec_from_file_location(PACKAGE_NAME + ".ex10_session_v2", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
session = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = session
SPEC.loader.exec_module(session)
ORACLE_PATH = APP.parents[1] / "scripts" / "s11_ex10_official_launcher.py"
ORACLE_SPEC = importlib.util.spec_from_file_location("b2_frozen_v1_oracle", ORACLE_PATH)
assert ORACLE_SPEC is not None and ORACLE_SPEC.loader is not None
oracle = importlib.util.module_from_spec(ORACLE_SPEC)
sys.modules[ORACLE_SPEC.name] = oracle
ORACLE_SPEC.loader.exec_module(oracle)
RUN_ID = "s11-11111111-2222-3333-4444-555555555555"
EXPECTED_HEADER_KEYS = (
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
    "runtime_profile_schema",
    "runtime_profile_id",
    "runtime_profile_sha256",
    "runtime_profile_approval_reference",
    "host_roots",
    "fixed_targets",
    "compose_files",
    "override_path",
    "override_sha256",
    "source_binding",
    "masks",
    "deployment_verification_reference",
    "historical_lifecycle_authority_reference",
    "external_source_anchor_reference",
    "expected_services",
    "beat_policy",
    "path_identity_binding",
)


def encoded(value):
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode()


def mkdir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def write(path, content=b"{}\n"):
    path.write_bytes(content)
    path.chmod(0o600)
    return path


@pytest.fixture
def fixture():
    with tempfile.TemporaryDirectory(
        prefix="b2-synthetic-", dir=os.environ["S11_B2_TEST_BASE"]
    ) as temporary:
        home = Path(temporary)
        repo = mkdir(home / "source")
        infra = mkdir(repo / "infra")
        compose = [write(infra / name) for name in ("docker-compose.yml", "docker-compose.s11.yml")]
        for path in compose:
            path.chmod(0o644)
        roots = {role: mkdir(home / "roots" / role) for role in rp.ROLES}
        mask = mkdir(home / "mask")
        override = write(home / "override.yml")
        uid, gid = os.getuid(), os.getgid()
        refs = {}
        for name in rp.REFERENCE_FIELDS:
            path = write(home / (name + ".json"))
            refs[name] = {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "schema_version": "s11-ex10-synthetic-reference-v1",
            }
        profile = {
            "schema_version": rp.SCHEMA_VERSION,
            "profile_id": "synthetic-profile",
            "approved_at": "2026-10-04T00:00:00.000000Z",
            "source_revision": "a" * 40,
            "project": "synthetic-project",
            "compose_files": [
                {
                    "kind": "tracked",
                    "path": str(path),
                    "git_path": "infra/" + path.name,
                    "git_blob": "b" * 40,
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
                    "path": str(path),
                    "owner_uid": uid,
                    "owner_gid": gid,
                    "directory_mode": "0700",
                    "artifact_mode": "0600",
                }
                for role, path in roots.items()
            },
            "fixed_targets": dict(rp.FIXED_TARGETS),
            "expected_owner": {"uid": uid, "gid": gid},
            "approved_modes": {"directory": "0700", "artifact": "0600"},
            "masks": [
                {
                    "relative_path": "var",
                    "kind": "directory",
                    "source_path": str(mask),
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
        approval_path = write(home / "profile-approval.json")
        approval = {
            "path": str(approval_path),
            "sha256": hashlib.sha256(approval_path.read_bytes()).hexdigest(),
            "schema_version": "s11-ex10-synthetic-profile-approval-v1",
        }
        paths = [repo, *compose, *roots.values(), mask, Path("/dev/null"), override, approval_path]
        paths += [Path(refs[name]["path"]) for name in rp.REFERENCE_FIELDS[1:]]
        bindings = []
        for path in sorted(paths, key=str):
            info = path.stat()
            bindings.append(
                {
                    "path": str(path),
                    "device": info.st_dev,
                    "inode": info.st_ino,
                    "uid": info.st_uid,
                    "gid": info.st_gid,
                    "mode": f"{stat.S_IMODE(info.st_mode):04o}",
                }
            )
        header = session.new_session_v2_header(
            profile,
            session_id="synthetic-session",
            created_at="2026-10-04T00:01:00.000000Z",
            branch="feat/s11-ex10-runtime-profile-v2",
            launcher_blobs={name: "c" * 40 for name in session.LAUNCHER_PATHS},
            runtime_profile_approval_reference=approval,
            path_identity_binding=bindings,
        )
        parent = mkdir(home / "records")
        context = {
            "parent": str(parent),
            "owner_uid": uid,
            "owner_gid": gid,
            "repo_root": str(repo),
            "historical_roots": [],
            "prior_session_roots": [],
        }
        yield profile, header, context, parent, home


def fresh(fixture):
    profile, header, *_ = fixture
    return session.new_official_session_v2(header, profile=profile)


def payload(event="observation", *, run_id=None):
    return {
        "event": event,
        "output_sha256": {"synthetic.json": "e" * 64},
        "state": {
            "known_phase": "PREPARED" if run_id else None,
            "uncertain": False,
            "blocked": False,
            "next_allowed_actions": ["inspect"],
        },
        "official_run_id": run_id,
        "preflight_report_sha256": "d" * 64 if run_id else None,
        "operational_envelope_sha256": None,
        "in_flight": [],
    }


def history(fixture, count=3):
    result = fresh(fixture)
    for index in range(count):
        result = session.append_history_entry_v2(result, payload("observation-" + str(index)))
    return result


def load(value, original=None, profile=None):
    return session.load_session_v2(
        encoded(value),
        profile=profile,
        expected_tip=original._expected_tip if original else value.get("header_sha256"),
        expected_count=original._expected_count if original else 0,
    )


def reject_load(value, original=None, profile=None):
    with pytest.raises(session.OfficialSessionError):
        load(value, original, profile)


def v1_manifest(repo):
    return oracle.new_session_manifest(
        session_id="synthetic-v1",
        created_at="2026-10-04T00:00:00.000000Z",
        git_sha="a" * 40,
        branch="main",
        repo_root=repo,
        integrity={oracle.LAUNCHER_PATH: "c" * 40, oracle.BASE_LAUNCHER_PATH: "d" * 40},
    )


def test_valid_v2_pure_and_profile_hash_from_b1(fixture, monkeypatch):
    profile, header, *_ = fixture
    called = []
    original = rp.runtime_profile_sha256

    def observed(value):
        called.append(True)
        return original(value)

    monkeypatch.setattr(rp, "runtime_profile_sha256", observed)
    result = session.new_official_session_v2(header, profile=profile)
    assert called
    assert result.header["runtime_profile_sha256"] == original(profile)
    assert result.to_dict()["official_run_id"] is None
    assert result.to_dict()["state"]["next_allowed_actions"] == ["inspect"]
    assert isinstance(
        session.verify_official_session_v2(result, profile=profile), session.OfficialSessionV2
    )


@pytest.mark.parametrize("name", EXPECTED_HEADER_KEYS)
def test_property_every_security_header_field_changes_digest(fixture, name):
    _, header, *_ = fixture
    assert tuple(session.HEADER_KEYS) == EXPECTED_HEADER_KEYS
    original = session.session_header_sha256(header)
    altered = copy.deepcopy(header)
    value = altered[name]
    if isinstance(value, str):
        altered[name] = value + "-tampered"
    elif isinstance(value, list):
        altered[name] = value + ["synthetic-tamper"]
    else:
        altered[name] = {**value, "synthetic-tamper": True}
    assert session.session_header_sha256(altered) != original


@pytest.mark.parametrize("name", EXPECTED_HEADER_KEYS)
def test_missing_header_field_rejected(fixture, name):
    value = fresh(fixture).to_dict()
    del value[name]
    reject_load(value)


@pytest.mark.parametrize("where", ["top", "header", "state", "history", "reference"])
def test_unknown_closed_fields(fixture, where):
    original = history(fixture, 1) if where == "history" else fresh(fixture)
    value = original.to_dict()
    if where in {"top", "header"}:
        value["arbitrary"] = "synthetic"
    elif where == "state":
        value["state"]["arbitrary"] = True
    elif where == "history":
        value["history"][0]["arbitrary"] = True
    else:
        value["runtime_profile_approval_reference"]["arbitrary"] = True
    reject_load(value, original)


@pytest.mark.parametrize(
    "name",
    [
        "schema_version",
        "project",
        "host_roots",
        "runtime_profile_sha256",
        "compose_files",
        "override_sha256",
        "source_binding",
        "masks",
        "historical_lifecycle_authority_reference",
        "external_source_anchor_reference",
        "header_sha256",
    ],
)
def test_security_binding_tamper_fails(fixture, name):
    value = fresh(fixture).to_dict()
    if name == "host_roots":
        value[name]["evidence"]["path"] += "-tamper"
    elif name == "compose_files":
        value[name][0]["sha256"] = "f" * 64
    elif name == "source_binding":
        value[name]["read_only"] = False
    elif name == "masks":
        value[name][0]["source_path"] += "-tamper"
    elif isinstance(value[name], dict):
        value[name]["sha256"] = "f" * 64
    elif name.endswith("sha256"):
        value[name] = "f" * 64
    else:
        value[name] += "-tamper"
    reject_load(value)


@pytest.mark.parametrize("binding", ["sha", "id", "schema", "copied-field"])
def test_profile_mismatch_fails_even_with_rehashed_header(fixture, binding):
    profile, _, *_ = fixture
    value = fresh(fixture).to_dict()
    key = {
        "sha": "runtime_profile_sha256",
        "id": "runtime_profile_id",
        "schema": "runtime_profile_schema",
        "copied-field": "project",
    }[binding]
    value[key] = (
        "f" * 64
        if binding == "sha"
        else ("s11-ex10-runtime-profile-v9" if binding == "schema" else "other-identity")
    )
    header = {name: value[name] for name in EXPECTED_HEADER_KEYS}
    value["header_sha256"] = session.session_header_sha256(header)
    reject_load(value, profile=profile)


def test_history_seed_and_entry_hash_match_contract(fixture):
    original = fresh(fixture)
    first = session.append_history_entry_v2(original, payload())
    entry = first.to_dict()["history"][0]
    seed = hashlib.sha256(encoded(original.header)).hexdigest()
    assert original._expected_tip == seed == session.initial_history_seed(original.header)
    raw = encoded({key: value for key, value in entry.items() if key != "chain"})
    assert session.history_entry_payload(entry) == raw
    assert entry["chain"] == hashlib.sha256(seed.encode("ascii") + raw).hexdigest()


@pytest.mark.parametrize("count", [1, 2, 5])
def test_history_multiple_entries(fixture, count):
    original = history(fixture, count)
    result = load(original.to_dict(), original)
    assert result.to_dict() == original.to_dict()
    assert (
        session.verify_history_chain(
            result.to_dict()["header_sha256"],
            result.to_dict()["history"],
            expected_tip=result._expected_tip,
            expected_count=count,
        )
        == result._expected_tip
    )


@pytest.mark.parametrize(
    "case", ["entry", "middle-removed", "order", "previous", "chain", "truncated", "all-removed"]
)
def test_broken_or_truncated_history_rejected(fixture, case):
    original = history(fixture)
    value = original.to_dict()
    if case == "entry":
        value["history"][0]["payload"]["event"] = "modified"
    elif case == "middle-removed":
        value["history"].pop(1)
    elif case == "order":
        value["history"].reverse()
    elif case == "previous":
        value["history"][1]["previous_digest"] = "f" * 64
    elif case == "chain":
        value["history"][0]["chain"] = "f" * 64
    elif case == "truncated":
        value["history"].pop()
        value.update({key: value["history"][-1]["payload"][key] for key in session.SNAPSHOT_KEYS})
    else:
        value["history"] = []
        value.update(session._initial_snapshot())
    reject_load(value, original)


def test_nonempty_history_requires_explicit_checkpoint(fixture):
    original = history(fixture, 1)
    with pytest.raises(session.OfficialSessionError) as caught:
        session.load_session_v2(original._bytes)
    assert caught.value.code == "history-checkpoint-required"


@pytest.mark.parametrize(
    "field,value",
    [
        ("known_phase", "ARBITRARY"),
        ("known_phase", []),
        ("uncertain", 1),
        ("blocked", "false"),
        ("next_allowed_actions", ["shell-command"]),
        ("next_allowed_actions", ["status", "inspect"]),
        ("next_allowed_actions", ["inspect", "inspect"]),
    ],
)
def test_state_codec_rejected(fixture, field, value):
    result = fresh(fixture)
    next_payload = payload()
    next_payload["state"][field] = value
    with pytest.raises(session.OfficialSessionError):
        session.append_history_entry_v2(result, next_payload)


@pytest.mark.parametrize(
    "key",
    [
        "state",
        "official_run_id",
        "preflight_report_sha256",
        "operational_envelope_sha256",
        "in_flight",
    ],
)
def test_dynamic_footer_bound_to_history(fixture, key):
    original = history(fixture, 1)
    value = original.to_dict()
    if key == "state":
        value[key]["blocked"] = True
    elif key == "official_run_id":
        value[key] = RUN_ID
    elif key == "in_flight":
        value[key] = [
            {
                "action": "cleanup-stack",
                "container_name": "synthetic",
                "output_dir": "actions/synthetic",
                "started_at": "2026-10-04T00:00:00.000000Z",
                "after_seq": 0,
            }
        ]
    else:
        value[key] = "f" * 64
    reject_load(value, original)


@pytest.mark.parametrize(
    "run_id",
    [
        "",
        "s11-invalid",
        "S11-11111111-2222-3333-4444-555555555555",
        "s11-11111111-2222-3333-4444-555555555555\n",
        1,
        [],
        {},
    ],
)
def test_malformed_run_id_rejected(fixture, run_id):
    with pytest.raises(session.OfficialSessionError) as caught:
        session.append_history_entry_v2(fresh(fixture), payload(run_id=run_id))
    assert caught.value.code == "run-id"


def test_null_and_formatted_run_id_are_data_only(fixture):
    initial = fresh(fixture)
    assert initial.to_dict()["official_run_id"] is None
    updated = session.append_history_entry_v2(initial, payload(run_id=RUN_ID))
    assert updated.to_dict()["official_run_id"] == RUN_ID


def test_create_exclusive_canonical_private_record(fixture):
    profile, header, context, parent, _ = fixture
    path = parent / "synthetic-record.json"
    created = session.create_official_session_v2(path, header, profile=profile, context=context)
    assert path.read_bytes() == created._bytes == encoded(created.to_dict())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = session.load_official_session(
        path,
        context=context,
        profile=profile,
        expected_tip=fresh(fixture)._expected_tip,
        expected_count=0,
    )
    assert loaded.to_dict() == created.to_dict()


def test_create_never_overwrites_existing_record(fixture):
    profile, header, context, parent, _ = fixture
    path = write(parent / "existing.json", b"synthetic-existing\n")
    previous = path.read_bytes()
    with pytest.raises(session.OfficialSessionError) as caught:
        session.create_official_session_v2(path, header, profile=profile, context=context)
    assert caught.value.code == "exists"
    assert path.read_bytes() == previous


@pytest.mark.parametrize("operation", ["create", "load", "update"])
def test_session_symlink_rejected_without_touching_target(fixture, operation):
    profile, header, context, parent, _ = fixture
    target = parent / "target.json"
    initial = session.create_official_session_v2(target, header, profile=profile, context=context)
    before = target.stat()
    path = parent / "symlink.json"
    path.symlink_to(target)
    with pytest.raises(session.OfficialSessionError) as caught:
        if operation == "create":
            session.create_official_session_v2(path, header, profile=profile, context=context)
        elif operation == "load":
            session.load_official_session(
                path,
                context=context,
                profile=profile,
                expected_tip=fresh(fixture)._expected_tip,
                expected_count=0,
            )
        else:
            session.update_official_session_v2(
                path,
                initial,
                history(fixture, 1),
                context=context,
                caller_serialized=True,
            )
    assert caught.value.code == "symlink"
    assert target.read_bytes() == initial._bytes
    assert target.stat().st_ctime_ns == before.st_ctime_ns


@pytest.mark.parametrize("wrong", ["schema", "profile"])
def test_invalid_create_makes_no_file(fixture, wrong):
    profile, header, context, parent, _ = fixture
    if wrong == "schema":
        header["schema_version"] = "unknown"
    else:
        header["runtime_profile_sha256"] = "f" * 64
    path = parent / "invalid.json"
    with pytest.raises(session.OfficialSessionError):
        session.create_official_session_v2(path, header, profile=profile, context=context)
    assert not path.exists()


def test_atomic_update_preserves_header_and_creates_private_inode(fixture):
    profile, header, context, parent, _ = fixture
    path = parent / "atomic.json"
    before = session.create_official_session_v2(path, header, profile=profile, context=context)
    inode = path.stat().st_ino
    after = session.append_history_entry_v2(before, payload())
    result = session.update_official_session_v2(
        path,
        before,
        after,
        context=context,
        profile=profile,
        caller_serialized=True,
    )
    assert path.read_bytes() == result._bytes == after._bytes
    assert path.stat().st_ino != inode
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(parent.glob("*.tmp"))
    assert (
        session.load_official_session(
            path,
            context=context,
            profile=profile,
            expected_tip=after._expected_tip,
            expected_count=after._expected_count,
        ).to_dict()
        == after.to_dict()
    )


def test_header_update_is_forbidden_even_when_rehashed(fixture):
    profile, header, context, parent, _ = fixture
    path = parent / "immutable.json"
    before = session.create_official_session_v2(path, header, profile=profile, context=context)
    altered = copy.deepcopy(header)
    altered["session_id"] = "different-session"
    after = session.new_official_session_v2(altered, profile=profile)
    with pytest.raises(session.OfficialSessionError) as caught:
        session.update_official_session_v2(
            path,
            before,
            after,
            context=context,
            caller_serialized=True,
        )
    assert caught.value.code == "header-update"
    assert path.read_bytes() == before._bytes


@pytest.mark.parametrize("phase", ["write", "file-fsync", "replace"])
def test_failed_atomic_write_preserves_prior_record(fixture, monkeypatch, phase):
    profile, header, context, parent, _ = fixture
    path = parent / "failed-update.json"
    before = session.create_official_session_v2(path, header, profile=profile, context=context)
    after = session.append_history_entry_v2(before, payload())

    def fail(*args, **kwargs):
        raise OSError("synthetic-private-canary")

    if phase == "write":
        monkeypatch.setattr(session.os, "write", fail)
    elif phase == "file-fsync":
        monkeypatch.setattr(session.os, "fsync", fail)
    else:
        monkeypatch.setattr(session.os, "replace", fail)
    with pytest.raises(session.OfficialSessionError) as caught:
        session.update_official_session_v2(
            path,
            before,
            after,
            context=context,
            caller_serialized=True,
        )
    assert caught.value.code == "io"
    assert path.read_bytes() == before._bytes


def test_directory_fsync_failure_reports_uncertain_after_replace(fixture, monkeypatch):
    profile, header, context, parent, _ = fixture
    path = parent / "uncertain.json"
    before = session.create_official_session_v2(path, header, profile=profile, context=context)
    after = session.append_history_entry_v2(before, payload())
    real = os.fsync

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("synthetic-durability-failure")
        real(fd)

    monkeypatch.setattr(session.os, "fsync", fail_directory)
    with pytest.raises(session.OfficialSessionError) as caught:
        session.update_official_session_v2(
            path,
            before,
            after,
            context=context,
            caller_serialized=True,
        )
    assert caught.value.code == "write-uncertain"
    assert path.read_bytes() == after._bytes


@pytest.mark.parametrize(
    "fault", ["mode", "hardlink", "parent-symlink", "parent-mode", "owner", "history"]
)
def test_record_io_context_and_file_guards(fixture, fault):
    profile, header, context, parent, home = fixture
    path = parent / "guards.json"
    initial = session.create_official_session_v2(path, header, profile=profile, context=context)
    if fault == "mode":
        path.chmod(0o644)
    elif fault == "hardlink":
        os.link(path, parent / "hardlink")
    elif fault == "parent-symlink":
        link = home / "linked-parent"
        link.symlink_to(parent, target_is_directory=True)
        context["parent"] = str(link)
        path = link / path.name
    elif fault == "parent-mode":
        parent.chmod(0o755)
    elif fault == "owner":
        context["owner_uid"] += 1
    else:
        context["historical_roots"] = [str(parent)]
    with pytest.raises(session.OfficialSessionError):
        session.load_official_session(
            path,
            context=context,
            profile=profile,
            expected_tip=fresh(fixture)._expected_tip,
            expected_count=0,
        )
    assert initial.to_dict()["official_run_id"] is None


@pytest.mark.parametrize(
    "form", ["indent", "no-lf", "duplicate", "nan", "utf8", "too-large", "array"]
)
def test_property_malformed_json_is_not_repaired(fixture, form):
    original = fresh(fixture)
    raw = {
        "indent": json.dumps(original.to_dict(), indent=2).encode(),
        "no-lf": original._bytes[:-1],
        "duplicate": original._bytes.replace(b'"project":', b'"project":"ignored","project":', 1),
        "nan": original._bytes.replace(b'"official_run_id":null', b'"official_run_id":NaN'),
        "utf8": b"\xff",
        "too-large": b"x" * (session.MAX_SESSION_BYTES + 1),
        "array": b"[]\n",
    }[form]
    with pytest.raises(session.OfficialSessionError):
        session.load_session_v2(raw)


@pytest.mark.parametrize("seed", range(5))
def test_property_canonicalization_idempotent_and_digest_stable(fixture, seed):
    _, header, *_ = fixture
    names = list(header)
    rotated = names[seed:] + names[:seed]
    changed = {name: header[name] for name in rotated}
    raw = session.canonical_session_header_bytes(changed)
    assert raw == encoded(header)
    assert session.canonical_session_header_bytes(json.loads(raw)) == raw
    assert session.session_header_sha256(changed) == session.session_header_sha256(header)


def test_property_history_verification_deterministic(fixture):
    original = history(fixture)
    args = (original.to_dict()["header_sha256"], original.to_dict()["history"])
    options = {"expected_tip": original._expected_tip, "expected_count": original._expected_count}
    assert session.verify_history_chain(*args, **options) == session.verify_history_chain(
        *args, **options
    )


def test_property_failed_file_load_has_no_writes(fixture):
    profile, _, context, parent, _ = fixture
    path = write(parent / "malformed.json", b"synthetic-malformed\n")
    before = path.stat()
    contents = path.read_bytes()
    with pytest.raises(session.OfficialSessionError):
        session.load_official_session(
            path,
            context=context,
            profile=profile,
            expected_tip=fresh(fixture)._expected_tip,
            expected_count=0,
        )
    after = path.stat()
    assert (before.st_ino, before.st_mtime_ns, before.st_ctime_ns) == (
        after.st_ino,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    assert path.read_bytes() == contents


def test_v1_byte_identity_and_no_auto_upgrade(fixture):
    _, _, context, parent, _ = fixture
    value = v1_manifest(Path(context["repo_root"]))
    raw = encoded(value)
    result = session.load_session_v1(raw, repo_root=context["repo_root"])
    assert isinstance(result, session.OfficialSessionV1)
    assert result.original_bytes == raw and result.to_dict() == value
    assert result.to_dict()["schema_version"] == session.SESSION_V1_SCHEMA
    assert "runtime_profile_sha256" not in result.to_dict()
    assert isinstance(
        session.dispatch_session_by_schema(raw, repo_root=context["repo_root"]),
        session.OfficialSessionV1,
    )
    write(parent / oracle.SESSION_FILE, raw)
    assert oracle.load_session(parent, Path(context["repo_root"])) == result.to_dict()


@pytest.mark.parametrize("change", ["unknown", "missing-branch", "float", "bool-after-seq"])
def test_v1_original_open_schema_and_quirks_are_preserved(fixture, change):
    _, _, context, parent, _ = fixture
    value = v1_manifest(Path(context["repo_root"]))
    if change == "unknown":
        value["historical-extension"] = {"opaque": True}
    elif change == "missing-branch":
        del value["branch"]
        value["header_sha256"] = oracle._header_digest(value)
    elif change == "float":
        value["historical-number"] = 1.5
    else:
        value["in_flight"] = [
            {
                "action": "cleanup-stack",
                "container_name": "synthetic",
                "output_dir": "actions/synthetic",
                "started_at": "synthetic",
                "after_seq": False,
            }
        ]
        value["state"] = oracle.derive_state(value)
    raw = encoded(value)
    write(parent / oracle.SESSION_FILE, raw)
    assert oracle.load_session(parent, Path(context["repo_root"])) == value
    result = session.load_session_v1(raw, repo_root=context["repo_root"])
    assert result.original_bytes == raw and result.to_dict() == value


@pytest.mark.parametrize(
    "scenario",
    ["refused", "uncertain", "blocking", "started", "status", "draining", "reconciled", "terminal"],
)
def test_v1_history_and_state_match_original_loader(fixture, scenario):
    _, _, context, parent, _ = fixture
    value = v1_manifest(Path(context["repo_root"]))
    phase = {
        "started": "PREPARED",
        "status": "EMITTING",
        "draining": "DRAINING",
        "reconciled": "RECONCILING",
        "terminal": "FINALIZED_PASS",
    }.get(scenario)
    entries = []
    if phase:
        entries.append(
            {
                "action": "start-official",
                "classification": "COHERENT",
                "phase": "PREPARED",
                "run_id": RUN_ID,
                "output_dir": None,
                "output_sha256": {"preflight.json": "d" * 64},
            }
        )
        value["official_run_id"] = RUN_ID
        value["preflight_report_sha256"] = "d" * 64
        entries.append(
            {
                "action": "execute-official" if scenario == "draining" else "status",
                "classification": "COHERENT",
                "phase": phase,
                "output_dir": None,
            }
        )
        if scenario == "reconciled":
            entries.append(
                {
                    "action": "reconcile",
                    "classification": "COHERENT",
                    "phase": None,
                    "output_dir": None,
                }
            )
    else:
        entries.append(
            {
                "action": "start-official",
                "classification": "APPARATUS_ABORT"
                if scenario == "uncertain"
                else "VALIDATION_FAILED",
                "phase": None,
                "session_blocking": scenario == "blocking",
                "output_dir": None,
            }
        )
    previous = value["header_sha256"]
    for entry in entries:
        entry["chain"] = oracle._chain(previous, entry)
        previous = entry["chain"]
    value["history"] = entries
    value["state"] = oracle.derive_state(value)
    raw = encoded(value)
    write(parent / oracle.SESSION_FILE, raw)
    expected = oracle.load_session(parent, Path(context["repo_root"]))
    assert session.load_session_v1(raw, repo_root=context["repo_root"]).to_dict() == expected


def test_v1_output_verification_is_explicit_and_read_only(fixture):
    _, _, context, parent, _ = fixture
    value = v1_manifest(Path(context["repo_root"]))
    output = mkdir(parent / "actions" / "synthetic")
    write(output / "stdout.json", b"{}\n")
    hashes = oracle._output_hashes(output)
    entry = {
        "action": "inspect",
        "classification": "COHERENT",
        "phase": None,
        "output_dir": "actions/synthetic",
        "output_sha256": hashes,
    }
    entry["chain"] = oracle._chain(value["header_sha256"], entry)
    value["history"] = [entry]
    value["state"] = oracle.derive_state(value)
    raw = encoded(value)
    write(parent / oracle.SESSION_FILE, raw)
    assert oracle.load_session(parent, Path(context["repo_root"])) == value
    with pytest.raises(session.OfficialSessionError) as caught:
        session.load_session_v1(raw, repo_root=context["repo_root"])
    assert caught.value.code == "v1-output-verifier-required"
    result = session.load_session_v1(
        raw,
        repo_root=context["repo_root"],
        session_root=str(parent),
        output_verifier=observe_v1_output,
    )
    assert result.original_bytes == raw
    with pytest.raises(session.OfficialSessionError):
        session.load_session_v1(
            raw,
            repo_root=context["repo_root"],
            session_root=str(parent),
            output_verifier=lambda path: {"is_directory": True, "is_symlink": False, "hashes": {}},
        )


@pytest.mark.parametrize("operation", ["append", "update", "verify"])
def test_v1_cannot_reach_v2_mutator(fixture, operation):
    _, _, context, parent, _ = fixture
    raw = encoded(v1_manifest(Path(context["repo_root"])))
    original = session.load_session_v1(raw, repo_root=context["repo_root"])
    path = write(parent / "historical.json", raw)
    before = path.stat()
    with pytest.raises(session.OfficialSessionError) as caught:
        if operation == "append":
            session.append_history_entry_v2(original, payload())
        elif operation == "verify":
            session.verify_official_session_v2(original)
        else:
            session.update_official_session_v2(
                path,
                original,
                fresh(fixture),
                context=context,
                caller_serialized=True,
            )
    assert caught.value.code == "v1-read-only"
    assert path.read_bytes() == raw
    assert path.stat().st_ctime_ns == before.st_ctime_ns


@pytest.mark.parametrize("kind", ["json", "project", "header", "state", "schema", "chain"])
def test_v1_malformed_fails_sanitized(fixture, kind):
    _, _, context, *_ = fixture
    value = v1_manifest(Path(context["repo_root"]))
    if kind == "json":
        raw = b"malformed synthetic-private-canary"
    else:
        key = {
            "project": "project",
            "header": "header_sha256",
            "state": "state",
            "schema": "schema_version",
            "chain": "history",
        }[kind]
        value[key] = [{"chain": "invalid"}] if kind == "chain" else "synthetic-private-canary"
        raw = encoded(value)
    with pytest.raises(session.OfficialSessionError) as caught:
        session.load_session_v1(raw, repo_root=context["repo_root"])
    assert "synthetic" not in str(caught.value)
    assert "canary" not in str(caught.value)


def test_property_v1_file_load_never_writes(fixture):
    _, _, context, parent, _ = fixture
    raw = encoded(v1_manifest(Path(context["repo_root"])))
    path = write(parent / "historical.json", raw)
    before = path.stat()
    result = session.load_official_session(
        path,
        context=context,
        expected_tip=fresh(fixture)._expected_tip,
        expected_count=0,
    )
    after = path.stat()
    assert isinstance(result, session.OfficialSessionV1)
    assert result.original_bytes == raw == path.read_bytes()
    assert (before.st_ino, before.st_mtime_ns, before.st_ctime_ns) == (
        after.st_ino,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )


def test_dispatch_unknown_schema_and_no_fallback(fixture):
    value = fresh(fixture).to_dict()
    value["schema_version"] = "unknown-session-schema"
    with pytest.raises(session.OfficialSessionError):
        session.dispatch_session_by_schema(encoded(value))
    del value["schema_version"]
    with pytest.raises(session.OfficialSessionError):
        session.dispatch_session_by_schema(encoded(value))


def test_profile_and_session_defensive_copies(fixture):
    original = fresh(fixture)
    value = original.to_dict()
    value["project"] = "changed"
    assert original.to_dict()["project"] != "changed"
    with pytest.raises(AttributeError):
        original._bytes = b"{}"
    assert "synthetic-profile" not in repr(original)


def test_module_has_only_stdlib_and_b1_imports():
    parsed = ast.parse(MODULE_PATH.read_text())
    imported = {node.module for node in ast.walk(parsed) if isinstance(node, ast.ImportFrom)}
    assert imported <= {
        "__future__",
        "collections.abc",
        "dataclasses",
        "datetime",
        "pathlib",
        "typing",
        None,
    }


def observe_v1_output(path):
    source = Path(path)
    return {
        "is_directory": source.is_dir(),
        "is_symlink": source.is_symlink(),
        "hashes": oracle._output_hashes(source) if source.is_dir() else {},
    }


def test_genesis_and_fully_truncated_record_require_caller_checkpoint(fixture):
    original = history(fixture, 1)
    value = original.to_dict()
    value["history"] = []
    value.update(session._initial_snapshot())
    for raw in (fresh(fixture)._bytes, encoded(value)):
        with pytest.raises(session.OfficialSessionError) as caught:
            session.load_session_v2(raw)
        assert caught.value.code == "history-checkpoint-required"


def test_v1_symlink_output_matches_original_rejection(fixture):
    _, _, context, parent, home = fixture
    value = v1_manifest(Path(context["repo_root"]))
    external = mkdir(home / "synthetic-external-output")
    write(external / "stdout.json")
    mkdir(parent / "actions")
    (parent / "actions/synthetic").symlink_to(external, target_is_directory=True)
    entry = {
        "action": "inspect",
        "classification": "COHERENT",
        "phase": None,
        "output_dir": "actions/synthetic",
        "output_sha256": oracle._output_hashes(external),
    }
    entry["chain"] = oracle._chain(value["header_sha256"], entry)
    value["history"] = [entry]
    value["state"] = oracle.derive_state(value)
    raw = encoded(value)
    write(parent / oracle.SESSION_FILE, raw)
    with pytest.raises(oracle.SessionTampered):
        oracle.load_session(parent, Path(context["repo_root"]))
    with pytest.raises(session.OfficialSessionError):
        session.load_session_v1(
            raw,
            repo_root=context["repo_root"],
            session_root=str(parent),
            output_verifier=observe_v1_output,
        )


def test_permission_change_during_file_open_is_rejected(fixture, monkeypatch):
    profile, header, context, parent, _ = fixture
    path = parent / "changed-mode.json"
    initial = session.create_official_session_v2(path, header, profile=profile, context=context)
    real_open = os.open

    def altered_open(name, flags, *args, **kwargs):
        if name == path.name and not flags & os.O_CREAT:
            path.chmod(0o644)
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(session.os, "open", altered_open)
    with pytest.raises(session.OfficialSessionError) as caught:
        session.load_official_session(
            path, context=context, expected_tip=initial._expected_tip, expected_count=0
        )
    assert caught.value.code == "path"


@pytest.mark.parametrize("role", ["evidence", "backup", "restore", "objects"])
def test_property_every_root_path_affects_header_digest(fixture, role):
    _, header, *_ = fixture
    changed = copy.deepcopy(header)
    changed["host_roots"][role]["path"] += "-changed"
    assert session.session_header_sha256(changed) != session.session_header_sha256(header)


def test_atomic_update_requires_explicit_serialization(fixture):
    profile, header, context, parent, _ = fixture
    path = parent / "serialized.json"
    original = session.create_official_session_v2(path, header, profile=profile, context=context)
    after = session.append_history_entry_v2(original, payload())
    with pytest.raises(session.OfficialSessionError) as caught:
        session.update_official_session_v2(
            path, original, after, context=context, caller_serialized=False
        )
    assert caught.value.code == "writer-serialization-required"
    assert path.read_bytes() == original._bytes


@pytest.mark.parametrize(
    "reference",
    [
        "external_source_anchor_reference",
        "historical_lifecycle_authority_reference",
        "deployment_verification_reference",
    ],
)
def test_rehashed_reference_mismatch_still_fails_profile_binding(fixture, reference):
    profile, _, *_ = fixture
    value = fresh(fixture).to_dict()
    value[reference]["sha256"] = "f" * 64
    header = {key: value[key] for key in EXPECTED_HEADER_KEYS}
    value["header_sha256"] = session.session_header_sha256(header)
    reject_load(value, profile=profile)


def test_header_observations_reject_root_mask_identity_alias(fixture):
    profile, header, *_ = fixture
    changed = copy.deepcopy(header)
    evidence_path = header["host_roots"]["evidence"]["path"]
    mask_path = header["masks"][0]["source_path"]
    source = next(
        entry for entry in changed["path_identity_binding"] if entry["path"] == evidence_path
    )
    alias = next(entry for entry in changed["path_identity_binding"] if entry["path"] == mask_path)
    alias["device"], alias["inode"] = source["device"], source["inode"]
    with pytest.raises(session.OfficialSessionError) as caught:
        session.new_official_session_v2(changed, profile=profile)
    assert caught.value.code == "overlap"


def test_parent_privacy_change_before_replace_rejected(fixture, monkeypatch):
    profile, header, context, parent, _ = fixture
    path = parent / "parent-changed.json"
    before = session.create_official_session_v2(path, header, profile=profile, context=context)
    after = session.append_history_entry_v2(before, payload())
    real_write = session._write_at

    def write_and_change(fd, content):
        real_write(fd, content)
        parent.chmod(0o755)

    monkeypatch.setattr(session, "_write_at", write_and_change)
    with pytest.raises(session.OfficialSessionError) as caught:
        session.update_official_session_v2(
            path, before, after, context=context, caller_serialized=True
        )
    assert caught.value.code == "mode"
    assert path.read_bytes() == before._bytes
