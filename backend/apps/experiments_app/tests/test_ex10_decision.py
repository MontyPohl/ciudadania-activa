"""EX-10 fase 3A: HT1-01…04 y precedencia sobre evidencia reconciliada. LOCAL TEST.

Runs, base y telemetría sintéticos y coherentes (un PASS por defecto); cada test cambia un
solo hecho y comprueba el criterio y la decisión. Sin stack S11, proveedor ni run oficial:
la reconciliación y el evaluador de G0 son funciones puras sobre datos ya leídos.
Los números entre paréntesis son los ítems de §49–§52 de la especificación de la fase 3A.
"""

from dataclasses import replace

import pytest

from apps.experiments_app import ex10_executor as executor
from apps.experiments_app import ex10_load as ex10
from apps.experiments_app import ex10_reconcile as rec
from apps.experiments_app.canonical import canonical_json_bytes, sha256_bytes
from apps.experiments_app.constants import Decision
from apps.experiments_app.tests.ex10_executor_support import (
    API,
    MS,
    at,
    contract_body,
    exchange,
    expected_config,
    iso,
    ms,
    report_id_for,
    scenario,
    synthetic_run,
)

C = ex10.CriterionStatus
E = ex10.EvidenceStatus
ND = Decision.NOT_DEMONSTRATED


@pytest.fixture(scope="module")
def plan():
    return ex10.build_plan(ex10.load_dataset())


@pytest.fixture(scope="module")
def first(plan):
    return plan.items[0].request_key


@pytest.fixture(scope="module")
def last(plan):
    """El último intento del VU 1: moverlo en el tiempo no arrastra a otros."""
    return plan.for_vu(1)[-1].request_key


def codes(evaluation):
    return {f.code for f in evaluation.ht1_04.result.findings}


def with_durations(plan, durations_ms):
    """Los primeros N ítems del plan son 201 con esas duraciones; el resto, 500."""
    overrides = {}
    for index, item in enumerate(plan.items):
        if index < len(durations_ms):
            overrides[item.request_key] = ms(durations_ms[index])
        else:
            overrides[item.request_key] = ms(20, status=500)
    return synthetic_run(plan, overrides=overrides)


def edit(key, change):
    """`events_mutate` que aplica `change(eventos)` sólo a los eventos de `key`."""
    return lambda k, events: change(events) if k == key else events


def db_edit(key, change):
    return lambda k, row: change(row) if k == key else row


def event(events, name):
    return next(e for e in events if e["event"] == name)


def swap(name, **fields):
    """Cambio de eventos: reemplaza campos del evento `name`."""
    return lambda events: [dict(e, **fields) if e["event"] == name else e for e in events]


def shift(name, field, delta_ns):
    def change(events):
        target = event(events, name)
        moved = iso(rec.utc_ns(target[field]) + delta_ns)
        return [dict(e, **{field: moved}) if e is target else e for e in events]

    return change


# --- Decisión base -----------------------------------------------------------------------------


def test_a_coherent_complete_run_passes_every_criterion(plan):
    evaluation = scenario(plan).evaluate()
    assert evaluation.decision is Decision.PASS
    assert evaluation.result.criteria == {
        "HT1-01": "PASS",
        "HT1-02": "PASS",
        "HT1-03": "PASS",
        "HT1-04": "PASS",
        "audit-postcondition": "PASS",
    }
    assert evaluation.result.invalidity_reasons == ()


def test_criteria_stay_separate_without_score_or_weight(plan):
    payload = scenario(plan).evaluate().decision_payload()
    assert set(payload["criteria"]) == {
        "HT1-01",
        "HT1-02",
        "HT1-03",
        "HT1-04",
        "audit-postcondition",
    }
    text = canonical_json_bytes(payload).decode()
    assert not {"score", "weight", "average", "mean"} & set(text.replace('"', " ").split())


# --- §49 Percentiles (42–53) ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("durations", "p50", "p99", "ranks"),
    [
        ([300], 300, 300, (1, 1)),  # (42)
        ([900, 100], 100, 900, (1, 2)),  # (43)
        (list(range(10, 510, 10)), 250, 500, (25, 50)),  # (44)
        ([400, 100, 300, 200], 200, 400, (2, 4)),
    ],
    ids=["n1", "n2", "n50", "n4"],
)
def test_nearest_rank_is_ceil_base_1(plan, durations, p50, p99, ranks):
    # (45) P50 = elemento ceil(0,50·N); (46) P99 = elemento ceil(0,99·N), base 1.
    percentiles = scenario(plan, with_durations(plan, durations)).evaluate().percentiles
    assert percentiles.n == len(durations)
    assert (percentiles.p50_rank, percentiles.p99_rank) == ranks
    assert (percentiles.p50_ns, percentiles.p99_ns) == (p50 * MS, p99 * MS)


def test_percentiles_never_interpolate(plan):
    # (47) Con [100, 900] una interpolación daría 500; con [100…400], 250.
    two = scenario(plan, with_durations(plan, [100, 900])).evaluate().percentiles
    four = scenario(plan, with_durations(plan, [100, 200, 300, 400])).evaluate().percentiles
    assert two.p50_ns == 100 * MS and four.p50_ns == 200 * MS
    assert {two.p50_ns, two.p99_ns} <= set(two.durations_ns)
    assert {four.p50_ns, four.p99_ns} <= set(four.durations_ns)


def test_percentile_population_is_every_complete_201(plan):
    # (48) 201 tardío incluido; (49) 201 no contractual incluido; (50) 500 excluido;
    # (51) 201 truncado excluido.
    keys = [plan.for_vu(vu)[-1].request_key for vu in (1, 2, 3, 4)]
    run = synthetic_run(
        plan,
        overrides={
            keys[0]: at(61_000, 61_070),
            keys[1]: ms(90, body=b'{"id": "x"}'),
            keys[2]: ms(80, status=500),
            keys[3]: lambda i, s, e: exchange(i, s, s + 70 * MS, error="incomplete_read"),
        },
    )
    evaluation = scenario(plan, run).evaluate()
    rows = {r["request_key"]: r for r in rec.attempt_rows(plan, run, evaluation.successes)}
    assert [rows[k]["percentile_member"] for k in keys] == [True, True, False, False]
    assert rows[keys[0]]["started_after_window"] is True
    assert rows[keys[1]]["transport_result"] == "CONTRACT_VIOLATION"
    assert rows[keys[3]]["transport_result"] == "TRANSPORT_FAILED"
    durations = evaluation.percentiles.durations_ns
    assert evaluation.percentiles.n == 48
    assert 70 * MS in durations and 90 * MS in durations and 80 * MS not in durations
    assert durations.count(70 * MS) == 1  # sólo el tardío, no el truncado


def test_no_201_is_not_computable_and_never_invents_a_value(plan):
    # (52) N=0: NOT_COMPUTABLE, sin rango ni valor; (91) aparato válido: el FAIL lo da
    # HT1-01 (0 éxitos), nunca un percentil inventado.
    run = synthetic_run(
        plan, overrides={item.request_key: ms(20, status=500) for item in plan.items}
    )
    evaluation = scenario(plan, run).evaluate()
    latency = evaluation.calculation_payload()["latency"]
    assert latency["n"] == 0
    assert latency["ht1_02"]["p50_ns"] == ex10.NOT_COMPUTABLE
    assert latency["ht1_03"]["p99_ns"] == ex10.NOT_COMPUTABLE
    assert latency["ht1_02"]["rank"] is None and latency["ht1_03"]["rank"] is None
    assert evaluation.decision is Decision.FAIL
    assert "HT1-01" in evaluation.result.failed_criteria
    assert evaluation.throughput.n_ok_window == 0


def test_no_201_at_all_timeouts_is_still_not_computable(plan):
    timeout = lambda i, s, e: exchange(i, s, s + 60_000 * MS, error="timeout")  # noqa: E731
    run = synthetic_run(plan, overrides={item.request_key: timeout for item in plan.items})
    evaluation = scenario(plan, run).evaluate()
    assert evaluation.percentiles.n == 0 and evaluation.percentiles.p50_ns is None
    assert evaluation.decision is Decision.FAIL
    assert "HT1-01" in evaluation.result.failed_criteria


def test_no_201_with_an_invalid_apparatus_is_not_demonstrated(plan):
    run = synthetic_run(
        plan,
        overrides={item.request_key: ms(20, status=500) for item in plan.items},
        faults=(ex10.ApparatusFault(ex10.FaultKind.THREAD_DIED, vu=1, detail="X"),),
    )
    assert scenario(plan, run).evaluate().decision is ND


def test_outliers_are_never_removed(plan):
    # (53) El outlier es el rango 50 de 50: P99 lo toma tal cual.
    evaluation = scenario(plan, with_durations(plan, [100] * 49 + [55_000])).evaluate()
    assert evaluation.percentiles.p99_ns == 55_000 * MS
    assert evaluation.percentiles.ht1_03 is C.FAIL
    assert evaluation.throughput.status is C.PASS


# --- §50 HT1-01 (54–59) ------------------------------------------------------------------------


def test_fifty_reconciled_successes_inside_the_window_pass(plan):
    # (54)
    view = executor._ht1_01_view(scenario(plan).evaluate())
    assert view == {
        "numerator": 50,
        "denominator_seconds": 60,
        "throughput_per_minute": 50,
        "threshold_per_minute": 50,
        "result": "PASS",
    }


def test_forty_nine_successes_fail(plan, last):
    # (55)
    run = synthetic_run(plan, overrides={last: ms(20, status=500)})
    evaluation = scenario(plan, run).evaluate()
    assert (evaluation.throughput.n_ok_window, evaluation.throughput.status) == (49, C.FAIL)
    assert evaluation.decision is Decision.FAIL


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda row: None, "report-rows:0"),
        (lambda row: replace(row, created_rewards=()), "report-created-reward"),
        (lambda row: replace(row, created_rewards=((20, 10, "PENDIENTE"),)),
         "report-created-reward"),
        (lambda row: replace(row, media_hashes=("0" * 64,)), "report-media-hash"),
        (lambda row: replace(row, citizen_id="00000000-0000-4000-8000-000000000999"),
         "report-owner"),
    ],
    ids=["report-missing", "reward-missing", "reward-wrong", "media-hash", "owner"],
)  # fmt: skip
def test_a_201_that_does_not_reconcile_is_not_a_success(plan, last, change, reason):
    # (56) 50 × 201, pero uno no se reconcilia: FAIL, no NOT_DEMONSTRATED.
    evaluation = scenario(plan, db_mutate=db_edit(last, change)).evaluate()
    success = {s.request_key: s for s in evaluation.successes}[last]
    assert (success.status, success.reason) == (ex10.SuccessStatus.NOT_SUCCESS, reason)
    assert (evaluation.throughput.n_ok_window, evaluation.decision) == (49, Decision.FAIL)
    assert "HT1-01" in evaluation.result.failed_criteria


def test_a_success_completing_after_the_window_does_not_count(plan, last):
    # (57) Empieza a los 59,99 s y termina a los 60,01 s: éxito reconciliado, fuera de HT1-01.
    run = synthetic_run(plan, overrides={last: at(59_990, 60_010)})
    evaluation = scenario(plan, run).evaluate()
    attempt = {a.request_key: a for a in run.attempts}[last]
    assert (attempt.started_in_window, attempt.completed_in_window) == (True, False)
    success = {s.request_key: s for s in evaluation.successes}[last]
    assert success.status is ex10.SuccessStatus.RECONCILED_SUCCESS
    assert (evaluation.throughput.n_ok_window, evaluation.decision) == (49, Decision.FAIL)
    assert 20 * MS in evaluation.percentiles.durations_ns


@pytest.mark.parametrize("start_ms", [60_000, 60_001, 90_000])
def test_a_late_start_is_emitted_and_never_counts(plan, last, start_ms):
    # (58) Un inicio en window_end o después no cuenta nunca, aunque se reconcilie.
    run = synthetic_run(plan, overrides={last: at(start_ms, start_ms + 40)})
    evaluation = scenario(plan, run).evaluate()
    row = {r["request_key"]: r for r in rec.attempt_rows(plan, run, evaluation.successes)}[last]
    assert row["started_after_window"] is True and row["completed_by_window_end"] is False
    assert row["reconciliation_status"] == "RECONCILED_SUCCESS"
    assert row["throughput_success"] is False and row["percentile_member"] is True
    assert len(run.attempts) == 50 and evaluation.throughput.n_ok_window == 49


def test_the_throughput_denominator_is_always_sixty_seconds(plan):
    # (59) Con un run de 5 s o de 55 s, el denominador sigue siendo 60 s.
    for per_request in (1_000, 11_000):
        run = with_durations(plan, [per_request] * 50)
        view = executor._ht1_01_view(scenario(plan, run).evaluate())
        assert view["denominator_seconds"] == 60
        assert view["numerator"] == view["throughput_per_minute"] == 50
    assert ex10.WINDOW_NS == 60 * 1_000_000_000
    assert ex10.ThroughputResult(C.PASS, 50, 50).to_json()["denominator_minutes"] == 1


# --- §51 HT1-04 (60–83) ------------------------------------------------------------------------


def test_every_structural_fact_holds_for_each_success(plan):
    # (60–72) Un queued exacto (nombre, task_id, proceso API, sin transacción, orden), un
    # started exacto en otro proceso, un finished success, una clasificación del fake,
    # CLASIFICADO y el audit.
    base = scenario(plan)
    evaluation = base.evaluate()
    assert evaluation.ht1_04.status is E.PASS and codes(evaluation) == set()
    rows = rec.ht1_04_rows(
        plan,
        evaluation.successes,
        base.reconcile().async_evidence,
        base.database,
        base.run.attempts,
    )
    assert len(rows) == 50
    expected = {
        "queued": 1,
        "queued_task_name_ok": True,
        "queued_process_is_api": True,
        "db_transaction_open": False,
        "created_at_relation": True,
        "started": 1,
        "worker_process_present": True,
        "process_separation": True,
        "finished_success": True,
        "finished": 1,
        "failed": 0,
        "classification_count": 1,
        "fake_model": True,
        "final_status": "CLASIFICADO",
        "audit_ai_classified": 1,
        "status_history": [["", "CREADO"], ["CREADO", "CLASIFICADO"]],
    }
    for row in rows:
        assert {k: row[k] for k in expected} == expected


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (swap("celery.queued", task_name="ai.other"), "queued:task-name"),  # (61)
        (swap("celery.queued", db_transaction_open=True), "queued:db-transaction-open"),  # (78)
        (lambda es: [*es, event(es, "celery.queued")], "queued:count:2"),  # (73)
        (lambda es: [*es, event(es, "celery.started")], "started:count:2"),  # (74)
        (lambda es: [*es, event(es, "celery.finished")], "finished:count:2"),  # (75)
        (lambda es: [*es, dict(event(es, "celery.finished"), event="celery.failed",
                               status="failed", celery_state="failure")],
         "finished:celery-failed"),  # (76)
        (swap("celery.started", process={"hostname": API.hostname, "pid": API.pid}),
         "started:process-is-api"),  # (77)
        (swap("celery.queued", process={"hostname": "other", "pid": 5}), "queued:process-not-api"),
        (swap("celery.finished", celery_state="retry"), "finished:state"),  # (68)
    ],
    ids=["task-name", "db-transaction-open", "queued-dup", "started-dup", "finished-dup",
         "failed-terminal", "worker-is-api", "queued-not-api", "not-success"],
)  # fmt: skip
def test_a_failed_structural_fact_fails_ht1_04(plan, last, change, code):
    evaluation = scenario(plan, events_mutate=edit(last, change)).evaluate()
    assert code in codes(evaluation)
    assert evaluation.ht1_04.status is E.FACT_FAILED
    assert (evaluation.decision, evaluation.result.failed_criteria) == (Decision.FAIL, ("HT1-04",))


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda row: replace(row, classification_models=()), "classification:count:0"),  # (80)
        (lambda row: replace(row, classification_models=(ex10.AI_MODEL_NAME,) * 2),
         "classification:count:2"),  # (81)
        (lambda row: replace(row, classification_models=("gemini-real",)),
         "classification:model-name"),  # (82)
        (lambda row: replace(row, status="CREADO"), "final-status"),  # (71)
        (lambda row: replace(row, classified_audit_rows=0), "audit-ai-classified:count:0"),  # (83)
        (lambda row: replace(row, classified_audit_rows=2), "audit-ai-classified:count:2"),
    ],
    ids=["classification-missing", "classification-dup", "wrong-model", "not-classified",
         "audit-missing", "audit-dup"],
)  # fmt: skip
def test_persisted_postconditions_of_ht1_04(plan, last, change, code):
    evaluation = scenario(plan, db_mutate=db_edit(last, change)).evaluate()
    assert code in codes(evaluation)
    assert (evaluation.decision, evaluation.result.failed_criteria) == (Decision.FAIL, ("HT1-04",))


def test_a_missing_report_created_audit_fails_the_audit_postcondition(plan, last):
    change = db_edit(last, lambda row: replace(row, created_audit_rows=0))
    evaluation = scenario(plan, db_mutate=change).evaluate()
    assert evaluation.audit.status is E.FACT_FAILED
    assert (evaluation.decision, evaluation.result.failed_criteria) == (
        Decision.FAIL,
        ("audit-postcondition",),
    )


def test_a_task_id_without_correlation_is_missing_evidence(plan, last):
    # (62) started/finished de otro task_id: el hecho está probado, falta la correlación.
    change = swap_all_but("celery.queued", task_id="other")
    evaluation = scenario(plan, events_mutate=edit(last, change)).evaluate()
    assert {"started:missing", "finished:missing"} <= codes(evaluation)
    assert evaluation.decision is ND


def swap_all_but(name, **fields):
    return lambda events: [e if e["event"] == name else dict(e, **fields) for e in events]


def test_a_missing_api_process_is_missing_evidence(plan, last):
    # (63)
    change = edit(last, swap("celery.queued", process=None))
    evaluation = scenario(plan, events_mutate=change).evaluate()
    assert "queued:process-missing" in codes(evaluation)
    assert evaluation.decision is ND


def test_a_missing_queued_event_with_proof_of_processing_is_missing_evidence(plan, last):
    # (60) Sin celery.queued, pero con started/finished y la clasificación: NOT_DEMONSTRATED.
    change = lambda es: [e for e in es if e["event"] != "celery.queued"]  # noqa: E731
    evaluation = scenario(plan, events_mutate=edit(last, change)).evaluate()
    assert "queued:missing" in codes(evaluation)
    assert evaluation.decision is ND


def test_worker_events_missing_with_a_classification_is_missing_evidence(plan, last):
    # (66) La clasificación prueba el hecho: faltan los eventos, no el procesamiento.
    change = lambda es: [e for e in es if e["event"] not in {"celery.started", "celery.finished"}]  # noqa: E501, E731
    evaluation = scenario(plan, events_mutate=edit(last, change)).evaluate()
    assert {"started:missing", "finished:missing"} <= codes(evaluation)
    assert evaluation.decision is ND


def test_a_task_that_never_existed_is_a_failure(plan, last):
    # Sin eventos, sin clasificación y broker vacío: el hecho no ocurrió → FAIL.
    change = lambda es: [e for e in es if e["event"] == "http.finished"]  # noqa: E731
    evaluation = scenario(
        plan,
        events_mutate=edit(last, change),
        db_mutate=db_edit(last, lambda r: replace(r, classification_models=(), status="CREADO")),
    ).evaluate()
    assert "queued:not-processed" in codes(evaluation)
    assert evaluation.decision is Decision.FAIL


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (shift("celery.queued", "enqueue_at", -30 * MS), "queued:temporal-order"),
        (shift("celery.queued", "enqueue_at", 40 * MS), "queued:temporal-order"),
        (shift("celery.started", "start_at", -20 * MS), "started:before-enqueue"),
    ],
    ids=["enqueue-before-created", "enqueue-after-http-finished", "started-before-enqueue"],
)
def test_an_invalid_timestamp_relation_is_inconsistent(plan, last, change, code):
    # (65, 79, 92) created ≤ enqueue ≤ http.finished y started ≥ enqueue.
    evaluation = scenario(plan, events_mutate=edit(last, change)).evaluate()
    assert code in evaluation.ht1_04.temporal_violations[plan.by_key()[last].request_id]
    assert (evaluation.ht1_04.status, evaluation.decision) == (E.INCONSISTENT, ND)


@pytest.mark.parametrize("delta_ms", [-15, 90_000])
def test_task_end_versus_http_end_is_never_a_criterion(plan, last, delta_ms):
    # §21: que la tarea termine antes o mucho después del HTTP no cambia nada.
    change = edit(last, shift("celery.finished", "finish_at", delta_ms * MS))
    assert scenario(plan, events_mutate=change).evaluate().decision is Decision.PASS


@pytest.mark.parametrize(
    "changes",
    [
        {"after": expected_config(worker_max_concurrency=4)},
        {"after": expected_config(worker_nodes=2)},
        {"before": expected_config(api_task_always_eager=True)},
        {"before": expected_config(worker_fake_ai_enabled=False)},
        {"before": expected_config(worker_ai_adapter="gemini")},
        {"after": None},
        {"before": None},
    ],
    ids=["concurrency", "nodes", "eager", "fake-off", "adapter", "after-missing", "before-missing"],
)
def test_async_configuration_mismatch_is_not_demonstrated(plan, changes):
    assert scenario(plan, **changes).evaluate().decision is ND


# --- §52 Precedencia (84–93) ---------------------------------------------------------------------


def test_each_decisive_criterion_alone_fails_a_complete_valid_run(plan, last):
    # (84) HT1-01, (85) HT1-02, (86) HT1-03 y (87) HT1-04, cada uno con evidencia completa.
    ht1_01 = scenario(plan, synthetic_run(plan, overrides={last: ms(20, status=500)})).evaluate()
    ht1_02 = scenario(plan, with_durations(plan, [900] * 50)).evaluate()
    ht1_03 = scenario(plan, with_durations(plan, [100] * 49 + [2_500])).evaluate()
    ht1_04 = scenario(
        plan, db_mutate=db_edit(last, lambda r: replace(r, classification_models=("x",)))
    ).evaluate()
    for evaluation, criterion in (
        (ht1_01, "HT1-01"),
        (ht1_02, "HT1-02"),
        (ht1_03, "HT1-03"),
        (ht1_04, "HT1-04"),
    ):
        assert evaluation.result.invalidity_reasons == ()
        assert evaluation.result.not_demonstrable == ()
        assert (evaluation.decision, evaluation.result.failed_criteria) == (
            Decision.FAIL,
            (criterion,),
        )


def test_a_fail_with_missing_required_evidence_is_not_demonstrated(plan, last):
    # (88) HT1-01 falla, pero celery.jsonl no se pudo leer: NOT_DEMONSTRATED prevalece.
    run = synthetic_run(plan, overrides={last: ms(20, status=500)})
    unreadable = rec.parse_stream(None, name="celery", run_id="x", readable=False)
    evaluation = scenario(plan, run, celery=unreadable).evaluate()
    assert "HT1-01" in evaluation.result.failed_criteria
    assert "ht1-04:celery-telemetry-not-inspected" in evaluation.result.invalidity_reasons
    assert evaluation.decision is ND


def test_an_apparatus_failure_beats_bad_thresholds(plan):
    # (89)
    run = replace(
        with_durations(plan, [900] * 50),
        faults=(ex10.ApparatusFault(ex10.FaultKind.INTERNAL_EXCEPTION, vu=2),),
    )
    evaluation = scenario(plan, run).evaluate()
    assert "HT1-02" in evaluation.result.failed_criteria
    assert evaluation.decision is ND


@pytest.mark.parametrize("error", ["timeout", "connection_reset", "incomplete_read"])
def test_a_valid_transport_failure_can_fail_the_run(plan, last, error):
    # (90) MD-02: un error de transporte es un fallo válido del request, no del aparato.
    failure = lambda i, s, e: exchange(i, s, s + 30 * MS, error=error, status=None)  # noqa: E731
    run = synthetic_run(plan, overrides={last: failure})
    evaluation = scenario(plan, run).evaluate()
    outcome = {a.request_key: a for a in run.attempts}[last].outcome
    assert outcome is ex10.AttemptOutcome.TRANSPORT_FAILED
    assert (evaluation.decision, evaluation.result.invalidity_reasons) == (Decision.FAIL, ())


def test_a_shared_report_id_is_an_ambiguity_not_a_failure(plan):
    # (93) Dos 201 con el mismo `id`: no se puede atribuir el Report a ninguno.
    twin = plan.items[1].request_key
    body = contract_body(report_id_for(plan.items[0].request_key))
    run = synthetic_run(plan, overrides={twin: ms(40, body=body)})
    evaluation = scenario(plan, run).evaluate()
    shared = [r for r in evaluation.result.invalidity_reasons if "report-id-shared" in r]
    assert len(shared) == 2 and evaluation.decision is ND


def test_a_duplicated_report_is_a_system_failure(plan, first):
    # §23: un segundo Report del mismo usuario e imagen es un fallo del sistema → FAIL.
    base = scenario(plan)
    image = plan.by_key()[first].image_sha256
    original = next(r for r in base.database.reports.values() if r.media_hashes == (image,))
    duplicate = replace(original, report_id="00000000-0000-4000-8000-000000000001")
    database = rec.DatabaseSnapshot({**base.database.reports, duplicate.report_id: duplicate})
    evaluation = replace(base, database=database).evaluate()
    success = {s.request_key: s for s in evaluation.successes}[first]
    assert (success.status, success.reason) == (ex10.SuccessStatus.NOT_SUCCESS, "report-rows:2")
    assert (evaluation.decision, evaluation.result.invalidity_reasons) == (Decision.FAIL, ())


def test_an_unattributable_report_is_contamination(plan):
    base = scenario(plan)
    foreign = rec.ReportRow(
        "00000000-0000-4000-8000-00000000abcd", None, "CREADO", 1, ("f" * 64,), (), 0, 0, ()
    )
    database = rec.DatabaseSnapshot({**base.database.reports, foreign.report_id: foreign})
    evaluation = replace(base, database=database).evaluate()
    assert "reconciliation:unattributable-reports:1" in evaluation.result.invalidity_reasons
    assert evaluation.decision is ND


@pytest.mark.parametrize(
    "changes",
    [{"broker_dynamic": 3}, {"broker_dynamic": None}, {"database": None}],
    ids=["broker-residual", "broker-not-inspected", "database-not-inspected"],
)
def test_uninspected_or_dirty_environment_is_not_demonstrated(plan, changes):
    assert scenario(plan, **changes).evaluate().decision is ND


def test_a_duplicated_http_event_is_an_ambiguity(plan, last):
    change = lambda es: [*es, event(es, "http.finished")]  # noqa: E731
    evaluation = scenario(plan, events_mutate=edit(last, change)).evaluate()
    assert f"telemetry:http-duplicate:{last}" in evaluation.result.invalidity_reasons
    assert evaluation.decision is ND


def test_not_demonstrated_is_never_degraded_to_fail(plan):
    # HT1-02 falla con cualquier evidencia y además falta una fuente: nunca FAIL (Decisión B).
    run = with_durations(plan, [900] * 50)
    for changes in ({"database": None}, {"broker_dynamic": None}, {"after": None}):
        evaluation = scenario(plan, run, **changes).evaluate()
        assert "HT1-02" in evaluation.result.failed_criteria, changes
        assert evaluation.decision is ND, changes


def test_the_decision_is_deterministic(plan):
    digests = {
        sha256_bytes(canonical_json_bytes(scenario(plan).evaluate().decision_payload()))
        for _ in range(5)
    }
    assert len(digests) == 1
