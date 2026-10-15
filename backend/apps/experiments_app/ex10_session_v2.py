"""EX-10 B2: offline session codecs and caller-scoped private record I/O.

No launcher dispatch, Git, Docker, lifecycle, authority, coordination or run creation.
References and path observations are typed data, never authenticated here.
Every serialized history needs a caller-supplied tip/count, including genesis.
Atomic update requires a serialized caller and controlled parent namespace; it is not CAS.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import ex10_runtime_profile as rp

SESSION_V1_SCHEMA = "s11c3-ex10-official-launcher-session-v1"
SESSION_V2_SCHEMA = "s11c3-ex10-official-launcher-session-v2"
SESSION_KIND = "operator-session-record-not-experimental-evidence"
MAX_SESSION_BYTES = 1024 * 1024
V1_HEADER_KEYS = (
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
HEADER_KEYS = V1_HEADER_KEYS + (
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
DYNAMIC_KEYS = frozenset(
    {
        "header_sha256",
        "preflight_report_sha256",
        "operational_envelope_sha256",
        "official_run_id",
        "history",
        "state",
        "in_flight",
    }
)
SNAPSHOT_KEYS = frozenset(
    {
        "state",
        "official_run_id",
        "preflight_report_sha256",
        "operational_envelope_sha256",
        "in_flight",
    }
)
LAUNCHER_PATHS = (
    "backend/scripts/s11_ex10_official_launcher.py",
    "backend/scripts/s11_ex10_preflight_launcher.py",
)
_RUN_ID = re.compile(r"s11-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.ASCII)
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", re.ASCII)
_MODE = re.compile(r"0[0-7]{3}", re.ASCII)
ACTIONS = frozenset(
    {
        "initialize-session",
        "inspect",
        "start-official",
        "status",
        "execute-official",
        "drain",
        "reconcile",
        "finalize",
        "abandon-official-pre-t0",
        "declare-executor-gone",
        "cleanup-stack",
    }
)
V1_MUTATING = ACTIONS - {"inspect", "status"}
TERMINAL_PHASES = frozenset(
    {
        "ABANDONED_PRE_T0",
        "NOT_DEMONSTRATED",
        "FINALIZED_PASS",
        "FINALIZED_FAIL",
    }
)
PHASES = frozenset(
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


class OfficialSessionError(ValueError):
    """Sanitized fixed category, without record contents or filesystem names."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise OfficialSessionError(code)


def _keys(value: Any, required: set[str] | frozenset[str], code: str = "schema") -> None:
    if type(value) is not dict:
        _fail(code)
    if set(value) - required:
        _fail("unknown-field")
    if required - set(value):
        _fail(code)


def _dump(value: Any) -> bytes:
    try:
        raw = (
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            + "\n"
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("schema")
    if len(raw) > MAX_SESSION_BYTES:
        _fail("schema")
    return raw


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate-key")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    _fail("schema")


def _json(data: bytes, *, unique: bool = True) -> dict[str, Any]:
    if type(data) is not bytes or not data or len(data) > MAX_SESSION_BYTES:
        _fail("schema")
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs if unique else None,
            parse_constant=_constant,
        )
        if type(value) is not dict:
            _fail("schema")
        if data != _dump(value):
            _fail("noncanonical")
        return value
    except OfficialSessionError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("schema")
    raise AssertionError("unreachable")


def _b1(function: Callable, *args: Any) -> Any:
    """Reuse the frozen B1 primitive type contracts without changing B1."""
    try:
        return function(*args)
    except rp.RuntimeProfileError as exc:
        raise OfficialSessionError(exc.code) from None
    except (ValueError, TypeError, RecursionError):
        _fail("schema")
    raise AssertionError("unreachable")


def _uint(value: Any, *, positive: bool = False) -> None:
    if type(value) is not int or value < (1 if positive else 0) or value >= 2**64:
        _fail("schema")


def _run_id(value: Any) -> None:
    if value is not None and (type(value) is not str or not _RUN_ID.fullmatch(value)):
        _fail("run-id")


def _state(value: Any) -> None:
    _keys(value, {"known_phase", "uncertain", "blocked", "next_allowed_actions"}, "state")
    if value["known_phase"] is not None and (
        type(value["known_phase"]) is not str or value["known_phase"] not in PHASES
    ):
        _fail("state")
    if type(value["uncertain"]) is not bool or type(value["blocked"]) is not bool:
        _fail("state")
    actions = value["next_allowed_actions"]
    if type(actions) is not list or any(
        type(action) is not str or action not in ACTIONS for action in actions
    ):
        _fail("state")
    if actions != sorted(set(actions)):
        _fail("state")


def _snapshot(value: Any) -> None:
    _keys(value, SNAPSHOT_KEYS, "state")
    _state(value["state"])
    _run_id(value["official_run_id"])
    for key in ("preflight_report_sha256", "operational_envelope_sha256"):
        if value[key] is not None:
            _b1(rp._hash, value[key])
    records = value["in_flight"]
    if type(records) is not list:
        _fail("state")
    for record in records:
        _keys(
            record, {"action", "container_name", "output_dir", "started_at", "after_seq"}, "state"
        )
        if type(record["action"]) is not str or record["action"] not in V1_MUTATING:
            _fail("state")
        _b1(rp._match, record["container_name"], rp._ID, "state")
        _b1(rp._relative, record["output_dir"], "state")
        _timestamp(record["started_at"])
        _uint(record["after_seq"])


def _initial_snapshot() -> dict[str, Any]:
    return {
        "state": {
            "known_phase": None,
            "uncertain": False,
            "blocked": False,
            "next_allowed_actions": ["inspect"],
        },
        "official_run_id": None,
        "preflight_report_sha256": None,
        "operational_envelope_sha256": None,
        "in_flight": [],
    }


def _timestamp(value: Any) -> None:
    _b1(rp._match, value, rp._TIMESTAMP, "schema")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        _fail("schema")


def _validate_header(header: Any) -> None:
    _keys(header, set(HEADER_KEYS))
    _b1(rp._tree, header)
    if header["schema_version"] != SESSION_V2_SCHEMA or header["kind"] != SESSION_KIND:
        _fail("schema")
    _b1(rp._match, header["session_id"], rp._ID, "schema")
    _timestamp(header["created_at"])
    _b1(rp._match, header["expected_git_sha"], rp._SHA1, "hash")
    _b1(rp._match, header["branch"], _BRANCH, "schema")
    if ".." in header["branch"] or "//" in header["branch"] or header["branch"].endswith("/"):
        _fail("schema")
    _b1(rp._absolute, header["repo_root"])
    _b1(rp._match, header["project"], rp._PROJECT, "project")
    if header["evidence_mode"] != "OFFICIAL":
        _fail("schema")
    _keys(header["launcher_blobs"], set(LAUNCHER_PATHS), "hash")
    for blob in header["launcher_blobs"].values():
        _b1(rp._match, blob, rp._SHA1, "hash")
    if header["runtime_profile_schema"] != rp.SCHEMA_VERSION:
        _fail("profile-binding")
    _b1(rp._match, header["runtime_profile_id"], rp._ID, "profile-binding")
    _b1(rp._hash, header["runtime_profile_sha256"])
    _b1(rp._reference, header["runtime_profile_approval_reference"])
    _keys(header["host_roots"], set(rp.ROLES), "path")
    owner = None
    for root in header["host_roots"].values():
        _keys(root, {"path", "owner_uid", "owner_gid", "directory_mode", "artifact_mode"}, "path")
        _b1(rp._absolute, root["path"])
        _b1(rp._integer, root["owner_uid"])
        _b1(rp._integer, root["owner_gid"])
        pair = (root["owner_uid"], root["owner_gid"])
        if owner is not None and pair != owner:
            _fail("owner")
        owner = pair
        if root["directory_mode"] != "0700" or root["artifact_mode"] != "0600":
            _fail("mode")
    roots = [header["host_roots"][role]["path"] for role in rp.ROLES]
    for index, root in enumerate(roots):
        _b1(rp._outside, root, header["repo_root"], [])
        if any(rp._overlap(root, other) for other in roots[index + 1 :]):
            _fail("overlap")
    _keys(header["fixed_targets"], set(rp.ROLES), "target")
    if header["fixed_targets"] != rp.FIXED_TARGETS:
        _fail("target")
    _b1(rp._absolute, header["override_path"])
    _b1(rp._outside, header["override_path"], header["repo_root"], roots)
    _b1(rp._hash, header["override_sha256"])
    source = header["source_binding"]
    _keys(source, {"host", "target", "read_only"}, "target")
    if (
        source["host"] != header["repo_root"]
        or source["target"] != "/s11-full-source"
        or source["read_only"] is not True
    ):
        _fail("target")
    compose = header["compose_files"]
    if type(compose) is not list or len(compose) != 3:
        _fail("schema")
    for index, entry in enumerate(compose):
        _keys(entry, {"kind", "path", "git_path", "git_blob", "sha256"})
        _b1(rp._absolute, entry["path"])
        _b1(rp._hash, entry["sha256"])
        if index < 2:
            relative = ("infra/docker-compose.yml", "infra/docker-compose.s11.yml")[index]
            if (
                entry["kind"] != "tracked"
                or entry["git_path"] != relative
                or entry["path"] != header["repo_root"] + "/" + relative
            ):
                _fail("schema")
            _b1(rp._match, entry["git_blob"], rp._SHA1, "hash")
        elif (
            entry["kind"] != "external_override"
            or entry["git_path"] is not None
            or entry["git_blob"] is not None
            or entry["path"] != header["override_path"]
            or entry["sha256"] != header["override_sha256"]
        ):
            _fail("hash")
    masks = header["masks"]
    if type(masks) is not list or not 2 <= len(masks) <= 64:
        _fail("mask")
    seen = {}
    for mask in masks:
        _keys(mask, {"relative_path", "kind", "source_path", "read_only"}, "mask")
        target = _b1(rp._relative, mask["relative_path"], "mask")
        _b1(rp._absolute, mask["source_path"])
        if target in seen or mask["read_only"] is not True or type(mask["kind"]) is not str:
            _fail("mask")
        if mask["kind"] == "dev_null_file":
            if target != ".env" or mask["source_path"] != "/dev/null":
                _fail("mask")
        elif mask["kind"] == "directory":
            _b1(rp._outside, mask["source_path"], header["repo_root"], roots)
        else:
            _fail("mask")
        seen[target] = mask["kind"]
    if seen.get("var") != "directory" or seen.get(".env") != "dev_null_file":
        _fail("mask")
    if (
        header["expected_services"] != ["db", "redis", "backend", "worker"]
        or header["beat_policy"] != "disabled"
    ):
        _fail("service")
    for name in rp.REFERENCE_FIELDS[1:]:
        _b1(rp._reference, header[name])
    _path_bindings(header)


def _path_bindings(header: Mapping[str, Any]) -> None:
    roots = header["host_roots"]
    expected = {
        root["path"]: (root["owner_uid"], root["owner_gid"], "0700") for root in roots.values()
    }
    uid, gid = roots["evidence"]["owner_uid"], roots["evidence"]["owner_gid"]
    mask_paths = []
    for mask in header["masks"]:
        if mask["kind"] == "directory":
            if any(rp._overlap(mask["source_path"], other) for other in mask_paths):
                _fail("overlap")
            mask_paths.append(mask["source_path"])
        expected[mask["source_path"]] = (
            (0, 0, "0666") if mask["kind"] == "dev_null_file" else (uid, gid, "0700")
        )
    expected[header["repo_root"]] = (uid, gid, None)
    for entry in header["compose_files"][:2]:
        expected[entry["path"]] = (uid, gid, None)
    references = [header["runtime_profile_approval_reference"]]
    references.extend(header[name] for name in rp.REFERENCE_FIELDS[1:])
    private = [header["override_path"], *(reference["path"] for reference in references)]
    if len(set(private)) != len(private):
        _fail("overlap")
    for path in private:
        _b1(rp._outside, path, header["repo_root"], [*expected])
        expected[path] = (uid, gid, "0600")
    bindings = header["path_identity_binding"]
    if type(bindings) is not list or len(bindings) != len(expected):
        _fail("path")
    names = []
    private_directory_paths = set(root["path"] for root in roots.values()) | set(mask_paths)
    directory_identities = set()
    for entry in bindings:
        _keys(entry, {"path", "device", "inode", "uid", "gid", "mode"}, "path")
        path = _b1(rp._absolute, entry["path"])
        if path not in expected:
            _fail("path")
        _uint(entry["device"])
        _uint(entry["inode"], positive=True)
        _b1(rp._integer, entry["uid"])
        _b1(rp._integer, entry["gid"])
        _b1(rp._match, entry["mode"], _MODE, "mode")
        wanted_uid, wanted_gid, wanted_mode = expected[path]
        if (entry["uid"], entry["gid"]) != (wanted_uid, wanted_gid):
            _fail("owner")
        if wanted_mode is not None and entry["mode"] != wanted_mode:
            _fail("mode")
        if path in private_directory_paths:
            identity = (entry["device"], entry["inode"])
            if identity in directory_identities:
                _fail("overlap")
            directory_identities.add(identity)
        names.append(path)
    if names != sorted(expected):
        _fail("path")


def canonical_session_header_bytes(header: Mapping[str, Any]) -> bytes:
    """Hash projection only; the session loader separately validates field semantics."""
    _keys(header, set(HEADER_KEYS))
    _b1(rp._tree, header)
    return _dump(header)


def session_header_sha256(header: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_session_header_bytes(header)).hexdigest()


def initial_history_seed(header: Mapping[str, Any]) -> str:
    return session_header_sha256(header)


def _profile_binding(
    header: Mapping[str, Any], profile: rp.RuntimeProfile | Mapping[str, Any]
) -> None:
    validated = _b1(rp.validate_runtime_profile, profile)
    value = validated.to_dict()
    if (
        header["runtime_profile_schema"] != value["schema_version"]
        or header["runtime_profile_id"] != value["profile_id"]
        or header["runtime_profile_sha256"] != rp.runtime_profile_sha256(validated)
    ):
        _fail("profile-binding")
    pairs = {
        "expected_git_sha": "source_revision",
        "repo_root": "source_host_root",
        "project": "project",
        "host_roots": "host_roots",
        "fixed_targets": "fixed_targets",
        "compose_files": "compose_files",
        "override_path": "override_path",
        "override_sha256": "override_sha256",
        "masks": "masks",
        "expected_services": "expected_services",
        "beat_policy": "beat_policy",
        **{name: name for name in rp.REFERENCE_FIELDS[1:]},
    }
    if any(header[name] != value[source] for name, source in pairs.items()):
        _fail("profile-binding")
    if header["source_binding"] != {
        "host": value["source_host_root"],
        "target": value["source_container_target"],
        "read_only": value["source_read_only"],
    }:
        _fail("profile-binding")


def new_session_v2_header(
    profile: rp.RuntimeProfile | Mapping[str, Any],
    *,
    session_id: str,
    created_at: str,
    branch: str,
    launcher_blobs: Mapping[str, str],
    runtime_profile_approval_reference: Mapping[str, Any],
    path_identity_binding: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pure builder. Every non-profile identity is explicit; no defaults or discovery."""
    validated = _b1(rp.validate_runtime_profile, profile)
    value = validated.to_dict()
    _keys(launcher_blobs, set(LAUNCHER_PATHS), "hash")
    _b1(rp._reference, runtime_profile_approval_reference)
    header = {
        "schema_version": SESSION_V2_SCHEMA,
        "kind": SESSION_KIND,
        "session_id": session_id,
        "created_at": created_at,
        "expected_git_sha": value["source_revision"],
        "branch": branch,
        "repo_root": value["source_host_root"],
        "project": value["project"],
        "evidence_mode": "OFFICIAL",
        "launcher_blobs": dict(launcher_blobs),
        "runtime_profile_schema": value["schema_version"],
        "runtime_profile_id": value["profile_id"],
        "runtime_profile_sha256": rp.runtime_profile_sha256(validated),
        "runtime_profile_approval_reference": dict(runtime_profile_approval_reference),
        "source_binding": {
            "host": value["source_host_root"],
            "target": value["source_container_target"],
            "read_only": value["source_read_only"],
        },
        "path_identity_binding": path_identity_binding,
        **{
            key: value[key]
            for key in (
                "host_roots",
                "fixed_targets",
                "compose_files",
                "override_path",
                "override_sha256",
                "masks",
                "deployment_verification_reference",
                "historical_lifecycle_authority_reference",
                "external_source_anchor_reference",
                "expected_services",
                "beat_policy",
            )
        },
    }
    _validate_header(header)
    return json.loads(_dump(header))


def _entry(entry: Any) -> None:
    _keys(entry, {"seq", "previous_digest", "payload", "chain"}, "history")
    _uint(entry["seq"], positive=True)
    _b1(rp._hash, entry["previous_digest"])
    _b1(rp._hash, entry["chain"])
    payload = entry["payload"]
    _keys(payload, SNAPSHOT_KEYS | {"event", "output_sha256"}, "history")
    _snapshot({key: payload[key] for key in SNAPSHOT_KEYS})
    _b1(rp._tree, payload)
    _b1(rp._match, payload["event"], rp._ID, "history")
    if type(payload["output_sha256"]) is not dict:
        _fail("history")
    for name, value in payload["output_sha256"].items():
        _b1(rp._relative, name, "history")
        _b1(rp._hash, value)


def history_entry_payload(entry: Mapping[str, Any]) -> bytes:
    _entry(entry)
    return _dump({key: value for key, value in entry.items() if key != "chain"})


def history_entry_chain(previous_digest: str, entry: Mapping[str, Any]) -> str:
    _b1(rp._hash, previous_digest)
    return hashlib.sha256(
        previous_digest.encode("ascii") + history_entry_payload(entry)
    ).hexdigest()


def verify_history_chain(
    seed: str, history: list[dict[str, Any]], *, expected_tip: str, expected_count: int
) -> str:
    """Trusted observations supplied by caller; hashes do not authenticate that caller."""
    _b1(rp._hash, seed)
    _b1(rp._hash, expected_tip)
    _uint(expected_count)
    if type(history) is not list or len(history) != expected_count:
        _fail("history")
    previous = seed
    for seq, entry in enumerate(history, 1):
        _entry(entry)
        if (
            entry["seq"] != seq
            or entry["previous_digest"] != previous
            or entry["chain"] != history_entry_chain(previous, entry)
        ):
            _fail("history")
        previous = entry["chain"]
    if previous != expected_tip:
        _fail("history")
    return previous


def _validate_v2(
    value: dict[str, Any], expected_tip: str | None, expected_count: int | None
) -> tuple[str, int]:
    _keys(value, set(HEADER_KEYS) | DYNAMIC_KEYS)
    header = {key: value[key] for key in HEADER_KEYS}
    _validate_header(header)
    digest = session_header_sha256(header)
    if value["header_sha256"] != digest:
        _fail("header-binding")
    history = value["history"]
    if type(history) is not list:
        _fail("history")
    if expected_tip is None or expected_count is None:
        _fail("history-checkpoint-required")
    verify_history_chain(digest, history, expected_tip=expected_tip, expected_count=expected_count)
    snapshot = {key: value[key] for key in SNAPSHOT_KEYS}
    _snapshot(snapshot)
    bound = (
        {key: history[-1]["payload"][key] for key in SNAPSHOT_KEYS}
        if history
        else _initial_snapshot()
    )
    if snapshot != bound:
        _fail("state-binding")
    if any(record["after_seq"] > len(history) for record in snapshot["in_flight"]):
        _fail("state")
    return expected_tip, expected_count


@dataclass(frozen=True, slots=True)
class OfficialSessionV2:
    _bytes: bytes = field(repr=False)
    _expected_tip: str = field(repr=False)
    _expected_count: int = field(repr=False)

    def __post_init__(self) -> None:
        _validate_v2(_json(self._bytes), self._expected_tip, self._expected_count)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._bytes)

    @property
    def header(self) -> dict[str, Any]:
        value = self.to_dict()
        return {key: value[key] for key in HEADER_KEYS}


def load_session_v2(
    data: bytes,
    *,
    profile: rp.RuntimeProfile | Mapping[str, Any] | None = None,
    expected_tip: str | None = None,
    expected_count: int | None = None,
) -> OfficialSessionV2:
    value = _json(data)
    if value.get("schema_version") != SESSION_V2_SCHEMA:
        _fail("schema")
    tip, count = _validate_v2(value, expected_tip, expected_count)
    if profile is not None:
        _profile_binding({key: value[key] for key in HEADER_KEYS}, profile)
    return OfficialSessionV2(data, tip, count)


def _require_v2(session: Any) -> OfficialSessionV2:
    if not isinstance(session, OfficialSessionV2):
        _fail("v1-read-only")
    return session


def verify_official_session_v2(
    session: OfficialSessionV2, *, profile: rp.RuntimeProfile | Mapping[str, Any] | None = None
) -> OfficialSessionV2:
    session = _require_v2(session)
    return load_session_v2(
        session._bytes,
        profile=profile,
        expected_tip=session._expected_tip,
        expected_count=session._expected_count,
    )


def new_official_session_v2(
    header: Mapping[str, Any], *, profile: rp.RuntimeProfile | Mapping[str, Any]
) -> OfficialSessionV2:
    _validate_header(header)
    _profile_binding(header, profile)
    seed = initial_history_seed(header)
    value = {**header, "header_sha256": seed, "history": [], **_initial_snapshot()}
    return load_session_v2(_dump(value), profile=profile, expected_tip=seed, expected_count=0)


def append_history_entry_v2(
    session: OfficialSessionV2, payload: Mapping[str, Any]
) -> OfficialSessionV2:
    session = verify_official_session_v2(_require_v2(session))
    value = session.to_dict()
    previous = session._expected_tip
    entry = {
        "seq": session._expected_count + 1,
        "previous_digest": previous,
        "payload": payload,
        "chain": "0" * 64,
    }
    entry["chain"] = history_entry_chain(previous, entry)
    value["history"].append(entry)
    value.update({key: payload[key] for key in SNAPSHOT_KEYS})
    return load_session_v2(_dump(value), expected_tip=entry["chain"], expected_count=entry["seq"])


def _v1_coherent(value: Mapping[str, Any], action: str) -> list[dict[str, Any]]:
    return [
        entry
        for entry in value.get("history", [])
        if entry["action"] == action and entry["classification"] == "COHERENT"
    ]


def _v1_state(value: Mapping[str, Any]) -> dict[str, Any]:
    """Frozen pure baseline derive_state semantics; no v2 defaults."""
    history = list(value.get("history", []))
    run_id = value.get("official_run_id")
    phase, uncertain, blocked = None, False, False
    in_flight = list(value.get("in_flight", []))
    pending = {record["after_seq"] for record in in_flight}
    for position, entry in enumerate(history):
        if position in pending:
            uncertain = True
        if entry.get("session_blocking"):
            blocked = True
        reported = entry.get("phase")
        if entry["classification"] == "COHERENT" and reported is not None:
            phase, uncertain = reported, False
        elif entry["action"] not in ACTIONS:
            _fail("v1-malformed")
        elif entry["action"] in V1_MUTATING and entry["classification"] in {
            "LAUNCHER_FAILURE",
            "APPARATUS_ABORT",
        }:
            uncertain = True
    if any(record["after_seq"] >= len(history) for record in in_flight):
        uncertain = True
    invoked = {
        entry["action"] for entry in history if entry["classification"] != "VALIDATION_FAILED"
    }
    invoked |= {record["action"] for record in in_flight}
    allowed = ["inspect"]
    if blocked:
        pass
    elif run_id is None:
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
        elif phase == "RECONCILING" and not _v1_coherent(value, "reconcile"):
            allowed.append("reconcile")
        elif phase in {"RECONCILING", "DECIDED"} and _v1_coherent(value, "reconcile"):
            allowed.append("finalize")
        elif phase in TERMINAL_PHASES:
            allowed.append("cleanup-stack")
        if (
            phase in {"STARTED", "EMITTING"}
            and "execute-official" in invoked
            and "declare-executor-gone" not in invoked
        ):
            allowed.append("declare-executor-gone")
    return {
        "known_phase": phase,
        "uncertain": uncertain,
        "blocked": blocked,
        "next_allowed_actions": sorted(set(allowed)),
    }


def _validate_v1(
    value: dict[str, Any],
    repo_root: str,
    session_root: str | None,
    output_verifier: Callable[[str], Mapping[str, Any]] | None,
) -> None:
    try:
        if (
            value.get("schema_version") != SESSION_V1_SCHEMA
            or value.get("project") != "ciudadania-s11-ex10-official"
        ):
            _fail("v1-malformed")
        if value.get("repo_root") != repo_root:
            _fail("v1-repository")
        header = {key: value.get(key) for key in V1_HEADER_KEYS}
        digest = hashlib.sha256(_dump(header)).hexdigest()
        if value.get("header_sha256") != digest:
            _fail("history")
        previous = digest
        for entry in value.get("history", []):
            if not isinstance(entry, dict):
                _fail("history")
            body = {key: item for key, item in entry.items() if key != "chain"}
            chain = hashlib.sha256(previous.encode() + _dump(body)).hexdigest()
            if entry.get("chain") != chain:
                _fail("history")
            previous = entry["chain"]
            output = entry.get("output_dir")
            if output is not None:
                if output_verifier is None or session_root is None:
                    _fail("v1-output-verifier-required")
                root = Path(session_root)
                path = root / output
                if path.parent != root / "actions":
                    _fail("history")
                proof = output_verifier(str(path))
                if (
                    type(proof) is not dict
                    or set(proof) != {"is_directory", "is_symlink", "hashes"}
                    or proof["is_directory"] is not True
                    or proof["is_symlink"] is not False
                    or proof["hashes"] != entry.get("output_sha256")
                ):
                    _fail("history")
        for record in value.get("in_flight", []):
            if (
                not isinstance(record, dict)
                or set(record)
                != {"action", "container_name", "output_dir", "started_at", "after_seq"}
                or record["action"] not in V1_MUTATING
                or not isinstance(record["after_seq"], int)
                or not 0 <= record["after_seq"] <= len(value.get("history", []))
            ):
                _fail("history")
        starts = _v1_coherent(value, "start-official")
        bound = starts[-1]["run_id"] if starts else None
        attached = starts[-1]["output_sha256"].get("preflight.json") if starts else None
        if (
            value.get("official_run_id") != bound
            or value.get("preflight_report_sha256") != attached
        ):
            _fail("history")
        if value.get("state") != _v1_state(value):
            _fail("state")
    except OfficialSessionError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, UnicodeError, RecursionError, OSError):
        _fail("v1-malformed")


@dataclass(frozen=True, slots=True)
class OfficialSessionV1:
    _bytes: bytes = field(repr=False)
    _repo_root: str = field(repr=False)
    _session_root: str | None = field(default=None, repr=False)
    _output_verifier: Callable | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        _validate_v1(
            _json(self._bytes, unique=False),
            self._repo_root,
            self._session_root,
            self._output_verifier,
        )

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._bytes)

    @property
    def original_bytes(self) -> bytes:
        return self._bytes


def load_session_v1(
    data: bytes,
    *,
    repo_root: str,
    session_root: str | None = None,
    output_verifier: Callable[[str], Mapping[str, Any]] | None = None,
) -> OfficialSessionV1:
    return OfficialSessionV1(data, repo_root, session_root, output_verifier)


def dispatch_session_by_schema(
    data: bytes,
    *,
    profile: rp.RuntimeProfile | Mapping[str, Any] | None = None,
    expected_tip: str | None = None,
    expected_count: int | None = None,
    repo_root: str | None = None,
    session_root: str | None = None,
    output_verifier: Callable[[str], Mapping[str, Any]] | None = None,
) -> OfficialSessionV1 | OfficialSessionV2:
    value = _json(data, unique=False)
    schema = value.get("schema_version")
    if schema == SESSION_V1_SCHEMA:
        if repo_root is None:
            _fail("v1-repository")
        return load_session_v1(
            data, repo_root=repo_root, session_root=session_root, output_verifier=output_verifier
        )
    if schema == SESSION_V2_SCHEMA:
        return load_session_v2(
            data, profile=profile, expected_tip=expected_tip, expected_count=expected_count
        )
    _fail("schema")
    raise AssertionError("unreachable")


def _parent(
    path: str | Path, context: Mapping[str, Any], *, mutate: bool
) -> tuple[int, str, tuple]:
    path = _b1(rp._absolute, str(path))
    _keys(
        context,
        {
            "parent",
            "owner_uid",
            "owner_gid",
            "repo_root",
            "historical_roots",
            "prior_session_roots",
        },
        "path",
    )
    parent, name = str(Path(path).parent), Path(path).name
    if parent != _b1(rp._absolute, context["parent"]):
        _fail("path")
    _b1(rp._integer, context["owner_uid"])
    _b1(rp._integer, context["owner_gid"])
    repo = _b1(rp._absolute, context["repo_root"])
    excluded = []
    for key in ("historical_roots", "prior_session_roots"):
        if type(context[key]) not in {list, tuple}:
            _fail("path")
        excluded.extend(_b1(rp._absolute, item) for item in context[key])
    if rp._overlap(parent, repo) or any(rp._overlap(parent, other) for other in excluded):
        _fail("path")
    if mutate and (parent == "/tmp" or parent.startswith("/tmp/")):
        _fail("path")
    fd, components = _b1(rp._walk, parent, "directory")
    try:
        info = os.fstat(fd)
        if (info.st_uid, info.st_gid) != (context["owner_uid"], context["owner_gid"]):
            _fail("owner")
        if stat.S_IMODE(info.st_mode) != 0o700:
            _fail("mode")
        _parent_current(fd, components, context)
        return fd, name, components
    except BaseException:
        os.close(fd)
        raise


def _parent_current(fd: int, components: tuple, context: Mapping[str, Any]) -> None:
    for path, device, inode in components:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode):
            _fail("symlink")
        if (info.st_dev, info.st_ino) != (device, inode):
            _fail("path")
    current = os.fstat(fd)
    if (current.st_dev, current.st_ino) != components[-1][1:]:
        _fail("path")
    if (current.st_uid, current.st_gid) != (context["owner_uid"], context["owner_gid"]):
        _fail("owner")
    if stat.S_IMODE(current.st_mode) != 0o700:
        _fail("mode")


def _read_at(parent_fd: int, name: str, context: Mapping[str, Any]) -> tuple[bytes, os.stat_result]:
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if stat.S_ISLNK(before.st_mode):
        _fail("symlink")
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        _fail("path")
    if (before.st_uid, before.st_gid) != (context["owner_uid"], context["owner_gid"]):
        _fail("owner")
    if stat.S_IMODE(before.st_mode) != 0o600:
        _fail("mode")
    fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK, dir_fd=parent_fd)
    try:
        actual = os.fstat(fd)
        signature = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(getattr(actual, key) != getattr(before, key) for key in signature):
            _fail("path")
        data = bytearray()
        while len(data) <= MAX_SESSION_BYTES:
            part = os.read(fd, min(65536, MAX_SESSION_BYTES + 1 - len(data)))
            if not part:
                break
            data.extend(part)
        after = os.fstat(fd)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)

        if any(
            getattr(actual, key) != getattr(after, key)
            or getattr(after, key) != getattr(named, key)
            for key in signature
        ):
            _fail("path")
        return bytes(data), after
    finally:
        os.close(fd)


def load_official_session(
    path: str | Path,
    *,
    context: Mapping[str, Any],
    profile: rp.RuntimeProfile | Mapping[str, Any] | None = None,
    expected_tip: str | None = None,
    expected_count: int | None = None,
    output_verifier: Callable[[str], Mapping[str, Any]] | None = None,
) -> OfficialSessionV1 | OfficialSessionV2:
    fd = None
    try:
        fd, name, components = _parent(path, context, mutate=False)
        data, _ = _read_at(fd, name, context)
        _parent_current(fd, components, context)
        return dispatch_session_by_schema(
            data,
            profile=profile,
            expected_tip=expected_tip,
            expected_count=expected_count,
            repo_root=context["repo_root"],
            session_root=context["parent"],
            output_verifier=output_verifier,
        )
    except OfficialSessionError:
        raise
    except OSError:
        _fail("io")
    finally:
        if fd is not None:
            os.close(fd)
    raise AssertionError("unreachable")


def _write_at(fd: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            _fail("io")
        view = view[written:]
    os.fsync(fd)


def create_official_session_v2(
    path: str | Path,
    header: Mapping[str, Any],
    *,
    profile: rp.RuntimeProfile | Mapping[str, Any],
    context: Mapping[str, Any],
) -> OfficialSessionV2:
    session = new_official_session_v2(header, profile=profile)
    if type(context) is not dict or context.get("repo_root") != header["repo_root"]:
        _fail("path")
    parent_fd = file_fd = None
    created = False
    try:
        parent_fd, name, components = _parent(path, context, mutate=True)
        try:
            existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            _fail("symlink" if stat.S_ISLNK(existing.st_mode) else "exists")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        file_fd = os.open(name, flags, 0o600, dir_fd=parent_fd)
        created = True
        os.fchmod(file_fd, 0o600)
        _write_at(file_fd, session._bytes)
        _parent_current(parent_fd, components, context)
        os.fsync(parent_fd)
        return session
    except OfficialSessionError:
        raise
    except OSError:
        _fail("write-uncertain" if created else "io")
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if parent_fd is not None:
            os.close(parent_fd)
    raise AssertionError("unreachable")


def update_official_session_v2(
    path: str | Path,
    expected: OfficialSessionV2,
    replacement: OfficialSessionV2,
    *,
    context: Mapping[str, Any],
    profile: rp.RuntimeProfile | Mapping[str, Any] | None = None,
    caller_serialized: bool,
) -> OfficialSessionV2:
    """Atomic replacement, requiring caller serialization and controlled namespace.

    The boolean is an explicit caller precondition, not a coordination certificate.
    B4 must supply real fencing. This writer does not implement a global lock or CAS.
    A post-replace directory-fsync failure is uncertain and must never be retried.
    """
    if caller_serialized is not True:
        _fail("writer-serialization-required")
    expected = verify_official_session_v2(_require_v2(expected), profile=profile)
    replacement = verify_official_session_v2(_require_v2(replacement), profile=profile)
    if canonical_session_header_bytes(expected.header) != canonical_session_header_bytes(
        replacement.header
    ):
        _fail("header-update")
    old = expected.to_dict()
    new = replacement.to_dict()
    if new["history"][: len(old["history"])] != old["history"] or len(new["history"]) < len(
        old["history"]
    ):
        _fail("history")
    if type(context) is not dict or context.get("repo_root") != expected.header["repo_root"]:
        _fail("path")
    parent_fd = temp_fd = None
    replaced = False
    try:
        parent_fd, name, components = _parent(path, context, mutate=True)
        original, identity = _read_at(parent_fd, name, context)
        if original != expected._bytes:
            _fail("stale-record")
        temporary = "." + name + "." + secrets.token_hex(12) + ".tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        temp_fd = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
        os.fchmod(temp_fd, 0o600)
        _write_at(temp_fd, replacement._bytes)
        _parent_current(parent_fd, components, context)
        current, observed = _read_at(parent_fd, name, context)
        if current != original or (identity.st_dev, identity.st_ino) != (
            observed.st_dev,
            observed.st_ino,
        ):
            _fail("stale-record")
        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        replaced = True
        os.fsync(parent_fd)
        return replacement
    except OfficialSessionError:
        raise
    except OSError:
        _fail("write-uncertain" if replaced else "io")
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        if parent_fd is not None:
            os.close(parent_fd)
    raise AssertionError("unreachable")
