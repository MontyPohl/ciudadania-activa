"""Host-wide durable opening journal, injected trust and one live POSIX writer.

The registry, lock, journal, external coordination floor and witness are fixed
by deployment. Normal calls cannot create stores, choose keys, reset counters
or supply verifiers. This core does not dispatch executor commands.
"""

from __future__ import annotations

import copy
import os
import secrets
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import ex10_authority_sqlite as sql
from . import ex10_bridge_protocol as wire
from . import ex10_governance_certificate as governance
from . import ex10_history_authority as history
from . import ex10_open_evidence as evidence
from .ex10_authority_backend import (
    AuthorityBackend,
    DurableFloor,
    FileIdentity,
    HeldWriter,
    PrivateStore,
    existing_database,
)
from .ex10_witness_service import RecoveryApproval

NAMESPACE = "S11C3-EX10"


class HostSources(Protocol):
    """Fixed host qualifier; W06 supplies full B1/B2/deployment/GOV qualification."""

    def request_context(self, raw: bytes, *, operation: str) -> wire.RequestContext: ...
    def read_authority(self): ...
    def verify_reservation_context(self, record: bytes, authenticated_request): ...
    def verify_registry(self, *, canonical_binding: bytes, journal): ...
    def qualify_open(self, *, authenticated_request, opening): ...
    def sign_response(self, *, authenticated_request, opening): ...
    def verify_uncertainty(self, *, authenticated_request, opening): ...
    def recovery_context(self, raw: bytes, *, operation: str): ...
    def qualify_recovery(self, *, authenticated_request, record, writer, coordination_head): ...
    def verify_manual_event(self, *, event, original_record): ...
    def qualify_owner_loss(
        self, *, operation_id, record, observation_reference, writer, coordination_head
    ): ...
    def verify_owner_loss(self, *, canonical_observation, original_record): ...
    def qualify_terminal(self, *, operation_id, record, writer): ...


class CoordinationTransport(Protocol):
    """Fixed authenticated witness channel; credentials never come from requests."""

    def head(self, challenge: str) -> dict: ...
    def reserve(
        self, record: bytes, *, expected_sequence: int, expected_digest: str | None
    ) -> dict: ...
    def transition(self, **kwargs) -> dict: ...


def _utc(value):
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _invoke(call, code):
    try:
        return call()
    except Exception:
        raise sql.AuthorityError(code) from None


def _reference(path, value):
    return {
        "path": path,
        "sha256": sql.digest(value),
        "schema_version": value.get("schema_version", value.get("witness_schema_version")),
    }


def _seal_record(value):
    value = copy.deepcopy(value)
    value["reservation_digest"] = sql.digest({k: value[k] for k in wire.RESERVATION_INTENT_FIELDS})
    value["record_digest"] = sql.digest({k: v for k, v in value.items() if k != "record_digest"})
    return wire.qualify_reservation(sql.canonical_bytes(value)).to_dict()


def qualify_local_filesystem(store):
    """Fixed read-only OS query, no request executable/options/environment."""
    sql.require(type(store) is PrivateStore, "HOST_QUALIFICATION_REQUIRED")
    store.check()
    result = _invoke(
        lambda: subprocess.run(
            ["/usr/bin/stat", "-f", "-c", "%T", "--", store.root],
            capture_output=True,
            timeout=5,
            check=False,
            close_fds=True,
            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        ),
        "HOST_QUALIFICATION_REQUIRED",
    )
    sql.require(
        result.returncode == 0
        and len(result.stdout) <= 64
        and result.stdout.strip() in (b"ext2/ext3", b"xfs", b"btrfs", b"tmpfs", b"overlayfs"),
        "HOST_QUALIFICATION_REQUIRED",
    )
    store.check()


TABLES = (
    "CREATE TABLE coordination_metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
    "namespace TEXT NOT NULL CHECK(namespace='S11C3-EX10'), authority_id TEXT NOT NULL, "
    "authority_epoch INTEGER NOT NULL, registry_binding BLOB NOT NULL, "
    "generation INTEGER NOT NULL, "
    "active_reservation_id TEXT, witness_sequence INTEGER NOT NULL, witness_digest TEXT, "
    "fencing_token INTEGER NOT NULL) STRICT",
    "CREATE TABLE reservations (reservation_id TEXT PRIMARY KEY, reservation_digest TEXT NOT NULL "
    "UNIQUE, canonical BLOB NOT NULL) STRICT",
    "CREATE TABLE reservation_events (reservation_id TEXT NOT NULL REFERENCES "
    "reservations(reservation_id), record_sequence INTEGER NOT NULL, append_order INTEGER NOT NULL "
    "UNIQUE, record_digest TEXT NOT NULL UNIQUE, canonical BLOB NOT NULL, "
    "PRIMARY KEY(reservation_id,record_sequence)) STRICT",
    "CREATE TABLE request_claims (namespace TEXT NOT NULL, authority_id TEXT NOT NULL, "
    "authority_epoch INTEGER NOT NULL, nonce TEXT NOT NULL, request_id TEXT NOT NULL, "
    "request_digest TEXT NOT NULL, operation TEXT NOT NULL, reservation_id TEXT NOT NULL "
    "REFERENCES reservations(reservation_id), canonical BLOB NOT NULL, "
    "PRIMARY KEY(namespace,authority_id,authority_epoch,nonce), "
    "UNIQUE(namespace,authority_id,authority_epoch,request_id)) STRICT",
    "CREATE TABLE response_records (request_id TEXT PRIMARY KEY, response_digest TEXT NOT NULL, "
    "canonical BLOB NOT NULL) STRICT",
    "CREATE TABLE remote_actions (operation_id TEXT PRIMARY KEY, transition TEXT NOT NULL, "
    "reservation_id TEXT NOT NULL REFERENCES reservations(reservation_id), "
    "expected_sequence INTEGER NOT NULL, expected_digest TEXT, canonical BLOB NOT NULL) STRICT",
    "CREATE TABLE host_observations (operation_id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL "
    "REFERENCES reservations(reservation_id), record_sequence INTEGER NOT NULL, "
    "record_digest TEXT NOT NULL, canonical BLOB NOT NULL) STRICT",
    "CREATE TABLE remote_receipts (sequence INTEGER PRIMARY KEY, operation_id TEXT NOT NULL "
    "UNIQUE REFERENCES remote_actions(operation_id), digest TEXT NOT NULL UNIQUE, "
    "canonical BLOB NOT NULL) STRICT",
)


def schema_statements():
    result = list(TABLES)
    keys = {
        "coordination_metadata": "singleton",
        "reservations": "reservation_id",
        "reservation_events": "append_order",
        "request_claims": "request_id",
        "response_records": "request_id",
        "remote_actions": "operation_id",
        "host_observations": "operation_id",
        "remote_receipts": "sequence",
    }
    for table, key in keys.items():
        result.append(
            f"CREATE TRIGGER reject_replace_{table} BEFORE INSERT ON {table} "
            f"WHEN EXISTS(SELECT 1 FROM {table} WHERE {key}=NEW.{key}) "
            "BEGIN SELECT RAISE(ABORT, 'APPEND_ONLY'); END"
        )
        for action in ("DELETE",) if table == "coordination_metadata" else ("UPDATE", "DELETE"):
            result.append(
                f"CREATE TRIGGER reject_{action.lower()}_{table} BEFORE {action} "
                f"ON {table} BEGIN SELECT RAISE(ABORT, 'APPEND_ONLY'); END"
            )
    return tuple(result)


def verify_schema(con):
    expected = {(s.split()[1].lower(), s.split()[2]): s for s in schema_statements()}
    actual = {
        (kind, name): statement
        for kind, name, statement in con.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL"
        )
    }
    sql.require(actual == expected, "SCHEMA_TAMPERED")
    sql.require(
        con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        and not con.execute("PRAGMA foreign_key_check").fetchall(),
        "HISTORY_INVALID",
    )


class CoordinationFloor:
    """Existing external append-only authenticated B4 sequence/token floor."""

    def __init__(self, store, *, authority_id, authority_epoch, witness_pins):
        sql.require(
            type(store) is PrivateStore
            and type(witness_pins) is tuple
            and witness_pins
            and all(type(p) is wire.PublicKeyPin for p in witness_pins),
            "TRUST_UNAPPROVED",
        )
        self.store, self.authority_id, self.authority_epoch = store, authority_id, authority_epoch
        self.pins = witness_pins
        self.current()

    def _path(self, sequence):
        return self.store.root + f"/head-{sequence:020d}.json"

    def verify_head(self, head, *, challenge, now, minimum_sequence=0, minimum_token=0):
        pin = next(
            (
                p
                for p in self.pins
                if (p.key_id, p.key_epoch) == (head.get("key_id"), head.get("key_epoch"))
            ),
            None,
        )
        sql.require(pin is not None, "TRUST_UNAPPROVED")
        expected = {
            k: v
            for k, v in head.items()
            if k not in ("signature", "issued_at", "expires_at", "sequence", "fencing_token")
        }
        sql.require(
            expected.get("namespace") == NAMESPACE
            and expected.get("authority_id") == self.authority_id
            and expected.get("authority_epoch") == self.authority_epoch
            and expected.get("challenge") == challenge,
            "REFERENCE_MISMATCH",
        )
        return wire.verify_coordination_head(
            sql.canonical_bytes(head),
            expected_context=wire.CoordinationHeadContext(
                sql.canonical_bytes(expected), minimum_sequence, minimum_token, now
            ),
            witness_pin=pin,
        ).to_dict()

    def current(self):
        self.store.check()
        # Fork shares the descriptor's directory position; use a private one (PR #50).
        fd = os.open(
            ".",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=self.store.fd,
        )
        try:
            names = os.listdir(fd)
        finally:
            os.close(fd)
        sql.require(
            names
            and all(
                len(n) == 30 and n.startswith("head-") and n.endswith(".json") and n[5:25].isdigit()
                for n in names
            ),
            "COORDINATION_ROLLBACK",
        )
        numbers = sorted(int(n[5:25]) for n in names)
        sql.require(numbers == list(range(len(numbers))), "COORDINATION_ROLLBACK")
        prior = None
        for n in numbers:
            value = sql.parse_canonical(self.store.read(self._path(n)))
            now = sql.utc_time(value["issued_at"]).replace(tzinfo=UTC)
            value = self.verify_head(
                value,
                challenge=value["challenge"],
                now=now,
                minimum_sequence=n,
                minimum_token=0 if prior is None else prior["fencing_token"],
            )
            sql.require(
                value["sequence"] == n
                and (
                    n > 0
                    or (
                        value["fencing_token"] == 0
                        and value["receipt_digest"] is None
                        and value["active_reservation_id"] is None
                    )
                ),
                "COORDINATION_ROLLBACK",
            )
            prior = value
        return prior

    def advance(self, head, *, challenge, now):
        prior = self.current()
        accepted = self.verify_head(
            head,
            challenge=challenge,
            now=now,
            minimum_sequence=prior["sequence"],
            minimum_token=prior["fencing_token"],
        )
        if accepted["sequence"] == prior["sequence"]:
            sql.require(
                all(
                    accepted[k] == prior[k]
                    for k in ("fencing_token", "receipt_digest", "active_reservation_id")
                ),
                "COORDINATION_ROLLBACK",
            )
            return
        sql.require(accepted["sequence"] == prior["sequence"] + 1, "COORDINATION_ROLLBACK")
        self.store.create_exact(self._path(accepted["sequence"]), sql.canonical_bytes(accepted))


@dataclass(frozen=True, slots=True)
class DuplicateRecognition:
    original_request_id: str
    state: str
    reservation_id: str
    saved_response: bytes | None


class CoordinationJournal:
    """Validated append-only local journal. Reads never acquire the writer lock."""

    def __init__(
        self,
        *,
        store,
        name,
        identity,
        registry_binding,
        authority_id,
        authority_epoch,
        receipt_store,
        floor,
        transport,
        bridge_pin,
        host_sources,
        clock,
        clock_health,
        lock_store,
        lock_name,
        lock_identity,
    ):
        sql.require(
            type(store) is PrivateStore
            and type(identity) is FileIdentity
            and type(receipt_store) is PrivateStore
            and type(floor) is CoordinationFloor
            and type(lock_store) is PrivateStore
            and type(lock_identity) is FileIdentity
            and type(bridge_pin) is wire.PublicKeyPin
            and host_sources is not None
            and transport is not None
            and callable(clock)
            and callable(clock_health),
            "TRUST_UNAPPROVED",
        )
        sql.validate_scalar(authority_id, {"type": "uuid", "not_null": True})
        sql.validate_scalar(authority_epoch, {"type": "positive_int64", "not_null": True})
        sql.parse_canonical(registry_binding)
        sql.require(
            (floor.authority_id, floor.authority_epoch) == (authority_id, authority_epoch)
            and len(
                {
                    (p.identity.device, p.identity.inode)
                    for p in (store, receipt_store, floor.store, lock_store)
                }
            )
            == 4,
            "UNSAFE_PATH",
        )
        self.store, self.name, self.identity = store, name, identity
        self.binding, self.authority_id, self.authority_epoch = (
            registry_binding,
            authority_id,
            authority_epoch,
        )
        self.receipt_store, self.floor, self.transport = receipt_store, floor, transport
        self.bridge_pin, self.sources = bridge_pin, host_sources
        self.clock, self.clock_health = clock, clock_health
        self.lock_store, self.lock_name, self.lock_identity = lock_store, lock_name, lock_identity
        self._registry()
        self._local_state()
        self.floor.current()  # Permit construction for explicit recovery; reads still reject lag.

    def _registry(self):
        for store in (self.store, self.receipt_store, self.floor.store, self.lock_store):
            store.check()
        sql.require(
            _invoke(
                lambda: self.sources.verify_registry(canonical_binding=self.binding, journal=self),
                "TRUST_UNAPPROVED",
            )
            is None,
            "TRUST_UNAPPROVED",
        )

    def _database(self, *, write=False):
        return existing_database(
            self.store, self.name, self.identity, write=write, schema_check=verify_schema
        )

    def _now(self):
        value = _invoke(self.clock, "EXPIRED_OBSERVATION")
        healthy = _invoke(self.clock_health, "EXPIRED_OBSERVATION")
        sql.require(
            healthy is True
            and type(value) is datetime
            and value.tzinfo is not None
            and value.utcoffset() == UTC.utcoffset(value),
            "EXPIRED_OBSERVATION",
        )
        return value

    def _historical_request(self, raw):
        """Authenticate an already claimed message at its signed issue time.

        The fixed bridge signature admits the persisted holder key. This never
        admits a new live action or bypasses current host context qualification.
        """
        request = wire.parse_request_bytes(raw).to_dict()
        cap = request["capability"]
        wire._verify_pin(cap, self.bridge_pin, bridge=True)
        wire._verify_signature("capability", cap, self.bridge_pin.public_key)
        cls = (
            wire.RecoveryRequestContext
            if request["operation"] in wire.RECOVERY_OPERATIONS
            else wire.RequestContext
        )
        values = {
            k: request[k]
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
        values.update(
            authorization_reference=sql.canonical_bytes(request["authorization_reference"]),
            deployment_verification_sha256=cap["deployment_verification_sha256"],
            container_identity_digest=cap["container_identity_digest"],
            expected_run_id=cap["expected_run_id"],
            reservation_id=cap["reservation_id"],
            reservation_digest=cap["reservation_digest"],
            fencing_token=cap["fencing_token"],
            holder_public_key_hex=cap["holder_public_key_hex"],
            expected_payload=sql.canonical_bytes(request["payload"]),
            now=sql.utc_time(request["issued_at"]).replace(tzinfo=UTC),
        )
        key = _invoke(
            lambda: Ed25519PublicKey.from_public_bytes(bytes.fromhex(cap["holder_public_key_hex"])),
            "TRUST_UNAPPROVED",
        )
        if cls is wire.RecoveryRequestContext:
            values["operator_authorization_reference"] = sql.canonical_bytes(
                request["payload"]["operator_authorization_reference"]
            )
            return wire.verify_recovery_request(
                raw,
                expected_context=cls(**values),
                bridge_pin=self.bridge_pin,
                operator_public_key=key,
            )
        return wire.verify_request(
            raw, expected_context=cls(**values), bridge_pin=self.bridge_pin, holder_public_key=key
        )

    def _read(self, con):
        meta = con.execute(
            "SELECT namespace,authority_id,authority_epoch,registry_binding,generation,"
            "active_reservation_id,witness_sequence,witness_digest,fencing_token "
            "FROM coordination_metadata WHERE singleton=1"
        ).fetchone()
        sql.require(
            meta is not None
            and meta[:4] == (NAMESPACE, self.authority_id, self.authority_epoch, self.binding),
            "TRUST_UNAPPROVED",
        )
        for value in (meta[4], meta[6], meta[8]):
            sql.validate_scalar(value, {"type": "int64_nonnegative", "not_null": True})
        self._registry()
        registry = {}
        for rid, digest, raw in con.execute(
            "SELECT reservation_id,reservation_digest,canonical FROM reservations"
        ):
            value = wire.qualify_reservation(raw).to_dict()
            sql.require(
                (value["reservation_id"], value["reservation_digest"]) == (rid, digest)
                and value["record_sequence"] == 1,
                "HISTORY_INVALID",
            )
            registry[rid] = value
        latest, active, events = {}, None, []
        for rid, seq, order, digest, raw in con.execute(
            "SELECT reservation_id,record_sequence,append_order,"
            "record_digest,canonical FROM reservation_events ORDER BY append_order"
        ):
            value = wire.qualify_reservation(raw).to_dict()
            prior = latest.get(rid)
            sql.require(
                rid in registry
                and order == len(events) + 1
                and (value["reservation_id"], value["record_sequence"], value["record_digest"])
                == (rid, seq, digest)
                and value["reservation_digest"] == registry[rid]["reservation_digest"],
                "HISTORY_INVALID",
            )
            sql.require(
                seq == (1 if prior is None else prior["record_sequence"] + 1)
                and value["previous_reservation_record_digest"]
                == (None if prior is None else prior["record_digest"]),
                "HISTORY_INVALID",
            )
            if prior is None:
                sql.require(
                    value == registry[rid] and active is None and value["state"] == "PENDING",
                    "RESERVATION_BUSY",
                )
                active = rid
            else:
                sql.require(active == rid, "HISTORY_INVALID")
                allowed = {
                    "PENDING": ("PENDING", "COMMITTED", "UNCERTAIN"),
                    "COMMITTED": ("COMMITTED", "UNCERTAIN"),
                    "UNCERTAIN": ("UNCERTAIN", "COMMITTED", "CANCELLED_MANUALLY"),
                    "CANCELLED_MANUALLY": (),
                }
                for field in (
                    "open_witness_reference",
                    "evidence_receipt_reference",
                    "terminal_witness_reference",
                    "manual_resolution_reference",
                ):
                    if prior[field] is not None:
                        sql.require(value[field] == prior[field], "REFERENCE_MISMATCH")
                sql.require(
                    value["state"] in allowed[prior["state"]]
                    and value["updated_at"] >= prior["updated_at"],
                    "HISTORY_INVALID",
                )
                if (
                    value["state"] == "CANCELLED_MANUALLY"
                    or value["terminal_witness_reference"] is not None
                ):
                    active = None
            latest[rid] = value
            events.append(value)
        sql.require(
            set(latest) == set(registry) and meta[4] == len(events) and meta[5] == active,
            "HISTORY_INVALID",
        )
        observations = {}
        for op, rid, seq, digest, raw in con.execute(
            "SELECT operation_id,reservation_id,record_sequence,"
            "record_digest,canonical FROM host_observations"
        ):
            value = sql.parse_canonical(raw)
            sql.require(
                set(value)
                == {
                    "operation_id",
                    "reservation_id",
                    "expected_record_sequence",
                    "expected_record_digest",
                    "reason_code",
                    "observation_reference",
                    "operator_authorization_reference",
                }
                and (
                    value["operation_id"],
                    value["reservation_id"],
                    value["expected_record_sequence"],
                    value["expected_record_digest"],
                )
                == (op, rid, seq, digest)
                and rid in registry,
                "REFERENCE_MISMATCH",
            )
            prior = next(
                (e for e in events if e["reservation_id"] == rid and e["record_sequence"] == seq),
                None,
            )
            introduced = next(
                (
                    e
                    for e in events
                    if e["reservation_id"] == rid and e["record_sequence"] == seq + 1
                ),
                None,
            )
            sql.require(
                prior is not None
                and prior["record_digest"] == digest
                and introduced is not None
                and introduced["state"] == "UNCERTAIN"
                and value["reason_code"] in introduced["uncertainty_codes"],
                "REFERENCE_MISMATCH",
            )
            sql.require(
                _invoke(
                    lambda raw=raw, prior=prior: self.sources.verify_owner_loss(
                        canonical_observation=raw, original_record=copy.deepcopy(prior)
                    ),
                    "TRUST_UNAPPROVED",
                )
                is None,
                "TRUST_UNAPPROVED",
            )
            observations[op] = value
        claims = {}
        for ns, aid, epoch, nonce, rid, digest, operation, resid, raw in con.execute(
            "SELECT namespace,authority_id,authority_epoch,nonce,request_id,request_digest,"
            "operation,reservation_id,canonical FROM request_claims"
        ):
            parsed = self._historical_request(raw).request
            request = parsed.to_dict()
            sql.require(wire.request_digest(parsed) == request["request_digest"], "REPLAY_CONFLICT")
            sql.require(
                (ns, aid, epoch) == (NAMESPACE, self.authority_id, self.authority_epoch)
                and resid in registry
                and (
                    request["nonce"],
                    request["request_id"],
                    request["request_digest"],
                    request["operation"],
                )
                == (nonce, rid, digest, operation),
                "REPLAY_CONFLICT",
            )
            claims[rid] = {"request": request, "canonical": raw, "reservation_id": resid}
        sql.require(
            all(
                v["request_id"] in claims
                and claims[v["request_id"]]["reservation_id"] == v["reservation_id"]
                and claims[v["request_id"]]["request"]["nonce"] == v["nonce"]
                and claims[v["request_id"]]["request"]["request_digest"] == v["request_digest"]
                for v in registry.values()
            ),
            "REPLAY_CONFLICT",
        )
        responses = {}
        for rid, digest, raw in con.execute(
            "SELECT request_id,response_digest,canonical FROM response_records"
        ):
            value = wire.parse_response_bytes(raw).to_dict()
            sql.require(
                rid in claims
                and value["request_id"] == rid
                and value["response_digest"] == digest
                and value["request_digest"] == claims[rid]["request"]["request_digest"],
                "REPLAY_CONFLICT",
            )
            authenticated = self._historical_request(claims[rid]["canonical"])
            wire.verify_response(
                raw,
                authenticated_request=authenticated,
                bridge_pin=self.bridge_pin,
                now=self._now(),
            )
            responses[rid] = raw
        actions = {}
        for op, transition, resid, seq, digest, raw in con.execute(
            "SELECT operation_id,transition,"
            "reservation_id,expected_sequence,expected_digest,canonical FROM remote_actions"
        ):
            sql.require(
                resid in registry
                and type(seq) is int
                and 0 <= seq < sql.MAX_COUNTER
                and (digest is None if seq == 0 else type(digest) is str),
                "HISTORY_INVALID",
            )
            if transition == "RESERVE":
                value = wire.qualify_reservation(raw).to_dict()
                sql.require(
                    value == registry[resid] and value["request_id"] == op, "REFERENCE_MISMATCH"
                )
            else:
                body = sql.parse_canonical(raw)
                sql.require(
                    set(body)
                    == {
                        "reservation_id",
                        "transition",
                        "operation_id",
                        "sequence",
                        "receipt_digest",
                        "open_witness_digest",
                        "terminal_witness_digest",
                    }
                    and (
                        body["reservation_id"],
                        body["transition"],
                        body["operation_id"],
                        body["sequence"],
                        body["receipt_digest"],
                    )
                    == (resid, transition, op, seq, digest)
                    and transition
                    in (
                        "COMMIT_OPEN",
                        "RECORD_UNCERTAIN",
                        "CANCEL_MANUAL",
                        "RETIRE_TERMINAL",
                        "ADOPT_OPEN_MANUAL",
                    ),
                    "REFERENCE_MISMATCH",
                )
            actions[op] = {
                "transition": transition,
                "reservation_id": resid,
                "sequence": seq,
                "digest": digest,
                "canonical": raw,
            }
        receipts = []
        previous = None
        token = 0
        witness_active = None
        witness_latest = {}
        for seq, op, digest, raw in con.execute(
            "SELECT sequence,operation_id,digest,canonical "
            "FROM remote_receipts ORDER BY sequence"
        ):
            value = wire.parse_reservation_receipt_bytes(raw).to_dict()
            action = actions.get(op)
            expected = {k: v for k, v in value.items() if k not in ("signature", "issued_at")}
            pin = next(
                (
                    p
                    for p in self.floor.pins
                    if (p.key_id, p.key_epoch) == (value["key_id"], value["key_epoch"])
                ),
                None,
            )
            sql.require(pin is not None, "TRUST_UNAPPROVED")
            wire.verify_reservation_receipt(
                raw,
                expected_context=wire.ReceiptContext(sql.canonical_bytes(expected), self._now()),
                witness_pin=pin,
            )
            sql.require(
                action is not None
                and seq == len(receipts) + 1
                and value["sequence"] == seq
                and value["operation_id"] == op
                and sql.digest(value) == digest
                and value["previous_receipt_digest"] == previous
                and action["sequence"] == seq - 1
                and action["digest"] == previous
                and action["transition"] == value["transition"]
                and action["reservation_id"] == value["reservation_id"],
                "HISTORY_INVALID",
            )
            record = registry[value["reservation_id"]]
            sql.require(
                value["reservation_digest"] == record["reservation_digest"]
                and value["fencing_token"] == record["fencing_token"]
                and (value["authority_id"], value["authority_epoch"])
                == (self.authority_id, self.authority_epoch)
                and value["namespace"] == NAMESPACE
                and (value["base_authority_revision"], value["base_authority_witness_digest"])
                == (record["previous_authority_revision"], record["previous_witness_digest"]),
                "REFERENCE_MISMATCH",
            )
            if value["transition"] == "RESERVE":
                sql.require(
                    witness_active is None
                    and value["reservation_id"] not in witness_latest
                    and value["state"] == "PENDING"
                    and value["open_witness_digest"] is None
                    and value["terminal_witness_digest"] is None,
                    "HISTORY_INVALID",
                )
                witness_active = value["reservation_id"]
                sql.require(value["fencing_token"] == token + 1, "FENCING_TOKEN_INVALID")
                token = value["fencing_token"]
            else:
                sql.require(
                    value["fencing_token"] == token and witness_active == value["reservation_id"],
                    "FENCING_TOKEN_INVALID",
                )
                prior = witness_latest.get(value["reservation_id"])
                sql.require(prior is not None, "HISTORY_INVALID")
                rules = {
                    "COMMIT_OPEN": (("PENDING",), "COMMITTED"),
                    "RECORD_UNCERTAIN": (("PENDING", "COMMITTED"), "UNCERTAIN"),
                    "CANCEL_MANUAL": (("UNCERTAIN",), "CANCELLED_MANUALLY"),
                    "ADOPT_OPEN_MANUAL": (("UNCERTAIN",), "COMMITTED"),
                    "RETIRE_TERMINAL": (("COMMITTED",), "COMMITTED"),
                }
                allowed, target = rules[value["transition"]]
                sql.require(
                    prior["state"] in allowed and value["state"] == target, "HISTORY_INVALID"
                )
                body = sql.parse_canonical(action["canonical"])
                sql.require(
                    (body["open_witness_digest"], body["terminal_witness_digest"])
                    == (value["open_witness_digest"], value["terminal_witness_digest"]),
                    "REFERENCE_MISMATCH",
                )
                if value["transition"] in ("RECORD_UNCERTAIN", "RETIRE_TERMINAL"):
                    sql.require(
                        value["open_witness_digest"] == prior["open_witness_digest"],
                        "REFERENCE_MISMATCH",
                    )
                if value["transition"] in ("CANCEL_MANUAL", "RETIRE_TERMINAL"):
                    witness_active = None
            previous = digest
            receipts.append(value)
            witness_latest[value["reservation_id"]] = value
        sql.require(
            (meta[6], meta[7], meta[8]) == (len(receipts), previous, token), "COORDINATION_ROLLBACK"
        )
        by_digest = {sql.digest(r): r for r in receipts}
        for event in events:
            ref = event["reservation_receipt_reference"]
            sql.require(
                event["terminal_witness_reference"] is None or event["state"] == "COMMITTED",
                "HISTORY_INVALID",
            )
            if ref is None:
                sql.require(event["terminal_witness_reference"] is None, "HISTORY_INVALID")
                if event["state"] == "CANCELLED_MANUALLY":
                    sql.require(
                        event["manual_resolution_reference"] is not None
                        and _invoke(
                            lambda event=event: self.sources.verify_manual_event(
                                event=copy.deepcopy(event),
                                original_record=copy.deepcopy(registry[event["reservation_id"]]),
                            ),
                            "TRUST_UNAPPROVED",
                        )
                        is None,
                        "TRUST_UNAPPROVED",
                    )
                else:
                    sql.require(
                        event["state"] in ("PENDING", "UNCERTAIN")
                        and event["manual_resolution_reference"] is None,
                        "HISTORY_INVALID",
                    )
                continue
            receipt = by_digest.get(ref["sha256"])
            sql.require(
                receipt is not None
                and receipt["reservation_id"] == event["reservation_id"]
                and ref
                == _reference(
                    self.receipt_store.root + f"/receipt-{receipt['sequence']:020d}.json", receipt
                ),
                "REFERENCE_MISMATCH",
            )
            sql.require(
                self.receipt_store.read(ref["path"]) == sql.canonical_bytes(receipt),
                "REFERENCE_MISMATCH",
            )
            if event["terminal_witness_reference"] is not None:
                sql.require(receipt["transition"] == "RETIRE_TERMINAL", "HISTORY_INVALID")
            if event["state"] == "CANCELLED_MANUALLY":
                sql.require(
                    receipt["transition"] == "CANCEL_MANUAL"
                    and _invoke(
                        lambda event=event: self.sources.verify_manual_event(
                            event=copy.deepcopy(event),
                            original_record=copy.deepcopy(registry[event["reservation_id"]]),
                        ),
                        "TRUST_UNAPPROVED",
                    )
                    is None,
                    "TRUST_UNAPPROVED",
                )
            sql.require(
                event["state"] == receipt["state"] or event["state"] == "UNCERTAIN",
                "HISTORY_INVALID",
            )
            for field, key in (
                ("open_witness_reference", "open_witness_digest"),
                ("terminal_witness_reference", "terminal_witness_digest"),
            ):
                witness = event[field]
                if event["state"] != "UNCERTAIN":
                    sql.require(
                        (None if witness is None else witness["witness_digest"]) == receipt[key],
                        "REFERENCE_MISMATCH",
                    )
        return {
            "generation": meta[4],
            "active": active,
            "registry": registry,
            "latest": latest,
            "events": events,
            "claims": claims,
            "responses": responses,
            "actions": actions,
            "receipts": receipts,
            "sequence": len(receipts),
            "digest": previous,
            "token": token,
            "witness_active": witness_active,
            "witness_latest": witness_latest,
            "observations": observations,
        }

    def _local_state(self):
        with self._database() as con:
            con.execute("BEGIN")
            return self._read(con)

    def read(self):
        state = self._local_state()
        floor = self.floor.current()
        sql.require(
            (state["sequence"], state["digest"], state["token"], state["witness_active"])
            == (
                floor["sequence"],
                floor["receipt_digest"],
                floor["fencing_token"],
                floor["active_reservation_id"],
            ),
            "COORDINATION_ROLLBACK",
        )
        return state

    def fresh_head(self, state):
        floor = self.floor.current()
        challenge = secrets.token_hex(32)
        start = time.monotonic()
        raw = _invoke(lambda: self.transport.head(challenge), "WITNESS_UNAVAILABLE")
        head = self.floor.verify_head(
            raw,
            challenge=challenge,
            now=self._now(),
            minimum_sequence=floor["sequence"],
            minimum_token=floor["fencing_token"],
        )
        sql.require(time.monotonic() - start <= 30, "EXPIRED_OBSERVATION")
        sql.require(
            (head["sequence"], head["receipt_digest"], head["fencing_token"])
            == (state["sequence"], state["digest"], state["token"]),
            "COORDINATION_ROLLBACK",
        )
        sql.require(
            (state["sequence"], state["digest"], state["token"])
            == (floor["sequence"], floor["receipt_digest"], floor["fencing_token"]),
            "COORDINATION_ROLLBACK",
        )
        remote_active = head["active_reservation_id"]
        # An unwitnessed local pending reservation is also a blocker; no repair here.
        sql.require(
            remote_active == state["witness_active"]
            and remote_active == floor["active_reservation_id"],
            "COORDINATION_ROLLBACK",
        )
        return head

    def authenticate(self, raw, operation):
        context = _invoke(
            lambda: self.sources.request_context(raw, operation=operation), "TRUST_UNAPPROVED"
        )
        sql.require(
            type(context) is wire.RequestContext and context.operation == operation,
            "TRUST_UNAPPROVED",
        )
        now = self._now()
        sql.require(
            context.now <= now and (now - context.now).total_seconds() <= 5, "EXPIRED_OBSERVATION"
        )
        key = _invoke(
            lambda: Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(context.holder_public_key_hex)
            ),
            "TRUST_UNAPPROVED",
        )
        return wire.verify_request(
            raw,
            expected_context=replace(context, now=now),
            bridge_pin=self.bridge_pin,
            holder_public_key=key,
        )

    def authenticate_recovery(self, raw, operation):
        sql.require(operation in wire.RECOVERY_OPERATIONS, "TRUST_UNAPPROVED")
        context = _invoke(
            lambda: self.sources.recovery_context(raw, operation=operation), "TRUST_UNAPPROVED"
        )
        sql.require(
            type(context) is wire.RecoveryRequestContext and context.operation == operation,
            "TRUST_UNAPPROVED",
        )
        now = self._now()
        sql.require(
            context.now <= now and (now - context.now).total_seconds() <= 5, "EXPIRED_OBSERVATION"
        )
        key = _invoke(
            lambda: Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(context.holder_public_key_hex)
            ),
            "TRUST_UNAPPROVED",
        )
        return wire.verify_recovery_request(
            raw,
            expected_context=replace(context, now=now),
            bridge_pin=self.bridge_pin,
            operator_public_key=key,
        )

    def recognize(self, state, authenticated):
        value = authenticated.request.to_dict()
        claim = state["claims"].get(value["request_id"])
        nonce = next(
            (x for x in state["claims"].values() if x["request"]["nonce"] == value["nonce"]), None
        )
        if claim is None:
            sql.require(nonce is None, "REPLAY_CONFLICT")
            return None
        sql.require(claim["canonical"] == authenticated.request.canonical, "REPLAY_CONFLICT")
        record = state["latest"][claim["reservation_id"]]
        return DuplicateRecognition(
            value["request_id"],
            record["state"],
            record["reservation_id"],
            state["responses"].get(value["request_id"]),
        )

    def eligible(self, authenticated, record):
        snapshot = _invoke(self.sources.read_authority, "HISTORY_INVALID")
        sql.require(history.first_valid_status(snapshot)["status"] == "NONE", "FIRST_VALID_BLOCKED")
        head = history.current_revision(snapshot)
        request = authenticated.request.to_dict()
        rows = []
        for run in history.list_official_runs(snapshot):
            if (
                run["lifecycle_state"] == "TERMINAL"
                and run["result"] == "NOT_DEMONSTRATED"
                and run["binding"] is False
                and run["ack_required"] is True
            ):
                target = history.verify_ack_target(snapshot, run["run_id"])
                sql.require(target["recognizable"], "ACK_INVALID")
                rows.append(
                    {
                        "run_id": run["run_id"],
                        "authority_revision": target["revision"],
                        "terminal_evidence_reference": target["evidence_reference"],
                    }
                )
        rows.sort(key=lambda x: x["run_id"])
        sql.require(
            request["payload"]["acknowledgments"] == rows
            and record["acknowledgment_digest"] == sql.digest(rows),
            "ACK_INVALID",
        )
        sql.require(
            (
                head["authority_id"],
                head["epoch"],
                head["revision"],
                head["ledger_digest"],
                head["witness_digest"],
            )
            == (
                self.authority_id,
                self.authority_epoch,
                record["previous_authority_revision"],
                record["previous_ledger_digest"],
                record["previous_witness_digest"],
            ),
            "REVISION_STALE",
        )
        sql.require(request["authority_revision"] == head["revision"], "REVISION_STALE")
        sql.require(
            _invoke(
                lambda: self.sources.verify_reservation_context(
                    sql.canonical_bytes(record), authenticated
                ),
                "TRUST_UNAPPROVED",
            )
            is None,
            "TRUST_UNAPPROVED",
        )
        return snapshot

    def _append_event(self, con, state, record):
        sql.require(state["generation"] < sql.MAX_COUNTER, "REVISION_OVERFLOW")
        con.execute(
            "INSERT INTO reservation_events VALUES (?,?,?,?,?)",
            (
                record["reservation_id"],
                record["record_sequence"],
                state["generation"] + 1,
                record["record_digest"],
                sql.canonical_bytes(record),
            ),
        )
        active = (
            None
            if record["state"] == "CANCELLED_MANUALLY"
            or record["terminal_witness_reference"] is not None
            else record["reservation_id"]
        )
        con.execute(
            "UPDATE coordination_metadata SET generation=?,active_reservation_id=? "
            "WHERE singleton=1 AND generation=?",
            (state["generation"] + 1, active, state["generation"]),
        )

    def _claim(self, con, state, authenticated, record):
        duplicate = self.recognize(state, authenticated)
        sql.require(duplicate is None, "REPLAY_CONFLICT")
        value = authenticated.request.to_dict()
        con.execute(
            "INSERT INTO request_claims VALUES (?,?,?,?,?,?,?,?,?)",
            (
                NAMESPACE,
                self.authority_id,
                self.authority_epoch,
                value["nonce"],
                value["request_id"],
                value["request_digest"],
                value["operation"],
                record["reservation_id"],
                authenticated.request.canonical,
            ),
        )

    def inspect(self, *, request_id=None, reservation_id=None):
        """Host internal read only; W06 wraps with fresh read capability authorization."""
        sql.require((request_id is None) != (reservation_id is None), "SCHEMA")
        state = self.read()
        if request_id is not None:
            sql.validate_scalar(request_id, {"type": "uuid", "not_null": True})
            claim = state["claims"].get(request_id)
            if claim is None:
                return None
            record = state["latest"][claim["reservation_id"]]
            return DuplicateRecognition(
                request_id,
                record["state"],
                record["reservation_id"],
                state["responses"].get(request_id),
            )
        sql.validate_scalar(reservation_id, {"type": "uuid", "not_null": True})
        return copy.deepcopy(state["latest"].get(reservation_id))

    def retire_terminal(self, *, reservation_id, operation_id):
        """Internal coordinated B7 retirement; full terminal authority never grants START."""
        return self._retire_terminal(reservation_id, operation_id, recovery=False)

    def recover_terminal_retirement(self, *, reservation_id, operation_id):
        """Explicit host-only approval of one already-signed retirement; no remote CAS."""
        return self._retire_terminal(reservation_id, operation_id, recovery=True)

    def _retire_terminal(self, reservation_id, operation_id, *, recovery):
        for value in (reservation_id, operation_id):
            sql.validate_scalar(value, {"type": "uuid", "not_null": True})
        writer = HeldWriter(self.lock_store, self.lock_name, self.lock_identity)
        with writer:
            state = self._local_state() if recovery else self.read()
            record = state["latest"].get(reservation_id)
            old = state["actions"].get(operation_id)
            if old is not None:
                sql.require(
                    old["transition"] == "RETIRE_TERMINAL"
                    and old["reservation_id"] == reservation_id,
                    "REPLAY_CONFLICT",
                )
                confirmed = next(
                    (r for r in state["receipts"] if r["operation_id"] == operation_id), None
                )
                if confirmed is not None:
                    sql.require(
                        record["terminal_witness_reference"] is not None
                        and state["active"] is None,
                        "UNCERTAIN_PUBLICATION",
                    )
                    self.read()
                    return copy.deepcopy(record)
                sql.require(recovery, "UNCERTAIN_PUBLICATION")
            else:
                sql.require(not recovery, "TRUST_UNAPPROVED")
            sql.require(
                record is not None
                and record["state"] in (("COMMITTED", "UNCERTAIN") if recovery else ("COMMITTED",))
                and state["active"] == reservation_id,
                "UNCERTAIN_PUBLICATION",
            )
            qualified = _invoke(
                lambda: self.sources.qualify_terminal(
                    operation_id=operation_id, record=copy.deepcopy(record), writer=writer
                ),
                "TRUST_UNAPPROVED",
            )
            sql.require(
                type(qualified) is TerminalQualification and type(qualified.floor) is DurableFloor,
                "TRUST_UNAPPROVED",
            )
            current = history.current_revision(qualified.authority_snapshot)
            run = history.find_run(qualified.authority_snapshot, record["run_id"])
            floor = qualified.floor.current()
            ref = sql.parse_canonical(qualified.terminal_witness_reference)
            sql.require(
                run is not None
                and run["lifecycle_state"] == "TERMINAL"
                and run["result"] in ("PASS", "FAIL", "NOT_DEMONSTRATED")
                and current["revision"] > record["expected_next_revision"]
                and (
                    current["authority_id"],
                    current["epoch"],
                    current["revision"],
                    current["witness_digest"],
                )
                == (self.authority_id, self.authority_epoch, floor.revision, floor.witness_digest)
                and (
                    ref["authority_id"],
                    ref["authority_epoch"],
                    ref["revision"],
                    ref["ledger_digest"],
                    ref["witness_digest"],
                )
                == (
                    current["authority_id"],
                    current["epoch"],
                    current["revision"],
                    current["ledger_digest"],
                    current["witness_digest"],
                )
                and ref["receipt_reference"]["sha256"] == current["witness_digest"],
                "REFERENCE_MISMATCH",
            )
            receipts = history._receipts(history._verified(qualified.authority_snapshot)["state"])
            sql.require(
                sql.digest(receipts[record["expected_next_revision"]])
                == record["open_witness_reference"]["witness_digest"],
                "REFERENCE_MISMATCH",
            )
            if recovery:
                approval = qualified.recovery_approval
                sql.require(
                    type(approval) is RecoveryApproval and qualified.existing_receipt is not None,
                    "TRUST_UNAPPROVED",
                )
                sql.require(
                    approval.operation_id == operation_id
                    and approval.reservation_id == reservation_id
                    and approval.prior_receipt_digest == state["digest"]
                    and approval.writer_closure_reference is not None,
                    "REFERENCE_MISMATCH",
                )
                for raw in (approval.reference, approval.writer_closure_reference):
                    history._reference(sql.parse_canonical(raw))
                receipt = wire.parse_reservation_receipt_bytes(qualified.existing_receipt).to_dict()
                body = sql.parse_canonical(old["canonical"])
                sql.require(
                    old["sequence"] == state["sequence"]
                    and old["digest"] == state["digest"]
                    and receipt["sequence"] == state["sequence"] + 1
                    and receipt["operation_id"] == operation_id
                    and receipt["transition"] == "RETIRE_TERMINAL"
                    and body["terminal_witness_digest"]
                    == receipt["terminal_witness_digest"]
                    == ref["witness_digest"]
                    and body["open_witness_digest"]
                    == receipt["open_witness_digest"]
                    == record["open_witness_reference"]["witness_digest"],
                    "REFERENCE_MISMATCH",
                )
                challenge = secrets.token_hex(32)
                head = self.floor.verify_head(
                    _invoke(lambda: self.transport.head(challenge), "WITNESS_UNAVAILABLE"),
                    challenge=challenge,
                    now=self._now(),
                    minimum_sequence=self.floor.current()["sequence"],
                    minimum_token=self.floor.current()["fencing_token"],
                )
                sql.require(
                    head["sequence"] == receipt["sequence"]
                    and head["receipt_digest"] == sql.digest(receipt)
                    and head["active_reservation_id"] is None
                    and head["fencing_token"] == record["fencing_token"],
                    "COORDINATION_ROLLBACK",
                )
            else:
                sql.require(
                    qualified.existing_receipt is None and qualified.recovery_approval is None,
                    "TRUST_UNAPPROVED",
                )
                head = self.fresh_head(state)
            original = state["registry"][reservation_id]
            request = self._historical_request(state["claims"][original["request_id"]]["canonical"])
            attempt = OpeningAttempt(
                self, writer, request, record, qualified.authority_snapshot, head
            )
            attempt.sent = True
            attempt.receipt = state["witness_latest"][reservation_id]
            if recovery:
                attempt._persist_receipt(
                    receipt,
                    "RETIRE_TERMINAL",
                    operation_id,
                    "COMMITTED",
                    terminal_witness_reference=ref,
                    manual_resolution_reference=sql.parse_canonical(approval.reference),
                    uncertainty_codes=[],
                    recovery=True,
                )
                return copy.deepcopy(attempt.record)
            attempt._transition(
                transition="RETIRE_TERMINAL",
                operation_id=operation_id,
                result_state="COMMITTED",
                terminal_witness_reference=ref,
            )
            return copy.deepcopy(attempt.record)

    def record_owner_uncertain(self, *, reservation_id, operation_id, observation_reference):
        """Explicit qualified host detector/admin recording after the original owner is lost.

        This cannot originate from the normal executor RPC allowlist. Qualification
        must independently establish stopped ownership and external recording approval.
        """
        for value in (reservation_id, operation_id):
            sql.validate_scalar(value, {"type": "uuid", "not_null": True})
        reference = sql.parse_canonical(observation_reference)
        history._reference(reference)
        writer = HeldWriter(self.lock_store, self.lock_name, self.lock_identity)
        with writer:
            self._registry()
            state = self._local_state()
            record = state["latest"].get(reservation_id)
            old = state["observations"].get(operation_id)
            if old is not None:
                sql.require(
                    old["reservation_id"] == reservation_id
                    and old["observation_reference"] == reference,
                    "REPLAY_CONFLICT",
                )
                return copy.deepcopy(record)  # Recognition only; no repeat witness action.
            sql.require(
                record is not None
                and state["active"] == reservation_id
                and record["state"] in ("PENDING", "COMMITTED", "UNCERTAIN"),
                "UNCERTAIN_PUBLICATION",
            )
            challenge = secrets.token_hex(32)
            before = time.monotonic()
            floor = self.floor.current()
            head = _invoke(lambda: self.transport.head(challenge), "WITNESS_UNAVAILABLE")
            head = self.floor.verify_head(
                head,
                challenge=challenge,
                now=self._now(),
                minimum_sequence=floor["sequence"],
                minimum_token=floor["fencing_token"],
            )
            sql.require(time.monotonic() - before <= 30, "EXPIRED_OBSERVATION")
            qualification = _invoke(
                lambda: self.sources.qualify_owner_loss(
                    operation_id=operation_id,
                    record=copy.deepcopy(record),
                    observation_reference=observation_reference,
                    writer=writer,
                    coordination_head=copy.deepcopy(head),
                ),
                "TRUST_UNAPPROVED",
            )
            sql.require(
                type(qualification) is OwnerLossQualification
                and (qualification.expected_record_sequence, qualification.expected_record_digest)
                == (record["record_sequence"], record["record_digest"])
                and qualification.reason_code
                in (
                    "OWNER_DIED",
                    "WITNESS_RESULT_UNKNOWN",
                    "EVIDENCE_RESULT_UNKNOWN",
                    "LOCAL_COMMIT_UNKNOWN",
                    "RESPONSE_LOST",
                    "IDENTITY_REPLACED",
                    "CLOCK_UNHEALTHY",
                    "OBSERVATION_STALE",
                ),
                "TRUST_UNAPPROVED",
            )
            approval = sql.parse_canonical(qualification.operator_reference)
            history._reference(approval)
            observation = {
                "operation_id": operation_id,
                "reservation_id": reservation_id,
                "expected_record_sequence": record["record_sequence"],
                "expected_record_digest": record["record_digest"],
                "reason_code": qualification.reason_code,
                "observation_reference": reference,
                "operator_authorization_reference": approval,
            }
            canonical = sql.canonical_bytes(observation)
            sql.require(
                _invoke(
                    lambda: self.sources.verify_owner_loss(
                        canonical_observation=canonical, original_record=copy.deepcopy(record)
                    ),
                    "TRUST_UNAPPROVED",
                )
                is None,
                "TRUST_UNAPPROVED",
            )
            updated = copy.deepcopy(record)
            updated.update(
                state="UNCERTAIN",
                record_sequence=record["record_sequence"] + 1,
                previous_reservation_record_digest=record["record_digest"],
                updated_at=_utc(self._now()),
                uncertainty_codes=sorted(
                    set(record["uncertainty_codes"] + [qualification.reason_code])
                ),
            )
            updated = _seal_record(updated)
            with self._database(write=True) as con:
                con.execute("BEGIN IMMEDIATE")
                sql.require(self._read(con) == state, "REVISION_STALE")
                con.execute(
                    "INSERT INTO host_observations VALUES (?,?,?,?,?)",
                    (
                        operation_id,
                        reservation_id,
                        record["record_sequence"],
                        record["record_digest"],
                        canonical,
                    ),
                )
                self._append_event(con, state, updated)
                self._read(con)
                writer.check()
                con.commit()
            original = state["registry"][reservation_id]
            authenticated = self._historical_request(
                state["claims"][original["request_id"]]["canonical"]
            )
            attempt = OpeningAttempt(self, writer, authenticated, updated, None, head)
            attempt.sent = True
            attempt.receipt = state["witness_latest"].get(reservation_id)
            if qualification.existing_receipt is not None:
                receipt = wire.parse_reservation_receipt_bytes(
                    qualification.existing_receipt
                ).to_dict()
                sql.require(
                    receipt["operation_id"] in state["actions"]
                    and receipt["reservation_id"] == reservation_id
                    and receipt["transition"] in ("RESERVE", "RECORD_UNCERTAIN"),
                    "REFERENCE_MISMATCH",
                )
                attempt._persist_receipt(
                    receipt,
                    receipt["transition"],
                    receipt["operation_id"],
                    receipt["state"],
                    local_state="UNCERTAIN",
                    recovery=True,
                )
            if qualification.existing_receipt is None and head["sequence"] > state["sequence"]:
                sql.require(
                    head["active_reservation_id"] == reservation_id, "UNCERTAIN_PUBLICATION"
                )
                return copy.deepcopy(
                    attempt.record
                )  # Explicit recording only; separate receipt adoption needed.
            if attempt.receipt is None:
                sql.require(
                    head["sequence"] == state["sequence"] and head["active_reservation_id"] is None,
                    "UNCERTAIN_PUBLICATION",
                )
                return copy.deepcopy(attempt.record)
            if attempt.receipt["state"] != "UNCERTAIN":
                attempt._transition(
                    transition="RECORD_UNCERTAIN",
                    operation_id=operation_id,
                    result_state="UNCERTAIN",
                )
            return copy.deepcopy(attempt.record)

    def recover(self, *, raw_request):
        """Separate host-only admin entry; never admitted by a normal capability.

        Durable marker repair is explicit. The fixed qualifier proves stopped
        writers, independent no-open/existing OPEN, and exact external approval.
        """
        operation = wire.parse_request_bytes(raw_request).to_dict()["operation"]
        authenticated = self.authenticate_recovery(raw_request, operation)
        request = authenticated.request.to_dict()
        payload = request["payload"]
        writer = HeldWriter(self.lock_store, self.lock_name, self.lock_identity)
        with writer:
            self._registry()
            authenticated = self.authenticate_recovery(raw_request, operation)
            state = self._local_state()
            duplicate = self.recognize(state, authenticated)
            if duplicate is not None:
                return duplicate
            record = state["latest"].get(payload["reservation_id"])
            sql.require(
                record is not None
                and record["state"] == "UNCERTAIN"
                and (record["record_sequence"], record["record_digest"])
                == (payload["expected_record_sequence"], payload["expected_record_digest"])
                and state["active"] == record["reservation_id"],
                "REVISION_STALE",
            )
            # Idempotency token is a lifetime tombstone across all admin requests.
            sql.require(
                not any(
                    c["request"]["payload"].get("idempotency_token") == payload["idempotency_token"]
                    for c in state["claims"].values()
                ),
                "REPLAY_CONFLICT",
            )
            challenge = secrets.token_hex(32)
            before = time.monotonic()
            head = _invoke(lambda: self.transport.head(challenge), "WITNESS_UNAVAILABLE")
            floor = self.floor.current()
            head = self.floor.verify_head(
                head,
                challenge=challenge,
                now=self._now(),
                minimum_sequence=floor["sequence"],
                minimum_token=floor["fencing_token"],
            )
            sql.require(time.monotonic() - before <= 30, "EXPIRED_OBSERVATION")
            qualified = _invoke(
                lambda: self.sources.qualify_recovery(
                    authenticated_request=authenticated,
                    record=copy.deepcopy(record),
                    writer=writer,
                    coordination_head=copy.deepcopy(head),
                ),
                "TRUST_UNAPPROVED",
            )
            sql.require(
                type(qualified) is RecoveryQualification
                and type(qualified.approval) is RecoveryApproval,
                "TRUST_UNAPPROVED",
            )
            approval = qualified.approval
            sql.require(
                (
                    approval.operation_id,
                    approval.reservation_id,
                    approval.prior_receipt_digest,
                    sql.parse_canonical(approval.reference),
                )
                == (
                    request["request_id"],
                    record["reservation_id"],
                    head["receipt_digest"],
                    payload["operator_authorization_reference"],
                ),
                "REFERENCE_MISMATCH",
            )
            if "writer_closure_reference" in payload:
                sql.require(
                    sql.parse_canonical(approval.writer_closure_reference)
                    == payload["writer_closure_reference"],
                    "REFERENCE_MISMATCH",
                )
            if operation != wire.Operation.RESOLVE_COMMITTED:
                sql.require(
                    approval.no_open_reference is not None
                    and sql.parse_canonical(approval.no_open_reference)
                    == payload["no_open_certificate_reference"],
                    "TRUST_UNAPPROVED",
                )
            for raw in (approval.reference, approval.writer_closure_reference):
                history._reference(sql.parse_canonical(raw))
            authority = history.current_revision(qualified.authority_snapshot)
            sql.require(
                (authority["authority_id"], authority["epoch"])
                == (self.authority_id, self.authority_epoch),
                "REFERENCE_MISMATCH",
            )
            if operation == wire.Operation.RESOLVE_COMMITTED:
                self._check_existing_open(qualified, record, payload)
            else:
                sql.require(
                    authority["revision"] == record["previous_authority_revision"]
                    and authority["witness_digest"] == record["previous_witness_digest"]
                    and history.find_run(qualified.authority_snapshot, record["run_id"]) is None,
                    "UNCERTAIN_PUBLICATION",
                )
            fresh = payload["fresh_authority_head"]
            sql.require(
                (
                    fresh["authority_id"],
                    fresh["authority_epoch"],
                    fresh["revision"],
                    fresh["ledger_digest"],
                    fresh["witness_digest"],
                )
                == (
                    authority["authority_id"],
                    authority["epoch"],
                    authority["revision"],
                    authority["ledger_digest"],
                    authority["witness_digest"],
                ),
                "REVISION_STALE",
            )
            given = payload["fresh_coordination_head"]
            self.floor.verify_head(
                given,
                challenge=given["challenge"],
                now=self._now(),
                minimum_sequence=head["sequence"],
                minimum_token=head["fencing_token"],
            )
            sql.require(
                all(
                    given[k] == head[k]
                    for k in (
                        "namespace",
                        "authority_id",
                        "authority_epoch",
                        "sequence",
                        "receipt_digest",
                        "active_reservation_id",
                        "fencing_token",
                    )
                ),
                "REVISION_STALE",
            )
            if qualified.authority_completion is not None:
                sql.require(
                    type(qualified.authority_completion) is ManualAuthorityCompletion,
                    "TRUST_UNAPPROVED",
                )
                original_claim = state["claims"].get(
                    qualified.authority_completion.publication_operation_id
                )
                sql.require(
                    original_claim is not None
                    and original_claim["reservation_id"] == record["reservation_id"]
                    and original_claim["request"]["operation"] == "commit-open",
                    "REFERENCE_MISMATCH",
                )
                qualified.authority_completion.validate(
                    approved_snapshot=qualified.authority_snapshot,
                    operation=operation,
                    operator_reference=payload["operator_authorization_reference"],
                )
            self.authenticate_recovery(raw_request, operation)
            with self._database(write=True) as con:
                con.execute("BEGIN IMMEDIATE")
                sql.require(self._read(con) == state, "REVISION_STALE")
                self._claim(con, state, authenticated, record)
                self._read(con)
                writer.check()
                con.commit()
            # Any B3 completion uses persisted candidate/receipts and zero remote OPEN CAS.
            if qualified.authority_completion is not None:
                sql.require(
                    type(qualified.authority_completion) is ManualAuthorityCompletion,
                    "TRUST_UNAPPROVED",
                )
                self.authenticate_recovery(raw_request, operation)
                qualified.authority_completion.apply(
                    writer=writer,
                    approved_snapshot=qualified.authority_snapshot,
                    operation=operation,
                    operator_reference=payload["operator_authorization_reference"],
                )
            if operation == wire.Operation.RESOLVE_COMMITTED:
                self.authenticate_recovery(raw_request, operation)
                self._repair_existing_open_floor(qualified, record, payload, writer)
            original = state["registry"][record["reservation_id"]]
            original_request = self._historical_request(
                state["claims"][original["request_id"]]["canonical"]
            )
            attempt = OpeningAttempt(
                self, writer, original_request, record, qualified.authority_snapshot, head
            )
            attempt.sent = True
            attempt.receipt = state["witness_latest"].get(record["reservation_id"])
            attempt.action_request = authenticated
            # A lagging marker may reuse one exact already-sent independent receipt;
            # this is an explicitly approved repair, never another remote action.
            if qualified.existing_receipt is not None:
                receipt = wire.parse_reservation_receipt_bytes(qualified.existing_receipt).to_dict()
                action = state["actions"].get(receipt["operation_id"])
                sql.require(
                    action is not None and receipt["sequence"] == state["sequence"] + 1,
                    "REFERENCE_MISMATCH",
                )
                if receipt["transition"] == "RESERVE":
                    attempt._persist_receipt(
                        receipt,
                        "RESERVE",
                        receipt["operation_id"],
                        "PENDING",
                        local_state="UNCERTAIN",
                        recovery=True,
                    )
                elif (
                    operation == wire.Operation.RESOLVE_COMMITTED
                    and receipt["transition"] == "COMMIT_OPEN"
                ):
                    self._check_existing_open(qualified, record, payload)
                    attempt._persist_receipt(
                        receipt,
                        "COMMIT_OPEN",
                        receipt["operation_id"],
                        "COMMITTED",
                        open_witness_reference=sql.parse_canonical(
                            qualified.open_witness_reference
                        ),
                        evidence_receipt_reference=sql.parse_canonical(
                            qualified.evidence_reference
                        ),
                        manual_resolution_reference=payload["operator_authorization_reference"],
                        uncertainty_codes=[],
                        recovery=True,
                    )
                    return copy.deepcopy(attempt.record)
                else:
                    raise sql.AuthorityError("TRUST_UNAPPROVED")
            state = self.read()
            latest = state["witness_latest"].get(record["reservation_id"])
            if latest is None:
                sql.require(
                    operation != wire.Operation.RESOLVE_COMMITTED
                    and head["sequence"] == state["sequence"]
                    and head["active_reservation_id"] is None
                    and authority["revision"] == record["previous_authority_revision"]
                    and authority["witness_digest"] == record["previous_witness_digest"],
                    "UNCERTAIN_PUBLICATION",
                )
                attempt._append_local(
                    state,
                    target="CANCELLED_MANUALLY",
                    manual_resolution_reference=payload["operator_authorization_reference"],
                    uncertainty_codes=[],
                )
                return copy.deepcopy(attempt.record)
            attempt.receipt = latest
            attempt.head = self.fresh_head(state)
            sql.require(attempt.receipt["state"] == "UNCERTAIN", "UNCERTAIN_PUBLICATION")
            if operation == wire.Operation.RESOLVE_COMMITTED:
                self._check_existing_open(qualified, record, payload)
                attempt._transition(
                    transition="ADOPT_OPEN_MANUAL",
                    operation_id=request["request_id"],
                    result_state="COMMITTED",
                    recovery_approval=approval,
                    open_witness_reference=sql.parse_canonical(qualified.open_witness_reference),
                    evidence_receipt_reference=sql.parse_canonical(qualified.evidence_reference),
                    manual_resolution_reference=payload["operator_authorization_reference"],
                    uncertainty_codes=[],
                )
            else:
                sql.require(
                    authority["revision"] == record["previous_authority_revision"]
                    and authority["witness_digest"] == record["previous_witness_digest"]
                    and history.find_run(qualified.authority_snapshot, record["run_id"]) is None,
                    "UNCERTAIN_PUBLICATION",
                )
                sql.require(
                    approval.prior_receipt_digest == sql.digest(attempt.receipt), "REVISION_STALE"
                )
                attempt._transition(
                    transition="CANCEL_MANUAL",
                    operation_id=request["request_id"],
                    result_state="CANCELLED_MANUALLY",
                    recovery_approval=approval,
                    manual_resolution_reference=payload["operator_authorization_reference"],
                    uncertainty_codes=[],
                )
            return copy.deepcopy(attempt.record)

    def _check_existing_open(self, qualified, record, payload):
        sql.require(
            qualified.open_witness_reference is not None
            and qualified.evidence_reference is not None,
            "REFERENCE_MISMATCH",
        )
        sql.require(
            type(qualified.open_evidence) is evidence.QualifiedOpenEvidence, "REFERENCE_MISMATCH"
        )
        accepted = qualified.open_evidence.receipt.to_dict()
        ref = sql.parse_canonical(qualified.evidence_reference)
        sql.require(
            accepted == payload["open_evidence"]
            and ref["sha256"] == qualified.open_evidence.sha256
            and (
                accepted["reserve_request_id"],
                accepted["reservation_id"],
                accepted["fencing_token"],
                accepted["run_id"],
                accepted["identity_digest"],
                accepted["runtime_profile_sha256"],
                accepted["session_header_sha256"],
            )
            == (
                record["request_id"],
                record["reservation_id"],
                record["fencing_token"],
                record["run_id"],
                record["run_identity"]["identity_digest"],
                record["runtime_profile_sha256"],
                record["session_header_sha256"],
            ),
            "REFERENCE_MISMATCH",
        )
        head = sql.parse_canonical(qualified.open_witness_reference)
        current = history.current_revision(qualified.authority_snapshot)
        run = history.find_run(qualified.authority_snapshot, record["run_id"])
        sql.require(
            (
                head["authority_id"],
                head["authority_epoch"],
                head["revision"],
                head["ledger_digest"],
                head["witness_digest"],
            )
            == (
                self.authority_id,
                self.authority_epoch,
                record["expected_next_revision"],
                current["ledger_digest"],
                current["witness_digest"],
            )
            and current["revision"] == record["expected_next_revision"]
            and head["receipt_reference"]["sha256"] == head["witness_digest"]
            and head == payload["existing_open_witness_reference"]
            and payload["run_identity"] == record["run_identity"]
            and run is not None
            and run["lifecycle_state"] == "OPEN"
            and run["introduced_revision"] == record["expected_next_revision"],
            "REFERENCE_MISMATCH",
        )
        self._check_existing_open_floor(qualified, record, payload)

    def _check_existing_open_floor(self, qualified, record, payload):
        sql.require(type(qualified.authority_floor) is DurableFloor, "TRUST_UNAPPROVED")
        floor = qualified.authority_floor.current()
        head = payload["existing_open_witness_reference"]
        sql.require(
            (floor.authority_id, floor.authority_epoch)
            == (self.authority_id, self.authority_epoch),
            "REFERENCE_MISMATCH",
        )
        sql.require(
            (floor.revision, floor.witness_digest)
            in (
                (record["previous_authority_revision"], record["previous_witness_digest"]),
                (head["revision"], head["witness_digest"]),
            ),
            "ROLLBACK_DETECTED",
        )
        return floor

    def _repair_existing_open_floor(self, qualified, record, payload, writer):
        writer.check()
        floor = self._check_existing_open_floor(qualified, record, payload)
        head = payload["existing_open_witness_reference"]
        receipt = history._receipts(history._verified(qualified.authority_snapshot)["state"])[
            head["revision"]
        ]
        sql.require(sql.digest(receipt) == head["witness_digest"], "REFERENCE_MISMATCH")
        if floor.revision != head["revision"]:
            qualified.authority_floor.advance(receipt)
        accepted = self._check_existing_open_floor(qualified, record, payload)
        sql.require(
            (accepted.revision, accepted.witness_digest)
            == (head["revision"], head["witness_digest"]),
            "FLOOR_INCOMPLETE",
        )
        writer.check()

    @contextmanager
    def opening(self, *, raw_request, raw_record):
        writer = HeldWriter(self.lock_store, self.lock_name, self.lock_identity)
        authenticated = self.authenticate(raw_request, "reserve-start")
        record = wire.qualify_reservation(raw_record).to_dict()
        value = authenticated.request.to_dict()
        sql.require(
            record["record_sequence"] == 1
            and record["state"] == "PENDING"
            and record["request_id"] == value["request_id"]
            and record["nonce"] == value["nonce"]
            and record["request_digest"] == value["request_digest"],
            "REFERENCE_MISMATCH",
        )
        preliminary = self.read()
        duplicate = self.recognize(preliminary, authenticated)
        if duplicate is not None:
            yield duplicate
            return
        self.eligible(authenticated, record)
        self.fresh_head(preliminary)
        for store in (writer.store, self.store, self.receipt_store, self.floor.store):
            qualify_local_filesystem(store)
        with writer:
            writer.check()
            current = self.read()
            duplicate = self.recognize(current, authenticated)
            if duplicate is not None:
                yield duplicate
                return
            authenticated = self.authenticate(raw_request, "reserve-start")
            snapshot = self.eligible(authenticated, record)
            head = self.fresh_head(current)
            sql.require(
                current["active"] is None and head["active_reservation_id"] is None,
                "RESERVATION_BUSY",
            )
            sql.require(
                current["token"] < sql.MAX_COUNTER
                and record["fencing_token"] == current["token"] + 1,
                "FENCING_TOKEN_INVALID",
            )
            sql.require(
                sql.utc_time(record["requested_at"]).replace(tzinfo=UTC) <= self._now()
                and self._now() < sql.utc_time(record["freshness_deadline"]).replace(tzinfo=UTC),
                "EXPIRED_OBSERVATION",
            )
            self.authenticate(raw_request, "reserve-start")
            with self._database(write=True) as con:
                con.execute("BEGIN IMMEDIATE")
                actual = self._read(con)
                sql.require(actual == current, "REVISION_STALE")
                con.execute(
                    "INSERT INTO reservations VALUES (?,?,?)",
                    (record["reservation_id"], record["reservation_digest"], raw_record),
                )
                con.execute(
                    "INSERT INTO request_claims VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        NAMESPACE,
                        self.authority_id,
                        self.authority_epoch,
                        value["nonce"],
                        value["request_id"],
                        value["request_digest"],
                        value["operation"],
                        record["reservation_id"],
                        raw_request,
                    ),
                )
                self._append_event(con, current, record)
                con.execute(
                    "INSERT INTO remote_actions VALUES (?,?,?,?,?,?)",
                    (
                        value["request_id"],
                        "RESERVE",
                        record["reservation_id"],
                        head["sequence"],
                        head["receipt_digest"],
                        raw_record,
                    ),
                )
                self._read(con)
                writer.check()
                con.commit()
            attempt = OpeningAttempt(self, writer, authenticated, record, snapshot, head)
            attempt._grant()
            try:
                yield attempt
            finally:
                writer.check()  # Expiration never removes the durable pending/committed slot.


@dataclass(frozen=True, slots=True)
class TerminalQualification:
    authority_snapshot: object
    terminal_witness_reference: bytes
    floor: DurableFloor
    existing_receipt: bytes | None = None
    recovery_approval: RecoveryApproval | None = None


@dataclass(frozen=True, slots=True)
class OwnerLossQualification:
    operator_reference: bytes
    expected_record_sequence: int
    expected_record_digest: str
    reason_code: str
    existing_receipt: bytes | None = None


@dataclass(frozen=True, slots=True)
class RecoveryQualification:
    """Fixed admin qualifier output; external proof verification precedes any write."""

    approval: RecoveryApproval
    authority_snapshot: object
    authority_completion: object | None = None
    existing_receipt: bytes | None = None
    open_witness_reference: bytes | None = None
    evidence_reference: bytes | None = None
    open_evidence: evidence.QualifiedOpenEvidence | None = None
    authority_floor: DurableFloor | None = None


@dataclass(frozen=True, slots=True)
class ManualAuthorityCompletion:
    """Exact preexisting candidate adoption/manual close; never publish or create OPEN."""

    ledger_store: PrivateStore
    ledger_name: str
    ledger_identity: FileIdentity
    base_state: bytes
    candidate_store: PrivateStore
    candidate_path: str
    floor: DurableFloor
    publication_operation_id: str

    def validate(self, *, approved_snapshot, operation, operator_reference):
        base = sql.parse_canonical(self.base_state)
        target = history._verified(approved_snapshot)["state"]
        sql.validate_state(base)
        sql.validate_state(target)
        pending = [
            i
            for i in base["publication_intents"]
            if i["operation_id"] == self.publication_operation_id
        ]
        sql.require(
            len(pending) == 1
            and not any(
                r["operation_id"] == self.publication_operation_id
                for r in base["publication_resolutions"]
            ),
            "REPLAY_CONFLICT",
        )
        intent = pending[0]
        sql.require(intent["candidate_payload_path"] == self.candidate_path, "REFERENCE_MISMATCH")
        raw = self.candidate_store.read(self.candidate_path)
        sql.require(
            sql.digest(sql.parse_canonical(raw)) == intent["candidate_payload_sha256"],
            "REFERENCE_MISMATCH",
        )
        additions = {}
        for table in sql.TABLES:
            sql.require(all(row in target[table] for row in base[table]), "HISTORY_INVALID")
            additions[table] = [row for row in target[table] if row not in base[table]]
        resolutions = additions["publication_resolutions"]
        sql.require(
            len(resolutions) == 1
            and resolutions[0]["operation_id"] == self.publication_operation_id,
            "REFERENCE_MISMATCH",
        )
        resolution = resolutions[0]
        if operation == wire.Operation.RESOLVE_COMMITTED:
            sql.require(
                resolution["disposition"] == "COMMITTED"
                and resolution["committed_revision"] == intent["candidate_revision"]
                and raw
                == sql.canonical_bytes(
                    sql.logical_projection(target, intent["candidate_revision"])
                ),
                "REFERENCE_MISMATCH",
            )
            allowed = {
                "official_runs",
                "authority_events",
                "history_revisions",
                "artifact_references",
                "witness_checkpoints",
                "publication_resolutions",
            }
            sql.require(
                all(not rows for table, rows in additions.items() if table not in allowed)
                and history.current_revision(approved_snapshot)["revision"]
                == intent["candidate_revision"]
                and len(additions["official_runs"])
                == len(additions["authority_events"])
                == len(additions["history_revisions"])
                == len(additions["witness_checkpoints"])
                == 1
                and 1 <= len(additions["artifact_references"]) <= 3
                and additions["authority_events"][0]["event_kind"] == "RUN_OPENED"
                and additions["official_runs"][0]["run_version"] == 0
                and additions["official_runs"][0]["introduced_revision"]
                == intent["candidate_revision"],
                "REFERENCE_MISMATCH",
            )
            receipt = history._receipts(target)[intent["candidate_revision"]]
            sql.require(sql.digest(receipt) == resolution["receipt_digest"], "REFERENCE_MISMATCH")
        else:
            sql.require(
                resolution["disposition"] == "MANUAL_CANCELLED"
                and all(
                    not rows
                    for table, rows in additions.items()
                    if table != "publication_resolutions"
                )
                and {
                    "path": resolution["operator_approval_path"],
                    "sha256": resolution["operator_approval_sha256"],
                    "schema_version": resolution["operator_approval_schema"],
                }
                == operator_reference
                and approved_snapshot.write_closed is True,
                "TRUST_UNAPPROVED",
            )
            receipt = None
        return base, target, additions, receipt

    def apply(self, *, writer, approved_snapshot, operation, operator_reference):
        writer.check()
        base, target, additions, receipt = self.validate(
            approved_snapshot=approved_snapshot,
            operation=operation,
            operator_reference=operator_reference,
        )
        with existing_database(
            self.ledger_store,
            self.ledger_name,
            self.ledger_identity,
            write=True,
            schema_check=sql._verify_sql_schema,
        ) as con:
            con.execute("BEGIN IMMEDIATE")
            sql.require(
                sql._ordered_state(sql._read_state(con)) == sql._ordered_state(base),
                "REVISION_STALE",
            )
            sql._insert_rows(con, additions)
            sql.require(
                sql._ordered_state(sql._read_state(con)) == sql._ordered_state(target),
                "HISTORY_INVALID",
            )
            writer.check()
            con.commit()
        if receipt is not None:
            floor = self.floor.current()
            if floor.revision == receipt["revision"]:
                sql.require(floor.witness_digest == sql.digest(receipt), "ROLLBACK_DETECTED")
            else:
                self.floor.advance(receipt)
        writer.check()


@dataclass(frozen=True, slots=True)
class OpenPublication:
    """Host-qualified existing evidence and exact B3 additions, no request-supplied adapter."""

    open_evidence: evidence.QualifiedOpenEvidence
    evidence_reference: bytes
    governance_certificate: governance.GovernanceCertificateV1
    governance_reference: bytes
    reference_additions: tuple
    run: dict
    event: dict
    publication: dict


class _OpeningPublicationVerifier:
    """B3 serialization admission backed by the exact live W05 writer and durable claim."""

    def __init__(self, opening, authenticated, prepared):
        self.opening, self.authenticated, self.prepared = opening, authenticated, prepared

    def verify(
        self, *, authority_id, epoch, operation_id, revision, action, proof, candidate_state
    ):
        opening = self.opening
        j = opening.journal
        opening.check()
        request = self.authenticated.request.to_dict()
        sql.require(
            (authority_id, epoch, operation_id, revision, action)
            == (
                j.authority_id,
                j.authority_epoch,
                request["request_id"],
                opening.record["expected_next_revision"],
                "RUN_OPENED",
            )
            and sql.canonical_bytes(proof) == self.authenticated.request.canonical,
            "B4_CAPABILITY_REQUIRED",
        )
        j.authenticate(self.authenticated.request.canonical, "commit-open")
        state = j.read()
        claim = state["claims"].get(operation_id)
        sql.require(
            claim is not None
            and claim["canonical"] == self.authenticated.request.canonical
            and claim["reservation_id"] == opening.record["reservation_id"]
            and state["active"] == opening.record["reservation_id"]
            and opening.receipt["transition"] == "RESERVE"
            and opening.receipt["state"] == "PENDING",
            "B4_CAPABILITY_REQUIRED",
        )
        candidate = sql.parse_canonical(candidate_state)
        sql.require(
            [r for r in candidate["official_runs"] if r["run_id"] == opening.record["run_id"]]
            == [self.prepared.run]
            and [e for e in candidate["authority_events"] if e["revision"] == revision]
            == [self.prepared.event],
            "B4_CAPABILITY_REQUIRED",
        )


class OpeningAttempt:
    """Internal context kept under one actual global writer; never deserializable."""

    def __init__(self, journal, writer, authenticated, record, snapshot, head):
        self.journal, self.writer, self.request = journal, writer, authenticated
        self.record, self.snapshot, self.head = copy.deepcopy(record), snapshot, copy.deepcopy(head)
        self.sent, self.receipt = False, None
        self.actions_sent = set()
        self.open_result = None
        self.commit_request = None
        self.action_request = None

    def check(self):
        self.writer.check()
        sql.require(
            type(self.journal) is CoordinationJournal and self.sent and self.receipt is not None,
            "SERIALIZATION_REQUIRED",
        )
        state = self.journal.read()
        latest = state["latest"].get(self.record["reservation_id"])
        sql.require(
            latest == self.record
            and self.receipt == state["witness_latest"].get(self.record["reservation_id"])
            and (self.writer.store.identity, self.writer.name, self.writer.identity)
            == (
                self.journal.lock_store.identity,
                self.journal.lock_name,
                self.journal.lock_identity,
            ),
            "REVISION_STALE",
        )

    def _same_owner(self, authenticated):
        request = authenticated.request.to_dict()
        reserve = self.request.request.to_dict()
        for field in (
            "exchange_id",
            "session_id",
            "session_header_sha256",
            "runtime_profile_sha256",
            "authority_id",
            "authority_epoch",
            "source_revision",
            "project",
        ):
            sql.require(request[field] == reserve[field], "REFERENCE_MISMATCH")
        for field in (
            "holder_public_key_hex",
            "deployment_verification_sha256",
            "container_identity_digest",
        ):
            sql.require(
                request["capability"][field] == reserve["capability"][field], "REFERENCE_MISMATCH"
            )
        sql.require(
            (
                request["capability"]["reservation_id"],
                request["capability"]["reservation_digest"],
                request["capability"]["fencing_token"],
            )
            == (
                self.record["reservation_id"],
                self.record["reservation_digest"],
                self.record["fencing_token"],
            ),
            "REFERENCE_MISMATCH",
        )

    def _append_local(self, state, *, target, **updates):
        j = self.journal
        self.writer.check()
        value = copy.deepcopy(self.record)
        value.update(updates)
        value.update(
            state=target,
            record_sequence=self.record["record_sequence"] + 1,
            previous_reservation_record_digest=self.record["record_digest"],
            updated_at=_utc(j._now()),
        )
        value = _seal_record(value)
        with j._database(write=True) as con:
            con.execute("BEGIN IMMEDIATE")
            sql.require(j._read(con) == state, "REVISION_STALE")
            j._append_event(con, state, value)
            j._read(con)
            self.writer.check()
            con.commit()
        self.record = value

    def _fresh_action(self):
        if self.action_request is None:
            return  # Host owner-loss recording has its separately qualified proof.
        request = self.action_request.request
        if request.operation in wire.RECOVERY_OPERATIONS:
            self.journal.authenticate_recovery(request.canonical, request.operation)
        else:
            self.journal.authenticate(request.canonical, request.operation)

    def _transition(
        self, *, transition, operation_id, result_state, recovery_approval=None, **updates
    ):
        j = self.journal
        self.check()
        self._fresh_action()
        state = j.read()
        self.head = j.fresh_head(state)
        sql.require(
            operation_id not in self.actions_sent and operation_id not in state["actions"],
            "REPLAY_CONFLICT",
        )
        open_ref = updates.get("open_witness_reference", self.record["open_witness_reference"])
        terminal_ref = updates.get(
            "terminal_witness_reference", self.record["terminal_witness_reference"]
        )
        body = {
            "reservation_id": self.record["reservation_id"],
            "transition": transition,
            "operation_id": operation_id,
            "sequence": state["sequence"],
            "receipt_digest": state["digest"],
            "open_witness_digest": None if open_ref is None else open_ref["witness_digest"],
            "terminal_witness_digest": None
            if terminal_ref is None
            else terminal_ref["witness_digest"],
        }
        with j._database(write=True) as con:
            con.execute("BEGIN IMMEDIATE")
            sql.require(j._read(con) == state, "REVISION_STALE")
            con.execute(
                "INSERT INTO remote_actions VALUES (?,?,?,?,?,?)",
                (
                    operation_id,
                    transition,
                    self.record["reservation_id"],
                    state["sequence"],
                    state["digest"],
                    sql.canonical_bytes(body),
                ),
            )
            j._read(con)
            self.writer.check()
            con.commit()
        # Mark before dispatch; any exception leaves the durable action and active slot.
        self.actions_sent.add(operation_id)
        self.writer.check()
        self._fresh_action()
        receipt = _invoke(
            lambda: j.transport.transition(
                reservation_id=body["reservation_id"],
                transition=transition,
                operation_id=operation_id,
                expected_sequence=body["sequence"],
                expected_receipt_digest=body["receipt_digest"],
                open_witness_digest=body["open_witness_digest"],
                terminal_witness_digest=body["terminal_witness_digest"],
                recovery_approval=recovery_approval,
            ),
            "UNCERTAIN_PUBLICATION",
        )
        self._persist_receipt(receipt, transition, operation_id, result_state, **updates)

    def complete_open(self, *, raw_request):
        """Consume one already-created evidence receipt under the original live writer.

        The deployment qualifier supplies verified W01 evidence, full GOV context and
        the actual durable W04 adapter; no generic callback is accepted from the RPC.
        """
        j = self.journal
        self.check()
        authenticated = j.authenticate(raw_request, "commit-open")
        self._same_owner(authenticated)
        state = j.read()
        duplicate = j.recognize(state, authenticated)
        if duplicate is not None:
            return duplicate
        sql.require(
            self.record["state"] == "PENDING" and self.open_result is None, "UNCERTAIN_PUBLICATION"
        )
        j.fresh_head(state)
        request = authenticated.request.to_dict()
        payload = request["payload"]
        for field in ("reservation_id", "reservation_digest", "fencing_token", "run_identity"):
            sql.require(payload[field] == self.record[field], "REFERENCE_MISMATCH")
        sql.require(
            payload["reserve_request_id"] == self.record["request_id"]
            and payload["reservation_receipt_reference"]
            == self.record["reservation_receipt_reference"]
            and payload["expected_head"]
            == self.request.request.to_dict()["payload"]["expected_head"],
            "REFERENCE_MISMATCH",
        )
        # Fresh authority check includes exact ACKs immediately before publication.
        snapshot = j.eligible(
            self.request, self.journal.read()["registry"][self.record["reservation_id"]]
        )
        self.snapshot = snapshot
        prepared = _invoke(
            lambda: j.sources.qualify_open(authenticated_request=authenticated, opening=self),
            "REFERENCE_MISMATCH",
        )
        sql.require(
            type(prepared) is OpenPublication
            and type(prepared.open_evidence) is evidence.QualifiedOpenEvidence
            and type(prepared.governance_certificate) is governance.GovernanceCertificateV1,
            "REFERENCE_MISMATCH",
        )
        evd = prepared.open_evidence.receipt.to_dict()
        sql.require(
            evd == payload["open_evidence"]
            and prepared.open_evidence.sha256 == sql.digest(evd)
            and evd["reserve_request_id"] == self.record["request_id"]
            and evd["open_permit_digest"] == payload["open_permit_digest"],
            "REFERENCE_MISMATCH",
        )
        evidence_ref = sql.parse_canonical(prepared.evidence_reference)
        history._reference(evidence_ref)
        sql.require(
            evidence_ref["sha256"] == prepared.open_evidence.sha256
            and evidence_ref["schema_version"] == evd["schema_version"],
            "REFERENCE_MISMATCH",
        )
        publication = dict(prepared.publication)
        adapter = publication.get("adapter")
        sql.require(
            type(adapter) is AuthorityBackend
            and adapter.writer is self.writer
            and type(adapter.floor) is DurableFloor,
            "SERIALIZATION_REQUIRED",
        )
        cert = governance.validate_governance_certificate(prepared.governance_certificate).value
        sql.require(
            (
                cert["commit_request_id"],
                cert["commit_request_digest"],
                cert["reserve_request_id"],
                cert["reserve_request_digest"],
                cert["run_id"],
                cert["run_identity_digest"],
                cert["open_evidence_reference"],
                cert["open_one_permit"]["permit_digest"],
            )
            == (
                request["request_id"],
                request["request_digest"],
                self.record["request_id"],
                self.record["request_digest"],
                self.record["run_id"],
                self.record["run_identity"]["identity_digest"],
                evidence_ref,
                payload["open_permit_digest"],
            ),
            "REFERENCE_MISMATCH",
        )
        # Snapshot head used by GOV must be the exact signed source qualified by host.
        sql.require(
            history.current_revision(snapshot)["witness_digest"]
            == history.current_revision(self.snapshot)["witness_digest"],
            "REVISION_STALE",
        )
        j.authenticate(raw_request, "commit-open")
        with j._database(write=True) as con:
            con.execute("BEGIN IMMEDIATE")
            sql.require(j._read(con) == state, "REVISION_STALE")
            j._claim(con, state, authenticated, self.record)
            j._read(con)
            self.writer.check()
            con.commit()
        self.commit_request = authenticated
        self.action_request = authenticated
        publication["verifier"] = _OpeningPublicationVerifier(self, authenticated, prepared)
        publication["capability"] = authenticated.request.to_dict()
        result = history._append_run_opened(
            self.snapshot,
            copy.deepcopy(prepared.run),
            copy.deepcopy(prepared.event),
            governance_certificate=prepared.governance_certificate,
            governance_certificate_reference=sql.parse_canonical(prepared.governance_reference),
            reference_additions=prepared.reference_additions,
            **publication,
        )
        self.writer.check()
        head = history.current_revision(result)
        run = history.find_run(result, self.record["run_id"])
        floor = adapter.floor.current()
        sql.require(
            head["revision"] == self.record["expected_next_revision"]
            and floor.revision == head["revision"]
            and floor.witness_digest == head["witness_digest"]
            and run is not None
            and run["lifecycle_state"] == "OPEN"
            and run["introduced_revision"] == head["revision"],
            "REFERENCE_MISMATCH",
        )
        witness_ref = {
            "authority_id": head["authority_id"],
            "authority_epoch": head["epoch"],
            "revision": head["revision"],
            "ledger_digest": head["ledger_digest"],
            "witness_digest": head["witness_digest"],
            "receipt_reference": _reference(
                adapter.floor._path(head["revision"]),
                sql.parse_canonical(
                    adapter.floor.store.read(adapter.floor._path(head["revision"]))
                ),
            ),
        }
        self.open_result = result
        self._transition(
            transition="COMMIT_OPEN",
            operation_id=request["request_id"],
            result_state="COMMITTED",
            open_witness_reference=witness_ref,
            evidence_receipt_reference=evidence_ref,
            uncertainty_codes=[],
        )
        response = _invoke(
            lambda: j.sources.sign_response(authenticated_request=authenticated, opening=self),
            "UNCERTAIN_PUBLICATION",
        )
        return self.save_response(authenticated=authenticated, raw_response=response)

    def save_response(self, *, authenticated, raw_response):
        self.check()
        j = self.journal
        state = j.read()
        request = authenticated.request.to_dict()
        sql.require(request["request_id"] in state["claims"], "REPLAY_CONFLICT")
        response = wire.verify_response(
            raw_response, authenticated_request=authenticated, bridge_pin=j.bridge_pin, now=j._now()
        ).to_dict()
        if request["operation"] == "commit-open" and response["status"] == "OK":
            sql.require(
                self.record["state"] == "COMMITTED"
                and response["result"]["reservation"] == self.record
                and response["result"]["open_witness"] == self.record["open_witness_reference"]
                and response["result"]["open_evidence"] == request["payload"]["open_evidence"],
                "REFERENCE_MISMATCH",
            )
        old = state["responses"].get(request["request_id"])
        if old is not None:
            sql.require(old == raw_response, "REPLAY_CONFLICT")
            return old
        with j._database(write=True) as con:
            con.execute("BEGIN IMMEDIATE")
            sql.require(j._read(con) == state, "REVISION_STALE")
            con.execute(
                "INSERT INTO response_records VALUES (?,?,?)",
                (request["request_id"], response["response_digest"], raw_response),
            )
            j._read(con)
            self.writer.check()
            con.commit()
        return raw_response

    def record_uncertain(self, *, raw_request):
        self.check()
        j = self.journal
        authenticated = j.authenticate(raw_request, "record-uncertain")
        self._same_owner(authenticated)
        state = j.read()
        duplicate = j.recognize(state, authenticated)
        if duplicate is not None:
            return duplicate
        request = authenticated.request.to_dict()
        payload = request["payload"]
        sql.require(
            (payload["reservation_id"], payload["fencing_token"])
            == (self.record["reservation_id"], self.record["fencing_token"]),
            "REFERENCE_MISMATCH",
        )
        sql.require(
            _invoke(
                lambda: j.sources.verify_uncertainty(
                    authenticated_request=authenticated, opening=self
                ),
                "TRUST_UNAPPROVED",
            )
            is None,
            "TRUST_UNAPPROVED",
        )
        with j._database(write=True) as con:
            con.execute("BEGIN IMMEDIATE")
            sql.require(j._read(con) == state, "REVISION_STALE")
            j._claim(con, state, authenticated, self.record)
            j._read(con)
            self.writer.check()
            con.commit()
        self.action_request = authenticated
        self._append_local(j.read(), target="UNCERTAIN", uncertainty_codes=[payload["reason_code"]])
        self._transition(
            transition="RECORD_UNCERTAIN",
            operation_id=request["request_id"],
            result_state="UNCERTAIN",
        )
        return copy.deepcopy(self.record)

    def _grant(self):
        self.writer.check()
        sql.require(not self.sent, "REPLAY_CONFLICT")
        self.journal.authenticate(self.request.request.canonical, "reserve-start")
        self.sent = True
        receipt = _invoke(
            lambda: self.journal.transport.reserve(
                sql.canonical_bytes(self.record),
                expected_sequence=self.head["sequence"],
                expected_digest=self.head["receipt_digest"],
            ),
            "UNCERTAIN_PUBLICATION",
        )
        self._persist_receipt(receipt, "RESERVE", self.record["request_id"], "PENDING")

    def _persist_receipt(
        self,
        receipt,
        transition,
        operation_id,
        result_state,
        *,
        local_state=None,
        recovery=False,
        **updates,
    ):
        j = self.journal
        self.writer.check()
        state = j._local_state() if recovery else j.read()
        expected = {
            "schema_version": "s11-ex10-b4-reservation-receipt-v1",
            "namespace": NAMESPACE,
            "authority_id": j.authority_id,
            "authority_epoch": j.authority_epoch,
            "reservation_id": self.record["reservation_id"],
            "reservation_digest": self.record["reservation_digest"],
            "state": result_state,
            "fencing_token": self.record["fencing_token"],
            "sequence": state["sequence"] + 1,
            "previous_receipt_digest": state["digest"],
            "base_authority_revision": self.record["previous_authority_revision"],
            "base_authority_witness_digest": self.record["previous_witness_digest"],
            "open_witness_digest": None
            if updates.get("open_witness_reference", self.record["open_witness_reference"]) is None
            else updates.get("open_witness_reference", self.record["open_witness_reference"])[
                "witness_digest"
            ],
            "terminal_witness_digest": None
            if updates.get("terminal_witness_reference", self.record["terminal_witness_reference"])
            is None
            else updates.get(
                "terminal_witness_reference", self.record["terminal_witness_reference"]
            )["witness_digest"],
            "transition": transition,
            "operation_id": operation_id,
            "key_id": receipt.get("key_id"),
            "key_epoch": receipt.get("key_epoch"),
        }
        pin = next(
            (
                p
                for p in j.floor.pins
                if (p.key_id, p.key_epoch) == (receipt.get("key_id"), receipt.get("key_epoch"))
            ),
            None,
        )
        sql.require(pin is not None, "TRUST_UNAPPROVED")
        wire.verify_reservation_receipt(
            sql.canonical_bytes(receipt),
            expected_context=wire.ReceiptContext(sql.canonical_bytes(expected), j._now()),
            witness_pin=pin,
        )
        path = j.receipt_store.root + f"/receipt-{receipt['sequence']:020d}.json"
        if recovery:
            j.receipt_store.check()
            try:
                os.stat(
                    j.receipt_store.name(path), dir_fd=j.receipt_store.fd, follow_symlinks=False
                )
            except FileNotFoundError:
                j.receipt_store.create_exact(path, sql.canonical_bytes(receipt))
            except OSError:
                raise sql.AuthorityError("DURABLE_WRITE_UNKNOWN") from None
            else:
                sql.require(
                    j.receipt_store.read(path) == sql.canonical_bytes(receipt), "REFERENCE_MISMATCH"
                )
        else:
            j.receipt_store.create_exact(path, sql.canonical_bytes(receipt))
        challenge = secrets.token_hex(32)
        before = time.monotonic()
        head = _invoke(lambda: j.transport.head(challenge), "UNCERTAIN_PUBLICATION")
        accepted = j.floor.verify_head(
            head,
            challenge=challenge,
            now=j._now(),
            minimum_sequence=receipt["sequence"],
            minimum_token=receipt["fencing_token"],
        )
        sql.require(
            time.monotonic() - before <= 30
            and accepted["sequence"] == receipt["sequence"]
            and accepted["receipt_digest"] == sql.digest(receipt)
            and accepted["active_reservation_id"]
            == (
                None
                if transition in ("CANCEL_MANUAL", "RETIRE_TERMINAL")
                else self.record["reservation_id"]
            ),
            "COORDINATION_ROLLBACK",
        )
        j.floor.advance(accepted, challenge=challenge, now=j._now())
        value = copy.deepcopy(self.record)
        value.update(updates)
        value.update(
            state=result_state if local_state is None else local_state,
            record_sequence=value["record_sequence"] + 1,
            previous_reservation_record_digest=self.record["record_digest"],
            updated_at=_utc(j._now()),
            reservation_receipt_reference=_reference(path, receipt),
        )
        value = _seal_record(value)
        with j._database(write=True) as con:
            con.execute("BEGIN IMMEDIATE")
            actual = j._read(con)
            sql.require(
                actual == state and actual["latest"][self.record["reservation_id"]] == self.record,
                "REVISION_STALE",
            )
            con.execute(
                "INSERT INTO remote_receipts VALUES (?,?,?,?)",
                (
                    receipt["sequence"],
                    operation_id,
                    sql.digest(receipt),
                    sql.canonical_bytes(receipt),
                ),
            )
            con.execute(
                "UPDATE coordination_metadata SET witness_sequence=?,witness_digest=?,"
                "fencing_token=? WHERE singleton=1",
                (receipt["sequence"], sql.digest(receipt), receipt["fencing_token"]),
            )
            j._append_event(con, state, value)
            j._read(con)
            self.writer.check()
            con.commit()
        self.record, self.receipt, self.head = value, copy.deepcopy(receipt), accepted


def _provision_coordination_database(
    *, store, name, registry_binding, authority_id, authority_epoch, authorizer, credentials
):
    """Internal host-admin provisioning only; normal journal cannot create/reset it."""
    sql.require(authorizer is not None and credentials is not None, "TRUST_UNAPPROVED")
    body = sql.canonical_bytes(
        {
            "namespace": NAMESPACE,
            "authority_id": authority_id,
            "authority_epoch": authority_epoch,
            "registry_binding": sql.parse_canonical(registry_binding),
        }
    )
    sql.require(
        _invoke(
            lambda: authorizer.verify(
                operation="PROVISION_COORDINATION", canonical_body=body, credentials=credentials
            ),
            "TRUST_UNAPPROVED",
        )
        is None,
        "TRUST_UNAPPROVED",
    )
    store.create_exact(store.root + "/" + name, b"")
    info = os.stat(name, dir_fd=store.fd, follow_symlinks=False)
    identity = FileIdentity(info.st_dev, info.st_ino, info.st_uid, 0o600)
    with existing_database(store, name, identity, write=True) as con:
        sql.require(not con.execute("SELECT name FROM sqlite_master").fetchall(), "SCHEMA_TAMPERED")
        con.execute("BEGIN IMMEDIATE")
        for statement in schema_statements():
            con.execute(statement)
        con.execute(
            "INSERT INTO coordination_metadata VALUES (1,?,?,?,?,0,NULL,0,NULL,0)",
            (NAMESPACE, authority_id, authority_epoch, registry_binding),
        )
        verify_schema(con)
        con.commit()
    return identity
