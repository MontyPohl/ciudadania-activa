"""B5 operational deployment observations and closed, immutable envelope.

Host providers are installed by the approved launcher; no RPC chooses a runner,
source path, container, approval, or trust pin. This core never invokes Docker,
imports Django, runs preflight, refreshes an envelope, or changes a session.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from . import ex10_load
from . import ex10_open_evidence as evidence
from . import ex10_runtime_profile as rp
from . import ex10_session_v2 as sessions

SCHEMA_VERSION = "s11-ex10-operational-deployment-envelope-v1"
MAX_BYTES = 1024 * 1024
MAX_AGE = 900
FIELDS = frozenset(
    {
        "schema_version",
        "result",
        "checked_at",
        "expires_at",
        "expected_git_sha",
        "runtime_profile_sha256",
        "session_id",
        "session_header_sha256",
        "project",
        "host_roots",
        "fixed_targets",
        "compose_files",
        "override_path",
        "override_sha256",
        "resolved_compose_projection_sha256",
        "deployment_verification_reference",
        "deployment_identity",
        "container_identities",
        "mounts",
        "running_sources",
        "host_operational_sources",
        "ready_state",
        "protocol_identity",
        "application_preflight_sha256",
        "historical_authority_view_reference",
        "checks",
    }
)
MOUNT_FIELDS = frozenset(
    {"container_id", "type", "source_canonical", "destination", "read_only", "ownership_reference"}
)
CONTAINER_FIELDS = frozenset({"container_id", "service", "role", "image_digest", "running"})
CHECK_FIELDS = frozenset({"check", "result", "expected", "observed_sanitized", "detail_code"})


class EnvelopeError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(value, code="ENVELOPE_INVALID"):
    if not value:
        raise EnvelopeError(code)


def _call(provider, **arguments):
    try:
        return provider(**arguments)
    except Exception:
        raise EnvelopeError("OBSERVATION_UNAVAILABLE") from None


def _keys(value, fields):
    require(type(value) is dict and set(value) == fields)


def _time(value):
    try:
        rp._match(value, rp._TIMESTAMP, "timestamp")
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except Exception:
        raise EnvelopeError("ENVELOPE_INVALID") from None


def _pf_time(value):
    # Exact UTC forms emitted by the frozen application's datetime.isoformat().
    # Parse the existing artifact bytes; never normalize or rewrite its identity.
    require(
        type(value) is str
        and re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{6})?Z", value
        ),
        "PREFLIGHT_INVALID",
    )
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except Exception:
        raise EnvelopeError("PREFLIGHT_INVALID") from None


def _utc(value):
    require(type(value) is datetime and value.tzinfo is not None)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sha(value):
    try:
        rp._hash(value)
    except Exception:
        raise EnvelopeError("ENVELOPE_INVALID") from None


def _path(value):
    try:
        return rp._absolute(value)
    except Exception:
        raise EnvelopeError("PATH_INVALID") from None


def _reference(value):
    try:
        rp._reference(value)
    except Exception:
        raise EnvelopeError("REFERENCE_INVALID") from None


def canonical(value):
    try:
        raw = (
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            + "\n"
        ).encode()
        require(len(raw) <= MAX_BYTES)
        return raw
    except EnvelopeError:
        raise
    except Exception:
        raise EnvelopeError("ENVELOPE_INVALID") from None


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def _mount(value):
    _keys(value, MOUNT_FIELDS)
    _sha(value["container_id"])
    require(value["type"] in ("bind", "volume") and type(value["read_only"]) is bool)
    _path(value["source_canonical"])
    _path(value["destination"])
    _reference(value["ownership_reference"])


def _containers(values):
    require(type(values) is list and len(values) <= 32)
    services = set()
    ids = set()
    for v in values:
        _keys(v, CONTAINER_FIELDS)
        _sha(v["container_id"])
        require(type(v["service"]) is str and rp._PROJECT.fullmatch(v["service"]) is not None)
        require(type(v["role"]) is str and rp._ID.fullmatch(v["role"]) is not None)
        require(type(v["image_digest"]) is str and v["image_digest"].startswith("sha256:"))
        _sha(v["image_digest"][7:])
        require(
            type(v["running"]) is bool
            and v["container_id"] not in ids
            and v["service"] not in services
        )
        ids.add(v["container_id"])
        services.add(v["service"])


def _sources(value):
    require(type(value) is dict and len(value) <= 512)
    for path, sha in value.items():
        try:
            rp._relative(path)
        except Exception:
            raise EnvelopeError("SOURCE_INVALID") from None
        _sha(sha)


def parse_envelope_bytes(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
    try:
        value = json.loads(
            raw.decode(), object_pairs_hook=_unique, parse_constant=lambda _: require(False)
        )
    except EnvelopeError:
        raise
    except Exception:
        raise EnvelopeError("ENVELOPE_INVALID") from None
    _keys(value, FIELDS)
    require(
        value["schema_version"] == SCHEMA_VERSION
        and value["result"] in ("PASS", "FAIL", "INCOMPLETE")
    )
    checked, expires = _time(value["checked_at"]), _time(value["expires_at"])
    require(checked <= expires <= checked + timedelta(seconds=MAX_AGE))
    try:
        rp._match(value["expected_git_sha"], rp._SHA1, "hash")
        rp._match(value["project"], rp._PROJECT, "project")
    except Exception:
        raise EnvelopeError("ENVELOPE_INVALID") from None
    require(type(value["session_id"]) is str and rp._ID.fullmatch(value["session_id"]) is not None)
    for name in (
        "runtime_profile_sha256",
        "session_header_sha256",
        "override_sha256",
        "application_preflight_sha256",
    ):
        _sha(value[name])
    for name in ("resolved_compose_projection_sha256", "deployment_identity"):
        if value[name] is not None:
            _sha(value[name])
        else:
            require(value["result"] != "PASS")
    _path(value["override_path"])
    _reference(value["deployment_verification_reference"])
    _reference(value["historical_authority_view_reference"])
    _keys(value["host_roots"], set(rp.ROLES))
    require(value["fixed_targets"] == rp.FIXED_TARGETS)
    for root in value["host_roots"].values():
        _keys(root, {"path", "owner_uid", "owner_gid", "directory_mode", "artifact_mode"})
        _path(root["path"])
        require(
            type(root["owner_uid"]) is int
            and root["owner_uid"] >= 0
            and type(root["owner_gid"]) is int
            and root["owner_gid"] >= 0
            and root["directory_mode"] == "0700"
            and root["artifact_mode"] == "0600"
        )
    require(type(value["compose_files"]) is list and len(value["compose_files"]) == 3)
    for index, entry in enumerate(value["compose_files"]):
        _keys(entry, {"kind", "path", "git_path", "git_blob", "sha256"})
        _path(entry["path"])
        _sha(entry["sha256"])
        if index < 2:
            require(
                entry["kind"] == "tracked"
                and entry["git_path"]
                == ("infra/docker-compose.yml", "infra/docker-compose.s11.yml")[index]
            )
            rp._match(entry["git_blob"], rp._SHA1, "hash")
        else:
            require(
                entry["kind"] == "external_override"
                and entry["git_path"] is None
                and entry["git_blob"] is None
                and entry["path"] == value["override_path"]
                and entry["sha256"] == value["override_sha256"]
            )
    _keys(value["ready_state"], {"report_reference", "checks"})
    _reference(value["ready_state"]["report_reference"])
    require(type(value["ready_state"]["checks"]) is list and value["ready_state"]["checks"])
    for ready in value["ready_state"]["checks"]:
        _keys(ready, {"check", "result"})
        require(
            type(ready["check"]) is str
            and rp._ID.fullmatch(ready["check"])
            and ready["result"] in ("PASS", "FAIL", "INCOMPLETE")
        )
    _containers(value["container_identities"])
    require(type(value["mounts"]) is list and len(value["mounts"]) <= 256)
    destinations = set()
    for mount in value["mounts"]:
        _mount(mount)
        key = (mount["container_id"], mount["destination"])
        require(key not in destinations)
        destinations.add(key)
    for name in ("running_sources", "host_operational_sources"):
        _sources(value[name])
    require(type(value["checks"]) is list and value["checks"])
    names = set()
    for check in value["checks"]:
        _keys(check, CHECK_FIELDS)
        require(
            type(check["check"]) is str
            and check["check"] not in names
            and check["result"] in ("PASS", "FAIL", "INCOMPLETE")
        )
        names.add(check["check"])
    outcomes = {v["result"] for v in value["checks"]}
    expected = (
        "FAIL" if "FAIL" in outcomes else "INCOMPLETE" if "INCOMPLETE" in outcomes else "PASS"
    )
    require(value["result"] == expected)
    require(raw == canonical(value))
    require(value["protocol_identity"] == ex10_load.protocol_identity(), "PROTOCOL_CHANGED")
    for key, item in value.items():
        if key != "protocol_identity":
            _safe_values(item)
    return OperationalEnvelope(raw)


@dataclass(frozen=True, slots=True)
class OperationalEnvelope:
    _bytes: bytes

    def to_dict(self):
        return json.loads(self._bytes)

    @property
    def sha256(self):
        return hashlib.sha256(self._bytes).hexdigest()


# A positive allowlist is applied BEFORE any raw Compose value can enter output.
def sanitized_compose_projection(raw):
    require(type(raw) is dict and type(raw.get("services")) is dict, "COMPOSE_INVALID")
    project = raw.get("name")
    require(type(project) is str and rp._PROJECT.fullmatch(project), "COMPOSE_INVALID")
    result = {"project": project, "services": {}}
    for name, service in sorted(raw["services"].items()):
        require(
            type(name) is str and rp._PROJECT.fullmatch(name) and type(service) is dict,
            "COMPOSE_INVALID",
        )
        image = service.get("image")
        build = service.get("build")
        require(image is not None or build is not None, "COMPOSE_INVALID")
        identity = {}
        if image is not None:
            require(type(image) is str and 0 < len(image) <= 256, "COMPOSE_INVALID")
            require(
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*(?:@sha256:[0-9a-f]{64})?", image)
                and "://" not in image
                and not rp._SECRET.search(image),
                "COMPOSE_INVALID",
            )
            identity["image"] = image
        if build is not None:
            require(type(build) is dict, "COMPOSE_INVALID")
            context = build.get("context")
            dockerfile = build.get("dockerfile", "Dockerfile")
            _path(context)
            try:
                rp._relative(dockerfile)
            except Exception:
                raise EnvelopeError("COMPOSE_INVALID") from None
            _safe_values(context)
            _safe_values(dockerfile)
            identity["build"] = {"context": context, "dockerfile": dockerfile}
        volumes = service.get("volumes", [])
        require(type(volumes) is list, "COMPOSE_INVALID")
        safe = []
        for mount in volumes:
            require(type(mount) is dict, "COMPOSE_INVALID")
            kind = mount.get("type")
            source = mount.get("source")
            target = mount.get("target")
            readonly = mount.get("read_only", False)
            require(kind in ("bind", "volume") and type(readonly) is bool, "COMPOSE_INVALID")
            _path(target)
            if kind == "bind":
                _path(source)
            else:
                require(type(source) is str and rp._ID.fullmatch(source), "COMPOSE_INVALID")
            safe.append({"type": kind, "source": source, "target": target, "read_only": readonly})
        result["services"][name] = {
            **identity,
            "volumes": sorted(safe, key=lambda m: m["target"]),
        }
    return result


def collect_observations(*, compose_observer, container_observer):
    # Providers may observe private Docker data; it is never returned or logged.
    raw_compose = _call(compose_observer)
    require(raw_compose is not None, "OBSERVATION_UNAVAILABLE")
    compose = sanitized_compose_projection(raw_compose)
    containers = _call(container_observer)
    require(type(containers) is list, "OBSERVATION_UNAVAILABLE")
    safe = []
    for raw in containers:
        require(type(raw) is dict, "OBSERVATION_UNAVAILABLE")
        item = {key: raw.get(key) for key in CONTAINER_FIELDS}
        _containers([item])
        require(type(raw.get("mounts")) is list, "OBSERVATION_UNAVAILABLE")
        item["mounts"] = []
        for original in raw["mounts"]:
            require(type(original) is dict, "OBSERVATION_UNAVAILABLE")
            mount = {key: original.get(key) for key in MOUNT_FIELDS}
            _mount(mount)
            item["mounts"].append(mount)
        safe.append(item)
    return {"compose": compose, "containers": safe}


def _file(path, *, owner, private=False):
    """Full bytes and path/FD stat lease, never resolve aliases or follow links."""
    _path(path)
    fd = None
    try:
        fd, components = rp._walk(path, "file")
        before = os.fstat(fd)
        require(
            (before.st_uid, before.st_gid) == owner
            and before.st_nlink == 1
            and 0 < before.st_size <= 16 * 1024 * 1024,
            "FILE_INVALID",
        )
        require(
            stat.S_IMODE(before.st_mode) in ((0o600,) if private else (0o600, 0o644)),
            "FILE_INVALID",
        )
        result = bytearray()
        while len(result) <= before.st_size:
            chunk = os.read(fd, min(65536, before.st_size + 1 - len(result)))
            if not chunk:
                break
            result.extend(chunk)
        current = os.fstat(fd)
        check, second = rp._walk(path, "file")
        try:

            def stamp(s):
                return (
                    s.st_dev,
                    s.st_ino,
                    s.st_uid,
                    s.st_gid,
                    s.st_mode,
                    s.st_nlink,
                    s.st_size,
                    s.st_mtime_ns,
                    s.st_ctime_ns,
                )

            require(
                stamp(before) == stamp(current) == stamp(os.fstat(check))
                and components == second
                and len(result) == before.st_size,
                "FILE_CHANGED",
            )
        finally:
            os.close(check)
        return bytes(result), (components, stamp(before))
    except EnvelopeError:
        raise
    except Exception:
        raise EnvelopeError("FILE_UNAVAILABLE") from None
    finally:
        if fd is not None:
            os.close(fd)


def _safe_values(value):
    if value is None or type(value) in (bool, int):
        return
    if type(value) is str:
        require(
            len(value) <= 4096
            and not rp._SECRET.search(value)
            and "://" not in value
            and all(ord(c) >= 32 for c in value),
            "SANITIZATION_INVALID",
        )
        return
    if type(value) is list:
        require(len(value) <= 512)
        for v in value:
            _safe_values(v)
        return
    require(type(value) is dict and len(value) <= 512)
    for key, v in value.items():
        require(type(key) is str)
        _safe_values(key)
        _safe_values(v)


def _check(name, result, expected, observed, code=None):
    value = {
        "check": name,
        "result": result,
        "expected": expected,
        "observed_sanitized": observed,
        "detail_code": code,
    }
    _safe_values(value)
    return value


class EnvelopeQualifier:
    """Fixed host resources, fresh injected Docker observations and exact bytes.

    Approval/source/authority providers are mandatory externally installed trust
    boundaries. A provider must return None, not a self-declared true. Explicit
    build obtains a new observation; qualify never updates a stale envelope.
    """

    def __init__(
        self,
        *,
        profile_reference,
        header_reference,
        application_preflight_reference,
        historical_authority_view_reference,
        path_context,
        running_sources,
        host_operational_sources,
        container_identities,
        mounts,
        compose_projection,
        ready_state,
        application_required_checks,
        compose_observer,
        container_observer,
        running_source_reader,
        bootstrap_verifier,
        source_verifier,
        session_verifier,
        authority_verifier,
        ready_verifier,
        clock,
    ):
        for ref in (
            profile_reference,
            header_reference,
            application_preflight_reference,
            historical_authority_view_reference,
        ):
            _reference(ref)
        _sources(running_sources)
        _sources(host_operational_sources)
        require(running_sources and host_operational_sources, "SOURCE_INVALID")
        require(
            all(
                running_sources[p] == host_operational_sources[p]
                for p in set(running_sources) & set(host_operational_sources)
            ),
            "SOURCE_INVALID",
        )
        _containers(container_identities)
        require(type(mounts) is list and mounts, "MOUNT_INVALID")
        for mount in mounts:
            _mount(mount)
        require(
            type(application_required_checks) is frozenset
            and application_required_checks
            and all(type(n) is str and n for n in application_required_checks),
            "PREFLIGHT_INVALID",
        )
        for callback in (
            compose_observer,
            container_observer,
            running_source_reader,
            bootstrap_verifier,
            source_verifier,
            session_verifier,
            authority_verifier,
            ready_verifier,
            clock,
        ):
            require(callable(callback), "APPROVAL_REQUIRED")
        # Canonical immutable configuration; mappings cannot be rebound by callers.
        self.config = canonical(
            {
                "profile_reference": profile_reference,
                "header_reference": header_reference,
                "application_preflight_reference": application_preflight_reference,
                "historical_authority_view_reference": historical_authority_view_reference,
                "path_context": path_context,
                "running_sources": running_sources,
                "host_operational_sources": host_operational_sources,
                "container_identities": container_identities,
                "mounts": mounts,
                "compose_projection": compose_projection,
                "ready_state": ready_state,
                "application_required_checks": sorted(application_required_checks),
            }
        )
        self.compose_observer, self.container_observer, self.running_reader = (
            compose_observer,
            container_observer,
            running_source_reader,
        )
        self.bootstrap, self.source, self.session, self.authority, self.ready = (
            bootstrap_verifier,
            source_verifier,
            session_verifier,
            authority_verifier,
            ready_verifier,
        )
        self.clock = clock

    def _now(self):
        try:
            now = self.clock()
            require(type(now) is datetime and now.tzinfo is not None)
            return now.astimezone(UTC)
        except Exception:
            raise EnvelopeError("CLOCK_UNAVAILABLE") from None

    def _ref(self, ref, owner):
        raw, lease = _file(ref["path"], owner=owner, private=True)
        require(hashlib.sha256(raw).hexdigest() == ref["sha256"], "REFERENCE_CHANGED")
        return raw, lease

    def _fixed(self, *, fresh_required=True):
        require(type(fresh_required) is bool, "ENVELOPE_INVALID")
        cfg = json.loads(self.config)
        # Owner comes from fixed B1 path context, not any envelope/request.
        profile_path = cfg["profile_reference"]["path"]
        try:
            fd, _ = rp._walk(profile_path, "file")
            try:
                st = os.fstat(fd)
                owner = (st.st_uid, st.st_gid)
            finally:
                os.close(fd)
        except Exception:
            raise EnvelopeError("FILE_UNAVAILABLE") from None
        raw, lease = self._ref(cfg["profile_reference"], owner)
        try:
            profile = rp.parse_runtime_profile_bytes(raw, context=cfg["path_context"])
            value = profile.to_dict()
            require(
                owner == (value["expected_owner"]["uid"], value["expected_owner"]["gid"]),
                "FILE_INVALID",
            )
            header_raw, header_lease = self._ref(cfg["header_reference"], owner)
            header = json.loads(header_raw)
            require(header_raw == sessions.canonical_session_header_bytes(header))
            sessions._profile_binding(header, profile)
        except EnvelopeError:
            raise
        except Exception:
            raise EnvelopeError("PROFILE_SESSION_INVALID") from None
        require(
            set(cfg["path_context"]["running_sources"])
            >= set(cfg["running_sources"]) | set(cfg["host_operational_sources"]),
            "MASKED_SOURCE",
        )
        require(
            _call(self.session, profile=profile, header=header, canonical_header=header_raw)
            is None,
            "APPROVAL_REQUIRED",
        )
        deployment, deployment_lease = self._ref(value["deployment_verification_reference"], owner)
        require(
            _call(
                self.bootstrap,
                reference=value["deployment_verification_reference"],
                canonical_approval=deployment,
                profile=profile,
                approved_containers=cfg["container_identities"],
                approved_mounts=cfg["mounts"],
                approved_compose_projection=cfg["compose_projection"],
            )
            is None,
            "APPROVAL_REQUIRED",
        )
        actual_bindings = []
        directories = {
            value["source_host_root"],
            *(r["path"] for r in value["host_roots"].values()),
            *(m["source_path"] for m in value["masks"] if m["kind"] == "directory"),
        }
        for binding in header["path_identity_binding"]:
            path = binding["path"]
            kind = (
                "dev_null"
                if path == "/dev/null"
                else "directory"
                if path in directories
                else "file"
            )
            try:
                fd, _ = rp._walk(path, kind)
                try:
                    s = os.fstat(fd)
                    actual_bindings.append(
                        {
                            "path": path,
                            "device": s.st_dev,
                            "inode": s.st_ino,
                            "uid": s.st_uid,
                            "gid": s.st_gid,
                            "mode": format(stat.S_IMODE(s.st_mode), "04o"),
                        }
                    )
                finally:
                    os.close(fd)
            except Exception:
                raise EnvelopeError("PATH_CHANGED") from None
        require(actual_bindings == header["path_identity_binding"], "PATH_CHANGED")
        for entry in value["compose_files"]:
            compose_raw, _ = _file(
                entry["path"], owner=owner, private=entry["kind"] == "external_override"
            )
            require(hashlib.sha256(compose_raw).hexdigest() == entry["sha256"], "COMPOSE_CHANGED")
        pf_raw, pf_lease = self._ref(cfg["application_preflight_reference"], owner)
        try:
            pf = json.loads(pf_raw)
            require(pf_raw == canonical(pf))
            _keys(pf, evidence.ARTIFACT_FIELDS["preflight_reference"])
        except EnvelopeError:
            raise
        except Exception:
            raise EnvelopeError("PREFLIGHT_INVALID") from None
        expected = {
            "schema_version": evidence.ARTIFACT_SCHEMAS["preflight_reference"],
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
            "git_sha": value["source_revision"],
            "expected_git_sha": value["source_revision"],
        }
        require(
            all(type(pf[k]) is type(v) and pf[k] == v for k, v in expected.items()),
            "PREFLIGHT_INVALID",
        )
        checks = pf["checks"]
        require(type(checks) is list and checks, "PREFLIGHT_INVALID")
        names = []
        for check in checks:
            _keys(check, CHECK_FIELDS)
            require(
                check["result"] == "PASS"
                and type(check["check"]) is str
                and 0 < len(check["check"]) <= 256,
                "PREFLIGHT_INVALID",
            )
            _safe_values(check)
            names.append(check["check"])
        require(
            len(names) == len(set(names))
            and set(cfg["application_required_checks"]) <= set(names)
            and any(n.startswith("ready_state:") for n in names),
            "PREFLIGHT_INVALID",
        )
        preflight_age = (self._now() - _pf_time(pf["checked_at"])).total_seconds()
        require(
            0 <= preflight_age and (not fresh_required or preflight_age <= MAX_AGE),
            "PREFLIGHT_EXPIRED",
        )
        auth_raw, auth_lease = self._ref(cfg["historical_authority_view_reference"], owner)
        require(
            _call(
                self.authority,
                reference=cfg["historical_authority_view_reference"],
                canonical_observation=auth_raw,
                profile=profile,
                header=header,
            )
            is None,
            "APPROVAL_REQUIRED",
        )
        ready_raw, ready_lease = self._ref(cfg["ready_state"]["report_reference"], owner)
        require(
            all(c["result"] == "PASS" for c in cfg["ready_state"]["checks"]), "READY_UNAPPROVED"
        )
        require(
            _call(
                self.ready,
                ready_state=cfg["ready_state"],
                canonical_report=ready_raw,
                profile=profile,
                header=header,
            )
            is None,
            "READY_UNAPPROVED",
        )
        return (
            cfg,
            profile,
            header,
            pf,
            (lease, header_lease, deployment_lease, pf_lease, auth_lease, ready_lease),
        )

    def _observe(self, cfg, profile, header):
        p = profile.to_dict()
        owner = (p["expected_owner"]["uid"], p["expected_owner"]["gid"])
        first = collect_observations(
            compose_observer=self.compose_observer, container_observer=self.container_observer
        )
        require(
            first["compose"] == cfg["compose_projection"]
            and first["compose"]["project"] == p["project"],
            "COMPOSE_CHANGED",
        )
        observed = []
        mounts = []
        for item in first["containers"]:
            observed.append({k: item[k] for k in CONTAINER_FIELDS})
            require(type(item["mounts"]) is list, "MOUNT_INVALID")
            for mount in item["mounts"]:
                _mount(mount)
                require(mount["container_id"] == item["container_id"], "MOUNT_INVALID")
                mounts.append(mount)
        _containers(observed)
        observed.sort(key=lambda v: v["service"])
        expected = sorted(cfg["container_identities"], key=lambda v: v["service"])
        require(
            observed == expected
            and {v["service"] for v in observed} == set(p["expected_services"])
            and all(v["running"] is True for v in observed),
            "CONTAINER_CHANGED",
        )
        mounts.sort(key=lambda v: (v["container_id"], v["destination"]))
        wanted = sorted(cfg["mounts"], key=lambda v: (v["container_id"], v["destination"]))
        require(mounts == wanted, "MOUNT_CHANGED")
        for item in observed:
            if item["service"] not in ("backend", "worker"):
                continue
            by_target = {
                m["destination"]: m for m in mounts if m["container_id"] == item["container_id"]
            }
            require(
                len(by_target) == sum(m["container_id"] == item["container_id"] for m in mounts),
                "MOUNT_INVALID",
            )
            require(
                by_target.get(p["source_container_target"], {}).get("source_canonical")
                == p["source_host_root"]
                and by_target[p["source_container_target"]]["read_only"] is True,
                "MOUNT_CHANGED",
            )
            for mask in p["masks"]:
                target = p["source_container_target"] + "/" + mask["relative_path"]
                require(
                    target in by_target
                    and by_target[target]["source_canonical"] == mask["source_path"]
                    and by_target[target]["read_only"] is True,
                    "MASKED_SOURCE",
                )
            for role, root in p["host_roots"].items():
                require(
                    by_target.get(p["fixed_targets"][role], {}).get("source_canonical")
                    == root["path"],
                    "MOUNT_CHANGED",
                )
            # All mounts are part of the independently approved exact allowlist;
            # an extra Docker socket, host ledger/key, or witness mount fails above.
        host = {}
        running = {}
        leases = {}
        for relative, expected_hash in sorted(
            {**cfg["running_sources"], **cfg["host_operational_sources"]}.items()
        ):
            raw, lease = _file(p["source_host_root"] + "/" + relative, owner=owner)
            actual = hashlib.sha256(raw).hexdigest()
            require(actual == expected_hash, "SOURCE_CHANGED")
            leases[relative] = lease
            if relative in cfg["host_operational_sources"]:
                host[relative] = actual
            if relative in cfg["running_sources"]:
                for container in observed:
                    if container["service"] not in ("backend", "worker"):
                        continue
                    runtime = _call(
                        self.running_reader,
                        container_id=container["container_id"],
                        relative_path=relative,
                    )
                    require(
                        type(runtime) is bytes and hashlib.sha256(runtime).hexdigest() == actual,
                        "RUNNING_SOURCE_CHANGED",
                    )
                running[relative] = actual
        require(
            _call(
                self.source,
                profile=profile,
                header=header,
                host_hashes=host,
                running_hashes=running,
            )
            is None,
            "SOURCE_UNAPPROVED",
        )
        second = collect_observations(
            compose_observer=self.compose_observer, container_observer=self.container_observer
        )
        require(first == second, "DEPLOYMENT_CHANGED")
        for relative, prior in leases.items():
            _, current = _file(p["source_host_root"] + "/" + relative, owner=owner)
            require(prior == current, "SOURCE_CHANGED")
        return {
            "resolved_compose_projection_sha256": digest(first["compose"]),
            "container_identities": observed,
            "mounts": mounts,
            "running_sources": running,
            "host_operational_sources": host,
            "ready_state": cfg["ready_state"],
        }, leases

    def _postcheck(self, cfg, profile, header, leases, observed, source_leases):
        """Passive final reads after every approval provider; no trust callback."""
        p = profile.to_dict()
        owner = (p["expected_owner"]["uid"], p["expected_owner"]["gid"])
        first = collect_observations(
            compose_observer=self.compose_observer, container_observer=self.container_observer
        )
        require(first["compose"] == cfg["compose_projection"], "COMPOSE_CHANGED")
        current = sorted(
            ({k: v[k] for k in CONTAINER_FIELDS} for v in first["containers"]),
            key=lambda v: v["service"],
        )
        mounts = sorted(
            (m for v in first["containers"] for m in v["mounts"]),
            key=lambda v: (v["container_id"], v["destination"]),
        )
        require(
            current == observed["container_identities"] and mounts == observed["mounts"],
            "DEPLOYMENT_CHANGED",
        )
        sources = {**cfg["running_sources"], **cfg["host_operational_sources"]}
        for relative, expected in sources.items():
            raw, lease = _file(p["source_host_root"] + "/" + relative, owner=owner)
            require(
                lease == source_leases[relative] and hashlib.sha256(raw).hexdigest() == expected,
                "SOURCE_CHANGED",
            )
            if relative in cfg["running_sources"]:
                for container in current:
                    if container["service"] not in ("backend", "worker"):
                        continue
                    runtime = _call(
                        self.running_reader,
                        container_id=container["container_id"],
                        relative_path=relative,
                    )
                    require(
                        type(runtime) is bytes and hashlib.sha256(runtime).hexdigest() == expected,
                        "RUNNING_SOURCE_CHANGED",
                    )
        last = collect_observations(
            compose_observer=self.compose_observer, container_observer=self.container_observer
        )
        require(first == last, "DEPLOYMENT_CHANGED")
        for relative, expected in sources.items():
            raw, lease = _file(p["source_host_root"] + "/" + relative, owner=owner)
            require(
                lease == source_leases[relative] and hashlib.sha256(raw).hexdigest() == expected,
                "SOURCE_CHANGED",
            )
        refs = (
            cfg["profile_reference"],
            cfg["header_reference"],
            p["deployment_verification_reference"],
            cfg["application_preflight_reference"],
            cfg["historical_authority_view_reference"],
            cfg["ready_state"]["report_reference"],
        )
        actual = tuple(self._ref(ref, owner)[1] for ref in refs)
        require(actual == leases, "FILE_CHANGED")
        try:
            rp.validate_runtime_profile_paths(profile, cfg["path_context"])
            directories = {
                p["source_host_root"],
                *(r["path"] for r in p["host_roots"].values()),
                *(m["source_path"] for m in p["masks"] if m["kind"] == "directory"),
            }
            for binding in header["path_identity_binding"]:
                path = binding["path"]
                kind = (
                    "dev_null"
                    if path == "/dev/null"
                    else "directory"
                    if path in directories
                    else "file"
                )
                fd, _ = rp._walk(path, kind)
                try:
                    st = os.fstat(fd)
                    require(
                        binding
                        == {
                            "path": path,
                            "device": st.st_dev,
                            "inode": st.st_ino,
                            "uid": st.st_uid,
                            "gid": st.st_gid,
                            "mode": format(stat.S_IMODE(st.st_mode), "04o"),
                        },
                        "PATH_CHANGED",
                    )
                finally:
                    os.close(fd)
            for entry in p["compose_files"]:
                raw, _ = _file(
                    entry["path"], owner=owner, private=entry["kind"] == "external_override"
                )
                require(hashlib.sha256(raw).hexdigest() == entry["sha256"], "COMPOSE_CHANGED")
        except EnvelopeError:
            raise
        except Exception:
            raise EnvelopeError("PATH_CHANGED") from None

    def _projection(self, *, fresh_required=True):
        cfg, profile, header, pf, leases = self._fixed(fresh_required=fresh_required)
        observed, source_leases = self._observe(cfg, profile, header)
        second = self._fixed(fresh_required=fresh_required)
        require(
            (second[1]._bytes, canonical(second[2]), second[4])
            == (profile._bytes, canonical(header), leases),
            "FILE_CHANGED",
        )
        self._postcheck(cfg, profile, header, leases, observed, source_leases)
        p = profile.to_dict()
        identity = digest(
            {
                "project": p["project"],
                "deployment_verification_reference": p["deployment_verification_reference"],
                **observed,
            }
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "expected_git_sha": p["source_revision"],
            "runtime_profile_sha256": rp.runtime_profile_sha256(profile),
            "session_id": header["session_id"],
            "session_header_sha256": sessions.session_header_sha256(header),
            "project": p["project"],
            "host_roots": p["host_roots"],
            "fixed_targets": p["fixed_targets"],
            "compose_files": p["compose_files"],
            "override_path": p["override_path"],
            "override_sha256": p["override_sha256"],
            "deployment_verification_reference": p["deployment_verification_reference"],
            "deployment_identity": identity,
            "protocol_identity": ex10_load.protocol_identity(),
            "application_preflight_sha256": cfg["application_preflight_reference"]["sha256"],
            "historical_authority_view_reference": cfg["historical_authority_view_reference"],
            **observed,
        }, pf

    def build(self):
        # Explicit observation action only. No qualification automatically refreshes.
        try:
            value, pf = self._projection()
        except EnvelopeError as error:
            cfg = json.loads(self.config)
            try:
                st = os.lstat(cfg["profile_reference"]["path"])
                profile_raw = _file(
                    cfg["profile_reference"]["path"],
                    owner=(
                        st.st_uid,
                        st.st_gid,
                    ),
                    private=True,
                )[0]
                require(
                    hashlib.sha256(profile_raw).hexdigest() == cfg["profile_reference"]["sha256"],
                    "REFERENCE_CHANGED",
                )
                profile = rp.parse_runtime_profile_bytes(profile_raw)
                p = profile.to_dict()
                owner = (p["expected_owner"]["uid"], p["expected_owner"]["gid"])
                header_raw, _ = self._ref(cfg["header_reference"], owner)
                header = json.loads(header_raw)
                sessions._profile_binding(header, profile)
            except EnvelopeError:
                raise
            except Exception:
                raise EnvelopeError("FILE_UNAVAILABLE") from None
            now = self._now()
            result = (
                "INCOMPLETE"
                if error.code
                in ("OBSERVATION_UNAVAILABLE", "FILE_UNAVAILABLE", "CLOCK_UNAVAILABLE")
                else "FAIL"
            )
            value = {
                "schema_version": SCHEMA_VERSION,
                "result": result,
                "checked_at": _utc(now),
                "expires_at": _utc(now),
                "expected_git_sha": p["source_revision"],
                "runtime_profile_sha256": rp.runtime_profile_sha256(profile),
                "session_id": header["session_id"],
                "session_header_sha256": sessions.session_header_sha256(header),
                "project": p["project"],
                "host_roots": p["host_roots"],
                "fixed_targets": p["fixed_targets"],
                "compose_files": p["compose_files"],
                "override_path": p["override_path"],
                "override_sha256": p["override_sha256"],
                "resolved_compose_projection_sha256": None,
                "deployment_verification_reference": p["deployment_verification_reference"],
                "deployment_identity": None,
                "container_identities": [],
                "mounts": [],
                "running_sources": {},
                "host_operational_sources": {},
                "ready_state": cfg["ready_state"],
                "protocol_identity": ex10_load.protocol_identity(),
                "application_preflight_sha256": cfg["application_preflight_reference"]["sha256"],
                "historical_authority_view_reference": cfg["historical_authority_view_reference"],
                "checks": [_check("observations", result, True, None, error.code)],
            }
            return parse_envelope_bytes(canonical(value))
        now = self._now()
        expires = min(
            now + timedelta(seconds=MAX_AGE),
            _pf_time(pf["checked_at"]) + timedelta(seconds=MAX_AGE),
        )
        require(now <= expires, "PREFLIGHT_EXPIRED")
        value.update(
            result="PASS",
            checked_at=_utc(now),
            expires_at=_utc(expires),
            checks=[
                _check(name, "PASS", True, True)
                for name in (
                    "profile_session_sources",
                    "bootstrap_deployment",
                    "compose_projection",
                    "container_instances",
                    "mounts_and_masks",
                    "host_and_running_sources",
                    "application_preflight_bytes",
                    "ready_state",
                    "authority_view",
                    "path_leases",
                )
            ],
        )
        return parse_envelope_bytes(canonical(value))

    def qualify(self, raw):
        return self.qualify_attached(raw, fresh_required=True)

    def qualify_attached(self, raw, *, fresh_required):
        """Reobserve the unchanged SUT and approved original application bytes.

        The installed action engine selects freshness from the approved action
        matrix: START/execute before admission remain fresh; lifecycle reads and
        terminal closure do not repeat START PF/READY. This grants no bridge,
        lifecycle or scientific permission and never rebuilds the envelope.
        """
        require(type(fresh_required) is bool, "ENVELOPE_INVALID")
        envelope = parse_envelope_bytes(raw)
        value = envelope.to_dict()
        require(value["result"] == "PASS", "ENVELOPE_NOT_PASS")
        now = self._now()
        require(
            0 <= (now - _time(value["checked_at"])).total_seconds()
            and (
                not fresh_required
                or (
                    (now - _time(value["checked_at"])).total_seconds() <= MAX_AGE
                    and now <= _time(value["expires_at"])
                )
            ),
            "ENVELOPE_EXPIRED",
        )
        expected, pf = self._projection(fresh_required=fresh_required)
        require(
            all(value[name] == observed for name, observed in expected.items()),
            "ENVELOPE_BINDING_CHANGED",
        )
        current = self._now()
        require(
            0 <= (current - _time(value["checked_at"])).total_seconds()
            and 0 <= (current - _pf_time(pf["checked_at"])).total_seconds()
            and _pf_time(pf["checked_at"]) <= _time(value["checked_at"])
            and _time(value["expires_at"])
            <= _pf_time(pf["checked_at"]) + timedelta(seconds=MAX_AGE)
            and (
                not fresh_required
                or (
                    (current - _time(value["checked_at"])).total_seconds() <= MAX_AGE
                    and current <= _time(value["expires_at"])
                )
            ),
            "ENVELOPE_EXPIRED",
        )
        return envelope
