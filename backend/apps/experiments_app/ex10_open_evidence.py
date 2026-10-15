"""B4 OPEN_EVIDENCE codec and qualification of four confined initial artifacts.

No receipt supplies trust, filesystem authority or an executor key. The host
supplies an independently qualified context, key and bounded resolver. Importing
this module performs no I/O and does not import B3, Django or the executor.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import ex10_load
from .manifest import ManifestError, RunManifest, make_run_id, validate_run_manifest

_SURROGATE_CODEPOINT_RE = re.compile(r"[\ud800-\udfff]")

SCHEMA_VERSION = "s11-ex10-b4-open-evidence-v1"
MAX_RECEIPT_BYTES = 262144
MAX_ARTIFACT_BYTES = 4194304
SAFE_INTEGER_MAX = 9007199254740991
REFERENCE_FIELDS = (
    "manifest_initial_reference",
    "start_reference",
    "preflight_reference",
    "plan_reference",
)
FIELDS = frozenset(
    {
        "schema_version",
        "reserve_request_id",
        "open_permit_digest",
        "reservation_id",
        "fencing_token",
        "run_id",
        "identity_digest",
        "runtime_profile_sha256",
        "session_header_sha256",
        *REFERENCE_FIELDS,
        "opened_at",
        "receipt_digest",
        "executor_signature",
    }
)
ARTIFACT_SCHEMAS = dict(
    zip(
        REFERENCE_FIELDS,
        (
            "s11-run-manifest-v1",
            "s11c3-ex10-start-v1",
            "s11c3-ex10-preflight-v1",
            "s11-ex10-load-plan-v1",
        ),
        strict=True,
    )
)
ARTIFACT_FIELDS = {
    "manifest_initial_reference": frozenset(
        {
            "schema_version",
            "methodology_version",
            "run_id",
            "git_sha",
            "git_clean",
            "started_at",
            "finished_at",
            "environment",
            "timezone",
            "seed",
            "dataset_id",
            "dataset_sha256",
            "scenario_id",
            "experiment_id",
            "status",
            "configuration",
            "runtime",
            "artifact_references",
            "invalidity_reason",
        }
    ),
    "start_reference": frozenset(
        {
            "schema_version",
            "scenario_id",
            "run_id",
            "recorded_at",
            "evidence_mode",
            "git_sha",
            "plan_sha256",
            "dataset_sha256",
            "preflight",
            "executor_sources",
            "executor_fingerprint",
            "prior_ex10_runs",
            "acknowledged_not_demonstrated",
        }
    ),
    "preflight_reference": frozenset(
        {
            "schema_version",
            "protocol_version",
            "methodology_version",
            "scenario_id",
            "result",
            "gate",
            "ready_for_execution",
            "run_id",
            "evidence_written",
            "report_posts",
            "provider_calls",
            "task_publishes",
            "health_requests",
            "control_probes",
            "expected_git_sha",
            "git_sha",
            "checked_at",
            "checks",
        }
    ),
    "plan_reference": frozenset(
        {
            "schema_version",
            "protocol_id",
            "methodology_version",
            "scenario_id",
            "dataset_id",
            "dataset_sha256",
            "virtual_users",
            "requests_per_user",
            "total_requests",
            "items",
        }
    ),
}
_HEX = re.compile(r"[0-9a-f]{64}")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
_RUN = re.compile(r"s11-[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z")


class OpenEvidenceError(ValueError):
    """Only a fixed error code crosses the bridge; no paths or payload values."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise OpenEvidenceError(code)


def _tree(value: Any, depth: int = 0) -> None:
    if depth > 64:
        _fail("EVD_PARSE_INVALID")
    if type(value) is str:
        if not value.isascii() and (
            unicodedata.normalize("NFC", value) != value
            or _SURROGATE_CODEPOINT_RE.search(value) is not None
        ):
            _fail("EVD_PARSE_INVALID")
    elif value is None or type(value) is bool:
        return
    elif type(value) is int:
        if abs(value) > SAFE_INTEGER_MAX:
            _fail("EVD_PARSE_INVALID")
    elif type(value) in (dict, list):
        if type(value) is dict:
            for key in value:
                if type(key) is not str:
                    _fail("EVD_PARSE_INVALID")
                _tree(key, depth + 1)
            values = value.values()
        else:
            values = value
        for child in values:
            _tree(child, depth + 1)
    else:
        _fail("EVD_PARSE_INVALID")


def canonical_bytes(value: Any) -> bytes:
    """Reject unsupported/non-NFC values rather than repairing signed input."""
    if type(value) is str and value.isascii():
        return (json.encoder.encode_basestring(value) + "\n").encode("utf-8")
    _tree(value)
    return (
        json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode("utf-8")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("EVD_PARSE_INVALID")
        result[key] = value
    return result


def parse_canonical_bytes(raw: bytes, *, maximum: int = MAX_RECEIPT_BYTES) -> dict[str, Any]:
    if type(raw) is not bytes or not 0 < len(raw) <= maximum:
        _fail("EVD_PARSE_INVALID")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _: _fail("EVD_PARSE_INVALID"),
        )
        if type(value) is not dict or canonical_bytes(value) != raw:
            _fail("EVD_PARSE_INVALID")
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _fail("EVD_PARSE_INVALID")
    return value


def utc_instant(value: str) -> datetime:
    if type(value) is not str or _TIME.fullmatch(value) is None:
        _fail("EVD_PARSE_INVALID")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("EVD_PARSE_INVALID")


def _string(value: Any, pattern: re.Pattern[str], maximum: int) -> None:
    if type(value) is not str or len(value) > maximum or pattern.fullmatch(value) is None:
        _fail("EVD_PARSE_INVALID")


@dataclass(frozen=True, slots=True)
class OpenEvidence:
    canonical: bytes

    def to_dict(self) -> dict[str, Any]:
        # Return a detached value; callers cannot mutate the verified preimage.
        return json.loads(self.canonical)


def parse_open_evidence_bytes(raw: bytes) -> OpenEvidence:
    value = parse_canonical_bytes(raw)
    if set(value) != FIELDS or value["schema_version"] != SCHEMA_VERSION:
        _fail("EVD_PARSE_INVALID")
    for field in ("reserve_request_id", "reservation_id"):
        _string(value[field], _UUID, 36)
    _string(value["run_id"], _RUN, 40)
    for field in (
        "open_permit_digest",
        "identity_digest",
        "runtime_profile_sha256",
        "session_header_sha256",
        "receipt_digest",
    ):
        _string(value[field], _HEX, 64)
    _string(value["executor_signature"], re.compile(r"[0-9a-f]{128}"), 128)
    if (
        type(value["fencing_token"]) is not int
        or not 1 <= value["fencing_token"] <= SAFE_INTEGER_MAX
    ):
        _fail("EVD_PARSE_INVALID")
    utc_instant(value["opened_at"])
    for field in REFERENCE_FIELDS:
        ref = value[field]
        if type(ref) is not dict or set(ref) != {"path", "sha256", "schema_version"}:
            _fail("EVD_PARSE_INVALID")
        if type(ref["path"]) is not str or not 1 <= len(ref["path"]) <= 4096:
            _fail("EVD_PARSE_INVALID")
        _string(ref["sha256"], _HEX, 64)
        _string(ref["schema_version"], re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}"), 128)
    return OpenEvidence(raw)


def receipt_digest(value: OpenEvidence) -> str:
    checked = parse_open_evidence_bytes(value.canonical).to_dict()
    return hashlib.sha256(
        canonical_bytes(
            {k: v for k, v in checked.items() if k not in {"receipt_digest", "executor_signature"}}
        )
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ArtifactExpectation:
    """Host-selected locator, byte bound and additional independent field bindings."""

    field: str
    path: str
    max_bytes: int
    bindings: tuple[tuple[tuple[str, ...], Any], ...] = ()


@dataclass(frozen=True, slots=True)
class OpenEvidenceContext:
    reserve_request_id: str
    reservation_id: str
    fencing_token: int
    open_permit_digest: str
    runtime_profile_sha256: str
    session_header_sha256: str
    run_identity: bytes
    not_before: datetime
    deadline: datetime
    now: datetime
    executor_fingerprint: str
    preflight_required_checks: frozenset[str]
    artifacts: tuple[ArtifactExpectation, ...]


@dataclass(frozen=True, slots=True)
class QualifiedArtifact:
    field: str
    canonical: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class QualifiedOpenEvidence:
    receipt: OpenEvidence
    receipt_digest: str
    sha256: str
    artifacts: tuple[QualifiedArtifact, ...]


class ArtifactResolver(Protocol):
    def read_artifacts(
        self, expectations: tuple[ArtifactExpectation, ...], references: Mapping[str, Any]
    ) -> Mapping[str, bytes]: ...


def _safe_path(path: str, *, absolute: bool) -> tuple[str, ...]:
    if type(path) is not str or not path or "\x00" in path or "\\" in path:
        _fail("EVD_PATH_UNSAFE")
    parts = path.split("/")
    if absolute:
        if parts.pop(0) != "":
            _fail("EVD_PATH_UNSAFE")
    elif path.startswith("/"):
        _fail("EVD_PATH_UNSAFE")
    if any(part in {"", ".", ".."} for part in parts):
        _fail("EVD_PATH_UNSAFE")
    return tuple(parts)


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_gid,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


class ConfinedArtifactResolver:
    """Read-only POSIX root lease. The root is host-selected, never receipt-selected.

    Every ancestor is opened with NOFOLLOW and retained until lease closure.
    Private root/descendant directories and files retain exact owner and modes;
    inode/metadata checks before and after reads detect replacement or mutation.
    No file creation, network access, or pathname fallback is available.
    """

    def __init__(self, root: str, *, expected_owner: int):
        self.root = root
        self.owner = expected_owner
        self._fds: list[int] = []
        self._links: list[tuple[int, str, int]] = []
        self._closed = False
        try:
            parts = _safe_path(root, absolute=True)
            fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            self._fds.append(fd)
            for part in parts:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd
                )
                self._fds.append(child)
                self._links.append((fd, part, child))
                fd = child
            self._root_fd = fd
            self._private_directory(os.fstat(fd))
            self._check_lease()
        except (OSError, OpenEvidenceError):
            self.close()
            _fail("EVD_PATH_UNSAFE")

    def _private_directory(self, info: os.stat_result) -> None:
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != self.owner
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            _fail("EVD_PATH_UNSAFE")

    def _check_lease(self) -> None:
        if self._closed:
            _fail("EVD_IDENTITY_CHANGED")
        try:
            for parent, name, child in self._links:
                if _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _identity(
                    os.fstat(child)
                ):
                    _fail("EVD_IDENTITY_CHANGED")
            self._private_directory(os.fstat(self._root_fd))
        except OSError:
            _fail("EVD_IDENTITY_CHANGED")

    def _read(
        self, path: str, bound: int, stack: ExitStack, guards: list
    ) -> tuple[bytes, tuple[int, int]]:
        parts = _safe_path(path, absolute=path.startswith("/"))
        if path.startswith("/"):
            base = _safe_path(self.root, absolute=True)
            if parts[: len(base)] != base:
                _fail("EVD_PATH_UNSAFE")
            parts = parts[len(base) :]
        if not parts:
            _fail("EVD_PATH_UNSAFE")
        checks = []
        fd = self._root_fd
        try:
            for part in parts[:-1]:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd
                )
                stack.callback(os.close, child)
                checks.append((fd, part, child))
                self._private_directory(os.fstat(child))
                fd = child
            file_fd = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd
            )
            stack.callback(os.close, file_fd)
            before = os.fstat(file_fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != self.owner
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
            ):
                _fail("EVD_PATH_UNSAFE")
            if not 0 < before.st_size <= bound:
                _fail("EVD_REFERENCE_INVALID")
            chunks = []
            length = 0
            while length <= bound:
                chunk = os.read(file_fd, min(65536, bound + 1 - length))
                if not chunk:
                    break
                chunks.append(chunk)
                length += len(chunk)
            if length != before.st_size or _stamp(os.fstat(file_fd)) != _stamp(before):
                _fail("EVD_IDENTITY_CHANGED")
            if _stamp(os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)) != _stamp(before):
                _fail("EVD_IDENTITY_CHANGED")
            for parent, name, child in checks:
                info = os.fstat(child)
                self._private_directory(info)
                if _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _identity(
                    info
                ):
                    _fail("EVD_IDENTITY_CHANGED")
            guards.extend(
                (parent, name, child, _stamp(os.fstat(child))) for parent, name, child in checks
            )
            guards.append((fd, parts[-1], file_fd, _stamp(before)))
            return b"".join(chunks), _identity(before)
        except OSError:
            _fail("EVD_PATH_UNSAFE")

    def read_artifacts(self, expectations, references):
        self._check_lease()
        result, seen, guards = {}, set(), []
        with ExitStack() as stack:
            for spec in expectations:
                if references[spec.field]["path"] != spec.path:
                    _fail("EVD_PATH_UNSAFE")
                raw, inode = self._read(spec.path, spec.max_bytes, stack, guards)
                if inode in seen:
                    _fail("EVD_PATH_UNSAFE")
                seen.add(inode)
                result[spec.field] = raw
            self._check_lease()
            try:
                for parent, name, child, expected in guards:
                    if (
                        _stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)) != expected
                        or _stamp(os.fstat(child)) != expected
                    ):
                        _fail("EVD_IDENTITY_CHANGED")
            except OSError:
                _fail("EVD_IDENTITY_CHANGED")
            return result

    def close(self):
        for fd in reversed(self._fds):
            os.close(fd)
        self._fds.clear()
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _equal(actual: Any, expected: Any, code: str = "EVD_REFERENCE_INVALID") -> None:
    if type(actual) is not type(expected) or canonical_bytes(actual) != canonical_bytes(expected):
        _fail(code)


def _bind_artifacts(artifacts: dict[str, dict[str, Any]], value, identity, context) -> None:
    manifest, start, preflight, plan = (artifacts[k] for k in REFERENCE_FIELDS)
    for key, expected in {
        "run_id": identity["run_id"],
        "git_sha": identity["git_sha"],
        "started_at": identity["started_at"],
        "seed": identity["seed"],
        "dataset_id": identity["dataset_id"],
        "scenario_id": "EX-10",
        "git_clean": True,
        "status": "PREPARED",
        "finished_at": None,
        "invalidity_reason": None,
        "artifact_references": [],
        "methodology_version": ex10_load.METHODOLOGY_VERSION,
        "dataset_sha256": ex10_load.DATASET_SHA256,
        "experiment_id": "S11C3-EX10",
        "environment": "isolated-experiment",
    }.items():
        _equal(manifest.get(key), expected)
    try:
        validate_run_manifest(RunManifest(**{**manifest, "artifact_references": ()}))
    except (ManifestError, TypeError, AttributeError):
        _fail("EVD_REFERENCE_INVALID")
    cfg = manifest["configuration"]
    if type(cfg) is not dict:
        _fail("EVD_REFERENCE_INVALID")
    for key, expected in {
        "git_sha": identity["git_sha"],
        "evidence_mode": "OFFICIAL",
        "experiment_id": "S11C3-EX10",
        "plan_sha256": ex10_load.PLAN_SHA256,
        "executor_fingerprint": context.executor_fingerprint,
    }.items():
        _equal(cfg.get(key), expected)
    _equal(cfg.get("protocol"), ex10_load.protocol_identity())
    for key, expected in {
        "run_id": identity["run_id"],
        "scenario_id": "EX-10",
        "recorded_at": identity["started_at"],
        "git_sha": identity["git_sha"],
        "plan_sha256": value["plan_reference"]["sha256"],
        "dataset_sha256": ex10_load.DATASET_SHA256,
        "evidence_mode": "OFFICIAL",
        "executor_fingerprint": context.executor_fingerprint,
    }.items():
        _equal(start.get(key), expected)
    sources = start["executor_sources"]
    if (
        type(sources) is not dict
        or set(sources) != {"files", "match"}
        or type(sources["files"]) is not int
        or sources["files"] < 1
    ):
        _fail("EVD_REFERENCE_INVALID")
    _equal(sources["match"], True)
    summary = start["preflight"]
    if type(summary) is not dict or set(summary) != {
        "sha256",
        "checked_at",
        "result",
        "gate",
        "checks",
    }:
        _fail("EVD_REFERENCE_INVALID")
    prior, acknowledgments = start["prior_ex10_runs"], start["acknowledged_not_demonstrated"]
    if type(prior) is not list or type(acknowledgments) is not list:
        _fail("EVD_REFERENCE_INVALID")
    run_ids = set()
    for row in prior:
        if (
            type(row) is not dict
            or set(row) != {"run_id", "evidence_mode", "finalized", "decision", "problems"}
            or type(row["run_id"]) is not str
            or _RUN.fullmatch(row["run_id"]) is None
            or row["run_id"] in run_ids
            or type(row["finalized"]) is not bool
            or row["evidence_mode"] not in {None, "OFFICIAL", "LOCAL_TEST"}
            or row["decision"] not in {None, "PASS", "FAIL", "NOT_DEMONSTRATED"}
            or type(row["problems"]) is not list
            or any(type(p) is not str for p in row["problems"])
        ):
            _fail("EVD_REFERENCE_INVALID")
        run_ids.add(row["run_id"])
    if any(
        type(run) is not str or _RUN.fullmatch(run) is None for run in acknowledgments
    ) or acknowledgments != sorted(set(acknowledgments)):
        _fail("EVD_REFERENCE_INVALID")
    for key, expected in {
        "scenario_id": "EX-10",
        "protocol_version": ex10_load.PROTOCOL_ID,
        "methodology_version": ex10_load.METHODOLOGY_VERSION,
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
        "git_sha": identity["git_sha"],
        "expected_git_sha": identity["git_sha"],
    }.items():
        _equal(preflight.get(key), expected)
    checks = preflight["checks"]
    if (
        type(checks) is not list
        or not checks
        or any(
            type(c) is not dict
            or set(c) != {"check", "result", "expected", "observed_sanitized", "detail_code"}
            or c.get("result") != "PASS"
            or type(c.get("check")) is not str
            for c in checks
        )
    ):
        _fail("EVD_REFERENCE_INVALID")
    names = [c["check"] for c in checks]
    if (
        len(set(names)) != len(names)
        or not context.preflight_required_checks <= set(names)
        or not any(n.startswith("ready_state:") for n in names)
    ):
        _fail("EVD_REFERENCE_INVALID")
    try:
        checked = datetime.fromisoformat(preflight["checked_at"].replace("Z", "+00:00"))
        if checked.tzinfo is None or checked.utcoffset() != UTC.utcoffset(checked):
            _fail("EVD_REFERENCE_INVALID")
        prepared = utc_instant(identity["started_at"])
        opened = utc_instant(value["opened_at"])
        if not checked <= prepared <= opened <= context.now:
            _fail("EVD_REFERENCE_INVALID")
        if not 0 <= (context.now - checked).total_seconds() <= 900:
            _fail("EVD_REFERENCE_INVALID")
    except (AttributeError, TypeError, ValueError):
        _fail("EVD_REFERENCE_INVALID")
    for key, expected in {
        "sha256": value["preflight_reference"]["sha256"],
        "checked_at": preflight["checked_at"],
        "result": preflight["result"],
        "gate": preflight["gate"],
        "checks": len(checks),
    }.items():
        _equal(start["preflight"].get(key), expected)
    _equal(value["plan_reference"]["sha256"], ex10_load.PLAN_SHA256)
    for key, expected in {
        "protocol_id": ex10_load.PROTOCOL_ID,
        "scenario_id": "EX-10",
        "methodology_version": ex10_load.METHODOLOGY_VERSION,
        "dataset_id": ex10_load.DATASET_ID,
        "dataset_sha256": ex10_load.DATASET_SHA256,
        "virtual_users": 10,
        "requests_per_user": 5,
        "total_requests": 50,
    }.items():
        _equal(plan.get(key), expected)
    if type(plan["items"]) is not list or len(plan["items"]) != 50:
        _fail("EVD_REFERENCE_INVALID")


def _context_identity(context: OpenEvidenceContext) -> dict[str, Any]:
    if type(context) is not OpenEvidenceContext:
        _fail("EVD_CONTEXT_MISMATCH")
    try:
        identity = parse_canonical_bytes(context.run_identity)
        if set(identity) != {
            "started_at",
            "git_sha",
            "dataset_id",
            "scenario_id",
            "seed",
            "execution_nonce",
            "run_id",
            "identity_digest",
        }:
            _fail("EVD_CONTEXT_MISMATCH")
        started = utc_instant(identity["started_at"])
        _string(identity["git_sha"], re.compile(r"[0-9a-f]{40}"), 40)
        _string(identity["execution_nonce"], _HEX, 64)
        _string(identity["run_id"], _RUN, 40)
        _string(identity["identity_digest"], _HEX, 64)
        _equal(identity["dataset_id"], "s11-load-v1", "EVD_CONTEXT_MISMATCH")
        _equal(identity["scenario_id"], "EX-10", "EVD_CONTEXT_MISMATCH")
        _equal(identity["seed"], 20260921, "EVD_CONTEXT_MISMATCH")
        run = make_run_id(
            started_at=started,
            git_sha=identity["git_sha"],
            dataset_id=identity["dataset_id"],
            scenario_id=identity["scenario_id"],
            seed=identity["seed"],
            execution_nonce=identity["execution_nonce"],
        )
        _equal(identity["run_id"], run, "EVD_CONTEXT_MISMATCH")
        digest = hashlib.sha256(
            canonical_bytes({k: v for k, v in identity.items() if k != "identity_digest"})
        ).hexdigest()
        _equal(identity["identity_digest"], digest, "EVD_CONTEXT_MISMATCH")
        for instant in (context.now, context.not_before, context.deadline):
            if (
                type(instant) is not datetime
                or instant.tzinfo is None
                or instant.utcoffset() != UTC.utcoffset(instant)
            ):
                _fail("EVD_CONTEXT_MISMATCH")
        if not started <= context.not_before <= context.now <= context.deadline:
            _fail("EVD_CONTEXT_MISMATCH")
        if (
            type(context.artifacts) is not tuple
            or len(context.artifacts) != 4
            or {s.field for s in context.artifacts} != set(REFERENCE_FIELDS)
            or type(context.preflight_required_checks) is not frozenset
            or not context.preflight_required_checks
        ):
            _fail("EVD_CONTEXT_MISMATCH")
        _string(context.executor_fingerprint, _HEX, 64)
        for spec in context.artifacts:
            if (
                type(spec) is not ArtifactExpectation
                or type(spec.max_bytes) is not int
                or not 0 < spec.max_bytes <= MAX_ARTIFACT_BYTES
            ):
                _fail("EVD_CONTEXT_MISMATCH")
            _safe_path(spec.path, absolute=spec.path.startswith("/"))
        return identity
    except (TypeError, AttributeError, KeyError):
        _fail("EVD_CONTEXT_MISMATCH")


def verify_open_evidence(
    raw: bytes,
    *,
    expected_context: OpenEvidenceContext,
    executor_public_key: Ed25519PublicKey,
    artifact_resolver: ArtifactResolver,
) -> QualifiedOpenEvidence:
    receipt = parse_open_evidence_bytes(raw)
    value = receipt.to_dict()
    digest = receipt_digest(receipt)
    if digest != value["receipt_digest"]:
        _fail("EVD_DIGEST_MISMATCH")
    if not isinstance(executor_public_key, Ed25519PublicKey):
        _fail("EVD_POP_INVALID")
    try:
        executor_public_key.verify(
            bytes.fromhex(value["executor_signature"]),
            canonical_bytes({k: v for k, v in value.items() if k != "executor_signature"}),
        )
    except (InvalidSignature, ValueError):
        _fail("EVD_POP_INVALID")
    context = expected_context
    identity = _context_identity(context)
    for field in (
        "reserve_request_id",
        "reservation_id",
        "fencing_token",
        "open_permit_digest",
        "runtime_profile_sha256",
        "session_header_sha256",
    ):
        _equal(value[field], getattr(context, field), "EVD_CONTEXT_MISMATCH")
    for field in ("run_id", "identity_digest"):
        _equal(value[field], identity[field], "EVD_CONTEXT_MISMATCH")
    if not context.not_before <= utc_instant(value["opened_at"]) <= context.now:
        _fail("EVD_CONTEXT_MISMATCH")
    for spec in context.artifacts:
        ref = value[spec.field]
        _safe_path(ref["path"], absolute=ref["path"].startswith("/"))
        _equal(ref["path"], spec.path, "EVD_PATH_UNSAFE")
        _equal(ref["schema_version"], ARTIFACT_SCHEMAS[spec.field])
    try:
        resolved = artifact_resolver.read_artifacts(context.artifacts, value)
    except OSError:
        _fail("EVD_REFERENCE_INVALID")
    if type(resolved) is not dict or set(resolved) != set(REFERENCE_FIELDS):
        _fail("EVD_REFERENCE_INVALID")
    qualified, payloads = [], {}
    for spec in context.artifacts:
        content = resolved[spec.field]
        if type(content) is not bytes or not 0 < len(content) <= spec.max_bytes:
            _fail("EVD_REFERENCE_INVALID")
        actual_sha = hashlib.sha256(content).hexdigest()
        _equal(actual_sha, value[spec.field]["sha256"])
        artifact = parse_canonical_bytes(content, maximum=spec.max_bytes)
        if set(artifact) != ARTIFACT_FIELDS[spec.field]:
            _fail("EVD_REFERENCE_INVALID")
        _equal(artifact["schema_version"], ARTIFACT_SCHEMAS[spec.field])
        for path, expected in spec.bindings:
            try:
                observed = artifact
                for part in path:
                    observed = observed[part]
                _equal(observed, expected)
            except (KeyError, TypeError):
                _fail("EVD_REFERENCE_INVALID")
        payloads[spec.field] = artifact
        qualified.append(QualifiedArtifact(spec.field, content, actual_sha))
    try:
        _bind_artifacts(payloads, value, identity, context)
    except (AttributeError, KeyError, TypeError):
        _fail("EVD_REFERENCE_INVALID")
    return QualifiedOpenEvidence(receipt, digest, hashlib.sha256(raw).hexdigest(), tuple(qualified))
