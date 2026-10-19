"""Pure shared EX-10 prior policy and caller-locked FD custody reading.

No Django, executor import, runtime settings, root creation or scientific action.
Host code supplies its qualified root, installed custody material and fixed fences.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .constants import ArtifactKind, Classification, Decision
from .custody_policy import (
    CustodyPolicyError,
    PublicationApproval,
    validate_custody_index_signature,
    validate_custody_record_signature,
    validate_public_approval,
)
from .evidence import EvidencePolicyError, validate_finalized_payload

OFFICIAL_MODE = "OFFICIAL"
LOCAL_TEST_MODE = "LOCAL_TEST"
_RUN_ID = re.compile(r"^s11-[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class PriorContextUnavailable(RuntimeError):
    """No known domain refusal may be inferred from unsupported context."""


@dataclass(frozen=True, slots=True)
class PriorRun:
    run_id: str
    evidence_mode: str | None
    finalized: bool
    decision: str | None
    problems: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "evidence_mode": self.evidence_mode,
            "finalized": self.finalized,
            "decision": self.decision,
            "problems": list(self.problems),
        }


def binding_refusal(
    prior: Sequence[PriorRun], *, evidence_mode: str, acknowledged: Iterable[str]
) -> tuple[list[str], bool]:
    """Motivos para no abrir un run nuevo y si son definitivos (primer run válido).

    Un run oficial `PASS` o `FAIL` es el resultado de EX-10: ninguno oficial más. Un run
    abierto (de cualquier modo) impide otro. Tras runs oficiales `NOT_DEMONSTRATED`, un
    nuevo intento sólo si el operador los reconoce uno a uno: nunca automáticamente.
    """
    acknowledged = set(acknowledged)
    reasons: list[str] = []
    terminal = False
    for run in prior:
        if run.problems:
            reasons.append(f"prior-run-unverifiable:{run.run_id}")
        elif not run.finalized:
            reasons.append(f"active-run:{run.run_id}")
        elif run.evidence_mode == OFFICIAL_MODE and evidence_mode == OFFICIAL_MODE:
            if run.decision in {Decision.PASS, Decision.FAIL}:
                reasons.append(f"binding-run-exists:{run.run_id}")
                terminal = True
            elif run.run_id not in acknowledged:
                reasons.append(f"not-demonstrated-unacknowledged:{run.run_id}")
    known = {
        run.run_id
        for run in prior
        if run.evidence_mode == OFFICIAL_MODE and run.decision == Decision.NOT_DEMONSTRATED
    }
    reasons.extend(f"acknowledged-unknown:{run_id}" for run_id in sorted(acknowledged - known))
    return reasons, terminal


class PriorCustodyError(ValueError):
    """Physical/parsing failure from a known prior run, never an approval error."""


class _PriorFDReader:
    """Borrow an already qualified root FD; own and close every traversal FD."""

    def __init__(self, root_fd, check_current):
        self.root_fd, self.current = root_fd, check_current
        self.facts, self.listings = {}, {}
        root = os.fstat(root_fd)
        self.owner = (root.st_uid, root.st_gid)
        self._directory(root)

    @staticmethod
    def _stamp(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_uid,
            info.st_gid,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_rdev,
        )

    @staticmethod
    def _parts(path):
        if (
            type(path) is not str
            or not path
            or "\x00" in path
            or any(p in ("", ".", "..") for p in path.split("/"))
        ):
            raise PriorCustodyError("prior-path-invalid")
        try:
            path.encode("utf8")
        except UnicodeError as error:
            raise PriorCustodyError("prior-path-invalid") from error
        return tuple(path.split("/"))

    def _directory(self, info):
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or (info.st_uid, info.st_gid) != self.owner
        ):
            raise PriorCustodyError("prior-directory-invalid")

    def _fact(self, parts, info):
        value = None if info is None else self._stamp(info)
        if parts in self.facts and self.facts[parts] != value:
            raise PriorCustodyError("prior-read-race")
        self.facts[parts] = value

    def _stat(self, parent, name):
        self.current()
        try:
            return os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise PriorCustodyError("prior-stat-unreadable") from error

    def _parent(self, parts, stack):
        parent = self.root_fd
        for count, name in enumerate(parts[:-1], start=1):
            mapped_before = self._stat(parent, name)
            self._fact(parts[:count], mapped_before)
            if mapped_before is None:
                return None
            self._directory(mapped_before)
            self.current()
            try:
                fd = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                    dir_fd=parent,
                )
            except FileNotFoundError:
                self._fact(parts[:count], None)
                return None
            except OSError as error:
                raise PriorCustodyError("prior-directory-unreadable") from error
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            self._directory(info)
            mapped = self._stat(parent, name)
            if mapped is None or self._stamp(mapped) != self._stamp(info):
                raise PriorCustodyError("prior-read-race")
            self._fact(parts[:count], info)
            parent = fd
        return parent

    def probe(self, path):
        parts = self._parts(path)
        self.current()
        with contextlib.ExitStack() as stack:
            parent = self._parent(parts, stack)
            if parent is None:
                return None
            value = self._stat(parent, parts[-1])
            self._fact(parts, value)
        self.current()
        return value

    def names(self, path):
        parts = self._parts(path)
        self.current()
        with contextlib.ExitStack() as stack:
            parent = self._parent(parts + ("_",), stack)
            if parent is None:
                return ()
            try:
                names = tuple(sorted(os.listdir(parent)))
            except OSError as error:
                raise PriorCustodyError("prior-directory-unreadable") from error
            if parts in self.listings and self.listings[parts] != names:
                raise PriorCustodyError("prior-read-race")
            self.listings[parts] = names
        self.current()
        return names

    def read(self, path, *, collect=True):
        parts = self._parts(path)
        self.current()
        with contextlib.ExitStack() as stack:
            parent = self._parent(parts, stack)
            if parent is None:
                raise PriorCustodyError("prior-file-missing")
            try:
                metadata = os.open(
                    parts[-1], os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent
                )
            except OSError as error:
                raise PriorCustodyError("prior-file-unreadable") from error
            stack.callback(os.close, metadata)
            pinned = os.fstat(metadata)
            if (
                not stat.S_ISREG(pinned.st_mode)
                or pinned.st_nlink != 1
                or stat.S_IMODE(pinned.st_mode) != 0o600
                or (pinned.st_uid, pinned.st_gid) != self.owner
            ):
                raise PriorCustodyError("prior-file-invalid")
            self._fact(parts, pinned)
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            # Follow only a regular FD's kernel link, never the original name.
            proc = os.open("/proc", flags)
            stack.callback(os.close, proc)
            info = os.fstat(proc)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise RuntimeError("prior-proc-context")
            process = os.open(str(os.getpid()), flags, dir_fd=proc)
            stack.callback(os.close, process)
            descriptors = os.open("fd", flags, dir_fd=process)
            stack.callback(os.close, descriptors)
            for fd in (process, descriptors):
                info = os.fstat(fd)
                if (info.st_uid, info.st_gid) != self.owner or info.st_mode & 0o022:
                    raise RuntimeError("prior-proc-context")
            self.current()
            try:
                payload = os.open(
                    str(metadata), os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=descriptors
                )
            except OSError as error:
                raise RuntimeError("prior-proc-context") from error
            stack.callback(os.close, payload)
            if self._stamp(os.fstat(payload)) != self._stamp(pinned):
                raise PriorCustodyError("prior-read-race")
            digest, blocks, remaining = hashlib.sha256(), [], pinned.st_size
            while remaining:
                self.current()
                try:
                    block = os.read(payload, min(65536, remaining))
                except OSError as error:
                    raise PriorCustodyError("prior-file-unreadable") from error
                if not block:
                    raise PriorCustodyError("prior-file-truncated")
                remaining -= len(block)
                digest.update(block)
                if collect:
                    blocks.append(block)
                self.current()
            try:
                extra = os.read(payload, 1)
            except OSError as error:
                raise PriorCustodyError("prior-file-unreadable") from error
            mapped = self._stat(parent, parts[-1])
            if (
                extra
                or mapped is None
                or self._stamp(mapped) != self._stamp(pinned)
                or self._stamp(os.fstat(payload)) != self._stamp(pinned)
            ):
                raise PriorCustodyError("prior-read-race")
        self.current()
        return (b"".join(blocks) if collect else None), digest.hexdigest()

    def freeze(self):
        return tuple(sorted(self.facts.items())), tuple(sorted(self.listings.items()))

    def verify(self, frozen):
        # A fresh reader cannot adopt a replacement or absence learned later.
        reader = _PriorFDReader(self.root_fd, self.current)
        for parts, expected in frozen[0]:
            observed = reader.probe("/".join(parts))
            if (None if observed is None else self._stamp(observed)) != expected:
                raise PriorCustodyError("prior-read-race")
        for parts, expected in frozen[1]:
            if reader.names("/".join(parts)) != expected:
                raise PriorCustodyError("prior-read-race")
        self.current()


def _prior_json(raw):
    try:
        return json.loads(raw.decode("utf8"))
    except (UnicodeError, ValueError) as error:
        raise PriorCustodyError("prior-json-unreadable") from error


def _prior_run(reader, run_id, custody_hmac_key, publication_verification_key):
    base = "runs/" + run_id + "/"
    try:
        raw, _ = reader.read(base + "metadata/artifacts.jsonl")
        signature, _ = reader.read(base + "metadata/artifacts.jsonl.hmac")
        try:
            validate_custody_index_signature(
                raw, signature.decode("ascii").strip(), custody_hmac_key=custody_hmac_key
            )
        except (CustodyPolicyError, UnicodeError, ValueError) as error:
            raise PriorCustodyError("prior-index-signature") from error
        records = [_prior_json(line) for line in raw.splitlines()]
        if any(type(r) is not dict for r in records):
            raise PriorCustodyError("prior-index-invalid")
        payloads, problems = {}, []
        for record in records:
            try:
                validate_custody_record_signature(record, custody_hmac_key=custody_hmac_key)
                if (
                    record.get("classification") == Classification.PUBLIC.value
                    and publication_verification_key is None
                ):
                    raise PriorContextUnavailable("prior-publication-context-unavailable")
                approval_payload = record.get("publication_approval")
                approval = (
                    PublicationApproval(**approval_payload)
                    if approval_payload is not None
                    else None
                )
                validate_public_approval(
                    Classification(record["classification"]),
                    approval,
                    expected_artifact_id=record["artifact_id"],
                    expected_content_sha256=record["sha256"],
                    verification_key=publication_verification_key,
                )
            except (CustodyPolicyError, KeyError, TypeError, ValueError) as error:
                raise PriorCustodyError("prior-record-policy") from error
            if record.get("run_id") != run_id or any(
                type(record.get(k)) is not str
                for k in ("relative_path", "artifact_type", "artifact_id", "sha256")
            ):
                raise PriorCustodyError("prior-record-binding")
            content, digest = reader.read(
                base + record["relative_path"],
                collect=record.get("mime_type") == "application/json",
            )
            if digest != record.get("sha256"):
                problems.append("custody:sha256")
            if content is not None:
                try:
                    payloads[record["artifact_type"]] = _prior_json(content)
                except PriorCustodyError:
                    problems.append("unreadable:" + record["artifact_type"])
        initial = payloads.get("run-manifest-initial")
        mode = None
        if type(initial) is dict and initial.get("scenario_id") == "EX-10":
            if type(initial.get("experiment_id")) is not str:
                raise PriorCustodyError("prior-initial-manifest-invalid")
            mode = {
                "S11C3-EX10": "OFFICIAL",
                "S11C3-EX10-LOCAL-TEST-NON-EXPERIMENTAL": "LOCAL_TEST",
            }.get(initial.get("experiment_id"))
        if mode is None:
            problems.append("initial-manifest")
        seal_exists = reader.probe(base + ".s11-finalized") is not None
        final_records = [r for r in records if r.get("artifact_type") == "run-manifest-final"]
        finalized = bool(final_records or seal_exists)
        seal_bytes = reader.read(base + ".s11-finalized")[0] if seal_exists else None
        final_bytes = None
        if len(final_records) == 1:
            final_bytes = reader.read(base + final_records[0]["relative_path"])[0]
        try:
            validate_finalized_payload(
                run_id,
                records,
                seal_bytes=seal_bytes,
                final_manifest_bytes=final_bytes,
                custody_hmac_key=custody_hmac_key,
            )
        except EvidencePolicyError:
            problems.append("custody:finalized-state")
        decision = payloads.get("ex10-decision")
        decision = decision.get("decision") if isinstance(decision, Mapping) else None
        if decision is not None and type(decision) is not str:
            raise PriorCustodyError("prior-decision-invalid")
        if finalized and decision not in {d.value for d in Decision}:
            problems.append("finalized-without-ex10-decision")
        return PriorRun(run_id, mode, finalized, decision, tuple(problems))
    except PriorCustodyError:
        return PriorRun(run_id, None, False, None, ("records-unreadable",))


def read_prior_custody(root_fd, *, custody_hmac_key, publication_verification_key, check_current):
    """Pure host scan under caller-held custody; never create, repair or lock."""
    if type(custody_hmac_key) is not bytes or len(custody_hmac_key) < 32:
        raise RuntimeError("prior-custody-context")
    reader = _PriorFDReader(root_fd, check_current)
    rows = []
    runs = reader.probe("runs")
    if runs is not None:
        reader._directory(runs)
        for name in reader.names("runs"):
            path = "runs/" + name
            entry = reader.probe(path)
            canonical = _RUN_ID.fullmatch(name) is not None
            if not canonical:
                # Unknown names cannot become fabricated causal run IDs.
                raise PriorContextUnavailable("prior-run-namespace-context")
            if entry is None or not stat.S_ISDIR(entry.st_mode):
                rows.append(PriorRun(name, None, False, None, ("run-directory-invalid",)))
                continue
            try:
                reader._directory(entry)
                trace = any(
                    reader.probe(path + "/" + kind.value + "/EX-10") is not None
                    for kind in ArtifactKind
                )
            except PriorCustodyError:
                rows.append(PriorRun(name, None, False, None, ("records-unreadable",)))
                continue
            if trace:
                rows.append(
                    _prior_run(reader, name, custody_hmac_key, publication_verification_key)
                )
    frozen = reader.freeze()
    reader.verify(frozen)
    return tuple(rows), frozen


def verify_prior_snapshot(root_fd, frozen, *, check_current):
    """Observe exactly the prior named metadata without reinterpreting policy."""
    _PriorFDReader(root_fd, check_current).verify(frozen)
