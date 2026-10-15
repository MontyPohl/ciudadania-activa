"""Authenticated witness channels; external disposable stores, memory-only test keys."""

from __future__ import annotations

import hashlib
import importlib.util
import multiprocessing
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "w04_backend_test_" + __name__.replace(".", "_"),
    Path(__file__).with_name("test_ex10_authority_backend.py"),
)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
apparatus = h.apparatus
service, backend, sql, witness, wire = h.service, h.backend, h.sql, h.witness, h.wire
spec = importlib.util.spec_from_file_location(
    "w04_b4_builder_" + __name__.replace(".", "_"),
    Path(__file__).with_name("test_ex10_bridge_protocol.py"),
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def record(a, *, label="first", token=1):
    value = builder.reservation_value()
    now = datetime.now(UTC)
    identity = builder.identity()
    identity["execution_nonce"] = hashlib.sha256(label.encode()).hexdigest()
    identity["started_at"] = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    identity["git_sha"] = "1" * 40
    identity["run_id"] = wire.make_run_id(
        started_at=now,
        git_sha=identity["git_sha"],
        dataset_id=identity["dataset_id"],
        scenario_id=identity["scenario_id"],
        seed=identity["seed"],
        execution_nonce=identity["execution_nonce"],
    )
    identity.pop("identity_digest")
    identity["identity_digest"] = sql.digest(identity)
    value.update(
        namespace="S11C3-EX10",
        authority_id=h.b.AUTH_ID,
        authority_epoch=1,
        reservation_id=str(uuid.uuid5(uuid.NAMESPACE_URL, label + "-reservation")),
        request_id=str(uuid.uuid5(uuid.NAMESPACE_URL, label + "-request")),
        nonce=identity["execution_nonce"],
        run_identity=identity,
        run_id=identity["run_id"],
        source_revision=identity["git_sha"],
        record_sequence=1,
        previous_reservation_record_digest=None,
        previous_authority_revision=0,
        expected_next_revision=1,
        previous_ledger_digest=a.f.receipt["ledger_digest"],
        previous_witness_digest=sql.digest(a.f.receipt),
        fencing_token=token,
        requested_at=now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        updated_at=now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        freshness_deadline=(now + timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        reservation_receipt_reference=None,
        open_witness_reference=None,
        evidence_receipt_reference=None,
        manual_resolution_reference=None,
        terminal_witness_reference=None,
        uncertainty_codes=[],
    )
    return reseal(value)


def reseal(value):
    value["reservation_digest"] = sql.digest({k: value[k] for k in wire.RESERVATION_INTENT_FIELDS})
    value["record_digest"] = sql.digest({k: v for k, v in value.items() if k != "record_digest"})
    raw = sql.canonical_bytes(value)
    wire.qualify_reservation(raw)
    return raw


def reserve(a, raw=None):
    return a.remote.reserve(
        record(a) if raw is None else raw,
        expected_sequence=0,
        expected_receipt_digest=None,
        credentials=a.auth.credential,
    )


def transition(a, prior, kind="RECORD_UNCERTAIN", *, approval=None, opened=None, terminal=None):
    return a.remote.transition(
        reservation_id=prior["reservation_id"],
        transition=kind,
        operation_id=str(uuid.uuid5(uuid.NAMESPACE_URL, kind + "-op")),
        expected_sequence=prior["sequence"],
        expected_receipt_digest=sql.digest(prior),
        open_witness_digest=opened,
        terminal_witness_digest=terminal,
        credentials=a.auth.credential,
        recovery_approval=approval,
    )


def test_signed_reservation_and_head_survive_service_restart(apparatus):
    a = apparatus
    receipt = reserve(a)
    assert receipt["state"] == "PENDING" and receipt["sequence"] == 1
    assert receipt["transition"] == "RESERVE" and receipt["fencing_token"] == 1
    restarted = service.WitnessService(**a.remote_options)
    head = restarted.head_b4("9" * 64, credentials=a.auth.credential)
    assert head["sequence"] == 1 and head["active_reservation_id"] == receipt["reservation_id"]
    expected = {
        k: v
        for k, v in head.items()
        if k not in ("signature", "issued_at", "expires_at", "sequence", "fencing_token")
    }
    accepted = wire.verify_coordination_head(
        sql.canonical_bytes(head),
        expected_context=wire.CoordinationHeadContext(
            sql.canonical_bytes(expected), 1, 1, datetime.now(UTC)
        ),
        witness_pin=a.b4_pin,
    )
    assert accepted.to_dict()["receipt_digest"] == sql.digest(receipt)


@pytest.mark.parametrize("change", ["other_root", "other_project", "other_session", "same_request"])
def test_independent_slot_blocks_second_attempt_in_every_context(apparatus, change):
    a = apparatus
    first = reserve(a)
    value = sql.parse_canonical(record(a, label="second", token=2))
    if change == "other_project":
        value["project"] = "other-approved-project"
    elif change == "other_root":
        value["host_root_binding_sha256"] = "b" * 64
    elif change == "other_session":
        value["session_id"] = str(uuid.uuid4())
    else:
        value["request_id"] = first["operation_id"]
    with pytest.raises(sql.AuthorityError, match="RESERVATION_BUSY"):
        a.remote.reserve(
            reseal(value),
            expected_sequence=1,
            expected_receipt_digest=sql.digest(first),
            credentials=a.auth.credential,
        )
    head = a.remote.head_b4("a" * 64, credentials=a.auth.credential)
    assert head["sequence"] == 1 and head["fencing_token"] == 1


def test_expired_or_dead_owner_never_releases_pending(apparatus):
    a = apparatus
    receipt = reserve(a)
    a.remote.clock = lambda: datetime.now(UTC) + timedelta(days=7)
    head = a.remote.head_b4("a" * 64, credentials=a.auth.credential)
    assert head["active_reservation_id"] == receipt["reservation_id"]
    raw = record(a, label="new", token=2)
    # A fresh second proposal from another process/context still cannot free the existing grant.
    a.remote.clock = lambda: datetime.now(UTC)
    with pytest.raises(sql.AuthorityError, match="RESERVATION_BUSY"):
        a.remote.reserve(
            raw,
            expected_sequence=1,
            expected_receipt_digest=sql.digest(receipt),
            credentials=a.auth.credential,
        )


def test_b3_response_loss_inspection_never_republishes(apparatus):
    a = apparatus
    a.transport.fail = "response"
    with pytest.raises(TimeoutError):
        a.transport.publish_b3(h.next_payload(a), sql.digest(a.f.receipt))
    receipt = a.remote.inspect_b3_operation(h.b.OP_NEXT, credentials=a.auth.credential)
    assert receipt["revision"] == 1
    before = a.remote.store.read(a.remote.store.root + "/" + a.remote.name)
    assert a.remote.inspect_b3_operation(h.b.OP_NEXT, credentials=a.auth.credential) == receipt
    assert a.remote.store.read(a.remote.store.root + "/" + a.remote.name) == before
    with pytest.raises(sql.AuthorityError, match="REVISION_STALE"):
        a.remote.publish_b3(
            h.next_payload(a), sql.digest(a.f.receipt), credentials=a.auth.credential
        )
    assert a.transport.calls.count("B3_CAS") == 1


def test_commit_open_is_distinct_cas_binding_existing_b3_receipt(apparatus):
    a = apparatus
    first = reserve(a)
    opened = a.remote.publish_b3(
        h.next_payload(a), sql.digest(a.f.receipt), credentials=a.auth.credential
    )
    committed = transition(a, first, "COMMIT_OPEN", opened=sql.digest(opened))
    assert committed["state"] == "COMMITTED" and committed["sequence"] == 2
    assert committed["open_witness_digest"] == sql.digest(opened)
    head = a.remote.head_b4("9" * 64, credentials=a.auth.credential)
    assert head["active_reservation_id"] == first["reservation_id"]
    assert a.auth.calls.count("B4_RESERVE") == 1
    assert a.auth.calls.count("B3_CAS") == 1
    assert a.auth.calls.count("B4_COMMIT_OPEN") == 1
    with pytest.raises(sql.AuthorityError, match="REVISION_STALE"):
        transition(a, first, "COMMIT_OPEN", opened=sql.digest(opened))


@pytest.mark.parametrize("digest", ["unknown", "genesis"])
def test_commit_without_exact_existing_open_receipt_rejected(apparatus, digest):
    a = apparatus
    first = reserve(a)
    wrong = "8" * 64 if digest == "unknown" else sql.digest(a.f.receipt)
    with pytest.raises(sql.AuthorityError, match="REFERENCE_MISMATCH"):
        transition(a, first, "COMMIT_OPEN", opened=wrong)
    assert a.remote.head_b4("9" * 64, credentials=a.auth.credential)["sequence"] == 1


def test_record_uncertain_keeps_slot_and_cannot_become_normal_commit(apparatus):
    a = apparatus
    first = reserve(a)
    unknown = transition(a, first)
    assert unknown["state"] == "UNCERTAIN"
    a.remote.publish_b3(h.next_payload(a), sql.digest(a.f.receipt), credentials=a.auth.credential)
    opened = a.remote.inspect_b3_operation(h.b.OP_NEXT, credentials=a.auth.credential)
    with pytest.raises(sql.AuthorityError, match="UNCERTAIN_PUBLICATION"):
        transition(a, unknown, "COMMIT_OPEN", opened=sql.digest(opened))
    assert a.remote.head_b4("9" * 64, credentials=a.auth.credential)["active_reservation_id"]


def approval(a, prior, kind):
    ref = sql.canonical_bytes(h.b.reference("test-only-manual-approval"))
    return service.RecoveryApproval(
        str(uuid.uuid5(uuid.NAMESPACE_URL, kind + "-op")),
        prior["reservation_id"],
        sql.digest(prior),
        ref,
        ref,
        ref,
    )


def test_cancel_requires_admin_writer_closure_and_authenticated_no_open(apparatus):
    a = apparatus
    unknown = transition(a, reserve(a))
    for fake in (None, True, {"no_open": True}):
        with pytest.raises(sql.AuthorityError, match="TRUST_UNAPPROVED"):
            transition(a, unknown, "CANCEL_MANUAL", approval=fake)
    cancelled = transition(
        a, unknown, "CANCEL_MANUAL", approval=approval(a, unknown, "CANCEL_MANUAL")
    )
    assert cancelled["state"] == "CANCELLED_MANUALLY"
    head = a.remote.head_b4("9" * 64, credentials=a.auth.credential)
    assert head["active_reservation_id"] is None and head["fencing_token"] == 1
    second = a.remote.reserve(
        record(a, label="second", token=2),
        expected_sequence=3,
        expected_receipt_digest=sql.digest(cancelled),
        credentials=a.auth.credential,
    )
    assert second["fencing_token"] == 2
    with a.remote._database() as con:
        assert con.execute("SELECT count(*) FROM reservation_intents").fetchone() == (2,)
        assert con.execute("SELECT count(*) FROM reservation_receipts").fetchone() == (4,)


def test_false_absence_after_remote_open_cannot_cancel(apparatus):
    a = apparatus
    unknown = transition(a, reserve(a))
    a.remote.publish_b3(h.next_payload(a), sql.digest(a.f.receipt), credentials=a.auth.credential)
    with pytest.raises(sql.AuthorityError, match="UNCERTAIN_PUBLICATION"):
        transition(a, unknown, "CANCEL_MANUAL", approval=approval(a, unknown, "CANCEL_MANUAL"))
    assert a.remote.head_b4("9" * 64, credentials=a.auth.credential)["active_reservation_id"]


def test_adopt_existing_open_does_not_repeat_b3_cas(apparatus):
    a = apparatus
    unknown = transition(a, reserve(a))
    opened = a.remote.publish_b3(
        h.next_payload(a), sql.digest(a.f.receipt), credentials=a.auth.credential
    )
    adopted = transition(
        a,
        unknown,
        "ADOPT_OPEN_MANUAL",
        opened=sql.digest(opened),
        approval=approval(a, unknown, "ADOPT_OPEN_MANUAL"),
    )
    assert adopted["state"] == "COMMITTED"
    assert a.auth.calls.count("B3_CAS") == 1
    assert a.admin.calls == ["ADOPT_OPEN_MANUAL"]


@pytest.mark.parametrize("operation", ["head_b3", "head_b4", "reserve", "publish_b3"])
def test_unapproved_peer_cannot_read_sign_or_mutate(apparatus, operation):
    a = apparatus
    before = a.remote.store.read(a.remote.store.root + "/" + a.remote.name)
    with pytest.raises(sql.AuthorityError, match="TRUST_UNAPPROVED"):
        if operation == "reserve":
            a.remote.reserve(
                record(a), expected_sequence=0, expected_receipt_digest=None, credentials=object()
            )
        elif operation == "publish_b3":
            a.remote.publish_b3(h.next_payload(a), sql.digest(a.f.receipt), credentials=object())
        else:
            getattr(a.remote, operation)("a" * 64, credentials=object())
    assert a.remote.store.read(a.remote.store.root + "/" + a.remote.name) == before


def test_clock_unhealthy_blocks_signing_and_admission(apparatus):
    a = apparatus
    a.remote.clock_health = lambda: False
    with pytest.raises(sql.AuthorityError, match="EXPIRED_OBSERVATION"):
        reserve(a)
    with pytest.raises(sql.AuthorityError, match="EXPIRED_OBSERVATION"):
        a.remote.head_b3("a" * 64, credentials=a.auth.credential)


def test_service_no_executor_genesis_reset_or_key_creation_interface(apparatus):
    a = apparatus
    for name in ("genesis", "reset", "create_key", "set_pin", "delete_claims"):
        assert not hasattr(a.remote, name)
    with pytest.raises(sql.AuthorityError, match="TRUST_UNAPPROVED"):
        service._provision_witness_database(
            store=a.stores["independent-witness"],
            name="other.sqlite3",
            receipts=[a.f.receipt],
            trust_pins=[a.f.pin],
            expected_genesis_digest=sql.digest(a.f.receipt),
            expected_genesis_binding=a.f.receipt["genesis"],
            authorizer=a.auth,
            credentials=object(),
        )
    assert not (a.root / "independent-witness" / "other.sqlite3").exists()


def test_append_only_and_schema_tampering_rejected(apparatus):
    a = apparatus
    with a.remote._database(write=True) as con:
        for query in (
            "DELETE FROM history_receipts",
            "UPDATE history_receipts SET digest=digest",
            "INSERT OR REPLACE INTO service_identity SELECT * FROM service_identity",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="APPEND_ONLY"):
                con.execute(query)
    path = a.root / "independent-witness" / "witness.sqlite3"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE injected(x)")
    con.commit()
    con.close()
    with pytest.raises(sql.AuthorityError, match="SCHEMA_TAMPERED"):
        a.remote.head_b4("9" * 64, credentials=a.auth.credential)


def test_multi_process_independent_witness_cas_admits_at_most_one(apparatus):
    a = apparatus
    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Event()
    pipes = [ctx.Pipe(duplex=False) for _ in range(2)]

    def worker(label, sender):
        try:
            remote = service.WitnessService(**a.remote_options)
            raw = record(a, label=label)
            barrier.wait(5)
            receipt = remote.reserve(
                raw,
                expected_sequence=0,
                expected_receipt_digest=None,
                credentials=a.auth.credential,
            )
            sender.send(("OK", receipt["reservation_id"]))
        except sql.AuthorityError as error:
            sender.send(("BLOCKED", str(error)))
        finally:
            sender.close()

    processes = [ctx.Process(target=worker, args=(str(i), pipes[i][1])) for i in range(2)]
    for p in processes:
        p.start()
    barrier.set()
    results = []
    for receiver, sender in pipes:
        sender.close()
        assert receiver.poll(15)
        results.append(receiver.recv())
        receiver.close()
    for p in processes:
        p.join(5)
        assert p.exitcode == 0
    assert sum(r[0] == "OK" for r in results) == 1
    assert a.remote.head_b4("9" * 64, credentials=a.auth.credential)["sequence"] == 1


@pytest.mark.parametrize("sequence", [False, 0.0, -1, sql.MAX_COUNTER + 1])
def test_expected_cas_sequence_requires_exact_safe_integer(apparatus, sequence):
    a = apparatus
    before = a.remote.store.read(a.remote.store.root + "/" + a.remote.name)
    with pytest.raises(sql.AuthorityError):
        a.remote.reserve(
            record(a),
            expected_sequence=sequence,
            expected_receipt_digest=None,
            credentials=a.auth.credential,
        )
    assert a.remote.store.read(a.remote.store.root + "/" + a.remote.name) == before
    assert a.remote.head_b4("9" * 64, credentials=a.auth.credential)["sequence"] == 0


def test_retire_requires_exact_later_history_receipt_and_releases_only_slot(apparatus):
    a = apparatus
    first = reserve(a)
    opened = a.remote.publish_b3(
        h.next_payload(a), sql.digest(a.f.receipt), credentials=a.auth.credential
    )
    committed = transition(a, first, "COMMIT_OPEN", opened=sql.digest(opened))
    for wrong in (None, sql.digest(a.f.receipt), sql.digest(opened), "b" * 64):
        with pytest.raises((sql.AuthorityError, wire.BridgeProtocolError)):
            transition(a, committed, "RETIRE_TERMINAL", opened=sql.digest(opened), terminal=wrong)
    fields = h.next_payload(a, operation=str(uuid.uuid4()))
    fields.update(revision=2, previous_witness_digest=sql.digest(opened), ledger_digest="c" * 64)
    terminal = a.remote.publish_b3(fields, sql.digest(opened), credentials=a.auth.credential)
    retired = transition(
        a, committed, "RETIRE_TERMINAL", opened=sql.digest(opened), terminal=sql.digest(terminal)
    )
    restarted = service.WitnessService(**a.remote_options)
    head = restarted.head_b4("9" * 64, credentials=a.auth.credential)
    assert head["active_reservation_id"] is None and head["fencing_token"] == 1
    assert head["sequence"] == 3 and head["receipt_digest"] == sql.digest(retired)
    # Claims survive retirement; a replay is not a fresh start even when slot is free.
    with pytest.raises(sql.AuthorityError):
        a.remote.reserve(
            record(a),
            expected_sequence=3,
            expected_receipt_digest=sql.digest(retired),
            credentials=a.auth.credential,
        )


@pytest.mark.parametrize("field", ["sequence", "token"])
def test_safe_integer_overflow_does_not_reset_or_sign_receipt(apparatus, field):
    a = apparatus
    raw = record(a)
    value = sql.parse_canonical(raw)
    before = a.remote.store.read(a.remote.store.root + "/" + a.remote.name)
    # Boundary arithmetic exercised directly; constructing 2**53 persistent rows is unnecessary.
    if field == "sequence":
        state = {"sequence": sql.MAX_COUNTER, "digest": None}
        with pytest.raises(sql.AuthorityError, match="REVISION_OVERFLOW"):
            a.remote._make_receipt(
                value,
                state,
                "RESERVE",
                value["request_id"],
                "PENDING",
                None,
                None,
                datetime.now(UTC),
            )
    else:
        value["fencing_token"] = sql.MAX_COUNTER + 1
        with pytest.raises((sql.AuthorityError, wire.BridgeProtocolError)):
            reseal(value)
    assert a.remote.store.read(a.remote.store.root + "/" + a.remote.name) == before


def rotation(a):
    key = h.Ed25519PrivateKey.generate()
    raw = key.public_key().public_bytes_raw()
    fields = a.f.pin.record
    fields.update(
        key_id="test-approved-rotation",
        key_epoch=2,
        public_key_hex=raw.hex(),
        fingerprint=hashlib.sha256(raw).hexdigest(),
        activation_revision=1,
        key_transition_reference=h.b.reference("approved-pin-rotation"),
    )
    return witness.TrustPin(sql.canonical_bytes(fields)), key


def test_approved_rotation_preserves_previous_head_and_complete_history(apparatus):
    a = apparatus
    pin, key = rotation(a)
    options = {
        **a.remote_options,
        "b3_pins": [a.f.pin, pin],
        "b3_signer_pin": pin,
        "b3_signer": key,
        "b3_head_signers": ((a.f.pin, a.f.key),),
    }
    remote = service.WitnessService(**options)
    # Before the first new-epoch CAS, the observable tip still belongs to the old pin.
    head = remote.head_b3("a" * 64, credentials=a.auth.credential)
    assert head["revision"] == 0 and head["key_id"] == a.f.pin.record["key_id"]
    fields = h.next_payload(a)
    fields.update(
        key_id=pin.record["key_id"], key_epoch=2, key_fingerprint=pin.record["fingerprint"]
    )
    opened = remote.publish_b3(fields, sql.digest(a.f.receipt), credentials=a.auth.credential)
    assert witness.verify_chain(
        [a.f.receipt, opened],
        [a.f.pin, pin],
        expected_genesis_digest=sql.digest(a.f.receipt),
        expected_genesis_binding=a.f.receipt["genesis"],
    ) == sql.digest(opened)
    restarted = service.WitnessService(**options)
    head = restarted.head_b3("b" * 64, credentials=a.auth.credential)
    assert head["revision"] == 1 and head["key_id"] == pin.record["key_id"]
    assert (
        restarted.inspect_b3_operation(a.f.receipt["operation_id"], credentials=a.auth.credential)
        == a.f.receipt
    )


def test_rotation_missing_historical_signer_has_no_fallback_or_write(apparatus):
    a = apparatus
    pin, key = rotation(a)
    remote = service.WitnessService(
        **{**a.remote_options, "b3_pins": [a.f.pin, pin], "b3_signer_pin": pin, "b3_signer": key}
    )
    before = remote.store.read(remote.store.root + "/" + remote.name)
    with pytest.raises(sql.AuthorityError, match="TRUST_UNAPPROVED"):
        remote.head_b3("a" * 64, credentials=a.auth.credential)
    assert remote.store.read(remote.store.root + "/" + remote.name) == before


@pytest.mark.parametrize("provider", ["auth", "clock", "health", "b3_signer", "b4_signer"])
def test_injected_provider_failure_never_exposes_exception_details(apparatus, provider):
    a = apparatus
    secret = "sensitive provider material must not appear"

    class Broken:
        def verify(self, **kwargs):
            raise RuntimeError(secret)

        def sign(self, preimage):
            raise RuntimeError(secret)

        def __call__(self):
            raise RuntimeError(secret)

    before = a.remote.store.read(a.remote.store.root + "/" + a.remote.name)
    if provider == "auth":
        a.remote.authorizer = Broken()
    elif provider == "clock":
        a.remote.clock = Broken()
    elif provider == "health":
        a.remote.clock_health = Broken()
    elif provider == "b3_signer":
        a.remote.b3_signer = Broken()
    else:
        a.remote.b4_signer = Broken()
    with pytest.raises(sql.AuthorityError) as error:
        if provider == "b3_signer":
            a.remote.publish_b3(
                h.next_payload(a), sql.digest(a.f.receipt), credentials=a.auth.credential
            )
        else:
            reserve(a)
    assert secret not in str(error.value)
    assert a.remote.store.read(a.remote.store.root + "/" + a.remote.name) == before
