"""External disposable W05 journal with actual B3 verification and independent B4 CAS."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import multiprocessing
import os
import tempfile
import types
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

spec = importlib.util.spec_from_file_location(
    "w05_backend_fixture_" + __name__.replace(".", "_"),
    Path(__file__).with_name("test_ex10_authority_backend.py"),
)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
b = h.b
backend = h.backend
service = h.service
sql = h.sql
history = h.history
wire = h.wire
coord = h.load("ex10_start_coordination")
spec = importlib.util.spec_from_file_location(
    "w05_wire_builder_" + __name__.replace(".", "_"),
    Path(__file__).with_name("test_ex10_bridge_protocol.py"),
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ExactAckFixture(b.Fixture):
    def run(self, kind, run_id=b.RUN_ID):
        value = super().run(kind, run_id)
        if kind == "ND":
            value["run_id"] = "s11-" + str(uuid.uuid5(uuid.NAMESPACE_URL, run_id))
        return value


class RecoveryAdmin(h.Admin):
    def verify(self, *, transition, approval, canonical_receipt, credentials):
        receipt = sql.parse_canonical(canonical_receipt)
        sql.require(
            credentials is self.auth.credential
            and approval.writer_closure_reference is not None
            and receipt["operation_id"] == approval.operation_id
            and receipt["reservation_id"] == approval.reservation_id
            and receipt["previous_receipt_digest"] == approval.prior_receipt_digest,
            "TRUST_UNAPPROVED",
        )
        if transition == "CANCEL_MANUAL":
            sql.require(approval.no_open_reference is not None, "TRUST_UNAPPROVED")
        elif transition == "ADOPT_OPEN_MANUAL":
            sql.require(receipt["open_witness_digest"] is not None, "TRUST_UNAPPROVED")
        else:
            raise sql.AuthorityError("TRUST_UNAPPROVED")
        self.calls.append(transition)


class Transport:
    def __init__(self, remote, credential):
        self.remote, self.credential = remote, credential
        self.calls = []
        self.fail = None

    def head(self, challenge):
        self.calls.append("HEAD")
        if self.fail == "head":
            raise OSError("private witness endpoint")
        return self.remote.head_b4(challenge, credentials=self.credential)

    def reserve(self, record, *, expected_sequence, expected_digest):
        self.calls.append("RESERVE")
        if self.fail == "before":
            raise TimeoutError("private endpoint lost request")
        receipt = self.remote.reserve(
            record,
            expected_sequence=expected_sequence,
            expected_receipt_digest=expected_digest,
            credentials=self.credential,
        )
        if self.fail == "response":
            raise TimeoutError("private endpoint lost response")
        return receipt

    def transition(self, **kwargs):
        self.calls.append(kwargs["transition"])
        return self.remote.transition(credentials=self.credential, **kwargs)


class Sources:
    """Test-host fixed qualified input registry; request cannot supply callback/context."""

    def __init__(self, f, remote, credential):
        self.f, self.remote, self.credential = f, remote, credential
        self.approved = {}
        self.reads = 0
        self.before_read = None
        self.record_inputs = {}
        self.registry_binding = None
        self.registry_identities = None
        self.now = lambda: datetime.now(UTC)
        self.intent_reader = None
        self.recovery_approved = {}
        self.manual_events = {}
        self.uncertainty_inputs = set()

    def verify_uncertainty(self, *, authenticated_request, opening):
        sql.require(
            authenticated_request.request.sha256 in self.uncertainty_inputs, "TRUST_UNAPPROVED"
        )

    def recovery_context(self, raw, *, operation):
        context = self.recovery_approved.get(hashlib.sha256(raw).hexdigest())
        sql.require(context is not None and context.operation == operation, "TRUST_UNAPPROVED")
        return replace(context, now=self.now())

    def verify_manual_event(self, *, event, original_record):
        expected = self.manual_events.get(event["reservation_id"])
        sql.require(
            expected == event["manual_resolution_reference"]
            and event["reservation_digest"] == original_record["reservation_digest"],
            "TRUST_UNAPPROVED",
        )

    def verify_registry(self, *, canonical_binding, journal):
        sql.require(
            canonical_binding == self.registry_binding
            and self.registry_identities
            == (
                journal.store.identity,
                journal.identity,
                journal.lock_store.identity,
                journal.lock_identity,
            ),
            "TRUST_UNAPPROVED",
        )

    def request_context(self, raw, *, operation):
        item = self.approved.get(hashlib.sha256(raw).hexdigest())
        sql.require(item is not None and item.operation == operation, "TRUST_UNAPPROVED")
        return replace(item, now=self.now())

    def read_authority(self):
        self.reads += 1
        if self.before_read is not None:
            self.before_read(self.reads)
        f = self.f
        challenge = b.hashed("actual-w05-authority-" + str(self.reads))
        observation = {
            "head": self.remote.head_b3(challenge, credentials=self.credential),
            "challenge": challenge,
            "now": coord._utc(self.remote.clock()),
            "request_elapsed_seconds": 0,
            "clock_healthy": True,
        }
        trust = f.trust()
        if self.intent_reader is not None:
            trust["intent_payload_reader"] = self.intent_reader
        return f.verify(state=sql._load_database(f.database), trust=trust, observation=observation)

    def verify_reservation_context(self, raw, authenticated):
        value = wire.qualify_reservation(raw).to_dict()
        expected = self.record_inputs.get(authenticated.request.to_dict()["request_id"])
        sql.require(expected == value, "REFERENCE_MISMATCH")


@pytest.fixture
def rig(request):
    with tempfile.TemporaryDirectory(
        prefix="s11-b3-test-w05-", dir=os.environ["S11_B3_TEST_BASE"]
    ) as raw:
        root = Path(raw)
        root.chmod(0o700)
        local = root / "s11-b3-test-local"
        local.mkdir(mode=0o700)
        f = ExactAckFixture(local, getattr(request, "param", ("ND",)), create_db=True)
        stores = {}
        for name in ("witness", "coordination", "coordination-floor", "receipts", "lock"):
            p = root / name
            p.mkdir(mode=0o700)
            stores[name] = backend.PrivateStore(str(p), h.identity(p))
        lock = root / "lock/global.lock"
        lock.write_bytes(b"")
        lock.chmod(0o600)
        auth = h.Auth()
        admin = RecoveryAdmin(auth)
        key = Ed25519PrivateKey.generate()
        pin = wire.PublicKeyPin("w05-independent-witness", 1, key.public_key())
        identity = service._provision_witness_database(
            store=stores["witness"],
            name="witness.sqlite3",
            receipts=[f.receipt],
            trust_pins=[f.pin],
            expected_genesis_digest=sql.digest(f.receipt),
            expected_genesis_binding=f.receipt["genesis"],
            authorizer=auth,
            credentials=auth.credential,
        )
        remote = service.WitnessService(
            store=stores["witness"],
            name="witness.sqlite3",
            identity=identity,
            authority_id=b.AUTH_ID,
            authority_epoch=1,
            genesis_digest=sql.digest(f.receipt),
            genesis_binding=f.receipt["genesis"],
            b3_pins=[f.pin],
            b3_signer_pin=f.pin,
            b3_signer=f.key,
            b4_pin=pin,
            b4_pins=[pin],
            b4_signer=key,
            authorizer=auth,
            recovery_authorizer=admin,
            clock=lambda: datetime.now(UTC),
            clock_health=lambda: True,
        )
        head = remote.head_b4("0" * 64, credentials=auth.credential)
        floor_store = stores["coordination-floor"]
        floor_store.create_exact(
            floor_store.root + "/head-00000000000000000000.json", sql.canonical_bytes(head)
        )
        floor = coord.CoordinationFloor(
            floor_store, authority_id=b.AUTH_ID, authority_epoch=1, witness_pins=(pin,)
        )
        bridge = Ed25519PrivateKey.generate()
        holder = Ed25519PrivateKey.generate()
        bridge_pin = wire.PublicKeyPin("test-host-bridge", 1, bridge.public_key())
        sources = Sources(f, remote, auth.credential)
        transport = Transport(remote, auth.credential)
        binding = sql.canonical_bytes(
            {
                "namespace": "S11C3-EX10",
                "authority_id": b.AUTH_ID,
                "authority_epoch": 1,
                "lock_inode": lock.stat().st_ino,
                "coordination_inode": Path(stores["coordination"].root).stat().st_ino,
            }
        )
        db_identity = coord._provision_coordination_database(
            store=stores["coordination"],
            name="coord.sqlite3",
            registry_binding=binding,
            authority_id=b.AUTH_ID,
            authority_epoch=1,
            authorizer=auth,
            credentials=auth.credential,
        )
        options = dict(
            store=stores["coordination"],
            name="coord.sqlite3",
            identity=db_identity,
            registry_binding=binding,
            authority_id=b.AUTH_ID,
            authority_epoch=1,
            receipt_store=stores["receipts"],
            floor=floor,
            transport=transport,
            bridge_pin=bridge_pin,
            host_sources=sources,
            clock=lambda: datetime.now(UTC),
            clock_health=lambda: True,
            lock_store=stores["lock"],
            lock_name="global.lock",
            lock_identity=h.identity(lock),
        )
        sources.registry_binding = binding
        sources.registry_identities = (
            stores["coordination"].identity,
            db_identity,
            stores["lock"].identity,
            h.identity(lock),
        )
        journal = coord.CoordinationJournal(**options)
        a = types.SimpleNamespace(
            now=lambda: datetime.now(UTC),
            root=root,
            f=f,
            stores=stores,
            remote=remote,
            auth=auth,
            transport=transport,
            sources=sources,
            journal=journal,
            options=options,
            bridge=bridge,
            holder=holder,
            bridge_pin=bridge_pin,
            floor=floor,
            lock=lock,
        )
        try:
            yield a
        finally:
            for store in stores.values():
                store.close()


def proposal(a, label="first", token=1, *, ack=None, project=None, session=None):
    schema = next(
        s
        for s in wire.request_schemas()
        if s["properties"]["operation"]["const"] == "reserve-start"
    )
    request = builder.fill(schema)
    now = a.now()
    identity = builder.identity()
    identity.update(
        started_at=coord._utc(now),
        git_sha="1" * 40,
        execution_nonce=hashlib.sha256(label.encode()).hexdigest(),
    )
    identity["run_id"] = wire.make_run_id(
        started_at=now,
        git_sha=identity["git_sha"],
        dataset_id=identity["dataset_id"],
        scenario_id=identity["scenario_id"],
        seed=identity["seed"],
        execution_nonce=identity["execution_nonce"],
    )
    identity["identity_digest"] = sql.digest(
        {k: v for k, v in identity.items() if k != "identity_digest"}
    )
    snapshot = a.sources.read_authority()
    head = history.current_revision(snapshot)
    targets = []
    for run in history.list_official_runs(snapshot):
        target = history.verify_ack_target(snapshot, run["run_id"])
        if target["recognizable"]:
            targets.append(
                {
                    "run_id": run["run_id"],
                    "authority_revision": target["revision"],
                    "terminal_evidence_reference": target["evidence_reference"],
                }
            )
    targets.sort(key=lambda x: x["run_id"])
    if ack is not None:
        targets = ack
    request.update(
        request_id=str(uuid.uuid5(uuid.NAMESPACE_URL, label + "-request")),
        nonce=identity["execution_nonce"],
        exchange_id=str(uuid.uuid5(uuid.NAMESPACE_URL, label + "-exchange")),
        authority_id=b.AUTH_ID,
        authority_epoch=1,
        authority_revision=head["revision"],
        source_revision=identity["git_sha"],
        issued_at=coord._utc(now),
        expires_at=coord._utc(now + timedelta(seconds=120)),
    )
    request["payload"].update(
        run_identity=identity,
        acknowledgments=targets,
        expected_head={
            "authority_id": b.AUTH_ID,
            "authority_epoch": 1,
            "revision": head["revision"],
            "ledger_digest": head["ledger_digest"],
            "witness_digest": head["witness_digest"],
            "receipt_reference": b.reference("approved-current-receipt"),
        },
    )
    if project is not None:
        request["project"] = project
    if session is not None:
        request["session_id"] = session
    cap = request["capability"]
    for field in (
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
    ):
        cap[field] = copy.deepcopy(request[field])
    cap.update(
        request_nonce=request["nonce"],
        expected_revision=request["authority_revision"],
        expected_run_id=identity["run_id"],
        reservation_id=None,
        reservation_digest=None,
        fencing_token=None,
        holder_public_key_hex=a.holder.public_key().public_bytes_raw().hex(),
        issued_at=request["issued_at"],
        expires_at=request["expires_at"],
        bridge_key_id=a.bridge_pin.key_id,
        bridge_key_epoch=a.bridge_pin.key_epoch,
    )
    raw = builder.signed_request(request, a.bridge, a.holder)
    request = wire.parse_request_bytes(raw).to_dict()
    context = wire.RequestContext(
        **{
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
        },
        authorization_reference=sql.canonical_bytes(request["authorization_reference"]),
        deployment_verification_sha256=cap["deployment_verification_sha256"],
        container_identity_digest=cap["container_identity_digest"],
        expected_run_id=identity["run_id"],
        reservation_id=None,
        reservation_digest=None,
        fencing_token=None,
        holder_public_key_hex=cap["holder_public_key_hex"],
        expected_payload=sql.canonical_bytes(request["payload"]),
        now=now,
    )
    a.sources.approved[hashlib.sha256(raw).hexdigest()] = context
    value = builder.reservation_value()
    value.update(
        namespace="S11C3-EX10",
        authority_id=b.AUTH_ID,
        authority_epoch=1,
        reservation_id=str(uuid.uuid5(uuid.NAMESPACE_URL, label + "-reservation")),
        session_id=request["session_id"],
        session_header_sha256=request["session_header_sha256"],
        runtime_profile_sha256=request["runtime_profile_sha256"],
        source_revision=request["source_revision"],
        project=request["project"],
        run_id=identity["run_id"],
        run_identity=identity,
        nonce=request["nonce"],
        request_id=request["request_id"],
        request_digest=request["request_digest"],
        acknowledgment_digest=sql.digest(targets),
        requested_at=request["issued_at"],
        updated_at=request["issued_at"],
        freshness_deadline=request["expires_at"],
        previous_authority_revision=head["revision"],
        previous_ledger_digest=head["ledger_digest"],
        previous_witness_digest=head["witness_digest"],
        expected_next_revision=head["revision"] + 1,
        fencing_token=token,
        state="PENDING",
        record_sequence=1,
        previous_reservation_record_digest=None,
        reservation_receipt_reference=None,
        open_witness_reference=None,
        evidence_receipt_reference=None,
        terminal_witness_reference=None,
        manual_resolution_reference=None,
        uncertainty_codes=[],
    )
    value = coord._seal_record(value)
    record = sql.canonical_bytes(value)
    a.sources.record_inputs[request["request_id"]] = value
    return raw, record


def test_atomic_local_claim_then_independent_grant_and_external_floor(rig):
    a = rig
    raw, record = proposal(a)
    base = a.f.database.read_bytes()
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        assert type(attempt) is coord.OpeningAttempt
        attempt.check()
        state = a.journal.read()
        assert len(state["registry"]) == len(state["claims"]) == 1
        assert len(state["events"]) == 2 and state["sequence"] == 1 and state["token"] == 1
        assert attempt.record["reservation_receipt_reference"] is not None
        assert a.floor.current()["sequence"] == 1
        assert not os.get_inheritable(attempt.writer.fd)
    assert a.f.database.read_bytes() == base  # Reserve never consumes a B3 revision.
    assert a.transport.calls.count("RESERVE") == 1
    assert history.current_revision(a.sources.read_authority())["revision"] == 0


def test_exact_duplicate_recognized_without_dispatch_or_new_events(rig):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record):
        pass
    before = a.journal.read()
    calls = a.transport.calls.count("RESERVE")
    with a.journal.opening(raw_request=raw, raw_record=record) as result:
        assert type(result) is coord.DuplicateRecognition and result.state == "PENDING"
        assert not hasattr(result, "writer")
    assert a.journal.read() == before and a.transport.calls.count("RESERVE") == calls


@pytest.mark.parametrize("failure", ["before", "response"])
def test_witness_result_unknown_preserves_local_claim_and_never_resends(rig, failure):
    a = rig
    raw, record = proposal(a)
    a.transport.fail = failure
    with pytest.raises(sql.AuthorityError, match="UNCERTAIN_PUBLICATION"):
        with a.journal.opening(raw_request=raw, raw_record=record):
            pytest.fail("unknown grant admitted")
    state = a.journal.read()
    assert state["active"] and len(state["events"]) == 1
    assert len(state["claims"]) == 1 and state["sequence"] == 0
    a.transport.fail = None
    with a.journal.opening(raw_request=raw, raw_record=record) as replay:
        assert type(replay) is coord.DuplicateRecognition
    assert a.transport.calls.count("RESERVE") == 1
    restarted = coord.CoordinationJournal(**a.options)
    assert restarted.read()["active"] == state["active"]


def test_exact_nd_ack_missing_is_denied_before_claim(rig):
    a = rig
    raw, record = proposal(a, ack=[])
    with pytest.raises(sql.AuthorityError, match="ACK_INVALID"):
        with a.journal.opening(raw_request=raw, raw_record=record):
            pytest.fail("missing ACK admitted")
    assert a.journal.read()["claims"] == {} and a.transport.calls.count("RESERVE") == 0


def test_second_pending_proposal_blocks_even_after_deadline(rig):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record):
        pass
    expired = coord.sql.utc_time(
        a.journal.read()["latest"][wire.qualify_reservation(record).to_dict()["reservation_id"]][
            "freshness_deadline"
        ]
    ).replace(tzinfo=UTC) + timedelta(seconds=1)
    a.now = lambda: expired
    a.journal.clock = a.now
    a.remote.clock = a.now
    a.sources.now = a.now
    second, record2 = proposal(a, "second", 2)
    with pytest.raises(sql.AuthorityError, match="RESERVATION_BUSY"):
        with a.journal.opening(raw_request=second, raw_record=record2):
            pytest.fail("second admitted")
    assert a.transport.calls.count("RESERVE") == 1
    assert len(a.journal.read()["claims"]) == 1


@pytest.mark.parametrize("context", ["root-project-a", "root-project-b", "other-session"])
def test_distinct_projects_sessions_share_single_deployment_slot(rig, context):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record):
        pass
    raw2, record2 = proposal(
        a,
        context,
        2,
        project=context if context != "other-session" else None,
        session=str(uuid.uuid4()) if context == "other-session" else None,
    )
    with pytest.raises(sql.AuthorityError, match="RESERVATION_BUSY"):
        with a.journal.opening(raw_request=raw2, raw_record=record2):
            pytest.fail("partitioned grant")
    assert a.transport.calls.count("RESERVE") == 1


def test_registry_refuses_independent_lock_alias(rig):
    a = rig
    other = a.root / "lock/other.lock"
    other.write_bytes(b"")
    other.chmod(0o600)
    options = {**a.options, "lock_name": "other.lock", "lock_identity": h.identity(other)}
    with pytest.raises(sql.AuthorityError, match="TRUST_UNAPPROVED"):
        coord.CoordinationJournal(**options)
    assert a.journal.read()["claims"] == {}


def test_registry_refuses_aliased_store_or_changed_binding(rig):
    a = rig
    with pytest.raises(sql.AuthorityError):
        coord.CoordinationJournal(**{**a.options, "receipt_store": a.stores["lock"]})
    with pytest.raises(sql.AuthorityError, match="TRUST_UNAPPROVED"):
        coord.CoordinationJournal(
            **{**a.options, "registry_binding": sql.canonical_bytes({"other": "binding"})}
        )


def test_capability_expires_during_preliminary_head_before_claim(rig):
    a = rig
    raw, record = proposal(a)
    requested = wire.parse_request_bytes(raw).to_dict()
    expiry = sql.utc_time(requested["expires_at"]).replace(tzinfo=UTC)
    original = a.transport.head

    def delayed_head(challenge):
        result = original(challenge)
        # Simulate elapsed local time after a completed valid head read.
        a.journal.clock = lambda: expiry + timedelta(seconds=1)
        a.sources.now = lambda: expiry + timedelta(seconds=1)
        return result

    a.transport.head = delayed_head
    with pytest.raises((sql.AuthorityError, wire.BridgeProtocolError), match="EXPIRED|expired"):
        with a.journal.opening(raw_request=raw, raw_record=record):
            pytest.fail("expired grant")
    assert a.journal.read()["claims"] == {} and a.transport.calls.count("RESERVE") == 0


def test_expiration_after_local_claim_preserves_blocker_without_grant(rig, monkeypatch):
    a = rig
    raw, record = proposal(a)
    expiry = sql.utc_time(wire.parse_request_bytes(raw).to_dict()["expires_at"]).replace(tzinfo=UTC)
    original = coord.OpeningAttempt._grant

    def expired_grant(attempt):
        a.journal.clock = lambda: expiry + timedelta(seconds=1)
        a.sources.now = lambda: expiry + timedelta(seconds=1)
        return original(attempt)

    monkeypatch.setattr(coord.OpeningAttempt, "_grant", expired_grant)
    with pytest.raises((sql.AuthorityError, wire.BridgeProtocolError), match="EXPIRED|expired"):
        with a.journal.opening(raw_request=raw, raw_record=record):
            pytest.fail("expired grant")
    state = a.journal.read()
    assert state["active"] and len(state["claims"]) == 1 and state["sequence"] == 0
    assert a.transport.calls.count("RESERVE") == 0


def test_lock_busy_is_one_nonblocking_attempt_with_no_durable_claim(rig):
    a = rig
    raw, record = proposal(a)
    with backend.HeldWriter(a.stores["lock"], "global.lock", h.identity(a.lock)):
        with pytest.raises(sql.AuthorityError, match="LOCK_BUSY"):
            with a.journal.opening(raw_request=raw, raw_record=record):
                pytest.fail("second owner")
    assert a.journal.read()["claims"] == {} and a.transport.calls.count("RESERVE") == 0


@pytest.mark.parametrize(
    "phase", ["before-lock", "before-claim", "after-local-claim", "after-independent-grant"]
)
def test_actual_owner_death_releases_os_lock_but_keeps_durable_slot(rig, phase):
    a = rig
    raw, record = proposal(a)

    def die():
        if phase == "before-lock":
            a.sources.before_read = lambda reads: os._exit(40)
        if phase == "before-claim":
            with backend.HeldWriter(a.stores["lock"], "global.lock", h.identity(a.lock)):
                os._exit(41)
        if phase == "after-local-claim":
            a.transport.reserve = lambda *args, **kwargs: os._exit(42)
        with a.journal.opening(raw_request=raw, raw_record=record):
            os._exit(43)

    proc = multiprocessing.get_context("fork").Process(target=die)
    proc.start()
    proc.join(15)
    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        if proc.is_alive():
            proc.kill()
            proc.join(5)
        assert not proc.is_alive(), "owned crash child survived bounded cleanup"
        pytest.fail("child blocked instead of bounded phase")
    assert (
        proc.exitcode
        == {
            "before-lock": 40,
            "before-claim": 41,
            "after-local-claim": 42,
            "after-independent-grant": 43,
        }[phase]
    )
    # Fresh flock is now possible, durable ownership cannot be inferred from PID death.
    with backend.HeldWriter(a.stores["lock"], "global.lock", h.identity(a.lock)):
        pass
    restarted = coord.CoordinationJournal(**a.options)
    state = restarted.read()
    if phase in ("before-lock", "before-claim"):
        assert state["claims"] == {} and state["active"] is None
    else:
        assert len(state["claims"]) == 1 and state["active"]
        assert state["sequence"] == (1 if phase == "after-independent-grant" else 0)
        with restarted.opening(raw_request=raw, raw_record=record) as duplicate:
            assert type(duplicate) is coord.DuplicateRecognition
        other, other_record = proposal(a, "after-death", 2)
        with pytest.raises(sql.AuthorityError, match="RESERVATION_BUSY"):
            with restarted.opening(raw_request=other, raw_record=other_record):
                pytest.fail("released by death")


def test_saved_signed_response_roundtrip_and_duplicate_without_redispatch(rig):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        request = wire.parse_request_bytes(raw).to_dict()
        schema = next(
            s
            for s in wire.response_schemas()
            if s["properties"]["operation"]["const"] == "reserve-start"
            and s["properties"]["status"]["const"] == "OK"
        )
        response = builder.fill(schema)
        for field in (
            "request_id",
            "nonce",
            "exchange_id",
            "operation",
            "request_digest",
            "session_id",
            "runtime_profile_sha256",
            "authority_id",
            "authority_epoch",
        ):
            response[field] = request[field]
        response.update(
            authority_revision_before=request["authority_revision"],
            authority_revision_after=request["authority_revision"],
            completed_at=coord._utc(a.now()),
            bridge_key_id=a.bridge_pin.key_id,
            bridge_key_epoch=a.bridge_pin.key_epoch,
            run_id=request["payload"]["run_identity"]["run_id"],
            reservation_id=attempt.record["reservation_id"],
        )
        response["result"]["reservation"] = copy.deepcopy(attempt.record)
        encoded = builder.signed_response(response, a.bridge)
        assert attempt.save_response(authenticated=attempt.request, raw_response=encoded) == encoded
    before = a.journal.read()
    with a.journal.opening(raw_request=raw, raw_record=record) as duplicate:
        assert duplicate.saved_response == encoded
    assert a.journal.inspect(request_id=request["request_id"]).saved_response == encoded
    assert a.journal.read() == before and a.transport.calls.count("RESERVE") == 1


spec = importlib.util.spec_from_file_location(
    "w05_evd_builder_" + __name__.replace(".", "_"),
    Path(__file__).with_name("test_ex10_open_evidence.py"),
)
ebuilder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ebuilder)
spec = importlib.util.spec_from_file_location(
    "w05_gov_builder_" + __name__.replace(".", "_"),
    Path(__file__).with_name("test_ex10_governance_certificate.py"),
)
gbuilder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gbuilder)


def bound_request(a, attempt, operation, payload, label):
    base = attempt.request.request.to_dict()
    schema = next(
        s for s in wire.request_schemas() if s["properties"]["operation"]["const"] == operation
    )
    value = builder.fill(schema)
    now = a.now()
    for field in (
        "exchange_id",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "authorization_reference",
    ):
        value[field] = copy.deepcopy(base[field])
    value.update(
        request_id=str(uuid.uuid5(uuid.NAMESPACE_URL, label)),
        nonce=b.hashed(label),
        payload=payload,
        issued_at=coord._utc(now),
        expires_at=base["expires_at"],
    )
    cap = value["capability"]
    for field in (
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
    ):
        cap[field] = copy.deepcopy(value[field])
    cap.update(
        request_nonce=value["nonce"],
        expected_revision=value["authority_revision"],
        expected_run_id=attempt.record["run_id"],
        reservation_id=attempt.record["reservation_id"],
        reservation_digest=attempt.record["reservation_digest"],
        fencing_token=attempt.record["fencing_token"],
        holder_public_key_hex=base["capability"]["holder_public_key_hex"],
        deployment_verification_sha256=base["capability"]["deployment_verification_sha256"],
        container_identity_digest=base["capability"]["container_identity_digest"],
        issued_at=value["issued_at"],
        expires_at=value["expires_at"],
        bridge_key_id=a.bridge_pin.key_id,
        bridge_key_epoch=a.bridge_pin.key_epoch,
    )
    raw = builder.signed_request(value, a.bridge, a.holder)
    value = wire.parse_request_bytes(raw).to_dict()
    context = wire.RequestContext(
        **{
            k: value[k]
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
        authorization_reference=sql.canonical_bytes(value["authorization_reference"]),
        deployment_verification_sha256=cap["deployment_verification_sha256"],
        container_identity_digest=cap["container_identity_digest"],
        expected_run_id=attempt.record["run_id"],
        reservation_id=attempt.record["reservation_id"],
        reservation_digest=attempt.record["reservation_digest"],
        fencing_token=attempt.record["fencing_token"],
        holder_public_key_hex=cap["holder_public_key_hex"],
        expected_payload=sql.canonical_bytes(payload),
        now=now,
    )
    a.sources.approved[hashlib.sha256(raw).hexdigest()] = context
    return raw


@pytest.fixture
def initial_artifacts():
    gen = ebuilder.sample.__wrapped__()
    try:
        yield next(gen)
    finally:
        gen.close()


def prepare_existing_open_candidate_reader(a):
    """Pin the one synthetic OPEN candidate store before a child can publish."""
    root = a.root / "candidate"
    if "candidate" not in a.stores:
        root.mkdir(mode=0o700)
        a.stores["candidate"] = backend.PrivateStore(str(root), h.identity(root))
    store = a.stores["candidate"]
    sql.require(store.root == str(root), "UNSAFE_PATH")
    store.check()
    original_reader = a.f.trust()["intent_payload_reader"]
    candidate_path = store.root + "/exact-open.json"

    def read_candidate(path):
        if path == candidate_path:
            return store.read(path)
        return original_reader(path)

    a.sources.intent_reader = read_candidate


def existing_open_sources(a, attempt, sample):
    """Synthetic fixed host, actual W01 file verification and W04 independent B3 CAS.

    Native B1/B2/deployment full qualification remains W06; no hash-only evidence proof.
    """
    evd = coord.evidence
    identity = attempt.record["run_identity"]
    root = sample["root"]
    payloads = copy.deepcopy(sample["payloads"])
    manifest, start, pf = (payloads[k] for k in evd.REFERENCE_FIELDS[:3])
    manifest.update(
        run_id=identity["run_id"], git_sha=identity["git_sha"], started_at=identity["started_at"]
    )
    manifest["configuration"]["git_sha"] = identity["git_sha"]
    pf.update(
        git_sha=identity["git_sha"],
        expected_git_sha=identity["git_sha"],
        checked_at=identity["started_at"],
    )
    start.update(
        run_id=identity["run_id"], git_sha=identity["git_sha"], recorded_at=identity["started_at"]
    )
    start["preflight"].update(sha256=sql.digest(pf), checked_at=identity["started_at"])
    refs = {}
    for field in evd.REFERENCE_FIELDS:
        path = root / (field + ".json")
        path.write_bytes(sql.canonical_bytes(payloads[field]))
        path.chmod(0o600)
        refs[field] = {
            "path": str(path),
            "sha256": sql.digest(payloads[field]),
            "schema_version": evd.ARTIFACT_SCHEMAS[field],
        }
    opening = {
        **sample["receipt"],
        **refs,
        "reserve_request_id": attempt.record["request_id"],
        "reservation_id": attempt.record["reservation_id"],
        "fencing_token": attempt.record["fencing_token"],
        "open_permit_digest": b.hashed("exact-test-permit"),
        "run_id": identity["run_id"],
        "identity_digest": identity["identity_digest"],
        "runtime_profile_sha256": attempt.record["runtime_profile_sha256"],
        "session_header_sha256": attempt.record["session_header_sha256"],
        "opened_at": identity["started_at"],
    }
    raw_evd = ebuilder.sign(opening, a.holder)
    opening = evd.parse_open_evidence_bytes(raw_evd).to_dict()
    specs = tuple(
        evd.ArtifactExpectation(field, refs[field]["path"], 1048576)
        for field in evd.REFERENCE_FIELDS
    )
    evd_context = evd.OpenEvidenceContext(
        attempt.record["request_id"],
        attempt.record["reservation_id"],
        attempt.record["fencing_token"],
        opening["open_permit_digest"],
        attempt.record["runtime_profile_sha256"],
        attempt.record["session_header_sha256"],
        sql.canonical_bytes(identity),
        sql.utc_time(identity["started_at"]).replace(tzinfo=UTC),
        sql.utc_time(attempt.record["freshness_deadline"]).replace(tzinfo=UTC),
        a.now(),
        sample["context"].executor_fingerprint,
        sample["context"].preflight_required_checks,
        specs,
    )
    evidence_path = root / "open-evidence.json"
    evidence_path.write_bytes(raw_evd)
    evidence_path.chmod(0o600)
    evidence_ref = {
        "path": str(evidence_path),
        "sha256": hashlib.sha256(raw_evd).hexdigest(),
        "schema_version": opening["schema_version"],
    }
    payload = {
        k: copy.deepcopy(attempt.record[k])
        for k in ("reservation_id", "reservation_digest", "fencing_token", "run_identity")
    }
    payload.update(
        reserve_request_id=attempt.record["request_id"],
        open_permit_digest=opening["open_permit_digest"],
        reservation_receipt_reference=attempt.record["reservation_receipt_reference"],
        open_evidence=opening,
        expected_head=attempt.request.request.to_dict()["payload"]["expected_head"],
    )
    commit = bound_request(a, attempt, "commit-open", payload, "exact-test-commit")
    prepare_existing_open_candidate_reader(a)
    for name in ("b3-floor",):
        p = a.root / name
        p.mkdir(mode=0o700)
        a.stores[name] = backend.PrivateStore(str(p), h.identity(p))
    a.stores["ledger"] = backend.PrivateStore(str(a.f.root), h.identity(a.f.root))
    floor_store = a.stores["b3-floor"]
    floor_store.create_exact(
        floor_store.root + "/floor-00000000000000000000.json", sql.canonical_bytes(a.f.receipt)
    )
    floor = backend.DurableFloor(
        floor_store, [a.f.pin], sql.digest(a.f.receipt), a.f.receipt["genesis"]
    )
    transport = h.Transport(a.remote, a.auth.credential)
    a.b3_transport = transport
    a.b3_floor = floor
    a.existing_evidence = types.SimpleNamespace(
        root=root, path=evidence_path, raw=raw_evd, opening=opening
    )

    def qualify_open(*, authenticated_request, opening):
        assert opening is attempt
        with evd.ConfinedArtifactResolver(str(root), expected_owner=os.geteuid()) as resolver:
            accepted = evd.verify_open_evidence(
                raw_evd,
                expected_context=replace(evd_context, now=a.now()),
                executor_public_key=a.holder.public_key(),
                artifact_resolver=resolver,
            )
        value = gbuilder.candidate(a.f, attempt.snapshot)["governance_certificate"].value
        request = authenticated_request.request.to_dict()
        stamp = coord._utc(a.now())
        value.update(
            run_id=identity["run_id"],
            source_revision=identity["git_sha"],
            run_identity_digest=identity["identity_digest"],
            project=attempt.record["project"],
            issued_at=stamp,
            reserve_request_id=attempt.record["request_id"],
            reserve_request_digest=attempt.record["request_digest"],
            commit_request_id=request["request_id"],
            commit_request_digest=request["request_digest"],
            open_evidence_reference=evidence_ref,
            acknowledgments=attempt.request.request.to_dict()["payload"]["acknowledgments"],
            acknowledgment_digest=attempt.record["acknowledgment_digest"],
        )
        value["session"].update(
            session_id=attempt.record["session_id"],
            header_sha256=attempt.record["session_header_sha256"],
        )
        value["session"]["header_reference"]["sha256"] = attempt.record["session_header_sha256"]
        value["runtime_profile"]["sha256"] = attempt.record["runtime_profile_sha256"]
        value["runtime_profile"]["reference"]["sha256"] = attempt.record["runtime_profile_sha256"]
        value["reservation"].update(
            reservation_id=attempt.record["reservation_id"],
            reservation_digest=attempt.record["reservation_digest"],
            record_sequence=attempt.record["record_sequence"],
            fencing_token=attempt.record["fencing_token"],
            state_at_open="PENDING",
            witness_receipt_reference=attempt.record["reservation_receipt_reference"],
            witness_sequence=1,
        )
        value["open_one_permit"]["permit_digest"] = payload["open_permit_digest"]
        value["certificate_id"] = coord.governance.certificate_identity(value)
        cert = coord.governance.validate_governance_certificate(value)
        path = a.f.root / (value["certificate_id"] + ".json")
        path.write_bytes(cert.canonical_bytes)
        path.chmod(0o600)
        ref = b.artifact(
            {
                "path": str(path),
                "sha256": coord.governance.governance_certificate_sha256(cert),
                "schema_version": coord.governance.SCHEMA_VERSION,
            },
            "GOVERNANCE",
        )
        ref["introduced_revision"] = 1
        rows = []
        for item, kind in (
            (value["runtime_profile"]["reference"], "PROFILE"),
            (value["session"]["header_reference"], "SESSION"),
        ):
            row = b.artifact(item, kind)
            row["introduced_revision"] = 1
            rows.append(row)
        run = a.f.run("OPEN", identity["run_id"])
        run.update(
            provenance_model="PROFILE_V2",
            introduced_revision=1,
            created_at=identity["started_at"],
            opened_at=opening.record["run_identity"]["started_at"],
            source_revision=identity["git_sha"],
            profile_reference_id=rows[0]["reference_id"],
            session_reference_id=rows[1]["reference_id"],
            evidence_authority_reference_id=ref["reference_id"],
        )
        event = a.f.event(
            "RUN_OPENED", sequence=len(a.f.state["authority_events"]) + 1, run=run, revision=1
        )
        event.update(occurred_at=stamp, evidence_reference_id=ref["reference_id"])
        adapter = backend.AuthorityBackend(
            ledger_store=a.stores["ledger"],
            ledger_name="ledger.sqlite3",
            ledger_identity=h.identity(a.f.database),
            candidate_store=a.stores["candidate"],
            floor=floor,
            writer=attempt.writer,
            transport=transport,
            signer_pin=a.f.pin,
            clock_health=lambda: True,
        )
        args = a.f.publication_args(adapter, "RUN_OPENED")
        args.update(
            operation_id=request["request_id"],
            occurred_at=stamp,
            evidence_reference_id=ref["reference_id"],
            candidate_payload_path=a.stores["candidate"].root + "/exact-open.json",
        )
        prepared = coord.OpenPublication(
            accepted,
            sql.canonical_bytes(evidence_ref),
            cert,
            sql.canonical_bytes(ref),
            tuple(rows),
            run,
            event,
            args,
        )
        a.prepared_open = prepared
        return prepared

    a.sources.qualify_open = qualify_open

    def sign_response(*, authenticated_request, opening):
        request = authenticated_request.request.to_dict()
        schema = next(
            s
            for s in wire.response_schemas()
            if s["properties"]["operation"]["const"] == "commit-open"
            and s["properties"]["status"]["const"] == "OK"
        )
        response = builder.fill(schema)
        for field in (
            "request_id",
            "nonce",
            "exchange_id",
            "operation",
            "request_digest",
            "session_id",
            "runtime_profile_sha256",
            "authority_id",
            "authority_epoch",
        ):
            response[field] = request[field]
        response.update(
            authority_revision_before=0,
            authority_revision_after=1,
            completed_at=coord._utc(a.now()),
            bridge_key_id=a.bridge_pin.key_id,
            bridge_key_epoch=a.bridge_pin.key_epoch,
            run_id=attempt.record["run_id"],
            reservation_id=attempt.record["reservation_id"],
        )
        response["result"].update(
            open_evidence=request["payload"]["open_evidence"],
            open_witness=attempt.record["open_witness_reference"],
            reservation=copy.deepcopy(attempt.record),
        )
        return builder.signed_response(response, a.bridge)

    a.sources.sign_response = sign_response
    return commit


def test_actual_open_b3_floor_then_distinct_b4_commit_and_saved_response(rig, initial_artifacts):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        commit = existing_open_sources(a, attempt, initial_artifacts)
        response = attempt.complete_open(raw_request=commit)
        state = a.journal.read()
        assert state["latest"][attempt.record["reservation_id"]]["state"] == "COMMITTED"
        assert state["sequence"] == 2 and state["token"] == 1 and len(state["claims"]) == 2
        assert a.b3_transport.calls.count("B3_CAS") == 1
        assert a.transport.calls.count("RESERVE") == a.transport.calls.count("COMMIT_OPEN") == 1
        assert a.b3_floor.current().revision == 1 and a.floor.current()["sequence"] == 2
        request = wire.parse_request_bytes(commit).to_dict()
        assert a.journal.inspect(request_id=request["request_id"]).saved_response == response
        recognized = attempt.complete_open(raw_request=commit)
        assert (
            type(recognized) is coord.DuplicateRecognition and recognized.saved_response == response
        )
        assert a.b3_transport.calls.count("B3_CAS") == 1
    snapshot = a.sources.read_authority()
    assert history.first_valid_status(snapshot)["status"] == "ACTIVE_BLOCKER"
    assert history.find_run(snapshot, attempt.record["run_id"])["lifecycle_state"] == "OPEN"
    assert a.existing_evidence.path.read_bytes() == a.existing_evidence.raw


def mark_alive_uncertain(a, attempt):
    payload = {
        "reservation_id": attempt.record["reservation_id"],
        "fencing_token": attempt.record["fencing_token"],
        "observation_reference": b.reference("qualified-unknown-observation"),
        "reason_code": "RESPONSE_LOST",
    }
    raw = bound_request(a, attempt, "record-uncertain", payload, "record-uncertain-request")
    a.sources.uncertainty_inputs.add(hashlib.sha256(raw).hexdigest())
    attempt.record_uncertain(raw_request=raw)
    return raw


def approved_cancel(a, record, *, change=None, approve=True):
    # A distinct test operator; no executor key can invoke the recovery admission API.
    sample = builder.sample_for("cancel-reservation-manual")
    value = wire.parse_request_bytes(sample["raw"]).to_dict()
    operator = Ed25519PrivateKey.generate()
    now = a.now()
    state = a.journal.read()
    snapshot = a.sources.read_authority()
    head = history.current_revision(snapshot)
    fresh = a.journal.fresh_head(state)
    value.update(
        request_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "manual-cancel-exact")),
        nonce=b.hashed("manual-cancel-nonce"),
        authority_id=b.AUTH_ID,
        authority_epoch=1,
        authority_revision=head["revision"],
        issued_at=coord._utc(now),
        expires_at=coord._utc(now + timedelta(seconds=120)),
        session_id=record["session_id"],
        session_header_sha256=record["session_header_sha256"],
        runtime_profile_sha256=record["runtime_profile_sha256"],
        source_revision=record["source_revision"],
        project=record["project"],
    )
    payload = value["payload"]
    payload.update(
        reservation_id=record["reservation_id"],
        expected_state="UNCERTAIN",
        expected_record_digest=record["record_digest"],
        expected_record_sequence=record["record_sequence"],
        fresh_coordination_head=fresh,
        fresh_authority_head={
            "authority_id": b.AUTH_ID,
            "authority_epoch": 1,
            "revision": head["revision"],
            "ledger_digest": head["ledger_digest"],
            "witness_digest": head["witness_digest"],
            "receipt_reference": b.reference("approved-head-ref"),
        },
        evidence_references=[b.reference("approved-no-open")],
        operator_authorization_reference=b.reference("approved-explicit-admin"),
        writer_closure_reference=b.reference("verified-stopped-writers"),
        no_open_certificate_reference=b.reference("independent-no-open"),
        idempotency_token=b.hashed("exact-manual-token"),
        reason_code="OPERATOR_CANCELLED_BEFORE_OPEN",
    )
    if change is not None:
        change(value)
    cap = value["capability"]
    for field in (
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
    ):
        cap[field] = copy.deepcopy(value[field])
    cap.update(
        request_nonce=value["nonce"],
        expected_revision=value["authority_revision"],
        expected_run_id=record["run_id"],
        reservation_id=record["reservation_id"],
        reservation_digest=record["reservation_digest"],
        fencing_token=record["fencing_token"],
        holder_public_key_hex=operator.public_key().public_bytes_raw().hex(),
        issued_at=value["issued_at"],
        expires_at=value["expires_at"],
        bridge_key_id=a.bridge_pin.key_id,
        bridge_key_epoch=a.bridge_pin.key_epoch,
    )
    raw = builder.signed_request(value, a.bridge, operator)
    value = wire.parse_request_bytes(raw).to_dict()
    context = wire.RecoveryRequestContext(
        **{
            k: value[k]
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
        authorization_reference=sql.canonical_bytes(value["authorization_reference"]),
        deployment_verification_sha256=cap["deployment_verification_sha256"],
        container_identity_digest=cap["container_identity_digest"],
        expected_run_id=record["run_id"],
        reservation_id=record["reservation_id"],
        reservation_digest=record["reservation_digest"],
        fencing_token=record["fencing_token"],
        holder_public_key_hex=cap["holder_public_key_hex"],
        expected_payload=sql.canonical_bytes(value["payload"]),
        now=now,
        operator_authorization_reference=sql.canonical_bytes(
            payload["operator_authorization_reference"]
        ),
    )
    if approve:
        a.sources.recovery_approved[hashlib.sha256(raw).hexdigest()] = context
    a.sources.manual_events[record["reservation_id"]] = payload["operator_authorization_reference"]

    def qualify_recovery(*, authenticated_request, record, writer, coordination_head):
        writer.check()
        actual = authenticated_request.request.to_dict()
        sql.require(
            actual == value
            and record["state"] == "UNCERTAIN"
            and a.journal.read()["active"] == record["reservation_id"]
            and coordination_head["active_reservation_id"] == record["reservation_id"],
            "TRUST_UNAPPROVED",
        )
        current = a.sources.read_authority()
        sql.require(
            history.current_revision(current)["revision"] == record["previous_authority_revision"]
            and history.find_run(current, record["run_id"]) is None
            and not (
                {i["operation_id"] for i in current.material["state"]["publication_intents"]}
                - {r["operation_id"] for r in current.material["state"]["publication_resolutions"]}
            ),
            "TRUST_UNAPPROVED",
        )
        approval = service.RecoveryApproval(
            value["request_id"],
            record["reservation_id"],
            coordination_head["receipt_digest"],
            sql.canonical_bytes(payload["operator_authorization_reference"]),
            sql.canonical_bytes(payload["writer_closure_reference"]),
            sql.canonical_bytes(payload["no_open_certificate_reference"]),
        )
        return coord.RecoveryQualification(approval, current)

    a.sources.qualify_recovery = qualify_recovery
    return raw


def test_explicit_uncertainty_then_separate_approved_manual_cancel_preserves_tombstones(rig):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        mark_alive_uncertain(a, attempt)
        assert attempt.record["state"] == "UNCERTAIN" and a.floor.current()["sequence"] == 2
        uncertain = copy.deepcopy(attempt.record)
    recovery = approved_cancel(a, uncertain)
    result = a.journal.recover(raw_request=recovery)
    assert result["state"] == "CANCELLED_MANUALLY"
    state = a.journal.read()
    assert state["active"] is None and state["witness_active"] is None and state["sequence"] == 3
    assert len(state["claims"]) == 3 and len(state["registry"]) == 1
    assert (
        a.transport.calls.count("CANCEL_MANUAL") == 1
        and a.transport.calls.count("RECORD_UNCERTAIN") == 1
    )
    before = copy.deepcopy(state)
    duplicate = a.journal.recover(raw_request=recovery)
    assert type(duplicate) is coord.DuplicateRecognition and duplicate.state == "CANCELLED_MANUALLY"
    assert a.journal.read() == before and a.transport.calls.count("CANCEL_MANUAL") == 1
    assert history.current_revision(a.sources.read_authority())["revision"] == 0
    with a.journal.opening(raw_request=raw, raw_record=record) as old:
        assert old.state == "CANCELLED_MANUALLY"
    assert a.transport.calls.count("RESERVE") == 1


@pytest.mark.parametrize(
    "failure", ["unapproved", "sequence", "digest", "normal-capability", "false-no-open"]
)
def test_recovery_rejects_missing_approval_stale_cas_and_normal_capability(rig, failure):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        uncertain_request = mark_alive_uncertain(a, attempt)
        uncertain = copy.deepcopy(attempt.record)
    changes = {
        "sequence": lambda v: v["payload"].update(expected_record_sequence=1),
        "digest": lambda v: v["payload"].update(expected_record_digest="f" * 64),
    }
    recovery = approved_cancel(
        a, uncertain, change=changes.get(failure), approve=failure != "unapproved"
    )
    if failure == "normal-capability":
        recovery = uncertain_request
    if failure == "false-no-open":
        a.sources.qualify_recovery = lambda **kwargs: True
    before = a.journal.read()
    with pytest.raises((sql.AuthorityError, wire.BridgeProtocolError)):
        a.journal.recover(raw_request=recovery)
    assert a.journal.read() == before and a.transport.calls.count("CANCEL_MANUAL") == 0


def test_uncertainty_result_unknown_keeps_local_blocker_and_does_not_retry(rig):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        original = a.transport.transition

        def unknown(**kwargs):
            original(**kwargs)
            raise TimeoutError("private response lost")

        a.transport.transition = unknown
        with pytest.raises(sql.AuthorityError, match="UNCERTAIN_PUBLICATION"):
            mark_alive_uncertain(a, attempt)
        assert a.journal.read()["latest"][attempt.record["reservation_id"]]["state"] == "UNCERTAIN"
        raw_uncertain = next(
            c["canonical"]
            for c in a.journal.read()["claims"].values()
            if c["request"]["operation"] == "record-uncertain"
        )
        duplicate = attempt.record_uncertain(raw_request=raw_uncertain)
        assert type(duplicate) is coord.DuplicateRecognition
        assert a.transport.calls.count("RECORD_UNCERTAIN") == 1


@pytest.mark.parametrize(
    "phase", ["C5-evidence", "C5-intent", "C5-independent-open", "C6-floor", "C7-response"]
)
def test_actual_late_crash_keeps_evidence_claims_and_never_reopens(rig, initial_artifacts, phase):
    a = rig
    raw, record = proposal(a)
    prepare_existing_open_candidate_reader(a)

    def die():
        with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
            commit = existing_open_sources(a, attempt, initial_artifacts)
            if phase == "C5-evidence":
                os._exit(51)
            if phase == "C5-intent":
                a.b3_transport.publish_b3 = lambda *args, **kwargs: os._exit(52)
            if phase == "C5-independent-open":
                original = a.b3_transport.publish_b3

                def after_cas(*args, **kwargs):
                    original(*args, **kwargs)
                    os._exit(53)

                a.b3_transport.publish_b3 = after_cas
            if phase == "C6-floor":
                attempt._transition = lambda **kwargs: os._exit(54)
            if phase == "C7-response":
                save = attempt.save_response

                def after_response(**kwargs):
                    save(**kwargs)
                    os._exit(55)

                attempt.save_response = after_response
            attempt.complete_open(raw_request=commit)
            pytest.fail("configured crash phase never reached")

    proc = multiprocessing.get_context("fork").Process(target=die)
    proc.start()
    proc.join(20)
    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        if proc.is_alive():
            proc.kill()
            proc.join(5)
        assert not proc.is_alive(), "owned crash child survived bounded cleanup"
        pytest.fail("unbounded crash phase")
    assert (
        proc.exitcode
        == {
            "C5-evidence": 51,
            "C5-intent": 52,
            "C5-independent-open": 53,
            "C6-floor": 54,
            "C7-response": 55,
        }[phase]
    )
    evidence_path = initial_artifacts["root"] / "open-evidence.json"
    retained = evidence_path.read_bytes()
    assert retained
    state = coord.CoordinationJournal(**a.options).read()
    assert state["active"] and len(state["registry"]) == 1
    assert len(state["claims"]) == (1 if phase == "C5-evidence" else 2)
    current = a.remote.head_b3(b.hashed("fresh-crash-inspection"), credentials=a.auth.credential)
    ledger = sql._load_database(a.f.database)
    local_revision = max(row["revision"] for row in ledger["history_revisions"])
    assert current["revision"] == (
        1 if phase in ("C5-independent-open", "C6-floor", "C7-response") else 0
    )
    assert local_revision == (1 if phase in ("C6-floor", "C7-response") else 0)
    assert len(ledger["publication_intents"]) == (1 if phase == "C5-evidence" else 2)
    assert state["latest"][state["active"]]["state"] == (
        "COMMITTED" if phase == "C7-response" else "PENDING"
    )
    assert len(state["responses"]) == (1 if phase == "C7-response" else 0)
    before = copy.deepcopy(state)
    with a.journal.opening(raw_request=raw, raw_record=record) as duplicate:
        assert type(duplicate) is coord.DuplicateRecognition
    assert a.journal.read() == before and evidence_path.read_bytes() == retained
    if phase == "C7-response":
        claim = next(
            c for c in state["claims"].values() if c["request"]["operation"] == "commit-open"
        )
        recognition = a.journal.inspect(request_id=claim["request"]["request_id"])
        assert recognition.saved_response == state["responses"][claim["request"]["request_id"]]
    # New independent identities cannot displace the durable slot even if B3 is still NONE.
    if phase == "C5-evidence":
        newer, new_record = proposal(a, "after-crash-other", 2)
        with pytest.raises(sql.AuthorityError, match="RESERVATION_BUSY"):
            with a.journal.opening(raw_request=newer, raw_record=new_record):
                pytest.fail("crash reopened")


def test_actual_sidecar_backup_same_inode_is_rejected_by_external_floor(rig):
    a = rig
    raw, record = proposal(a)
    path = Path(a.stores["coordination"].root) / "coord.sqlite3"
    before = path.read_bytes()
    inode = path.stat().st_ino
    with a.journal.opening(raw_request=raw, raw_record=record):
        pass
    after = path.read_bytes()
    path.write_bytes(before)
    path.chmod(0o600)
    assert path.stat().st_ino == inode
    try:
        with pytest.raises(sql.AuthorityError, match="COORDINATION_ROLLBACK"):
            a.journal.read()
        with pytest.raises(sql.AuthorityError, match="COORDINATION_ROLLBACK"):
            coord.CoordinationJournal(**a.options).read()
    finally:
        path.write_bytes(after)
        path.chmod(0o600)
    assert a.journal.read()["active"]


def test_b3_publication_requires_actual_coordinator_lease_and_grant(rig, initial_artifacts):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        commit = existing_open_sources(a, attempt, initial_artifacts)
        authenticated = a.journal.authenticate(commit, "commit-open")
        prepared = a.sources.qualify_open(authenticated_request=authenticated, opening=attempt)
        guard = coord._OpeningPublicationVerifier(attempt, authenticated, prepared)
        with pytest.raises(sql.AuthorityError, match="B4_CAPABILITY_REQUIRED"):
            guard.verify(
                authority_id=b.AUTH_ID,
                epoch=1,
                operation_id=authenticated.request.to_dict()["request_id"],
                revision=1,
                action="RUN_OPENED",
                proof=True,
                candidate_state=sql.canonical_bytes(a.f.state),
            )
        original = attempt.receipt
        attempt.receipt = {"state": "PENDING"}
        with pytest.raises(sql.AuthorityError, match="REVISION_STALE"):
            attempt.check()
        attempt.receipt = original
    with pytest.raises(sql.AuthorityError):
        attempt.complete_open(raw_request=commit)
    assert a.b3_transport.calls.count("B3_CAS") == 0


@pytest.mark.parametrize(
    "rig",
    [
        ("PASS",),
        ("FAIL",),
        ("OPEN",),
        ("ACTIVE",),
        ("UNCERTAIN",),
        ("PASS", "ACTIVE"),
        ("FAIL", "UNCERTAIN"),
    ],
    indirect=True,
)
def test_fully_verified_global_history_non_none_denies_before_claim(rig):
    a = rig
    raw, record = proposal(a)
    snapshot = a.sources.read_authority()
    assert history.first_valid_status(snapshot)["status"] != "NONE"
    initial = a.f.database.read_bytes()
    with pytest.raises(sql.AuthorityError, match="FIRST_VALID_BLOCKED"):
        with a.journal.opening(raw_request=raw, raw_record=record):
            pytest.fail("consumed/blocking global history")
    assert a.journal.read()["claims"] == {} and a.transport.calls.count("RESERVE") == 0
    assert a.f.database.read_bytes() == initial


@pytest.mark.parametrize("rig", [(), ("ND", "ND")], indirect=True)
def test_complete_empty_or_multi_nd_history_accepts_exact_ack(rig):
    a = rig
    raw, record = proposal(a)
    request = wire.parse_request_bytes(raw).to_dict()
    assert len(request["payload"]["acknowledgments"]) == len(a.f.state["official_runs"])
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        attempt.check()
    assert a.transport.calls.count("RESERVE") == 1


@pytest.mark.parametrize(
    "change", ["omit-one", "unknown", "wrong-reference", "wrong-revision", "duplicate"]
)
@pytest.mark.parametrize("rig", [("ND", "ND")], indirect=True)
def test_multi_nd_ack_set_rejects_omission_extra_and_substitution(rig, change):
    a = rig
    raw, _ = proposal(a, "initial")
    acks = copy.deepcopy(wire.parse_request_bytes(raw).to_dict()["payload"]["acknowledgments"])
    if change == "omit-one":
        acks.pop()
    elif change == "unknown":
        acks[0]["run_id"] = "s11-" + str(uuid.uuid5(uuid.NAMESPACE_URL, "unknown-nd"))
    elif change == "wrong-reference":
        acks[0]["terminal_evidence_reference"]["sha256"] = "f" * 64
    elif change == "wrong-revision":
        acks[0]["authority_revision"] = 1
    else:
        acks.append(copy.deepcopy(acks[0]))
    with pytest.raises((sql.AuthorityError, wire.BridgeProtocolError)):
        raw, record = proposal(a, "changed", ack=acks)
        with a.journal.opening(raw_request=raw, raw_record=record):
            pytest.fail("bad exact ACK set")
    assert a.journal.read()["claims"] == {} and a.transport.calls.count("RESERVE") == 0


def resign_and_admit(a, raw, change):
    value = wire.parse_request_bytes(raw).to_dict()
    old = a.sources.approved[hashlib.sha256(raw).hexdigest()]
    change(value)
    cap = value["capability"]
    cap.update(request_id=value["request_id"], request_nonce=value["nonce"])
    encoded = builder.signed_request(value, a.bridge, a.holder)
    value = wire.parse_request_bytes(encoded).to_dict()
    context = replace(
        old,
        request_id=value["request_id"],
        nonce=value["nonce"],
        expected_payload=sql.canonical_bytes(value["payload"]),
    )
    a.sources.approved[hashlib.sha256(encoded).hexdigest()] = context
    return encoded


@pytest.mark.parametrize("change", ["nonce-new-id", "same-id-changed-body", "same-nonce-new-id"])
def test_claim_id_nonce_body_one_to_one_conflict_never_dispatches(rig, change):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record):
        pass
    if change == "same-id-changed-body":

        def mutate(v):
            # Keep UUID5 identity valid while changing a signed payload reference.
            v["payload"]["preflight_reference"].update(sha256="f" * 64)
    elif change == "same-nonce-new-id":

        def mutate(v):
            v.update(request_id=str(uuid.uuid4()))
    else:

        def mutate(v):
            v.update(request_id=str(uuid.uuid4()), nonce=b.hashed("new-nonce"))
            identity = v["payload"]["run_identity"]
            identity["execution_nonce"] = v["nonce"]
            identity["run_id"] = wire.make_run_id(
                started_at=sql.utc_time(identity["started_at"]).replace(tzinfo=UTC),
                git_sha=identity["git_sha"],
                dataset_id=identity["dataset_id"],
                scenario_id=identity["scenario_id"],
                seed=identity["seed"],
                execution_nonce=identity["execution_nonce"],
            )
            identity["identity_digest"] = sql.digest(
                {k: x for k, x in identity.items() if k != "identity_digest"}
            )

    if change == "nonce-new-id":
        # New identity still cannot bypass the existing durable global pending slot.
        modified, modified_record = proposal(a, "new-independent", 2)
        with pytest.raises(sql.AuthorityError, match="RESERVATION_BUSY"):
            with a.journal.opening(raw_request=modified, raw_record=modified_record):
                pytest.fail("new bypass")
    else:
        modified = resign_and_admit(a, raw, mutate)
        value = wire.qualify_reservation(record).to_dict()
        req = wire.parse_request_bytes(modified).to_dict()
        value.update(request_id=req["request_id"], request_digest=req["request_digest"])
        modified_record = sql.canonical_bytes(coord._seal_record(value))
        with pytest.raises(sql.AuthorityError, match="REPLAY_CONFLICT"):
            with a.journal.opening(raw_request=modified, raw_record=modified_record):
                pytest.fail("claim remap")
    assert a.transport.calls.count("RESERVE") == 1 and len(a.journal.read()["claims"]) == 1


def approve_owner_loss(a, record, proc, operation_id, existing_receipt=None):
    assert proc.exitcode is not None and not proc.is_alive()
    path = a.stores["receipts"].root + "/owner-loss-" + operation_id + ".json"
    body = sql.canonical_bytes(
        {
            "schema_version": "s11-test-owner-loss-v1",
            "process": proc.pid,
            "observed_exit_code": proc.exitcode,
        }
    )
    a.stores["receipts"].create_exact(path, body)
    reference = {
        "path": path,
        "sha256": hashlib.sha256(body).hexdigest(),
        "schema_version": "s11-test-owner-loss-v1",
    }
    approval = b.reference("external-test-approved-owner-recording-" + operation_id)
    expected = {
        "operation_id": operation_id,
        "reservation_id": record["reservation_id"],
        "expected_record_sequence": record["record_sequence"],
        "expected_record_digest": record["record_digest"],
        "reason_code": "OWNER_DIED",
        "observation_reference": reference,
        "operator_authorization_reference": approval,
    }
    previous = getattr(a.sources, "owner_observations", {})
    previous[operation_id] = sql.canonical_bytes(expected)
    a.sources.owner_observations = previous

    def verify_owner_loss(*, canonical_observation, original_record):
        value = sql.parse_canonical(canonical_observation)
        sql.require(
            a.sources.owner_observations.get(value["operation_id"]) == canonical_observation
            and original_record["record_digest"] == value["expected_record_digest"],
            "TRUST_UNAPPROVED",
        )
        ref = value["observation_reference"]
        observed = a.stores["receipts"].read(ref["path"])
        sql.require(hashlib.sha256(observed).hexdigest() == ref["sha256"], "TRUST_UNAPPROVED")

    def qualify_owner_loss(
        *, operation_id, record, observation_reference, writer, coordination_head
    ):
        writer.check()
        sql.require(
            operation_id == expected["operation_id"]
            and record["record_digest"] == expected["expected_record_digest"]
            and sql.parse_canonical(observation_reference) == reference
            and proc.exitcode is not None
            and not proc.is_alive(),
            "TRUST_UNAPPROVED",
        )
        return coord.OwnerLossQualification(
            sql.canonical_bytes(approval),
            record["record_sequence"],
            record["record_digest"],
            "OWNER_DIED",
            existing_receipt,
        )

    a.sources.qualify_owner_loss = qualify_owner_loss
    a.sources.verify_owner_loss = verify_owner_loss
    return sql.canonical_bytes(reference)


@pytest.mark.parametrize("phase", ["C3-local", "C3-remote-unknown", "C4-grant", "B4-floor-lag"])
def test_explicit_host_owner_loss_recording_recovers_pending_without_retry(rig, phase):
    a = rig
    raw, record = proposal(a)

    def die():
        if phase == "C3-local":
            a.transport.reserve = lambda *args, **kwargs: os._exit(61)
        if phase == "C3-remote-unknown":
            send = a.transport.reserve

            def after_send(*args, **kwargs):
                send(*args, **kwargs)
                os._exit(62)

            a.transport.reserve = after_send
        if phase == "B4-floor-lag":
            advance = a.floor.advance

            def after_floor(*args, **kwargs):
                advance(*args, **kwargs)
                os._exit(64)

            a.floor.advance = after_floor
        with a.journal.opening(raw_request=raw, raw_record=record):
            os._exit(63)

    proc = multiprocessing.get_context("fork").Process(target=die)
    proc.start()
    proc.join(15)
    if proc.is_alive():
        proc.terminate()
        proc.join()
        pytest.fail("owner phase not bounded")
    assert (
        proc.exitcode
        == {"C3-local": 61, "C3-remote-unknown": 62, "C4-grant": 63, "B4-floor-lag": 64}[phase]
    )
    journal = coord.CoordinationJournal(**a.options)
    if phase == "B4-floor-lag":
        with pytest.raises(sql.AuthorityError, match="COORDINATION_ROLLBACK"):
            journal.read()
    state = journal._local_state()
    rid = state["active"]
    pending = state["latest"][rid]
    existing = None
    if phase in ("C3-remote-unknown", "B4-floor-lag"):
        with a.remote._database() as con:
            con.execute("BEGIN")
            existing = sql.canonical_bytes(a.remote._read(con)["receipts"][-1])
    op = str(uuid.uuid5(uuid.NAMESPACE_URL, "owner-loss-" + phase))
    observation = approve_owner_loss(a, pending, proc, op, existing)
    result = journal.record_owner_uncertain(
        reservation_id=rid, operation_id=op, observation_reference=observation
    )
    assert result["state"] == "UNCERTAIN" and result["uncertainty_codes"] == ["OWNER_DIED"]
    state = journal.read()
    assert state["active"] == rid and len(state["claims"]) == 1
    assert state["sequence"] == (0 if phase == "C3-local" else 2)
    assert (
        len(state["observations"]) == 1 and a.transport.calls.count("RESERVE") == 0
    )  # Child-only grant already happened.
    assert a.transport.calls.count("RECORD_UNCERTAIN") == (0 if phase == "C3-local" else 1)
    before = copy.deepcopy(state)
    journal.record_owner_uncertain(
        reservation_id=rid, operation_id=op, observation_reference=observation
    )
    assert journal.read() == before
    assert a.transport.calls.count("RECORD_UNCERTAIN") == (0 if phase == "C3-local" else 1)
    # External recording approval grants no new OPEN. Same original request is recognition only.
    with journal.opening(raw_request=raw, raw_record=record) as duplicate:
        assert duplicate.state == "UNCERTAIN"


def test_owner_loss_recording_rejects_serialized_boolean_without_mutation(rig):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record):
        pass
    state = a.journal.read()
    rid = state["active"]
    a.sources.qualify_owner_loss = lambda **kwargs: True
    with pytest.raises(sql.AuthorityError, match="TRUST_UNAPPROVED"):
        a.journal.record_owner_uncertain(
            reservation_id=rid,
            operation_id=str(uuid.uuid4()),
            observation_reference=sql.canonical_bytes(b.reference("unqualified-owner")),
        )
    assert a.journal.read() == state and a.transport.calls.count("RECORD_UNCERTAIN") == 0


def test_admin_capability_expiration_during_qualifier_writes_no_claim_or_cas(rig):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        mark_alive_uncertain(a, attempt)
        uncertain = copy.deepcopy(attempt.record)
    recovery = approved_cancel(
        a,
        uncertain,
        change=lambda v: v.update(expires_at=coord._utc(a.now() + timedelta(seconds=1))),
    )
    qualified = a.sources.qualify_recovery
    before = a.journal.read()
    original_now = a.now()

    def delayed(**kwargs):
        result = qualified(**kwargs)
        a.now = lambda: original_now + timedelta(seconds=2)
        a.journal.clock = a.now
        a.sources.now = a.now
        return result

    a.sources.qualify_recovery = delayed
    with pytest.raises(wire.BridgeProtocolError, match="EXPIRED"):
        a.journal.recover(raw_request=recovery)
    assert a.journal.read() == before and a.transport.calls.count("CANCEL_MANUAL") == 0


def full_existing_candidate(a, commit):
    """Reconstruct only the durable original candidate and independently existing receipt."""
    base = sql._load_database(a.f.database)
    request = wire.parse_request_bytes(commit).to_dict()
    intent = next(
        i for i in base["publication_intents"] if i["operation_id"] == request["request_id"]
    )
    projection = sql.parse_canonical(a.stores["candidate"].read(intent["candidate_payload_path"]))
    assert sql.digest(projection) == intent["candidate_payload_sha256"]
    target = copy.deepcopy(base)
    for table in sql.APPROVED_SCHEMA["logical_digest"]["fields"]:
        if table in ("schema_version", "revision"):
            continue
        if table in ("metadata", "bootstrap_manifest"):
            target[table] = [copy.deepcopy(projection[table])]
        elif table == "history_revisions":
            previous = {row["revision"]: row["ledger_digest"] for row in base[table]}
            target[table] = [
                {
                    **row,
                    "ledger_digest": previous.get(
                        row["revision"], intent["candidate_ledger_digest"]
                    ),
                }
                for row in projection[table]
            ]
        else:
            target[table] = copy.deepcopy(projection[table])
    receipt = a.remote.inspect_b3_operation(request["request_id"], credentials=a.auth.credential)
    assert receipt is not None
    if not any(c["revision"] == receipt["revision"] for c in target["witness_checkpoints"]):
        target["witness_checkpoints"].append(history._checkpoint(receipt))
    challenge = b.hashed("manual-approved-inspection-" + request["request_id"])
    observation = {
        "head": a.remote.head_b3(challenge, credentials=a.auth.credential),
        "challenge": challenge,
        "now": coord._utc(a.now()),
        "request_elapsed_seconds": 0,
        "clock_healthy": True,
    }
    if not any(
        r["operation_id"] == request["request_id"] for r in target["publication_resolutions"]
    ):
        target["publication_resolutions"].append(
            {
                "operation_id": request["request_id"],
                "disposition": "COMMITTED",
                "resolved_at": coord._utc(a.now()),
                "committed_revision": receipt["revision"],
                "receipt_digest": sql.digest(receipt),
                "operator_approval_path": None,
                "operator_approval_sha256": None,
                "operator_approval_schema": None,
                "fresh_witness_observation_digest": sql.digest(observation["head"]),
            }
        )
    trust = a.f.trust()
    trust["intent_payload_reader"] = a.sources.intent_reader
    verified = a.f.verify(state=target, trust=trust, observation=observation)
    open_ref = {
        "authority_id": b.AUTH_ID,
        "authority_epoch": 1,
        "revision": receipt["revision"],
        "ledger_digest": receipt["ledger_digest"],
        "witness_digest": sql.digest(receipt),
        "receipt_reference": {
            "path": a.b3_floor._path(receipt["revision"]),
            "sha256": sql.digest(receipt),
            "schema_version": receipt["witness_schema_version"],
        },
    }
    completion = coord.ManualAuthorityCompletion(
        a.stores["ledger"],
        "ledger.sqlite3",
        h.identity(a.f.database),
        sql.canonical_bytes(base),
        a.stores["candidate"],
        intent["candidate_payload_path"],
        a.b3_floor,
        request["request_id"],
    )
    return verified, open_ref, completion


def approved_adopt(
    a,
    record,
    verified,
    open_ref,
    evidence_ref,
    accepted,
    *,
    completion=None,
    existing_receipt=None,
    change=None,
):
    sample = builder.sample_for("resolve-uncertain-as-committed")
    value = wire.parse_request_bytes(sample["raw"]).to_dict()
    operator = Ed25519PrivateKey.generate()
    now = a.now()
    current = history.current_revision(verified)
    head = a.transport.head(b.hashed("manual-adopt-coordination-challenge"))
    value.update(
        request_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "exact-manual-adopt")),
        nonce=b.hashed("exact-manual-adopt-nonce"),
        authority_id=b.AUTH_ID,
        authority_epoch=1,
        authority_revision=current["revision"],
        issued_at=coord._utc(now),
        expires_at=coord._utc(now + timedelta(seconds=120)),
        session_id=record["session_id"],
        session_header_sha256=record["session_header_sha256"],
        runtime_profile_sha256=record["runtime_profile_sha256"],
        source_revision=record["source_revision"],
        project=record["project"],
    )
    payload = value["payload"]
    payload.update(
        reservation_id=record["reservation_id"],
        expected_state="UNCERTAIN",
        expected_record_digest=record["record_digest"],
        expected_record_sequence=record["record_sequence"],
        run_identity=record["run_identity"],
        open_evidence=accepted.receipt.to_dict(),
        existing_open_witness_reference=copy.deepcopy(open_ref),
        fresh_authority_head=copy.deepcopy(open_ref),
        fresh_coordination_head=head,
        operator_authorization_reference=b.reference("approved-explicit-adopt"),
        idempotency_token=b.hashed("manual-adopt-lifetime-token"),
        evidence_references=[evidence_ref, b.reference("stopped-writer")],
    )
    if change is not None:
        change(value)
    cap = value["capability"]
    for field in (
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
    ):
        cap[field] = copy.deepcopy(value[field])
    cap.update(
        request_nonce=value["nonce"],
        expected_revision=value["authority_revision"],
        expected_run_id=record["run_id"],
        reservation_id=record["reservation_id"],
        reservation_digest=record["reservation_digest"],
        fencing_token=record["fencing_token"],
        holder_public_key_hex=operator.public_key().public_bytes_raw().hex(),
        issued_at=value["issued_at"],
        expires_at=value["expires_at"],
        bridge_key_id=a.bridge_pin.key_id,
        bridge_key_epoch=a.bridge_pin.key_epoch,
    )
    raw = builder.signed_request(value, a.bridge, operator)
    value = wire.parse_request_bytes(raw).to_dict()
    context = wire.RecoveryRequestContext(
        **{
            k: value[k]
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
        authorization_reference=sql.canonical_bytes(value["authorization_reference"]),
        deployment_verification_sha256=cap["deployment_verification_sha256"],
        container_identity_digest=cap["container_identity_digest"],
        expected_run_id=record["run_id"],
        reservation_id=record["reservation_id"],
        reservation_digest=record["reservation_digest"],
        fencing_token=record["fencing_token"],
        holder_public_key_hex=cap["holder_public_key_hex"],
        expected_payload=sql.canonical_bytes(value["payload"]),
        now=now,
        operator_authorization_reference=sql.canonical_bytes(
            payload["operator_authorization_reference"]
        ),
    )
    a.sources.recovery_approved[hashlib.sha256(raw).hexdigest()] = context

    def qualify(*, authenticated_request, record, writer, coordination_head):
        writer.check()
        sql.require(authenticated_request.request.to_dict() == value, "TRUST_UNAPPROVED")
        approval = service.RecoveryApproval(
            value["request_id"],
            record["reservation_id"],
            coordination_head["receipt_digest"],
            sql.canonical_bytes(payload["operator_authorization_reference"]),
            sql.canonical_bytes(b.reference("stopped-writer")),
            None,
        )
        return coord.RecoveryQualification(
            approval,
            verified,
            completion,
            existing_receipt,
            sql.canonical_bytes(open_ref),
            sql.canonical_bytes(evidence_ref),
            accepted,
            authority_floor=a.b3_floor,
        )

    a.sources.qualify_recovery = qualify
    return raw


def test_manual_adopts_exact_candidate_after_remote_open_loss_without_republication(
    rig, initial_artifacts
):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        commit = existing_open_sources(a, attempt, initial_artifacts)
        a.b3_transport.fail = "response"
        with pytest.raises(sql.AuthorityError, match="UNCERTAIN_PUBLICATION"):
            attempt.complete_open(raw_request=commit)
        assert a.b3_transport.calls.count("B3_CAS") == 1
        mark_alive_uncertain(a, attempt)
        uncertain = copy.deepcopy(attempt.record)
    verified, open_ref, completion = full_existing_candidate(a, commit)
    prepared = a.prepared_open
    evidence_ref = sql.parse_canonical(prepared.evidence_reference)
    recovery = approved_adopt(
        a,
        uncertain,
        verified,
        open_ref,
        evidence_ref,
        prepared.open_evidence,
        completion=completion,
    )
    candidate = Path(completion.candidate_path).read_bytes()
    result = a.journal.recover(raw_request=recovery)
    assert result["state"] == "COMMITTED" and result["run_id"] == uncertain["run_id"]
    assert (
        a.b3_transport.calls.count("B3_CAS") == 1
        and a.transport.calls.count("ADOPT_OPEN_MANUAL") == 1
    )
    assert Path(completion.candidate_path).read_bytes() == candidate
    assert a.b3_floor.current().revision == 1 and a.floor.current()["sequence"] == 3
    assert (
        history.find_run(a.sources.read_authority(), uncertain["run_id"])["lifecycle_state"]
        == "OPEN"
    )
    before = a.journal.read()
    duplicate = a.journal.recover(raw_request=recovery)
    assert duplicate.state == "COMMITTED" and a.journal.read() == before
    assert (
        a.b3_transport.calls.count("B3_CAS") == 1
        and a.transport.calls.count("ADOPT_OPEN_MANUAL") == 1
    )


@pytest.mark.parametrize("change", ["run-identity", "ledger-digest", "record-digest"])
def test_manual_adoption_wrong_target_rejected_before_any_ledger_write(
    rig, initial_artifacts, change
):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        commit = existing_open_sources(a, attempt, initial_artifacts)
        a.b3_transport.fail = "response"
        with pytest.raises(sql.AuthorityError):
            attempt.complete_open(raw_request=commit)
        mark_alive_uncertain(a, attempt)
        uncertain = copy.deepcopy(attempt.record)
    verified, open_ref, completion = full_existing_candidate(a, commit)

    def alter(value):
        if change == "ledger-digest":
            value["payload"]["existing_open_witness_reference"]["ledger_digest"] = "f" * 64
        elif change == "record-digest":
            value["payload"]["expected_record_digest"] = "f" * 64
        else:
            identity = value["payload"]["run_identity"]
            identity["execution_nonce"] = "f" * 64
            identity["run_id"] = wire.make_run_id(
                started_at=sql.utc_time(identity["started_at"]).replace(tzinfo=UTC),
                git_sha=identity["git_sha"],
                dataset_id=identity["dataset_id"],
                scenario_id=identity["scenario_id"],
                seed=identity["seed"],
                execution_nonce=identity["execution_nonce"],
            )
            identity["identity_digest"] = sql.digest(
                {k: v for k, v in identity.items() if k != "identity_digest"}
            )

    recovery = approved_adopt(
        a,
        uncertain,
        verified,
        open_ref,
        sql.parse_canonical(a.prepared_open.evidence_reference),
        a.prepared_open.open_evidence,
        completion=completion,
        change=alter,
    )
    ledger = a.f.database.read_bytes()
    state = a.journal.read()
    with pytest.raises((sql.AuthorityError, wire.BridgeProtocolError)):
        a.journal.recover(raw_request=recovery)
    assert a.f.database.read_bytes() == ledger and a.journal.read() == state
    assert a.b3_floor.current().revision == 0 and a.b3_transport.calls.count("B3_CAS") == 1
    assert a.transport.calls.count("ADOPT_OPEN_MANUAL") == 0


def test_manual_reuses_existing_committed_b4_receipt_after_response_loss_without_second_cas(
    rig, initial_artifacts
):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        commit = existing_open_sources(a, attempt, initial_artifacts)
        transition = a.transport.transition

        def unknown(**kwargs):
            receipt = transition(**kwargs)
            if kwargs["transition"] == "COMMIT_OPEN":
                raise TimeoutError("lost commit response")
            return receipt

        a.transport.transition = unknown
        with pytest.raises(sql.AuthorityError, match="UNCERTAIN_PUBLICATION"):
            attempt.complete_open(raw_request=commit)
        with pytest.raises(sql.AuthorityError, match="COORDINATION_ROLLBACK"):
            mark_alive_uncertain(a, attempt)
        uncertain = copy.deepcopy(attempt.record)
    # B3 is already fully committed and floored; no local B3 mutation is required.
    verified = a.sources.read_authority()
    receipt = a.remote.inspect_b3_operation(
        wire.parse_request_bytes(commit).to_dict()["request_id"], credentials=a.auth.credential
    )
    open_ref = {
        "authority_id": b.AUTH_ID,
        "authority_epoch": 1,
        "revision": 1,
        "ledger_digest": receipt["ledger_digest"],
        "witness_digest": sql.digest(receipt),
        "receipt_reference": coord._reference(a.b3_floor._path(1), receipt),
    }
    with a.remote._database() as con:
        con.execute("BEGIN")
        existing = sql.canonical_bytes(a.remote._read(con)["receipts"][-1])
    recovery = approved_adopt(
        a,
        uncertain,
        verified,
        open_ref,
        sql.parse_canonical(a.prepared_open.evidence_reference),
        a.prepared_open.open_evidence,
        existing_receipt=existing,
    )
    ledger = a.f.database.read_bytes()
    result = a.journal.recover(raw_request=recovery)
    assert result["state"] == "COMMITTED" and a.f.database.read_bytes() == ledger
    assert (
        a.transport.calls.count("COMMIT_OPEN") == 1
        and a.transport.calls.count("ADOPT_OPEN_MANUAL") == 0
    )
    assert a.b3_transport.calls.count("B3_CAS") == 1 and a.floor.current()["sequence"] == 2


def test_pending_b3_manual_cancel_permanently_closes_authority_and_preserves_candidate(
    rig, initial_artifacts
):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        commit = existing_open_sources(a, attempt, initial_artifacts)
        a.b3_transport.fail = "before"
        with pytest.raises(sql.AuthorityError, match="UNCERTAIN_PUBLICATION"):
            attempt.complete_open(raw_request=commit)
        mark_alive_uncertain(a, attempt)
        uncertain = copy.deepcopy(attempt.record)
    recovery = approved_cancel(a, uncertain)
    payload = wire.parse_request_bytes(recovery).to_dict()["payload"]
    base = sql._load_database(a.f.database)
    target = copy.deepcopy(base)
    original = wire.parse_request_bytes(commit).to_dict()["request_id"]
    snapshot = a.sources.read_authority()
    target["publication_resolutions"].append(
        {
            "operation_id": original,
            "disposition": "MANUAL_CANCELLED",
            "resolved_at": coord._utc(a.now()),
            "committed_revision": None,
            "receipt_digest": None,
            "operator_approval_path": payload["operator_authorization_reference"]["path"],
            "operator_approval_sha256": payload["operator_authorization_reference"]["sha256"],
            "operator_approval_schema": payload["operator_authorization_reference"][
                "schema_version"
            ],
            "fresh_witness_observation_digest": sql.digest(
                sql.parse_canonical(snapshot.canonical_current_head)
            ),
        }
    )
    trust = a.f.trust()
    trust["intent_payload_reader"] = a.sources.intent_reader
    verified = a.f.verify(
        state=target,
        trust=trust,
        observation={
            "head": sql.parse_canonical(snapshot.canonical_current_head),
            "challenge": sql.parse_canonical(snapshot.canonical_current_head)["challenge"],
            "now": coord._utc(a.now()),
            "request_elapsed_seconds": 0,
            "clock_healthy": True,
        },
    )
    assert verified.write_closed is True
    candidate = a.stores["candidate"].root + "/exact-open.json"
    retained = Path(candidate).read_bytes()
    completion = coord.ManualAuthorityCompletion(
        a.stores["ledger"],
        "ledger.sqlite3",
        h.identity(a.f.database),
        sql.canonical_bytes(base),
        a.stores["candidate"],
        candidate,
        a.b3_floor,
        original,
    )

    def qualified(*, authenticated_request, record, writer, coordination_head):
        writer.check()
        assert authenticated_request.request.canonical == recovery
        approval = service.RecoveryApproval(
            authenticated_request.request.to_dict()["request_id"],
            record["reservation_id"],
            coordination_head["receipt_digest"],
            sql.canonical_bytes(payload["operator_authorization_reference"]),
            sql.canonical_bytes(payload["writer_closure_reference"]),
            sql.canonical_bytes(payload["no_open_certificate_reference"]),
        )
        return coord.RecoveryQualification(approval, verified, authority_completion=completion)

    a.sources.qualify_recovery = qualified
    result = a.journal.recover(raw_request=recovery)
    assert result["state"] == "CANCELLED_MANUALLY" and a.journal.read()["active"] is None
    assert (
        Path(candidate).read_bytes() == retained
        and a.existing_evidence.path.read_bytes() == a.existing_evidence.raw
    )
    assert a.b3_transport.calls.count("B3_CAS") == 1 and a.b3_floor.current().revision == 0
    current = a.sources.read_authority()
    assert (
        current.write_closed is True
        and history.first_valid_status(current)["status"] == "UNCERTAIN_BLOCKER"
    )
    new_request, new_record = proposal(a, "after-manual-permanent-close", 2)
    with pytest.raises(sql.AuthorityError, match="FIRST_VALID_BLOCKED"):
        with a.journal.opening(raw_request=new_request, raw_record=new_record):
            pytest.fail("manual authority reopened")
    assert a.transport.calls.count("RESERVE") == 1


def test_terminal_retirement_rejects_open_or_unqualified_snapshot_without_freeing_slot(
    rig, initial_artifacts
):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        commit = existing_open_sources(a, attempt, initial_artifacts)
        attempt.complete_open(raw_request=commit)
    snapshot = a.sources.read_authority()
    receipt = a.remote.inspect_b3_operation(
        wire.parse_request_bytes(commit).to_dict()["request_id"], credentials=a.auth.credential
    )
    ref = {
        "authority_id": b.AUTH_ID,
        "authority_epoch": 1,
        "revision": 1,
        "ledger_digest": receipt["ledger_digest"],
        "witness_digest": sql.digest(receipt),
        "receipt_reference": coord._reference(a.b3_floor._path(1), receipt),
    }
    a.sources.qualify_terminal = lambda **kwargs: coord.TerminalQualification(
        snapshot, sql.canonical_bytes(ref), a.b3_floor
    )
    state = a.journal.read()
    with pytest.raises(sql.AuthorityError, match="REFERENCE_MISMATCH"):
        a.journal.retire_terminal(reservation_id=state["active"], operation_id=str(uuid.uuid4()))
    assert a.journal.read() == state and a.transport.calls.count("RETIRE_TERMINAL") == 0


def publish_synthetic_terminal(a, record, kind):
    """Existing B3 primitive + actual W04 adapter; zero experimental traffic."""
    before = a.sources.read_authority()
    run = copy.deepcopy(history.find_run(before, record["run_id"]))
    run.update(
        run_version=1,
        previous_run_version=0,
        introduced_revision=2,
        lifecycle_state="TERMINAL",
        phase="FINALIZED",
        result="NOT_DEMONSTRATED" if kind == "ND" else kind,
        binding=kind != "ND",
        ack_required=kind == "ND",
        validity_classification="NON_BINDING_ND" if kind == "ND" else "VALID_BINDING",
        finalized_at=coord._utc(a.now()),
    )
    event = a.f.event(
        "RUN_TERMINAL",
        sequence=len(before.material["state"]["authority_events"]) + 1,
        run=run,
        revision=2,
    )
    event.update(
        evidence_reference_id=run["evidence_authority_reference_id"],
        occurred_at=run["finalized_at"],
    )
    op = str(uuid.uuid5(uuid.NAMESPACE_URL, "terminal-" + kind))
    path = a.stores["candidate"].root + "/terminal-" + kind + ".json"
    previous_reader = a.sources.intent_reader
    a.sources.intent_reader = (
        lambda candidate: a.stores["candidate"].read(candidate)
        if candidate == path
        else previous_reader(candidate)
    )
    with backend.HeldWriter(a.stores["lock"], "global.lock", h.identity(a.lock)) as writer:
        adapter = backend.AuthorityBackend(
            ledger_store=a.stores["ledger"],
            ledger_name="ledger.sqlite3",
            ledger_identity=h.identity(a.f.database),
            candidate_store=a.stores["candidate"],
            floor=a.b3_floor,
            writer=writer,
            transport=a.b3_transport,
            signer_pin=a.f.pin,
            clock_health=lambda: True,
        )
        args = a.f.publication_args(adapter, "RUN_TERMINAL")
        args.update(
            operation_id=op,
            occurred_at=event["occurred_at"],
            evidence_reference_id=run["evidence_authority_reference_id"],
            candidate_payload_path=path,
        )
        args["capability"].update(operation_id=op, expected_revision=2)
        result = history._append_run_terminal(before, run, event, **args)
    receipt = a.remote.inspect_b3_operation(op, credentials=a.auth.credential)
    ref = {
        "authority_id": b.AUTH_ID,
        "authority_epoch": 1,
        "revision": 2,
        "ledger_digest": receipt["ledger_digest"],
        "witness_digest": sql.digest(receipt),
        "receipt_reference": coord._reference(a.b3_floor._path(2), receipt),
    }

    def qualify_terminal(*, operation_id, record, writer):
        writer.check()
        assert history.find_run(result, record["run_id"])["lifecycle_state"] == "TERMINAL"
        return coord.TerminalQualification(result, sql.canonical_bytes(ref), a.b3_floor)

    a.sources.qualify_terminal = qualify_terminal
    return result


@pytest.mark.parametrize("kind", ["ND", "PASS", "FAIL"])
def test_actual_authenticated_terminal_retires_slot_but_next_attempt_reruns_first_valid_and_ack(
    rig, initial_artifacts, kind
):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        commit = existing_open_sources(a, attempt, initial_artifacts)
        attempt.complete_open(raw_request=commit)
        committed = copy.deepcopy(attempt.record)
    publish_synthetic_terminal(a, committed, kind)
    operation = str(uuid.uuid5(uuid.NAMESPACE_URL, "retire-" + kind))
    retired = a.journal.retire_terminal(
        reservation_id=committed["reservation_id"], operation_id=operation
    )
    assert (
        retired["state"] == "COMMITTED" and retired["terminal_witness_reference"]["revision"] == 2
    )
    state = a.journal.read()
    assert (
        state["active"] is None
        and state["witness_active"] is None
        and state["sequence"] == 3
        and state["token"] == 1
    )
    assert len(state["claims"]) == 2 and len(state["registry"]) == 1
    assert a.transport.calls.count("RETIRE_TERMINAL") == 1
    assert (
        a.journal.retire_terminal(
            reservation_id=committed["reservation_id"], operation_id=operation
        )
        == retired
    )
    assert a.transport.calls.count("RETIRE_TERMINAL") == 1
    new_request, new_record = proposal(a, "after-terminal-" + kind, 2)
    if kind == "ND":
        assert (
            len(wire.parse_request_bytes(new_request).to_dict()["payload"]["acknowledgments"]) == 2
        )
        with a.journal.opening(raw_request=new_request, raw_record=new_record) as next_attempt:
            next_attempt.check()
        assert a.journal.read()["token"] == 2 and a.transport.calls.count("RESERVE") == 2
    else:
        with pytest.raises(sql.AuthorityError, match="FIRST_VALID_BLOCKED"):
            with a.journal.opening(raw_request=new_request, raw_record=new_record):
                pytest.fail("binding bypass after retirement")
        assert a.transport.calls.count("RESERVE") == 1


@pytest.mark.parametrize("kind", ["PASS", "FAIL", "ND-extra"])
def test_under_lock_second_verified_history_recheck_denies_before_claim(rig, kind):
    a = rig
    raw, record = proposal(a)
    root = a.root / "s11-b3-test-second-qualified-history"
    root.mkdir(mode=0o700)
    f = ExactAckFixture(root, ("ND", "ND") if kind == "ND-extra" else (kind,))
    changed = f.verify()
    original = a.sources.read_authority
    calls = []

    def latest():
        calls.append(1)
        return original() if len(calls) == 1 else changed

    a.sources.read_authority = latest
    with pytest.raises(sql.AuthorityError):
        with a.journal.opening(raw_request=raw, raw_record=record):
            pytest.fail("stale preliminary eligibility")
    assert len(calls) == 2 and a.journal.read()["claims"] == {}
    assert a.transport.calls.count("RESERVE") == 0


def test_forged_pending_terminal_reference_cannot_release_global_slot(rig):
    a = rig
    raw, record = proposal(a)
    a.transport.fail = "before"
    with pytest.raises(sql.AuthorityError):
        with a.journal.opening(raw_request=raw, raw_record=record):
            pytest.fail("grant failed")
    state = a.journal.read()
    prior = state["latest"][state["active"]]
    forged = copy.deepcopy(prior)
    forged.update(
        record_sequence=2,
        previous_reservation_record_digest=prior["record_digest"],
        terminal_witness_reference={
            "authority_id": b.AUTH_ID,
            "authority_epoch": 1,
            "revision": 1,
            "ledger_digest": "1" * 64,
            "witness_digest": "2" * 64,
            "receipt_reference": b.reference("forged-terminal"),
        },
    )
    forged = coord._seal_record(forged)
    with a.journal._database(write=True) as con:
        con.execute("BEGIN IMMEDIATE")
        a.journal._append_event(con, state, forged)
        con.commit()
    with pytest.raises(sql.AuthorityError, match="HISTORY_INVALID"):
        a.journal.read()
    assert a.floor.current()["sequence"] == 0 and a.transport.calls.count("RESERVE") == 1


def test_swapped_claim_reservation_mapping_rejected_despite_valid_signatures(
    rig, initial_artifacts
):
    import sqlite3

    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        commit = existing_open_sources(a, attempt, initial_artifacts)
        attempt.complete_open(raw_request=commit)
        first = copy.deepcopy(attempt.record)
    publish_synthetic_terminal(a, first, "ND")
    a.journal.retire_terminal(
        reservation_id=first["reservation_id"], operation_id=str(uuid.uuid4())
    )
    other, other_record = proposal(a, "second-mapping", 2)
    with a.journal.opening(raw_request=other, raw_record=other_record) as attempt:
        second = copy.deepcopy(attempt.record)
    path = Path(a.stores["coordination"].root) / "coord.sqlite3"
    with sqlite3.connect(path) as con:
        trigger = con.execute(
            "SELECT sql FROM sqlite_master WHERE name='reject_update_request_claims'"
        ).fetchone()[0]
        con.execute("DROP TRIGGER reject_update_request_claims")
        con.execute(
            "UPDATE request_claims SET reservation_id=CASE reservation_id WHEN ? THEN ? ELSE ? END",
            (first["reservation_id"], second["reservation_id"], first["reservation_id"]),
        )
        con.execute(trigger)
    with pytest.raises(sql.AuthorityError, match="REPLAY_CONFLICT"):
        a.journal.read()
    assert a.transport.calls.count("RESERVE") == 2


def test_manual_completion_rejects_later_verified_authority_before_any_write(
    rig, initial_artifacts
):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        commit = existing_open_sources(a, attempt, initial_artifacts)
        a.b3_transport.fail = "response"
        with pytest.raises(sql.AuthorityError):
            attempt.complete_open(raw_request=commit)
        mark_alive_uncertain(a, attempt)
        uncertain = copy.deepcopy(attempt.record)
    verified, open_ref, completion = full_existing_candidate(a, commit)
    recovery = approved_adopt(
        a,
        uncertain,
        verified,
        open_ref,
        sql.parse_canonical(a.prepared_open.evidence_reference),
        a.prepared_open.open_evidence,
        completion=completion,
    )
    committed = a.journal.recover(raw_request=recovery)
    a.b3_transport.fail = None  # End the OPEN-only response-loss injection; new terminal operation.
    later = publish_synthetic_terminal(a, committed, "ND")
    ledger = a.f.database.read_bytes()
    floor = a.b3_floor.current()
    with pytest.raises(sql.AuthorityError, match="REFERENCE_MISMATCH"):
        completion.validate(
            approved_snapshot=later,
            operation=wire.Operation.RESOLVE_COMMITTED,
            operator_reference=wire.parse_request_bytes(recovery).to_dict()["payload"][
                "operator_authorization_reference"
            ],
        )
    assert a.f.database.read_bytes() == ledger and a.b3_floor.current() == floor


def test_actual_two_processes_distinct_projects_have_at_most_one_grant(rig):
    a = rig
    proposals = [proposal(a, "contender-" + str(i), project="project-" + str(i)) for i in range(2)]
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    release = context.Event()
    results = context.Queue()

    def contend(raw, record):
        try:
            barrier.wait(timeout=10)
            with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
                attempt.check()
                results.put(("GRANTED", attempt.record["reservation_id"]))
                assert release.wait(15), "parent did not release test-only writer"
        except sql.AuthorityError as exc:
            results.put(("DENIED", str(exc)))

    children = [context.Process(target=contend, args=p) for p in proposals]
    try:
        for child in children:
            child.start()
        observed = [results.get(timeout=20) for _ in children]
        assert sorted(x[0] for x in observed) == ["DENIED", "GRANTED"]
        assert next(x[1] for x in observed if x[0] == "DENIED") in ("LOCK_BUSY", "RESERVATION_BUSY")
    finally:
        release.set()
        for child in children:
            child.join(10)
            if child.is_alive():
                child.terminate()
                child.join()
        results.close()
        results.join_thread()
    assert all(child.exitcode == 0 for child in children)
    state = a.journal.read()
    assert len(state["claims"]) == len(state["registry"]) == len(state["receipts"]) == 1
    assert state["active"] is not None and state["sequence"] == state["token"] == 1
    assert history.current_revision(a.sources.read_authority())["revision"] == 0


def test_manual_existing_open_adoption_repairs_b3_floor_after_ledger_commit(rig, initial_artifacts):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        commit = existing_open_sources(a, attempt, initial_artifacts)
        original = a.b3_floor.advance

        def interrupted(receipt):
            raise OSError("simulated crash immediately before durable B3floor")

        a.b3_floor.advance = interrupted
        with pytest.raises(OSError):
            attempt.complete_open(raw_request=commit)
        a.b3_floor.advance = original
        mark_alive_uncertain(a, attempt)
        uncertain = copy.deepcopy(attempt.record)
    verified = a.sources.read_authority()
    receipt = a.remote.inspect_b3_operation(
        wire.parse_request_bytes(commit).to_dict()["request_id"], credentials=a.auth.credential
    )
    ref = {
        "authority_id": b.AUTH_ID,
        "authority_epoch": 1,
        "revision": 1,
        "ledger_digest": receipt["ledger_digest"],
        "witness_digest": sql.digest(receipt),
        "receipt_reference": coord._reference(a.b3_floor._path(1), receipt),
    }
    assert (
        history.current_revision(verified)["revision"] == 1 and a.b3_floor.current().revision == 0
    )
    recovery = approved_adopt(
        a,
        uncertain,
        verified,
        ref,
        sql.parse_canonical(a.prepared_open.evidence_reference),
        a.prepared_open.open_evidence,
    )
    ledger = a.f.database.read_bytes()
    result = a.journal.recover(raw_request=recovery)
    assert result["state"] == "COMMITTED" and a.f.database.read_bytes() == ledger
    assert (
        a.b3_floor.current().revision == 1
        and a.b3_floor.current().witness_digest == ref["witness_digest"]
    )
    assert (
        a.b3_transport.calls.count("B3_CAS") == 1
        and a.transport.calls.count("ADOPT_OPEN_MANUAL") == 1
    )


def test_unknown_terminal_retirement_never_returns_unconfirmed_success_or_resends(
    rig, initial_artifacts
):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        commit = existing_open_sources(a, attempt, initial_artifacts)
        attempt.complete_open(raw_request=commit)
        committed = copy.deepcopy(attempt.record)
    publish_synthetic_terminal(a, committed, "ND")
    original = a.transport.transition

    def lost(**kwargs):
        raw = original(**kwargs)
        if kwargs["transition"] == "RETIRE_TERMINAL":
            raise TimeoutError("terminal response lost")
        return raw

    a.transport.transition = lost
    op = str(uuid.uuid4())
    with pytest.raises(sql.AuthorityError):
        a.journal.retire_terminal(reservation_id=committed["reservation_id"], operation_id=op)
    with pytest.raises(sql.AuthorityError, match="UNCERTAIN_PUBLICATION"):
        a.journal.retire_terminal(reservation_id=committed["reservation_id"], operation_id=op)
    assert (
        a.transport.calls.count("RETIRE_TERMINAL") == 1
        and a.journal.read()["active"] == committed["reservation_id"]
    )


@pytest.mark.parametrize("phase", ["response", "receipt-before-floor", "floor-before-marker"])
def test_explicit_existing_terminal_retirement_recovery_repairs_lag_without_second_cas(
    rig, initial_artifacts, phase
):
    a = rig
    raw, record = proposal(a)
    with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
        commit = existing_open_sources(a, attempt, initial_artifacts)
        attempt.complete_open(raw_request=commit)
        committed = copy.deepcopy(attempt.record)
    publish_synthetic_terminal(a, committed, "ND")
    op = str(uuid.uuid4())
    transition = a.transport.transition
    advance = a.floor.advance

    def lost(**kwargs):
        value = transition(**kwargs)
        if kwargs["transition"] == "RETIRE_TERMINAL":
            raise TimeoutError("terminal response lost")
        return value

    def interrupted(head, **kwargs):
        if phase == "floor-before-marker":
            advance(head, **kwargs)
        raise OSError("terminal durable marker interrupted")

    if phase == "response":
        a.transport.transition = lost
    else:
        a.floor.advance = interrupted
    with pytest.raises((sql.AuthorityError, OSError)):
        a.journal.retire_terminal(reservation_id=committed["reservation_id"], operation_id=op)
    a.transport.transition = transition
    a.floor.advance = advance
    restarted = coord.CoordinationJournal(**a.options)
    before = restarted._local_state()
    assert before["active"] == committed["reservation_id"] and before["sequence"] == 2
    with pytest.raises(sql.AuthorityError):
        restarted.recover_terminal_retirement(
            reservation_id=committed["reservation_id"], operation_id=op
        )
    assert restarted._local_state() == before
    with a.remote._database() as con:
        con.execute("BEGIN")
        existing = sql.canonical_bytes(a.remote._read(con)["receipts"][-1])
    base_qualify = a.sources.qualify_terminal

    def approved(*, operation_id, record, writer):
        writer.check()
        assert operation_id == op and record == before["latest"][committed["reservation_id"]]
        qualification = base_qualify(operation_id=operation_id, record=record, writer=writer)
        approval = service.RecoveryApproval(
            op,
            record["reservation_id"],
            before["digest"],
            sql.canonical_bytes(b.reference("explicit-retire-repair-approval")),
            sql.canonical_bytes(b.reference("verified-stopped-terminal-writer")),
            None,
        )
        return replace(qualification, existing_receipt=existing, recovery_approval=approval)

    a.sources.qualify_terminal = approved
    repaired = restarted.recover_terminal_retirement(
        reservation_id=committed["reservation_id"], operation_id=op
    )
    assert repaired["terminal_witness_reference"]["revision"] == 2
    assert repaired["manual_resolution_reference"] == b.reference("explicit-retire-repair-approval")
    assert restarted.read()["active"] is None and restarted.read()["sequence"] == 3
    assert a.floor.current()["sequence"] == 3 and a.transport.calls.count("RETIRE_TERMINAL") == 1
    assert a.b3_transport.calls.count("B3_CAS") == 2
    assert (
        restarted.recover_terminal_retirement(
            reservation_id=committed["reservation_id"], operation_id=op
        )
        == repaired
    )
    assert a.transport.calls.count("RETIRE_TERMINAL") == 1
