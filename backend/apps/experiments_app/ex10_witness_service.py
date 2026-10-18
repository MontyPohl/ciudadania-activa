"""Independent durable B3/B4 witness core with deployment-injected authentication.

No listener, key loader/generator, executor genesis/reset API or transport fallback.
Only a separately approved service account owns this store and signing providers.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from . import ex10_authority_sqlite as sql
from . import ex10_authority_witness as b3
from . import ex10_bridge_protocol as b4
from .ex10_authority_backend import FileIdentity, PrivateStore, existing_database

NAMESPACE = "S11C3-EX10"


class ServiceAuthorizer(Protocol):
    """Deployment-selected peer/transport verifier; never supplied by a request."""

    def verify(self, *, operation: str, canonical_body: bytes, credentials: object) -> None: ...


@dataclass(frozen=True, slots=True)
class RecoveryApproval:
    """Externally verified admin reference and exact stopped-writer/no-open bindings."""

    operation_id: str
    reservation_id: str
    prior_receipt_digest: str
    reference: bytes
    writer_closure_reference: bytes
    no_open_reference: bytes | None


class RecoveryAuthorizer(Protocol):
    def verify(
        self,
        *,
        transition: str,
        approval: RecoveryApproval,
        canonical_receipt: bytes,
        credentials: object,
    ) -> None: ...


TABLE_STATEMENTS = (
    "CREATE TABLE service_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
    "authority_id TEXT NOT NULL, authority_epoch INTEGER NOT NULL CHECK(authority_epoch>0), "
    "namespace TEXT NOT NULL CHECK(namespace='S11C3-EX10'), genesis_digest TEXT NOT NULL, "
    "genesis_binding BLOB NOT NULL) STRICT",
    "CREATE TABLE history_receipts (revision INTEGER PRIMARY KEY CHECK(revision>=0), "
    "operation_id TEXT NOT NULL UNIQUE, digest TEXT NOT NULL UNIQUE, "
    "canonical BLOB NOT NULL) STRICT",
    "CREATE TABLE reservation_intents (reservation_id TEXT PRIMARY KEY, "
    "reservation_digest TEXT NOT NULL UNIQUE, request_id TEXT NOT NULL UNIQUE, "
    "nonce TEXT NOT NULL UNIQUE, run_id TEXT NOT NULL UNIQUE, canonical BLOB NOT NULL) STRICT",
    "CREATE TABLE reservation_receipts (sequence INTEGER PRIMARY KEY CHECK(sequence>0), "
    "reservation_id TEXT NOT NULL REFERENCES reservation_intents(reservation_id), "
    "transition TEXT NOT NULL, operation_id TEXT NOT NULL, digest TEXT NOT NULL UNIQUE, "
    "canonical BLOB NOT NULL, UNIQUE(transition,operation_id)) STRICT",
)


def service_schema_statements():
    result = list(TABLE_STATEMENTS)
    for table in (
        "service_identity",
        "history_receipts",
        "reservation_intents",
        "reservation_receipts",
    ):
        # Duplicate INSERT/REPLACE is rejected before SQLite's conflict policy can remove rows.
        key = {
            "service_identity": "singleton",
            "history_receipts": "revision",
            "reservation_intents": "reservation_id",
            "reservation_receipts": "sequence",
        }[table]
        result.append(
            f"CREATE TRIGGER reject_replace_{table} BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {key}=NEW.{key}) "
            "BEGIN SELECT RAISE(ABORT, 'APPEND_ONLY'); END"
        )
        for action in ("UPDATE", "DELETE"):
            result.append(
                f"CREATE TRIGGER reject_{action.lower()}_{table} BEFORE {action} "
                f"ON {table} BEGIN SELECT RAISE(ABORT, 'APPEND_ONLY'); END"
            )
    return tuple(result)


def verify_service_schema(con):
    expected = {(s.split()[1].lower(), s.split()[2]): s for s in service_schema_statements()}
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


def _provision_witness_database(
    *,
    store: PrivateStore,
    name: str,
    receipts,
    trust_pins,
    expected_genesis_digest,
    expected_genesis_binding,
    authorizer: ServiceAuthorizer,
    credentials: object,
):
    """Host-admin provisioning primitive. Normal service interfaces cannot call it.

    Receives a complete already signed, externally approved genesis/history vector;
    it never generates a key, signs genesis, resets a database or chooses a pin.
    """
    sql.require(authorizer is not None and credentials is not None, "TRUST_UNAPPROVED")
    witness_digest = b3.verify_chain(
        receipts,
        trust_pins,
        expected_genesis_digest=expected_genesis_digest,
        expected_genesis_binding=expected_genesis_binding,
    )
    body = sql.canonical_bytes(
        {
            "receipts": receipts,
            "genesis_digest": expected_genesis_digest,
            "genesis_binding": expected_genesis_binding,
        }
    )
    sql.require(
        authorizer.verify(
            operation="PROVISION_WITNESS", canonical_body=body, credentials=credentials
        )
        is None,
        "TRUST_UNAPPROVED",
    )
    store.create_exact(store.root + "/" + name, b"")
    identity = FileIdentity(
        store.identity.device,
        os.stat(name, dir_fd=store.fd, follow_symlinks=False).st_ino,
        store.identity.owner,
        0o600,
    )
    with existing_database(store, name, identity, write=True) as con:
        sql.require(not con.execute("SELECT name FROM sqlite_master").fetchall(), "SCHEMA_TAMPERED")
        con.execute("BEGIN IMMEDIATE")
        for statement in service_schema_statements():
            con.execute(statement)
        first = receipts[0]
        con.execute(
            "INSERT INTO service_identity VALUES (1,?,?,?,?,?)",
            (
                first["authority_id"],
                first["authority_epoch"],
                NAMESPACE,
                expected_genesis_digest,
                sql.canonical_bytes(expected_genesis_binding),
            ),
        )
        for receipt in receipts:
            con.execute(
                "INSERT INTO history_receipts VALUES (?,?,?,?)",
                (
                    receipt["revision"],
                    receipt["operation_id"],
                    sql.digest(receipt),
                    sql.canonical_bytes(receipt),
                ),
            )
        verify_service_schema(con)
        con.commit()
    sql.require(witness_digest == sql.digest(receipts[-1]), "FORK_DETECTED")
    return identity


class WitnessService:
    """Existing independent store, authenticated single-attempt CAS and signed observations."""

    def __init__(
        self,
        *,
        store,
        name,
        identity,
        authority_id,
        authority_epoch,
        genesis_digest,
        genesis_binding,
        b3_pins,
        b3_signer_pin,
        b3_signer,
        b4_pin,
        b4_pins,
        b4_signer,
        authorizer,
        recovery_authorizer,
        clock,
        clock_health,
        b3_head_signers=(),
    ):
        sql.require(
            type(store) is PrivateStore
            and type(identity) is FileIdentity
            and type(b3_signer_pin) is b3.TrustPin
            and type(b4_pin) is b4.PublicKeyPin
            and authorizer is not None
            and callable(clock)
            and callable(clock_health),
            "TRUST_UNAPPROVED",
        )
        sql.validate_scalar(authority_id, {"type": "uuid", "not_null": True})
        sql.validate_scalar(authority_epoch, {"type": "positive_int64", "not_null": True})
        self.store, self.name, self.identity = store, name, identity
        self.authority_id, self.authority_epoch = authority_id, authority_epoch
        self.genesis_digest, self.genesis_binding = genesis_digest, copy.deepcopy(genesis_binding)
        self.b3_pins, self.b3_pin, self.b3_signer = tuple(b3_pins), b3_signer_pin, b3_signer
        # Fixed deployment providers, never accepted in operation/request arguments.
        sql.require(type(b3_head_signers) is tuple, "TRUST_UNAPPROVED")
        self._head_signers = {self.b3_pin.canonical_record: self.b3_signer}
        for item in b3_head_signers:
            sql.require(type(item) is tuple and len(item) == 2, "TRUST_UNAPPROVED")
            pin, signer = item
            sql.require(
                type(pin) is b3.TrustPin
                and pin.canonical_record in {p.canonical_record for p in self.b3_pins}
                and pin.canonical_record not in self._head_signers
                and callable(getattr(signer, "sign", None)),
                "TRUST_UNAPPROVED",
            )
            self._head_signers[pin.canonical_record] = signer
        self.b4_pin, self.b4_signer = b4_pin, b4_signer
        self.b4_pins = tuple(b4_pins)
        sql.require(
            self.b4_pin in self.b4_pins
            and len({(p.key_id, p.key_epoch) for p in self.b4_pins}) == len(self.b4_pins),
            "TRUST_UNAPPROVED",
        )
        self.authorizer, self.recovery_authorizer = authorizer, recovery_authorizer
        self.clock, self.clock_health = clock, clock_health
        with self._database() as con:
            self._read(con)

    def _database(self, *, write=False):
        return existing_database(
            self.store, self.name, self.identity, write=write, schema_check=verify_service_schema
        )

    @staticmethod
    def _invoke(provider, *, code):
        try:
            return provider()
        except Exception:
            raise sql.AuthorityError(code) from None

    def _now(self):
        now = self._invoke(self.clock, code="EXPIRED_OBSERVATION")
        healthy = self._invoke(self.clock_health, code="EXPIRED_OBSERVATION")
        sql.require(
            healthy is True
            and type(now) is datetime
            and now.tzinfo is not None
            and now.utcoffset() == timedelta(0),
            "EXPIRED_OBSERVATION",
        )
        return now

    def _authorize(self, op, body, credentials):
        sql.require(
            credentials is not None
            and self._invoke(
                lambda: self.authorizer.verify(
                    operation=op, canonical_body=body, credentials=credentials
                ),
                code="TRUST_UNAPPROVED",
            )
            is None,
            "TRUST_UNAPPROVED",
        )

    def _pin(self, receipt):
        pin = next(
            (
                p
                for p in self.b3_pins
                if (p.record["key_id"], p.record["key_epoch"])
                == (receipt["key_id"], receipt["key_epoch"])
            ),
            None,
        )
        sql.require(pin is not None, "TRUST_UNAPPROVED")
        return pin

    def _read(self, con):
        meta = con.execute(
            "SELECT authority_id,authority_epoch,namespace,genesis_digest,"
            "genesis_binding FROM service_identity WHERE singleton=1"
        ).fetchone()
        sql.require(
            meta
            == (
                self.authority_id,
                self.authority_epoch,
                NAMESPACE,
                self.genesis_digest,
                sql.canonical_bytes(self.genesis_binding),
            ),
            "TRUST_UNAPPROVED",
        )
        rows = con.execute(
            "SELECT revision,operation_id,digest,canonical FROM history_receipts "
            "ORDER BY revision"
        ).fetchall()
        history = [sql.parse_canonical(row[3]) for row in rows]
        tip = b3.verify_chain(
            history,
            self.b3_pins,
            expected_genesis_digest=self.genesis_digest,
            expected_genesis_binding=self.genesis_binding,
        )
        for row, receipt in zip(rows, history, strict=True):
            sql.require(
                row[:3] == (receipt["revision"], receipt["operation_id"], sql.digest(receipt)),
                "FORK_DETECTED",
            )
        sql.require(tip == sql.digest(history[-1]), "FORK_DETECTED")
        intents = {}
        for rid, digest, request, nonce, run, raw in con.execute(
            "SELECT reservation_id,reservation_digest,request_id,nonce,run_id,canonical "
            "FROM reservation_intents"
        ):
            record = b4.qualify_reservation(raw).to_dict()
            sql.require(
                (rid, digest, request, nonce, run)
                == (
                    record["reservation_id"],
                    record["reservation_digest"],
                    record["request_id"],
                    record["nonce"],
                    record["run_id"],
                ),
                "FORK_DETECTED",
            )
            intents[rid] = record
        reservation_rows = con.execute(
            "SELECT sequence,reservation_id,transition,operation_id,"
            "digest,canonical FROM reservation_receipts ORDER BY sequence"
        ).fetchall()
        receipts, active, token, previous = [], None, 0, None
        latest = {}
        for i, row in enumerate(reservation_rows, 1):
            receipt = b4.parse_reservation_receipt_bytes(row[5]).to_dict()
            expected = {k: v for k, v in receipt.items() if k not in ("signature", "issued_at")}
            receipt_pin = next(
                (
                    p
                    for p in self.b4_pins
                    if (p.key_id, p.key_epoch) == (receipt["key_id"], receipt["key_epoch"])
                ),
                None,
            )
            sql.require(receipt_pin is not None, "TRUST_UNAPPROVED")
            b4.verify_reservation_receipt(
                row[5],
                expected_context=b4.ReceiptContext(sql.canonical_bytes(expected), self._now()),
                witness_pin=receipt_pin,
            )
            sql.require(
                row[:5]
                == (
                    i,
                    receipt["reservation_id"],
                    receipt["transition"],
                    receipt["operation_id"],
                    sql.digest(receipt),
                )
                and receipt["sequence"] == i
                and receipt["previous_receipt_digest"] == previous,
                "FORK_DETECTED",
            )
            record = intents.get(receipt["reservation_id"])
            sql.require(
                record is not None
                and receipt["reservation_digest"] == record["reservation_digest"]
                and receipt["fencing_token"] == record["fencing_token"]
                and (receipt["base_authority_revision"], receipt["base_authority_witness_digest"])
                == (record["previous_authority_revision"], record["previous_witness_digest"]),
                "FORK_DETECTED",
            )
            if receipt["transition"] == "RESERVE":
                sql.require(
                    active is None
                    and receipt["fencing_token"] == token + 1
                    and receipt["reservation_id"] not in latest,
                    "FORK_DETECTED",
                )
                active, token = receipt["reservation_id"], receipt["fencing_token"]
            else:
                prior = latest.get(receipt["reservation_id"])
                sql.require(
                    prior is not None and active == receipt["reservation_id"], "FORK_DETECTED"
                )
                self._check_transition(prior, receipt, history)
                if receipt["transition"] in ("RETIRE_TERMINAL", "CANCEL_MANUAL"):
                    active = None
            latest[receipt["reservation_id"]] = receipt
            previous = sql.digest(receipt)
            receipts.append(receipt)
        sql.require(set(intents) == set(latest), "UNCERTAIN_PUBLICATION")
        return {
            "history": history,
            "receipts": receipts,
            "intents": intents,
            "latest": latest,
            "active": active,
            "token": token,
            "sequence": len(receipts),
            "digest": previous,
        }

    @staticmethod
    def _check_transition(prior, receipt, history):
        transition = receipt["transition"]
        if transition == "COMMIT_OPEN":
            sql.require(prior["state"] == "PENDING", "UNCERTAIN_PUBLICATION")
        elif transition == "RECORD_UNCERTAIN":
            sql.require(prior["state"] in ("PENDING", "COMMITTED"), "UNCERTAIN_PUBLICATION")
            sql.require(
                receipt["open_witness_digest"] == prior["open_witness_digest"], "REFERENCE_MISMATCH"
            )
        elif transition in ("CANCEL_MANUAL", "ADOPT_OPEN_MANUAL"):
            sql.require(prior["state"] == "UNCERTAIN", "UNCERTAIN_PUBLICATION")
        elif transition == "RETIRE_TERMINAL":
            sql.require(
                prior["state"] == "COMMITTED"
                and receipt["open_witness_digest"] == prior["open_witness_digest"],
                "UNCERTAIN_PUBLICATION",
            )
        else:
            raise sql.AuthorityError("SCHEMA")
        if transition in ("COMMIT_OPEN", "ADOPT_OPEN_MANUAL"):
            matches = [r for r in history if sql.digest(r) == receipt["open_witness_digest"]]
            sql.require(
                len(matches) == 1
                and matches[0]["revision"] == receipt["base_authority_revision"] + 1
                and matches[0]["previous_witness_digest"]
                == receipt["base_authority_witness_digest"],
                "REFERENCE_MISMATCH",
            )
        if transition == "RETIRE_TERMINAL":
            matches = [r for r in history if sql.digest(r) == receipt["terminal_witness_digest"]]
            opened = next(r for r in history if sql.digest(r) == receipt["open_witness_digest"])
            sql.require(
                len(matches) == 1 and matches[0]["revision"] > opened["revision"],
                "REFERENCE_MISMATCH",
            )
        if transition == "CANCEL_MANUAL":
            sql.require(
                prior["open_witness_digest"] is None and receipt["open_witness_digest"] is None,
                "UNCERTAIN_PUBLICATION",
            )

    def publish_b3(self, payload, expected_previous, *, credentials):
        fields = b3.build_witness_payload(payload)
        body = sql.canonical_bytes(fields)
        self._authorize("B3_CAS", body, credentials)
        self._now()
        with self._database(write=True) as con:
            con.execute("BEGIN IMMEDIATE")
            state = self._read(con)
            prior = state["history"][-1]
            sql.require(
                fields["revision"] == prior["revision"] + 1 <= sql.MAX_COUNTER
                and fields["previous_witness_digest"] == expected_previous == sql.digest(prior),
                "REVISION_STALE",
            )
            sql.require(
                not any(r["operation_id"] == fields["operation_id"] for r in state["history"]),
                "REPLAY_CONFLICT",
            )
            receipt = self._invoke(
                lambda: b3.sign_witness(fields, self.b3_signer), code="UNCERTAIN_PUBLICATION"
            )
            b3.verify_chain(
                [*state["history"], receipt],
                self.b3_pins,
                expected_genesis_digest=self.genesis_digest,
                expected_genesis_binding=self.genesis_binding,
            )
            sql.require(
                self._pin(receipt).canonical_record == self.b3_pin.canonical_record,
                "TRUST_UNAPPROVED",
            )
            con.execute(
                "INSERT INTO history_receipts VALUES (?,?,?,?)",
                (
                    receipt["revision"],
                    receipt["operation_id"],
                    sql.digest(receipt),
                    sql.canonical_bytes(receipt),
                ),
            )
            con.commit()
        return receipt

    def head_b3(self, challenge, *, credentials):
        sql.validate_scalar(challenge, {"type": "sha256", "not_null": True})
        self._authorize("B3_HEAD", sql.canonical_bytes({"challenge": challenge}), credentials)
        now = self._now()
        with self._database() as con:
            con.execute("BEGIN")
            tip = self._read(con)["history"][-1]
        tip_pin = self._pin(tip)
        signer = self._head_signers.get(tip_pin.canonical_record)
        sql.require(signer is not None, "TRUST_UNAPPROVED")
        pin = tip_pin.record
        fields = {
            "witness_schema_version": b3.HEAD_SCHEMA,
            "authority_id": self.authority_id,
            "authority_epoch": self.authority_epoch,
            "namespace": NAMESPACE,
            "challenge": challenge,
            "revision": tip["revision"],
            "ledger_digest": tip["ledger_digest"],
            "witness_digest": sql.digest(tip),
            "issued_at": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "expires_at": (now + timedelta(seconds=240)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "key_id": pin["key_id"],
            "key_epoch": pin["key_epoch"],
            "key_fingerprint": pin["fingerprint"],
        }
        result = self._invoke(
            lambda: b3._sign_current_head(fields, signer), code="WITNESS_UNAVAILABLE"
        )
        b3._verify_current_head(
            result,
            tip_pin,
            challenge=challenge,
            now=fields["issued_at"],
            request_elapsed_seconds=0,
            clock_healthy=True,
        )
        return result

    @staticmethod
    def _validate_cas(sequence, digest):
        sql.validate_scalar(sequence, {"type": "int64_nonnegative", "not_null": True})
        sql.require((sequence == 0) == (digest is None), "SCHEMA")
        if digest is not None:
            sql.validate_scalar(digest, {"type": "sha256", "not_null": True})

    def reserve(self, raw_record, *, expected_sequence, expected_receipt_digest, credentials):
        self._validate_cas(expected_sequence, expected_receipt_digest)
        record = b4.qualify_reservation(raw_record).to_dict()
        self._authorize("B4_RESERVE", raw_record, credentials)
        now = self._now()
        sql.require(
            record["record_sequence"] == 1
            and record["state"] == "PENDING"
            and all(
                record[k] is None
                for k in (
                    "reservation_receipt_reference",
                    "open_witness_reference",
                    "evidence_receipt_reference",
                    "manual_resolution_reference",
                )
            )
            and not record["uncertainty_codes"]
            and sql.utc_time(record["requested_at"]).replace(tzinfo=UTC)
            <= now + timedelta(seconds=5)
            and now < sql.utc_time(record["freshness_deadline"]).replace(tzinfo=UTC),
            "SCHEMA",
        )
        with self._database(write=True) as con:
            con.execute("BEGIN IMMEDIATE")
            state = self._read(con)
            sql.require(
                (state["sequence"], state["digest"])
                == (expected_sequence, expected_receipt_digest),
                "REVISION_STALE",
            )
            sql.require(state["active"] is None, "RESERVATION_BUSY")
            sql.require(
                state["token"] < sql.MAX_COUNTER and record["fencing_token"] == state["token"] + 1,
                "FENCING_TOKEN_INVALID",
            )
            prior = state["history"][-1]
            sql.require(
                (
                    record["namespace"],
                    record["authority_id"],
                    record["authority_epoch"],
                    record["previous_authority_revision"],
                    record["previous_ledger_digest"],
                    record["previous_witness_digest"],
                )
                == (
                    NAMESPACE,
                    self.authority_id,
                    self.authority_epoch,
                    prior["revision"],
                    prior["ledger_digest"],
                    sql.digest(prior),
                ),
                "REVISION_STALE",
            )
            sql.require(
                record["reservation_id"] not in state["intents"]
                and not any(
                    record[k] == r[k]
                    for r in state["intents"].values()
                    for k in ("request_id", "nonce", "run_id")
                ),
                "REPLAY_CONFLICT",
            )
            receipt = self._make_receipt(
                record, state, "RESERVE", record["request_id"], "PENDING", None, None, now
            )
            con.execute(
                "INSERT INTO reservation_intents VALUES (?,?,?,?,?,?)",
                (
                    record["reservation_id"],
                    record["reservation_digest"],
                    record["request_id"],
                    record["nonce"],
                    record["run_id"],
                    raw_record,
                ),
            )
            self._insert_receipt(con, receipt)
            con.commit()
        return receipt

    def _make_receipt(
        self,
        record,
        state,
        transition,
        operation_id,
        result_state,
        open_digest,
        terminal_digest,
        now,
    ):
        sql.require(state["sequence"] < sql.MAX_COUNTER, "REVISION_OVERFLOW")
        fields = {
            "schema_version": "s11-ex10-b4-reservation-receipt-v1",
            "namespace": NAMESPACE,
            "authority_id": self.authority_id,
            "authority_epoch": self.authority_epoch,
            "reservation_id": record["reservation_id"],
            "reservation_digest": record["reservation_digest"],
            "state": result_state,
            "fencing_token": record["fencing_token"],
            "sequence": state["sequence"] + 1,
            "previous_receipt_digest": state["digest"],
            "base_authority_revision": record["previous_authority_revision"],
            "base_authority_witness_digest": record["previous_witness_digest"],
            "open_witness_digest": open_digest,
            "terminal_witness_digest": terminal_digest,
            "transition": transition,
            "operation_id": operation_id,
            "issued_at": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "key_id": self.b4_pin.key_id,
            "key_epoch": self.b4_pin.key_epoch,
            "signature": "0" * 128,
        }
        parsed = b4.parse_reservation_receipt_bytes(sql.canonical_bytes(fields))
        fields["signature"] = self._invoke(
            lambda: self.b4_signer.sign(b4.signing_preimage(parsed)).hex(),
            code="UNCERTAIN_PUBLICATION",
        )
        expected = {k: v for k, v in fields.items() if k not in ("signature", "issued_at")}
        b4.verify_reservation_receipt(
            sql.canonical_bytes(fields),
            expected_context=b4.ReceiptContext(sql.canonical_bytes(expected), now),
            witness_pin=self.b4_pin,
        )
        return fields

    @staticmethod
    def _insert_receipt(con, receipt):
        con.execute(
            "INSERT INTO reservation_receipts VALUES (?,?,?,?,?,?)",
            (
                receipt["sequence"],
                receipt["reservation_id"],
                receipt["transition"],
                receipt["operation_id"],
                sql.digest(receipt),
                sql.canonical_bytes(receipt),
            ),
        )

    def transition(
        self,
        *,
        reservation_id,
        transition,
        operation_id,
        expected_sequence,
        expected_receipt_digest,
        open_witness_digest,
        terminal_witness_digest,
        credentials,
        recovery_approval=None,
    ):
        self._validate_cas(expected_sequence, expected_receipt_digest)
        sql.validate_scalar(reservation_id, {"type": "uuid", "not_null": True})
        sql.validate_scalar(operation_id, {"type": "uuid", "not_null": True})
        sql.require(
            transition
            in (
                "COMMIT_OPEN",
                "RECORD_UNCERTAIN",
                "CANCEL_MANUAL",
                "RETIRE_TERMINAL",
                "ADOPT_OPEN_MANUAL",
            ),
            "SCHEMA",
        )
        body = sql.canonical_bytes(
            {
                "reservation_id": reservation_id,
                "transition": transition,
                "operation_id": operation_id,
                "sequence": expected_sequence,
                "receipt_digest": expected_receipt_digest,
                "open_witness_digest": open_witness_digest,
                "terminal_witness_digest": terminal_witness_digest,
            }
        )
        self._authorize("B4_" + transition, body, credentials)
        now = self._now()
        with self._database(write=True) as con:
            con.execute("BEGIN IMMEDIATE")
            state = self._read(con)
            sql.require(
                (state["sequence"], state["digest"])
                == (expected_sequence, expected_receipt_digest),
                "REVISION_STALE",
            )
            sql.require(state["active"] == reservation_id, "RESERVATION_BUSY")
            sql.require(
                not any(
                    r["transition"] == transition and r["operation_id"] == operation_id
                    for r in state["receipts"]
                ),
                "REPLAY_CONFLICT",
            )
            result_state = {
                "COMMIT_OPEN": "COMMITTED",
                "RECORD_UNCERTAIN": "UNCERTAIN",
                "CANCEL_MANUAL": "CANCELLED_MANUALLY",
                "RETIRE_TERMINAL": "COMMITTED",
                "ADOPT_OPEN_MANUAL": "COMMITTED",
            }[transition]
            record = state["intents"][reservation_id]
            receipt = self._make_receipt(
                record,
                state,
                transition,
                operation_id,
                result_state,
                open_witness_digest,
                terminal_witness_digest,
                now,
            )
            self._check_transition(state["latest"][reservation_id], receipt, state["history"])
            if transition in ("CANCEL_MANUAL", "ADOPT_OPEN_MANUAL"):
                sql.require(
                    type(recovery_approval) is RecoveryApproval
                    and self.recovery_authorizer is not None,
                    "TRUST_UNAPPROVED",
                )
                sql.require(
                    (
                        recovery_approval.operation_id,
                        recovery_approval.reservation_id,
                        recovery_approval.prior_receipt_digest,
                    )
                    == (operation_id, reservation_id, expected_receipt_digest),
                    "REFERENCE_MISMATCH",
                )
                for reference in (
                    recovery_approval.reference,
                    recovery_approval.writer_closure_reference,
                ):
                    b3._validate_reference(sql.parse_canonical(reference))
                if transition == "CANCEL_MANUAL":
                    sql.require(
                        state["history"][-1]["revision"] == record["previous_authority_revision"],
                        "UNCERTAIN_PUBLICATION",
                    )
                    sql.require(recovery_approval.no_open_reference is not None, "TRUST_UNAPPROVED")
                    b3._validate_reference(sql.parse_canonical(recovery_approval.no_open_reference))
                sql.require(
                    self._invoke(
                        lambda: self.recovery_authorizer.verify(
                            transition=transition,
                            approval=recovery_approval,
                            canonical_receipt=sql.canonical_bytes(receipt),
                            credentials=credentials,
                        ),
                        code="TRUST_UNAPPROVED",
                    )
                    is None,
                    "TRUST_UNAPPROVED",
                )
            self._insert_receipt(con, receipt)
            con.commit()
        return receipt

    def head_b4(self, challenge, *, credentials):
        sql.validate_scalar(challenge, {"type": "sha256", "not_null": True})
        self._authorize("B4_HEAD", sql.canonical_bytes({"challenge": challenge}), credentials)
        now = self._now()
        with self._database() as con:
            con.execute("BEGIN")
            state = self._read(con)
        fields = {
            "schema_version": "s11-ex10-b4-coordination-head-v1",
            "namespace": NAMESPACE,
            "authority_id": self.authority_id,
            "authority_epoch": self.authority_epoch,
            "sequence": state["sequence"],
            "receipt_digest": state["digest"],
            "active_reservation_id": state["active"],
            "fencing_token": state["token"],
            "challenge": challenge,
            "issued_at": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "expires_at": (now + timedelta(seconds=240)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "key_id": self.b4_pin.key_id,
            "key_epoch": self.b4_pin.key_epoch,
            "signature": "0" * 128,
        }
        parsed = b4.parse_coordination_head_bytes(sql.canonical_bytes(fields))
        fields["signature"] = self._invoke(
            lambda: self.b4_signer.sign(b4.signing_preimage(parsed)).hex(),
            code="UNCERTAIN_PUBLICATION",
        )
        expected = {
            k: v
            for k, v in fields.items()
            if k not in ("signature", "issued_at", "expires_at", "sequence", "fencing_token")
        }
        b4.verify_coordination_head(
            sql.canonical_bytes(fields),
            expected_context=b4.CoordinationHeadContext(
                sql.canonical_bytes(expected), state["sequence"], state["token"], now
            ),
            witness_pin=self.b4_pin,
        )
        return fields

    def inspect_b3_operation(self, operation_id, *, credentials):
        sql.validate_scalar(operation_id, {"type": "uuid", "not_null": True})
        self._authorize(
            "B3_INSPECT", sql.canonical_bytes({"operation_id": operation_id}), credentials
        )
        with self._database() as con:
            con.execute("BEGIN")
            state = self._read(con)
        return next(
            (copy.deepcopy(r) for r in state["history"] if r["operation_id"] == operation_id), None
        )
