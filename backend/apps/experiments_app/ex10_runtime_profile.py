"""EX-10 B1: offline runtime-profile data and explicit filesystem validation.

This core verifies form, never approval authority, Git, history, or deployment.
Returned path identities are observations, not a lock or a Docker TOCTOU guarantee.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "s11-ex10-runtime-profile-v1"
MAX_PROFILE_BYTES = 1024 * 1024
NOFOLLOW = os.O_NOFOLLOW
PROJECT_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,62}$"
_PROJECT = re.compile(PROJECT_PATTERN, re.ASCII)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", re.ASCII)
_SCHEMA = re.compile(r"s11[a-z0-9_-]{1,127}", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_SHA1 = re.compile(r"[0-9a-f]{40}", re.ASCII)
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", re.ASCII)
_SECRET = re.compile(
    r"-----BEGIN .*PRIVATE KEY-----|tskey-|Bearer\s+|" r"(?:password|secret|token|api_key)\s*[:=]",
    re.IGNORECASE,
)
ROLES = ("evidence", "backup", "restore", "objects")
FIXED_TARGETS = dict(
    zip(ROLES, ("/s11-evidence", "/s11-backup", "/s11-restore", "/s11-objects"), strict=False)
)
REFERENCE_FIELDS = (
    "decision_reference",
    "deployment_verification_reference",
    "historical_lifecycle_authority_reference",
    "external_source_anchor_reference",
)
REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "profile_id",
        "approved_at",
        "decision_reference",
        "source_revision",
        "project",
        "compose_files",
        "override_path",
        "override_sha256",
        "source_host_root",
        "source_container_target",
        "source_read_only",
        "host_roots",
        "fixed_targets",
        "expected_owner",
        "approved_modes",
        "masks",
        "expected_services",
        "beat_policy",
        "deployment_verification_reference",
        "historical_lifecycle_authority_reference",
        "external_source_anchor_reference",
    }
)


class RuntimeProfileError(ValueError):
    """Fixed sanitized code; never echoes input keys, values, or paths."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise RuntimeProfileError(code)


def _keys(value: Any, required: set[str] | frozenset[str], code: str = "schema") -> None:
    if type(value) is not dict:
        _fail(code)
    keys = set(value)
    if keys - required:
        _fail("unknown-field")
    if required - keys:
        _fail(
            "approval-reference"
            if (required - keys) & (set(REFERENCE_FIELDS) | {"approved_at"})
            else code
        )


def _text(value: Any, code: str = "schema", maximum: int = 4096) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        _fail(code)
    if re.search(r"[\x00-\x1f\x7f]", value) is not None:
        _fail(code)
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        _fail(code)
    if _SECRET.search(value):
        _fail("schema")
    return value


def _match(value: Any, pattern: re.Pattern[str], code: str) -> None:
    if not pattern.fullmatch(_text(value, code)):
        _fail(code)


def _absolute(value: Any) -> str:
    value = _text(value, "path")
    if not value.startswith("/") or value.startswith("//") or value == "/":
        _fail("path")
    if any(char in value for char in ":,\\\x60$;&|<>'\""):
        _fail("path")
    if any(part in {"", ".", ".."} for part in value.split("/")[1:]):
        _fail("path")
    if os.path.normpath(value) != value:
        _fail("path")
    return value


def _relative(value: Any, code: str = "path") -> str:
    value = _text(value, code)
    if value.startswith("/") or any(char in value for char in ":,\\\x60$;&|<>'\""):
        _fail(code)
    if any(part in {"", ".", ".."} for part in value.split("/")):
        _fail(code)
    return value


def _integer(value: Any) -> None:
    if type(value) is not int or not 0 <= value < 2**32 - 1:
        _fail("owner")


def _hash(value: Any) -> None:
    _match(value, _SHA256, "hash")


def _reference(value: Any) -> None:
    _keys(value, {"path", "sha256", "schema_version"}, "approval-reference")
    _absolute(value["path"])
    _hash(value["sha256"])
    _match(value["schema_version"], _SCHEMA, "approval-reference")


def _tree(value: Any) -> None:
    if type(value) is dict:
        for key, item in value.items():
            _text(key)
            _tree(item)
    elif type(value) is list:
        for item in value:
            _tree(item)
    elif type(value) is str:
        _text(value)
    elif value is not None and type(value) not in {int, bool}:
        _fail("schema")


def _schema(value: Any) -> None:
    _keys(value, REQUIRED_FIELDS)
    _tree(value)
    if value["schema_version"] != SCHEMA_VERSION:
        _fail("schema")
    _match(value["profile_id"], _ID, "schema")
    _match(value["approved_at"], _TIMESTAMP, "approval-reference")
    try:
        datetime.strptime(value["approved_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        _fail("approval-reference")
    for name in REFERENCE_FIELDS:
        _reference(value[name])
    _match(value["source_revision"], _SHA1, "hash")
    _match(value["project"], _PROJECT, "project")
    source = _absolute(value["source_host_root"])
    override = _absolute(value["override_path"])
    _hash(value["override_sha256"])
    if value["source_container_target"] != "/s11-full-source":
        _fail("target")
    if value["source_read_only"] is not True:
        _fail("target")
    _keys(value["expected_owner"], {"uid", "gid"}, "owner")
    for number in value["expected_owner"].values():
        _integer(number)
    _keys(value["approved_modes"], {"directory", "artifact"}, "mode")
    if value["approved_modes"] != {"directory": "0700", "artifact": "0600"}:
        _fail("mode")
    _keys(value["host_roots"], set(ROLES), "path")
    for root in value["host_roots"].values():
        _keys(root, {"path", "owner_uid", "owner_gid", "directory_mode", "artifact_mode"}, "path")
        _absolute(root["path"])
        _integer(root["owner_uid"])
        _integer(root["owner_gid"])
        if (root["owner_uid"], root["owner_gid"]) != (
            value["expected_owner"]["uid"],
            value["expected_owner"]["gid"],
        ):
            _fail("owner")
        if root["directory_mode"] != "0700" or root["artifact_mode"] != "0600":
            _fail("mode")
    _keys(value["fixed_targets"], set(ROLES), "target")
    if value["fixed_targets"] != FIXED_TARGETS:
        _fail("target")
    if value["expected_services"] != ["db", "redis", "backend", "worker"]:
        _fail("service")
    if value["beat_policy"] != "disabled":
        _fail("service")
    compose = value["compose_files"]
    if type(compose) is not list or len(compose) != 3:
        _fail("schema")
    for index, entry in enumerate(compose):
        _keys(entry, {"kind", "path", "git_path", "git_blob", "sha256"})
        _absolute(entry["path"])
        _hash(entry["sha256"])
        if index < 2:
            git_path = ("infra/docker-compose.yml", "infra/docker-compose.s11.yml")[index]
            if entry["kind"] != "tracked" or entry["git_path"] != git_path:
                _fail("schema")
            if entry["path"] != source + "/" + git_path:
                _fail("path")
            _match(entry["git_blob"], _SHA1, "hash")
        else:
            if entry["kind"] != "external_override":
                _fail("schema")
            if entry["git_path"] is not None or entry["git_blob"] is not None:
                _fail("schema")
            if entry["path"] != override or entry["sha256"] != value["override_sha256"]:
                _fail("hash")
    masks = value["masks"]
    if type(masks) is not list or not 2 <= len(masks) <= 64:
        _fail("mask")
    seen: dict[str, str] = {}
    for mask in masks:
        _keys(mask, {"relative_path", "kind", "source_path", "read_only"}, "mask")
        target = _relative(mask["relative_path"], "mask")
        if target in seen:
            _fail("mask")
        if (
            type(mask["kind"]) is not str
            or mask["kind"] not in {"directory", "dev_null_file"}
            or mask["read_only"] is not True
        ):
            _fail("mask")
        _absolute(mask["source_path"])
        if mask["kind"] == "dev_null_file" and (
            mask["source_path"] != "/dev/null" or target != ".env"
        ):
            _fail("mask")
        seen[target] = mask["kind"]
    if seen.get("var") != "directory" or seen.get(".env") != "dev_null_file":
        _fail("mask")


def _dump(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail("schema")
    raise AssertionError("unreachable")


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate-key")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    _fail("schema")


def _parse(data: bytes) -> dict[str, Any]:
    if type(data) is not bytes or not data or len(data) > MAX_PROFILE_BYTES:
        _fail("schema")
    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant
        )
        _schema(value)
        if data != _dump(value):
            _fail("noncanonical")
        return value
    except RuntimeProfileError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("schema")
    raise AssertionError("unreachable")


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    """Immutable canonical bytes. Approval authority is deliberately unverified."""

    _bytes: bytes = field(repr=False)

    def __post_init__(self) -> None:
        _parse(self._bytes)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._bytes)


def parse_runtime_profile_bytes(
    data: bytes, *, context: Mapping[str, Any] | None = None
) -> RuntimeProfile:
    profile = RuntimeProfile(data)
    if context is not None:
        validate_runtime_profile_paths(profile, context)
    return profile


def validate_runtime_profile(
    profile: RuntimeProfile | Mapping[str, Any], context: Mapping[str, Any] | None = None
) -> RuntimeProfile:
    if isinstance(profile, RuntimeProfile):
        result = RuntimeProfile(profile._bytes)
    else:
        try:
            _schema(profile)
        except RecursionError:
            _fail("schema")
        result = RuntimeProfile(_dump(profile))
    if context is not None:
        validate_runtime_profile_paths(result, context)
    return result


def canonical_runtime_profile_bytes(profile: RuntimeProfile | Mapping[str, Any]) -> bytes:
    return validate_runtime_profile(profile)._bytes


def runtime_profile_sha256(profile: RuntimeProfile | Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_runtime_profile_bytes(profile)).hexdigest()


def _overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _outside(path: str, repo: str, excluded: Sequence[str]) -> None:
    if _overlap(path, repo) or path == "/tmp" or path.startswith("/tmp/"):
        _fail("path")
    if any(_overlap(path, other) for other in excluded):
        _fail("overlap")


def _walk(path: str, kind: str) -> tuple[int, tuple[tuple[str, int, int], ...]]:
    path = _absolute(path)
    try:
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW)
    except OSError:
        _fail("path")
    components: list[tuple[str, int, int]] = []
    current = ""
    try:
        parts = path.split("/")[1:]
        for index, part in enumerate(parts):
            current += "/" + part
            info = os.stat(part, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                _fail("symlink")
            flags = os.O_RDONLY | NOFOLLOW | os.O_NONBLOCK
            if index < len(parts) - 1 or kind == "directory":
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=fd)
            try:
                actual = os.fstat(child)
                if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
                    _fail("path")
            except BaseException:
                os.close(child)
                raise
            os.close(fd)
            fd = child
            components.append((current, actual.st_dev, actual.st_ino))
        if os.path.realpath(path) != path:
            _fail("path")
        if kind == "directory" and not stat.S_ISDIR(actual.st_mode):
            _fail("path")
        if kind == "file" and (not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1):
            _fail("path")
        if kind == "dev_null" and (
            not stat.S_ISCHR(actual.st_mode)
            or (os.major(actual.st_rdev), os.minor(actual.st_rdev)) != (1, 3)
        ):
            _fail("mask")
        return fd, tuple(components)
    except RuntimeProfileError:
        os.close(fd)
        raise
    except OSError as exc:
        os.close(fd)
        _fail("symlink" if exc.errno == errno.ELOOP else "path")
    raise AssertionError("unreachable")


def _private(
    path: str, kind: str, uid: int, gid: int, mode: int | None
) -> tuple[tuple[str, int, int], ...]:
    fd, components = _walk(path, kind)
    try:
        info = os.fstat(fd)
        if kind != "dev_null" and (info.st_uid, info.st_gid) != (uid, gid):
            _fail("owner")
        if mode is not None and stat.S_IMODE(info.st_mode) != mode:
            _fail("mode")
        for component, device, inode in components:
            check = os.lstat(component)
            if stat.S_ISLNK(check.st_mode):
                _fail("symlink")
            if (check.st_dev, check.st_ino) != (device, inode):
                _fail("path")
        return components
    except RuntimeProfileError:
        raise
    except OSError:
        _fail("path")
    finally:
        os.close(fd)
    raise AssertionError("unreachable")


def validate_runtime_profile_paths(
    profile: RuntimeProfile | Mapping[str, Any], context: Mapping[str, Any]
) -> tuple[tuple[str, int, int], ...]:
    """Explicit filesystem checks; exclusions are supplied, not history-certified.

    Context explicitly provides repo_root, historical_roots, prior_session_roots,
    running_sources, approved_reference_schemas (field -> allowed schema strings).
    The result is a component identity snapshot for later comparisons, not a lock.
    """
    value = validate_runtime_profile(profile).to_dict()
    _keys(
        context,
        {
            "repo_root",
            "historical_roots",
            "prior_session_roots",
            "running_sources",
            "approved_reference_schemas",
        },
    )
    repo = _absolute(context["repo_root"])
    if repo != value["source_host_root"]:
        _fail("path")
    excluded: list[str] = []
    for name in ("historical_roots", "prior_session_roots"):
        if type(context[name]) not in {tuple, list}:
            _fail("schema")
        excluded.extend(_absolute(path) for path in context[name])
    sources = context["running_sources"]
    if type(sources) not in {tuple, list}:
        _fail("mask")
    for source in sources:
        _relative(source, "mask")
    schemas = context["approved_reference_schemas"]
    _keys(schemas, set(REFERENCE_FIELDS), "approval-reference")
    for name in REFERENCE_FIELDS:
        if type(schemas[name]) not in {tuple, list} or not schemas[name]:
            _fail("approval-reference")
        if value[name]["schema_version"] not in schemas[name]:
            _fail("approval-reference")
    uid, gid = value["expected_owner"]["uid"], value["expected_owner"]["gid"]
    roots = [value["host_roots"][role]["path"] for role in ROLES]
    for index, root_path in enumerate(roots):
        _outside(root_path, repo, excluded)
        for other in roots[index + 1 :]:
            if _overlap(root_path, other):
                _fail("overlap")
    masks = value["masks"]
    directory_masks = [m["source_path"] for m in masks if m["kind"] == "directory"]
    for index, path in enumerate(directory_masks):
        _outside(path, repo, [*excluded, *roots])
        if any(_overlap(path, other) for other in directory_masks[index + 1 :]):
            _fail("mask")
    for mask in masks:
        if any(_overlap(mask["relative_path"], source) for source in sources):
            _fail("mask")
    external_files = [value["override_path"], *(value[name]["path"] for name in REFERENCE_FIELDS)]
    if len(set(external_files)) != len(external_files):
        _fail("overlap")
    for path in external_files:
        _outside(path, repo, [*excluded, *roots, *directory_masks])
    identities = list(_private(repo, "directory", uid, gid, None))
    leaves: set[tuple[int, int]] = set()
    for path in roots:
        snapshot = _private(path, "directory", uid, gid, 0o700)
        identity = snapshot[-1][1:]
        if identity in leaves:
            _fail("overlap")
        leaves.add(identity)
        identities.extend(snapshot)
    for mask in masks:
        path = mask["source_path"]
        if mask["kind"] == "directory":
            snapshot = _private(path, "directory", uid, gid, 0o700)
            identity = snapshot[-1][1:]
            if identity in leaves:
                _fail("overlap")
            leaves.add(identity)
            fd, current = _walk(path, "directory")
            try:
                if current != snapshot:
                    _fail("path")
                if os.listdir(fd):
                    _fail("mask")
            except OSError:
                _fail("path")
            finally:
                os.close(fd)
        else:
            snapshot = _private(path, "dev_null", uid, gid, None)
        identities.extend(snapshot)
    for path in external_files:
        identities.extend(_private(path, "file", uid, gid, 0o600))
    for entry in value["compose_files"][:2]:
        identities.extend(_private(entry["path"], "file", uid, gid, None))
    return tuple(sorted(set(identities)))


def load_runtime_profile(
    path: str | Path, *, context: Mapping[str, Any] | None = None
) -> RuntimeProfile:
    """Read a private regular file; verify form, never approval authority.

    Supply context to reject repository/history/session paths before reading.
    Without context, only the explicit file and profile form are validated.
    """
    path = _absolute(str(path))
    if path == "/tmp" or path.startswith("/tmp/"):
        _fail("path")
    if context is not None:
        _keys(
            context,
            {
                "repo_root",
                "historical_roots",
                "prior_session_roots",
                "running_sources",
                "approved_reference_schemas",
            },
        )
        excluded = []
        for name in ("historical_roots", "prior_session_roots"):
            if type(context[name]) not in {tuple, list}:
                _fail("schema")
            excluded.extend(_absolute(item) for item in context[name])
        _outside(path, _absolute(context["repo_root"]), excluded)
    fd, components = _walk(path, "file")
    try:
        before = os.fstat(fd)
        if before.st_uid != os.getuid():
            _fail("owner")
        if stat.S_IMODE(before.st_mode) != 0o600:
            _fail("mode")
        chunks = bytearray()
        while len(chunks) <= MAX_PROFILE_BYTES:
            block = os.read(fd, min(65536, MAX_PROFILE_BYTES + 1 - len(chunks)))
            if not block:
                break
            chunks.extend(block)
        after = os.fstat(fd)
        observed = os.lstat(path)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or (observed.st_dev, observed.st_ino) != (after.st_dev, after.st_ino)
            or stat.S_ISLNK(observed.st_mode)
        ):
            _fail("path")
        for component, device, inode in components:
            check = os.lstat(component)
            if stat.S_ISLNK(check.st_mode):
                _fail("symlink")
            if (check.st_dev, check.st_ino) != (device, inode):
                _fail("path")
        profile = parse_runtime_profile_bytes(bytes(chunks), context=context)
        value = profile.to_dict()
        uid, gid = value["expected_owner"]["uid"], value["expected_owner"]["gid"]
        if (after.st_uid, after.st_gid) != (uid, gid):
            _fail("owner")
        other_paths = [
            *(root["path"] for root in value["host_roots"].values()),
            *(mask["source_path"] for mask in value["masks"] if mask["kind"] == "directory"),
            value["override_path"],
            *(value[name]["path"] for name in REFERENCE_FIELDS),
        ]
        _outside(path, value["source_host_root"], other_paths)
        return profile
    except RuntimeProfileError:
        raise
    except OSError:
        _fail("path")
    finally:
        os.close(fd)
    raise AssertionError("unreachable")
