"""B1-only offline tests. No Django, Docker, Git, network, or real historical roots."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

MODULE_PATH = Path(
    os.environ.get(
        "S11_B1_MODULE_PATH",
        str(Path(__file__).resolve().parents[1] / "ex10_runtime_profile.py"),
    )
)
SPEC = importlib.util.spec_from_file_location("b1_runtime_profile_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
rp = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rp
SPEC.loader.exec_module(rp)
PROPERTY_CASES = 18


def encoded(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
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
def sample():
    base = Path(os.environ["S11_B1_TEST_BASE"])
    with tempfile.TemporaryDirectory(prefix="b1-", dir=base) as temporary:
        home = Path(temporary)
        repo = mkdir(home / "source")
        infra = mkdir(repo / "infra")
        first = write(infra / "docker-compose.yml")
        second = write(infra / "docker-compose.s11.yml")
        first.chmod(0o644)
        second.chmod(0o644)
        roots = {role: mkdir(home / "roots" / role) for role in rp.ROLES}
        mask = mkdir(home / "masked-var")
        override = write(home / "override.yml")
        uid, gid = os.getuid(), os.getgid()
        references = {}
        for name in rp.REFERENCE_FIELDS:
            path = write(home / (name + ".json"))
            references[name] = {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "schema_version": "s11-ex10-test-reference-v1",
            }
        value = {
            "schema_version": "s11-ex10-runtime-profile-v1",
            "profile_id": "offline-profile-1",
            "approved_at": "2026-10-04T00:00:00.000000Z",
            "source_revision": "a" * 40,
            "project": "offline-project",
            "compose_files": [
                {
                    "kind": "tracked",
                    "path": str(path),
                    "git_path": relative,
                    "git_blob": "b" * 40,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for path, relative in [
                    (first, "infra/docker-compose.yml"),
                    (second, "infra/docker-compose.s11.yml"),
                ]
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
            **references,
        }
        context = {
            "repo_root": str(repo),
            "historical_roots": [],
            "prior_session_roots": [],
            "running_sources": ["backend/module.py"],
            "approved_reference_schemas": {
                name: ["s11-ex10-test-reference-v1"] for name in rp.REFERENCE_FIELDS
            },
        }
        yield value, context, home


def reject(value, code, context=None):
    with pytest.raises(rp.RuntimeProfileError) as caught:
        rp.validate_runtime_profile(value, context)
    assert caught.value.code == code
    assert str(caught.value) == code


def test_valid_canonical_and_explicit_paths(sample):
    value, context, _ = sample
    profile = rp.parse_runtime_profile_bytes(encoded(value), context=context)
    assert profile.to_dict() == value
    assert rp.canonical_runtime_profile_bytes(profile) == encoded(value)
    assert rp.runtime_profile_sha256(profile) == hashlib.sha256(encoded(value)).hexdigest()
    identities = rp.validate_runtime_profile_paths(profile, context)
    assert identities
    assert not hasattr(profile, "approval_verified")


@pytest.mark.parametrize(
    "key",
    ["unexpected", "command", "shell", "environment", "secret", "password", "token", "AUTH_START"],
)
def test_unknown_forbidden_fields(sample, key):
    value, _, _ = sample
    value[key] = "synthetic-canary"
    reject(value, "unknown-field")


@pytest.mark.parametrize("key", sorted(rp.REQUIRED_FIELDS))
def test_missing_required_fields(sample, key):
    value, _, _ = sample
    del value[key]
    with pytest.raises(rp.RuntimeProfileError):
        rp.validate_runtime_profile(value)


@pytest.mark.parametrize("nested", [False, True])
def test_duplicate_keys_are_specific(sample, nested):
    value, _, _ = sample
    raw = encoded(value)
    if nested:
        raw = raw.replace(b'"uid":', b'"uid":0,"uid":', 1)
    else:
        raw = raw.replace(b'"project":', b'"project":"ignored","project":', 1)
    with pytest.raises(rp.RuntimeProfileError) as caught:
        rp.parse_runtime_profile_bytes(raw)
    assert caught.value.code == "duplicate-key"


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1.5"])
def test_nonfinite_and_float(sample, number):
    value, _, _ = sample
    raw = encoded(value).replace(
        b'"uid":' + str(os.getuid()).encode(), b'"uid":' + number.encode(), 1
    )
    with pytest.raises(rp.RuntimeProfileError):
        rp.parse_runtime_profile_bytes(raw)


@pytest.mark.parametrize("form", ["indent", "no-lf", "extra-lf", "reverse", "bom"])
def test_noncanonical_representation(sample, form):
    value, _, _ = sample
    raw = {
        "indent": json.dumps(value, indent=2).encode(),
        "no-lf": encoded(value)[:-1],
        "extra-lf": encoded(value) + b"\n",
        "reverse": (json.dumps(dict(reversed(list(value.items())))) + "\n").encode(),
        "bom": b"\xef\xbb\xbf" + encoded(value),
    }[form]
    with pytest.raises(rp.RuntimeProfileError) as caught:
        rp.parse_runtime_profile_bytes(raw)
    assert caught.value.code in {"noncanonical", "schema"}


@pytest.mark.parametrize("project", ["a", "a" * 63, "project_1-safe"])
def test_project_valid(sample, project):
    value, _, _ = sample
    value["project"] = project
    rp.validate_runtime_profile(value)


@pytest.mark.parametrize(
    "project",
    ["", "a" * 64, "Upper", "a/b", "a:b", "a b", "a\n", "a\t", "a\x00", "é", "$(id)", "a;id"],
)
def test_project_rejected(sample, project):
    value, _, _ = sample
    value["project"] = project
    with pytest.raises(rp.RuntimeProfileError):
        rp.validate_runtime_profile(value)


@pytest.mark.parametrize(
    "path",
    [
        "relative",
        "/double//component",
        "/trailing/",
        "/dot/./path",
        "/parent/../path",
        "//double",
        "/",
    ],
)
def test_path_lexical_rejection(sample, path):
    value, _, _ = sample
    value["host_roots"]["evidence"]["path"] = path
    reject(value, "path")


@pytest.mark.parametrize("place", ["repo", "var", "tmp", "repo-ancestor"])
def test_forbidden_root_locations(sample, place):
    value, context, home = sample
    repo = Path(value["source_host_root"])
    paths = {
        "repo": mkdir(repo / "evidence"),
        "var": mkdir(repo / "var" / "evidence"),
        "tmp": Path("/tmp/forbidden-b1-root"),
        "repo-ancestor": home,
    }
    value["host_roots"]["evidence"]["path"] = str(paths[place])
    reject(value, "path", context)


@pytest.mark.parametrize("where", ["leaf", "parent"])
def test_symlink_components(sample, where):
    value, context, home = sample
    actual = Path(value["host_roots"]["evidence"]["path"])
    if where == "leaf":
        link = home / "linked-root"
        link.symlink_to(actual, target_is_directory=True)
        candidate = link
    else:
        link = home / "linked-parent"
        link.symlink_to(actual.parent, target_is_directory=True)
        candidate = link / actual.name
    value["host_roots"]["evidence"]["path"] = str(candidate)
    reject(value, "symlink", context)


@pytest.mark.parametrize("case", ["duplicate", "nested"])
def test_root_overlap(sample, case):
    value, context, _ = sample
    path = Path(value["host_roots"]["evidence"]["path"])
    other = path if case == "duplicate" else mkdir(path / "backup")
    value["host_roots"]["backup"]["path"] = str(other)
    reject(value, "overlap", context)


@pytest.mark.parametrize("which", ["historical_roots", "prior_session_roots"])
def test_external_exclusions_block_before_filesystem(sample, which, monkeypatch):
    value, context, _ = sample
    context[which] = [value["host_roots"]["evidence"]["path"]]

    def unexpected_walk(*args):
        raise AssertionError("Excluded paths must not be opened")

    monkeypatch.setattr(rp, "_walk", unexpected_walk)
    reject(value, "overlap", context)


@pytest.mark.parametrize("wrong", ["owner", "gid", "mode"])
def test_root_owner_and_mode(sample, wrong):
    value, context, _ = sample
    if wrong == "mode":
        Path(value["host_roots"]["evidence"]["path"]).chmod(0o755)
        reject(value, "mode", context)
    else:
        key = "uid" if wrong == "owner" else "gid"
        value["expected_owner"][key] += 1
        for root in value["host_roots"].values():
            root["owner_" + key] += 1
        reject(value, "owner", context)


@pytest.mark.parametrize("role", ["evidence", "objects"])
def test_fixed_targets_immutable(sample, role):
    value, _, _ = sample
    value["fixed_targets"][role] = "/arbitrary"
    reject(value, "target")


def test_unknown_target(sample):
    value, _, _ = sample
    value["fixed_targets"]["other"] = "/other"
    reject(value, "unknown-field")


@pytest.mark.parametrize(
    "key,new_value",
    [("source_read_only", False), ("source_read_only", 1), ("source_container_target", "/other")],
)
def test_source_contract(sample, key, new_value):
    value, _, _ = sample
    value[key] = new_value
    reject(value, "target")


@pytest.mark.parametrize("case", ["missing", "extra", "beat", "reordered"])
def test_service_contract(sample, case):
    value, _, _ = sample
    if case == "missing":
        value["expected_services"].pop()
    elif case == "extra":
        value["expected_services"].append("beat")
    elif case == "beat":
        value["beat_policy"] = "enabled"
    else:
        value["expected_services"].reverse()
    reject(value, "service")


@pytest.mark.parametrize("hash_value", ["A" * 64, "a" * 63, "g" * 64])
def test_hash_reference_rejection(sample, hash_value):
    value, _, _ = sample
    value["decision_reference"]["sha256"] = hash_value
    reject(value, "hash")


@pytest.mark.parametrize("which", ["approved_at", "decision_reference"])
def test_approval_missing_specific(sample, which):
    value, _, _ = sample
    del value[which]
    reject(value, "approval-reference")


@pytest.mark.parametrize(
    "timestamp", ["2026-10-04", "2026-10-04T00:00:00Z", "2026-02-30T00:00:00.000000Z"]
)
def test_approval_timestamp_shape(sample, timestamp):
    value, _, _ = sample
    value["approved_at"] = timestamp
    reject(value, "approval-reference")


def test_approval_reference_not_self_approved(sample):
    value, _, _ = sample
    value["decision_reference"] = {}
    reject(value, "approval-reference")


@pytest.mark.parametrize(
    "case",
    ["missing-var", "missing-env", "wrong-var-kind", "wrong-device", "source-hidden", "nonempty"],
)
def test_mask_contract(sample, case):
    value, context, home = sample
    if case == "missing-var":
        value["masks"].pop(0)
    elif case == "missing-env":
        value["masks"].pop()
    elif case == "wrong-var-kind":
        value["masks"][0]["kind"] = "dev_null_file"
        value["masks"][0]["source_path"] = "/dev/null"
    elif case == "wrong-device":
        value["masks"][1]["source_path"] = str(home / "fake-null")
    elif case == "source-hidden":
        context["running_sources"] = ["var/source.py"]
    else:
        write(Path(value["masks"][0]["source_path"]) / "not-empty")
    reject(value, "mask", context)


def test_supplied_reference_allowlist(sample):
    value, context, _ = sample
    context["approved_reference_schemas"]["decision_reference"] = ["s11-other-v1"]
    reject(value, "approval-reference", context)


def test_context_has_no_silent_defaults(sample):
    value, context, _ = sample
    del context["historical_roots"]
    reject(value, "schema", context)


@pytest.mark.parametrize("case", ["mode", "hardlink", "symlink", "noncanonical"])
def test_private_profile_file_loading(sample, case):
    value, _, home = sample
    path = write(home / "profile.json", encoded(value))
    if case == "mode":
        path.chmod(0o644)
        code = "mode"
    elif case == "hardlink":
        os.link(path, home / "second-link")
        code = "path"
    elif case == "symlink":
        link = home / "profile-link"
        link.symlink_to(path)
        path = link
        code = "symlink"
    else:
        path.write_bytes(encoded(value)[:-1])
        code = "noncanonical"
    with pytest.raises(rp.RuntimeProfileError) as caught:
        rp.load_runtime_profile(path)
    assert caught.value.code == code


def test_private_profile_load_success(sample):
    value, context, home = sample
    path = write(home / "profile.json", encoded(value))
    assert rp.load_runtime_profile(path, context=context).to_dict() == value


def test_snapshot_detects_replaced_root(sample):
    value, context, home = sample
    before = rp.validate_runtime_profile_paths(value, context)
    root = Path(value["host_roots"]["evidence"]["path"])
    root.rename(home / "old-root")
    mkdir(root)
    after = rp.validate_runtime_profile_paths(value, context)
    assert before != after


def test_immutable_profile_and_defensive_copy(sample):
    value, _, _ = sample
    profile = rp.validate_runtime_profile(value)
    changed = profile.to_dict()
    changed["project"] = "different"
    assert profile.to_dict()["project"] == value["project"]
    with pytest.raises(AttributeError):
        profile._bytes = b"{}"
    assert "offline-project" not in repr(profile)


def test_all_errors_are_sanitized(sample):
    value, _, _ = sample
    value["unexpected-private-canary"] = "synthetic-private-value"
    with pytest.raises(rp.RuntimeProfileError) as caught:
        rp.validate_runtime_profile(value)
    assert "canary" not in str(caught.value)
    assert "synthetic" not in str(caught.value)


def test_size_utf8_and_json_guards():
    for raw in [b"", b"[]\n", b"\xff", b"{" * 2000, b"x" * (rp.MAX_PROFILE_BYTES + 1)]:
        with pytest.raises(rp.RuntimeProfileError):
            rp.parse_runtime_profile_bytes(raw)


@pytest.mark.parametrize("seed", range(8))
def test_property_canonical_idempotence_and_key_order(sample, seed):
    value, _, _ = sample
    keys = list(value)
    rotated = keys[seed:] + keys[:seed]
    altered = {key: value[key] for key in rotated}
    profile = rp.validate_runtime_profile(altered)
    raw = rp.canonical_runtime_profile_bytes(profile)
    assert rp.canonical_runtime_profile_bytes(rp.parse_runtime_profile_bytes(raw)) == raw
    assert raw == encoded(value)
    assert rp.runtime_profile_sha256(altered) == rp.runtime_profile_sha256(value)


@pytest.mark.parametrize("seed", range(5))
def test_property_unknown_keys_never_dropped(sample, seed):
    value, _, _ = sample
    value["unknown_" + str(seed)] = seed
    reject(value, "unknown-field")


@pytest.mark.parametrize("alias", ["dot", "parent", "separator"])
def test_property_path_alias_identity_is_not_normalized(sample, alias):
    value, context, _ = sample
    original = value["host_roots"]["evidence"]["path"]
    candidate = {
        "dot": original + "/.",
        "parent": original + "/../evidence",
        "separator": original + "//",
    }[alias]
    assert os.path.realpath(candidate) == original
    value["host_roots"]["evidence"]["path"] = candidate
    reject(value, "path", context)


def test_property_hash_stable_across_processes(sample):
    value, _, home = sample
    path = write(home / "profile.json", encoded(value))
    code = (
        "import importlib.util,sys;"
        "s=importlib.util.spec_from_file_location('profile_child',sys.argv[1]);"
        "m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);"
        "print(m.runtime_profile_sha256(m.load_runtime_profile(sys.argv[2])))"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(MODULE_PATH), str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == rp.runtime_profile_sha256(value)


def test_property_mutation_changes_hash(sample):
    value, _, _ = sample
    original = rp.runtime_profile_sha256(value)
    changed = copy.deepcopy(value)
    changed["profile_id"] = "different-profile"
    assert rp.runtime_profile_sha256(changed) != original


@pytest.mark.parametrize("kind", [[], {}, None, 1])
def test_mask_kind_type_errors_are_sanitized(sample, kind):
    value, _, _ = sample
    value["masks"][0]["kind"] = kind
    reject(value, "mask")


def test_directory_mask_cannot_alias_root_identity(sample, monkeypatch):
    value, context, _ = sample
    real_private = rp._private
    evidence = value["host_roots"]["evidence"]["path"]
    mask = value["masks"][0]["source_path"]
    info = os.stat(evidence)

    def observed_alias(path, kind, uid, gid, mode):
        observed = real_private(path, kind, uid, gid, mode)
        if path == mask:
            return (*observed[:-1], (path, info.st_dev, info.st_ino))
        return observed

    monkeypatch.setattr(rp, "_private", observed_alias)
    reject(value, "overlap", context)


@pytest.mark.parametrize("marker", ["password=synthetic", "Bearer synthetic", "tskey-synthetic"])
def test_secret_value_markers_are_rejected(sample, marker):
    value, _, _ = sample
    value["profile_id"] = marker
    reject(value, "schema")


def test_root_must_be_directory(sample):
    value, context, home = sample
    path = write(home / "not-a-directory")
    value["host_roots"]["evidence"]["path"] = str(path)
    reject(value, "path", context)


@pytest.mark.parametrize("where", ["repo", "var", "tmp", "history", "prior"])
def test_profile_location_gate_precedes_read(sample, monkeypatch, where):
    value, context, home = sample
    repo = Path(value["source_host_root"])
    paths = {
        "repo": repo / "profile.json",
        "var": repo / "var" / "profile.json",
        "tmp": Path("/tmp/profile.json"),
        "history": home / "history" / "profile.json",
        "prior": home / "prior" / "profile.json",
    }
    context["historical_roots"] = [str(home / "history")]
    context["prior_session_roots"] = [str(home / "prior")]

    def unexpected_walk(*args):
        raise AssertionError("Forbidden profile must not be opened")

    monkeypatch.setattr(rp, "_walk", unexpected_walk)
    with pytest.raises(rp.RuntimeProfileError) as caught:
        rp.load_runtime_profile(paths[where], context=context)
    assert caught.value.code == ("overlap" if where in {"history", "prior"} else "path")


def test_mask_filesystem_error_is_sanitized(sample, monkeypatch):
    value, context, _ = sample

    def unavailable_directory(*args):
        raise PermissionError("synthetic-private-canary")

    monkeypatch.setattr(rp.os, "listdir", unavailable_directory)
    reject(value, "path", context)
