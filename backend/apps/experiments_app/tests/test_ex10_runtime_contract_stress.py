"""Offline B4 stress/crash infrastructure; campaigns execute only in validation.

All stores/keys/witnesses belong to disposable fixtures. These tests grant no
permission to operate an official deployment or to infer writer closure from
process death. The nine IDs follow the frozen external B4 stress plan.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import multiprocessing
import os
import random
import time
import uuid
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "s11_runtime_stress_coordination_fixture",
    Path(__file__).with_name("test_ex10_start_coordination.py"),
)
fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture)
rig = fixture.rig
initial_artifacts = fixture.initial_artifacts

SCENARIOS = {
    "B4-S01": (100, "same session"),
    "B4-S02": (100, "two independently identified disposable roots"),
    "B4-S03": (100, "two projects, common authority and slot"),
    "B4-S04": (2, "owner dies after durable pending claim"),
    "B4-S05": (2, "owner dies after evidence open"),
    "B4-S06": (2, "response loss preserves coordination under resource disappearance"),
    "B4-S07": (100, "witness request/CAS/response loss"),
    "B4-S08": (2, "authority or lock identity replacement"),
    "B4-S09": (100, "readers never acquire exclusive writer lock"),
}


def _record(record_property, scenario, *, requests=(), outcomes=(), state=None, **facts):
    value = {
        "scenario_id": scenario,
        "seed": 1107,
        "attempt_bound": SCENARIOS[scenario][0],
        "requests": [
            {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "request_id": fixture.wire.parse_request_bytes(raw).to_dict()["request_id"],
            }
            for raw in requests
        ],
        "outcomes": list(outcomes),
        "state": None
        if state is None
        else {
            "sequence": state["sequence"],
            "fencing_token": state["token"],
            "active": state["active"],
            "claims": sorted(state["claims"]),
            "reservations": sorted(state["registry"]),
            "receipt_digest": state["digest"],
        },
        "real_authority": False,
        "official_start": False,
        "experimental_posts": 0,
        "tasks": 0,
        "t0_created": False,
        **facts,
    }
    record_property("s11_stress_evidence", json.dumps(value, sort_keys=True))


def _assert_other_process_blocked(rig, raw, record, expected_code):
    """A fresh fork must fail closed without changing either durable witness."""
    before = rig.journal.read()
    ledger_before = fixture.sql._load_database(rig.f.database)
    b4_before = rig.remote.head_b4(
        fixture.b.hashed("stress-contender-before"), credentials=rig.auth.credential
    )
    b3_before = rig.remote.head_b3(
        fixture.b.hashed("stress-contender-before-b3"), credentials=rig.auth.credential
    )
    context = multiprocessing.get_context("fork")
    results = context.Queue()

    def contend():
        restarted = fixture.coord.CoordinationJournal(**rig.options)
        try:
            with restarted.opening(raw_request=raw, raw_record=record):
                results.put("UNEXPECTED_GRANT")
        except fixture.sql.AuthorityError as error:
            results.put(error.code)

    child = context.Process(target=contend)
    try:
        child.start()
        child.join(15)
        assert not child.is_alive(), "bounded contender did not return"
        assert child.exitcode == 0
        assert results.get(timeout=5) == expected_code
    finally:
        if child.pid is not None:
            if child.is_alive():
                child.terminate()
                child.join(5)
            if child.is_alive():
                child.kill()
                child.join(5)
            assert not child.is_alive(), "owned contender survived bounded cleanup"
        results.close()
        results.join_thread()
    assert rig.journal.read() == before
    assert fixture.sql._load_database(rig.f.database) == ledger_before
    b4_after = rig.remote.head_b4(
        fixture.b.hashed("stress-contender-after"), credentials=rig.auth.credential
    )
    b3_after = rig.remote.head_b3(
        fixture.b.hashed("stress-contender-after-b3"), credentials=rig.auth.credential
    )
    assert tuple(
        b4_after[k]
        for k in ("sequence", "receipt_digest", "fencing_token", "active_reservation_id")
    ) == tuple(
        b4_before[k]
        for k in ("sequence", "receipt_digest", "fencing_token", "active_reservation_id")
    )
    assert tuple(b3_after[k] for k in ("revision", "ledger_digest", "witness_digest")) == tuple(
        b3_before[k] for k in ("revision", "ledger_digest", "witness_digest")
    )
    return expected_code


class _SerializedSyntheticWitnessEndpoint:
    """Fixed available synthetic RPC profile over the real signed SQLite witness.

    The 100 host contenders and their global flock remain concurrent. Only the
    independent endpoint's RPCs serialize, as a single service request loop can.
    This does not qualify direct concurrent witness SQLite access in production.
    """

    def __init__(self, service, lock):
        assert type(service) is fixture.service.WitnessService
        self._service, self._lock = service, lock

    @property
    def clock(self):
        return self._service.clock

    def _call(self, method, *args, **kwargs):
        # The existing HEAD roundtrip guard remains <=30 seconds. No retry or
        # renewed request/deadline occurs if this synthetic endpoint is busy.
        if not self._lock.acquire(timeout=30):
            raise TimeoutError("synthetic witness request queue unavailable")
        try:
            return method(*args, **kwargs)
        finally:
            self._lock.release()

    def head_b3(self, challenge, *, credentials):
        return self._call(self._service.head_b3, challenge, credentials=credentials)

    def head_b4(self, challenge, *, credentials):
        return self._call(self._service.head_b4, challenge, credentials=credentials)

    def reserve(self, raw_record, *, expected_sequence, expected_receipt_digest, credentials):
        return self._call(
            self._service.reserve,
            raw_record,
            expected_sequence=expected_sequence,
            expected_receipt_digest=expected_receipt_digest,
            credentials=credentials,
        )

    def transition(self, **kwargs):
        return self._call(self._service.transition, **kwargs)


def _pending_coordination_bytes(rig):
    """Original durable stores only; no witness repair or inferred owner closure."""
    return {
        (name, str(path.relative_to(Path(rig.stores[name].root)))): path.read_bytes()
        for name in ("coordination", "coordination-floor", "receipts", "witness")
        for path in sorted(Path(rig.stores[name].root).rglob("*"))
        if path.is_file()
    }


def _assert_pending_witness_projection(rig, state, *, response_lost):
    head = rig.remote.head_b4(
        fixture.b.hashed("stress-pending-witness-state"), credentials=rig.auth.credential
    )
    floor = rig.floor.current()
    assert state["sequence"] == state["token"] == 0
    assert floor["sequence"] == floor["fencing_token"] == 0
    assert state["witness_active"] is None and floor["active_reservation_id"] is None
    expected_sequence = 1 if response_lost else 0
    assert head["sequence"] == head["fencing_token"] == expected_sequence
    assert head["active_reservation_id"] == (state["active"] if response_lost else None)
    return tuple(
        head[key]
        for key in ("sequence", "receipt_digest", "fencing_token", "active_reservation_id")
    )


@pytest.mark.parametrize("scenario", ["B4-S01", "B4-S02", "B4-S03"])
def test_100_simultaneous_global_reservation_contenders(rig, record_property, scenario):
    a = rig
    context = multiprocessing.get_context("fork")
    endpoint = _SerializedSyntheticWitnessEndpoint(a.remote, context.RLock())
    a.remote = a.sources.remote = a.transport.remote = endpoint
    roots = [a.root / ("stress-root-" + str(i)) for i in range(2)]
    for root in roots:
        root.mkdir(mode=0o700)
    proposals = []
    for i in range(100):
        group = i % 2
        proposals.append(
            fixture.proposal(
                a,
                scenario + "-" + str(i),
                project="stress-project-" + str(group) if scenario == "B4-S03" else None,
                session=str(uuid.uuid5(uuid.NAMESPACE_URL, str(roots[group])))
                if scenario == "B4-S02"
                else None,
            )
        )
    random.Random(1107).shuffle(proposals)
    barrier, release, results = context.Barrier(100), context.Event(), context.Queue()

    def contend(index, raw, record):
        try:
            barrier.wait(timeout=30)
            with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
                attempt.check()
                results.put((index, "GRANTED", attempt.record["reservation_id"]))
                if not release.wait(60):
                    raise AssertionError("bounded owner release timed out")
        except fixture.sql.AuthorityError as error:
            results.put((index, "DENIED", error.code))

    children = [
        context.Process(target=contend, args=(i, *proposal)) for i, proposal in enumerate(proposals)
    ]
    observed = []
    try:
        for child in children:
            child.start()
        observed = [results.get(timeout=60) for _ in children]
        assert sum(result[1] == "GRANTED" for result in observed) == 1
        assert all(
            # Fresh readers can observe a concurrent floor/HEAD publication and
            # fail closed; no stale grant or silent floor adoption is permitted.
            result[2] in {"LOCK_BUSY", "RESERVATION_BUSY", "COORDINATION_ROLLBACK"}
            for result in observed
            if result[1] == "DENIED"
        )
    finally:
        release.set()
        for child in children:
            if child.pid is None:
                continue
            child.join(5)
            if child.is_alive():
                child.terminate()
                child.join(5)
            if child.is_alive():
                child.kill()
                child.join(5)
        results.close()
        results.join_thread()
    assert all(child.exitcode == 0 for child in children)
    state = a.journal.read()
    assert len(state["registry"]) == len(state["claims"]) == len(state["receipts"]) == 1
    assert state["active"] is not None and state["token"] == state["sequence"] == 1
    head = a.remote.head_b4(
        fixture.b.hashed("stress-final-reservation-head"), credentials=a.auth.credential
    )
    floor = a.floor.current()
    assert (
        head["sequence"],
        head["receipt_digest"],
        head["fencing_token"],
        head["active_reservation_id"],
    ) == (state["sequence"], state["digest"], state["token"], state["active"])
    assert (
        floor["sequence"],
        floor["receipt_digest"],
        floor["fencing_token"],
        floor["active_reservation_id"],
    ) == (state["sequence"], state["digest"], state["token"], state["active"])
    assert fixture.history.current_revision(a.sources.read_authority())["revision"] == 0
    _record(
        record_property,
        scenario,
        requests=[p[0] for p in proposals],
        outcomes=sorted(observed),
        state=state,
        disposable_root_identities=[(root.stat().st_dev, root.stat().st_ino) for root in roots],
        scope=(
            "B4 common-slot contention using approved synthetic contexts; "
            "native B1 root qualification is separate"
        ),
        synthetic_open_count=0,
        reserve_witness_cas_count=1,
        witness_endpoint_profile="single-request-loop-real-signed-synthetic-witness",
        direct_concurrent_witness_sqlite_qualified=False,
    )


@pytest.mark.parametrize("phase", ["after-local-claim", "after-independent-grant"])
def test_b4_s04_durable_pending_survives_owner_death(rig, phase, record_property):
    fixture.test_actual_owner_death_releases_os_lock_but_keeps_durable_slot(rig, phase)
    before = rig.journal.read()
    pending = before["latest"][before["active"]]
    deadline = fixture.sql.utc_time(pending["freshness_deadline"]).replace(tzinfo=fixture.UTC)
    advanced = deadline + fixture.timedelta(seconds=1)
    rig.now = lambda: advanced
    rig.sources.now = rig.now
    rig.remote.clock = rig.now
    rig.options = {**rig.options, "clock": rig.now}
    raw, record = fixture.proposal(rig, "after-owner-deadline", 2)
    requested = fixture.wire.parse_request_bytes(raw).to_dict()
    assert fixture.sql.utc_time(requested["issued_at"]).replace(tzinfo=fixture.UTC) > deadline
    outcome = _assert_other_process_blocked(rig, raw, record, "RESERVATION_BUSY")
    assert rig.journal.read() == before
    _record(
        record_property,
        "B4-S04",
        requests=(raw,),
        outcomes=(outcome,),
        state=before,
        killpoint=phase,
        after_original_deadline=True,
        contender_clock=fixture.coord._utc(advanced),
        synthetic_clock=True,
    )


@pytest.mark.parametrize(
    "phase", ["C5-evidence", "C5-intent", "C5-independent-open", "C6-floor", "C7-response"]
)
def test_b4_s05_crash_preserves_existing_open_evidence(
    rig, initial_artifacts, phase, record_property
):
    raw, record = fixture.proposal(rig, "stress-after-open-crash-other", 2)
    fixture.test_actual_late_crash_keeps_evidence_claims_and_never_reopens(
        rig, initial_artifacts, phase
    )
    evidence_path = initial_artifacts["root"] / "open-evidence.json"
    retained = evidence_path.read_bytes()
    expected_code = {
        "C5-evidence": "RESERVATION_BUSY",
        "C5-intent": "FIRST_VALID_BLOCKED",
        "C5-independent-open": "HISTORY_INVALID",
        "C6-floor": "FIRST_VALID_BLOCKED",
        "C7-response": "FIRST_VALID_BLOCKED",
    }[phase]
    outcome = _assert_other_process_blocked(rig, raw, record, expected_code)
    assert evidence_path.read_bytes() == retained
    _record(
        record_property,
        "B4-S05",
        requests=(raw,),
        outcomes=(outcome,),
        state=rig.journal.read(),
        killpoint=phase,
        evidence_sha256=hashlib.sha256(retained).hexdigest(),
    )


def test_b4_s06_response_loss_then_disposable_resources_disappear(rig, record_property):
    # Project resources are outside authority/coordination stores. No cleanup
    # runner is invoked: joined cleanup safety is a separate B7 integration gate.
    resource = rig.root / "disposable-project-resource"
    resource.mkdir(mode=0o700)
    fixture.test_witness_result_unknown_preserves_local_claim_and_never_resends(rig, "response")
    before = rig.journal.read()
    resource.rmdir()
    restarted = fixture.coord.CoordinationJournal(**rig.options)
    assert restarted.read() == before
    pending_bytes = _pending_coordination_bytes(rig)
    witness_before = _assert_pending_witness_projection(rig, before, response_lost=True)
    other, record = fixture.proposal(rig, "after-resource-disappearance", 2)
    # The accepted independent CAS is ahead of local/floor after response loss;
    # exact current-head equality must deny before the pending-slot busy gate.
    with pytest.raises(fixture.sql.AuthorityError, match="^COORDINATION_ROLLBACK$"):
        with restarted.opening(raw_request=other, raw_record=record):
            pytest.fail("resource disappearance released durable slot")
    assert rig.transport.calls.count("RESERVE") == 1
    assert restarted.read() == before and _pending_coordination_bytes(rig) == pending_bytes
    assert _assert_pending_witness_projection(rig, before, response_lost=True) == witness_before
    _record(
        record_property,
        "B4-S06",
        state=before,
        scope=(
            "durable coordination under resource disappearance; "
            "full B7 cleanup separately required"
        ),
    )


@pytest.mark.parametrize("failure", ["before", "response"])
def test_b4_s07_100_witness_outage_contenders_do_not_retry(rig, failure, record_property):
    raw, record = fixture.proposal(rig, "outage-owner")
    rig.transport.fail = failure
    with pytest.raises(fixture.sql.AuthorityError, match="UNCERTAIN_PUBLICATION"):
        with rig.journal.opening(raw_request=raw, raw_record=record):
            pytest.fail("unknown witness outcome authorized owner")
    state = rig.journal.read()
    pending_bytes = _pending_coordination_bytes(rig)
    witness_before = _assert_pending_witness_projection(
        rig, state, response_lost=failure == "response"
    )
    expected_code = "RESERVATION_BUSY" if failure == "before" else "COORDINATION_ROLLBACK"
    outcomes = []
    # Distinct requests after the original durable claim; outage is never
    # cleared or automatically retried to force a successful grant.
    for i in range(99):
        other, other_record = fixture.proposal(rig, "outage-contender-" + str(i), 2)
        with pytest.raises(fixture.sql.AuthorityError, match="^" + expected_code + "$") as denied:
            with rig.journal.opening(raw_request=other, raw_record=other_record):
                pytest.fail("outage reopened slot")
        outcomes.append(denied.value.code)
        assert rig.journal.read() == state and _pending_coordination_bytes(rig) == pending_bytes
    assert rig.transport.calls.count("RESERVE") == 1
    assert (
        _assert_pending_witness_projection(rig, state, response_lost=failure == "response")
        == witness_before
    )
    assert rig.journal.read() == state and state["active"] is not None
    _record(
        record_property,
        "B4-S07",
        requests=[raw],
        outcomes=outcomes,
        state=state,
        killpoint="before-request" if failure == "before" else "accepted-CAS-before-response",
        actual_attempts=100,
        witness_dispatch_count=1,
    )


@pytest.mark.parametrize("target", ["lock", "authority"])
def test_b4_s08_original_identity_replacement_denied(rig, target, record_property):
    path = rig.lock if target == "lock" else rig.f.database
    original_identity = fixture.h.identity(path)
    store = None
    if target == "authority":
        store = fixture.backend.PrivateStore(str(path.parent), fixture.h.identity(path.parent))
    saved = path.read_bytes()
    mode = path.stat().st_mode & 0o777
    replacement = path.with_name(path.name + ".replacement")
    replacement.write_bytes(saved)
    replacement.chmod(mode)
    os.replace(replacement, path)
    raw, record = fixture.proposal(rig, "replaced-" + target) if target == "lock" else (None, None)
    if target == "lock":
        with pytest.raises(fixture.sql.AuthorityError):
            with rig.journal.opening(raw_request=raw, raw_record=record):
                pytest.fail("replaced lock admitted")
    else:
        try:
            with pytest.raises(fixture.sql.AuthorityError):
                with fixture.backend.existing_database(
                    store, path.name, original_identity, schema_check=fixture.sql._verify_sql_schema
                ):
                    pytest.fail("replaced authority database admitted")
        finally:
            store.close()
    assert path.read_bytes() == saved and rig.transport.calls.count("RESERVE") == 0
    _record(
        record_property,
        "B4-S08",
        target=target,
        retained_bytes_sha256=hashlib.sha256(saved).hexdigest(),
    )


def test_b4_s09_100_readers_complete_while_global_writer_is_held(rig, record_property):
    import traceback
    from queue import Empty

    context = multiprocessing.get_context("fork")
    results = context.Queue()
    before = rig.journal.read()

    def read_only(index):
        started = time.monotonic()
        try:
            state = rig.journal.read()
        except BaseException:
            results.put((index, False, time.monotonic() - started, traceback.format_exc()))
            raise
        results.put((index, state == before, time.monotonic() - started, None))

    children = [context.Process(target=read_only, args=(i,)) for i in range(100)]
    observed = []
    with fixture.backend.HeldWriter(
        rig.stores["lock"], "global.lock", fixture.h.identity(rig.lock)
    ):
        try:
            # One budget includes all forks and reads, including coverage overhead
            # on slow CI runners; receiving a result never renews the deadline.
            deadline = time.monotonic() + 120
            for child in children:
                child.start()
            for _ in children:
                try:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise Empty
                    result = results.get(timeout=remaining)
                except Empty:
                    received = {row[0] for row in observed}
                    missing = {
                        i: child.exitcode for i, child in enumerate(children) if i not in received
                    }
                    pytest.fail(
                        f"Reader deadline exceeded: {len(observed)}/100 results; "
                        f"missing reader indices/exit codes: {missing}"
                    )
                observed.append(result)
                assert result[3] is None, f"Reader {result[0]} failed:\n{result[3]}"
            assert {row[0] for row in observed} == set(range(100))
            assert all(result[1] for result in observed), observed
        finally:
            for child in children:
                if child.pid is None:
                    continue
                child.join(5)
                if child.is_alive():
                    child.terminate()
                    child.join(5)
                if child.is_alive():
                    child.kill()
                    child.join(5)
            results.close()
            results.join_thread()
    assert all(child.exitcode == 0 for child in children)
    assert rig.journal.read() == before
    _record(
        record_property,
        "B4-S09",
        outcomes=sorted(row[:3] for row in observed),
        state=before,
        reader_calls=100,
        exclusive_writer_held=True,
    )


def test_two_process_rehearsal_one_actual_synthetic_open(rig, initial_artifacts, record_property):
    """B4-T109: real journal/W01/B3/witness components, synthetic host admission.

    Independent evidence roots are not a claim of full native B1/B7 deployment
    qualification. The winner writes metadata only: no T0, POST or task.
    """
    a = rig
    roots = [initial_artifacts["root"] / ("rehearsal-" + str(i)) for i in range(2)]
    for path in roots:
        path.mkdir(mode=0o700)
    fixture.prepare_existing_open_candidate_reader(a)
    proposals = [
        fixture.proposal(
            a,
            "joined-rehearsal-" + str(i),
            session=str(uuid.uuid5(uuid.NAMESPACE_URL, str(roots[i]))),
        )
        for i in range(2)
    ]
    context = multiprocessing.get_context("fork")
    barrier, release, results = context.Barrier(2), context.Event(), context.Queue()

    def contend(index, raw, record):
        try:
            barrier.wait(timeout=15)
            with a.journal.opening(raw_request=raw, raw_record=record) as attempt:
                sample = {**initial_artifacts, "root": roots[index]}
                commit = fixture.existing_open_sources(a, attempt, sample)
                response = attempt.complete_open(raw_request=commit)
                results.put(
                    (
                        "OPEN",
                        index,
                        attempt.record["run_id"],
                        a.b3_transport.calls.count("B3_CAS"),
                        a.transport.calls.count("RESERVE"),
                        a.transport.calls.count("COMMIT_OPEN"),
                        hashlib.sha256(response).hexdigest(),
                    )
                )
                if not release.wait(60):
                    raise AssertionError("bounded rehearsal release timed out")
        except fixture.sql.AuthorityError as error:
            results.put(("DENIED", index, error.code))

    children = [
        context.Process(target=contend, args=(i, *proposal)) for i, proposal in enumerate(proposals)
    ]
    observed = []
    try:
        for child in children:
            child.start()
        observed = [results.get(timeout=60) for _ in children]
        assert sorted(row[0] for row in observed) == ["DENIED", "OPEN"]
        assert next(row[2] for row in observed if row[0] == "DENIED") in {
            "LOCK_BUSY",
            "RESERVATION_BUSY",
        }
    finally:
        release.set()
        for child in children:
            if child.pid is None:
                continue
            child.join(5)
            if child.is_alive():
                child.terminate()
                child.join(5)
            if child.is_alive():
                child.kill()
                child.join(5)
        results.close()
        results.join_thread()
    assert all(child.exitcode == 0 for child in children)
    winner = next(row for row in observed if row[0] == "OPEN")
    assert winner[3:6] == (1, 1, 1)
    state = a.journal.read()
    assert len(state["registry"]) == len(state["responses"]) == 1
    assert len(state["claims"]) == 2 and state["sequence"] == 2 and state["token"] == 1
    record = state["latest"][state["active"]]
    assert record["state"] == "COMMITTED" and record["run_id"] == winner[2]
    snapshot = a.sources.read_authority()
    assert fixture.history.current_revision(snapshot)["revision"] == 1
    assert fixture.history.find_run(snapshot, winner[2])["lifecycle_state"] == "OPEN"
    assert (roots[winner[1]] / "open-evidence.json").is_file()
    assert not (roots[1 - winner[1]] / "open-evidence.json").exists()
    _record(
        record_property,
        "B4-S02",
        requests=[p[0] for p in proposals],
        outcomes=sorted(observed),
        state=state,
        contract_test="B4-T109",
        synthetic_open_count=1,
        b3_open_cas_count=1,
        reserve_witness_cas_count=1,
        commit_witness_cas_count=1,
        scope="actual B4/W01/B3 components with synthetic admission; native B1/B7 separate",
    )
