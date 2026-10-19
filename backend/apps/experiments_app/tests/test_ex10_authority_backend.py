"""Disposable external authority storage and independent witness integration."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import sqlite3
import tempfile
import types
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

APP = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("S11_B3_SOURCE_DIR", str(APP)))
spec = importlib.util.spec_from_file_location(
    "w04_b3_fixture_" + __name__.replace(".", "_"),
    Path(__file__).with_name("test_ex10_history_authority.py"),
)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def load(name):
    spec = importlib.util.spec_from_file_location(
        b.PACKAGE.__name__ + "." + name, SOURCE / (name + ".py")
    )
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


backend = load("ex10_authority_backend")
service = load("ex10_witness_service")
wire = service.b4
sql, witness, history = b.sql, b.wit, b.history


def identity(path):
    s = path.lstat()
    return backend.FileIdentity(s.st_dev, s.st_ino, s.st_uid, s.st_mode & 0o777)


class Auth:
    """Test-only injected peer credential verifier; ephemeral credential is never serialized."""

    def __init__(self):
        self.credential = object()
        self.calls = []

    def verify(self, *, operation, canonical_body, credentials):
        sql.require(credentials is self.credential, "TRUST_UNAPPROVED")
        sql.parse_canonical(canonical_body)
        self.calls.append(operation)


class Admin:
    def __init__(self, auth):
        self.auth, self.calls = auth, []

    def verify(self, *, transition, approval, canonical_receipt, credentials):
        sql.require(
            credentials is self.auth.credential
            and approval.no_open_reference is not None
            and approval.writer_closure_reference is not None,
            "TRUST_UNAPPROVED",
        )
        self.calls.append(transition)


class Transport:
    def __init__(self, remote, credential):
        self.remote, self.credential = remote, credential
        self.calls, self.fail = [], None
        self.last_payload = None

    def publish_b3(self, payload, expected_previous):
        self.calls.append("B3_CAS")
        self.last_payload = dict(payload)
        if self.fail == "before":
            raise ConnectionError("private endpoint details must not escape")
        receipt = self.remote.publish_b3(payload, expected_previous, credentials=self.credential)
        if self.fail == "response":
            raise TimeoutError("private endpoint details must not escape")
        return receipt

    def head_b3(self, challenge):
        self.calls.append("B3_HEAD")
        if self.fail == "head":
            raise ConnectionError("private endpoint details must not escape")
        return self.remote.head_b3(challenge, credentials=self.credential)


@pytest.fixture
def apparatus():
    # All roots outside checkout, test HOME already confined by repository conftest.
    with tempfile.TemporaryDirectory(
        prefix="s11-b3-test-w04-", dir=os.environ["S11_B3_TEST_BASE"]
    ) as raw:
        root = Path(raw)
        root.chmod(0o700)
        local = root / "s11-b3-test-local"
        local.mkdir(mode=0o700)
        f = b.Fixture(local, ("OPEN",), create_db=True)
        stores = {}
        for name in ("candidate", "floor", "independent-witness", "lock"):
            p = root / name
            p.mkdir(mode=0o700)
            stores[name] = backend.PrivateStore(str(p), identity(p))
        stores["ledger"] = backend.PrivateStore(str(local), identity(local))
        lock = root / "lock" / "global.lock"
        lock.write_bytes(b"")
        lock.chmod(0o600)
        auth = Auth()
        admin = Admin(auth)
        key = Ed25519PrivateKey.generate()
        pin = wire.PublicKeyPin("test-independent-b4", 1, key.public_key())
        remote_store = stores["independent-witness"]
        remote_identity = service._provision_witness_database(
            store=remote_store,
            name="witness.sqlite3",
            receipts=[f.receipt],
            trust_pins=[f.pin],
            expected_genesis_digest=sql.digest(f.receipt),
            expected_genesis_binding=f.receipt["genesis"],
            authorizer=auth,
            credentials=auth.credential,
        )
        options = dict(
            store=remote_store,
            name="witness.sqlite3",
            identity=remote_identity,
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
        remote = service.WitnessService(**options)
        stores["floor"].create_exact(
            str(root / "floor" / "floor-00000000000000000000.json"), sql.canonical_bytes(f.receipt)
        )
        floor = backend.DurableFloor(
            stores["floor"], [f.pin], sql.digest(f.receipt), f.receipt["genesis"]
        )
        transport = Transport(remote, auth.credential)
        a = types.SimpleNamespace(
            root=root,
            f=f,
            stores=stores,
            floor=floor,
            remote=remote,
            remote_options=options,
            transport=transport,
            auth=auth,
            admin=admin,
            b4_key=key,
            b4_pin=pin,
            lock=lock,
            lock_identity=identity(lock),
            ledger_identity=identity(f.database),
        )
        try:
            yield a
        finally:
            for store in stores.values():
                store.close()


def writer(a):
    return backend.HeldWriter(a.stores["lock"], "global.lock", a.lock_identity)


def make_backend(a, lease):
    return backend.AuthorityBackend(
        ledger_store=a.stores["ledger"],
        ledger_name="ledger.sqlite3",
        ledger_identity=a.ledger_identity,
        candidate_store=a.stores["candidate"],
        floor=a.floor,
        writer=lease,
        transport=a.transport,
        signer_pin=a.f.pin,
        clock_health=lambda: True,
    )


def publish(a, adapter):
    f = a.f
    args = f.publication_args(adapter)
    args["candidate_payload_path"] = str(a.root / "candidate" / "next.json")
    run = f.next_run("ACTIVE")
    event = f.event("RUN_STATE", sequence=len(f.state["authority_events"]) + 1, run=run, revision=1)
    return history._append_run_state(f.verify(), run, event, **args)


def next_payload(a, *, operation=b.OP_NEXT, digest=None):
    old = a.f.receipt
    return witness.build_witness_payload(
        {
            **{k: v for k, v in old.items() if k != "signature"},
            "revision": 1,
            "operation_id": operation,
            "ledger_digest": digest or b.hashed("next"),
            "previous_witness_digest": sql.digest(old),
            "genesis": None,
            "created_at": b.timestamp(),
        }
    )


def test_durable_adapter_actual_publication_floor_and_restart(apparatus):
    a = apparatus
    with writer(a) as lease:
        adapter = make_backend(a, lease)
        result = publish(a, adapter)
        disk = adapter.read_state()
        assert sql._ordered_state(disk) == sql._ordered_state(result.material["state"])
        assert a.floor.current().revision == 1
        assert a.transport.calls.count("B3_CAS") == 1
        assert len(disk["publication_intents"]) == len(disk["publication_resolutions"]) == 2
    remote = service.WitnessService(**a.remote_options)
    head = remote.head_b3(b.hashed("fresh-restart"), credentials=a.auth.credential)
    assert head["revision"] == 1
    with writer(a) as lease:
        assert make_backend(a, lease).read_state()["history_revisions"][-1]["revision"] == 1


@pytest.mark.parametrize("failure", ["before", "response"])
def test_remote_unknown_preserves_durable_intent_and_never_retries(apparatus, failure):
    a = apparatus
    with writer(a) as lease:
        adapter = make_backend(a, lease)
        a.transport.fail = failure
        with pytest.raises(sql.AuthorityError, match="UNCERTAIN_PUBLICATION"):
            publish(a, adapter)
        assert a.transport.calls.count("B3_CAS") == 1
        state = adapter.read_state()
        assert len(state["publication_intents"]) == 2
        assert len(state["history_revisions"]) == 1
        assert (a.root / "candidate" / "next.json").is_file()
        with pytest.raises(sql.AuthorityError, match="REPLAY_CONFLICT"):
            adapter.publish_witness(a.transport.last_payload, sql.digest(a.f.receipt))
        assert a.transport.calls.count("B3_CAS") == 1
    a.transport.fail = None
    with writer(a) as lease:
        with pytest.raises(sql.AuthorityError, match="UNCERTAIN_PUBLICATION"):
            make_backend(a, lease)
    assert a.remote.head_b3(b.hashed("inspect"), credentials=a.auth.credential)["revision"] == (
        1 if failure == "response" else 0
    )


def test_floor_failure_never_returns_success_after_local_commit(apparatus, monkeypatch):
    a = apparatus
    with writer(a) as lease:
        adapter = make_backend(a, lease)

        def broken(receipt):
            raise sql.AuthorityError("DURABLE_WRITE_UNKNOWN")

        monkeypatch.setattr(a.floor, "advance", broken)
        with pytest.raises(sql.AuthorityError, match="DURABLE_WRITE_UNKNOWN"):
            publish(a, adapter)
        assert adapter.committed
        assert len(adapter.read_state()["history_revisions"]) == 2
        assert a.floor.current().revision == 0
        assert a.transport.calls.count("B3_CAS") == 1


def test_stale_ledger_backup_rejected_by_remote_head_and_floor(apparatus):
    a = apparatus
    backup = a.root / "stale-backup.sqlite3"
    shutil.copyfile(a.f.database, backup)
    with writer(a) as lease:
        publish(a, make_backend(a, lease))
    # Overwrite same existing inode in the synthetic test domain; no historical root involved.
    a.f.database.write_bytes(backup.read_bytes())
    a.f.database.chmod(0o600)
    with writer(a) as lease:
        with pytest.raises(sql.AuthorityError, match="ROLLBACK_DETECTED"):
            make_backend(a, lease)


def test_remote_ahead_even_without_floor_advance_blocks(apparatus):
    a = apparatus
    a.remote.publish_b3(next_payload(a), sql.digest(a.f.receipt), credentials=a.auth.credential)
    with writer(a) as lease:
        with pytest.raises(sql.AuthorityError, match="ROLLBACK_DETECTED"):
            make_backend(a, lease)


def test_witness_outage_before_intent_is_read_only(apparatus):
    a = apparatus
    before = a.f.database.read_bytes()
    a.transport.fail = "head"
    with writer(a) as lease:
        with pytest.raises(sql.AuthorityError, match="WITNESS_UNAVAILABLE") as error:
            make_backend(a, lease)
    assert str(error.value) == "WITNESS_UNAVAILABLE"
    assert a.f.database.read_bytes() == before
    assert not list((a.root / "candidate").iterdir())


def test_actual_lock_required_not_json_boolean_or_released_context(apparatus):
    a = apparatus
    for fake in (True, {"held": True}, writer(a)):
        with pytest.raises(sql.AuthorityError):
            make_backend(a, fake)
    with writer(a) as lease:
        lease.check()
    with pytest.raises(sql.AuthorityError, match="SERIALIZATION_REQUIRED"):
        make_backend(a, lease)


def test_global_lock_busy_and_replacement_never_recreates(apparatus):
    a = apparatus
    with writer(a):
        with pytest.raises(sql.AuthorityError, match="LOCK_BUSY"):
            with writer(a):
                pytest.fail("second owner entered")
    # Replacement is an adversarial mutation limited to disposable lock fixture.
    original = a.lock.with_name("retained-original.lock")
    a.lock.rename(original)
    a.lock.write_bytes(b"foreign")
    a.lock.chmod(0o600)
    with pytest.raises(sql.AuthorityError, match="STORAGE_IDENTITY_CHANGED"):
        with writer(a):
            pytest.fail("replaced lock accepted")
    assert a.lock.read_bytes() == b"foreign" and original.is_file()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "mode", "replacement", "journal"])
def test_existing_database_security_identity_and_no_recovery(apparatus, kind):
    a = apparatus
    p = a.f.database
    if kind == "symlink":
        original = p.with_name("retained.sqlite3")
        p.rename(original)
        p.symlink_to(original)
    elif kind == "hardlink":
        os.link(p, p.with_name("second-link.sqlite3"))
    elif kind == "mode":
        p.chmod(0o644)
    elif kind == "replacement":
        original = p.with_name("retained.sqlite3")
        p.rename(original)
        p.write_bytes(original.read_bytes())
        p.chmod(0o600)
    else:
        p.with_name(p.name + "-journal").write_bytes(b"hot-journal-test")
    with writer(a) as lease:
        with pytest.raises(sql.AuthorityError):
            make_backend(a, lease)


def test_read_database_cannot_write_or_mutate_bytes(apparatus):
    a = apparatus
    before = hashlib.sha256(a.f.database.read_bytes()).hexdigest()
    with backend.existing_database(
        a.stores["ledger"], "ledger.sqlite3", a.ledger_identity, schema_check=sql._verify_sql_schema
    ) as con:
        assert con.execute("PRAGMA query_only").fetchone() == (1,)
        with pytest.raises(sqlite3.OperationalError):
            con.execute("CREATE TABLE forbidden(x)")
    assert hashlib.sha256(a.f.database.read_bytes()).hexdigest() == before


def test_candidate_outside_approved_root_rejected_without_witness(apparatus):
    a = apparatus
    with writer(a) as lease:
        adapter = make_backend(a, lease)

        def badpath(intent, candidate):
            intent = {**intent, "candidate_payload_path": str(a.root / "outside.json")}
            backend.AuthorityBackend.durable_intent(adapter, intent, candidate)

        adapter.durable_intent = badpath
        with pytest.raises(sql.AuthorityError, match="UNSAFE_PATH"):
            publish(a, adapter)
    assert a.transport.calls.count("B3_CAS") == 0
    assert not (a.root / "outside.json").exists()


def test_floor_failure_restart_blocks_before_any_new_witness_cas(apparatus, monkeypatch):
    a = apparatus
    with writer(a) as lease:
        adapter = make_backend(a, lease)

        def broken(receipt):
            raise sql.AuthorityError("DURABLE_WRITE_UNKNOWN")

        with monkeypatch.context() as patch:
            patch.setattr(a.floor, "advance", broken)
            with pytest.raises(sql.AuthorityError, match="DURABLE_WRITE_UNKNOWN"):
                publish(a, adapter)
        assert adapter.committed and a.floor.current().revision == 0
    prior_calls = a.transport.calls.count("B3_CAS")
    with writer(a) as lease:
        with pytest.raises(sql.AuthorityError, match="FLOOR_INCOMPLETE"):
            make_backend(a, lease)
    assert a.transport.calls.count("B3_CAS") == prior_calls == 1


def test_sqlite_swap_restore_during_connect_rejects_actual_foreign_fd(apparatus, monkeypatch):
    a = apparatus
    foreign = a.root / "foreign-private"
    foreign.mkdir(mode=0o700)
    other = foreign / "foreign.sqlite3"
    shutil.copyfile(a.f.database, other)
    other.chmod(0o600)
    original = sqlite3.connect
    retained = a.f.database.with_name("retained-original.sqlite3")

    def raced(db_uri, **kwargs):
        if str(db_uri).split("?")[0].endswith("/ledger.sqlite3"):
            a.f.database.rename(retained)
            a.f.database.symlink_to(other)
            try:
                return original(db_uri, **kwargs)
            finally:
                a.f.database.unlink()
                retained.rename(a.f.database)
        return original(db_uri, **kwargs)

    before = other.read_bytes()
    monkeypatch.setattr(sqlite3, "connect", raced)
    with pytest.raises(sql.AuthorityError, match="STORAGE_IDENTITY_CHANGED"):
        with backend.existing_database(
            a.stores["ledger"], "ledger.sqlite3", a.ledger_identity, write=True
        ) as con:
            con.execute("SELECT count(*) FROM metadata").fetchone()
    assert other.read_bytes() == before


def test_failed_writer_enter_releases_descriptor_and_flock(apparatus, monkeypatch):
    a = apparatus
    lease = writer(a)

    def failed():
        raise sql.AuthorityError("STORAGE_IDENTITY_CHANGED")

    monkeypatch.setattr(lease, "check", failed)
    try:
        with pytest.raises(sql.AuthorityError, match="STORAGE_IDENTITY_CHANGED"):
            lease.__enter__()
        assert lease.fd is None
        with writer(a) as replacement:
            replacement.check()
    finally:
        # Reap only disposable leaked test resource while demonstrating the original bug.
        if lease.fd is not None:
            lease.__exit__(None, None, None)


def test_head_expires_before_atomic_commit_retains_intent_and_local_base(apparatus):
    a = apparatus
    with writer(a) as lease:
        adapter = make_backend(a, lease)
        observed = adapter.observe_head

        def delay(receipt):
            result = observed(receipt)
            stale = datetime.fromisoformat(result["now"].replace("Z", "+00:00"))
            from datetime import timedelta

            stale += timedelta(seconds=31)
            adapter.clock = lambda: stale.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            return result

        adapter.observe_head = delay
        with pytest.raises(sql.AuthorityError, match="EXPIRED_OBSERVATION"):
            publish(a, adapter)
        state = adapter.read_state()
        assert len(state["history_revisions"]) == 1
        assert len(state["publication_intents"]) == 2
        assert a.floor.current().revision == 0
        assert a.transport.calls.count("B3_CAS") == 1


@pytest.mark.parametrize(
    "phase", ["intent", "remote-cas", "local-before-floor", "floor-before-return"]
)
def test_process_crash_keeps_candidate_intent_and_durable_floor_boundary(apparatus, phase):
    import multiprocessing

    a = apparatus
    context = multiprocessing.get_context("fork")

    def child():
        with writer(a) as lease:
            adapter = make_backend(a, lease)
            if phase == "intent":
                adapter.publish_witness = lambda *_: os._exit(41)
            elif phase == "remote-cas":
                original = adapter.transport.publish_b3

                def crashed(payload, previous):
                    original(payload, previous)
                    os._exit(42)

                adapter.transport.publish_b3 = crashed
            elif phase == "local-before-floor":
                adapter.floor.advance = lambda *_: os._exit(43)
            else:
                original = adapter.floor.advance

                def crashed(receipt):
                    original(receipt)
                    os._exit(44)

                adapter.floor.advance = crashed
            publish(a, adapter)
        os._exit(99)

    process = context.Process(target=child)
    process.start()
    process.join(15)
    try:
        assert (
            process.exitcode
            == {
                "intent": 41,
                "remote-cas": 42,
                "local-before-floor": 43,
                "floor-before-return": 44,
            }[phase]
        )
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert (a.root / "candidate" / "next.json").is_file()
    with writer(a) as lease:
        if phase == "floor-before-return":
            current = make_backend(a, lease).read_state()
            assert len(current["history_revisions"]) == 2
        else:
            with pytest.raises(
                sql.AuthorityError,
                match=(
                    "FLOOR_INCOMPLETE" if phase == "local-before-floor" else "UNCERTAIN_PUBLICATION"
                ),
            ):
                make_backend(a, lease)
    with backend.existing_database(
        a.stores["ledger"], "ledger.sqlite3", a.ledger_identity, schema_check=sql._verify_sql_schema
    ) as con:
        local = sql._read_state(con)
    assert len(local["publication_intents"]) == 2
    assert len(local["history_revisions"]) == (1 if phase in ("intent", "remote-cas") else 2)
    assert a.floor.current().revision == (1 if phase == "floor-before-return" else 0)
    remote = service.WitnessService(**a.remote_options)
    tip = remote.head_b3(b.hashed("crash-inspect"), credentials=a.auth.credential)
    assert tip["revision"] == (0 if phase == "intent" else 1)


def test_journal_symlink_injected_after_connect_cannot_write_foreign_file(apparatus, monkeypatch):
    a = apparatus
    foreign = a.root / "foreign-journal-target"
    foreign.write_bytes(b"foreign-preserved" * 1000)
    foreign.chmod(0o600)
    journal = a.f.database.with_name(a.f.database.name + "-journal")
    original = sqlite3.connect

    def race(db_uri, **kwargs):
        con = original(db_uri, **kwargs)
        if str(db_uri).split("?")[0].endswith("/ledger.sqlite3"):
            journal.symlink_to(foreign)
        return con

    before = foreign.read_bytes()
    monkeypatch.setattr(sqlite3, "connect", race)
    with pytest.raises(sql.AuthorityError):
        with backend.existing_database(
            a.stores["ledger"],
            "ledger.sqlite3",
            a.ledger_identity,
            write=True,
            schema_check=sql._verify_sql_schema,
        ) as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("CREATE TABLE synthetic_journal_write_probe(value TEXT)")
            con.commit()
    assert foreign.read_bytes() == before
    assert journal.is_symlink()


def test_storage_constructor_identity_failure_closes_every_descriptor(apparatus):
    a = apparatus
    before = backend._regular_fd_snapshot()
    all_before = set()
    for entry in os.listdir("/proc/self/fd"):
        try:
            os.fstat(int(entry))
            all_before.add(int(entry))
        except OSError:
            pass
    root = a.root / "candidate"
    actual = identity(root)
    invalid = backend.FileIdentity(actual.device, actual.inode + 1, actual.owner, actual.mode)
    with pytest.raises(sql.AuthorityError):
        backend.PrivateStore(str(root), invalid)
    all_after = set()
    for entry in os.listdir("/proc/self/fd"):
        try:
            os.fstat(int(entry))
            all_after.add(int(entry))
        except OSError:
            pass
    assert all_before == all_after and before == backend._regular_fd_snapshot()


@pytest.mark.parametrize("kind", ["root", "hardlink"])
def test_sqlite_statement_boundary_rejects_root_and_journal_substitution(apparatus, kind):
    a = apparatus
    root = Path(a.stores["ledger"].root)
    saved = root.with_name(root.name + "-retained")
    foreign = a.root / "foreign-boundary"
    foreign.mkdir(mode=0o700)
    target = foreign / "approved-foreign-file"
    target.write_bytes(b"\0" * 8192)
    target.chmod(0o600)
    before = target.read_bytes()
    journal = root / "ledger.sqlite3-journal"
    with backend.existing_database(
        a.stores["ledger"],
        "ledger.sqlite3",
        a.ledger_identity,
        write=True,
        schema_check=sql._verify_sql_schema,
    ) as con:
        if kind == "root":
            # SQLite resolves the dirfd URI to a pathname: check the retained lease per call.
            assert con.execute("PRAGMA database_list").fetchone()[2].endswith("ledger.sqlite3")
            root.rename(saved)
            root.symlink_to(foreign, target_is_directory=True)
        else:
            os.link(target, journal)
        try:
            with pytest.raises(sql.AuthorityError):
                con.execute("BEGIN IMMEDIATE")
                con.execute("CREATE TABLE foreign_sidecar_probe(value TEXT)")
                con.commit()
            assert target.read_bytes() == before
            assert sorted(p.name for p in foreign.iterdir()) == ["approved-foreign-file"]
        finally:
            if kind == "root":
                root.unlink()
                saved.rename(root)
            else:
                journal.unlink(missing_ok=True)


def publish_revision(a, revision):
    f = a.f
    with writer(a) as lease:
        adapter = make_backend(a, lease)
        f.state = adapter.read_state()
        f.floor = a.floor.current()
        trust = f.trust()
        trust["intent_payload_reader"] = lambda path: (
            f.read_payload(path)
            if Path(path).parent == f.root
            else a.stores["candidate"].read(path)
        )
        challenge = b.hashed("actual-prefix-observation-" + str(revision))
        observation = {
            "head": a.transport.head_b3(challenge),
            "challenge": challenge,
            "now": b.timestamp(),
            "request_elapsed_seconds": 0,
            "clock_healthy": True,
        }
        verified = f.verify(trust=trust, observation=observation)
        args = f.publication_args(adapter)
        op = str(uuid.uuid5(uuid.NAMESPACE_URL, "durable-prefix-" + str(revision)))
        args["operation_id"] = op
        args["capability"].update(operation_id=op, expected_revision=revision)
        args["candidate_payload_path"] = str(a.root / "candidate" / f"revision-{revision}.json")
        run = f.next_run("ACTIVE")
        run.update(
            run_version=revision, previous_run_version=revision - 1, introduced_revision=revision
        )
        event = f.event(
            "RUN_STATE", sequence=len(f.state["authority_events"]) + 1, run=run, revision=revision
        )
        result = history._append_run_state(verified, run, event, **args)
        f.state = result.material["state"]
        assert sql._ordered_state(adapter.read_state()) == sql._ordered_state(f.state)
        assert a.floor.current().revision == revision
    return result


def test_actual_twenty_revision_prefix_rejects_fifteen_backup(apparatus):
    a = apparatus
    backup = a.root / "approved-backup-at-fifteen.sqlite3"
    for revision in range(1, 21):
        publish_revision(a, revision)
        if revision == 15:
            backup.write_bytes(a.f.database.read_bytes())
            backup.chmod(0o600)
    assert a.transport.calls.count("B3_CAS") == 20
    assert a.floor.current().revision == 20
    before_identity = identity(a.f.database)
    with a.f.database.open("r+b") as stream:
        stream.write(backup.read_bytes())
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
    assert identity(a.f.database) == before_identity
    with writer(a) as lease:
        with pytest.raises(sql.AuthorityError, match="ROLLBACK_DETECTED"):
            make_backend(a, lease)
    assert a.transport.calls.count("B3_CAS") == 20
    assert (
        a.remote.head_b3(b.hashed("rollback-20-15"), credentials=a.auth.credential)["revision"]
        == 20
    )
    assert a.floor.current().revision == 20


def test_invalid_root_during_transaction_never_closes_into_foreign_journal(apparatus):
    a = apparatus
    root = Path(a.stores["ledger"].root)
    saved = root.with_name(root.name + "-retained-transaction")
    foreign = a.root / "foreign-close-boundary"
    foreign.mkdir(mode=0o700)
    victim = foreign / "ledger.sqlite3-journal"
    victim.write_bytes(b"foreign journal must survive context exit")
    victim.chmod(0o600)
    before = victim.read_bytes()
    try:
        with pytest.raises(sql.AuthorityError):
            with backend.existing_database(
                a.stores["ledger"],
                "ledger.sqlite3",
                a.ledger_identity,
                write=True,
                schema_check=sql._verify_sql_schema,
            ) as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("CREATE TABLE active_transaction_probe(value TEXT)")
                assert (root / "ledger.sqlite3-journal").is_file()
                root.rename(saved)
                root.symlink_to(foreign, target_is_directory=True)
                con.commit()
        assert victim.is_file() and victim.read_bytes() == before
        assert sorted(p.name for p in foreign.iterdir()) == ["ledger.sqlite3-journal"]
        with pytest.raises(sql.AuthorityError, match="DURABLE_WRITE_UNKNOWN"):
            with backend.existing_database(
                a.stores["ledger"], "ledger.sqlite3", a.ledger_identity, write=True
            ):
                pytest.fail("quarantined storage must never reopen")
    finally:
        root.unlink()
        saved.rename(root)
        with pytest.raises(sql.AuthorityError, match="DURABLE_WRITE_UNKNOWN"):
            a.stores["ledger"].close()
        a.stores["ledger"].check()  # Routine outer cleanup must not destroy recovery lease.
        with writer(a) as lease:
            # Explicit test-host cleanup only after restoring the exact original root.
            backend._close_quarantined_database(
                a.stores["ledger"], "ledger.sqlite3", a.ledger_identity, writer=lease
            )
        assert victim.read_bytes() == before


def test_store_open_failure_after_descriptor_acquisition_never_leaks_fd(apparatus, monkeypatch):
    a = apparatus
    store = a.stores["candidate"]
    path = store.root + "/approved-candidate.json"
    store.create_exact(path, b"{}\n")
    before = backend._regular_fd_snapshot()

    def failure(name):
        raise OSError("sensitive storage details")

    monkeypatch.setattr(store, "entry_identity", failure)
    with pytest.raises(sql.AuthorityError, match="UNSAFE_PATH"):
        store.open("approved-candidate.json")
    assert backend._regular_fd_snapshot() == before


def test_candidate_directory_fsync_failure_is_sanitized_and_never_dispatches(
    apparatus, monkeypatch
):
    a = apparatus
    original = backend.os.fsync

    def failure(fd):
        if fd == a.stores["candidate"].fd:
            raise OSError("sensitive storage durability details")
        return original(fd)

    with writer(a) as lease:
        adapter = make_backend(a, lease)
        monkeypatch.setattr(backend.os, "fsync", failure)
        with pytest.raises(sql.AuthorityError, match="DURABLE_WRITE_UNKNOWN"):
            publish(a, adapter)
    assert a.transport.calls.count("B3_CAS") == 0
    assert (a.root / "candidate" / "next.json").is_file()


@pytest.mark.parametrize("forgery", ["wrong_key", "nonce", "expired", "self_key"])
def test_actual_backend_denies_forged_observation_before_candidate_or_cas(apparatus, forgery):
    a = apparatus
    original = a.transport.head_b3
    key = Ed25519PrivateKey.generate()

    def forged(challenge):
        head = original(challenge)
        fields = {k: v for k, v in head.items() if k != "signature"}
        if forgery == "nonce":
            fields["challenge"] = "e" * 64
        elif forgery == "expired":
            from datetime import timedelta

            now = datetime.now(UTC)
            fields["issued_at"] = (now - timedelta(seconds=31)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            fields["expires_at"] = (now - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        head = witness._sign_current_head(
            fields, key if forgery in ("wrong_key", "self_key") else a.f.key
        )
        if forgery == "self_key":
            head["public_key_hex"] = key.public_key().public_bytes_raw().hex()
        return head

    a.transport.head_b3 = forged
    with writer(a) as lease:
        with pytest.raises(sql.AuthorityError):
            make_backend(a, lease)
    assert a.transport.calls.count("B3_CAS") == 0
    assert os.listdir(a.stores["candidate"].fd) == []


def test_external_floor_verifies_every_signed_parent(apparatus):
    a = apparatus
    fields = next_payload(a)
    fields["previous_witness_digest"] = "e" * 64
    forged = witness.sign_witness(fields, a.f.key)
    a.stores["floor"].create_exact(a.floor._path(1), sql.canonical_bytes(forged))
    with pytest.raises(sql.AuthorityError, match="FORK_DETECTED"):
        a.floor.current()


def test_external_floor_genesis_cannot_self_reapprove_signed_substitution(apparatus):
    a = apparatus
    fields = {k: v for k, v in a.f.receipt.items() if k != "signature"}
    fields["genesis"] = {**fields["genesis"], "bootstrap_manifest_sha256": "e" * 64}
    forged = witness.sign_witness(fields, a.f.key)
    path = Path(a.floor._path(0))
    with path.open("r+b") as stream:
        stream.write(sql.canonical_bytes(forged))
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
    with pytest.raises(sql.AuthorityError, match="TRUST_UNAPPROVED"):
        a.floor.current()


def test_floor_requires_exact_externally_approved_genesis_binding(apparatus):
    a = apparatus
    wrong = {**a.f.receipt["genesis"], "bootstrap_manifest_sha256": "e" * 64}
    with pytest.raises(sql.AuthorityError, match="TRUST_UNAPPROVED"):
        backend.DurableFloor(a.stores["floor"], [a.f.pin], sql.digest(a.f.receipt), wrong)
