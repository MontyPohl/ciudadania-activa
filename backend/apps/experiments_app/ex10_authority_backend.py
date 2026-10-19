"""Durable host B3 adapter. Existing approved stores only; no key/bootstrap creation.

Deployment owns the injected lock, storage identities, external trust and transport.
This module never derives authority from requests or handles executor RPC dispatch.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import os
import secrets
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from . import ex10_authority_sqlite as sql
from . import ex10_authority_witness as witness
from .ex10_open_evidence import ConfinedArtifactResolver, OpenEvidenceError

MAX_CANDIDATE_BYTES = 64 * 1024 * 1024


def _utc():
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True, slots=True)
class FileIdentity:
    device: int
    inode: int
    owner: int
    mode: int

    def check(self, info, *, directory=False):
        sql.require(
            (info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode))
            == (self.device, self.inode, self.owner, self.mode)
            and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and (directory or info.st_nlink == 1),
            "STORAGE_IDENTITY_CHANGED",
        )


class PrivateStore:
    """Host-selected private root held by dirfd; no creation or path fallback."""

    def __init__(self, root: str, identity: FileIdentity):
        sql.require(type(identity) is FileIdentity and identity.mode == 0o700, "UNSAFE_PATH")
        self.root, self.identity = root, identity
        try:
            self.resolver = ConfinedArtifactResolver(root, expected_owner=identity.owner)
            self.fd = self.resolver._root_fd
            self.check()
        except (OSError, OpenEvidenceError, sql.AuthorityError):
            if hasattr(self, "resolver"):
                self.resolver.close()
            raise sql.AuthorityError("UNSAFE_PATH") from None

    def check(self):
        try:
            self.resolver._check_lease()
            self.identity.check(os.fstat(self.fd), directory=True)
        except (OSError, OpenEvidenceError):
            raise sql.AuthorityError("STORAGE_IDENTITY_CHANGED") from None

    def name(self, path):
        sql.require(type(path) is str and "\\" not in path and "\x00" not in path, "UNSAFE_PATH")
        prefix = self.root + "/"
        sql.require(path.startswith(prefix), "UNSAFE_PATH")
        name = path[len(prefix) :]
        sql.require(name and "/" not in name and name not in (".", ".."), "UNSAFE_PATH")
        return name

    def open(self, name, *, write=False, expected=None):
        self.check()
        sql.require(
            type(name) is str and name not in ("", ".", "..") and "/" not in name, "UNSAFE_PATH"
        )
        try:
            fd = os.open(
                name,
                (os.O_RDWR if write else os.O_RDONLY)
                | os.O_NOFOLLOW
                | os.O_NONBLOCK
                | os.O_CLOEXEC,
                dir_fd=self.fd,
            )
            info = os.fstat(fd)
            sql.require(
                stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and info.st_uid == self.identity.owner
                and stat.S_IMODE(info.st_mode) == 0o600,
                "UNSAFE_PATH",
            )
            if expected is not None:
                expected.check(info)
            sql.require(
                (info.st_dev, info.st_ino) == self.entry_identity(name), "STORAGE_IDENTITY_CHANGED"
            )
            return fd
        except OSError:
            if "fd" in locals():
                os.close(fd)
            raise sql.AuthorityError("UNSAFE_PATH") from None
        except BaseException:
            if "fd" in locals():
                os.close(fd)
            raise

    def entry_identity(self, name):
        s = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        return s.st_dev, s.st_ino

    def read(self, path, *, maximum=MAX_CANDIDATE_BYTES):
        name = self.name(path)
        fd = self.open(name)
        try:
            before = os.fstat(fd)
            sql.require(before.st_size <= maximum, "SCHEMA")
            blocks, count = [], 0
            while True:
                block = os.read(fd, min(65536, maximum + 1 - count))
                if not block:
                    break
                blocks.append(block)
                count += len(block)
                sql.require(count <= maximum, "SCHEMA")
            after = os.fstat(fd)
            sql.require(
                (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                and self.entry_identity(name) == (after.st_dev, after.st_ino),
                "STORAGE_IDENTITY_CHANGED",
            )
            self.check()
            return b"".join(blocks)
        finally:
            os.close(fd)

    def create_exact(self, path, data):
        """O_EXCL, file+directory fsync. A failed partial write is retained as a blocker."""
        name = self.name(path)
        self.check()
        sql.require(type(data) is bytes and len(data) <= MAX_CANDIDATE_BYTES, "SCHEMA")
        try:
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=self.fd,
            )
        except OSError:
            raise sql.AuthorityError("DURABLE_WRITE_CONFLICT") from None
        try:
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                sql.require(n > 0, "DURABLE_WRITE_UNKNOWN")
                view = view[n:]
            os.fsync(fd)
        except OSError:
            raise sql.AuthorityError("DURABLE_WRITE_UNKNOWN") from None
        finally:
            os.close(fd)
        try:
            os.fsync(self.fd)
        except OSError:
            raise sql.AuthorityError("DURABLE_WRITE_UNKNOWN") from None
        self.check()

    def close(self):
        with _SQLITE_CONNECTION_LOCK:
            sql.require(
                not any(guard._store is self for _, guard, _ in _QUARANTINED_DATABASES.values()),
                "DURABLE_WRITE_UNKNOWN",
            )
            self.resolver.close()


class HeldWriter:
    """One actual nonblocking POSIX flock; never a serialized proof or PID heuristic."""

    def __init__(self, store: PrivateStore, name: str, identity: FileIdentity, *, budget=120):
        sql.require(
            type(store) is PrivateStore
            and type(identity) is FileIdentity
            and type(budget) in (int, float)
            and 0 < budget <= 120,
            "SERIALIZATION_REQUIRED",
        )
        self.store, self.name, self.identity = store, name, identity
        self.budget, self.fd, self.started = budget, None, None
        self.owner = os.getpid(), threading.get_ident()

    def __enter__(self):
        sql.require(self.fd is None, "SERIALIZATION_REQUIRED")
        self.fd = self.store.open(self.name, write=True, expected=self.identity)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            raise sql.AuthorityError("LOCK_BUSY") from None
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise
        self.started = time.monotonic()
        try:
            self.check()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def check(self):
        sql.require(
            self.fd is not None
            and self.started is not None
            and self.owner == (os.getpid(), threading.get_ident()),
            "SERIALIZATION_REQUIRED",
        )
        self.store.check()
        try:
            self.identity.check(os.fstat(self.fd))
            sql.require(
                self.store.entry_identity(self.name) == (self.identity.device, self.identity.inode),
                "STORAGE_IDENTITY_CHANGED",
            )
            sql.require(not os.get_inheritable(self.fd), "SERIALIZATION_REQUIRED")
        except OSError:
            raise sql.AuthorityError("SERIALIZATION_REQUIRED") from None
        sql.require(time.monotonic() - self.started <= self.budget, "CRITICAL_SECTION_EXPIRED")

    def __exit__(self, *args):
        fd, self.fd = self.fd, None
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


class WitnessTransport(Protocol):
    """Authenticated deployment-selected endpoint; no executor key or local signing fallback."""

    def publish_b3(self, payload: dict, expected_previous: str | None) -> dict: ...
    def head_b3(self, challenge: str) -> dict: ...


class DurableFloor:
    """Append-only signed receipt floors in an independently approved external root."""

    def __init__(self, store: PrivateStore, trust_pins, genesis_digest, genesis_binding):
        self.store = store
        sql.require(type(store) is PrivateStore, "TRUST_UNAPPROVED")
        self.pins = tuple(trust_pins)
        self.genesis_digest, self.genesis_binding = genesis_digest, copy.deepcopy(genesis_binding)
        self.current()  # Missing or incomplete provisioned floors never get created automatically.

    def _path(self, revision):
        return self.store.root + f"/floor-{revision:020d}.json"

    def current(self):
        self.store.check()
        names = os.listdir(self.store.fd)
        sql.require(
            names
            and all(
                len(n) == 31
                and n.startswith("floor-")
                and n.endswith(".json")
                and n[6:26].isdigit()
                for n in names
            ),
            "ROLLBACK_DETECTED",
        )
        numbers = sorted(int(n[6:26]) for n in names)
        sql.require(numbers == list(range(len(numbers))), "ROLLBACK_DETECTED")
        receipts = [sql.parse_canonical(self.store.read(self._path(n))) for n in numbers]
        tip = witness.verify_chain(
            receipts,
            self.pins,
            expected_genesis_digest=self.genesis_digest,
            expected_genesis_binding=self.genesis_binding,
        )
        last = receipts[-1]
        return witness.TrustedFloor(
            last["authority_id"], last["authority_epoch"], last["revision"], tip
        )

    def advance(self, receipt):
        floor = self.current()
        sql.require(
            receipt["revision"] == floor.revision + 1
            and receipt["previous_witness_digest"] == floor.witness_digest,
            "ROLLBACK_DETECTED",
        )
        pin = next(
            (
                p
                for p in self.pins
                if p.record["key_id"] == receipt["key_id"]
                and p.record["key_epoch"] == receipt["key_epoch"]
            ),
            None,
        )
        sql.require(pin is not None, "TRUST_UNAPPROVED")
        witness.verify_witness(receipt, pin)
        sql.require(
            (receipt["authority_id"], receipt["authority_epoch"])
            == (floor.authority_id, floor.authority_epoch),
            "TRUST_UNAPPROVED",
        )
        self.store.create_exact(self._path(receipt["revision"]), sql.canonical_bytes(receipt))
        sql.require(self.current().witness_digest == sql.digest(receipt), "DURABLE_WRITE_UNKNOWN")


_SQLITE_CONNECTION_LOCK = threading.RLock()
# Strong references prevent SQLite finalizers from touching an unqualified pathname.
# This process-local blocker supplements the durable unresolved intent/hot journal.
_QUARANTINED_DATABASES = {}


def _database_key(store, name, identity):
    return (store.identity, name, identity)


def _close_quarantined_database(store, name, identity, *, writer):
    """Internal host-admin FD cleanup after exact storage restoration, never recovery.

    Does not restore paths, resolve intents, retry CAS or change committed history.
    Production account shutdown must respect this blocker until storage is restored.
    """
    sql.require(type(writer) is HeldWriter, "SERIALIZATION_REQUIRED")
    writer.check()
    with _SQLITE_CONNECTION_LOCK:
        key = _database_key(store, name, identity)
        sql.require(key in _QUARANTINED_DATABASES, "RECOVERY_REQUIRED")
        raw, guard, fd = _QUARANTINED_DATABASES[key]
        sql.require(guard._store is store, "STORAGE_IDENTITY_CHANGED")
        guard.check()
        if raw.in_transaction:
            guard.rollback()
        guard.check()
        raw.close()
        os.fsync(fd)
        os.fsync(store.fd)
        os.close(fd)
        del _QUARANTINED_DATABASES[key]


def _regular_fd_snapshot():
    try:
        names = os.listdir("/proc/self/fd")
    except OSError:
        raise sql.AuthorityError("HOST_QUALIFICATION_REQUIRED") from None
    result = {}
    for name in names:
        try:
            info = os.fstat(int(name))
        except (ValueError, OSError):
            continue
        if stat.S_ISREG(info.st_mode):
            result[int(name)] = (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink)
    return result


class _GuardedCursor:
    def __init__(self, connection, cursor):
        self._connection, self._cursor = connection, cursor

    def fetchone(self):
        return self._connection._call(self._cursor.fetchone)

    def fetchall(self):
        return self._connection._call(self._cursor.fetchall)

    def __iter__(self):
        return self

    def __next__(self):
        return self._connection._call(self._cursor.__next__)


class _GuardedConnection:
    """Check storage leases at each Python/SQLite boundary.

    Standard SQLite VFS resolves intermediate pathname components. Deployment
    must exclude untrusted processes with the same UID or writable ancestors;
    every admitted storage writer must use the approved serialization. These
    checks do not promise confinement against mutations inside a VFS call.
    Newly created rollback journals are admitted only as effects of one trusted
    SQLite call; existing unapproved sidecars never acquire that status.
    """

    def __init__(self, connection, store, name, identity, held_fd, sqlite_fd):
        self._connection = connection
        self._store, self._name, self._identity = store, name, identity
        self._held_fd, self._sqlite_fd = held_fd, sqlite_fd
        self._journal = None
        self.check()

    def check(self, *, after=False):
        self._store.check()
        self._identity.check(os.fstat(self._held_fd))
        self._identity.check(os.fstat(self._sqlite_fd))
        sql.require(
            self._store.entry_identity(self._name) == (self._identity.device, self._identity.inode),
            "STORAGE_IDENTITY_CHANGED",
        )
        for suffix in ("-journal", "-wal", "-shm"):
            try:
                info = os.stat(self._name + suffix, dir_fd=self._store.fd, follow_symlinks=False)
            except FileNotFoundError:
                if suffix == "-journal":
                    sql.require(self._journal is None or after, "STORAGE_IDENTITY_CHANGED")
                    self._journal = None
                continue
            sql.require(suffix == "-journal", "RECOVERY_REQUIRED")
            sql.require(
                stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and info.st_uid == self._store.identity.owner
                and stat.S_IMODE(info.st_mode) == 0o600,
                "RECOVERY_REQUIRED",
            )
            if self._journal is None:
                sql.require(after, "RECOVERY_REQUIRED")
                self._journal = FileIdentity(info.st_dev, info.st_ino, info.st_uid, 0o600)
            else:
                self._journal.check(info)

    def _call(self, function, *args):
        self.check()
        try:
            return function(*args)
        finally:
            self.check(after=True)

    def execute(self, statement, parameters=()):
        return _GuardedCursor(self, self._call(self._connection.execute, statement, parameters))

    def executemany(self, statement, parameters):
        return _GuardedCursor(self, self._call(self._connection.executemany, statement, parameters))

    def commit(self):
        return self._call(self._connection.commit)

    def rollback(self):
        return self._call(self._connection.rollback)

    @property
    def in_transaction(self):
        return self._connection.in_transaction


@contextmanager
def existing_database(store, name, identity, *, write=False, schema_check=None):
    # Fail closed if the Linux SQLite main descriptor cannot be identified uniquely.
    with _SQLITE_CONNECTION_LOCK:
        sql.require(
            _database_key(store, name, identity) not in _QUARANTINED_DATABASES,
            "DURABLE_WRITE_UNKNOWN",
        )
        with _existing_database_locked(
            store, name, identity, write=write, schema_check=schema_check
        ) as con:
            yield con


@contextmanager
def _existing_database_locked(store, name, identity, *, write=False, schema_check=None):
    """Held inode and checked schema; URI never creates, recovers or selects another DB."""
    sql.require(type(store) is PrivateStore and type(identity) is FileIdentity, "UNSAFE_PATH")
    fd = store.open(name, write=write, expected=identity)
    con = raw_con = None
    try:
        for suffix in ("-journal", "-wal", "-shm"):
            try:
                os.stat(name + suffix, dir_fd=store.fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise sql.AuthorityError("RECOVERY_REQUIRED")
        path = Path(f"/proc/self/fd/{store.fd}") / name
        before_fds = _regular_fd_snapshot()
        raw_con = sqlite3.connect(
            path.as_uri() + ("?mode=rw" if write else "?mode=ro"),
            uri=True,
            timeout=0,
            isolation_level=None,
        )
        after_fds = _regular_fd_snapshot()
        opened = [fd_no for fd_no, stamp in after_fds.items() if stamp != before_fds.get(fd_no)]
        sql.require(len(opened) == 1, "STORAGE_IDENTITY_CHANGED")
        sqlite_fd = opened[0]
        identity.check(os.fstat(sqlite_fd))
        sql.require(not os.get_inheritable(sqlite_fd), "STORAGE_IDENTITY_CHANGED")
        con = _GuardedConnection(raw_con, store, name, identity, fd, sqlite_fd)
        for pragma in (
            "foreign_keys=ON",
            "trusted_schema=OFF",
            "recursive_triggers=ON",
            "synchronous=EXTRA",
        ):
            con.execute("PRAGMA " + pragma)
        sql.require(
            con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            and con.execute("PRAGMA synchronous").fetchone()[0] == 3
            and con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            and con.execute("PRAGMA trusted_schema").fetchone()[0] == 0
            and con.execute("PRAGMA recursive_triggers").fetchone()[0] == 1,
            "CONFIGURATION_MISMATCH",
        )
        if not write:
            con.execute("PRAGMA query_only=ON")
        sql.require(
            store.entry_identity(name) == (identity.device, identity.inode),
            "STORAGE_IDENTITY_CHANGED",
        )
        if schema_check is not None:
            schema_check(con)
        yield con
        sql.require(
            store.entry_identity(name) == (identity.device, identity.inode),
            "STORAGE_IDENTITY_CHANGED",
        )
        identity.check(os.fstat(fd))
        identity.check(os.fstat(sqlite_fd))
        store.check()
        if write:
            os.fsync(fd)
            os.fsync(store.fd)
    except (sqlite3.Error, OSError):
        raise sql.AuthorityError("DURABLE_WRITE_UNKNOWN" if write else "HISTORY_INVALID") from None
    finally:
        retained = False
        try:
            if raw_con is not None:
                if con is not None:
                    try:
                        con.check()
                        if con.in_transaction:
                            con.rollback()
                    except (sql.AuthorityError, OSError):
                        _QUARANTINED_DATABASES[_database_key(store, name, identity)] = (
                            raw_con,
                            con,
                            fd,
                        )
                        retained = True
                        raise sql.AuthorityError("DURABLE_WRITE_UNKNOWN") from None
                raw_con.close()
        finally:
            if not retained:
                os.close(fd)


class AuthorityBackend:
    """One approved publication bound to a live host writer and exact base vector.

    Instantiation/read operations do not create stores. No automatic CAS retry or
    recovery is available. Unresolved intents remain in the append-only ledger.
    """

    def __init__(
        self,
        *,
        ledger_store,
        ledger_name,
        ledger_identity,
        candidate_store,
        floor,
        writer,
        transport,
        signer_pin,
        clock_health,
        clock=_utc,
    ):
        sql.require(
            type(writer) is HeldWriter
            and type(floor) is DurableFloor
            and type(candidate_store) is PrivateStore
            and type(signer_pin) is witness.TrustPin
            and transport is not None
            and callable(clock_health),
            "PUBLICATION_ADAPTER_REQUIRED",
        )
        writer.check()
        sql.require(
            {
                (s.identity.device, s.identity.inode)
                for s in (ledger_store, candidate_store, floor.store)
            }.__len__()
            == 3,
            "UNSAFE_PATH",
        )
        self.ledger_store, self.ledger_name = ledger_store, ledger_name
        self.ledger_identity, self.candidate_store = ledger_identity, candidate_store
        self.floor, self.writer, self.transport, self.pin = floor, writer, transport, signer_pin
        self.clock, self.clock_health = clock, clock_health
        self.base = self.read_state()
        sql.validate_state(self.base)
        pending = {i["operation_id"] for i in self.base["publication_intents"]} - {
            r["operation_id"] for r in self.base["publication_resolutions"]
        }
        sql.require(not pending, "UNCERTAIN_PUBLICATION")
        self.intent, self.payload, self.receipt, self.observation = None, None, None, None
        self.dispatched, self.committed = False, False
        self.verify_current_base()

    def read_state(self):
        with existing_database(
            self.ledger_store,
            self.ledger_name,
            self.ledger_identity,
            schema_check=sql._verify_sql_schema,
        ) as con:
            con.execute("BEGIN")
            return sql._read_state(con)

    def _current(self, state):
        sql.require(state["history_revisions"], "HISTORY_INVALID")
        return max(state["history_revisions"], key=lambda r: r["revision"])

    def _now(self):
        try:
            value, healthy = self.clock(), self.clock_health()
        except Exception:
            raise sql.AuthorityError("EXPIRED_OBSERVATION") from None
        sql.require(healthy is True, "EXPIRED_OBSERVATION")
        sql.utc_time(value)
        return value

    def verify_current_base(self):
        base = self._current(self.base)
        receipt = max(self.base["witness_checkpoints"], key=lambda r: r["revision"])
        challenge = secrets.token_hex(32)
        before = time.monotonic()
        try:
            head = self.transport.head_b3(challenge)
        except Exception:
            raise sql.AuthorityError("WITNESS_UNAVAILABLE") from None
        now = self._now()
        head_pin = next(
            (
                p
                for p in self.floor.pins
                if (p.record["key_id"], p.record["key_epoch"])
                == (head["key_id"], head["key_epoch"])
            ),
            None,
        )
        sql.require(head_pin is not None, "TRUST_UNAPPROVED")
        witness._verify_current_head(
            head,
            head_pin,
            challenge=challenge,
            now=now,
            request_elapsed_seconds=time.monotonic() - before,
            clock_healthy=True,
        )
        floor = self.floor.current()
        sql.require(base["revision"] >= floor.revision, "ROLLBACK_DETECTED")
        sql.require(base["revision"] == floor.revision, "FLOOR_INCOMPLETE")
        witness._verify_floor(
            base["revision"],
            base["ledger_digest"],
            receipt["witness_digest"],
            [
                self._receipt(r)
                for r in sorted(self.base["witness_checkpoints"], key=lambda r: r["revision"])
            ],
            head,
            floor,
        )

    @staticmethod
    def _receipt(row):
        from .ex10_history_authority import _receipts

        return _receipts({"witness_checkpoints": [row]})[0]

    def durable_intent(self, intent, candidate_payload):
        self.writer.check()
        sql.require(self.intent is None and type(candidate_payload) is bytes, "REPLAY_CONFLICT")
        sql.validate_row("publication_intents", intent)
        base = self._current(self.base)
        sql.require(
            (intent["base_revision"], intent["base_ledger_digest"])
            == (base["revision"], base["ledger_digest"])
            and intent["candidate_revision"] == base["revision"] + 1,
            "REVISION_STALE",
        )
        sql.require(
            hashlib.sha256(candidate_payload).hexdigest()
            == intent["candidate_payload_sha256"]
            == intent["candidate_ledger_digest"],
            "DIGEST_MISMATCH",
        )
        sql.parse_canonical(candidate_payload)
        self.candidate_store.create_exact(intent["candidate_payload_path"], candidate_payload)
        with existing_database(
            self.ledger_store,
            self.ledger_name,
            self.ledger_identity,
            write=True,
            schema_check=sql._verify_sql_schema,
        ) as con:
            con.execute("BEGIN IMMEDIATE")
            current = sql._read_state(con)
            sql.require(
                sql._ordered_state(current) == sql._ordered_state(self.base), "REVISION_STALE"
            )
            sql._insert_rows(con, {"publication_intents": [intent]})
            con.commit()
        self.intent, self.payload = copy.deepcopy(intent), candidate_payload

    def publish_witness(self, payload, expected_previous):
        self.writer.check()
        sql.require(self.intent is not None and not self.dispatched, "REPLAY_CONFLICT")
        # The durable candidate and intent must still be present before any remote mutation.
        current = self.read_state()
        sql.require(
            self.intent in current["publication_intents"]
            and self.candidate_store.read(self.intent["candidate_payload_path"]) == self.payload,
            "UNCERTAIN_PUBLICATION",
        )
        checked = witness.build_witness_payload(payload)
        sql.require(
            (checked["operation_id"], checked["revision"], checked["ledger_digest"])
            == (
                self.intent["operation_id"],
                self.intent["candidate_revision"],
                self.intent["candidate_ledger_digest"],
            )
            and checked["previous_witness_digest"]
            == expected_previous
            == max(self.base["witness_checkpoints"], key=lambda r: r["revision"])["witness_digest"],
            "REFERENCE_MISMATCH",
        )
        self.dispatched = True  # Before dispatch: a lost response is never resent.
        try:
            receipt = self.transport.publish_b3(checked, expected_previous)
        except Exception:
            raise sql.AuthorityError("UNCERTAIN_PUBLICATION") from None
        witness.verify_witness(receipt, self.pin)
        sql.require(
            {k: v for k, v in receipt.items() if k != "signature"} == checked, "FORK_DETECTED"
        )
        self.receipt = copy.deepcopy(receipt)
        return copy.deepcopy(receipt)

    def observe_head(self, receipt):
        self.writer.check()
        sql.require(self.receipt is not None and receipt == self.receipt, "REFERENCE_MISMATCH")
        challenge = secrets.token_hex(32)
        before = time.monotonic()
        try:
            head = self.transport.head_b3(challenge)
        except Exception:
            raise sql.AuthorityError("UNCERTAIN_PUBLICATION") from None
        observation = {
            "head": head,
            "challenge": challenge,
            "now": self._now(),
            "request_elapsed_seconds": time.monotonic() - before,
            "clock_healthy": True,
        }
        witness._verify_current_head(
            head, self.pin, **{k: v for k, v in observation.items() if k != "head"}
        )
        sql.require(
            (head["revision"], head["ledger_digest"], head["witness_digest"])
            == (receipt["revision"], receipt["ledger_digest"], sql.digest(receipt)),
            "FORK_DETECTED",
        )
        self.observed_monotonic = time.monotonic()
        self.observation = copy.deepcopy(observation)
        return copy.deepcopy(observation)

    def _fresh_commit_observation(self):
        observation = self.observation
        sql.require(observation is not None, "PUBLICATION_ADAPTER_REQUIRED")
        witness._verify_current_head(
            observation["head"],
            self.pin,
            challenge=observation["challenge"],
            now=self._now(),
            request_elapsed_seconds=observation["request_elapsed_seconds"]
            + time.monotonic()
            - self.observed_monotonic,
            clock_healthy=True,
        )

    def atomic_commit(self, additions):
        self.writer.check()
        sql.require(
            self.intent is not None
            and self.receipt is not None
            and self.observation is not None
            and not self.committed,
            "PUBLICATION_ADAPTER_REQUIRED",
        )
        self._fresh_commit_observation()
        with existing_database(
            self.ledger_store,
            self.ledger_name,
            self.ledger_identity,
            write=True,
            schema_check=sql._verify_sql_schema,
        ) as con:
            con.execute("BEGIN IMMEDIATE")
            current = sql._read_state(con)
            expected = copy.deepcopy(self.base)
            expected["publication_intents"].append(self.intent)
            sql.require(
                sql._ordered_state(current) == sql._ordered_state(expected), "REVISION_STALE"
            )
            candidate = copy.deepcopy(self.base)
            sql.require(type(additions) is dict and set(additions) <= set(sql.TABLES), "SCHEMA")
            for table, rows in additions.items():
                candidate[table].extend(copy.deepcopy(rows))
            sql.validate_state(candidate)
            sql.require(
                sql.canonical_bytes(
                    sql.logical_projection(candidate, self.intent["candidate_revision"])
                )
                == self.payload,
                "DIGEST_MISMATCH",
            )
            sql.require(
                candidate["publication_intents"] == expected["publication_intents"],
                "REFERENCE_MISMATCH",
            )
            from .ex10_history_authority import _receipts

            sql.require(_receipts(candidate)[-1] == self.receipt, "FORK_DETECTED")
            sql.require(
                any(
                    r["operation_id"] == self.intent["operation_id"]
                    and r["disposition"] == "COMMITTED"
                    and r["receipt_digest"] == sql.digest(self.receipt)
                    and r["fresh_witness_observation_digest"]
                    == sql.digest(self.observation["head"])
                    for r in candidate["publication_resolutions"]
                ),
                "REFERENCE_MISMATCH",
            )
            changes = copy.deepcopy(additions)
            changes[
                "publication_intents"
            ] = []  # The exact same intent was already durably inserted.
            sql._insert_rows(con, changes)
            sql.require(
                sql._ordered_state(sql._read_state(con)) == sql._ordered_state(candidate),
                "DURABLE_WRITE_UNKNOWN",
            )
            self.writer.check()
            self._fresh_commit_observation()
            con.commit()
        self.committed = True
        self.floor.advance(self.receipt)  # After local commit and before any success return.
