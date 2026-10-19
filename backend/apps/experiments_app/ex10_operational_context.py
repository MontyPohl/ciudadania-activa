"""S11 fixed operational bridge and full host qualification.

Transport admission is separate from protocol signatures and deployment trust.
Only constructor-fixed observed processes can carry bounded approved frames.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import secrets
import select
import stat
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import ex10_authority_backend as durable
from . import ex10_authority_sqlite as sql
from . import ex10_bridge_protocol as wire
from . import ex10_governance_certificate as governance
from . import ex10_history_authority as history
from . import ex10_open_evidence as evidence
from . import ex10_runtime_profile as profile_codec
from . import ex10_session_v2 as session_codec
from . import ex10_start_coordination as coordination

START_STEPS = (
    ("HOST_CHALLENGE", "HOST_TO_EXECUTOR"),
    ("PROPOSE_OPEN", "EXECUTOR_TO_HOST"),
    ("AUTHORIZE_REQUEST", "HOST_TO_EXECUTOR"),
    ("RPC_REQUEST", "EXECUTOR_TO_HOST"),
    ("RPC_RESPONSE", "HOST_TO_EXECUTOR"),
    ("OPEN_EVIDENCE", "EXECUTOR_TO_HOST"),
    ("AUTHORIZE_REQUEST", "HOST_TO_EXECUTOR"),
    ("RPC_REQUEST", "EXECUTOR_TO_HOST"),
    ("RPC_RESPONSE", "HOST_TO_EXECUTOR"),
)
SINGLE_STEPS = (
    ("HOST_CHALLENGE", "HOST_TO_EXECUTOR"),
    ("RPC_PROPOSAL", "EXECUTOR_TO_HOST"),
    ("AUTHORIZE_REQUEST", "HOST_TO_EXECUTOR"),
    ("RPC_REQUEST", "EXECUTOR_TO_HOST"),
    ("RPC_RESPONSE", "HOST_TO_EXECUTOR"),
)


class OperationalError(ValueError):
    """Sanitized fixed reason; unknown mutation never becomes retry permission."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _require(condition, code):
    if not condition:
        raise OperationalError(code)


class PipeFrameChannel:
    """One bounded exchange on retained private POSIX pipes, never stdout text RPC.

    Owns duplicated FDs. All reads/writes share one monotonic deadline and a
    fixed role/sequence. Bounded diagnostics are discarded, never logged.
    Signature/context verification happens in the typed broker above this layer.
    """

    def __init__(
        self,
        *,
        read_fd,
        write_fd,
        error_fd=None,
        exchange,
        exchange_id,
        role="HOST",
        budget=120,
        process_guard=None,
    ):
        _require(
            exchange in ("START", "SINGLE_RPC") and role in ("HOST", "EXECUTOR"), "PROTOCOL_INVALID"
        )
        _require(type(budget) in (int, float) and 0 < budget <= 120, "PROTOCOL_INVALID")
        sql.validate_scalar(exchange_id, {"type": "uuid", "not_null": True})
        self.exchange, self.exchange_id, self.role = exchange, exchange_id, role
        self.steps = START_STEPS if exchange == "START" else SINGLE_STEPS
        self.position = 0
        self.deadline = time.monotonic() + budget
        self.owner = os.getpid()
        self.dispatched = False
        self.finished = False
        self.closed = False
        self.guard = process_guard
        self.error_count = 0
        self.read_fd = self.write_fd = self.error_fd = None
        self.identities = {}
        # Immutable tombstones prove which original pipe objects this channel
        # retired. Descriptor numbers alone are unsafe because the process may
        # reuse them immediately after close.
        self.retired_identities = {}
        try:
            for name, fd in (("read_fd", read_fd), ("write_fd", write_fd), ("error_fd", error_fd)):
                if fd is None:
                    _require(name == "error_fd", "PROTOCOL_INVALID")
                    continue
                _require(type(fd) is int and fd >= 0, "PROTOCOL_INVALID")
                info = os.fstat(fd)
                access = os.O_WRONLY if name == "write_fd" else os.O_RDONLY
                _require(
                    stat.S_ISFIFO(info.st_mode)
                    and info.st_uid == os.geteuid()
                    and os.readlink("/proc/self/fd/" + str(fd)) == "pipe:[" + str(info.st_ino) + "]"
                    and fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == access,
                    "PRIVATE_PIPE_REQUIRED",
                )
                duplicate = os.dup(fd)
                setattr(self, name, duplicate)  # Track ownership before either flag syscall.
                os.set_inheritable(duplicate, False)
                os.set_blocking(duplicate, False)
                self.identities[duplicate] = (info.st_dev, info.st_ino)
            _require(
                len(set(self.identities.values())) == len(self.identities), "PRIVATE_PIPE_REQUIRED"
            )
            self._check()
        except (KeyboardInterrupt, SystemExit):
            self.close()
            raise
        except Exception:
            self.close()
            raise OperationalError("PRIVATE_PIPE_REQUIRED") from None

    def _fail(self, code):
        raise OperationalError("UNCERTAIN_PUBLICATION" if self.dispatched else code) from None

    def _check(self):
        if self.closed or self.owner != os.getpid():
            self._fail("PROTOCOL_INVALID")
        if time.monotonic() >= self.deadline:
            self._fail("EXCHANGE_EXPIRED")
        try:
            for fd, expected in self.identities.items():
                info = os.fstat(fd)
                _require(
                    (info.st_dev, info.st_ino) == expected
                    and stat.S_ISFIFO(info.st_mode)
                    and not os.get_inheritable(fd)
                    and info.st_uid == os.geteuid()
                    and os.readlink("/proc/self/fd/" + str(fd)) == "pipe:[" + str(info.st_ino) + "]"
                    and fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE
                    == (os.O_WRONLY if fd == self.write_fd else os.O_RDONLY),
                    "PRIVATE_PIPE_REQUIRED",
                )
            if self.guard is not None:
                _require(self.guard() is None, "PROCESS_IDENTITY_CHANGED")
        except Exception:
            self._fail("PROCESS_IDENTITY_CHANGED")

    def mark_dispatched(self):
        self._check()
        self.dispatched = True

    def _wait(self, *, writing=False):
        self._check()
        read = [] if writing else [self.read_fd]
        if self.error_fd is not None:
            read.append(self.error_fd)
        try:
            ready, writable, _ = select.select(
                read,
                [self.write_fd] if writing else [],
                [],
                max(0, self.deadline - time.monotonic()),
            )
            if self.error_fd is not None and self.error_fd in ready:
                chunk = os.read(self.error_fd, min(4097, 4097 - self.error_count))
                self.error_count += len(chunk)
                if self.error_count > 4096:
                    self._fail("DIAGNOSTIC_LIMIT")
                if not chunk:
                    identity = self.identities.pop(self.error_fd)
                    self.retired_identities[self.error_fd] = identity
                    os.close(self.error_fd)
                    self.error_fd = None
            self._check()
            return bool(writable) if writing else self.read_fd in ready
        except OperationalError:
            raise
        except Exception:
            self._fail("TRANSPORT_UNAVAILABLE")

    def _read_exact(self, size):
        _require(type(size) is int and 0 < size <= wire.MAX_RESPONSE_BYTES, "FRAME_LIMIT")
        chunks = bytearray()
        while len(chunks) < size:
            if not self._wait():
                continue
            try:
                chunk = os.read(self.read_fd, size - len(chunks))
            except BlockingIOError:
                continue
            except OSError:
                self._fail("TRANSPORT_UNAVAILABLE")
            if not chunk:
                self._fail("FRAME_INCOMPLETE")
            chunks.extend(chunk)
        self._check()
        return bytes(chunks)

    def _write_exact(self, data):
        view = memoryview(data)
        while view:
            if not self._wait(writing=True):
                continue
            try:
                written = os.write(self.write_fd, view)
            except BlockingIOError:
                continue
            except OSError:
                self._fail("TRANSPORT_UNAVAILABLE")
            if written <= 0:
                self._fail("TRANSPORT_UNAVAILABLE")
            view = view[written:]
        self._check()

    def _frame(self, raw, *, direction):
        self._check()
        if self.position >= len(self.steps):
            self._fail("SECOND_OPERATION_DENIED")
        try:
            parser = (
                wire.parse_handshake_frame_bytes
                if self.exchange == "START"
                else wire.parse_single_rpc_frame_bytes
            )
            message = parser(raw)
            value = message.to_dict()
            _require(
                (value["sequence"], value["kind"], value["direction"], value["exchange_id"])
                == (self.position + 1, *self.steps[self.position], self.exchange_id)
                and value["direction"] == direction,
                "FRAME_ORDER_INVALID",
            )
        except Exception:
            self._fail("FRAME_ORDER_INVALID")
        return message

    def send(self, raw):
        direction = "HOST_TO_EXECUTOR" if self.role == "HOST" else "EXECUTOR_TO_HOST"
        message = self._frame(raw, direction=direction)
        self._write_exact(wire.encode_frame(message))
        self.position += 1

    def receive(self):
        direction = "EXECUTOR_TO_HOST" if self.role == "HOST" else "HOST_TO_EXECUTOR"
        self._check()
        prefix = self._read_exact(4)
        length = int.from_bytes(prefix, "big")
        maximum = (
            wire.MAX_REQUEST_BYTES if direction == "EXECUTOR_TO_HOST" else wire.MAX_RESPONSE_BYTES
        )
        if not 0 < length <= maximum:
            self._fail("FRAME_LIMIT")
        message = self._frame(self._read_exact(length), direction=direction)
        self.position += 1
        return message

    def finish(self):
        self._check()
        if self.position != len(self.steps):
            self._fail("FRAME_INCOMPLETE")
        # EOF proves no second operation, trailing bytes, log lines or extra stdout.
        identity = self.identities.pop(self.write_fd)
        self.retired_identities[self.write_fd] = identity
        fd, self.write_fd = self.write_fd, None
        try:
            os.close(fd)
        except OSError:
            self._fail("TRANSPORT_UNAVAILABLE")
        while True:
            if not self._wait():
                continue
            try:
                extra = os.read(self.read_fd, 1)
            except BlockingIOError:
                continue
            except OSError:
                self._fail("TRANSPORT_UNAVAILABLE")
            if extra:
                self._fail("EXTRA_STDOUT")
            break
        # Monotonic proof that every frame and both EOF boundaries completed.
        # close() alone is not completion and must never grant this state.
        self.finished = True
        self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        for name in ("read_fd", "write_fd", "error_fd"):
            fd = getattr(self, name, None)
            if fd is not None:
                setattr(self, name, None)
                if self.finished and fd in self.identities:
                    self.retired_identities[fd] = self.identities[fd]
                try:
                    os.close(fd)
                except OSError:
                    pass  # Linux close may already have consumed FD; never mask primary outcome.
        self.identities.clear()

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *args):
        self.close()


@dataclass(frozen=True, slots=True)
class ExecutorProcessIdentity:
    """Deployment-qualified immutable executor identity, never parsed from RPC."""

    container_id: str
    identity_digest: str
    deployment_verification_sha256: str

    def __post_init__(self):
        _require(
            all(
                type(v) is str and re.fullmatch(r"[0-9a-f]{64}", v)
                for v in (
                    self.container_id,
                    self.identity_digest,
                    self.deployment_verification_sha256,
                )
            ),
            "PROCESS_IDENTITY_CHANGED",
        )


class FixedDockerAdapter:
    """Fixed host command. Real Docker use awaits authorized deployment/rehearsal.

    W08 supplies the existing management command's typed bridge selector. The
    OS/Docker administrator and independently observed deployment are trusted.
    No caller-selected executable, options, shell, env, mounts or Docker method.
    """

    def __init__(self, *, identity, observer, process_factory=subprocess.Popen):
        _require(type(identity) is ExecutorProcessIdentity, "PROCESS_IDENTITY_CHANGED")
        self.identity, self.observer, self.factory = identity, observer, process_factory
        self.used = False
        self.process = None
        self.check()

    def check(self):
        try:
            _require(self.observer() == self.identity, "PROCESS_IDENTITY_CHANGED")
        except Exception:
            raise OperationalError("PROCESS_IDENTITY_CHANGED") from None

    @property
    def argv(self):
        return (
            "/usr/bin/docker",
            "exec",
            "--interactive",
            self.identity.container_id,
            "/usr/local/bin/python",
            "-B",
            "/app/manage.py",
            "s11_ex10",
            "bridge",
        )

    def open(self, *, exchange, exchange_id, budget=120):
        self.check()
        _require(not self.used, "SECOND_OPERATION_DENIED")
        self.used = True
        try:
            self.process = self.factory(
                self.argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                start_new_session=True,
                env={
                    "PATH": "/usr/bin:/bin",
                    "LC_ALL": "C",
                    "DOCKER_HOST": "unix:///var/run/docker.sock",
                },
                bufsize=0,
            )
            self.check()
            channel = PipeFrameChannel(
                read_fd=self.process.stdout.fileno(),
                write_fd=self.process.stdin.fileno(),
                error_fd=self.process.stderr.fileno(),
                exchange=exchange,
                exchange_id=exchange_id,
                budget=budget,
                process_guard=self.check,
            )
            # Transfer sole pipe ownership; keeping originals would prevent peer EOF.
            self._close_original_pipes()
            return channel
        except Exception:
            self.abort_client()
            # Reaping the owned Docker CLI says nothing about remote executor state.
            raise OperationalError("TRANSPORT_UNAVAILABLE") from None

    def _close_original_pipes(self):
        if self.process is not None:
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

    def finish_client(self):
        """Reap only this client after channel EOF; process exit never authorizes retry."""
        _require(self.process is not None, "TRANSPORT_UNAVAILABLE")
        try:
            code = self.process.wait(timeout=1)
        except Exception:
            self.abort_client()
            raise OperationalError("UNCERTAIN_PUBLICATION") from None
        _require(code == 0, "UNCERTAIN_PUBLICATION")

    def abort_client(self):
        """Bounded cleanup of created local client, never a stopped-writer certificate."""
        if self.process is None:
            return
        self._close_original_pipes()
        try:
            if self.process.poll() is None:
                self.process.terminate()
            self.process.wait(timeout=1)
        except Exception:
            try:
                if self.process.poll() is None:
                    self.process.kill()
                self.process.wait(timeout=1)
            except Exception:
                pass  # Caller retains unknown state; no second executor invocation.


@dataclass(frozen=True, slots=True)
class HostArtifactBinding:
    """Constructor-approved reference and retained root; RPC cannot select either."""

    name: str
    reference: bytes
    store: durable.PrivateStore


class HostArtifactRegistry:
    """Read a fixed allowlist of existing immutable private artifacts.

    Each reference is approved outside the RPC. A digest supplied by a certificate
    cannot admit another path, inode alias or root. No network/object fetch occurs.
    """

    def __init__(self, bindings):
        _require(type(bindings) is tuple and bindings, "HOST_CONTEXT_INVALID")
        approved_bindings = {}
        paths = set()
        for binding in bindings:
            _require(
                type(binding) is HostArtifactBinding
                and type(binding.name) is str
                and binding.name not in approved_bindings
                and type(binding.store) is durable.PrivateStore,
                "HOST_CONTEXT_INVALID",
            )
            reference = sql.parse_canonical(binding.reference)
            _require(set(reference) == {"path", "sha256", "schema_version"}, "HOST_CONTEXT_INVALID")
            sql.validate_scalar(reference["path"], {"type": "locator", "not_null": True})
            sql.validate_scalar(reference["sha256"], {"type": "sha256", "not_null": True})
            _require(reference["path"] not in paths, "ARTIFACT_ALIAS")
            paths.add(reference["path"])
            approved_bindings[binding.name] = binding
        self._bindings = MappingProxyType(approved_bindings)

    @property
    def bindings(self):
        return self._bindings

    def reference(self, name):
        _require(name in self.bindings, "ARTIFACT_UNAPPROVED")
        return sql.parse_canonical(self.bindings[name].reference)

    def read(self, name, *, reference=None):
        _require(name in self.bindings, "ARTIFACT_UNAPPROVED")
        binding = self.bindings[name]
        expected = self.reference(name)
        _require(reference is None or reference == expected, "ARTIFACT_UNAPPROVED")
        try:
            raw = binding.store.read(expected["path"])
            _require(hashlib.sha256(raw).hexdigest() == expected["sha256"], "ARTIFACT_CHANGED")
            return raw
        except OperationalError:
            raise
        except Exception:
            raise OperationalError("ARTIFACT_CHANGED") from None


@dataclass(frozen=True, slots=True)
class QualifiedHostSession:
    """Native B1/B2 values after fixed host approval and deployment observation."""

    profile: profile_codec.RuntimeProfile
    session: session_codec.OfficialSessionV2
    profile_reference: bytes
    header_reference: bytes
    process: ExecutorProcessIdentity
    path_identities: tuple


class NativeSessionQualifier:
    """Requalify actual B1 files and all 27 B2 header fields before authorization.

    Approval verifier and deployment observer are host-installed trust providers,
    supplied once at construction. They are never selected by RPC/certificate
    data. The verifier must independently approve source HEAD/tree/blobs, public
    custody references, session tip, OS writer closure and deployment policy.
    The observer must verify resolved mounts/services/container against that
    approved material and return the exact immutable process identity. This
    module creates no approval, key, genesis or historical certificate.
    """

    def __init__(
        self,
        *,
        registry,
        path_context,
        source_revision,
        launcher_blobs,
        expected_tip,
        expected_count,
        process,
        approval_verifier,
        deployment_observer,
    ):
        _require(
            type(registry) is HostArtifactRegistry
            and type(process) is ExecutorProcessIdentity
            and type(expected_count) is int
            and expected_count >= 0
            and callable(approval_verifier)
            and callable(deployment_observer),
            "HOST_CONTEXT_INVALID",
        )
        self.registry = registry
        self.path_context = sql.canonical_bytes(sql.parse_canonical(path_context))
        self.source_revision = source_revision
        self.launcher_blobs = sql.canonical_bytes(sql.parse_canonical(launcher_blobs))
        self.expected_tip, self.expected_count = expected_tip, expected_count
        self.process = process
        self.approve, self.observe = approval_verifier, deployment_observer

    @staticmethod
    def _tracked_file(path, *, uid, gid):
        fd, components = profile_codec._walk(path, "file")
        try:
            before = os.fstat(fd)
            _require(
                before.st_uid == uid
                and before.st_gid == gid
                and stat.S_IMODE(before.st_mode) in (0o600, 0o644)
                and 0 < before.st_size <= 16 * 1024 * 1024,
                "SOURCE_CHANGED",
            )
            data = bytearray()
            while len(data) <= before.st_size:
                chunk = os.read(fd, min(65536, before.st_size + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            after = os.fstat(fd)
            check, current = profile_codec._walk(path, "file")
            try:

                def stamp(i):
                    return (
                        i.st_dev,
                        i.st_ino,
                        i.st_size,
                        i.st_mtime_ns,
                        i.st_ctime_ns,
                        i.st_uid,
                        i.st_gid,
                        i.st_mode,
                        i.st_nlink,
                    )

                _require(
                    stamp(before) == stamp(after) == stamp(os.fstat(check))
                    and components == current
                    and len(data) == before.st_size,
                    "SOURCE_CHANGED",
                )
            finally:
                os.close(check)
            return bytes(data)
        finally:
            os.close(fd)

    def qualify(self):
        try:
            profile = profile_codec.parse_runtime_profile_bytes(self.registry.read("profile"))
            value = profile.to_dict()
            paths = profile_codec.validate_runtime_profile_paths(
                profile, sql.parse_canonical(self.path_context)
            )
            session = session_codec.load_session_v2(
                self.registry.read("session"),
                profile=profile,
                expected_tip=self.expected_tip,
                expected_count=self.expected_count,
            )
            header = session.header
            projection = self.registry.read("header")
            _require(
                projection == session_codec.canonical_session_header_bytes(header),
                "SESSION_CHANGED",
            )
            _require(
                value["source_revision"] == self.source_revision
                and header["launcher_blobs"] == sql.parse_canonical(self.launcher_blobs),
                "SOURCE_CHANGED",
            )
            approved = {}
            for name in profile_codec.REFERENCE_FIELDS:
                approved[name] = self.registry.read(name, reference=value[name])
            approved["runtime_profile_approval_reference"] = self.registry.read(
                "runtime_profile_approval_reference",
                reference=header["runtime_profile_approval_reference"],
            )
            profile_reference = self.registry.bindings["profile"].reference
            header_reference = self.registry.bindings["header"].reference
            owner = value["expected_owner"]
            directories = {
                value["source_host_root"],
                *(r["path"] for r in value["host_roots"].values()),
                *(m["source_path"] for m in value["masks"] if m["kind"] == "directory"),
            }

            def check_source_files():
                for item in value["compose_files"]:
                    raw = self._tracked_file(item["path"], uid=owner["uid"], gid=owner["gid"])
                    _require(hashlib.sha256(raw).hexdigest() == item["sha256"], "SOURCE_CHANGED")
                actual = []
                for binding in header["path_identity_binding"]:
                    path = binding["path"]
                    kind = (
                        "dev_null"
                        if path == "/dev/null"
                        else ("directory" if path in directories else "file")
                    )
                    fd, _ = profile_codec._walk(path, kind)
                    try:
                        info = os.fstat(fd)
                        actual.append(
                            {
                                "path": path,
                                "device": info.st_dev,
                                "inode": info.st_ino,
                                "uid": info.st_uid,
                                "gid": info.st_gid,
                                "mode": f"{stat.S_IMODE(info.st_mode):04o}",
                            }
                        )
                    finally:
                        os.close(fd)
                _require(actual == header["path_identity_binding"], "PATH_IDENTITY_CHANGED")

            check_source_files()
            try:
                approved_result = self.approve(
                    profile=profile,
                    session=session,
                    approvals=tuple(sorted(approved.items())),
                    path_identities=paths,
                    expected_process=self.process,
                )
            except Exception:
                raise OperationalError("HOST_APPROVAL_REQUIRED") from None
            _require(approved_result is None, "HOST_APPROVAL_REQUIRED")
            try:
                observed = self.observe(profile=profile, session=session)
            except Exception:
                raise OperationalError("PROCESS_IDENTITY_CHANGED") from None
            _require(observed == self.process, "PROCESS_IDENTITY_CHANGED")
            _require(
                profile_codec.validate_runtime_profile_paths(
                    profile, sql.parse_canonical(self.path_context)
                )
                == paths,
                "PATH_IDENTITY_CHANGED",
            )
            check_source_files()
            # Re-read the same immutable references after external qualification.
            for name in (
                "profile",
                "session",
                "header",
                *profile_codec.REFERENCE_FIELDS,
                "runtime_profile_approval_reference",
            ):
                self.registry.read(name)
            return QualifiedHostSession(
                profile, session, profile_reference, header_reference, self.process, paths
            )
        except OperationalError:
            raise
        except Exception:
            raise OperationalError("HOST_CONTEXT_INVALID") from None


def _artifact_reference(path, raw, schema):
    return {"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "schema_version": schema}


def _prefix_open_facts(snapshot, revision, issued_at):
    """Derive facts from an already fully authenticated B3 history prefix.

    Never construct a fake VerifiedSnapshot or interpret latest terminal evidence
    as the original RUN_OPENED. All versions/prefix digests were verified by B3.
    """
    state = history._verified(snapshot)["state"]
    _require(
        type(revision) is int and 0 <= revision <= history.current_revision(snapshot)["revision"],
        "HISTORY_INVALID",
    )
    latest = {}
    for row in sorted(state["official_runs"], key=lambda x: x["run_version"]):
        if row["introduced_revision"] <= revision:
            latest[row["run_id"]] = row
    _require(
        not any(
            r["binding"] or r["lifecycle_state"] in ("OPEN", "ACTIVE", "UNCERTAIN")
            for r in latest.values()
        ),
        "FIRST_VALID_BLOCKED",
    )
    intents = {i["operation_id"]: i for i in state["publication_intents"]}
    _require(
        not any(
            r["disposition"] == "MANUAL_CANCELLED"
            and r["resolved_at"] <= issued_at
            and intents[r["operation_id"]]["base_revision"] <= revision
            for r in state["publication_resolutions"]
        ),
        "FIRST_VALID_BLOCKED",
    )
    events = [e for e in state["authority_events"] if e["revision"] <= revision]
    resolved = {
        e["resolves_event_sequence"] for e in events if e["event_kind"] == "MANUAL_RESOLUTION"
    }
    _require(
        not any(
            e["event_kind"] == "AUTHORITY_UNCERTAIN" and e["event_sequence"] not in resolved
            for e in events
        ),
        "FIRST_VALID_BLOCKED",
    )
    acknowledgments = []
    for run in latest.values():
        if (
            run["lifecycle_state"] == "TERMINAL"
            and run["result"] == "NOT_DEMONSTRATED"
            and run["binding"] is False
            and run["ack_required"] is True
        ):
            acknowledgments.append(
                {
                    "run_id": run["run_id"],
                    "authority_revision": revision,
                    "terminal_evidence_reference": history._reference_row(
                        state, run["evidence_authority_reference_id"]
                    ),
                }
            )
    return sorted(acknowledgments, key=lambda x: x["run_id"])


NORMAL_OPERATIONS = frozenset(
    op.value for op in wire.Operation if op not in wire.RECOVERY_OPERATIONS
)


def _file_identity(value):
    _require(type(value) is durable.FileIdentity, "HOST_POLICY_INVALID")
    return {"device": value.device, "inode": value.inode, "owner": value.owner, "mode": value.mode}


@dataclass(frozen=True, slots=True)
class NativePolicyResourcePlan:
    """Actual fixed resources for the existing fifteen policy facts.

    This is preparation data, not journal, floor, policy or signing admission.
    The real journal still qualifies its registry, local schema and current
    floor. A factory supplies these exact resources and retains this plan.
    """

    authority_id: str
    authority_epoch: int
    registry_binding: bytes
    lock_store: durable.PrivateStore
    lock_name: str
    lock_identity: durable.FileIdentity
    sidecar_store: durable.PrivateStore
    sidecar_name: str
    sidecar_identity: durable.FileIdentity
    ledger_store: durable.PrivateStore
    ledger_name: str
    ledger_identity: durable.FileIdentity
    receipt_store: durable.PrivateStore
    floor: coordination.CoordinationFloor
    transport: object
    bridge_pin: wire.PublicKeyPin
    clock: object
    clock_health: object

    _retained_stores: tuple = dataclass_field(init=False, repr=False, compare=False)
    _floor_pins: tuple = dataclass_field(init=False, repr=False, compare=False)

    def __post_init__(self):
        _require(type(self.floor) is coordination.CoordinationFloor, "HOST_POLICY_INVALID")
        stores = (
            self.lock_store,
            self.sidecar_store,
            self.ledger_store,
            self.receipt_store,
            self.floor.store,
        )
        _require(
            all(type(store) is durable.PrivateStore for store in stores),
            "HOST_POLICY_INVALID",
        )
        object.__setattr__(
            self,
            "_retained_stores",
            tuple(
                (store, store.root, store.identity, store.fd, store.resolver) for store in stores
            ),
        )
        _require(
            type(self.floor.pins) is tuple
            and self.floor.pins
            and all(type(pin) is wire.PublicKeyPin for pin in self.floor.pins),
            "HOST_POLICY_INVALID",
        )
        object.__setattr__(self, "_floor_pins", self.floor.pins)
        self.check()

    @classmethod
    def from_journal(cls, *, policy, journal):
        _require(
            type(policy) is NativeHostPolicy and type(journal) is coordination.CoordinationJournal,
            "HOST_POLICY_INVALID",
        )
        return cls(
            authority_id=journal.authority_id,
            authority_epoch=journal.authority_epoch,
            registry_binding=journal.binding,
            lock_store=journal.lock_store,
            lock_name=journal.lock_name,
            lock_identity=journal.lock_identity,
            sidecar_store=journal.store,
            sidecar_name=journal.name,
            sidecar_identity=journal.identity,
            ledger_store=policy.ledger,
            ledger_name=policy.name,
            ledger_identity=policy.identity,
            receipt_store=journal.receipt_store,
            floor=journal.floor,
            transport=journal.transport,
            bridge_pin=journal.bridge_pin,
            clock=journal.clock,
            clock_health=journal.clock_health,
        )

    def check(self):
        _require(type(self.floor) is coordination.CoordinationFloor, "HOST_POLICY_INVALID")
        _require(self.floor.pins is self._floor_pins, "HOST_POLICY_INVALID")
        stores = (
            self.lock_store,
            self.sidecar_store,
            self.ledger_store,
            self.receipt_store,
            self.floor.store,
        )
        _require(
            len(stores) == len(self._retained_stores)
            and all(
                actual is original
                and actual.root == root
                and actual.identity == identity
                and actual.fd == fd
                and actual.resolver is resolver
                for actual, (original, root, identity, fd, resolver) in zip(
                    stores, self._retained_stores, strict=True
                )
            ),
            "HOST_POLICY_INVALID",
        )
        for store in stores:
            _host_call(store.check, "HOST_POLICY_INVALID")
        _require(
            all(
                type(store) is durable.PrivateStore
                for store in (
                    self.lock_store,
                    self.sidecar_store,
                    self.ledger_store,
                    self.receipt_store,
                )
            )
            and all(
                type(identity) is durable.FileIdentity
                for identity in (self.lock_identity, self.sidecar_identity, self.ledger_identity)
            )
            and type(self.floor) is coordination.CoordinationFloor
            and type(self.bridge_pin) is wire.PublicKeyPin
            and self.transport is not None
            and callable(self.clock)
            and callable(self.clock_health)
            and type(self.registry_binding) is bytes,
            "HOST_POLICY_INVALID",
        )
        _host_call(
            lambda: sql.validate_scalar(self.authority_id, {"type": "uuid", "not_null": True}),
            "HOST_POLICY_INVALID",
        )
        _host_call(
            lambda: sql.validate_scalar(
                self.authority_epoch, {"type": "positive_int64", "not_null": True}
            ),
            "HOST_POLICY_INVALID",
        )
        _host_call(lambda: sql.parse_canonical(self.registry_binding), "HOST_POLICY_INVALID")
        _require(
            (self.floor.authority_id, self.floor.authority_epoch)
            == (self.authority_id, self.authority_epoch)
            and len(
                {
                    (store.identity.device, store.identity.inode)
                    for store in (
                        self.lock_store,
                        self.sidecar_store,
                        self.receipt_store,
                        self.floor.store,
                    )
                }
            )
            == 4,
            "HOST_POLICY_INVALID",
        )
        for store, name, identity in (
            (self.lock_store, self.lock_name, self.lock_identity),
            (self.sidecar_store, self.sidecar_name, self.sidecar_identity),
            (self.ledger_store, self.ledger_name, self.ledger_identity),
        ):
            _require(type(name) is str and bool(name), "HOST_POLICY_INVALID")
            fd = _host_call(
                lambda store=store, name=name, identity=identity: store.open(
                    name, expected=identity
                ),
                "HOST_POLICY_INVALID",
            )
            os.close(fd)
        for store in (self.receipt_store, self.floor.store):
            _host_call(store.check, "HOST_POLICY_INVALID")
        return None

    def matches(self, *, policy, journal):
        _require(
            type(policy) is NativeHostPolicy and type(journal) is coordination.CoordinationJournal,
            "HOST_POLICY_INVALID",
        )
        self.check()
        _require(
            self.ledger_store is policy.ledger
            and self.ledger_name == policy.name
            and self.ledger_identity == policy.identity
            and self.lock_store is journal.lock_store
            and self.lock_name == journal.lock_name
            and self.lock_identity == journal.lock_identity
            and self.sidecar_store is journal.store
            and self.sidecar_name == journal.name
            and self.sidecar_identity == journal.identity
            and self.receipt_store is journal.receipt_store
            and self.floor is journal.floor
            and self.transport is journal.transport
            and self.bridge_pin is journal.bridge_pin
            and self.clock is journal.clock
            and self.clock_health is journal.clock_health
            and self.registry_binding == journal.binding
            and (self.authority_id, self.authority_epoch)
            == (journal.authority_id, journal.authority_epoch),
            "HOST_POLICY_INVALID",
        )
        return None


class NativeHostPolicy:
    """An existing externally approved policy, with actual fixed host identities.

    No policy instance, key or approval is created here. The root-binding digest
    is an independently approved host input; the signed approval verifier binds
    it to the actual B1 paths and full native context. The design does not define
    a new digest preimage, so this class does not invent one from request data.
    Envelope qualification is a fixed B5 provider (W07), never an RPC selector.
    """

    def __init__(
        self,
        *,
        registry,
        ledger_store,
        ledger_name,
        ledger_identity,
        session_qualifier,
        approved_root_binding_sha256,
        approval_verifier,
        envelope_verifier,
        required_preflight_checks,
        authorization_binding=None,
        resource_plan=None,
    ):
        _require(
            type(session_qualifier) is NativeSessionQualifier
            and type(registry) is HostArtifactRegistry
            and type(ledger_store) is durable.PrivateStore
            and type(ledger_identity) is durable.FileIdentity
            and callable(approval_verifier)
            and callable(envelope_verifier)
            and type(required_preflight_checks) is frozenset
            and required_preflight_checks,
            "HOST_POLICY_INVALID",
        )
        sql.validate_scalar(approved_root_binding_sha256, {"type": "sha256", "not_null": True})
        self.registry, self.ledger, self.name, self.identity = (
            registry,
            ledger_store,
            ledger_name,
            ledger_identity,
        )
        self.root_binding = approved_root_binding_sha256
        self.sessions = session_qualifier
        self.approve, self.envelope = approval_verifier, envelope_verifier
        self.checks = required_preflight_checks
        _require(
            authorization_binding is None
            or type(authorization_binding) is HostArtifactBinding
            and authorization_binding.name == "authorization",
            "HOST_POLICY_INVALID",
        )
        # None preserves the originally approved alias for an existing context.
        # An independently installed binding never falls back to that alias.
        self._authorization_registry = (
            None
            if authorization_binding is None
            else HostArtifactRegistry((authorization_binding,))
        )
        _require(
            resource_plan is None or type(resource_plan) is NativePolicyResourcePlan,
            "HOST_POLICY_INVALID",
        )
        if resource_plan is not None:
            resource_plan.check()
        self._resource_plan = resource_plan
        original_authorization = self._authorization_registry
        original_resources = resource_plan

        def authorization_guard():
            _require(
                self._authorization_registry is original_authorization
                and self._resource_plan is original_resources
                and self._authorization_guard is authorization_guard,
                "HOST_POLICY_INVALID",
            )
            if original_resources is not None:
                _require(
                    original_resources.ledger_store is self.ledger
                    and original_resources.ledger_name == self.name
                    and original_resources.ledger_identity == self.identity,
                    "HOST_POLICY_INVALID",
                )
                original_resources.check()
            return original_authorization

        self._authorization_guard = authorization_guard

    @property
    def operator_authorization_reference(self):
        registry = self._authorization_guard()
        if registry is None:
            return self.reference
        return registry.reference("authorization")

    @property
    def reference(self):
        return self.registry.reference("policy")

    def postcheck(self, native):
        authorization_guard = self._authorization_guard
        authorization_guard()
        current = self.sessions.qualify()
        _require(
            (
                current.profile._bytes,
                current.session._bytes,
                current.profile_reference,
                current.header_reference,
                current.process,
                current.path_identities,
            )
            == (
                native.profile._bytes,
                native.session._bytes,
                native.profile_reference,
                native.header_reference,
                native.process,
                native.path_identities,
            ),
            "HOST_CONTEXT_INVALID",
        )
        authorization_guard()

    def facts(self, *, native, journal):
        _require(type(journal) is coordination.CoordinationJournal, "HOST_POLICY_INVALID")
        self._authorization_guard()
        resources = self._resource_plan
        if resources is None:
            resources = NativePolicyResourcePlan.from_journal(policy=self, journal=journal)
        resources.matches(policy=self, journal=journal)
        facts = self.facts_from_resources(native=native, resources=resources)
        resources.matches(policy=self, journal=journal)
        return facts

    def facts_from_resources(self, *, native, resources):
        """Prepare exact existing facts; real journal/approval admission remains mandatory."""
        authorization_guard = self._authorization_guard
        authorization_guard()
        _require(
            type(native) is QualifiedHostSession
            and type(resources) is NativePolicyResourcePlan
            and (self._resource_plan is None or resources is self._resource_plan)
            and resources.ledger_store is self.ledger
            and resources.ledger_name == self.name
            and resources.ledger_identity == self.identity,
            "HOST_POLICY_INVALID",
        )
        resources.check()
        profile = native.profile.to_dict()
        binding = self.registry.read("coordinator_registry")
        _require(binding == resources.registry_binding, "HOST_POLICY_INVALID")
        authorization_guard()
        facts = {
            "namespace": "S11C3-EX10",
            "authority_id": resources.authority_id,
            "authority_epoch": resources.authority_epoch,
            "descriptor_reference": profile["historical_lifecycle_authority_reference"],
            "approved_host_identity_reference": self.registry.reference("host_identity"),
            "coordinator_registry_reference": self.registry.reference("coordinator_registry"),
            "lock_file_identity": _file_identity(resources.lock_identity),
            "sidecar_identity": _file_identity(resources.sidecar_identity),
            "ledger_identity": _file_identity(self.identity),
            "bridge_key_public_approval_reference": self.registry.reference("bridge_approval"),
            "independent_witness_transport_reference": self.registry.reference("witness_transport"),
            "writer_closure_reference": self.registry.reference("writer_closure"),
            "approved_executor_entrypoint_identity": {
                "container_id": native.process.container_id,
                "identity_digest": native.process.identity_digest,
                "argv": ["/usr/local/bin/python", "-B", "/app/manage.py", "s11_ex10", "bridge"],
            },
            "normal_operation_allowlist": sorted(NORMAL_OPERATIONS),
            "recovery_operator_approval_policy": self.registry.reference("recovery_policy"),
        }

        resources.check()
        authorization_guard()
        return facts

    def qualify(self, *, native, journal):
        authorization_guard = self._authorization_guard
        authorization_registry = authorization_guard()
        facts = self.facts(native=native, journal=journal)
        approvals = tuple(
            (name, self.registry.read(name))
            for name in (
                "host_identity",
                "bridge_approval",
                "witness_transport",
                "writer_closure",
                "recovery_policy",
            )
        )
        raw = self.registry.read("policy")
        _require(sql.parse_canonical(raw) == facts, "HOST_POLICY_INVALID")
        authorization = (
            {}
            if authorization_registry is None
            else {
                "canonical_authorization": authorization_registry.read("authorization"),
                "authorization_reference": sql.canonical_bytes(
                    authorization_registry.reference("authorization")
                ),
            }
        )
        result = _host_call(
            self.approve,
            "HOST_APPROVAL_REQUIRED",
            **authorization,
            canonical_policy=raw,
            reference=sql.canonical_bytes(self.reference),
            native=native,
            journal=journal,
            actual_registry_binding=journal.binding,
            actual_approvals=approvals,
            actual_root_binding_sha256=self.root_binding,
        )
        _require(result is None, "HOST_APPROVAL_REQUIRED")
        self.postcheck(native)
        _require(
            self.facts(native=native, journal=journal) == facts
            and self.registry.read("policy") == raw,
            "HOST_POLICY_INVALID",
        )
        for name, expected in approvals:
            _require(self.registry.read(name) == expected, "HOST_POLICY_INVALID")
        _require(authorization_guard() is authorization_registry, "HOST_POLICY_INVALID")
        if authorization_registry is not None:
            _require(
                authorization_registry.read("authorization")
                == authorization["canonical_authorization"]
                and sql.canonical_bytes(self.operator_authorization_reference)
                == authorization["authorization_reference"],
                "HOST_POLICY_INVALID",
            )
        authorization_guard()
        if self._resource_plan is not None:
            self._resource_plan.matches(policy=self, journal=journal)
        return None

    def reserve_references(self, *, registry, native, snapshot, issued_at, live_clock=None):
        authorization_guard = self._authorization_guard
        authorization_guard()
        _require(type(registry) is HostArtifactRegistry, "HOST_CONTEXT_INVALID")
        profile = native.profile.to_dict()
        envelope_raw = registry.read("envelope")
        result = _host_call(
            self.envelope,
            "ENVELOPE_INVALID",
            canonical_envelope=envelope_raw,
            reference=sql.canonical_bytes(registry.reference("envelope")),
            native=native,
            snapshot=snapshot,
            issued_at=issued_at,
        )
        _require(result is None and registry.read("envelope") == envelope_raw, "ENVELOPE_INVALID")
        self.postcheck(native)
        preflight_raw = registry.read("preflight")
        preflight = sql.parse_canonical(preflight_raw)
        _require(
            set(preflight) == evidence.ARTIFACT_FIELDS["preflight_reference"]
            and preflight["schema_version"] == evidence.ARTIFACT_SCHEMAS["preflight_reference"],
            "PREFLIGHT_INVALID",
        )
        expected = {
            "scenario_id": "EX-10",
            "protocol_version": evidence.ex10_load.PROTOCOL_ID,
            "methodology_version": evidence.ex10_load.METHODOLOGY_VERSION,
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
            "git_sha": profile["source_revision"],
            "expected_git_sha": profile["source_revision"],
        }
        _require(
            all(type(preflight[k]) is type(v) and preflight[k] == v for k, v in expected.items()),
            "PREFLIGHT_INVALID",
        )
        checks = preflight["checks"]
        _require(
            type(checks) is list
            and checks
            and all(
                type(c) is dict
                and set(c) == {"check", "result", "expected", "observed_sanitized", "detail_code"}
                and type(c["check"]) is str
                and c["result"] == "PASS"
                for c in checks
            ),
            "PREFLIGHT_INVALID",
        )
        names = [c["check"] for c in checks]
        _require(
            len(names) == len(set(names))
            and self.checks <= set(names)
            and any(n.startswith("ready_state:") for n in names)
            and 0
            <= (
                (wire._time(issued_at) if live_clock is None else live_clock())
                - wire._time(preflight["checked_at"])
            ).total_seconds()
            <= 900,
            "PREFLIGHT_INVALID",
        )
        authorization_guard()
        return {
            "deployment_verification_reference": profile["deployment_verification_reference"],
            "descriptor_reference": profile["historical_lifecycle_authority_reference"],
            "operational_envelope_reference": registry.reference("envelope"),
            "preflight_reference": registry.reference("preflight"),
            "profile_reference": sql.parse_canonical(native.profile_reference),
            "session_reference": sql.parse_canonical(native.header_reference),
        }


class NativeGovernanceQualifier:
    """All 31 GOV claims reconstructed from native host sources, never copied from GOV.

    Registry, holder key, pin and immutable artifact namespace are constructor
    fixed. The host supplies the actual verified journal/snapshot. Replaying a
    committed certificate evaluates its original signed RUN_OPENED timestamp;
    expiry today does not revoke previously valid proofs. No extra GOV signature,
    trust root, request field or methodological rule is introduced.
    """

    def __init__(
        self,
        *,
        session_qualifier,
        registry,
        certificate_store,
        bridge_pin,
        holder_public_key,
        reservation_id,
        executor_fingerprint,
        required_preflight_checks,
        protocol,
        methodology,
        host_policy,
    ):
        _require(
            type(host_policy) is NativeHostPolicy
            and type(session_qualifier) is NativeSessionQualifier
            and type(registry) is HostArtifactRegistry
            and type(certificate_store) is durable.PrivateStore
            and type(bridge_pin) is wire.PublicKeyPin
            and type(required_preflight_checks) is frozenset
            and required_preflight_checks
            and protocol == evidence.ex10_load.PROTOCOL_ID
            and methodology == evidence.ex10_load.METHODOLOGY_VERSION,
            "HOST_CONTEXT_INVALID",
        )
        self.sessions, self.registry, self.store = session_qualifier, registry, certificate_store
        self.pin, self.holder = bridge_pin, holder_public_key
        self.reservation_id = reservation_id
        self.fingerprint, self.checks = executor_fingerprint, required_preflight_checks
        self.protocol, self.methodology = protocol, methodology
        self.policy = host_policy

    def _request(self, raw, *, operation, native, record, revision, issued_at):
        value = wire.parse_request_bytes(raw).to_dict()
        authorization = self.registry.reference("authorization")
        self.registry.read("authorization")
        context = wire.RequestContext(
            request_id=value["request_id"],
            nonce=value["nonce"],
            exchange_id=value["exchange_id"],
            operation=operation,
            session_id=native.session.header["session_id"],
            session_header_sha256=sql.parse_canonical(native.header_reference)["sha256"],
            runtime_profile_sha256=sql.parse_canonical(native.profile_reference)["sha256"],
            authority_id=record["authority_id"],
            authority_epoch=record["authority_epoch"],
            authority_revision=revision,
            source_revision=native.profile.to_dict()["source_revision"],
            project=native.profile.to_dict()["project"],
            authorization_reference=sql.canonical_bytes(authorization),
            deployment_verification_sha256=native.process.deployment_verification_sha256,
            container_identity_digest=native.process.identity_digest,
            expected_run_id=record["run_id"],
            reservation_id=None if operation == "reserve-start" else record["reservation_id"],
            reservation_digest=None
            if operation == "reserve-start"
            else record["reservation_digest"],
            fencing_token=None if operation == "reserve-start" else record["fencing_token"],
            holder_public_key_hex=self.holder.public_bytes_raw().hex(),
            expected_payload=sql.canonical_bytes(value["payload"]),
            now=issued_at,
        )
        # Payload is independently compared to journal/base/evidence below. Its
        # signature is verified here with a separately admitted process key.
        return wire.verify_request(
            raw, expected_context=context, bridge_pin=self.pin, holder_public_key=self.holder
        )

    def _expected(self, *, snapshot, journal, issued_at):
        _require(
            type(journal) is coordination.CoordinationJournal and journal.bridge_pin == self.pin,
            "HOST_CONTEXT_INVALID",
        )
        native = self.sessions.qualify()
        profile, header = native.profile.to_dict(), native.session.header
        self.policy.qualify(native=native, journal=journal)
        now = wire._time(issued_at)
        state = journal.read()
        record_raw = self.registry.read("record")
        record = wire.qualify_reservation(record_raw).to_dict()
        _require(
            record["reservation_id"] == self.reservation_id
            and record["state"] == "PENDING"
            and record["reservation_receipt_reference"] is not None
            and any(e == record for e in state["events"]),
            "RESERVATION_CONTEXT_INVALID",
        )
        before = record["previous_authority_revision"]
        material = history._verified(snapshot)
        actual = material["state"]
        metadata = actual["metadata"][0]
        _require(
            (record["authority_id"], record["authority_epoch"], record["namespace"])
            == (metadata["authority_id"], metadata["epoch"], metadata["namespace"]),
            "HISTORY_INVALID",
        )
        descriptor_ref = self.registry.reference("descriptor")
        _require(
            descriptor_ref == material["descriptor_reference"]
            and sql.parse_canonical(self.registry.read("descriptor")) == material["descriptor"]
            and profile["historical_lifecycle_authority_reference"] == descriptor_ref
            and header["historical_lifecycle_authority_reference"] == descriptor_ref,
            "HISTORY_INVALID",
        )
        receipts = history._receipts(actual)
        _require(0 <= before < len(receipts), "HISTORY_INVALID")
        receipt = receipts[before]
        receipt_raw = self.registry.read("base_receipt")
        _require(
            receipt_raw == sql.canonical_bytes(receipt)
            and (record["previous_ledger_digest"], record["previous_witness_digest"])
            == (receipt["ledger_digest"], sql.digest(receipt)),
            "HISTORY_INVALID",
        )
        head = sql.parse_canonical(self.registry.read("base_head"))
        from . import ex10_authority_witness as authority_witness

        pins = [authority_witness.TrustPin(raw) for raw in snapshot.canonical_trust_pins]
        selected = [
            p
            for p in pins
            if (p.record["key_id"], p.record["key_epoch"]) == (head["key_id"], head["key_epoch"])
        ]
        _require(len(selected) == 1, "HOST_APPROVAL_REQUIRED")
        authority_witness._verify_current_head(
            head,
            selected[0],
            challenge=head["challenge"],
            now=issued_at,
            request_elapsed_seconds=0,
            clock_healthy=True,
        )
        _require(
            (
                head["authority_id"],
                head["authority_epoch"],
                head["revision"],
                head["ledger_digest"],
                head["witness_digest"],
            )
            == (
                metadata["authority_id"],
                metadata["epoch"],
                before,
                receipt["ledger_digest"],
                sql.digest(receipt),
            ),
            "HISTORY_INVALID",
        )
        reserve_raw = self.registry.read("reserve_request")
        reserve = self._request(
            reserve_raw,
            operation="reserve-start",
            native=native,
            record=record,
            revision=before,
            issued_at=now,
        )
        commit_raw = self.registry.read("commit_request")
        commit = self._request(
            commit_raw,
            operation="commit-open",
            native=native,
            record=record,
            revision=before,
            issued_at=now,
        )
        request, committing = reserve.request.to_dict(), commit.request.to_dict()
        _require(
            request["request_id"] != committing["request_id"]
            and request["nonce"] != committing["nonce"]
            and request["capability"]["capability_id"] != committing["capability"]["capability_id"]
            and request["exchange_id"] == committing["exchange_id"]
            and request["request_id"] == record["request_id"]
            and request["nonce"] == record["nonce"]
            and request["request_digest"] == record["request_digest"]
            and state["claims"][request["request_id"]]["canonical"] == reserve_raw,
            "REQUEST_CONTEXT_INVALID",
        )
        identity = wire.qualify_run_identity(record["run_identity"])
        _require(
            identity == request["payload"]["run_identity"] == committing["payload"]["run_identity"]
            and identity["git_sha"] == profile["source_revision"]
            and record["session_id"] == header["session_id"]
            and record["project"] == profile["project"]
            and record["runtime_profile_sha256"]
            == sql.parse_canonical(native.profile_reference)["sha256"]
            and record["session_header_sha256"]
            == sql.parse_canonical(native.header_reference)["sha256"],
            "RESERVATION_CONTEXT_INVALID",
        )
        _require(
            self.registry.reference("authorization") == self.policy.operator_authorization_reference
            and record["operator_authorization_reference"]
            == self.policy.operator_authorization_reference
            and record["coordination_policy_reference"] == self.policy.reference
            and record["host_root_binding_sha256"] == self.policy.root_binding
            and record["deployment_verification_reference"]
            == profile["deployment_verification_reference"],
            "HOST_POLICY_INVALID",
        )
        references = self.policy.reserve_references(
            registry=self.registry, native=native, snapshot=snapshot, issued_at=issued_at
        )
        _require(
            all(request["payload"][k] == v for k, v in references.items()),
            "REQUEST_CONTEXT_INVALID",
        )
        ack = _prefix_open_facts(snapshot, before, issued_at)
        _require(
            ack == request["payload"]["acknowledgments"]
            and sql.digest(ack) == record["acknowledgment_digest"],
            "ACK_INVALID",
        )
        base = {
            "authority_id": metadata["authority_id"],
            "authority_epoch": metadata["epoch"],
            "revision": before,
            "ledger_digest": receipt["ledger_digest"],
            "witness_digest": sql.digest(receipt),
            "receipt_reference": self.registry.reference("base_receipt"),
        }
        _require(
            request["payload"]["expected_head"] == committing["payload"]["expected_head"] == base,
            "HISTORY_INVALID",
        )
        grant_raw = self.registry.read(
            "reservation_receipt", reference=record["reservation_receipt_reference"]
        )
        grant = wire.parse_reservation_receipt_bytes(grant_raw).to_dict()
        _require(
            any(r == grant for r in state["receipts"])
            and grant["transition"] == "RESERVE"
            and grant["state"] == "PENDING"
            and grant["operation_id"] == request["request_id"]
            and (
                grant["reservation_id"],
                grant["reservation_digest"],
                grant["fencing_token"],
                grant["base_authority_revision"],
                grant["base_authority_witness_digest"],
            )
            == (
                record["reservation_id"],
                record["reservation_digest"],
                record["fencing_token"],
                before,
                sql.digest(receipt),
            ),
            "RESERVATION_CONTEXT_INVALID",
        )
        evd_raw = self.registry.read("evidence")
        evd = evidence.parse_open_evidence_bytes(evd_raw).to_dict()
        preflight_raw = self.registry.read("preflight")
        preflight_ref = self.registry.reference("preflight")
        _require(
            preflight_ref["sha256"] == evd["preflight_reference"]["sha256"]
            and preflight_ref["schema_version"] == evd["preflight_reference"]["schema_version"],
            "HOST_CONTEXT_INVALID",
        )
        permit_expected = {
            "namespace": "S11C3-EX10",
            "authority_id": metadata["authority_id"],
            "authority_epoch": metadata["epoch"],
            "authorization_reference": self.registry.reference("authorization"),
            "base_authority_revision": before,
            "expected_open_revision": before + 1,
            "bridge_key_id": self.pin.key_id,
            "bridge_key_epoch": self.pin.key_epoch,
            "container_identity_digest": native.process.identity_digest,
            "deployment_verification_sha256": native.process.deployment_verification_sha256,
            "exchange_id": request["exchange_id"],
            "holder_public_key_hex": self.holder.public_bytes_raw().hex(),
            "attempt_nonce": record["nonce"],
            "acknowledgment_digest": sql.digest(ack),
            "preflight_reference": preflight_ref,
            "reserve_request_id": request["request_id"],
            "reserve_request_digest": request["request_digest"],
            **{
                k: record[k]
                for k in (
                    "fencing_token",
                    "project",
                    "reservation_digest",
                    "reservation_id",
                    "reservation_receipt_reference",
                    "run_identity",
                    "runtime_profile_sha256",
                    "session_header_sha256",
                    "session_id",
                    "source_revision",
                )
            },
        }
        permit = wire.verify_open_one(
            self.registry.read("permit"),
            expected_context=wire.PermitContext(
                sql.canonical_bytes(permit_expected), wire._time(record["freshness_deadline"]), now
            ),
            bridge_pin=self.pin,
            holder_public_key=self.holder,
        )
        permit_value = permit.to_dict()
        expected_payload = {
            **{
                k: record[k]
                for k in ("reservation_id", "reservation_digest", "fencing_token", "run_identity")
            },
            "reserve_request_id": request["request_id"],
            "open_permit_digest": permit_value["permit_digest"],
            "reservation_receipt_reference": record["reservation_receipt_reference"],
            "expected_head": base,
            "open_evidence": evd,
        }
        _require(committing["payload"] == expected_payload, "REQUEST_CONTEXT_INVALID")
        root = profile["host_roots"]["evidence"]["path"]
        artifacts = tuple(
            evidence.ArtifactExpectation(
                field, root + "/runs/" + identity["run_id"] + "/metadata/EX-10/" + filename, 1048576
            )
            for field, filename in zip(
                evidence.REFERENCE_FIELDS,
                (
                    "run-manifest.initial.json",
                    "ex10-start.json",
                    "ex10-preflight.json",
                    "ex10-plan.json",
                ),
                strict=True,
            )
        )
        # W08 exposes these exact existing-artifact filenames from the executor.
        evd_context = evidence.OpenEvidenceContext(
            request["request_id"],
            record["reservation_id"],
            record["fencing_token"],
            permit_value["permit_digest"],
            record["runtime_profile_sha256"],
            record["session_header_sha256"],
            sql.canonical_bytes(identity),
            wire._time(permit_value["issued_at"]),
            wire._time(permit_value["expires_at"]),
            now,
            self.fingerprint,
            self.checks,
            artifacts,
        )
        with evidence.ConfinedArtifactResolver(
            root, expected_owner=profile["expected_owner"]["uid"]
        ) as resolver:
            qualified_evidence = evidence.verify_open_evidence(
                evd_raw,
                expected_context=evd_context,
                executor_public_key=self.holder,
                artifact_resolver=resolver,
            )
        _require(
            next(
                a.canonical
                for a in qualified_evidence.artifacts
                if a.field == "preflight_reference"
            )
            == preflight_raw,
            "HOST_CONTEXT_INVALID",
        )
        _require(wire._time(evd["opened_at"]) <= now, "HOST_CONTEXT_INVALID")
        value = {
            "schema_version": governance.SCHEMA_VERSION,
            "kind": "GOVERNANCE_CERTIFICATE",
            "issuer": "HOST_GOVERNANCE_AUTHORITY",
            "issued_at": issued_at,
            "namespace": "S11C3-EX10",
            "authority_id": metadata["authority_id"],
            "authority_epoch": metadata["epoch"],
            "authority_descriptor_reference": descriptor_ref,
            "authority_revision_before": before,
            "authority_revision_after": before + 1,
            "b3_base_witness": base,
            "b3_base_current_head_reference": self.registry.reference("base_head"),
            "reservation": {
                "reservation_id": record["reservation_id"],
                "reservation_digest": record["reservation_digest"],
                "record_reference": self.registry.reference("record"),
                "record_sequence": record["record_sequence"],
                "fencing_token": record["fencing_token"],
                "state_at_open": "PENDING",
                "witness_receipt_reference": record["reservation_receipt_reference"],
                "witness_sequence": grant["sequence"],
            },
            "session": {
                "session_id": header["session_id"],
                "schema_version": session_codec.SESSION_V2_SCHEMA,
                "header_sha256": sql.parse_canonical(native.header_reference)["sha256"],
                "header_reference": sql.parse_canonical(native.header_reference),
            },
            "runtime_profile": {
                "profile_id": profile["profile_id"],
                "schema_version": profile_codec.SCHEMA_VERSION,
                "sha256": sql.parse_canonical(native.profile_reference)["sha256"],
                "reference": sql.parse_canonical(native.profile_reference),
            },
            "source_revision": profile["source_revision"],
            "project": profile["project"],
            "protocol": self.protocol,
            "methodology": self.methodology,
            "run_id": identity["run_id"],
            "run_identity_digest": identity["identity_digest"],
            "open_evidence_reference": self.registry.reference("evidence"),
            "acknowledgments": ack,
            "acknowledgment_digest": sql.digest(ack),
            "first_valid_status": "NONE",
            "reserve_request_id": request["request_id"],
            "reserve_request_digest": request["request_digest"],
            "commit_request_id": committing["request_id"],
            "commit_request_digest": committing["request_digest"],
            "open_one_permit": {
                "permit_id": permit_value["permit_id"],
                "permit_digest": permit_value["permit_digest"],
                "reference": self.registry.reference("permit"),
            },
        }
        self.policy.postcheck(native)
        value["certificate_id"] = governance.certificate_identity(value)
        cert = governance.validate_governance_certificate(value)
        _require(len(value) == 31, "HOST_CONTEXT_INVALID")
        return cert, qualified_evidence

    def live_window(self, *, opening, issued_at):
        """Current authorization before local seal/sole remote dispatch only.

        Historical replay never calls this; after a successful remote CAS the
        host must finish the already accepted local publication and floors.
        """
        opening.check()
        guard = getattr(opening, "_bridge_exchange_guard", None)
        if guard is not None:
            _require(callable(guard), "HOST_CONTEXT_INVALID")
            guard()  # Exact absolute exchange deadline immediately before local seal/remote CAS.
        current = opening.journal._now()  # Host fixed clock-health provider.
        _require(
            0 <= (current - wire._time(issued_at)).total_seconds() <= 6
            and current < wire._time(opening.record["freshness_deadline"]),
            "HOST_CONTEXT_INVALID",
        )
        try:
            permit = wire.parse_open_one_bytes(self.registry.read("permit")).to_dict()
            wire._fresh(permit, current, maximum=120, code="CAPABILITY_EXPIRED")
            for name in ("reserve_request", "commit_request"):
                request = wire.parse_request_bytes(self.registry.read(name)).to_dict()
                wire._fresh(request, current, maximum=120, code="REQUEST_EXPIRED")
                wire._fresh(request["capability"], current, maximum=120, code="CAPABILITY_EXPIRED")
        except Exception:
            raise OperationalError("HOST_CONTEXT_INVALID") from None
        if guard is not None:
            guard()  # Recheck after clock-health and all reads, before the effect.

    def seal(self, *, opening, authenticated_request, issued_at):
        _require(type(opening) is coordination.OpeningAttempt, "HOST_CONTEXT_INVALID")
        opening.check()
        _require(
            opening.record["reservation_id"] == self.reservation_id
            and authenticated_request.request.canonical == self.registry.read("commit_request"),
            "HOST_CONTEXT_INVALID",
        )
        _require(
            opening.record["state"] == "PENDING"
            and opening.open_result is None
            and sql.canonical_bytes(opening.record) == self.registry.read("record"),
            "RESERVATION_CONTEXT_INVALID",
        )
        _require(
            0 <= (opening.journal._now() - wire._time(issued_at)).total_seconds() <= 6,
            "HOST_CONTEXT_INVALID",
        )
        cert, accepted = self._expected(
            snapshot=opening.snapshot, journal=opening.journal, issued_at=issued_at
        )
        path = self.store.root + "/" + cert.value["certificate_id"] + ".json"
        row = {
            **_artifact_reference(path, cert.canonical_bytes, governance.SCHEMA_VERSION),
            "kind": "GOVERNANCE",
            "introduced_revision": cert.value["authority_revision_after"],
        }
        row["reference_id"] = sql.digest(
            {k: row[k] for k in ("path", "sha256", "schema_version", "kind")}
        )
        governance.validate_governance_reference(row, cert, artifact_namespace=self.store.root)
        self.live_window(opening=opening, issued_at=issued_at)
        self.store.create_exact(path, cert.canonical_bytes)
        self.live_window(opening=opening, issued_at=issued_at)
        return cert, row, accepted

    def replay(self, *, snapshot, journal, original_run, original_event, reference):
        _require(
            original_run["provenance_model"] == "PROFILE_V2"
            and original_run["run_version"] == 0
            and original_event["event_kind"] == "RUN_OPENED"
            and original_run["run_id"] == original_event["run_id"]
            and original_run["evidence_authority_reference_id"]
            == original_event["evidence_reference_id"]
            == reference["reference_id"],
            "HISTORICAL_GOV_INVALID",
        )
        state = history._verified(snapshot)["state"]
        _require(
            original_run in state["official_runs"]
            and original_event in state["authority_events"]
            and reference in state["artifact_references"],
            "HISTORICAL_GOV_INVALID",
        )
        cert, accepted = self._expected(
            snapshot=snapshot, journal=journal, issued_at=original_event["occurred_at"]
        )
        record = wire.qualify_reservation(self.registry.read("record")).to_dict()
        _require(
            all(
                original_run[k] == cert.value[k]
                for k in ("source_revision", "protocol", "methodology")
            )
            and original_run["created_at"] == record["run_identity"]["started_at"]
            and original_run["opened_at"] == accepted.receipt.to_dict()["opened_at"]
            and original_run["mode"] == "OFFICIAL"
            and original_run["lifecycle_state"] == "OPEN"
            and original_run["phase"] == "OPENED"
            and original_run["result"] is None
            and original_run["binding"] is False
            and original_run["ack_required"] is False
            and original_run["previous_run_version"] is None
            and original_run["finalized_at"] is None
            and original_run["validity_classification"] == "PENDING",
            "HISTORICAL_GOV_INVALID",
        )
        for kind, key, source in (
            ("PROFILE", "profile_reference_id", cert.value["runtime_profile"]["reference"]),
            ("SESSION", "session_reference_id", cert.value["session"]["header_reference"]),
        ):
            row = next(
                (r for r in state["artifact_references"] if r["reference_id"] == original_run[key]),
                None,
            )
            _require(
                row is not None
                and row["kind"] == kind
                and {k: row[k] for k in ("path", "sha256", "schema_version")} == source
                and row["introduced_revision"] <= original_run["introduced_revision"],
                "HISTORICAL_GOV_INVALID",
            )
        revision = next(
            r
            for r in state["history_revisions"]
            if r["revision"] == original_run["introduced_revision"]
        )
        _require(
            revision["operation_id"] == cert.value["commit_request_id"], "HISTORICAL_GOV_INVALID"
        )
        governance.validate_governance_reference(
            reference, cert, artifact_namespace=self.store.root
        )
        _require(
            self.store.read(reference["path"]) == cert.canonical_bytes
            and cert.value["run_id"] == original_run["run_id"]
            and cert.value["authority_revision_after"]
            == original_run["introduced_revision"]
            == original_event["revision"]
            and original_run["created_at"] <= original_run["opened_at"] <= cert.value["issued_at"],
            "HISTORICAL_GOV_INVALID",
        )
        return cert


class _NativePublicationTransport:
    """One fixed publication leg with live admission directly before CAS."""

    def __init__(self, transport, qualifier, opening, certificate):
        self.transport, self.qualifier, self.opening = transport, qualifier, opening
        self.certificate = certificate
        self.sent = False

    def publish_b3(self, payload, expected_previous):
        _require(
            not self.sent
            and payload["operation_id"] == self.certificate.value["commit_request_id"],
            "UNCERTAIN_PUBLICATION",
        )
        self.qualifier.live_window(
            opening=self.opening, issued_at=self.certificate.value["issued_at"]
        )
        self.sent = True  # Set before the sole call; outcome failure is never retry permission.
        return self.transport.publish_b3(payload, expected_previous)

    def head_b3(self, challenge):
        # Read-only head inspection also completes already dispatched publication.
        return self.transport.head_b3(challenge)


class NativeOpenPublisher:
    """Construct exact B3 OPEN artifacts using the held coordinator writer.

    All stores/transport/pins are fixed host resources. W05 replaces the internal
    publication verifier with its real live-lease guard before any durable intent.
    No adapter or candidate path comes from the request.
    """

    def __init__(
        self,
        *,
        ledger_store,
        ledger_name,
        ledger_identity,
        candidate_store,
        floor,
        transport,
        signer_pin,
        clock,
        clock_health,
    ):
        _require(
            type(ledger_store) is durable.PrivateStore
            and type(candidate_store) is durable.PrivateStore
            and type(floor) is durable.DurableFloor
            and callable(clock)
            and callable(clock_health),
            "HOST_CONTEXT_INVALID",
        )
        self.ledger, self.name, self.identity = ledger_store, ledger_name, ledger_identity
        self.candidates, self.floor, self.transport, self.pin = (
            candidate_store,
            floor,
            transport,
            signer_pin,
        )
        self.clock, self.health = clock, clock_health

    def prepare(self, *, qualifier, opening, authenticated_request):
        _require(
            type(qualifier) is NativeGovernanceQualifier
            and type(opening) is coordination.OpeningAttempt,
            "HOST_CONTEXT_INVALID",
        )
        opening.check()
        _require(
            (self.ledger.root, self.ledger.identity, self.name, self.identity)
            == (
                qualifier.policy.ledger.root,
                qualifier.policy.ledger.identity,
                qualifier.policy.name,
                qualifier.policy.identity,
            ),
            "HOST_POLICY_INVALID",
        )
        try:
            healthy, now = self.health(), self.clock()
        except Exception:
            raise OperationalError("HOST_CONTEXT_INVALID") from None
        _require(
            healthy is True and type(now) is datetime and now.tzinfo == UTC, "HOST_CONTEXT_INVALID"
        )
        issued = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        _require(
            qualifier.registry.read("base_head") == opening.snapshot.canonical_current_head,
            "HISTORY_INVALID",
        )
        cert, gov_ref, evd = qualifier.seal(
            opening=opening, authenticated_request=authenticated_request, issued_at=issued
        )
        value = cert.value
        existing = history._verified(opening.snapshot)["state"]
        reference_rows = []
        for field, kind in (
            (value["runtime_profile"]["reference"], "PROFILE"),
            (value["session"]["header_reference"], "SESSION"),
        ):
            row = {**field, "kind": kind, "introduced_revision": value["authority_revision_after"]}
            row["reference_id"] = sql.digest(
                {k: row[k] for k in ("path", "sha256", "schema_version", "kind")}
            )
            prior = next(
                (
                    r
                    for r in existing["artifact_references"]
                    if r["reference_id"] == row["reference_id"]
                ),
                None,
            )
            if prior is not None:
                row = prior
            else:
                reference_rows.append(row)
            if kind == "PROFILE":
                profile_id = row["reference_id"]
            else:
                session_id = row["reference_id"]
        run = {
            "run_id": value["run_id"],
            "run_version": 0,
            "previous_run_version": None,
            "source_revision": value["source_revision"],
            "protocol": value["protocol"],
            "methodology": value["methodology"],
            "mode": "OFFICIAL",
            "provenance_model": "PROFILE_V2",
            "lifecycle_state": "OPEN",
            "phase": "OPENED",
            "result": None,
            "binding": False,
            "validity_classification": "PENDING",
            "created_at": opening.record["run_identity"]["started_at"],
            "opened_at": evd.receipt.to_dict()["opened_at"],
            "finalized_at": None,
            "ack_required": False,
            "evidence_authority_reference_id": gov_ref["reference_id"],
            "profile_reference_id": profile_id,
            "session_reference_id": session_id,
            "introduced_revision": value["authority_revision_after"],
        }
        event = {
            "event_sequence": len(existing["authority_events"]) + 1,
            "revision": value["authority_revision_after"],
            "event_kind": "RUN_OPENED",
            "run_id": run["run_id"],
            "run_version": 0,
            "evidence_reference_id": gov_ref["reference_id"],
            "occurred_at": issued,
            "reason_code": None,
            "resolves_event_sequence": None,
        }
        sql.validate_row("official_runs", run)
        sql.validate_row("authority_events", event)
        adapter = durable.AuthorityBackend(
            ledger_store=self.ledger,
            ledger_name=self.name,
            ledger_identity=self.identity,
            candidate_store=self.candidates,
            floor=self.floor,
            writer=opening.writer,
            transport=_NativePublicationTransport(self.transport, qualifier, opening, cert),
            signer_pin=self.pin,
            clock_health=self.health,
            clock=lambda: coordination._utc(self.clock()),
        )
        publication = {
            "operation_id": value["commit_request_id"],
            "occurred_at": issued,
            "evidence_reference_id": gov_ref["reference_id"],
            "adapter": adapter,
            "signer_identity": self.pin,
            "candidate_payload_path": self.candidates.root
            + "/"
            + value["commit_request_id"]
            + ".json",
        }
        qualifier.live_window(opening=opening, issued_at=issued)
        return coordination.OpenPublication(
            evd,
            sql.canonical_bytes(value["open_evidence_reference"]),
            cert,
            sql.canonical_bytes(gov_ref),
            tuple(reference_rows),
            run,
            event,
            publication,
        )


class NativeGovernanceHistory:
    """Qualify every original native RUN_OPENED using fixed host contexts.

    Imported HISTORICAL_V1 runs retain their approved B3 provenance. This reader
    neither invents retroactive GOV for them nor uses a terminal event's later
    evidence reference in place of the original certificate. Inspections only
    read; they do not seal artifacts, claim nonces or acquire exclusive locks.
    """

    def __init__(self, qualifiers):
        _require(type(qualifiers) is tuple, "HOST_CONTEXT_INVALID")
        contexts = {}
        for reference_id, qualifier in qualifiers:
            _require(
                type(qualifier) is NativeGovernanceQualifier and reference_id not in contexts,
                "HOST_CONTEXT_INVALID",
            )
            sql.validate_scalar(reference_id, {"type": "sha256", "not_null": True})
            contexts[reference_id] = qualifier
        self.contexts = MappingProxyType(contexts)

    def qualify(self, *, snapshot, journal):
        material = history._verified(snapshot)
        state = material["state"]
        reservations, paths, runs = set(), set(), set()
        result = []
        for run in state["official_runs"]:
            if run["provenance_model"] != "PROFILE_V2" or run["run_version"] != 0:
                continue
            ref_id = run["evidence_authority_reference_id"]
            _require(ref_id in self.contexts, "HISTORICAL_GOV_UNQUALIFIED")
            events = [
                e
                for e in state["authority_events"]
                if e["event_kind"] == "RUN_OPENED"
                and (e["run_id"], e["run_version"], e["revision"])
                == (run["run_id"], 0, run["introduced_revision"])
            ]
            _require(len(events) == 1, "HISTORICAL_GOV_INVALID")
            reference = next(
                (r for r in state["artifact_references"] if r["reference_id"] == ref_id), None
            )
            _require(reference is not None, "HISTORICAL_GOV_INVALID")
            cert = self.contexts[ref_id].replay(
                snapshot=snapshot,
                journal=journal,
                original_run=run,
                original_event=events[0],
                reference=reference,
            )
            reservation = cert.value["reservation"]["reservation_id"]
            _require(
                reservation not in reservations
                and reference["path"] not in paths
                and run["run_id"] not in runs,
                "GOVERNANCE_REUSE",
            )
            reservations.add(reservation)
            paths.add(reference["path"])
            runs.add(run["run_id"])
            result.append((run["run_id"], cert.canonical_bytes))
        return tuple(result)


@dataclass(frozen=True, slots=True)
class BridgeBodyApproval:
    """Body admitted by fixed native host sources before capability issuance."""

    canonical_body: bytes
    context: wire.RequestContext
    reservation_record: bytes | None = None
    preflight_reference: bytes | None = None


def _host_call(provider, code, **arguments):
    try:
        return provider(**arguments)
    except Exception:
        raise OperationalError(code) from None


class NativeBridgeBroker:
    """Exact nine/five frame host orchestration, no generic RPC dispatch.

    Native sources and process-key admitter are constructor-fixed host trust
    providers. They must qualify the body against B1/B2/B3/B5/policy before
    returning BridgeBodyApproval. No provider/path/key/environment is received
    from RPC. Normal operations use observed Docker; recovery uses independently
    admitted private host pipes and a separate constructor-fixed OS role.
    """

    def __init__(
        self,
        *,
        journal,
        session_qualifier,
        signer,
        host_sources,
        holder_admitter,
        role="SESSION_EXECUTOR",
    ):
        _require(
            type(journal) is coordination.CoordinationJournal
            and type(session_qualifier) is NativeSessionQualifier
            and role in ("SESSION_EXECUTOR", "RECOVERY_OPERATOR")
            and callable(holder_admitter),
            "HOST_CONTEXT_INVALID",
        )
        pin = journal.bridge_pin
        _require(
            signer.public_key().public_bytes_raw() == pin.public_key.public_bytes_raw(),
            "HOST_CONTEXT_INVALID",
        )
        self.journal, self.sessions, self.signer, self.sources = (
            journal,
            session_qualifier,
            signer,
            host_sources,
        )
        self.admit, self.role, self.pin = holder_admitter, role, pin
        self.used = False

    def _signed(self, kind, value):
        value = dict(value, signature="0" * 128)
        checked = wire._parse(kind, sql.canonical_bytes(value), maximum=wire.MAX_RESPONSE_BYTES)
        signature = _host_call(
            self.signer.sign, "HOST_SIGNING_FAILED", data=wire.signing_preimage(checked)
        )
        _require(type(signature) is bytes and len(signature) == 64, "HOST_SIGNING_FAILED")
        value["signature"] = signature.hex()
        return wire._parse(kind, sql.canonical_bytes(value), maximum=wire.MAX_RESPONSE_BYTES)

    def _frame(self, channel, kind, body):
        value = {
            "schema_version": "s11-ex10-b4-frame-v1",
            "exchange_id": channel.exchange_id,
            "sequence": channel.position + 1,
            "kind": kind,
            "direction": "HOST_TO_EXECUTOR",
            "body": body,
        }
        channel.send(sql.canonical_bytes(value))

    def _challenge(self, channel, native):
        now = self.journal._now()
        value = {
            "schema_version": "s11-ex10-b4-host-challenge-v1",
            "exchange_id": channel.exchange_id,
            "challenge": secrets.token_hex(32),
            "issued_at": coordination._utc(now),
            "expires_at": coordination._utc(now + timedelta(seconds=120)),
            "authority_id": self.journal.authority_id,
            "authority_epoch": self.journal.authority_epoch,
            "authorization_reference": sql.parse_canonical(self.sources.authorization_reference),
            "bridge_key_id": self.pin.key_id,
            "bridge_key_epoch": self.pin.key_epoch,
            "container_identity_digest": native.process.identity_digest,
            "deployment_verification_sha256": native.process.deployment_verification_sha256,
            "session_id": native.session.header["session_id"],
            "session_header_sha256": sql.parse_canonical(native.header_reference)["sha256"],
            "runtime_profile_sha256": sql.parse_canonical(native.profile_reference)["sha256"],
            "source_revision": native.profile.to_dict()["source_revision"],
            "project": native.profile.to_dict()["project"],
        }
        challenge = self._signed("host_challenge", value)
        projection = {
            k: v
            for k, v in challenge.to_dict().items()
            if k not in ("signature", "issued_at", "expires_at")
        }
        wire.verify_host_challenge(
            challenge.canonical,
            expected_context=wire.ReceiptContext(sql.canonical_bytes(projection), now),
            bridge_pin=self.pin,
        )
        self._frame(channel, "HOST_CHALLENGE", challenge.to_dict())
        return challenge

    def _holder(self, channel, native, proposal):
        key = _host_call(
            self.admit,
            "PROCESS_ADMISSION_REQUIRED",
            channel=channel,
            process=native.process,
            proposal=proposal,
            role=self.role,
        )
        _require(
            isinstance(key, Ed25519PublicKey)
            and key.public_bytes_raw().hex() == proposal.to_dict()["holder_public_key_hex"],
            "PROCESS_ADMISSION_REQUIRED",
        )
        return key

    def _capability(self, approval, *, native, holder):
        _require(
            type(approval) is BridgeBodyApproval
            and type(approval.context)
            is (
                wire.RequestContext
                if self.role == "SESSION_EXECUTOR"
                else wire.RecoveryRequestContext
            ),
            "HOST_CONTEXT_INVALID",
        )
        body = sql.parse_canonical(approval.canonical_body)
        context = approval.context
        expected = {
            k: getattr(context, k)
            for k in (
                "request_id",
                "nonce",
                "exchange_id",
                "operation",
                "session_id",
                "session_header_sha256",
                "runtime_profile_sha256",
                "authority_id",
                "authority_epoch",
                "authority_revision",
                "source_revision",
                "project",
            )
        }
        expected.update(
            authorization_reference=sql.parse_canonical(context.authorization_reference),
            payload=sql.parse_canonical(context.expected_payload),
        )
        _require(
            all(body.get(k) == v for k, v in expected.items())
            and (
                context.session_id,
                context.session_header_sha256,
                context.runtime_profile_sha256,
                context.source_revision,
                context.project,
                context.authority_id,
                context.authority_epoch,
                context.holder_public_key_hex,
                context.deployment_verification_sha256,
                context.container_identity_digest,
            )
            == (
                native.session.header["session_id"],
                sql.parse_canonical(native.header_reference)["sha256"],
                sql.parse_canonical(native.profile_reference)["sha256"],
                native.profile.to_dict()["source_revision"],
                native.profile.to_dict()["project"],
                self.journal.authority_id,
                self.journal.authority_epoch,
                holder.public_bytes_raw().hex(),
                native.process.deployment_verification_sha256,
                native.process.identity_digest,
            ),
            "HOST_CONTEXT_INVALID",
        )
        now = self.journal._now()
        wire._fresh(body, now, maximum=120, code="REQUEST_EXPIRED")
        capability = {
            "schema_version": "s11-ex10-b4-capability-v1",
            "namespace": "S11C3-EX10",
            "role": self.role,
            "capability_id": str(uuid.uuid4()),
            "request_nonce": body["nonce"],
            "request_body_digest": hashlib.sha256(approval.canonical_body).hexdigest(),
            "expected_revision": context.authority_revision,
            "expected_run_id": context.expected_run_id,
            "reservation_id": context.reservation_id,
            "reservation_digest": context.reservation_digest,
            "fencing_token": context.fencing_token,
            "holder_public_key_hex": holder.public_bytes_raw().hex(),
            "deployment_verification_sha256": native.process.deployment_verification_sha256,
            "container_identity_digest": native.process.identity_digest,
            "issued_at": coordination._utc(now),
            "expires_at": body["expires_at"],
            "bridge_key_id": self.pin.key_id,
            "bridge_key_epoch": self.pin.key_epoch,
            **{
                k: body[k]
                for k in (
                    "request_id",
                    "exchange_id",
                    "operation",
                    "session_id",
                    "session_header_sha256",
                    "runtime_profile_sha256",
                    "authority_id",
                    "authority_epoch",
                    "source_revision",
                    "authorization_reference",
                )
            },
        }
        if self.role == "RECOVERY_OPERATOR":
            capability["operator_authorization_reference"] = sql.parse_canonical(
                context.operator_authorization_reference
            )
        return self._signed("capability", capability)

    def _request(self, channel, approval, capability, holder):
        frame = channel.receive()
        raw = sql.canonical_bytes(frame.to_dict()["body"])
        request = wire.parse_request_bytes(raw).to_dict()
        body = {
            k: v
            for k, v in request.items()
            if k not in ("capability", "sender_signature", "request_digest")
        }
        _require(
            sql.canonical_bytes(body) == approval.canonical_body
            and sql.canonical_bytes(request["capability"]) == capability.canonical,
            "REQUEST_CONTEXT_INVALID",
        )
        from dataclasses import replace

        context = replace(approval.context, now=self.journal._now())
        if self.role == "SESSION_EXECUTOR":
            authenticated = wire.verify_request(
                raw, expected_context=context, bridge_pin=self.pin, holder_public_key=holder
            )
        else:
            authenticated = wire.verify_recovery_request(
                raw, expected_context=context, bridge_pin=self.pin, operator_public_key=holder
            )
        _require(
            _host_call(
                self.sources.register_request, "HOST_CONTEXT_INVALID", raw=raw, approval=approval
            )
            is None,
            "HOST_CONTEXT_INVALID",
        )
        channel._check()
        return authenticated

    def _normal_response(
        self,
        authenticated,
        *,
        result,
        before,
        after,
        run_id,
        reservation_id,
        witness_head,
        coordination_head=None,
    ):
        request = authenticated.request.to_dict()
        value = {
            "schema_version": "s11-ex10-b4-bridge-response-v1",
            "status": "OK",
            "uncertain": False,
            "error_class": None,
            "result": result,
            "result_digest": "0" * 64,
            "response_digest": "0" * 64,
            "authority_revision_before": before,
            "authority_revision_after": after,
            "bridge_key_id": self.pin.key_id,
            "bridge_key_epoch": self.pin.key_epoch,
            "completed_at": coordination._utc(self.journal._now()),
            "run_id": run_id,
            "reservation_id": reservation_id,
            "witness_head": witness_head,
            "coordination_head": coordination_head,
            **{
                k: request[k]
                for k in (
                    "request_id",
                    "nonce",
                    "exchange_id",
                    "operation",
                    "request_digest",
                    "session_id",
                    "runtime_profile_sha256",
                    "authority_id",
                    "authority_epoch",
                )
            },
        }
        value["signature"] = "0" * 128
        parsed = wire.parse_response_bytes(sql.canonical_bytes(value))
        value["result_digest"] = wire.result_digest(parsed)
        value["response_digest"] = wire.response_digest(
            wire.parse_response_bytes(sql.canonical_bytes(value))
        )
        response = self._signed("response", value)
        wire.verify_response(
            response.canonical,
            authenticated_request=authenticated,
            bridge_pin=self.pin,
            now=self.journal._now(),
        )
        return response.canonical

    def _reserve_response(self, approval, opening, holder):
        opening.check()
        record = opening.record
        request = opening.request.request.to_dict()
        now = self.journal._now()
        _require(approval.preflight_reference is not None, "HOST_CONTEXT_INVALID")
        permit = {
            "schema_version": "s11-ex10-b4-open-one-permit-v1",
            "kind": "OPEN_ONE",
            "namespace": "S11C3-EX10",
            "permit_id": str(uuid.uuid4()),
            "permit_nonce": secrets.token_hex(32),
            "permit_digest": "0" * 64,
            "issued_at": coordination._utc(now),
            "expires_at": record["freshness_deadline"],
            "base_authority_revision": record["previous_authority_revision"],
            "expected_open_revision": record["expected_next_revision"],
            "authorization_reference": request["authorization_reference"],
            "bridge_key_id": self.pin.key_id,
            "bridge_key_epoch": self.pin.key_epoch,
            "container_identity_digest": approval.context.container_identity_digest,
            "deployment_verification_sha256": approval.context.deployment_verification_sha256,
            "holder_public_key_hex": holder.public_bytes_raw().hex(),
            "exchange_id": request["exchange_id"],
            "attempt_nonce": record["nonce"],
            "preflight_reference": sql.parse_canonical(approval.preflight_reference),
            "reserve_request_id": record["request_id"],
            "reserve_request_digest": record["request_digest"],
            **{
                k: record[k]
                for k in (
                    "authority_id",
                    "authority_epoch",
                    "session_id",
                    "session_header_sha256",
                    "runtime_profile_sha256",
                    "source_revision",
                    "project",
                    "reservation_id",
                    "reservation_digest",
                    "reservation_receipt_reference",
                    "fencing_token",
                    "run_identity",
                    "acknowledgment_digest",
                )
            },
        }
        permit["signature"] = "0" * 128
        permit["permit_digest"] = wire.open_permit_digest(
            wire.parse_open_one_bytes(sql.canonical_bytes(permit))
        )
        signed = self._signed("open_one_permit", permit)
        excluded = {
            "signature",
            "permit_digest",
            "issued_at",
            "expires_at",
            "permit_id",
            "permit_nonce",
            "kind",
            "schema_version",
        }
        wire.verify_open_one(
            signed.canonical,
            expected_context=wire.PermitContext(
                sql.canonical_bytes(
                    {k: v for k, v in signed.to_dict().items() if k not in excluded}
                ),
                wire._time(record["freshness_deadline"]),
                now,
            ),
            bridge_pin=self.pin,
            holder_public_key=holder,
        )
        _require(
            _host_call(
                self.sources.remember_permit, "HOST_CONTEXT_INVALID", permit=signed, opening=opening
            )
            is None,
            "HOST_CONTEXT_INVALID",
        )
        response = self._normal_response(
            opening.request,
            result={
                "open_permit": signed.to_dict(),
                "reservation": record,
                "reservation_receipt": opening.receipt,
            },
            before=record["previous_authority_revision"],
            after=record["previous_authority_revision"],
            run_id=record["run_id"],
            reservation_id=record["reservation_id"],
            witness_head=request["payload"]["expected_head"],
        )
        if type(self.sources) is NativeHostSources:
            self.sources.archive_response(
                authenticated_request=opening.request, raw_response=response
            )
        opening.save_response(authenticated=opening.request, raw_response=response)
        opening.check()
        return signed, response

    def _begin(self, channel):
        if type(self.sources) is NativeHostSources:
            _require(self.sources.journal is self.journal, "HOST_CONTEXT_INVALID")
            if self.sources.channel is None:
                self.sources.begin_exchange(channel=channel)
            _require(self.sources.channel is channel, "HOST_CONTEXT_INVALID")
            self.sources._live()

    def serve_start(self, channel):
        try:
            return self._serve_start(channel)
        except Exception:
            raise OperationalError(
                "UNCERTAIN_PUBLICATION"
                if type(channel) is PipeFrameChannel and channel.dispatched
                else "HOST_CONTEXT_INVALID"
            ) from None

    def _serve_start(self, channel):
        _require(
            not self.used
            and self.role == "SESSION_EXECUTOR"
            and type(channel) is PipeFrameChannel
            and channel.role == "HOST"
            and channel.exchange == "START",
            "PROTOCOL_INVALID",
        )
        self.used = True
        self._begin(channel)
        native = self.sessions.qualify()
        challenge = self._challenge(channel, native)
        proposal = wire.parse_open_proposal_bytes(
            sql.canonical_bytes(channel.receive().to_dict()["body"])
        )
        holder = self._holder(channel, native, proposal)
        proposal = wire.verify_open_proposal(
            proposal.canonical, challenge=challenge, holder_public_key=holder
        )
        approval = _host_call(
            self.sources.approve_reserve,
            "HOST_CONTEXT_INVALID",
            body=sql.canonical_bytes(proposal.to_dict()["reserve_request_body"]),
            native=native,
            holder=holder,
            challenge=challenge,
        )
        _require(
            type(approval) is BridgeBodyApproval
            and approval.reservation_record is not None
            and approval.canonical_body
            == sql.canonical_bytes(proposal.to_dict()["reserve_request_body"]),
            "HOST_CONTEXT_INVALID",
        )
        capability = self._capability(approval, native=native, holder=holder)
        self._frame(channel, "AUTHORIZE_REQUEST", capability.to_dict())
        authenticated = self._request(channel, approval, capability, holder)
        # Conservative before the first mutation; never undo on a transport error.
        channel.mark_dispatched()
        with self.journal.opening(
            raw_request=authenticated.request.canonical, raw_record=approval.reservation_record
        ) as opening:
            _require(type(opening) is coordination.OpeningAttempt, "UNCERTAIN_PUBLICATION")
            opening._bridge_exchange_guard = (
                channel._check
            )  # Fixed owned pipe, never supplied by RPC.
            permit, response = self._reserve_response(approval, opening, holder)
            self._frame(channel, "RPC_RESPONSE", sql.parse_canonical(response))
            evidence_frame = wire.verify_evidence_frame(
                sql.canonical_bytes(channel.receive().to_dict()["body"]),
                exchange_id=channel.exchange_id,
                open_permit_digest=permit.to_dict()["permit_digest"],
                holder_public_key=holder,
            )
            commit_approval = _host_call(
                self.sources.approve_commit,
                "HOST_CONTEXT_INVALID",
                body=sql.canonical_bytes(evidence_frame.to_dict()["commit_request_body"]),
                native=self.sessions.qualify(),
                holder=holder,
                opening=opening,
                permit=permit,
            )
            _require(
                type(commit_approval) is BridgeBodyApproval
                and commit_approval.context.request_id != approval.context.request_id
                and commit_approval.context.nonce != approval.context.nonce
                and commit_approval.canonical_body
                == sql.canonical_bytes(evidence_frame.to_dict()["commit_request_body"]),
                "REQUEST_CONTEXT_INVALID",
            )
            commit_cap = self._capability(
                commit_approval, native=self.sessions.qualify(), holder=holder
            )
            _require(
                commit_cap.to_dict()["capability_id"] != capability.to_dict()["capability_id"],
                "REQUEST_CONTEXT_INVALID",
            )
            self._frame(channel, "AUTHORIZE_REQUEST", commit_cap.to_dict())
            committed = self._request(channel, commit_approval, commit_cap, holder)
            channel._check()
            response = opening.complete_open(raw_request=committed.request.canonical)
            _require(type(response) is bytes, "UNCERTAIN_PUBLICATION")
        # The actual context manager has released the global writer before delivery.
        _require(opening.writer.fd is None, "UNCERTAIN_PUBLICATION")
        self._frame(channel, "RPC_RESPONSE", sql.parse_canonical(response))
        channel.finish()
        return response

    def open_docker_start(self, adapter, *, exchange_id):
        _require(
            type(adapter) is FixedDockerAdapter and self.role == "SESSION_EXECUTOR",
            "PROTOCOL_INVALID",
        )
        native = self.sessions.qualify()
        _require(adapter.identity == native.process, "PROCESS_IDENTITY_CHANGED")
        try:
            with adapter.open(exchange="START", exchange_id=exchange_id) as channel:
                response = self.serve_start(channel)
            adapter.finish_client()
            return response
        except Exception:
            adapter.abort_client()
            raise OperationalError(
                "UNCERTAIN_PUBLICATION" if adapter.used else "PROCESS_IDENTITY_CHANGED"
            ) from None

    def serve_single(self, channel):
        try:
            return self._serve_single(channel)
        except Exception:
            raise OperationalError(
                "UNCERTAIN_PUBLICATION"
                if type(channel) is PipeFrameChannel and channel.dispatched
                else "HOST_CONTEXT_INVALID"
            ) from None

    def _serve_single(self, channel):
        _require(
            not self.used
            and type(channel) is PipeFrameChannel
            and channel.role == "HOST"
            and channel.exchange == "SINGLE_RPC",
            "PROTOCOL_INVALID",
        )
        self.used = True
        self._begin(channel)
        native = self.sessions.qualify()
        challenge = self._challenge(channel, native)
        proposal = wire.parse_single_rpc_proposal_bytes(
            sql.canonical_bytes(channel.receive().to_dict()["body"])
        )
        holder = self._holder(channel, native, proposal)
        proposal = wire.verify_single_rpc_proposal(
            proposal.canonical, challenge=challenge, holder_public_key=holder
        )
        body = sql.canonical_bytes(proposal.to_dict()["request_body"])
        operation = proposal.to_dict()["request_body"]["operation"]
        allowed = (
            (NORMAL_OPERATIONS - {"reserve-start", "commit-open"})
            if self.role == "SESSION_EXECUTOR"
            else wire.RECOVERY_OPERATIONS
        )
        _require(operation in allowed, "ROLE_OPERATION_DENIED")
        approval = _host_call(
            self.sources.approve_single,
            "HOST_CONTEXT_INVALID",
            body=body,
            native=native,
            holder=holder,
            role=self.role,
        )
        _require(
            type(approval) is BridgeBodyApproval and approval.canonical_body == body,
            "REQUEST_CONTEXT_INVALID",
        )
        capability = self._capability(approval, native=native, holder=holder)
        self._frame(channel, "AUTHORIZE_REQUEST", capability.to_dict())
        authenticated = self._request(channel, approval, capability, holder)
        channel._check()
        if operation in wire.RECOVERY_OPERATIONS or operation == "record-uncertain":
            channel.mark_dispatched()
        result = _host_call(
            self.sources.dispatch_single,
            "HOST_CONTEXT_INVALID",
            authenticated_request=authenticated,
            role=self.role,
        )
        _require(type(result) is SingleOperationResult, "HOST_CONTEXT_INVALID")
        response = self._normal_response(
            authenticated,
            result=sql.parse_canonical(result.result),
            before=result.before,
            after=result.after,
            run_id=result.run_id,
            reservation_id=result.reservation_id,
            witness_head=sql.parse_canonical(result.witness_head),
            coordination_head=None
            if result.coordination_head is None
            else sql.parse_canonical(result.coordination_head),
        )
        self._frame(channel, "RPC_RESPONSE", sql.parse_canonical(response))
        channel.finish()
        return response

    def open_docker_single(self, adapter, *, exchange_id):
        _require(
            type(adapter) is FixedDockerAdapter and self.role == "SESSION_EXECUTOR",
            "ROLE_OPERATION_DENIED",
        )
        native = self.sessions.qualify()
        _require(adapter.identity == native.process, "PROCESS_IDENTITY_CHANGED")
        try:
            with adapter.open(exchange="SINGLE_RPC", exchange_id=exchange_id) as channel:
                response = self.serve_single(channel)
            adapter.finish_client()
            return response
        except Exception:
            adapter.abort_client()
            raise OperationalError(
                "UNCERTAIN_PUBLICATION" if adapter.used else "HOST_CONTEXT_INVALID"
            ) from None


@dataclass(frozen=True, slots=True)
class SingleOperationResult:
    result: bytes
    before: int
    after: int
    witness_head: bytes
    run_id: str | None = None
    reservation_id: str | None = None
    coordination_head: bytes | None = None


@dataclass(frozen=True, slots=True)
class NativeRecoveryAuthority:
    """Fixed host observation of a genuine full candidate and existing completion."""

    authority_snapshot: object
    authority_completion: object | None = None


class NativeOpeningScope:
    """Own the real journal context; uncertainty exits it before final delivery.

    This internal host object cannot be supplied by RPC. Closing resumes the
    original context manager, preserving its cleanup and durable tombstones.
    """

    def __init__(self, *, journal, raw_request, raw_record):
        _require(type(journal) is coordination.CoordinationJournal, "OWNER_LEASE_REQUIRED")
        self.journal = journal
        self.context = journal.opening(raw_request=raw_request, raw_record=raw_record)
        self.owner = (os.getpid(), threading.get_ident())
        self.used = False
        self.closed = False
        self.opening = None

    def __enter__(self):
        _require(
            not self.used and self.owner == (os.getpid(), threading.get_ident()),
            "OWNER_LEASE_REQUIRED",
        )
        self.used = True
        try:
            result = self.context.__enter__()
        except BaseException:
            self.closed = True
            raise
        try:
            _require(type(result) is coordination.OpeningAttempt, "OWNER_LEASE_REQUIRED")
            self.opening = result
            self.check()
        except BaseException as exc:
            try:
                self.context.__exit__(type(exc), exc, exc.__traceback__)
            finally:
                self.closed = True
            raise
        return self

    def check(self):
        _require(
            self.used
            and not self.closed
            and self.owner == (os.getpid(), threading.get_ident())
            and type(self.opening) is coordination.OpeningAttempt
            and self.opening.journal is self.journal,
            "OWNER_LEASE_REQUIRED",
        )
        self.opening.check()

    def close(self):
        self.check()
        try:
            self.context.__exit__(None, None, None)
        finally:
            self.closed = True
        _require(self.opening.writer.fd is None, "UNCERTAIN_PUBLICATION")

    def __exit__(self, kind, value, traceback):
        _require(self.owner == (os.getpid(), threading.get_ident()), "OWNER_LEASE_REQUIRED")
        if not self.closed:
            try:
                self.context.__exit__(kind, value, traceback)
            finally:
                self.closed = True
        _require(self.opening.writer.fd is None, "UNCERTAIN_PUBLICATION")
        return False


class NativeHostSources:
    """Fixed native capability admission and publication sources for W05/W06.

    Authority/trust, historic contexts and host-only manual recovery providers
    are externally configured. They cannot be supplied by any request. Every
    live action uses the actual B1/B2 paths, externally approved host policy,
    whole B3 history/GOV and the observed private exchange. No bootstrap, policy
    defaults, witness resets, real keys or generic RPC dispatch are introduced.
    """

    def __init__(
        self,
        *,
        session_qualifier,
        registry,
        policy,
        authority_reader,
        historical_contexts,
        artifact_store,
        certificate_store,
        publisher,
        executor_fingerprint,
        signer,
        bridge_pin,
        live_owner=None,
        uncertainty_verifier=None,
        recovery_sources=None,
        lifecycle_sources=None,
    ):
        _require(
            type(session_qualifier) is NativeSessionQualifier
            and type(registry) is HostArtifactRegistry
            and type(policy) is NativeHostPolicy
            and policy.sessions is session_qualifier
            and callable(authority_reader)
            and callable(historical_contexts)
            and type(artifact_store) is durable.PrivateStore
            and type(certificate_store) is durable.PrivateStore
            and type(publisher) is NativeOpenPublisher
            and type(bridge_pin) is wire.PublicKeyPin
            and signer.public_key().public_bytes_raw() == bridge_pin.public_key.public_bytes_raw(),
            "HOST_CONTEXT_INVALID",
        )
        self.sessions, self.registry, self.policy = session_qualifier, registry, policy
        self.reader, self.histories = authority_reader, historical_contexts
        self.artifacts, self.certificates, self.publisher = (
            artifact_store,
            certificate_store,
            publisher,
        )
        self.fingerprint, self.signer, self.pin = executor_fingerprint, signer, bridge_pin
        self.journal = None
        self.channel = None
        self.approvals = {}
        self.requests = {}
        self.grants = {}
        self.catalog = {}
        self.owner, self.uncertainty, self.recovery, self.lifecycle = (
            live_owner,
            uncertainty_verifier,
            recovery_sources,
            lifecycle_sources,
        )

    @property
    def authorization_reference(self):
        self.registry.read("authorization")
        _require(
            self.registry.reference("authorization")
            == self.policy.operator_authorization_reference,
            "HOST_POLICY_INVALID",
        )
        return sql.canonical_bytes(self.registry.reference("authorization"))

    def verify_registry(self, *, canonical_binding, journal):
        _require(
            type(journal) is coordination.CoordinationJournal
            and journal.bridge_pin == self.pin
            and canonical_binding == journal.binding,
            "HOST_POLICY_INVALID",
        )
        if self.journal is None:
            self.journal = journal
        _require(self.journal is journal, "HOST_POLICY_INVALID")
        native = self.sessions.qualify()
        self.policy.qualify(native=native, journal=journal)
        return None

    def begin_exchange(self, *, channel):
        _require(
            self.channel is None
            and type(channel) is PipeFrameChannel
            and channel.role == "HOST"
            and self.journal is not None,
            "HOST_CONTEXT_INVALID",
        )
        self.channel = channel
        self._live()
        return None

    def _live(self):
        _require(self.channel is not None, "PRIVATE_PIPE_REQUIRED")
        self.channel._check()

    def read_authority(self):
        _require(self.journal is not None, "HOST_CONTEXT_INVALID")
        native = self.sessions.qualify()
        self.policy.qualify(native=native, journal=self.journal)
        snapshot = _host_call(self.reader, "HISTORY_INVALID")
        return self.qualify_authority(snapshot=snapshot, native=native)

    def qualify_authority(self, *, snapshot, native):
        material = history._verified(snapshot)
        metadata = material["state"]["metadata"][0]
        _require(
            (metadata["authority_id"], metadata["epoch"], material["descriptor_reference"])
            == (
                self.journal.authority_id,
                self.journal.authority_epoch,
                native.profile.to_dict()["historical_lifecycle_authority_reference"],
            ),
            "HISTORY_INVALID",
        )
        contexts = _host_call(
            self.histories, "HISTORICAL_GOV_UNQUALIFIED", snapshot=snapshot, journal=self.journal
        )
        _require(type(contexts) is NativeGovernanceHistory, "HISTORICAL_GOV_UNQUALIFIED")
        merged = dict(contexts.contexts)
        for reference, qualifier in self.catalog.items():
            _require(
                reference not in merged or merged[reference] is qualifier, "HISTORICAL_GOV_INVALID"
            )
            merged[reference] = qualifier
        NativeGovernanceHistory(tuple(merged.items())).qualify(
            snapshot=snapshot, journal=self.journal
        )
        self.policy.postcheck(native)
        return snapshot

    def _body(self, raw, operation):
        _require(
            type(raw) is bytes and len(raw) <= wire.MAX_REQUEST_BYTES, "REQUEST_CONTEXT_INVALID"
        )
        body = sql.parse_canonical(raw)
        schema = next(
            s for s in wire.request_schemas() if s["properties"]["operation"]["const"] == operation
        )
        excluded = {"capability", "sender_signature", "request_digest"}
        wire._validate(
            body,
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {k: v for k, v in schema["properties"].items() if k not in excluded},
                "required": [k for k in schema["required"] if k not in excluded],
            },
        )
        _require(body["operation"] == operation, "REQUEST_CONTEXT_INVALID")
        wire._fresh(body, self.journal._now(), maximum=120, code="REQUEST_EXPIRED")
        return body

    def _context(self, body, *, native, holder, revision, run_id=None, record=None):
        _require(isinstance(holder, Ed25519PublicKey), "PROCESS_ADMISSION_REQUIRED")
        fixed = {
            "session_id": native.session.header["session_id"],
            "session_header_sha256": sql.parse_canonical(native.header_reference)["sha256"],
            "runtime_profile_sha256": sql.parse_canonical(native.profile_reference)["sha256"],
            "authority_id": self.journal.authority_id,
            "authority_epoch": self.journal.authority_epoch,
            "authority_revision": revision,
            "source_revision": native.profile.to_dict()["source_revision"],
            "project": native.profile.to_dict()["project"],
            "authorization_reference": sql.parse_canonical(self.authorization_reference),
        }
        _require(all(body[k] == v for k, v in fixed.items()), "REQUEST_CONTEXT_INVALID")
        context = wire.RequestContext(
            **{
                k: body[k]
                for k in (
                    "request_id",
                    "nonce",
                    "exchange_id",
                    "operation",
                    "session_id",
                    "session_header_sha256",
                    "runtime_profile_sha256",
                    "authority_id",
                    "authority_epoch",
                    "authority_revision",
                    "source_revision",
                    "project",
                )
            },
            authorization_reference=self.authorization_reference,
            deployment_verification_sha256=native.process.deployment_verification_sha256,
            container_identity_digest=native.process.identity_digest,
            expected_run_id=run_id,
            reservation_id=None if record is None else record["reservation_id"],
            reservation_digest=None if record is None else record["reservation_digest"],
            fencing_token=None if record is None else record["fencing_token"],
            holder_public_key_hex=holder.public_bytes_raw().hex(),
            expected_payload=sql.canonical_bytes(body["payload"]),
            now=self.journal._now(),
        )
        _require(body["exchange_id"] == self.channel.exchange_id, "REQUEST_CONTEXT_INVALID")
        self._live()
        return context

    def _base(self, snapshot, *, require_configured=True):
        material = history._verified(snapshot)
        head = history.current_revision(snapshot)
        receipt = history._receipts(material["state"])[head["revision"]]
        raw = sql.canonical_bytes(receipt)
        if self.registry.read("base_receipt") == raw:
            reference = self.registry.reference("base_receipt")
        else:
            _require(not require_configured, "HISTORY_INVALID")
            floor = self.publisher.floor
            accepted = floor.current()
            path = floor._path(head["revision"])
            _require(
                (accepted.authority_id, accepted.authority_epoch)
                == (self.journal.authority_id, self.journal.authority_epoch)
                and accepted.revision >= head["revision"]
                and floor.store.read(path) == raw,
                "HISTORY_INVALID",
            )
            reference = _artifact_reference(path, raw, receipt["witness_schema_version"])
        return {
            "authority_id": head["authority_id"],
            "authority_epoch": head["epoch"],
            "revision": head["revision"],
            "ledger_digest": head["ledger_digest"],
            "witness_digest": head["witness_digest"],
            "receipt_reference": reference,
        }

    def _approve(self, approval):
        self._live()
        digest = hashlib.sha256(approval.canonical_body).hexdigest()
        _require(digest not in self.approvals, "REPLAY_CONFLICT")
        self.approvals[digest] = approval
        return approval

    def approve_reserve(self, *, body, native, holder, challenge):
        self._live()
        self.policy.postcheck(native)
        self.policy.qualify(native=native, journal=self.journal)
        value = self._body(body, "reserve-start")
        snapshot = self.read_authority()
        base = self._base(snapshot)
        _require(history.first_valid_status(snapshot)["status"] == "NONE", "FIRST_VALID_BLOCKED")
        acknowledgments = _prefix_open_facts(
            snapshot, base["revision"], coordination._utc(self.journal._now())
        )
        references = self.policy.reserve_references(
            registry=self.registry,
            native=native,
            snapshot=snapshot,
            issued_at=coordination._utc(self.journal._now()),
            live_clock=self.journal._now,
        )
        payload = value["payload"]
        identity = wire.qualify_run_identity(payload["run_identity"])
        _require(
            all(payload[k] == v for k, v in references.items())
            and payload["expected_head"] == base
            and payload["acknowledgments"] == acknowledgments
            and identity["git_sha"] == value["source_revision"]
            and identity["execution_nonce"] == value["nonce"]
            and wire._time(identity["started_at"]) <= self.journal._now()
            and wire._time(sql.parse_canonical(self.registry.read("preflight"))["checked_at"])
            <= wire._time(identity["started_at"]),
            "REQUEST_CONTEXT_INVALID",
        )
        context = self._context(
            value,
            native=native,
            holder=holder,
            revision=base["revision"],
            run_id=identity["run_id"],
        )
        state = self.journal.read()
        remote = self.journal.fresh_head(state)
        _require(
            state["active"] is None
            and remote["active_reservation_id"] is None
            and state["token"] < wire.SAFE_INTEGER_MAX,
            "GLOBAL_START_BLOCKED",
        )
        record = {
            "schema_version": "s11-ex10-b4-reservation-v1",
            "namespace": "S11C3-EX10",
            "reservation_id": str(uuid.uuid4()),
            "deployment_verification_reference": references["deployment_verification_reference"],
            "coordination_policy_reference": self.policy.reference,
            "host_root_binding_sha256": self.policy.root_binding,
            "requested_at": value["issued_at"],
            "freshness_deadline": value["expires_at"],
            "state": "PENDING",
            "request_digest": hashlib.sha256(body).hexdigest(),
            "acknowledgment_digest": sql.digest(acknowledgments),
            "previous_authority_revision": base["revision"],
            "previous_ledger_digest": base["ledger_digest"],
            "previous_witness_digest": base["witness_digest"],
            "expected_next_revision": base["revision"] + 1,
            "run_id": identity["run_id"],
            "run_identity": identity,
            "fencing_token": state["token"] + 1,
            "record_sequence": 1,
            "previous_reservation_record_digest": None,
            "reservation_receipt_reference": None,
            "open_witness_reference": None,
            "evidence_receipt_reference": None,
            "terminal_witness_reference": None,
            "operator_authorization_reference": sql.parse_canonical(self.authorization_reference),
            "manual_resolution_reference": None,
            "updated_at": value["issued_at"],
            "uncertainty_codes": [],
            **{
                k: value[k]
                for k in (
                    "authority_id",
                    "authority_epoch",
                    "session_id",
                    "session_header_sha256",
                    "runtime_profile_sha256",
                    "source_revision",
                    "project",
                    "nonce",
                    "request_id",
                )
            },
        }
        raw_record = sql.canonical_bytes(coordination._seal_record(record))
        wire.qualify_reservation(raw_record)
        return self._approve(
            BridgeBodyApproval(
                body, context, raw_record, sql.canonical_bytes(self.registry.reference("preflight"))
            )
        )

    def register_request(self, *, raw, approval):
        self._live()
        request = wire.parse_request_bytes(raw).to_dict()
        body = {
            k: v
            for k, v in request.items()
            if k not in ("capability", "sender_signature", "request_digest")
        }
        digest = hashlib.sha256(sql.canonical_bytes(body)).hexdigest()
        _require(
            self.approvals.get(digest) is approval
            and sql.canonical_bytes(body) == approval.canonical_body,
            "REQUEST_CONTEXT_INVALID",
        )
        from dataclasses import replace

        context = replace(approval.context, now=self.journal._now())
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(context.holder_public_key_hex))
        if type(context) is wire.RequestContext:
            wire.verify_request(
                raw, expected_context=context, bridge_pin=self.pin, holder_public_key=key
            )
        else:
            wire.verify_recovery_request(
                raw, expected_context=context, bridge_pin=self.pin, operator_public_key=key
            )
        self._live()
        sha = hashlib.sha256(raw).hexdigest()
        _require(sha not in self.requests, "REPLAY_CONFLICT")
        self.requests[sha] = approval
        return None

    def request_context(self, raw, *, operation):
        self._live()
        approval = self.requests.get(hashlib.sha256(raw).hexdigest())
        _require(
            type(approval) is BridgeBodyApproval
            and type(approval.context) is wire.RequestContext
            and approval.context.operation == operation,
            "HOST_CONTEXT_INVALID",
        )
        native = self.sessions.qualify()
        self.policy.qualify(native=native, journal=self.journal)
        from dataclasses import replace

        context = replace(approval.context, now=self.journal._now())
        self._live()
        return context

    def verify_reservation_context(self, raw, authenticated):
        self._live()
        approval = self.requests.get(authenticated.request.sha256)
        _require(
            type(approval) is BridgeBodyApproval and approval.reservation_record == raw,
            "RESERVATION_CONTEXT_INVALID",
        )
        return None

    def _save(self, name, raw, schema, identity):
        sql.validate_scalar(identity, {"type": "uuid", "not_null": True})
        path = self.artifacts.root + "/" + name + "-" + identity + ".json"
        self.artifacts.create_exact(path, raw)
        return HostArtifactBinding(
            name, sql.canonical_bytes(_artifact_reference(path, raw, schema)), self.artifacts
        )

    def remember_permit(self, *, permit, opening):
        self._live()
        opening.check()
        _require(
            type(opening) is coordination.OpeningAttempt
            and opening.journal is self.journal
            and opening.request.request.sha256 in self.requests,
            "HOST_CONTEXT_INVALID",
        )
        record = opening.record
        native = self.sessions.qualify()
        request = opening.request.request.to_dict()
        self.policy.qualify(native=native, journal=self.journal)
        references = self.policy.reserve_references(
            registry=self.registry,
            native=native,
            snapshot=opening.snapshot,
            issued_at=coordination._utc(self.journal._now()),
            live_clock=self.journal._now,
        )
        _require(
            all(request["payload"][k] == v for k, v in references.items()),
            "REQUEST_CONTEXT_INVALID",
        )
        approval = self.requests[opening.request.request.sha256]
        expected = {
            "namespace": "S11C3-EX10",
            "authority_id": self.journal.authority_id,
            "authority_epoch": self.journal.authority_epoch,
            "authorization_reference": sql.parse_canonical(self.authorization_reference),
            "base_authority_revision": record["previous_authority_revision"],
            "expected_open_revision": record["expected_next_revision"],
            "bridge_key_id": self.pin.key_id,
            "bridge_key_epoch": self.pin.key_epoch,
            "container_identity_digest": native.process.identity_digest,
            "deployment_verification_sha256": native.process.deployment_verification_sha256,
            "exchange_id": request["exchange_id"],
            "holder_public_key_hex": approval.context.holder_public_key_hex,
            "attempt_nonce": record["nonce"],
            "preflight_reference": self.registry.reference("preflight"),
            "reserve_request_id": request["request_id"],
            "reserve_request_digest": request["request_digest"],
            **{
                k: record[k]
                for k in (
                    "session_id",
                    "session_header_sha256",
                    "runtime_profile_sha256",
                    "source_revision",
                    "project",
                    "reservation_id",
                    "reservation_digest",
                    "reservation_receipt_reference",
                    "fencing_token",
                    "run_identity",
                    "acknowledgment_digest",
                )
            },
        }
        holder = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(approval.context.holder_public_key_hex)
        )
        wire.verify_open_one(
            permit.canonical,
            expected_context=wire.PermitContext(
                sql.canonical_bytes(expected),
                wire._time(record["freshness_deadline"]),
                self.journal._now(),
            ),
            bridge_pin=self.pin,
            holder_public_key=holder,
        )
        identity = record["reservation_id"]
        _require(identity not in self.grants, "REPLAY_CONFLICT")
        self.grants[identity] = {
            "permit": permit,
            "opening": opening,
            "holder": holder,
            "bindings": [
                self._save(
                    "record", sql.canonical_bytes(record), record["schema_version"], identity
                ),
                self._save(
                    "reserve_request",
                    opening.request.request.canonical,
                    request["schema_version"],
                    identity,
                ),
                self._save(
                    "permit", permit.canonical, permit.to_dict()["schema_version"], identity
                ),
                HostArtifactBinding(
                    "reservation_receipt",
                    sql.canonical_bytes(record["reservation_receipt_reference"]),
                    self.journal.receipt_store,
                ),
            ],
        }
        self._live()
        opening.check()
        return None

    def approve_commit(self, *, body, native, holder, opening, permit):
        self._live()
        opening.check()
        record = opening.record
        grant = self.grants.get(record["reservation_id"])
        _require(
            grant is not None
            and grant["opening"] is opening
            and grant["permit"].canonical == permit.canonical
            and grant["holder"].public_bytes_raw() == holder.public_bytes_raw()
            and opening.record["state"] == "PENDING"
            and opening.open_result is None,
            "HOST_CONTEXT_INVALID",
        )
        self.policy.postcheck(native)
        self.policy.qualify(native=native, journal=self.journal)
        value = self._body(body, "commit-open")
        payload = value["payload"]
        before = self._base(self.read_authority())
        _require(
            before == opening.request.request.to_dict()["payload"]["expected_head"],
            "HISTORY_INVALID",
        )
        expected = {
            **{
                k: record[k]
                for k in (
                    "reservation_id",
                    "reservation_digest",
                    "fencing_token",
                    "run_identity",
                    "reservation_receipt_reference",
                )
            },
            "reserve_request_id": record["request_id"],
            "open_permit_digest": permit.to_dict()["permit_digest"],
            "expected_head": before,
        }
        _require(all(payload[k] == v for k, v in expected.items()), "REQUEST_CONTEXT_INVALID")
        context = self._context(
            value,
            native=native,
            holder=holder,
            revision=before["revision"],
            run_id=record["run_id"],
            record=record,
        )
        _require(
            value["request_id"] != record["request_id"] and value["nonce"] != record["nonce"],
            "REQUEST_CONTEXT_INVALID",
        )
        identity = record["run_identity"]
        root = native.profile.to_dict()["host_roots"]["evidence"]["path"]
        expectations = tuple(
            evidence.ArtifactExpectation(
                field, root + "/runs/" + identity["run_id"] + "/metadata/EX-10/" + filename, 1048576
            )
            for field, filename in zip(
                evidence.REFERENCE_FIELDS,
                (
                    "run-manifest.initial.json",
                    "ex10-start.json",
                    "ex10-preflight.json",
                    "ex10-plan.json",
                ),
                strict=True,
            )
        )
        pc = permit.to_dict()
        evd_context = evidence.OpenEvidenceContext(
            record["request_id"],
            record["reservation_id"],
            record["fencing_token"],
            pc["permit_digest"],
            record["runtime_profile_sha256"],
            record["session_header_sha256"],
            sql.canonical_bytes(identity),
            wire._time(pc["issued_at"]),
            wire._time(pc["expires_at"]),
            self.journal._now(),
            self.fingerprint,
            self.policy.checks,
            expectations,
        )
        raw = sql.canonical_bytes(payload["open_evidence"])
        with evidence.ConfinedArtifactResolver(
            root, expected_owner=native.profile.to_dict()["expected_owner"]["uid"]
        ) as resolver:
            accepted = evidence.verify_open_evidence(
                raw,
                expected_context=evd_context,
                executor_public_key=holder,
                artifact_resolver=resolver,
            )
        _require(
            next(a.canonical for a in accepted.artifacts if a.field == "preflight_reference")
            == self.registry.read("preflight"),
            "PREFLIGHT_INVALID",
        )
        self.policy.postcheck(native)
        self._live()
        opening.check()
        grant["evidence"] = self._save(
            "evidence", raw, evidence.SCHEMA_VERSION, value["request_id"]
        )
        self._live()
        return self._approve(BridgeBodyApproval(body, context))

    def qualify_open(self, *, authenticated_request, opening):
        self._live()
        opening.check()
        request = authenticated_request.request.to_dict()
        grant = self.grants.get(opening.record["reservation_id"])
        _require(
            grant is not None
            and grant["opening"] is opening
            and "evidence" in grant
            and authenticated_request.request.sha256 in self.requests,
            "HOST_CONTEXT_INVALID",
        )
        native = self.sessions.qualify()
        self.policy.qualify(native=native, journal=self.journal)
        descriptor = self.sessions.registry.bindings["historical_lifecycle_authority_reference"]
        bindings = [
            *self.registry.bindings.values(),
            *grant["bindings"],
            grant["evidence"],
            HostArtifactBinding("descriptor", descriptor.reference, descriptor.store),
            self._save(
                "commit_request",
                authenticated_request.request.canonical,
                request["schema_version"],
                request["request_id"],
            ),
            self._save(
                "base_head",
                opening.snapshot.canonical_current_head,
                "s11-ex10-authority-witness-head-v1",
                request["request_id"],
            ),
        ]
        qualifier = NativeGovernanceQualifier(
            session_qualifier=self.sessions,
            registry=HostArtifactRegistry(tuple(bindings)),
            certificate_store=self.certificates,
            bridge_pin=self.pin,
            holder_public_key=grant["holder"],
            reservation_id=opening.record["reservation_id"],
            executor_fingerprint=self.fingerprint,
            required_preflight_checks=self.policy.checks,
            protocol=evidence.ex10_load.PROTOCOL_ID,
            methodology=evidence.ex10_load.METHODOLOGY_VERSION,
            host_policy=self.policy,
        )
        prepared = self.publisher.prepare(
            qualifier=qualifier, opening=opening, authenticated_request=authenticated_request
        )
        reference = sql.parse_canonical(prepared.governance_reference)["reference_id"]
        _require(reference not in self.catalog, "GOVERNANCE_REUSE")
        self.catalog[reference] = qualifier
        return prepared

    def sign_response(self, *, authenticated_request, opening):
        opening.check()
        record = opening.record
        request = authenticated_request.request.to_dict()
        _require(
            record["state"] == "COMMITTED"
            and opening.open_result is not None
            and authenticated_request.request.sha256 in self.requests,
            "HOST_CONTEXT_INVALID",
        )
        head = history.current_revision(opening.open_result)
        _require(
            (head["revision"], head["witness_digest"])
            == (
                record["expected_next_revision"],
                record["open_witness_reference"]["witness_digest"],
            ),
            "HISTORY_INVALID",
        )
        # Reuse the fixed typed signer/schema builder; no nested exchange or dispatch.
        broker = NativeBridgeBroker(
            journal=self.journal,
            session_qualifier=self.sessions,
            signer=self.signer,
            host_sources=self,
            holder_admitter=lambda **kw: None,
        )
        response = broker._normal_response(
            authenticated_request,
            result={
                "open_evidence": request["payload"]["open_evidence"],
                "open_witness": record["open_witness_reference"],
                "opened": True,
                "reservation": record,
            },
            before=record["previous_authority_revision"],
            after=record["expected_next_revision"],
            run_id=record["run_id"],
            reservation_id=record["reservation_id"],
            witness_head=record["open_witness_reference"],
        )
        self.archive_response(authenticated_request=authenticated_request, raw_response=response)
        return response

    def archive_response(self, *, authenticated_request, raw_response):
        _require(
            type(authenticated_request) is wire.AuthenticatedRequest
            and authenticated_request.request.sha256 in self.requests,
            "HOST_CONTEXT_INVALID",
        )
        response = wire.verify_response(
            raw_response,
            authenticated_request=authenticated_request,
            bridge_pin=self.pin,
            now=self.journal._now(),
        )
        value = response.to_dict()
        _require(
            value["status"] == "OK" and value["operation"] in ("reserve-start", "commit-open"),
            "HOST_CONTEXT_INVALID",
        )
        self._save("saved_response", raw_response, value["schema_version"], value["request_id"])
        return None

    def saved_response_reference(self, *, state, request_id):
        raw = state["responses"].get(request_id)
        if raw is None:
            return None
        sql.validate_scalar(request_id, {"type": "uuid", "not_null": True})
        path = self.artifacts.root + "/saved_response-" + request_id + ".json"
        _require(self.artifacts.read(path) == raw, "SAVED_RESPONSE_INVALID")
        value = wire.parse_response_bytes(raw).to_dict()
        _require(
            value["request_id"] == request_id and value["status"] == "OK", "SAVED_RESPONSE_INVALID"
        )
        return _artifact_reference(path, raw, value["schema_version"])

    def recovery_authority(self, *, body, native):
        _require(self.recovery is not None, "RECOVERY_APPROVAL_REQUIRED")
        observer = getattr(self.recovery, "observe_authority", None)
        _require(callable(observer), "RECOVERY_APPROVAL_REQUIRED")
        observed = _host_call(
            observer,
            "RECOVERY_APPROVAL_REQUIRED",
            canonical_body=sql.canonical_bytes(body),
            native=native,
            journal=self.journal,
        )
        _require(type(observed) is NativeRecoveryAuthority, "RECOVERY_APPROVAL_REQUIRED")
        snapshot = self.qualify_authority(snapshot=observed.authority_snapshot, native=native)
        publisher = self.publisher
        _require(
            (publisher.ledger.root, publisher.ledger.identity, publisher.name, publisher.identity)
            == (
                self.policy.ledger.root,
                self.policy.ledger.identity,
                self.policy.name,
                self.policy.identity,
            ),
            "HOST_POLICY_INVALID",
        )
        with durable.existing_database(
            publisher.ledger,
            publisher.name,
            publisher.identity,
            write=False,
            schema_check=sql._verify_sql_schema,
        ) as con:
            state = sql._ordered_state(sql._read_state(con))
        completion = observed.authority_completion
        if completion is None:
            _require(
                state == sql._ordered_state(history._verified(snapshot)["state"]),
                "RECOVERY_APPROVAL_REQUIRED",
            )
        else:
            _require(
                type(completion) is coordination.ManualAuthorityCompletion
                and (
                    completion.ledger_store.root,
                    completion.ledger_store.identity,
                    completion.ledger_name,
                    completion.ledger_identity,
                )
                == (
                    publisher.ledger.root,
                    publisher.ledger.identity,
                    publisher.name,
                    publisher.identity,
                )
                and completion.candidate_store is publisher.candidates
                and completion.floor is publisher.floor
                and state == sql._ordered_state(sql.parse_canonical(completion.base_state)),
                "RECOVERY_APPROVAL_REQUIRED",
            )
            completion.validate(
                approved_snapshot=snapshot,
                operation=body["operation"],
                operator_reference=body["payload"]["operator_authorization_reference"],
            )
        metadata = state["metadata"][0]
        revision = len(state["history_revisions"]) - 1
        receipt = history._receipts(state)[revision]
        raw = sql.canonical_bytes(receipt)
        _require(
            (metadata["authority_id"], metadata["epoch"])
            == (self.journal.authority_id, self.journal.authority_epoch)
            and self.registry.read("base_receipt") == raw
            and sql.logical_digest(state, revision) == receipt["ledger_digest"],
            "RECOVERY_APPROVAL_REQUIRED",
        )
        base = {
            "authority_id": metadata["authority_id"],
            "authority_epoch": metadata["epoch"],
            "revision": revision,
            "ledger_digest": receipt["ledger_digest"],
            "witness_digest": sql.digest(receipt),
            "receipt_reference": self.registry.reference("base_receipt"),
        }
        fresh = body["payload"]["fresh_authority_head"]
        current = history.current_revision(snapshot)
        _require(
            (
                fresh["authority_id"],
                fresh["authority_epoch"],
                fresh["revision"],
                fresh["ledger_digest"],
                fresh["witness_digest"],
            )
            == (
                current["authority_id"],
                current["epoch"],
                current["revision"],
                current["ledger_digest"],
                current["witness_digest"],
            ),
            "RECOVERY_APPROVAL_REQUIRED",
        )
        if fresh != base:
            _require(
                body["operation"] == "resolve-uncertain-as-committed"
                and completion is not None
                and fresh["revision"] == revision + 1
                and fresh == body["payload"]["existing_open_witness_reference"]
                and fresh["receipt_reference"]["sha256"] == fresh["witness_digest"],
                "RECOVERY_APPROVAL_REQUIRED",
            )
        if body["operation"] == "resolve-uncertain-as-committed":
            coordinated = self.journal.read()
            payload = body["payload"]
            record = coordinated["latest"].get(payload["reservation_id"])
            _require(
                record is not None
                and record["state"] == "UNCERTAIN"
                and (record["record_sequence"], record["record_digest"])
                == (payload["expected_record_sequence"], payload["expected_record_digest"])
                and current["revision"] == record["expected_next_revision"]
                and payload["run_identity"] == record["run_identity"]
                and payload["existing_open_witness_reference"] == fresh,
                "RECOVERY_APPROVAL_REQUIRED",
            )
            material = history._verified(snapshot)["state"]
            run = history.find_run(snapshot, record["run_id"])
            revision_row = next(
                row
                for row in material["history_revisions"]
                if row["revision"] == current["revision"]
            )
            commit = coordinated["claims"].get(revision_row["operation_id"])
            _require(
                run is not None
                and run["lifecycle_state"] == "OPEN"
                and run["introduced_revision"] == record["expected_next_revision"]
                and commit is not None
                and commit["reservation_id"] == record["reservation_id"]
                and commit["request"]["operation"] == "commit-open"
                and commit["request"]["payload"]["run_identity"] == record["run_identity"]
                and commit["request"]["payload"]["open_evidence"] == payload["open_evidence"]
                and (
                    completion is None
                    or completion.publication_operation_id == revision_row["operation_id"]
                ),
                "RECOVERY_APPROVAL_REQUIRED",
            )
        self.policy.postcheck(native)
        self._live()
        return snapshot, base

    def owner_base(self):
        _require(callable(self.owner) and callable(self.uncertainty), "OWNER_LEASE_REQUIRED")
        scope = _host_call(self.owner, "OWNER_LEASE_REQUIRED")
        _require(
            type(scope) is NativeOpeningScope and scope.journal is self.journal,
            "OWNER_LEASE_REQUIRED",
        )
        scope.check()
        record = scope.opening.record
        if scope.opening.open_result is not None:
            known = scope.opening.open_result
            # Read a known completed fact only. Expiry continues to prohibit
            # authorization/START/queries via history._verified; preserving a
            # blocker must not require reviving that expired observation.
            _require(
                type(known) is history._VerifiedSnapshot and known.token is history._VERIFIED_TOKEN,
                "OWNER_LEASE_REQUIRED",
            )
            state = known.material["state"]
            revision = len(state["history_revisions"]) - 1
            receipt = history._receipts(state)[revision]
            floor = self.publisher.floor
            raw = sql.canonical_bytes(receipt)
            path = floor._path(revision)
            accepted = floor.current()
            _require(
                revision == record["expected_next_revision"]
                and (receipt["authority_id"], receipt["authority_epoch"])
                == (self.journal.authority_id, self.journal.authority_epoch)
                and accepted.revision >= revision
                and floor.store.read(path) == raw,
                "OWNER_LEASE_REQUIRED",
            )
            base = {
                "authority_id": self.journal.authority_id,
                "authority_epoch": self.journal.authority_epoch,
                "revision": revision,
                "ledger_digest": receipt["ledger_digest"],
                "witness_digest": sql.digest(receipt),
                "receipt_reference": _artifact_reference(
                    path, raw, receipt["witness_schema_version"]
                ),
            }
            _require(
                record["open_witness_reference"] is None
                or record["open_witness_reference"] == base,
                "OWNER_LEASE_REQUIRED",
            )
            return scope, base
        # This is the authenticated original grant's known base, not an
        # observation authorizing another OPEN. A diverged/unavailable B3 must
        # not prevent the real owner from preserving the UNCERTAIN blocker.
        base = scope.opening.request.request.to_dict()["payload"]["expected_head"]
        _require(
            (
                base["authority_id"],
                base["authority_epoch"],
                base["revision"],
                base["ledger_digest"],
                base["witness_digest"],
            )
            == (
                self.journal.authority_id,
                self.journal.authority_epoch,
                record["previous_authority_revision"],
                record["previous_ledger_digest"],
                record["previous_witness_digest"],
            )
            and base["receipt_reference"] == self.registry.reference("base_receipt")
            and hashlib.sha256(self.registry.read("base_receipt")).hexdigest()
            == base["witness_digest"],
            "OWNER_LEASE_REQUIRED",
        )
        return scope, base

    def approve_single(self, *, body, native, holder, role):
        self._live()
        self.policy.postcheck(native)
        self.policy.qualify(native=native, journal=self.journal)
        operation = sql.parse_canonical(body)["operation"]
        allowed = (
            (NORMAL_OPERATIONS - {"reserve-start", "commit-open"})
            if role == "SESSION_EXECUTOR"
            else wire.RECOVERY_OPERATIONS
        )
        _require(
            role in ("SESSION_EXECUTOR", "RECOVERY_OPERATOR") and operation in allowed,
            "ROLE_OPERATION_DENIED",
        )
        value = self._body(body, operation)
        payload = value["payload"]
        if operation == "record-uncertain":
            owner_scope, base = self.owner_base()
            snapshot = None
        elif operation in wire.RECOVERY_OPERATIONS:
            snapshot, base = self.recovery_authority(body=value, native=native)
        else:
            snapshot = self.read_authority()
            base = self._base(snapshot)
        state = self.journal.read()
        record = None
        run_id = None
        if operation in wire.RECOVERY_OPERATIONS:
            _require(self.recovery is not None, "RECOVERY_APPROVAL_REQUIRED")
            approval = _host_call(
                self.recovery.approve_body,
                "RECOVERY_APPROVAL_REQUIRED",
                body=body,
                native=native,
                holder=holder,
                journal=self.journal,
                snapshot=snapshot,
            )
            _require(
                type(approval) is BridgeBodyApproval
                and type(approval.context) is wire.RecoveryRequestContext
                and approval.canonical_body == body,
                "RECOVERY_APPROVAL_REQUIRED",
            )
            record = state["latest"].get(payload["reservation_id"])
            _require(
                record is not None
                and record["state"] == "UNCERTAIN"
                and (payload["expected_record_sequence"], payload["expected_record_digest"])
                == (record["record_sequence"], record["record_digest"])
                and sql.parse_canonical(approval.context.operator_authorization_reference)
                == payload["operator_authorization_reference"],
                "RECOVERY_APPROVAL_REQUIRED",
            )
            common = self._context(
                value,
                native=native,
                holder=holder,
                revision=base["revision"],
                run_id=record["run_id"],
                record=record,
            )
            from dataclasses import fields

            _require(
                all(
                    getattr(approval.context, f.name) == getattr(common, f.name)
                    for f in fields(common)
                    if f.name != "now"
                ),
                "RECOVERY_APPROVAL_REQUIRED",
            )
            return self._approve(approval)
        if operation in ("inspect-history", "verify-history-authority"):
            _require(payload["expected_head"] == base, "HISTORY_INVALID")
            if operation == "verify-history-authority":
                profile = native.profile.to_dict()
                _require(
                    payload["descriptor_reference"]
                    == profile["historical_lifecycle_authority_reference"]
                    and payload["deployment_verification_reference"]
                    == profile["deployment_verification_reference"],
                    "HISTORY_INVALID",
                )
        elif operation == "inspect-coordination":
            _require(
                (payload["expected_authority_id"], payload["expected_authority_epoch"])
                == (self.journal.authority_id, self.journal.authority_epoch),
                "HOST_CONTEXT_INVALID",
            )
        elif operation == "inspect-reservation":
            claim = state["claims"].get(payload["original_request_id"])
            record = state["latest"].get(payload["reservation_id"])
            _require(
                claim is not None
                and record is not None
                and claim["reservation_id"] == record["reservation_id"],
                "RESERVATION_CONTEXT_INVALID",
            )
            if record is not None:
                run_id = record["run_id"]
        elif operation == "record-uncertain":
            owner_scope.check()
            opening = owner_scope.opening
            record = opening.record
            run_id = record["run_id"]
            _require(
                record["state"] in ("PENDING", "COMMITTED", "UNCERTAIN")
                and (payload["reservation_id"], payload["fencing_token"])
                == (record["reservation_id"], record["fencing_token"]),
                "OWNER_LEASE_REQUIRED",
            )
            _require(
                _host_call(
                    self.uncertainty,
                    "UNCERTAINTY_APPROVAL_REQUIRED",
                    canonical_body=body,
                    native=native,
                    opening=opening,
                    holder=holder,
                )
                is None,
                "UNCERTAINTY_APPROVAL_REQUIRED",
            )
        context = self._context(
            value,
            native=native,
            holder=holder,
            revision=base["revision"],
            run_id=run_id,
            record=record,
        )
        if operation == "inspect-reservation":
            from dataclasses import replace

            context = replace(context, reservation_id=payload["reservation_id"])
        if operation == "record-uncertain":
            self.approved_owner = (owner_scope, body)
        return self._approve(BridgeBodyApproval(body, context))

    def dispatch_single(self, *, authenticated_request, role):
        self._live()
        request = authenticated_request.request.to_dict()
        operation = request["operation"]
        _require(authenticated_request.request.sha256 in self.requests, "HOST_CONTEXT_INVALID")
        payload = request["payload"]
        if operation == "record-uncertain":
            _, base = self.owner_base()
            snapshot = None
        elif role == "RECOVERY_OPERATOR":
            body = {
                k: v
                for k, v in request.items()
                if k not in ("capability", "sender_signature", "request_digest")
            }
            snapshot, base = self.recovery_authority(body=body, native=self.sessions.qualify())
        else:
            snapshot = self.read_authority()
            base = self._base(snapshot)
        state = self.journal.read()
        record = None
        coord_head = None
        if role == "RECOVERY_OPERATOR":
            _require(operation in wire.RECOVERY_OPERATIONS, "ROLE_OPERATION_DENIED")
            record = self.journal.recover(raw_request=authenticated_request.request.canonical)
            _require(type(record) is dict, "UNCERTAIN_PUBLICATION")
            snapshot = self.read_authority()
            base = self._base(snapshot, require_configured=False)
            if operation == "resolve-uncertain-as-committed":
                result = {
                    "committed": True,
                    "new_open_executed": False,
                    "reservation": record,
                    "run_id": record["run_id"],
                }
            elif operation == "resolve-uncertain-as-not-committed":
                result = {
                    "authority_write_closed": snapshot.write_closed,
                    "no_open_proven": True,
                    "reservation": record,
                }
            else:
                result = {
                    "authority_write_closed": snapshot.write_closed,
                    "new_start_permitted_by_this_response": False,
                    "reservation": record,
                }
            coord_head = self.journal.fresh_head(self.journal.read())
        elif operation == "inspect-history":
            _require(payload["expected_head"] == base, "HISTORY_INVALID")
            rows = sorted(history.list_official_runs(snapshot), key=lambda r: r["run_id"])
            if payload["cursor_after_run_id"] is not None:
                rows = [r for r in rows if r["run_id"] > payload["cursor_after_run_id"]]
            page = rows[: payload["limit"]]
            facts = history.first_valid_status(snapshot)
            _require(facts["status"] != "HISTORY_INVALID", "HISTORY_INVALID")
            ack = []
            for r in history.list_official_runs(snapshot):
                if (
                    r["lifecycle_state"] == "TERMINAL"
                    and r["result"] == "NOT_DEMONSTRATED"
                    and r["ack_required"]
                    and not r["binding"]
                ):
                    _require(
                        history.verify_ack_target(snapshot, r["run_id"])["recognizable"],
                        "ACK_INVALID",
                    )
                    ack.append(r["run_id"])
            result = {
                "blocking_run_ids": facts["blocking_run_ids"],
                "complete": len(rows) <= len(page),
                "consumed": facts["consumed"],
                "first_valid_status": facts["status"],
                "nd_ack_targets": sorted(ack),
                "observation_expires_at": snapshot.expires_at,
                "observed_revision": base["revision"],
                "run_ids": [r["run_id"] for r in page],
            }
        elif operation == "verify-history-authority":
            _require(payload["expected_head"] == base, "HISTORY_INVALID")
            result = {
                "bootstrap_complete": True,
                "descriptor_sha256": sql.digest(history._verified(snapshot)["descriptor"]),
                "observation_expires_at": snapshot.expires_at,
                "observed_head": base,
                "verified": True,
            }
        elif operation == "inspect-reservation":
            claim = state["claims"].get(payload["original_request_id"])
            record = state["latest"].get(payload["reservation_id"])
            _require(
                claim is not None
                and record is not None
                and claim["reservation_id"] == record["reservation_id"],
                "RESERVATION_CONTEXT_INVALID",
            )
            result = {
                "original_response_reference": self.saved_response_reference(
                    state=state, request_id=payload["original_request_id"]
                ),
                "reservation": record,
            }
        elif operation == "inspect-coordination":
            coord_head = self.journal.fresh_head(state)
            record = None if state["active"] is None else state["latest"][state["active"]]
            facts = history.first_valid_status(snapshot)
            _require(facts["status"] != "HISTORY_INVALID", "HISTORY_INVALID")
            result = {
                "authority_revision": base["revision"],
                "consistent": True,
                "coordination_sequence": state["sequence"],
                "global_start_blocked": state["active"] is not None or facts["status"] != "NONE",
                "lock_state": "NOT_PROBED",
                "reservation": record,
            }
        elif operation == "record-uncertain":
            _require(
                role == "SESSION_EXECUTOR" and hasattr(self, "approved_owner"),
                "OWNER_LEASE_REQUIRED",
            )
            owner_scope, body = self.approved_owner
            owner_scope.check()
            opening = owner_scope.opening
            _require(
                body == self.requests[authenticated_request.request.sha256].canonical_body,
                "OWNER_LEASE_REQUIRED",
            )
            record = opening.record_uncertain(raw_request=authenticated_request.request.canonical)
            _require(type(record) is dict, "UNCERTAIN_PUBLICATION")
            owner_scope.close()
            result = {"global_start_blocked": True, "reservation": record}
            coord_head = self.journal.fresh_head(self.journal.read())
        else:
            raise OperationalError("ROLE_OPERATION_DENIED")
        if operation != "record-uncertain" and role != "RECOVERY_OPERATOR":
            self._live()
        return SingleOperationResult(
            sql.canonical_bytes(result),
            request["authority_revision"],
            base["revision"],
            sql.canonical_bytes(base),
            None if record is None else record["run_id"],
            payload["reservation_id"]
            if operation == "inspect-reservation"
            else (None if record is None else record["reservation_id"]),
            None if coord_head is None else sql.canonical_bytes(coord_head),
        )

    def verify_uncertainty(self, *, authenticated_request, opening):
        _require(
            hasattr(self, "approved_owner")
            and self.approved_owner[0].opening is opening
            and self.requests.get(authenticated_request.request.sha256) is not None
            and self.requests[authenticated_request.request.sha256].canonical_body
            == self.approved_owner[1],
            "OWNER_LEASE_REQUIRED",
        )
        opening.check()
        self._live()
        return None

    def recovery_context(self, raw, *, operation):
        self._live()
        approval = self.requests.get(hashlib.sha256(raw).hexdigest())
        _require(
            type(approval) is BridgeBodyApproval
            and type(approval.context) is wire.RecoveryRequestContext
            and approval.context.operation == operation,
            "RECOVERY_APPROVAL_REQUIRED",
        )
        native = self.sessions.qualify()
        self.policy.qualify(native=native, journal=self.journal)
        from dataclasses import replace

        context = replace(approval.context, now=self.journal._now())
        self._live()
        return context

    def qualify_recovery(self, **arguments):
        _require(self.recovery is not None, "RECOVERY_APPROVAL_REQUIRED")
        result = _host_call(
            self.recovery.qualify_recovery, "RECOVERY_APPROVAL_REQUIRED", **arguments
        )
        _require(type(result) is coordination.RecoveryQualification, "RECOVERY_APPROVAL_REQUIRED")
        history._verified(result.authority_snapshot)
        contexts = _host_call(
            self.histories,
            "HISTORICAL_GOV_UNQUALIFIED",
            snapshot=result.authority_snapshot,
            journal=self.journal,
        )
        _require(type(contexts) is NativeGovernanceHistory, "HISTORICAL_GOV_UNQUALIFIED")
        merged = dict(contexts.contexts)
        for reference, qualifier in self.catalog.items():
            _require(
                reference not in merged or merged[reference] is qualifier, "HISTORICAL_GOV_INVALID"
            )
            merged[reference] = qualifier
        NativeGovernanceHistory(tuple(merged.items())).qualify(
            snapshot=result.authority_snapshot, journal=self.journal
        )
        self._live()
        return result

    def verify_manual_event(self, **arguments):
        _require(self.recovery is not None, "RECOVERY_APPROVAL_REQUIRED")
        _require(
            _host_call(self.recovery.verify_manual_event, "RECOVERY_APPROVAL_REQUIRED", **arguments)
            is None,
            "RECOVERY_APPROVAL_REQUIRED",
        )
        return None

    def qualify_owner_loss(self, **arguments):
        _require(self.recovery is not None, "RECOVERY_APPROVAL_REQUIRED")
        return _host_call(
            self.recovery.qualify_owner_loss, "RECOVERY_APPROVAL_REQUIRED", **arguments
        )

    def verify_owner_loss(self, **arguments):
        _require(self.recovery is not None, "RECOVERY_APPROVAL_REQUIRED")
        _require(
            _host_call(self.recovery.verify_owner_loss, "RECOVERY_APPROVAL_REQUIRED", **arguments)
            is None,
            "RECOVERY_APPROVAL_REQUIRED",
        )
        return None

    def qualify_terminal(self, **arguments):
        _require(self.lifecycle is not None, "TERMINAL_APPROVAL_REQUIRED")
        # Full original GOV qualification precedes internal lifecycle publication.
        self.read_authority()
        return _host_call(
            self.lifecycle.qualify_terminal, "TERMINAL_APPROVAL_REQUIRED", **arguments
        )
