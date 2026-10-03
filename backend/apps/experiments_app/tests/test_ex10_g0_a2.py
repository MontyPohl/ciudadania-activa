"""G0-A2 (03/10/2026): semántica post-run del broker de EX-10, sólo para runs futuros.

Runs, base y telemetría sintéticos (un PASS por defecto). Ningún test toca la evidencia del run
oficial 1 ni la reinterpreta: su resultado histórico es `NOT_DEMONSTRATED` para siempre.
"""

import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest
from django.core.cache import cache

from apps.experiments_app import ex10_executor as executor
from apps.experiments_app import ex10_load as ex10
from apps.experiments_app import ex10_preflight, ex10_prepare, ex10_state
from apps.experiments_app import ex10_reconcile as rec
from apps.experiments_app.constants import Decision
from apps.experiments_app.tests.ex10_executor_support import (
    QUIET_CACHE_KEY,
    close_harness,
    make_harness,
    ms,
    quiet_broker,
    scenario,
    synthetic_run,
    task_id_for,
)
from apps.map_app.cache import GENERATION_KEY

ND = Decision.NOT_DEMONSTRATED
OFFICIAL = executor.OFFICIAL_MODE
G0_A2_COMMIT = "2bab6cabe4108e1eddbb319c3f859ed75c914004"
OTHER_TASK = "11111111-2222-4333-8444-555555555555"
THROTTLE = tuple(f"s11-test:1:throttle_report_create_actor-{n}" for n in range(10))


@pytest.fixture(scope="module")
def plan():
    return ex10.build_plan(ex10.load_dataset())


@pytest.fixture(scope="module")
def tasks(plan):
    return tuple(sorted(task_id_for(item.request_key) for item in plan.items))


def ideal(tasks, **changes):
    """El residuo de un run correcto: 50 resultados, 10 throttle y la caché adjudicada."""
    values = {
        "result_task_ids": tasks,
        "throttle_keys": THROTTLE,
        "expected_throttle_keys": THROTTLE,
        "unknown_keys": (QUIET_CACHE_KEY,),
    }
    return quiet_broker(**{**values, **changes})


def problems(plan, broker, **scenario_changes):
    return rec.post_run_broker_problems(broker, scenario(plan, **scenario_changes).celery)


def g0_a2(plan, broker, run=None, **changes):
    return scenario(plan, run, post_run_broker=broker, g0_a2=True, **changes)


def broker_codes(evaluation):
    return [r for r in evaluation.result.invalidity_reasons if r.startswith("broker:")]


# --- Códigos congelados -----------------------------------------------------------------------


def test_the_frozen_post_run_codes():
    assert rec.POST_RUN_BROKER_CODES == (
        "broker:queues-not-empty",
        "broker:unacked",
        "broker:pending-task-messages",
        "broker:unexpected-result-key",
        "broker:task-failure",
        "broker:task-retry",
        "broker:unexpected-throttle-key",
        "broker:unknown-residual",
        "broker:inclassifiable-residual",
    )
    assert "broker:dynamic-residual" not in rec.POST_RUN_BROKER_CODES


# --- Residuo esperado: no invalida ------------------------------------------------------------


def test_the_ideal_post_run_residue_is_a_provisional_pass(plan, tasks):
    broker = ideal(tasks)
    assert len(broker.result_task_ids) == 50 and len(broker.throttle_keys) == 10
    assert problems(plan, broker) == []
    evaluation = g0_a2(plan, broker).evaluate()
    assert evaluation.result.invalidity_reasons == ()
    assert evaluation.decision is Decision.PASS
    assert {c: str(s) for c, s in evaluation.result.criteria.items()} == {
        "HT1-01": "PASS",
        "HT1-02": "PASS",
        "HT1-03": "PASS",
        "HT1-04": "PASS",
        "audit-postcondition": "PASS",
    }


@pytest.mark.parametrize("results", ["all", "half", "none"])
@pytest.mark.parametrize("throttle", ["all", "some", "none"])
def test_natural_expiry_of_results_or_throttle_does_not_invalidate(plan, tasks, results, throttle):
    # El TTL nunca es criterio: presentes, en parte o expiradas, las claves no invalidan.
    kept = {"all": tasks, "half": tasks[::2], "none": ()}[results]
    keys = {"all": THROTTLE, "some": THROTTLE[:3], "none": ()}[throttle]
    broker = ideal(tasks, result_task_ids=kept, throttle_keys=keys)
    assert problems(plan, broker) == []
    assert g0_a2(plan, broker).evaluate().decision is Decision.PASS


def test_the_cache_key_is_optional_and_never_read(plan, tasks):
    assert problems(plan, ideal(tasks, unknown_keys=())) == []


# --- Trabajo pendiente: invalida, cada uno con su código -------------------------------------


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"queue_keys": 1}, "broker:queues-not-empty"),
        ({"queue_keys": 3}, "broker:queues-not-empty"),
        ({"unacked": 1}, "broker:unacked"),
        ({"unacked_index": 1}, "broker:unacked"),
        ({"task_messages": 1}, "broker:pending-task-messages"),
        ({"unclassifiable": ("control-plane-type",)}, "broker:inclassifiable-residual"),
        ({"unclassifiable": ("too-many-keys",)}, "broker:inclassifiable-residual"),
        ({"malformed_result_keys": 1}, "broker:inclassifiable-residual"),
    ],
    ids=[
        "queue",
        "priority-queues",
        "unacked",
        "unacked-index",
        "control-plane-task-message",
        "inclassifiable-control-plane",
        "too-many-keys",
        "malformed-result-key",
    ],
)
def test_pending_work_invalidates_with_its_frozen_code(plan, tasks, changes, code):
    broker = ideal(tasks, **changes)
    assert problems(plan, broker) == [code]
    evaluation = g0_a2(plan, broker).evaluate()
    assert broker_codes(evaluation) == [code]
    assert evaluation.decision is ND


def test_pending_work_is_never_quiescent():
    assert quiet_broker().quiescent
    for changes in ({"queue_keys": 1}, {"unacked": 1}, {"unacked_index": 1}, {"task_messages": 1}):
        assert not quiet_broker(**changes).quiescent


# --- Claves de resultado: propiedad exacta ----------------------------------------------------


def test_a_result_key_of_another_run_or_unknown_task_is_unexpected(plan, tasks):
    for extra in (OTHER_TASK, str(uuid.uuid4())):
        broker = ideal(tasks, result_task_ids=(*tasks, extra))
        assert problems(plan, broker) == ["broker:unexpected-result-key"]


def test_a_result_key_without_readable_telemetry_cannot_be_attributed(plan, tasks):
    broker = ideal(tasks)
    unreadable = rec.Stream("celery", None, None, ())
    assert rec.post_run_broker_problems(broker, unreadable) == ["broker:unexpected-result-key"]
    assert rec.post_run_broker_problems(ideal(tasks, result_task_ids=()), unreadable) == []


def test_a_result_the_telemetry_does_not_reconcile_is_an_unreconciled_failure(plan, tasks):
    # Un desenlace en el broker sin evento terminal del run: G0-A2 «fallida sin reconciliar».
    key = plan.items[0].request_key

    def drop_finished(k, events):
        return [e for e in events if e["event"] != "celery.finished"] if k == key else events

    assert problems(plan, ideal(tasks), events_mutate=drop_finished) == ["broker:task-failure"]


def test_a_pending_retry_is_an_unreconciled_retry(plan, tasks):
    key = plan.items[0].request_key

    def retry_pending(k, events):
        if k != key:
            return events
        return [
            dict(e, celery_state="retry") if e["event"] == "celery.finished" else e for e in events
        ]

    others = tuple(t for t in tasks if t != task_id_for(key))
    broker = ideal(tasks, result_task_ids=others)
    assert problems(plan, broker, events_mutate=retry_pending) == ["broker:task-retry"]


def test_an_unreconciled_failure_or_retry_is_not_demonstrated_by_precedence(plan, tasks):
    # Matriz G0-A2 B: el residuo sin reconciliar invalida aunque HT1-04 también falle.
    key = plan.items[0].request_key

    def drop_finished(k, events):
        return [e for e in events if e["event"] != "celery.finished"] if k == key else events

    def retry_pending(k, events):
        if k != key:
            return events
        return [
            dict(e, celery_state="retry") if e["event"] == "celery.finished" else e for e in events
        ]

    failure = g0_a2(plan, ideal(tasks), events_mutate=drop_finished).evaluate()
    assert broker_codes(failure) == ["broker:task-failure"]
    assert failure.decision is ND
    others = tuple(t for t in tasks if t != task_id_for(key))
    retry = g0_a2(plan, ideal(tasks, result_task_ids=others), events_mutate=retry_pending)
    assert broker_codes(retry.evaluate()) == ["broker:task-retry"]
    assert retry.evaluate().decision is ND


def test_a_reconciled_task_failure_stays_a_system_fail_not_an_invalidity(plan, tasks):
    # G0 intacta: el fallo registrado por la telemetría es un hecho de HT1-04 (FAIL), nunca ND.
    key = plan.items[0].request_key

    def failed(k, events):
        if k != key:
            return events
        return [
            dict(e, event="celery.failed", status="failed", celery_state="failure")
            if e["event"] == "celery.finished"
            else e
            for e in events
        ]

    broker = ideal(tasks)
    assert problems(plan, broker, events_mutate=failed) == []
    evaluation = g0_a2(plan, broker, events_mutate=failed).evaluate()
    assert broker_codes(evaluation) == []
    assert "HT1-04" in evaluation.result.failed_criteria
    assert evaluation.decision is Decision.FAIL


def test_a_reconciled_retry_stays_a_system_fail_not_an_invalidity(plan, tasks):
    key = plan.items[0].request_key

    def retried(k, events):
        if k != key:
            return events
        started = next(e for e in events if e["event"] == "celery.started")
        finished = next(e for e in events if e["event"] == "celery.finished")
        return [*events, dict(finished, celery_state="retry"), started]

    broker = ideal(tasks)
    assert problems(plan, broker, events_mutate=retried) == []
    evaluation = g0_a2(plan, broker, events_mutate=retried).evaluate()
    assert broker_codes(evaluation) == []
    assert evaluation.decision is Decision.FAIL


# --- Throttle: sólo los diez actores ----------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        "s11-test:1:throttle_report_create_foreign-actor",
        "s11-test:1:throttle_other_scope_actor-1",
        "s11-test:2:throttle_report_create_actor-1",
        "throttle_report_create_actor-1",
    ],
    ids=["foreign-actor", "foreign-scope", "other-version", "unprefixed"],
)
def test_an_unexpected_throttle_key_invalidates(plan, tasks, extra):
    broker = ideal(tasks, throttle_keys=(*THROTTLE[:5], extra))
    assert problems(plan, broker) == ["broker:unexpected-throttle-key"]


# --- Caché adjudicada y desconocidas ----------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        GENERATION_KEY,
        "other:1:public-reports:v1:generation",
        "s11-test:2:public-reports:v1:generation",
        "s11-test:1:public-reports:v2:generation",
        QUIET_CACHE_KEY + "x",
        "x" + QUIET_CACHE_KEY,
        "s11-test:1:public-reports:v1:generation:extra",
    ],
    ids=["logical", "other-prefix", "other-version", "other-key", "suffix", "prefix", "longer"],
)
def test_only_the_exact_materialized_cache_key_is_adjudicated(plan, tasks, key):
    assert problems(plan, ideal(tasks, unknown_keys=(key,))) == ["broker:unknown-residual"]


def test_one_adjudicated_and_one_unknown_key_still_invalidates(plan, tasks):
    broker = ideal(tasks, unknown_keys=(QUIET_CACHE_KEY, "celery-unknown-thing"))
    assert problems(plan, broker) == ["broker:unknown-residual"]


def test_codes_are_reported_once_and_in_frozen_order(plan, tasks):
    broker = ideal(
        tasks,
        queue_keys=1,
        unacked=1,
        unacked_index=2,
        task_messages=1,
        result_task_ids=(*tasks, OTHER_TASK),
        throttle_keys=(*THROTTLE, "s11-test:1:throttle_report_create_x"),
        unknown_keys=("a", "b"),
        unclassifiable=("x", "y"),
        malformed_result_keys=3,
    )
    assert problems(plan, broker) == [
        "broker:queues-not-empty",
        "broker:unacked",
        "broker:pending-task-messages",
        "broker:unexpected-result-key",
        "broker:unexpected-throttle-key",
        "broker:unknown-residual",
        "broker:inclassifiable-residual",
    ]


# --- Semántica histórica intacta y selección por identidad -----------------------------------


def test_the_historical_semantics_is_kept_for_runs_without_g0_a2(plan, tasks):
    # Sin G0-A2 en la identidad, el broker post-run sigue la regla de broker vacío.
    evaluation = scenario(plan, broker_dynamic=60).evaluate()
    assert broker_codes(evaluation) == ["broker:dynamic-residual"]
    assert evaluation.decision is ND
    # El residuo G0-A2 no se mira en un run histórico, aunque esté presente.
    evaluation = scenario(plan, broker_dynamic=60, post_run_broker=ideal(tasks)).evaluate()
    assert broker_codes(evaluation) == ["broker:dynamic-residual"]
    assert scenario(plan, broker_dynamic=None).evaluate().decision is ND


def test_a_g0_a2_run_ignores_the_historical_counter(plan, tasks):
    evaluation = g0_a2(plan, ideal(tasks), broker_dynamic=60).evaluate()
    assert broker_codes(evaluation) == [] and evaluation.decision is Decision.PASS


def test_an_uninspected_post_run_broker_is_not_demonstrated(plan):
    evaluation = g0_a2(plan, None).evaluate()
    assert broker_codes(evaluation) == ["broker:not-inspected"]
    assert evaluation.decision is ND


def test_not_demonstrated_prevails_over_fail_and_pass(plan, tasks):
    slow = synthetic_run(plan, overrides={i.request_key: ms(900) for i in plan.items})
    assert g0_a2(plan, ideal(tasks), slow).evaluate().decision is Decision.FAIL
    assert g0_a2(plan, ideal(tasks, queue_keys=1), slow).evaluate().decision is ND
    assert g0_a2(plan, ideal(tasks, queue_keys=1)).evaluate().decision is ND


def test_the_task_in_broker_fact_follows_quiescence(plan, tasks):
    quiet = g0_a2(plan, ideal(tasks)).reconcile().async_evidence.facts
    assert {f.task_in_broker for f in quiet.values()} == {False}
    busy = g0_a2(plan, ideal(tasks, queue_keys=1)).reconcile().async_evidence.facts
    assert {f.task_in_broker for f in busy.values()} == {None}


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        ({"configuration": {"protocol": ex10.protocol_identity()}}, True),
        ({"configuration": {"protocol": {"decision_addendum_a2_commit": G0_A2_COMMIT}}}, True),
        ({"configuration": {"protocol": {"decision_addendum_commit": "db7f"}}}, False),
        ({"configuration": {"protocol": {"decision_addendum_a2_commit": "0" * 40}}}, False),
        ({"configuration": {"protocol": {"decision_addendum_a2_commit": None}}}, False),
        ({"configuration": {"protocol": [G0_A2_COMMIT]}}, False),
        ({"configuration": None}, False),
        ({}, False),
        (None, False),
    ],
    ids=[
        "current",
        "field",
        "historical",
        "wrong",
        "null",
        "not-mapping",
        "no-config",
        "empty",
        "none",
    ],
)
def test_the_run_identity_selects_the_semantics(manifest, expected):
    assert executor.follows_g0_a2(manifest) is expected


def test_the_historical_identity_has_no_g0_a2_field():
    historical = {
        k: v for k, v in ex10.protocol_identity().items() if k != "decision_addendum_a2_commit"
    }
    assert ex10.verify_protocol_identity(historical) == [
        "protocol-identity:decision_addendum_a2_commit"
    ]
    assert executor.follows_g0_a2({"configuration": {"protocol": historical}}) is False


# --- Executor: la identidad custodiada del run decide, no el HEAD ----------------------------


@pytest.fixture(scope="module")
def dataset():
    return ex10.load_dataset()


@pytest.fixture
def h(request, dataset):
    harness = make_harness(request.node.name, dataset)
    yield harness
    close_harness(harness)


def _forbidden(*_args, **_kwargs):
    raise AssertionError("probe de la otra semántica")


def test_a_future_run_uses_the_post_run_probe_and_never_the_historical_counter(h):
    seen = []
    h.set_probes(
        broker_dynamic=_forbidden, post_run_broker=lambda p: seen.append(p) or quiet_broker()
    )
    h.start(evidence_mode=OFFICIAL)
    assert (
        h.payload(executor.ART_MANIFEST_INITIAL)["configuration"]["protocol"][
            "decision_addendum_a2_commit"
        ]
        == G0_A2_COMMIT
    )
    h.execute()
    assert h.reconcile()["decision"] == "PASS"
    final = h.finalize()
    assert (final["decision"], final["binding"], final["reasons"]) == ("PASS", True, [])
    assert len(seen) == 2 and all(p.sha256 == ex10.PLAN_SHA256 for p in seen)


def test_pending_work_in_a_future_run_is_not_demonstrated(h):
    h.set_probes(broker_dynamic=_forbidden, post_run_broker=lambda p: quiet_broker(unacked=1))
    h.start(evidence_mode=OFFICIAL)
    h.execute()
    final = h.finalize()
    assert (final["decision"], final["binding"]) == ("NOT_DEMONSTRATED", False)
    assert final["reasons"] == ["broker:unacked"]


def test_a_broken_post_run_probe_is_not_inspected(h):
    def broken(plan):
        raise ConnectionError("down")

    h.set_probes(broker_dynamic=_forbidden, post_run_broker=broken)
    h.start(evidence_mode=OFFICIAL)
    h.execute()
    final = h.finalize()
    assert final["decision"] == "NOT_DEMONSTRATED"
    assert set(final["reasons"]) == {"broker:not-inspected", "probe:broker:ConnectionError"}


def test_a_run_started_with_the_historical_identity_keeps_the_historical_semantics(h, monkeypatch):
    # No retroactividad: un run sin G0-A2 en su identidad custodiada se juzga con la regla
    # histórica aunque el HEAD que lo finaliza ya incluya G0-A2.
    current = ex10.protocol_identity
    historical = {k: v for k, v in current().items() if k != "decision_addendum_a2_commit"}
    with monkeypatch.context() as patch:
        patch.setattr(ex10, "protocol_identity", lambda: dict(historical))
        h.start(evidence_mode=OFFICIAL)
    protocol = h.payload(executor.ART_MANIFEST_INITIAL)["configuration"]["protocol"]
    assert "decision_addendum_a2_commit" not in protocol
    assert "decision_addendum_a2_commit" in ex10.protocol_identity()
    h.set_probes(broker_dynamic=lambda: 60, post_run_broker=_forbidden)
    h.execute()
    assert h.reconcile()["invalidity_reasons"] == ["broker:dynamic-residual"]
    final = h.finalize()
    assert (final["decision"], final["reasons"]) == (
        "NOT_DEMONSTRATED",
        ["broker:dynamic-residual"],
    )


# --- Probe real: sólo lectura, nombres exactos -----------------------------------------------


WRITES = (
    "delete", "unlink", "flushdb", "flushall", "expire", "pexpire", "persist", "set", "mset",
    "hset", "lpush", "rpush", "zadd", "publish", "getdel", "getex", "rename", "eval",
)  # fmt: skip


class SpyRedis:
    """Cliente falso que registra cada llamada y prohíbe cualquier escritura."""

    def __init__(self, keys):
        self.keys = dict(keys)
        self.calls = []

    def __getattr__(self, name):
        if name in WRITES:
            raise AssertionError(f"escritura prohibida: {name}")
        raise AttributeError(name)

    def scan_iter(self, match=None, count=None):
        self.calls.append("scan_iter")
        return iter(list(self.keys))

    def type(self, name):
        self.calls.append("type")
        return self.keys[name].encode()

    def pttl(self, name):
        self.calls.append("pttl")
        return 1000

    def llen(self, name):
        self.calls.append("llen")
        return 0

    def lrange(self, name, start, end):
        self.calls.append("lrange")
        return []

    def close(self):
        self.calls.append("close")


@pytest.fixture
def spy(monkeypatch, plan):
    keys = ex10_state.broker_keys()
    actors = sorted({str(uuid.UUID(i.dataset_user_id)) for i in plan.items})
    throttle = [cache.make_key(ex10_state.throttle_key(a)) for a in actors]
    result_prefix = next(p[:-1] for p in keys.result_patterns if "task-meta" in p)
    names = {
        keys.queues[0]: "list",
        keys.unacked: "hash",
        result_prefix + OTHER_TASK: "string",
        result_prefix + "not-a-task-id": "string",
        throttle[0]: "string",
        throttle[1]: "string",
        cache.make_key(ex10_state.throttle_key("foreign-actor")): "string",
        cache.make_key("throttle_other_scope_x"): "string",
        "_kombu.binding.celery": "set",
        cache.make_key(GENERATION_KEY): "string",
        "zz-unknown": "string",
    }
    client = SpyRedis(names)
    environment = SimpleNamespace(redis=ex10_preflight.ReadOnlyRedis(client))
    monkeypatch.setattr(ex10_prepare, "readonly_environment", lambda: environment)
    return SimpleNamespace(client=client, throttle=throttle, keys=keys, actors=actors)


def test_the_real_probe_classifies_by_exact_name_without_writes(spy, plan):
    residue = executor.post_run_broker(plan)
    assert residue.queue_keys == 1 and residue.unacked == 1 and residue.unacked_index == 0
    assert residue.result_task_ids == (OTHER_TASK,)
    assert residue.malformed_result_keys == 1
    assert set(spy.throttle[:2]) <= set(residue.throttle_keys)
    assert len(residue.throttle_keys) == 4
    assert residue.expected_cache_key == cache.make_key(GENERATION_KEY)
    assert residue.unknown_keys == tuple(sorted([cache.make_key(GENERATION_KEY), "zz-unknown"]))
    assert residue.expected_throttle_keys == tuple(sorted(spy.throttle))
    assert len(residue.expected_throttle_keys) == 10 == len(spy.actors)
    assert set(spy.client.calls) <= {"scan_iter", "type", "pttl", "llen", "lrange", "close"}
    assert spy.client.calls[-1] == "close"


def test_the_probe_reads_no_values(spy, plan):
    executor.post_run_broker(plan)
    assert "get" not in spy.client.calls and "mget" not in spy.client.calls


def test_post_run_assessment_through_the_real_probe(spy, plan):
    residue = executor.post_run_broker(plan)
    celery = scenario(plan).celery
    assert rec.post_run_broker_problems(residue, celery) == [
        "broker:queues-not-empty",
        "broker:unacked",
        "broker:unexpected-result-key",
        "broker:unexpected-throttle-key",
        "broker:unknown-residual",
        "broker:inclassifiable-residual",
    ]


@pytest.mark.parametrize(
    ("name", "dynamic", "unknown"),
    [
        ("queue", 1, 0),
        ("unacked", 1, 0),
        ("result", 1, 0),
        ("throttle", 1, 0),
        ("cache", 0, 1),
        ("binding", 0, 0),
    ],
)
def test_the_pre_run_classifier_still_blocks_every_residue(spy, name, dynamic, unknown):
    # PRE_RUN_BROKER_POLICY_CHANGED=NO: el clasificador del preflight y del READY_STATE cuenta
    # como dinámicos resultados, throttle, colas y entregas, y la caché como desconocida; el
    # preflight exige `dynamic == 0` y `unknown == 0` antes de START.
    keys = spy.keys
    result_prefix = next(p[:-1] for p in keys.result_patterns if "task-meta" in p)
    only = {
        "queue": keys.queues[0],
        "unacked": keys.unacked,
        "result": result_prefix + OTHER_TASK,
        "throttle": spy.throttle[0],
        "cache": cache.make_key(GENERATION_KEY),
        "binding": "_kombu.binding.celery",
    }[name]
    client = SpyRedis({only: spy.client.keys[only]})
    snapshot = ex10_preflight.classify_broker(ex10_preflight.ReadOnlyRedis(client))
    assert (snapshot.dynamic, snapshot.unknown) == (dynamic, unknown)


def test_replace_keeps_the_dataclass_frozen(tasks):
    broker = ideal(tasks)
    with pytest.raises(AttributeError):
        broker.queue_keys = 1
    assert replace(broker, queue_keys=1).queue_keys == 1


def test_only_an_official_not_demonstrated_run_can_be_acknowledged(h):
    # El ACK exacto existe y no se debilita: un ND de LOCAL_TEST no es reconocible como oficial.
    h.set_probes(issue_tokens=lambda result: (_ for _ in ()).throw(KeyboardInterrupt))
    h.start(evidence_mode=executor.LOCAL_TEST_MODE)
    h.execute()
    h.finalize(abandon_reason="pre-t0-stop")
    local = h.run_id
    with pytest.raises(executor.Ex10Refusal) as info:
        h.start(evidence_mode=OFFICIAL, acknowledged_not_demonstrated=[local])
    assert info.value.reasons == (f"acknowledged-unknown:{local}",)
    assert not info.value.terminal
