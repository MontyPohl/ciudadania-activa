"""EX-10 fase 3A: reconciliación de sólo lectura (ex10_reconcile). LOCAL TEST.

Streams técnicos, cardinalidad y atribución de cada `Report`, `CFG_ASYNC`, los 50 registros
canónicos de §12 y la lectura `READ ONLY` de la base de test. Sin stack S11 ni run oficial.
"""

from dataclasses import replace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.admin_app.models import Category
from apps.ai_client_app.models import AiClassification
from apps.core.enums import ReportSource, ReportStatus
from apps.core.services import audit
from apps.experiments_app import ex10_load as ex10
from apps.experiments_app import ex10_reconcile as rec
from apps.experiments_app.canonical import canonical_json_bytes, sha256_bytes
from apps.experiments_app.constants import Decision
from apps.experiments_app.tests.ex10_executor_support import (
    MS,
    RUN_ID,
    events_for,
    exchange,
    expected_config,
    ms,
    scenario,
    stream,
    synthetic_run,
)
from apps.reports_app.enums import MediaType
from apps.reports_app.models import Report, ReportMedia, ReportStatusHistory
from apps.rewards_app.services import ledger

User = get_user_model()


@pytest.fixture(scope="module")
def dataset():
    return ex10.load_dataset()


@pytest.fixture(scope="module")
def plan(dataset):
    return ex10.build_plan(dataset)


@pytest.fixture(scope="module")
def last(plan):
    return plan.for_vu(1)[-1].request_key


# --- Tiempo y streams ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-28T10:00:00Z", 1_790_589_600_000_000_000),
        ("2026-09-28T10:00:00.123456Z", 1_790_589_600_123_456_000),
        ("2026-09-28T10:00:00+00:00", None),
        ("2026-09-28T10:00:00", None),
        ("not-a-date Z", None),
        (1_790_589_600, None),
        (None, None),
    ],
)
def test_utc_ns_accepts_only_utc_iso_with_z(value, expected):
    assert rec.utc_ns(value) == expected


def test_an_absent_stream_is_zero_events_not_an_inspection_failure():
    parsed = rec.parse_stream(None, name="celery", run_id=RUN_ID)
    assert (parsed.events, parsed.problems) == ((), ())


def test_an_unreadable_stream_is_not_inspected():
    parsed = rec.parse_stream(None, name="celery", run_id=RUN_ID, readable=False)
    assert parsed.events is None and parsed.problems == ("telemetry:celery:unreadable",)


@pytest.mark.parametrize(
    ("raw", "problem"),
    [
        (b"{not json}\n", "telemetry:http:malformed:2"),
        (b'{"schema_version": "x", "run_id": "r", "scenario_id": "EX-10", "event": "e"}\n',
         "telemetry:http:foreign-or-invalid:2"),
        (b'["list"]\n', "telemetry:http:foreign-or-invalid:2"),
        (b"\xff\xfe\n", "telemetry:http:malformed:2"),
    ],
    ids=["malformed", "foreign-schema", "not-an-object", "not-utf8"],
)  # fmt: skip
def test_one_doubtful_line_discards_the_whole_stream(plan, raw, problem):
    good = stream(events_for(plan, synthetic_run(plan))[0], "http").events[0]
    parsed = rec.parse_stream(canonical_json_bytes(good) + raw, name="http", run_id=RUN_ID)
    assert parsed.events is None and problem in parsed.problems
    assert parsed.sha256 == sha256_bytes(canonical_json_bytes(good) + raw)


def test_a_stream_of_another_run_is_rejected(plan):
    http, _ = events_for(
        plan, synthetic_run(plan), run_id="s11-22222222-2222-5222-8222-222222222222"
    )
    parsed = stream(http, "http")
    assert parsed.events is None
    assert parsed.problems[0] == "telemetry:http:foreign-or-invalid:1"


def test_an_unterminated_stream_is_rejected(plan):
    http, _ = events_for(plan, synthetic_run(plan))
    raw = b"".join(canonical_json_bytes(e) for e in http).rstrip(b"\n")
    parsed = rec.parse_stream(raw, name="http", run_id=RUN_ID)
    assert parsed.events is None and "telemetry:http:unterminated" in parsed.problems


def test_an_oversized_stream_is_not_inspected():
    line = canonical_json_bytes(
        {"schema_version": rec.TELEMETRY_SCHEMA_VERSION, "run_id": RUN_ID, "scenario_id": "EX-10",
         "event": "http.finished"}
    )  # fmt: skip
    parsed = rec.parse_stream(line * (rec.MAX_STREAM_EVENTS + 1), name="http", run_id=RUN_ID)
    assert parsed.events is None and parsed.problems == ("telemetry:http:too-many-events",)


def test_terminal_and_finished_request_ids(plan):
    run = synthetic_run(plan)
    http, celery = events_for(plan, run)
    queued, finished = rec.terminal_request_ids(stream(celery, "celery"))
    assert queued == finished == {item.request_id for item in plan.items}
    assert rec.http_finished_request_ids(stream(http, "http")) == queued
    unreadable = rec.parse_stream(None, name="celery", run_id=RUN_ID, readable=False)
    assert rec.terminal_request_ids(unreadable) is None
    assert rec.http_finished_request_ids(unreadable) is None


def test_a_retry_is_not_terminal_until_the_last_attempt(plan):
    # `ai.classify_report` reintenta: un celery.finished con RETRY no termina la tarea.
    run = synthetic_run(plan)
    _, celery = events_for(plan, run)
    key = plan.items[0].request_id

    def is_final(e):
        return e["event"] == "celery.finished" and e["request_id"] == key

    retried = [dict(e, celery_state="retry") if is_final(e) else e for e in celery]
    queued, terminal = rec.terminal_request_ids(stream(retried, "celery"))
    assert key in queued and key not in terminal
    final = next(e for e in celery if is_final(e))
    queued, terminal = rec.terminal_request_ids(stream([*retried, final], "celery"))
    assert key in terminal


def test_every_task_of_a_request_must_be_terminal(plan):
    run = synthetic_run(plan)
    _, celery = events_for(plan, run)
    key = plan.items[0].request_id
    second = next(e for e in celery if e["event"] == "celery.queued" and e["request_id"] == key)
    queued, terminal = rec.terminal_request_ids(
        stream([*celery, dict(second, task_id="00000000-0000-4000-8000-0000000000aa")], "celery")
    )
    assert key in queued and key not in terminal


def test_events_of_requests_outside_the_plan_are_contamination(plan):
    run = synthetic_run(plan)
    http, celery = events_for(plan, run)
    stray = dict(http[0], request_id="s11-ex10-v1:not-in-plan")
    base = scenario(plan, http=stream([*http, stray], "http"))
    assert "telemetry:foreign-request-events:1" in base.reconcile().environment_invalidity
    assert base.evaluate().decision is Decision.NOT_DEMONSTRATED


def test_a_failed_task_is_terminal(plan):
    run = synthetic_run(plan)
    _, celery = events_for(plan, run)
    only_failed = [
        dict(e, event="celery.failed") if e["event"] == "celery.finished" else e for e in celery
    ]
    queued, terminal = rec.terminal_request_ids(stream(only_failed, "celery"))
    assert queued == terminal


# --- Cardinalidad y atribución -----------------------------------------------------------------


def test_a_coherent_run_attributes_every_report_to_its_attempt(plan):
    result = scenario(plan).reconcile()
    assert result.environment_invalidity == ()
    assert len(result.persisted) == 50
    assert set(result.attributed_reports.values()) == {i.request_key for i in plan.items}
    assert result.diagnostics["attributed_reports"] == {"accepted-attempt": 50}
    evidence = result.persisted[plan.items[0].request_key]
    assert (evidence.report_rows, evidence.owner_alias) == (1, plan.items[0].user_alias)


def test_a_report_without_a_complete_201_is_attributed_by_owner_and_image(plan, last):
    # Un 201 truncado: el Report existe, pero el intento es un fallo de transporte.
    truncated = lambda i, s, e: exchange(i, s, s + 30 * MS, error="incomplete_read")  # noqa: E731
    run = synthetic_run(plan, overrides={last: truncated})
    base = scenario(plan)
    database = base.database  # contiene el Report del ítem, creado por el servidor
    result = replace(base, run=run, database=database).reconcile()
    assert result.environment_invalidity == ()
    assert result.diagnostics["attributed_reports"] == {"accepted-attempt": 49, "other-attempt": 1}
    assert last not in result.persisted
    evaluation = replace(base, run=run, database=database).evaluate()
    assert (evaluation.decision, evaluation.throughput.n_ok_window) == (Decision.FAIL, 49)


def test_a_missing_report_is_an_inspected_absence(plan, last):
    result = scenario(plan, db_mutate=lambda k, row: None if k == last else row).reconcile()
    evidence = result.persisted[last]
    assert (evidence.report_rows, evidence.media_file_hashes, evidence.report_created_rewards) == (
        0,
        (),
        (),
    )
    assert result.environment_invalidity == ()


def test_an_uninspected_database_is_never_zero(plan):
    result = scenario(plan, database=None).reconcile()
    assert result.persisted is None
    assert "database:not-inspected" in result.environment_invalidity
    assert result.diagnostics["database_reports"] is None


def test_unreadable_http_telemetry_leaves_timestamps_uninspected(plan):
    unreadable = rec.parse_stream(None, name="http", run_id=RUN_ID, readable=False)
    base = scenario(plan, http=unreadable)
    result = base.reconcile()
    assert "telemetry:http:unreadable" in result.environment_invalidity
    facts = next(iter(result.async_evidence.facts.values()))
    assert (facts.http_finished_at_utc_ns, facts.api_process) == (None, None)
    assert base.evaluate().decision is Decision.NOT_DEMONSTRATED


def test_unreadable_celery_telemetry_is_never_a_fail(plan, last):
    unreadable = rec.parse_stream(None, name="celery", run_id=RUN_ID, readable=False)
    base = scenario(plan, celery=unreadable)
    result = base.reconcile()
    assert result.async_evidence is None
    assert "ht1-04:celery-telemetry-not-inspected" in result.environment_invalidity
    assert base.evaluate().decision is Decision.NOT_DEMONSTRATED


def test_the_broker_state_feeds_task_in_broker(plan):
    empty = scenario(plan).reconcile()
    assert {f.task_in_broker for f in empty.async_evidence.facts.values()} == {False}
    unknown = scenario(plan, broker_dynamic=None).reconcile()
    assert {f.task_in_broker for f in unknown.async_evidence.facts.values()} == {None}
    assert "broker:not-inspected" in unknown.environment_invalidity
    dirty = scenario(plan, broker_dynamic=2).reconcile()
    assert "broker:dynamic-residual" in dirty.environment_invalidity


# --- CFG_ASYNC ---------------------------------------------------------------------------------


def test_async_config_before_and_after():
    config, problems = rec.async_config(expected_config(), expected_config())
    assert problems == []
    assert ex10.evaluate_async_config(config) == []
    assert (config.worker_nodes_after, config.worker_max_concurrency_after) == (1, 2)


@pytest.mark.parametrize(
    ("before", "after", "problem"),
    [
        (None, None, "cfg-async:before-missing"),
        (expected_config(), None, "cfg-async:after-missing"),
        (expected_config(), expected_config(worker_task_always_eager=True),
         "cfg-async:changed-during-run"),
        (expected_config(worker_ai_adapter="gemini"), expected_config(worker_ai_adapter="gemini"),
         "cfg-async:worker-adapter"),
    ],
    ids=["before-missing", "after-missing", "changed", "adapter"],
)  # fmt: skip
def test_async_config_problems(before, after, problem):
    _, problems = rec.async_config(before, after)
    assert problem in problems


def test_the_broker_url_is_reported_without_its_value():
    assert rec.broker_label(ex10.BROKER_URL) == ex10.BROKER_URL
    assert rec.broker_label("redis://:hunter2@elsewhere:6379/0") == "not-protocol-broker"
    assert rec.broker_label(None) is None


def test_config_observation_round_trip():
    config = expected_config()
    assert rec.ConfigObservation.from_json(config.to_json()) == config
    assert rec.ConfigObservation.from_json({**config.to_json(), "extra": 1}) is None
    assert rec.ConfigObservation.from_json(None) is None


# --- Registros canónicos (§12, §13) --------------------------------------------------------------


_ROW_FIELDS = {
    "attempt_index", "vu_id", "vu_sequence", "request_key", "request_id",
    "t_start_monotonic_ns", "t_end_monotonic_ns", "started_wall_time_utc_ns",
    "finished_wall_time_utc_ns", "started_after_window", "completed_by_window_end",
    "final_http_status", "response_complete", "response_contract_valid", "transport_result",
    "error_phase", "error_class", "report_id", "latency_ms", "reconciliation_status",
    "throughput_success", "percentile_member",
}  # fmt: skip


def test_attempt_rows_have_the_frozen_fields_and_invariants(plan):
    base = scenario(plan)
    rows = rec.attempt_rows(plan, base.run, base.evaluate().successes)
    assert len(rows) == 50
    assert all(set(row) == _ROW_FIELDS for row in rows)
    assert [row["attempt_index"] for row in rows] == list(range(1, 51))
    for vu in plan.vus:
        mine = [row for row in rows if row["vu_id"] == vu]
        assert [row["vu_sequence"] for row in mine] == [1, 2, 3, 4, 5]
        for previous, current in zip(mine, mine[1:], strict=False):
            assert current["t_start_monotonic_ns"] >= previous["t_end_monotonic_ns"]
    assert {row["reconciliation_status"] for row in rows} == {"RECONCILED_SUCCESS"}


def test_attempt_rows_of_failures(plan, last):
    run = synthetic_run(
        plan,
        overrides={
            last: lambda i, s, e: exchange(i, s, s + 5_000_000, error="connection_reset",
                                           status=None, phase="connect"),
            plan.for_vu(2)[-1].request_key: ms(40, status=503),
        },
    )  # fmt: skip
    base = scenario(plan, run)
    rows = {r["request_key"]: r for r in rec.attempt_rows(plan, run, base.evaluate().successes)}
    reset = rows[last]
    assert (reset["final_http_status"], reset["response_complete"], reset["error_phase"]) == (
        None,
        False,
        "connect",
    )
    assert reset["reconciliation_status"] == "NOT_SUCCESS" and reset["report_id"] is None
    rejected = rows[plan.for_vu(2)[-1].request_key]
    assert (rejected["final_http_status"], rejected["response_complete"]) == (503, True)
    assert (rejected["error_class"], rejected["percentile_member"]) == ("http_5xx", False)


def test_the_reconciliation_payload_is_canonical_sanitized_and_deterministic(plan, dataset):
    def payload():
        base = scenario(plan)
        evaluation = base.evaluate()
        return rec.reconciliation_payload(
            plan,
            base.run,
            base.reconcile(),
            run_id=RUN_ID,
            successes=evaluation.successes,
            http=base.http,
            celery=base.celery,
            config_before=base.before,
            config_after=base.after,
            database=base.database,
        )

    first, second = payload(), payload()
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert ex10.sensitive_findings(first, dataset=dataset) == []
    text = canonical_json_bytes(first).decode()
    for forbidden in ("Authorization", "Bearer", "Cookie", "multipart", "base64", "latitude"):
        assert forbidden not in text
    assert first["sources"]["database_inspected"] is True
    assert len(first["ht1_04_attempts"]) == 50


def test_ht1_04_rows_need_inspected_telemetry(plan):
    base = scenario(plan)
    assert rec.ht1_04_rows(plan, base.evaluate().successes, None, base.database, ()) is None


# --- Base de datos real, en modo lectura --------------------------------------------------------


@pytest.fixture
def persisted_report(db):
    call_command("seed_catalogs", verbosity=0)
    user = User.objects.create_user(email="ex10-reconcile@example.invalid", password=None)
    category = Category.objects.get(slug="baches")
    report = Report.objects.create(
        citizen=user,
        category=category,
        description="LOCAL-TEST",
        status=ReportStatus.CREADO,
        priority=category.default_priority,
        source=ReportSource.PLATFORM,
        location=Point(-57.5, -25.3, srid=4326),
    )
    ReportMedia.objects.create(
        report=report,
        file_url="reports/local-test/photo.png",
        file_hash="a" * 64,
        media_type=MediaType.FOTO,
        uploaded_by=user,
    )
    ledger.on_report_created(report)
    audit.log(user, "report.created", {"report_id": str(report.id)})
    audit.log(None, "ai.report.classified", {"report_id": str(report.id)})
    AiClassification.objects.create(report=report, model_name=ex10.AI_MODEL_NAME)
    ReportStatusHistory.objects.create(report=report, previous_status="", new_status="CREADO")
    return report


def test_snapshot_database_reads_everything_g0_needs_without_writing(persisted_report):
    with CaptureQueriesContext(connection) as queries:
        snapshot = rec.snapshot_database()
    row = snapshot.reports[str(persisted_report.id)]
    assert row.citizen_id == str(persisted_report.citizen_id)
    assert row.status == "CREADO"
    assert row.created_at_utc_ns == rec.datetime_ns(persisted_report.created_at)
    assert row.media_hashes == ("a" * 64,)
    assert row.created_rewards == ((20, 20, "PENDIENTE"),)
    assert (row.created_audit_rows, row.classified_audit_rows) == (1, 1)
    assert row.classification_models == (ex10.AI_MODEL_NAME,)
    assert row.status_history == (("", "CREADO"),)
    statements = [q["sql"].strip().upper() for q in queries.captured_queries]
    assert "SET TRANSACTION READ ONLY" in statements
    writes = [s for s in statements if s.split()[0] in {"INSERT", "UPDATE", "DELETE", "CREATE"}]
    assert writes == []
    selected = " ".join(s for s in statements if s.startswith("SELECT"))
    for column in ("DESCRIPTION", "LOCATION", "EMAIL", "PASSWORD", "FILE_URL"):
        assert f'."{column}"' not in selected


def test_the_snapshot_transaction_refuses_writes(persisted_report):
    # La transacción `READ ONLY` es la del snapshot: una escritura dentro de ella falla.
    from django.db import DatabaseError, transaction

    with pytest.raises(DatabaseError), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        Report.objects.filter(pk=persisted_report.pk).update(description="x")
