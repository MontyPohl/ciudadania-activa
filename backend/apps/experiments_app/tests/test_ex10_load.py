"""EX-10 fase 1 (`s11-ex10-v1`): contrato, plan, generador y cálculo puro.

LOCAL TEST / NON-EXPERIMENTAL. Ninguna prueba abre un socket real: la red queda
bloqueada, el generador usa un transporte doble y el transporte de biblioteca estándar
recibe sockets falsos. No se usa Docker, no se crea ningún run, no se escribe evidencia
y no se importa Django. Los tokens tienen forma de JWT, pero son inventados.
"""

import _thread
import copy
import errno
import http.client
import itertools
import json
import pickle
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from apps.experiments_app import constants
from apps.experiments_app import ex10_load as ex10
from apps.experiments_app.canonical import canonical_json_bytes, sha256_file
from apps.experiments_app.tests import ex10_signal_child as signal_child
from apps.experiments_app.tests.ex10_signal_child import run_signal_scenario

Outcome = ex10.AttemptOutcome
Fault = ex10.FaultKind
Evidence = ex10.EvidenceStatus
Criterion = ex10.CriterionStatus

RUN_ID = "s11-00000000-0000-5000-8000-0000000000a0"
T0 = 1_000_000_000_000
T0_UTC = 1_790_000_000_000_000_000
MS = 1_000_000
API = ex10.ProcessRef("backend-api", 11)
WORKER = ex10.ProcessRef("worker", 22)
CONFIG = ex10.AsyncConfigEvidence(
    api_task_always_eager=False,
    worker_task_always_eager=False,
    broker_url="redis://redis:6379/11",
    worker_nodes_before=1,
    worker_max_concurrency_before=2,
    worker_nodes_after=1,
    worker_max_concurrency_after=2,
    worker_fake_ai_enabled=True,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Cualquier intento de abrir una conexión real hace fallar la prueba."""

    def blocked(*args, **kwargs):
        raise AssertionError("LOCAL TEST / NON-EXPERIMENTAL: la red está prohibida.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture(scope="module")
def dataset():
    return ex10.load_dataset()


@pytest.fixture(scope="module")
def plan(dataset):
    return ex10.build_plan(dataset)


def make_tokens(plan):
    return {
        item.user_alias: f"eyJhbGciOiJIUzI1NiJ9.eyJsb2NhbCI6InRlc3QifQ.LOCAL-TEST-{item.user_alias}"
        for item in plan.items
    }


def make_category_ids(plan):
    return {
        item.category_slug: str(uuid.uuid5(uuid.NAMESPACE_URL, f"ex10-test:{item.category_slug}"))
        for item in plan.items
    }


def make_prepared(dataset, plan):
    return ex10.prepare_requests(
        dataset, plan, run_id=RUN_ID, tokens=make_tokens(plan), category_ids=make_category_ids(plan)
    )


@pytest.fixture(scope="module")
def tokens(plan):
    return make_tokens(plan)


@pytest.fixture(scope="module")
def category_ids(plan):
    return make_category_ids(plan)


@pytest.fixture(scope="module")
def prepared(dataset, plan, tokens, category_ids):
    return ex10.prepare_requests(
        dataset, plan, run_id=RUN_ID, tokens=tokens, category_ids=category_ids
    )


# --- Dobles ----------------------------------------------------------------------------


def report_uuid(request_key):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ex10-test-report:{request_key}"))


def created_body(request_key):
    return json.dumps(
        {
            "id": report_uuid(request_key),
            "status": "CREADO",
            "reward": {"impact_points": 20, "credits": 20, "status": "PENDIENTE"},
        }
    ).encode()


class FakeTransport:
    """Responde 201 contractual salvo lo indicado en `responses`.

    `responses[key]` es `("status", código, cuerpo)` o `("error", clase, detalle)`.
    `hook(request)` corre entre `t_start` y `t_end`: puede bloquear, fallar o saltar.
    """

    def __init__(self, responses=None, *, hook=None):
        self.responses = responses or {}
        self.hook = hook
        self.calls = []
        self._lock = threading.Lock()

    def send(self, request, *, clock, wall_clock):
        key = request.item.request_key
        with self._lock:
            self.calls.append((key, threading.current_thread().name))
        t_start_utc = wall_clock()
        t_start = clock()
        if self.hook is not None:
            self.hook(request)
        kind, value, extra = self.responses.get(key, ("status", 201, created_body(key)))
        t_end = clock()
        if kind == "status":
            return ex10.Exchange(
                t_start, t_end, t_start_utc, wall_clock(), http_status=value, body=extra
            )
        return ex10.Exchange(
            t_start, t_end, t_start_utc, wall_clock(), error_class=value, error_detail=extra
        )


class ThreadKill(BaseException):
    """Simula la muerte abrupta de un hilo VU."""


def run_generator(plan, prepared, transport, **kwargs):
    kwargs.setdefault("barrier_timeout_s", 5.0)
    kwargs.setdefault("join_timeout_s", 30.0)
    return ex10.LoadGenerator(plan, prepared, transport, **kwargs).run()


def exchange_for(item, start_offset, end_offset, response=None):
    kind, value, extra = response or ("status", 201, created_body(item.request_key))
    common = (T0 + start_offset, T0 + end_offset, T0_UTC + start_offset, T0_UTC + end_offset)
    if kind == "status":
        return ex10.Exchange(*common, http_status=value, body=extra)
    return ex10.Exchange(*common, error_class=value, error_detail=extra)


def synthetic_run(plan, *, timings=None, responses=None):
    """Run sin hilos: por defecto, cada ítem dura 50 ms y el VU lo encadena sin pausa."""
    timings = timings or {}
    responses = responses or {}
    attempts = []
    for item in plan.items:
        start = (item.sequence_for_user - 1) * 100 * MS
        start, end = timings.get(item.request_key, (start, start + 50 * MS))
        exchange = exchange_for(item, start, end, responses.get(item.request_key))
        attempts.append(ex10.attempt_record(item, exchange, t0_ns=T0))
    return ex10.GeneratorRun(plan.sha256, T0, T0_UTC, tuple(attempts))


def persisted_for(plan, run):
    """Reconciliación perfecta de cada intento `HTTP_ACCEPTED`."""
    by_key = plan.by_key()
    return {
        attempt.request_key: ex10.PersistedReportEvidence(
            request_key=attempt.request_key,
            report_id=attempt.report_id,
            report_rows=1,
            owner_alias=by_key[attempt.request_key].user_alias,
            media_file_hashes=(by_key[attempt.request_key].image_sha256,),
            report_created_rewards=((20, 20, "PENDIENTE"),),
            report_created_audit_rows=1,
        )
        for attempt in run.attempts
        if attempt.outcome is Outcome.HTTP_ACCEPTED
    }


def accepted_request_ids(plan, run):
    by_key = plan.by_key()
    return [
        by_key[a.request_key].request_id for a in run.attempts if a.outcome is Outcome.HTTP_ACCEPTED
    ]


def async_evidence_for(request_ids):
    """HT1-04 perfecto. La tarea termina *después* del HTTP: nunca se compara."""
    events = []
    facts = {}
    for number, request_id in enumerate(request_ids):
        created = T0_UTC + number * 10 * MS
        enqueue = created + 1_000
        http_finished = enqueue + 1_000
        start = enqueue + 5 * MS
        finish = start + 2 * MS
        task_id = f"task-{number:02d}"
        events += [
            ex10.CeleryEventEvidence(
                "celery.queued",
                request_id,
                task_id,
                ex10.TASK_NAME,
                API,
                enqueue_at_utc_ns=enqueue,
                db_transaction_open=False,
                status="queued",
            ),
            ex10.CeleryEventEvidence(
                "celery.started",
                request_id,
                task_id,
                ex10.TASK_NAME,
                WORKER,
                start_at_utc_ns=start,
                status="started",
            ),
            ex10.CeleryEventEvidence(
                "celery.finished",
                request_id,
                task_id,
                ex10.TASK_NAME,
                WORKER,
                start_at_utc_ns=start,
                finish_at_utc_ns=finish,
                status="finished",
                celery_state="success",
            ),
        ]
        facts[request_id] = ex10.AsyncFacts(
            request_id=request_id,
            report_created_at_utc_ns=created,
            http_finished_at_utc_ns=http_finished,
            api_process=API,
            ai_classification_models=(ex10.AI_MODEL_NAME,),
            final_status="CLASIFICADO",
            ai_classified_audit_rows=1,
            task_in_broker=False,
            enqueue_failure_logged=False,
        )
    return ex10.Ht104Evidence(CONFIG, tuple(events), facts)


def without_events(evidence, request_id, *names):
    events = tuple(
        e for e in evidence.events if not (e.request_id == request_id and e.event in names)
    )
    return replace(evidence, events=events)


def with_event(evidence, request_id, name, **changes):
    events = tuple(
        replace(e, **changes) if e.request_id == request_id and e.event == name else e
        for e in evidence.events
    )
    return replace(evidence, events=events)


def with_facts(evidence, request_id, **changes):
    facts = dict(evidence.facts)
    facts[request_id] = replace(facts[request_id], **changes)
    return replace(evidence, facts=facts)


def evaluate_complete(plan, run, **overrides):
    """Evalúa con reconciliación y HT1-04 perfectos, salvo lo indicado."""
    overrides.setdefault("persisted", persisted_for(plan, run))
    overrides.setdefault("async_evidence", async_evidence_for(accepted_request_ids(plan, run)))
    return ex10.evaluate_run(plan, run, **overrides)


def record_201(duration, index=0):
    return ex10.AttemptRecord(
        request_key=f"k-{index}",
        request_id=f"r-{index}",
        vu=1,
        user_alias="alias",
        sequence_for_user=1,
        state=ex10.ItemState.COMPLETED,
        send_count=1,
        outcome=Outcome.HTTP_ACCEPTED,
        t_start_ns=0,
        t_end_ns=duration,
        duration_ns=duration,
        http_status=201,
    )


def percentiles_of(durations):
    return ex10.latency_percentiles([record_201(d, n) for n, d in enumerate(durations)])


# --- Contrato del protocolo ------------------------------------------------------------


def test_protocol_identity_is_the_frozen_g0_contract():
    identity = ex10.protocol_identity()
    assert identity["protocol_id"] == "s11-ex10-v1"
    assert identity["methodology_version"] == "S11A-v1.1" == constants.METHODOLOGY_VERSION
    assert identity["scenario_id"] == "EX-10"
    assert identity["decision_commit"] == "1693d0ae562bfe777c496b616c9f8c8d5fa8a591"
    assert identity["decision_addendum_commit"] == "db7fe6649213a8d26f4ee60d36a797ee54f80322"
    assert identity["dataset"] == {
        "id": "s11-load-v1",
        "seed": 20260921,
        "sha256": "c6883b59dc861fb07fae730594c929fdd572a9ed71aee6d8b85c87f71ede0acb",
    }
    assert identity["load"] == {
        "virtual_users": 10,
        "requests_per_user": 5,
        "total_requests": 50,
        "barrier_parties": 10,
        "window_ns": 60_000_000_000,
        "pacing": "closed-loop-no-think-time",
        "retries": 0,
    }
    assert identity["request"]["timeout_ns"] == 60_000_000_000
    assert identity["request"]["connection"] == "close"
    assert identity["system_under_test"] == {
        "throttle_report_create": "5/hour",
        "worker_concurrency": 2,
        "ai_model_name": "s11-deterministic-fake-v1",
        "broker_url": "redis://redis:6379/11",
    }
    assert identity["thresholds"]["ht1_01_min_per_minute"] == 50
    assert identity["thresholds"]["ht1_02_p50_max_ns"] == 800_000_000
    assert identity["thresholds"]["ht1_03_p99_max_ns"] == 2_000_000_000
    assert ex10.verify_protocol_identity(identity) == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("protocol_id", "s11-ex10-v2"),
        ("methodology_version", "S11A-v1.2"),
        ("load", {"virtual_users": 10}),
        ("drain_limit_seconds", 301),
    ],
)
def test_any_other_protocol_value_is_rejected(key, value):
    candidate = {**ex10.protocol_identity(), key: value}
    assert ex10.verify_protocol_identity(candidate) == [f"protocol-identity:{key}"]


G0_COMMIT = "1693d0ae562bfe777c496b616c9f8c8d5fa8a591"
G0_A1_COMMIT = "db7fe6649213a8d26f4ee60d36a797ee54f80322"
G0_A2_COMMIT = "2bab6cabe4108e1eddbb319c3f859ed75c914004"


def test_identity_records_g0_g0_a1_and_g0_a2_as_three_distinct_commits():
    identity = ex10.protocol_identity()
    assert ex10.DECISION_COMMIT == G0_COMMIT
    assert ex10.G0_A1_DECISION_COMMIT == G0_A1_COMMIT
    assert ex10.G0_A2_DECISION_COMMIT == G0_A2_COMMIT
    assert (
        identity["decision_commit"],
        identity["decision_addendum_commit"],
        identity["decision_addendum_a2_commit"],
    ) == (G0_COMMIT, G0_A1_COMMIT, G0_A2_COMMIT)
    assert len({G0_COMMIT, G0_A1_COMMIT, G0_A2_COMMIT}) == 3


@pytest.mark.parametrize(
    ("change", "reasons"),
    [
        ({"drop": "decision_commit"}, ["protocol-identity:decision_commit"]),
        ({"decision_commit": G0_A1_COMMIT}, ["protocol-identity:decision_commit"]),
        ({"decision_commit": G0_COMMIT[:7]}, ["protocol-identity:decision_commit"]),
        ({"drop": "decision_addendum_commit"}, ["protocol-identity:decision_addendum_commit"]),
        ({"decision_addendum_commit": G0_COMMIT}, ["protocol-identity:decision_addendum_commit"]),
        ({"decision_addendum_commit": None}, ["protocol-identity:decision_addendum_commit"]),
        (
            {"drop": "decision_addendum_a2_commit"},
            ["protocol-identity:decision_addendum_a2_commit"],
        ),
        (
            {"decision_addendum_a2_commit": G0_A1_COMMIT},
            ["protocol-identity:decision_addendum_a2_commit"],
        ),
        (
            {"decision_addendum_a2_commit": None},
            ["protocol-identity:decision_addendum_a2_commit"],
        ),
        (
            {"decision_addendum_a2_commit": G0_A2_COMMIT[:7]},
            ["protocol-identity:decision_addendum_a2_commit"],
        ),
        (
            {"decision_addendum_a2_commit": [G0_A2_COMMIT]},
            ["protocol-identity:decision_addendum_a2_commit"],
        ),
        (
            {"decision_addendum_commit": "0" * 40},
            ["protocol-identity:decision_addendum_commit"],
        ),
    ],
)
def test_identity_rejects_a_missing_or_wrong_g0_g0_a1_or_g0_a2_commit(change, reasons):
    candidate = dict(ex10.protocol_identity())
    if "drop" in change:
        candidate.pop(change["drop"])
    else:
        candidate.update(change)
    assert ex10.verify_protocol_identity(candidate) == reasons


@pytest.mark.parametrize(
    "extra",
    [
        {"decision_commits": [G0_COMMIT, G0_A1_COMMIT]},
        {"g0_a1_decision_commit": "0" * 40},
        {"decision_addendum_commit_override": "0" * 40},
        {"decision_addendum": None},
    ],
)
def test_identity_rejects_extra_fields_that_try_to_replace_the_commits(extra):
    base = ex10.protocol_identity()
    # Añadido junto a los campos correctos, el extra se rechaza aunque valga `None`.
    assert ex10.verify_protocol_identity({**base, **extra}) == [
        f"protocol-identity:{key}" for key in extra
    ]
    # En lugar de los campos correctos, se rechazan los dos ausentes y el extra.
    replaced = {k: v for k, v in base.items() if not k.startswith("decision_")}
    replaced["decision_reference"] = base["decision_reference"]
    expected = (
        "decision_commit",
        "decision_addendum_commit",
        "decision_addendum_a2_commit",
        *extra,
    )
    assert ex10.verify_protocol_identity({**replaced, **extra}) == sorted(
        f"protocol-identity:{key}" for key in expected
    )


def test_documentary_commits_stay_outside_the_plan_hash(dataset, plan, monkeypatch):
    # El plan canónico no incluye la identidad documental: añadir G0-A1 no cambia
    # `PLAN_SHA256`, que sólo deriva de `s11-load-v1` bajo `s11-ex10-v1`.
    serialized = plan.to_json()
    text = canonical_json_bytes(serialized).decode()
    assert G0_COMMIT not in text and G0_A1_COMMIT not in text and G0_A2_COMMIT not in text
    assert not any(key.startswith("decision") for key in serialized)
    monkeypatch.setattr(ex10, "DECISION_COMMIT", "1" * 40)
    monkeypatch.setattr(ex10, "G0_A1_DECISION_COMMIT", "2" * 40)
    monkeypatch.setattr(ex10, "G0_A2_DECISION_COMMIT", "3" * 40)
    assert ex10.protocol_identity()["decision_addendum_commit"] == "2" * 40
    assert ex10.protocol_identity()["decision_addendum_a2_commit"] == "3" * 40
    assert ex10.build_plan(dataset).sha256 == plan.sha256 == ex10.PLAN_SHA256
    assert ex10.PLAN_SHA256 == "2b50aa48cfa975029ab38a4c0e17430f67469b185fce5aaed89414728e6934d7"


def test_wire_formats_match_the_runtime_validators():
    source = (Path(ex10.__file__).parent / "runtime.py").read_text(encoding="utf-8")
    for name, pattern in (
        ("_RUN_ID", ex10._RUN_ID_FORMAT.pattern),
        ("_REQUEST_ID", ex10._REQUEST_ID_FORMAT.pattern),
    ):
        match = re.search(rf'^{name} = re\.compile\(r"([^"]+)"\)', source, re.MULTILINE)
        assert match is not None and match.group(1) == pattern


def test_module_is_stdlib_only_and_writes_no_evidence():
    source = Path(ex10.__file__).read_text(encoding="utf-8")
    imported = set(re.findall(r"^(?:from|import) ([\w.]+)", source, re.MULTILINE))
    assert imported <= {
        "__future__",
        "base64",
        "binascii",
        "bisect",
        "contextlib",
        "errno",
        "hashlib",
        "http.client",
        "io",
        "ipaddress",
        "json",
        "math",
        "re",
        "socket",
        "threading",
        "time",
        "uuid",
        "collections",
        "collections.abc",
        "dataclasses",
        "enum",
        "pathlib",
        "typing",
        ".canonical",
        ".constants",
        ".redaction",
    }
    for forbidden in ("django", "s11-runs", "EvidenceStore", "write_text", "write_bytes", "open("):
        assert forbidden not in source


# --- Dataset ---------------------------------------------------------------------------


def test_dataset_sha_matches_the_frozen_protocol(dataset):
    registry = json.loads((ex10.DATASET_PATH.parent / "dataset-registry.json").read_text())
    assert dataset.sha256 == ex10.DATASET_SHA256
    assert sha256_file(ex10.DATASET_PATH) == ex10.DATASET_SHA256
    assert registry["datasets"]["s11-load-v1"]["sha256"] == ex10.DATASET_SHA256


def test_altered_dataset_is_rejected(tmp_path):
    payload = json.loads(ex10.DATASET_PATH.read_bytes())
    payload["requests"][0]["description"] += " alterado"
    altered = tmp_path / "s11-load-v1.json"
    altered.write_bytes(canonical_json_bytes(payload))
    with pytest.raises(ex10.Ex10DatasetError) as rejected:
        ex10.load_dataset(altered)
    assert rejected.value.reasons[0].startswith("dataset-sha256:")

    # Mismos datos, bytes distintos: tampoco se acepta.
    with pytest.raises(ex10.Ex10DatasetError) as reformatted:
        ex10.parse_dataset(ex10.DATASET_PATH.read_bytes() + b" ")
    assert "dataset-not-canonical" in reformatted.value.reasons
    with pytest.raises(ex10.Ex10DatasetError) as garbage:
        ex10.parse_dataset(b"\xff not json")
    assert "dataset-json" in garbage.value.reasons


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda p: p["requests"][1].update(sequence_for_user=1), "dataset-user-sequence:"),
        (lambda p: p["requests"][0]["image"].update(sha256="0" * 64), "dataset-request-image:"),
        (lambda p: p["requests"][0].update(extra="x"), "dataset-request-fields:"),
        (lambda p: p["requests"][0].update(latitude=float("nan")), "dataset-request-location:"),
        (lambda p: p["users"][0].update(verified=False), "dataset-user:"),
        (lambda p: p["requests"].pop(), "dataset-requests:49"),
        (lambda p: p.update(seed=1), "dataset-field:seed"),
        (lambda p: p["load_shape"].update(total_requests=51), "dataset-field:load_shape"),
    ],
)
def test_dataset_structure_is_validated_beyond_the_hash(dataset, mutate, reason):
    payload = copy.deepcopy(dict(dataset.payload))
    assert ex10.validate_dataset_payload(payload) == []
    mutate(payload)
    assert any(r.startswith(reason) for r in ex10.validate_dataset_payload(payload))


# --- Plan ------------------------------------------------------------------------------


def test_plan_has_exactly_ten_users_by_five_requests(plan):
    assert len(plan.items) == 50
    assert plan.vus == tuple(range(1, 11))
    assert Counter(item.vu for item in plan.items) == {vu: 5 for vu in range(1, 11)}
    assert len({item.user_alias for item in plan.items}) == 10
    assert all(len({i.user_alias for i in plan.for_vu(vu)}) == 1 for vu in plan.vus)
    assert ex10.validate_plan(plan) == []


def test_plan_orders_each_user_by_sequence(plan, dataset):
    for vu in plan.vus:
        items = plan.for_vu(vu)
        assert [item.sequence_for_user for item in items] == [1, 2, 3, 4, 5]
        assert items[0].user_alias == dataset.payload["users"][vu - 1]["alias"]
    assert [item.index for item in plan.items] == list(range(1, 51))
    assert [item.request_key for item in plan.items] == [
        f"load-request-{n:02d}" for n in range(1, 51)
    ]


def test_plan_identifiers_are_unique(plan):
    assert len({item.request_key for item in plan.items}) == 50
    assert len({item.request_id for item in plan.items}) == 50
    assert len({item.image_sha256 for item in plan.items}) == 50


def test_request_ids_are_deterministic_and_wire_valid(plan, dataset):
    again = ex10.build_plan(ex10.load_dataset())
    assert [i.request_id for i in again.items] == [i.request_id for i in plan.items]
    assert plan.items[0].request_id == "s11-ex10-v1:load-request-01"
    assert all(ex10._REQUEST_ID_FORMAT.fullmatch(item.request_id) for item in plan.items)
    assert all(ex10.request_id_for(i.request_key) == i.request_id for i in plan.items)


def test_plan_sha_is_deterministic_and_pinned(plan):
    assert plan.sha256 == ex10.build_plan(ex10.load_dataset()).sha256 == ex10.PLAN_SHA256
    changed = replace(plan, items=(replace(plan.items[0], category_slug="otros"), *plan.items[1:]))
    assert changed.sha256 != plan.sha256


def test_plan_contains_references_but_no_images_coordinates_or_tokens(plan, dataset, tokens):
    serialized = plan.to_json()
    assert set(serialized["items"][0]) == {
        "index",
        "request_key",
        "request_id",
        "vu",
        "user_alias",
        "dataset_user_id",
        "sequence_for_user",
        "category_slug",
        "image_sha256",
    }
    assert ex10.sensitive_findings(serialized, dataset=dataset, secrets=tokens.values()) == []


@pytest.mark.parametrize(
    ("tamper", "reason"),
    [
        (lambda items: items[:-1], "plan-size:49"),
        (lambda items: (items[1], items[0], *items[2:]), "plan-index"),
        (lambda items: (replace(items[0], request_id="otro"), *items[1:]), "plan-request-id:"),
        (
            lambda items: (*items[:-1], replace(items[-1], request_key=items[0].request_key)),
            "plan-request-key-duplicate",
        ),
        (
            lambda items: (
                *items[:5],
                replace(items[5], user_alias=items[0].user_alias),
                *items[6:],
            ),
            "plan-vu-user:2",
        ),
    ],
)
def test_tampered_plans_are_rejected(plan, tamper, reason):
    tampered = replace(plan, items=tuple(tamper(plan.items)))
    assert any(r.startswith(reason) for r in ex10.validate_plan(tampered))


# --- Requests precodificados -----------------------------------------------------------


def _multipart_parts(request):
    head, _, body = request.wire.partition(b"\r\n\r\n")
    segments = body.split(f"--{request.boundary}".encode())
    assert segments[0] == b"" and segments[-1] == b"--\r\n"
    parts = {}
    for segment in segments[1:-1]:
        assert segment.startswith(b"\r\n") and segment.endswith(b"\r\n")
        headers, _, content = segment[2:-2].partition(b"\r\n\r\n")
        name = re.search(rb'name="([^"]+)"', headers).group(1).decode()
        parts[name] = (headers, content)
    return head, body, parts


def test_requests_are_precoded_multipart_with_frozen_headers(
    dataset, plan, prepared, tokens, category_ids
):
    item = plan.items[0]
    request = prepared[item.request_key]
    head, body, parts = _multipart_parts(request)
    lines = head.decode("ascii").split("\r\n")
    headers = dict(line.split(": ", 1) for line in lines[1:])
    assert lines[0] == "POST /api/v1/reports/ HTTP/1.1"
    assert headers == {
        "Host": "backend:8000",
        "Authorization": f"Bearer {tokens[item.user_alias]}",
        "X-S11-Run-ID": RUN_ID,
        "X-S11-Scenario-ID": "EX-10",
        "X-S11-Request-ID": item.request_id,
        "Content-Type": f"multipart/form-data; boundary={request.boundary}",
        "Content-Length": str(len(body)),
        "Connection": "close",
    }
    assert request.body_length == len(body)
    assert request.boundary == ex10.boundary_for(item.request_key)
    row = dataset.request_rows()[item.request_key]
    assert list(parts) == ["category", "description", "lat", "lng", "photo"]
    assert parts["category"][1].decode() == category_ids[item.category_slug]
    assert parts["description"][1].decode() == row["description"]
    assert float(parts["lat"][1].decode()) == row["latitude"]
    assert float(parts["lng"][1].decode()) == row["longitude"]
    assert b'filename="s11-load-01.png"' in parts["photo"][0]
    assert b"Content-Type: image/png" in parts["photo"][0]
    assert ex10.sha256_bytes(parts["photo"][1]) == item.image_sha256


def test_precoding_is_deterministic(dataset, plan, prepared, tokens, category_ids):
    again = ex10.prepare_requests(
        dataset, plan, run_id=RUN_ID, tokens=tokens, category_ids=category_ids
    )
    assert {k: r.wire for k, r in again.items()} == {k: r.wire for k, r in prepared.items()}
    assert len({r.boundary for r in prepared.values()}) == 50


def test_prepared_requests_keep_tokens_in_memory_only(prepared, tokens):
    request = prepared["load-request-01"]
    assert tokens["load-citizen-01"] not in repr(request)
    assert "Bearer" not in repr(request)
    with pytest.raises(TypeError):
        pickle.dumps(request)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"run_id": "s11-no-valido"}, "run-id"),
        ({"bad_category": "baches"}, "category-id-format:baches"),
        ({"drop_token": "load-citizen-03"}, "tokens:aliases"),
        ({"bad_token": "load-citizen-04"}, "token-format:load-citizen-04"),
        ({"drop_category": "baches"}, "category-id-missing:baches"),
    ],
)
def test_preparation_rejects_incomplete_inputs_without_leaking_tokens(
    dataset, plan, tokens, category_ids, changes, reason
):
    tokens = dict(tokens)
    category_ids = dict(category_ids)
    kwargs = {"run_id": RUN_ID}
    if "drop_token" in changes:
        tokens.pop(changes["drop_token"])
    elif "bad_token" in changes:
        tokens[changes["bad_token"]] = "corto"
    elif "drop_category" in changes:
        category_ids.pop(changes["drop_category"])
    elif "bad_category" in changes:
        category_ids[changes["bad_category"]] = "no-es-uuid"
    else:
        kwargs.update(changes)
    with pytest.raises(ex10.Ex10PreparationError) as rejected:
        ex10.prepare_requests(dataset, plan, tokens=tokens, category_ids=category_ids, **kwargs)
    assert reason in rejected.value.reasons
    assert not any(token in str(rejected.value) for token in tokens.values())


# --- Generador -------------------------------------------------------------------------


def test_generator_emits_exactly_fifty_requests(plan, prepared):
    transport = FakeTransport()
    run = run_generator(plan, prepared, transport)
    assert len(transport.calls) == 50
    assert len(run.attempts) == 50
    assert all(a.state is ex10.ItemState.COMPLETED for a in run.attempts)
    assert all(a.outcome is Outcome.HTTP_ACCEPTED for a in run.attempts)
    assert run.faults == ()
    assert ex10.apparatus_invalidity(plan, run) == []


def test_each_item_is_sent_exactly_once(plan, prepared):
    transport = FakeTransport()
    run = run_generator(plan, prepared, transport)
    sent = Counter(key for key, _ in transport.calls)
    assert sent == {item.request_key: 1 for item in plan.items}
    assert all(a.send_count == 1 for a in run.attempts)


def test_failed_requests_are_never_retried(plan, prepared):
    responses = {
        "load-request-01": ("error", "timeout", "TimeoutError"),
        "load-request-07": ("status", 503, b"{}"),
        "load-request-12": ("error", "connection_refused", "ConnectionRefusedError"),
        "load-request-20": ("status", 429, b'{"detail": "throttled"}'),
    }
    transport = FakeTransport(responses)
    run = run_generator(plan, prepared, transport)
    assert Counter(key for key, _ in transport.calls) == {i.request_key: 1 for i in plan.items}
    assert ex10.GENERATOR_RETRIES == 0
    failed = {a.request_key for a in run.attempts if a.outcome is not Outcome.HTTP_ACCEPTED}
    assert failed == set(responses)
    assert ex10.apparatus_invalidity(plan, run) == []


def test_item_state_machine_refuses_a_second_send(plan):
    ledger = ex10._ItemLedger(plan)
    ledger.begin("load-request-01")
    with pytest.raises(ex10.Ex10ApparatusError):
        ledger.begin("load-request-01")
    with pytest.raises(ex10.Ex10ApparatusError):
        ledger.finish("load-request-02", None)


def test_generator_runs_only_once_and_needs_the_whole_plan(plan, prepared):
    generator = ex10.LoadGenerator(plan, prepared, FakeTransport(), barrier_timeout_s=5.0)
    generator.run()
    with pytest.raises(ex10.Ex10ApparatusError):
        generator.run()
    partial = {k: v for k, v in prepared.items() if k != "load-request-50"}
    with pytest.raises(ex10.Ex10PreparationError) as rejected:
        ex10.LoadGenerator(plan, partial, FakeTransport())
    assert "prepared:request-keys" in rejected.value.reasons


def test_barrier_releases_all_ten_vus_together(plan, prepared):
    first = {plan.for_vu(vu)[0].request_key for vu in plan.vus}
    # Sólo se atraviesa si los 10 primeros requests están en vuelo a la vez.
    together = threading.Barrier(10, timeout=5)

    def hook(request):
        if request.item.request_key in first:
            together.wait()

    transport = FakeTransport(hook=hook)
    run = run_generator(plan, prepared, transport)
    assert run.faults == ()
    assert run.t0_ns is not None and run.t0_utc_ns is not None
    assert run.window_end_ns == run.t0_ns + 60_000_000_000
    assert all(a.t_start_ns >= run.t0_ns for a in run.attempts)
    assert len({name for key, name in transport.calls if key in first}) == 10


def test_each_vu_runs_closed_loop_without_pause(plan, prepared, monkeypatch):
    def no_pause(*args):
        raise AssertionError("MD-10 A: el generador no hace pausas.")

    monkeypatch.setattr(time, "sleep", no_pause)
    transport = FakeTransport()
    run = run_generator(plan, prepared, transport)
    for vu in plan.vus:
        name = f"ex10-vu-{vu:02d}"
        keys = [key for key, thread in transport.calls if thread == name]
        assert keys == [item.request_key for item in plan.for_vu(vu)]
        attempts = [a for a in run.attempts if a.vu == vu]
        for previous, current in zip(attempts, attempts[1:], strict=False):
            assert current.t_start_ns >= previous.t_end_ns
            # Sin tiempo de pensar: el siguiente sale en cuanto termina el anterior. La
            # cota es holgada para no depender de la carga de la máquina.
            assert current.t_start_ns - previous.t_end_ns < 250 * MS


def test_t0_is_taken_once_inside_the_barrier_action(plan, prepared):
    calls = []

    def clock():
        calls.append(threading.current_thread().name)
        return time.monotonic_ns()

    generator = ex10.LoadGenerator(plan, prepared, FakeTransport(), clock=clock)
    run = generator.run()
    # Una lectura en la acción de la barrera y dos por envío (t_start y t_end).
    assert len(calls) == 1 + 2 * 50
    assert run.t0_ns <= min(a.t_start_ns for a in run.attempts)
    with pytest.raises(ex10.Ex10ApparatusError):
        generator._release()


class ShiftedClock:
    def __init__(self):
        self.offset = 0

    def __call__(self):
        return time.monotonic_ns() + self.offset

    def jump(self, nanoseconds):
        self.offset += nanoseconds


def test_late_requests_are_still_sent_and_flagged(plan, prepared):
    clock = ShiftedClock()
    first = {plan.for_vu(vu)[0].request_key for vu in plan.vus}
    # Con los 10 primeros requests en vuelo, el reloj salta 61 s: terminan fuera de la
    # ventana y todos los siguientes empiezan después de `window_end`.
    jump = threading.Barrier(10, action=lambda: clock.jump(61_000_000_000), timeout=5)

    def hook(request):
        if request.item.request_key in first:
            jump.wait()

    transport = FakeTransport(hook=hook)
    run = run_generator(plan, prepared, transport, clock=clock)
    assert len(transport.calls) == 50
    for attempt in run.attempts:
        if attempt.request_key in first:
            assert (attempt.started_in_window, attempt.completed_in_window) == (True, False)
        else:
            assert attempt.started_in_window is False
            assert attempt.t_start_ns >= run.window_end_ns
    assert ex10.apparatus_invalidity(plan, run) == []
    evaluation = evaluate_complete(plan, run)
    assert evaluation.throughput.n_ok_window == 0
    assert evaluation.percentiles.n == 50
    assert evaluation.decision is constants.Decision.FAIL


def test_timeout_is_a_single_valid_failure_and_the_vu_continues(plan, prepared):
    responses = {"load-request-02": ("error", "timeout", "TimeoutError")}
    transport = FakeTransport(responses)
    run = run_generator(plan, prepared, transport)
    by_key = {a.request_key: a for a in run.attempts}
    assert by_key["load-request-02"].outcome is Outcome.TRANSPORT_FAILED
    assert by_key["load-request-02"].error_class == "timeout"
    assert by_key["load-request-03"].outcome is Outcome.HTTP_ACCEPTED
    assert [key for key, _ in transport.calls].count("load-request-02") == 1
    assert ex10.apparatus_invalidity(plan, run) == []


@pytest.mark.parametrize("status", [400, 401, 403, 409])
def test_4xx_is_a_valid_request_failure(plan, prepared, status):
    run = run_generator(
        plan, prepared, FakeTransport({"load-request-04": ("status", status, b"{}")})
    )
    attempt = run.attempts[3]
    assert attempt.outcome is Outcome.HTTP_REJECTED
    assert (attempt.http_status, attempt.error_class) == (status, "http_4xx")
    assert ex10.apparatus_invalidity(plan, run) == []
    assert evaluate_complete(plan, run).decision is constants.Decision.FAIL


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_is_a_valid_request_failure(plan, prepared, status):
    run = run_generator(
        plan, prepared, FakeTransport({"load-request-30": ("status", status, b"<html>")})
    )
    attempt = run.attempts[29]
    assert (attempt.outcome, attempt.error_class) == (Outcome.HTTP_REJECTED, "http_5xx")
    assert attempt.minimal_response == {
        "body_sha256": ex10.sha256_bytes(b"<html>"),
        "body_bytes": 6,
        "json_keys": None,
    }
    assert ex10.apparatus_invalidity(plan, run) == []
    assert evaluate_complete(plan, run).decision is constants.Decision.FAIL


def test_429_throttle_is_a_valid_request_failure(plan, prepared):
    throttled = {
        plan.for_vu(vu)[4].request_key: ("status", 429, b'{"detail":"Request was throttled."}')
        for vu in plan.vus
    }
    run = run_generator(plan, prepared, FakeTransport(throttled))
    rejected = [a for a in run.attempts if a.http_status == 429]
    assert len(rejected) == 10
    assert all(a.error_class == "http_4xx" for a in rejected)
    assert rejected[0].minimal_response["json_keys"] == ["detail"]
    assert "throttled" not in json.dumps(ex10.raw_results_payload(plan, run))
    evaluation = evaluate_complete(plan, run)
    assert evaluation.throughput.n_ok_window == 40
    assert evaluation.decision is constants.Decision.FAIL


@pytest.mark.parametrize(
    "error_class",
    [
        "connection_refused",
        "connection_reset",
        "broken_pipe",
        "protocol_error",
        "incomplete_read",
    ],
)
def test_captured_transport_errors_are_valid_failures(plan, prepared, error_class):
    run = run_generator(
        plan, prepared, FakeTransport({"load-request-09": ("error", error_class, "X")})
    )
    attempt = run.attempts[8]
    assert (attempt.outcome, attempt.error_class) == (Outcome.TRANSPORT_FAILED, error_class)
    assert ex10.apparatus_invalidity(plan, run) == []
    assert evaluate_complete(plan, run).decision is constants.Decision.FAIL


def test_201_with_a_non_contractual_body_is_not_accepted(plan, prepared):
    body = b'{"id": "no-uuid", "status": "CREADO"}'
    run = run_generator(plan, prepared, FakeTransport({"load-request-05": ("status", 201, body)}))
    attempt = run.attempts[4]
    assert attempt.outcome is Outcome.CONTRACT_VIOLATION
    assert attempt.report_id is None
    assert ex10.latency_percentiles(run.attempts).n == 50
    assert evaluate_complete(plan, run).throughput.n_ok_window == 49


def test_thread_death_leaves_the_run_not_demonstrated(plan, prepared):
    def hook(request):
        if request.item.request_key == "load-request-13":
            raise ThreadKill

    transport = FakeTransport(hook=hook)
    run = run_generator(plan, prepared, transport)
    assert ex10.ApparatusFault(Fault.THREAD_DIED, vu=3, detail="ThreadKill") in run.faults
    by_key = {a.request_key: a for a in run.attempts}
    assert by_key["load-request-13"].outcome is Outcome.APPARATUS_FAILURE
    assert by_key["load-request-13"].error_class == "result_lost"
    assert by_key["load-request-14"].outcome is Outcome.NOT_SENT
    assert by_key["load-request-15"].outcome is Outcome.NOT_SENT
    assert len(transport.calls) == 48
    reasons = ex10.apparatus_invalidity(plan, run)
    assert "apparatus:thread_died:vu-03" in reasons
    assert "apparatus:result_lost:load-request-13" in reasons
    assert "attempt-outcome:load-request-14:NOT_SENT" in reasons
    assert evaluate_complete(plan, run).decision is constants.Decision.NOT_DEMONSTRATED


def test_broken_barrier_sends_nothing_and_is_not_demonstrated(plan, prepared):
    def thread_factory(*, target, args, name, daemon):
        if args[0] == 10:
            # El VU 10 nunca llega a la barrera y termina sin registro de salida.
            return threading.Thread(target=lambda: None, name=name, daemon=daemon)
        return threading.Thread(target=target, args=args, name=name, daemon=daemon)

    transport = FakeTransport()
    run = run_generator(
        plan, prepared, transport, barrier_timeout_s=0.3, thread_factory=thread_factory
    )
    assert transport.calls == []
    assert run.t0_ns is None
    assert sum(f.kind is Fault.BARRIER_BROKEN for f in run.faults) == 9
    assert ex10.ApparatusFault(Fault.THREAD_DIED, vu=10, detail="no_exit_record") in run.faults
    assert all(a.outcome is Outcome.NOT_SENT for a in run.attempts)
    reasons = ex10.apparatus_invalidity(plan, run)
    assert "t0-missing" in reasons
    assert "apparatus:barrier_broken:vu-01" in reasons
    assert ex10.evaluate_run(plan, run).decision is constants.Decision.NOT_DEMONSTRATED


def test_internal_exception_is_an_apparatus_failure_not_a_request_failure(plan, prepared):
    def hook(request):
        if request.item.request_key == "load-request-22":
            raise RuntimeError("fallo interno del generador")

    transport = FakeTransport(hook=hook)
    run = run_generator(plan, prepared, transport)
    by_key = {a.request_key: a for a in run.attempts}
    assert by_key["load-request-22"].outcome is Outcome.APPARATUS_FAILURE
    assert by_key["load-request-23"].outcome is Outcome.HTTP_ACCEPTED
    assert (
        ex10.ApparatusFault(
            Fault.INTERNAL_EXCEPTION, vu=5, request_key="load-request-22", detail="RuntimeError"
        )
        in run.faults
    )
    assert evaluate_complete(plan, run).decision is constants.Decision.NOT_DEMONSTRATED


def test_unattributable_error_invalidates_instead_of_failing(plan, prepared):
    # Decisión A: lo que no es inequívocamente HTTP/red (aquí, un recurso local) es un
    # fallo del aparato y nunca un FAIL.
    responses = {"load-request-31": ("error", "unattributable_error", "OSError:EMFILE")}
    run = run_generator(plan, prepared, FakeTransport(responses))
    assert run.attempts[30].outcome is Outcome.APPARATUS_FAILURE
    assert (
        ex10.ApparatusFault(
            Fault.UNATTRIBUTABLE_ERROR, vu=7, request_key="load-request-31", detail="OSError:EMFILE"
        )
        in run.faults
    )
    assert evaluate_complete(plan, run).decision is constants.Decision.NOT_DEMONSTRATED


class BackwardsTransport(FakeTransport):
    def send(self, request, *, clock, wall_clock):
        exchange = super().send(request, clock=clock, wall_clock=wall_clock)
        if request.item.request_key == "load-request-40":
            return replace(exchange, t_end_ns=exchange.t_start_ns - 1)
        return exchange


def test_impossible_timing_is_an_apparatus_failure(plan, prepared):
    run = run_generator(plan, prepared, BackwardsTransport())
    assert run.attempts[39].error_class == "timing_inconsistent"
    assert "apparatus:timing_inconsistent:load-request-40" in ex10.apparatus_invalidity(plan, run)
    assert evaluate_complete(plan, run).decision is constants.Decision.NOT_DEMONSTRATED


# --- Interrupciones asíncronas: siempre en un proceso hijo --------------------------------
#
# Una interrupción real entregada al proceso de pytest bajo `--cov` puede caer dentro del lock
# de datos de coverage.py y bloquearlo (COVERAGE_TRACER_INTERRUPT_DEADLOCK). Estos escenarios
# corren en un hijo sin coverage (`ex10_signal_child`): la misma interrupción real, los mismos
# hilos, barrera y generador, y las mismas aserciones. El hijo devuelve hechos saneados y el
# test los vuelve a comprobar aquí.


def child_inputs():
    """Los fixtures `plan` y `prepared`, construidos igual dentro del hijo."""
    dataset = ex10.load_dataset()
    plan = ex10.build_plan(dataset)
    return plan, make_prepared(dataset, plan)


def interruption_facts(plan, run, transport, threads=()):
    """Sólo hechos técnicos: ni tokens, ni cuerpos, ni tracebacks."""
    calls = Counter(key for key, _ in transport.calls)
    return {
        "t0_present": run.t0_ns is not None,
        "interrupted": run.interrupted,
        "faults": [[str(f.kind), f.vu, f.request_key, f.detail] for f in run.faults],
        "barrier_broken": sum(f.kind is Fault.BARRIER_BROKEN for f in run.faults),
        "attempts": len(run.attempts),
        "outcomes": [str(a.outcome) for a in run.attempts],
        "send_counts": [a.send_count for a in run.attempts],
        "duplicate_sends": sum(1 for n in calls.values() if n > 1),
        "decision": str(ex10.evaluate_run(plan, run).decision),
        "operator_interrupted_invalidity": "operator-interrupted"
        in ex10.apparatus_invalidity(plan, run),
        "vu_threads": len(threads),
        "vu_threads_alive": sum(t.is_alive() for t in threads),
        "python_threads_after": threading.active_count(),
    }


def scenario_interruption_after_t0():
    plan, prepared = child_inputs()
    in_flight = threading.Event()

    def hook(request):
        if request.item.request_key == "load-request-01":
            _thread.interrupt_main()
            # Sigue en vuelo mientras el hilo principal procesa la interrupción.
            in_flight.wait(1)

    # `interrupt_main` no hace nada si el proceso heredó SIGINT ignorado (p. ej. `&`).
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    started = time.monotonic()
    transport = FakeTransport(hook=hook)
    try:
        run = run_generator(plan, prepared, transport)
    finally:
        signal.signal(signal.SIGINT, previous)
    elapsed = time.monotonic() - started
    # El generador esperó al request en vuelo en lugar de darlo por perdido.
    assert elapsed >= 1
    assert run.interrupted is True
    assert run.faults == (ex10.ApparatusFault(Fault.OPERATOR_INTERRUPTED, detail="after_t0"),)
    assert run.attempts[0].outcome is Outcome.HTTP_ACCEPTED
    assert all(a.outcome is Outcome.NOT_SENT for a in run.attempts[1:5])
    assert all(a.send_count == 0 for a in run.attempts[1:5])
    assert "operator-interrupted" in ex10.apparatus_invalidity(plan, run)
    assert evaluate_complete(plan, run).decision is constants.Decision.NOT_DEMONSTRATED
    return {**interruption_facts(plan, run, transport), "waited_for_in_flight": elapsed >= 1}


def scenario_second_interruption():
    plan, prepared = child_inputs()
    in_flight = threading.Event()

    def hook(request):
        if request.item.request_key == "load-request-01":
            _thread.interrupt_main()
            in_flight.wait(0.5)
            # Llega mientras el generador espera al request en vuelo.
            _thread.interrupt_main()
            in_flight.wait(0.5)

    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    transport = FakeTransport(hook=hook)
    try:
        run = run_generator(plan, prepared, transport)
    finally:
        signal.signal(signal.SIGINT, previous)
    assert run.interrupted is True
    assert run.faults == (ex10.ApparatusFault(Fault.OPERATOR_INTERRUPTED, detail="after_t0"),)
    assert run.attempts[0].outcome is Outcome.HTTP_ACCEPTED
    return interruption_facts(plan, run, transport)


AFTER_T0_ONLY = [["operator_interrupted", None, None, "after_t0"]]


def assert_only_the_post_t0_interruption(facts):
    """Lo que `assert_interrupted_after_t0_only` exige, sobre los hechos que devuelve el hijo."""
    assert facts["t0_present"] is True and facts["interrupted"] is True
    assert facts["faults"] == AFTER_T0_ONLY and facts["barrier_broken"] == 0
    assert set(facts["outcomes"]) <= {"HTTP_ACCEPTED", "NOT_SENT"}
    assert facts["send_counts"] == [int(o == "HTTP_ACCEPTED") for o in facts["outcomes"]]
    assert facts["duplicate_sends"] == 0
    assert facts["operator_interrupted_invalidity"] is True
    assert facts["decision"] == "NOT_DEMONSTRATED"
    assert (facts["attempts"], facts["vu_threads"], facts["vu_threads_alive"]) == (50, 10, 0)
    assert facts["python_threads_after"] == 1


def test_operator_interruption_after_t0_is_not_demonstrated():
    facts = run_signal_scenario("interruption-after-t0")
    assert facts["waited_for_in_flight"] is True
    assert (facts["interrupted"], facts["faults"]) == (True, AFTER_T0_ONLY)
    assert facts["outcomes"][0] == "HTTP_ACCEPTED"
    assert facts["outcomes"][1:5] == ["NOT_SENT"] * 4 and facts["send_counts"][1:5] == [0] * 4
    assert facts["operator_interrupted_invalidity"] is True
    assert facts["decision"] == "NOT_DEMONSTRATED" and facts["duplicate_sends"] == 0


def test_a_second_interruption_does_not_discard_the_run():
    facts = run_signal_scenario("second-interruption")
    assert (facts["interrupted"], facts["faults"]) == (True, AFTER_T0_ONLY)
    assert facts["outcomes"][0] == "HTTP_ACCEPTED" and facts["attempts"] == 50


def recorded_threads():
    threads = []

    def thread_factory(*, target, args, name, daemon):
        thread = threading.Thread(target=target, args=args, name=name, daemon=daemon)
        threads.append(thread)
        return thread

    return threads, thread_factory


def assert_interrupted_after_t0_only(plan, run, transport, threads):
    """Tras t0, la interrupción es la única causa: ningún VU liberado queda como barrera
    rota, nada se envía dos veces y ningún VU sigue vivo ni atrapado en la barrera."""
    assert run.t0_ns is not None and run.interrupted is True
    assert run.faults == (ex10.ApparatusFault(Fault.OPERATOR_INTERRUPTED, detail="after_t0"),)
    assert all(a.outcome in {Outcome.HTTP_ACCEPTED, Outcome.NOT_SENT} for a in run.attempts)
    assert Counter(key for key, _ in transport.calls) == {
        a.request_key: 1 for a in run.attempts if a.outcome is Outcome.HTTP_ACCEPTED
    }
    assert all(a.send_count == (a.outcome is Outcome.HTTP_ACCEPTED) for a in run.attempts)
    assert "operator-interrupted" in ex10.apparatus_invalidity(plan, run)
    assert ex10.evaluate_run(plan, run).decision is constants.Decision.NOT_DEMONSTRATED
    for thread in threads:
        thread.join(5)
    assert len(threads) == 10 and not any(thread.is_alive() for thread in threads)


def scenario_interruption_at_t0(switch_interval):
    # La interrupción más cercana a t0: la primera lectura del reloj, dentro de la acción de
    # la barrera, la dispara y espera a que el hilo principal la atienda. Su `abort` llega
    # con la barrera ya liberada y con VU que aún no despertaron; con un intervalo de cambio
    # largo, los nueve que esperaban. Ninguno es un fallo de la barrera.
    plan, prepared = child_inputs()
    handled = threading.Event()
    reads = itertools.count()

    def handler(signum, frame):
        handled.set()
        raise KeyboardInterrupt

    def clock():
        if next(reads) == 0:
            _thread.interrupt_main()
            handled.wait(5)
        return time.monotonic_ns()

    threads, thread_factory = recorded_threads()
    transport = FakeTransport()
    previous, interval = signal.signal(signal.SIGINT, handler), sys.getswitchinterval()
    if switch_interval is not None:
        sys.setswitchinterval(switch_interval)
    started = time.monotonic()
    try:
        run = run_generator(plan, prepared, transport, clock=clock, thread_factory=thread_factory)
    finally:
        sys.setswitchinterval(interval)
        signal.signal(signal.SIGINT, previous)
    assert time.monotonic() - started < 5  # sin esperas de barrera ni de join
    assert handled.is_set()
    assert_interrupted_after_t0_only(plan, run, transport, threads)
    restored = (signal.getsignal(signal.SIGINT), sys.getswitchinterval()) == (previous, interval)
    assert restored
    return {
        **interruption_facts(plan, run, transport, threads),
        "handled": True,
        "restored": restored,
    }


@pytest.mark.parametrize("switch_interval", [1.0, None, 1e-6], ids=["long", "default", "short"])
def test_an_interruption_at_t0_never_breaks_a_released_vu(switch_interval):
    facts = run_signal_scenario("interruption-at-t0", switch_interval=switch_interval)
    assert facts["handled"] is True and facts["restored"] is True
    assert_only_the_post_t0_interruption(facts)


def scenario_interruption_after_all_left_the_barrier():
    # Los 10 primeros requests en vuelo a la vez: todos los VU salieron ya de la barrera.
    plan, prepared = child_inputs()
    first = {plan.for_vu(vu)[0].request_key for vu in plan.vus}
    together = threading.Barrier(10, action=_thread.interrupt_main, timeout=5)
    handled = threading.Event()

    def handler(signum, frame):
        handled.set()
        raise KeyboardInterrupt

    def hook(request):
        if request.item.request_key in first:
            together.wait()
            handled.wait(5)

    threads, thread_factory = recorded_threads()
    transport = FakeTransport(hook=hook)
    previous = signal.signal(signal.SIGINT, handler)
    try:
        run = run_generator(plan, prepared, transport, thread_factory=thread_factory)
    finally:
        signal.signal(signal.SIGINT, previous)
    assert handled.is_set()
    assert_interrupted_after_t0_only(plan, run, transport, threads)
    by_key = {a.request_key: a for a in run.attempts}
    assert all(by_key[key].outcome is Outcome.HTTP_ACCEPTED for key in first)
    first_accepted = sorted(i for i, a in enumerate(run.attempts) if a.request_key in first)
    return {**interruption_facts(plan, run, transport, threads), "first_indices": first_accepted}


def test_an_interruption_once_every_vu_left_the_barrier_is_only_the_interruption():
    facts = run_signal_scenario("interruption-after-all-left-the-barrier")
    assert_only_the_post_t0_interruption(facts)
    assert len(facts["first_indices"]) == 10
    assert all(facts["outcomes"][i] == "HTTP_ACCEPTED" for i in facts["first_indices"])


def test_the_signal_child_never_inherits_coverage(monkeypatch):
    # Con estas variables pytest-cov (`pytest-cov.pth`) trazaría al hijo: se eliminan.
    for name in (
        "COV_CORE_SOURCE",
        "COV_CORE_CONFIG",
        "COV_CORE_DATAFILE",
        "COVERAGE_PROCESS_START",
    ):
        monkeypatch.setenv(name, "/nonexistent")
    env = signal_child.child_environment()
    assert not {k for k in env if k.startswith("COV_CORE_") or k == "COVERAGE_PROCESS_START"}
    facts = run_signal_scenario("selftest-ok")
    assert facts == {"python_threads": 1, "coverage_loaded": False, "trace_active": False}


@pytest.mark.parametrize(
    ("scenario", "params", "reason"),
    [
        ("selftest-exit", {"code": 7}, "exit:7"),
        ("selftest-signal", {}, "signal:SIGKILL"),
        ("selftest-garbage", {}, "stdout-invalid"),
        ("unknown-scenario", {}, "exit:3"),
    ],
    ids=["nonzero-exit", "signal-death", "invalid-stdout", "unknown-scenario"],
)
def test_a_child_without_a_valid_result_fails(scenario, params, reason):
    with pytest.raises(signal_child.ChildFailure) as failure:
        run_signal_scenario(scenario, **params)
    assert failure.value.reason == reason


def _process_state(status):
    """La letra de estado de /proc/<pid>/status (R, S, D, Z...), o "unparseable"."""
    line = next((line for line in status.splitlines() if line.startswith("State:")), "")
    letter = line.removeprefix("State:").strip()[:1]
    return letter if letter.isalpha() and letter.isupper() else "unparseable"


def _assert_process_gone_or_zombie(pid, proc=Path("/proc"), timeout=5.0, poll=0.010):
    """El proceso queda recogido (/proc/<pid> desaparece) o zombi dentro de `timeout`.

    Tras killpg(SIGKILL) el kernel entrega la señal y termina al proceso de forma asíncrona
    respecto de quien recoge al hijo directo: durante unos milisegundos el nieto puede seguir
    en R/S/D. Por eso se sondea con reloj monotónico hasta el plazo. Sin exists() previo: la
    lectura de un proceso que desaparece da FileNotFoundError o ProcessLookupError (ESRCH), y
    eso es recogido; tras desaparecer no se vuelve a mirar (el PID podría reutilizarse).
    Cualquier otro error de lectura (PermissionError, E/S, decodificación) se propaga.
    """
    status_path = proc / str(pid) / "status"
    deadline = time.monotonic() + timeout
    while True:
        try:
            status = status_path.read_text()
        except (FileNotFoundError, ProcessLookupError):
            return
        state = _process_state(status)
        if state == "Z":
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"descendant-did-not-terminate pid={pid} state={state}")
        time.sleep(poll)


class _FakeClock:
    """time.monotonic/time.sleep deterministas; time.time no debe usarse."""

    def __init__(self, monkeypatch):
        self.now, self.sleeps = 1000.0, []
        monkeypatch.setattr(time, "monotonic", lambda: self.now)
        monkeypatch.setattr(time, "sleep", self.sleep)
        monkeypatch.setattr(time, "time", self.wall_clock)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def wall_clock(self):
        raise AssertionError("el helper no debe usar el reloj de pared")


@pytest.fixture
def proc_reads(tmp_path, monkeypatch):
    """Cada lectura de /proc/4242/status devuelve el siguiente estado o lanza la excepción."""
    clock = _FakeClock(monkeypatch)
    reads = []

    def script(*steps):
        pending = list(steps)

        def read_text(path, *args, **kwargs):
            assert path == tmp_path / "4242" / "status"
            reads.append(path)
            step = pending.pop(0) if len(pending) > 1 else pending[0]
            if isinstance(step, BaseException) or (
                isinstance(step, type) and issubclass(step, BaseException)
            ):
                raise step
            return f"Name:\tpython\nState:\t{step}\nPid:\t4242\n"

        monkeypatch.setattr(Path, "read_text", read_text)
        return tmp_path

    script.reads, script.clock = reads, clock
    return script


def test_process_check_passes_when_proc_is_absent_from_the_start(tmp_path):
    _assert_process_gone_or_zombie(4242, proc=tmp_path)


def test_process_check_passes_for_an_immediate_zombie(proc_reads):
    _assert_process_gone_or_zombie(4242, proc=proc_reads("Z (zombie)"))
    assert len(proc_reads.reads) == 1 and proc_reads.clock.sleeps == []


@pytest.mark.parametrize(
    "steps",
    [
        ("R (running)", FileNotFoundError(errno.ENOENT, "gone")),
        ("R (running)", "R (running)", ProcessLookupError(errno.ESRCH, "gone")),
        ("R (running)", "Z (zombie)"),
        ("S (sleeping)", "Z (zombie)"),
        ("D (disk sleep)", "S (sleeping)", "Z (zombie)"),
    ],
    ids=["R-then-gone", "R-then-esrch", "R-then-Z", "S-then-Z", "D-S-then-Z"],
)
def test_process_check_waits_for_a_dying_process(proc_reads, steps):
    _assert_process_gone_or_zombie(4242, proc=proc_reads(*steps))
    # Se detiene en cuanto desaparece o es zombi: nunca vuelve a leer (PID reutilizable).
    assert len(proc_reads.reads) == len(steps)
    assert proc_reads.clock.sleeps == [0.010] * (len(steps) - 1)


@pytest.mark.parametrize("error", [FileNotFoundError, ProcessLookupError])
def test_process_check_passes_when_the_process_vanishes_while_reading(proc_reads, error):
    # PROCFS_REAP_RACE: el status existía y el kernel recoge el proceso antes de leerlo.
    _assert_process_gone_or_zombie(4242, proc=proc_reads(error(errno.ESRCH, "gone")))


@pytest.mark.parametrize("state", ["R (running)", "S (sleeping)", "D (disk sleep)", "I (idle)"])
def test_process_check_fails_if_alive_for_the_whole_timeout(proc_reads, state):
    proc = proc_reads(state)
    start = proc_reads.clock.now
    expected = f"descendant-did-not-terminate pid=4242 state={state[0]}"
    with pytest.raises(AssertionError, match=expected):
        _assert_process_gone_or_zombie(4242, proc=proc)
    waited = proc_reads.clock.now - start
    # Espera el plazo completo y no lo excede en más de un sondeo.
    assert 5.0 <= waited <= 5.0 + 0.010 + 1e-9
    assert set(proc_reads.clock.sleeps) == {0.010}


def test_process_check_reports_an_unparseable_state_as_a_failure(proc_reads):
    with pytest.raises(AssertionError, match="state=unparseable"):
        _assert_process_gone_or_zombie(4242, proc=proc_reads("?? (odd)"), timeout=0.05)


@pytest.mark.parametrize(
    "error",
    [
        PermissionError(errno.EACCES, "denied"),
        OSError(errno.EIO, "I/O error"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
    ],
    ids=["permission", "eio", "decode"],
)
def test_process_check_propagates_any_other_read_error(proc_reads, error):
    with pytest.raises(type(error)):
        _assert_process_gone_or_zombie(4242, proc=proc_reads("R (running)", error))


def test_process_check_on_a_real_process_fails_while_alive_and_passes_once_reaped():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        start = time.monotonic()
        with pytest.raises(AssertionError, match="descendant-did-not-terminate"):
            _assert_process_gone_or_zombie(child.pid, timeout=0.2)
        assert time.monotonic() - start >= 0.2
    finally:
        child.kill()
        child.wait()
    _assert_process_gone_or_zombie(child.pid)


def test_a_hung_child_times_out_as_a_failure_and_leaves_no_process():
    with pytest.raises(signal_child.ChildFailure) as failure:
        run_signal_scenario("selftest-hang", timeout=5)
    assert failure.value.reason == "timeout"
    # El nieto estaba en el grupo del hijo: murió con él (a lo sumo queda por recoger).
    match = re.search(r'"grandchild": (\d+)', str(failure.value))
    assert match, "el hijo no llegó a informar su nieto"
    _assert_process_gone_or_zombie(int(match.group(1)))


def test_child_stderr_reaches_the_test_log_sanitized():
    with pytest.raises(signal_child.ChildFailure) as failure:
        run_signal_scenario("selftest-leak")
    message = str(failure.value)
    assert failure.value.reason == "exit:1"
    assert "[REDACTED]" in message and "eyJhbGciOiJIUzI1NiJ9.eyJsZWFrIjoidGVzdCJ9" not in message


SIGNAL_SCENARIOS = {
    "interruption-after-t0": scenario_interruption_after_t0,
    "second-interruption": scenario_second_interruption,
    "interruption-at-t0": scenario_interruption_at_t0,
    "interruption-after-all-left-the-barrier": scenario_interruption_after_all_left_the_barrier,
}


def test_a_failing_barrier_action_leaves_no_t0_and_breaks_the_barrier(plan, prepared):
    # La acción falla tras leer el reloj monotónico, antes de completar la segunda lectura:
    # t0 queda ausente y la barrera rota es real, así que los nueve que esperaban sí la
    # registran.
    def wall_clock():
        raise RuntimeError("reloj de pared no disponible")

    transport = FakeTransport()
    run = run_generator(plan, prepared, transport, wall_clock=wall_clock)
    assert transport.calls == []
    assert run.t0_ns is None and run.t0_utc_ns is None and run.interrupted is False
    assert sum(f.kind is Fault.BARRIER_BROKEN for f in run.faults) == 9
    assert [f.detail for f in run.faults if f.kind is Fault.THREAD_DIED] == ["RuntimeError"]
    assert all(a.outcome is Outcome.NOT_SENT for a in run.attempts)
    assert "t0-missing" in ex10.apparatus_invalidity(plan, run)
    assert ex10.evaluate_run(plan, run).decision is constants.Decision.NOT_DEMONSTRATED


class InterruptedStart(threading.Thread):
    def start(self):
        raise KeyboardInterrupt


def test_interruption_while_starting_threads_abandons_before_t0(plan, prepared):
    def thread_factory(*, target, args, name, daemon):
        kind = InterruptedStart if args[0] == 3 else threading.Thread
        return kind(target=target, args=args, name=name, daemon=daemon)

    transport = FakeTransport()
    started = time.monotonic()
    run = run_generator(plan, prepared, transport, thread_factory=thread_factory)
    # No espera el timeout de join por hilos que nunca arrancaron.
    assert time.monotonic() - started < 5
    assert transport.calls == []
    assert run.t0_ns is None and run.interrupted is True
    assert {(f.kind, f.vu, f.detail) for f in run.faults} == {
        (Fault.OPERATOR_INTERRUPTED, None, "before_t0"),
        (Fault.BARRIER_BROKEN, 1, None),
        (Fault.BARRIER_BROKEN, 2, None),
    }
    assert ex10.evaluate_run(plan, run).decision is constants.Decision.NOT_DEMONSTRATED


def test_hung_vu_is_an_apparatus_failure_and_its_result_is_lost(plan, prepared):
    unblock = threading.Event()

    def hook(request):
        if request.item.request_key == "load-request-02":
            unblock.wait(5)

    try:
        run = run_generator(plan, prepared, FakeTransport(hook=hook), join_timeout_s=0.3)
    finally:
        unblock.set()
    assert ex10.ApparatusFault(Fault.THREAD_HUNG, vu=1) in run.faults
    assert run.attempts[1].error_class == "result_lost"
    assert run.attempts[2].outcome is Outcome.NOT_SENT
    reasons = ex10.apparatus_invalidity(plan, run)
    assert "apparatus:thread_hung:vu-01" in reasons
    assert evaluate_complete(plan, run).decision is constants.Decision.NOT_DEMONSTRATED


# --- Transporte de biblioteca estándar -------------------------------------------------


class FakeSocket:
    def __init__(self, chunks=(), *, recv_error=None, send_error=None):
        self.chunks = [bytes(chunk) for chunk in chunks]
        self.recv_error = recv_error
        self.send_error = send_error
        self.timeouts = []
        self.sent = []
        self.closed = False

    def settimeout(self, value):
        self.timeouts.append(value)

    def sendall(self, data):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(bytes(data))

    def recv_into(self, buffer):
        if not self.chunks:
            if self.recv_error is not None:
                raise self.recv_error
            return 0
        chunk = self.chunks.pop(0)
        size = min(len(buffer), len(chunk))
        buffer[:size] = chunk[:size]
        if size < len(chunk):
            self.chunks.insert(0, chunk[size:])
        return size

    def close(self):
        self.closed = True


class Connector:
    def __init__(self, sock=None, error=None):
        self.sock = sock
        self.error = error
        self.calls = []

    def __call__(self, address, timeout):
        self.calls.append((address, timeout))
        if self.error is not None:
            raise self.error
        return self.sock


class StepClock:
    def __init__(self, step):
        self.step = step
        self.value = -step

    def __call__(self):
        self.value += self.step
        return self.value


def http_response(status, reason, body, *, length=None):
    head = (
        f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body) if length is None else length}\r\nConnection: close\r\n\r\n"
    )
    return head.encode("ascii") + body


def send_with(prepared, sock=None, *, error=None, step=1_000_000_000):
    connector = Connector(sock, error)
    transport = ex10.StdlibHttpTransport(("192.0.2.10", 8000), connect=connector)
    exchange = transport.send(
        prepared["load-request-01"], clock=StepClock(step), wall_clock=lambda: T0_UTC
    )
    return exchange, connector


def test_stdlib_transport_sends_once_on_a_new_connection_and_reads_the_201(prepared):
    body = created_body("load-request-01")
    response = http_response(201, "Created", body)
    sock = FakeSocket([response[:20], response[20:60], response[60:]])
    exchange, connector = send_with(prepared, sock)
    assert connector.calls == [(("192.0.2.10", 8000), 59.0)]
    assert sock.sent == [prepared["load-request-01"].wire]
    assert sock.closed is True
    assert (exchange.http_status, exchange.body, exchange.error_class) == (201, body, None)
    assert exchange.t_end_ns > exchange.t_start_ns
    # Cada operación recibe sólo lo que queda del plazo total de 60 s.
    assert sock.timeouts == sorted(sock.timeouts, reverse=True)
    assert len(sock.timeouts) >= 2 and all(0 < t < 60 for t in sock.timeouts)
    record = ex10.attempt_record(prepared["load-request-01"].item, exchange, t0_ns=0)
    assert record.outcome is Outcome.HTTP_ACCEPTED


def test_stdlib_transport_deadline_is_total_not_per_operation(prepared):
    response = http_response(201, "Created", created_body("load-request-01"))
    chunks = [response[i : i + 8] for i in range(0, len(response), 8)]
    sock = FakeSocket(chunks)
    exchange, connector = send_with(prepared, sock, step=10_000_000_000)
    assert connector.calls[0][1] == 50.0
    assert sock.timeouts == [40.0, 30.0, 20.0, 10.0]
    assert (exchange.error_class, exchange.error_detail) == ("timeout", "TimeoutError")
    assert exchange.http_status is None
    assert sock.closed is True


def test_stdlib_transport_stops_before_an_operation_once_the_deadline_passes(prepared):
    sock = FakeSocket([http_response(201, "Created", b"{}")])
    exchange, _ = send_with(prepared, sock, step=30_000_000_000)
    assert exchange.error_class == "timeout"
    assert sock.sent == []
    assert sock.closed is True


def test_stdlib_transport_timeout_is_reported_once_without_retry(prepared):
    sock = FakeSocket(recv_error=TimeoutError("timed out"))
    exchange, connector = send_with(prepared, sock)
    assert exchange.error_class == "timeout"
    assert len(connector.calls) == 1 and len(sock.sent) == 1


@pytest.mark.parametrize(
    ("sock", "error", "error_class", "detail"),
    [
        (
            None,
            ConnectionRefusedError(errno.ECONNREFUSED, "refused by 192.0.2.10"),
            "connection_refused",
            "ConnectionRefusedError:ECONNREFUSED",
        ),
        (
            FakeSocket(recv_error=ConnectionResetError(errno.ECONNRESET, "reset")),
            None,
            "connection_reset",
            "ConnectionResetError:ECONNRESET",
        ),
        (FakeSocket(), None, "connection_reset", "RemoteDisconnected"),
        (
            FakeSocket(send_error=BrokenPipeError(errno.EPIPE, "pipe")),
            None,
            "broken_pipe",
            "BrokenPipeError:EPIPE",
        ),
        (FakeSocket([b"garbage\r\n\r\n"]), None, "protocol_error", "BadStatusLine"),
        (
            FakeSocket([http_response(201, "Created", b"{}", length=100)]),
            None,
            "incomplete_read",
            "IncompleteRead",
        ),
        (
            FakeSocket(recv_error=OSError(errno.EHOSTUNREACH, "no route")),
            None,
            "network_unreachable",
            "OSError:EHOSTUNREACH",
        ),
        (
            FakeSocket(recv_error=OSError(errno.EMFILE, "too many open files")),
            None,
            "unattributable_error",
            "OSError:EMFILE",
        ),
    ],
)
def test_stdlib_transport_classifies_network_errors(prepared, sock, error, error_class, detail):
    exchange, _ = send_with(prepared, sock, error=error)
    assert (exchange.error_class, exchange.error_detail) == (error_class, detail)
    assert "192.0.2" not in exchange.error_detail
    if sock is not None:
        assert sock.closed is True


def test_stdlib_transport_connect_timeout_is_a_timeout(prepared):
    exchange, connector = send_with(prepared, error=TimeoutError("timed out"))
    assert (exchange.error_class, exchange.http_status) == ("timeout", None)
    assert len(connector.calls) == 1


def test_stdlib_transport_reads_chunked_and_close_delimited_bodies(prepared):
    body = created_body("load-request-01")
    chunked = (
        b"HTTP/1.1 201 Created\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        + f"{len(body[:10]):x}\r\n".encode()
        + body[:10]
        + b"\r\n"
        + f"{len(body[10:]):x}\r\n".encode()
        + body[10:]
        + b"\r\n0\r\n\r\n"
    )
    delimited = b"HTTP/1.1 201 Created\r\nConnection: close\r\n\r\n" + body
    for response in (chunked, delimited):
        exchange, _ = send_with(prepared, FakeSocket([response]))
        assert (exchange.http_status, exchange.body, exchange.error_class) == (201, body, None)


def test_stdlib_transport_keeps_the_status_of_a_truncated_201(prepared):
    sock = FakeSocket([http_response(201, "Created", b'{"id"', length=100)])
    exchange, _ = send_with(prepared, sock)
    assert (exchange.http_status, exchange.error_class) == (201, "incomplete_read")
    record = ex10.attempt_record(prepared["load-request-01"].item, exchange, t0_ns=0)
    assert (record.outcome, record.http_status) == (Outcome.TRANSPORT_FAILED, 201)
    # No terminó de leerse: no hay `t_end` tras el último byte y no entra en P50/P99.
    assert ex10.latency_set([record]) == ()


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        (("backend", 8000), "transport-address-not-ip"),
        (("192.0.2.10", 0), "transport-port"),
        (("192.0.2.10", "8000"), "transport-port"),
    ],
)
def test_stdlib_transport_requires_a_resolved_ip_before_the_barrier(address, reason):
    with pytest.raises(ex10.Ex10PreparationError) as rejected:
        ex10.StdlibHttpTransport(address)
    assert rejected.value.reasons == (reason,)


def test_stdlib_transport_lets_non_network_errors_escape(prepared):
    with pytest.raises(ValueError):
        send_with(prepared, FakeSocket(recv_error=ValueError("bug")))


def test_default_stdlib_connection_is_blocked_in_tests(prepared):
    transport = ex10.StdlibHttpTransport(("192.0.2.10", 8000))
    with pytest.raises(AssertionError, match="red está prohibida"):
        transport.send(
            prepared["load-request-01"], clock=time.monotonic_ns, wall_clock=time.time_ns
        )


# --- Ventana y throughput --------------------------------------------------------------


def test_started_in_window_boundaries():
    end = T0 + ex10.WINDOW_NS
    assert ex10.window_flags(T0, T0, T0)[0] is True
    assert ex10.window_flags(T0, end - 1, end - 1)[0] is True
    assert ex10.window_flags(T0, end, end)[0] is False
    assert ex10.window_flags(T0, end + 1, end + 2)[0] is False


def test_completed_in_window_boundaries():
    end = T0 + ex10.WINDOW_NS
    assert ex10.window_flags(T0, T0, end)[1] is True
    assert ex10.window_flags(T0, T0, end + 1)[1] is False


def test_throughput_fifty_passes(plan):
    run = synthetic_run(plan)
    evaluation = evaluate_complete(plan, run)
    assert evaluation.throughput.n_ok_window == 50
    assert evaluation.throughput.per_minute == 50
    assert evaluation.throughput.status is Criterion.PASS


def test_throughput_forty_nine_fails(plan):
    run = synthetic_run(plan, responses={"load-request-50": ("status", 500, b"")})
    evaluation = evaluate_complete(plan, run)
    assert evaluation.throughput.n_ok_window == 49
    assert evaluation.throughput.status is Criterion.FAIL
    assert evaluation.decision is constants.Decision.FAIL
    assert evaluation.result.failed_criteria == ("HT1-01",)


def test_late_201_counts_for_percentiles_but_not_for_throughput(plan):
    late = {"load-request-50": (400 * MS, ex10.WINDOW_NS + 1)}
    run = synthetic_run(plan, timings=late)
    evaluation = evaluate_complete(plan, run)
    assert run.attempts[-1].completed_in_window is False
    assert evaluation.throughput.n_ok_window == 49
    assert evaluation.percentiles.n == 50
    assert evaluation.percentiles.durations_ns[-1] == ex10.WINDOW_NS + 1 - 400 * MS
    assert evaluation.percentiles.ht1_03 is Criterion.FAIL


def test_request_starting_exactly_at_window_end_never_counts(plan):
    edge = {"load-request-50": (ex10.WINDOW_NS, ex10.WINDOW_NS)}
    run = synthetic_run(plan, timings=edge)
    assert (run.attempts[-1].started_in_window, run.attempts[-1].completed_in_window) == (
        False,
        True,
    )
    assert evaluate_complete(plan, run).throughput.n_ok_window == 49


def test_201_that_fails_reconciliation_is_not_a_success(plan):
    run = synthetic_run(plan)
    persisted = persisted_for(plan, run)
    persisted["load-request-10"] = replace(persisted["load-request-10"], report_rows=0)
    persisted["load-request-11"] = replace(persisted["load-request-11"], media_file_hashes=())
    persisted["load-request-12"] = replace(
        persisted["load-request-12"], report_created_rewards=((20, 20, "ACREDITADO"),)
    )
    persisted["load-request-13"] = replace(persisted["load-request-13"], owner_alias="otro")
    evaluation = evaluate_complete(plan, run, persisted=persisted)
    not_success = {
        s.request_key: s.reason
        for s in evaluation.successes
        if s.status is ex10.SuccessStatus.NOT_SUCCESS
    }
    assert not_success == {
        "load-request-10": "report-rows:0",
        "load-request-11": "report-media-hash",
        "load-request-12": "report-created-reward",
        "load-request-13": "report-owner",
    }
    assert evaluation.throughput.n_ok_window == 46
    assert evaluation.decision is constants.Decision.FAIL


@pytest.mark.parametrize(
    "body",
    [
        {"id": "X", "status": "CREADO", "reward": {"impact_points": 20, "credits": 20}},
        {"status": "CREADO", "reward": {"impact_points": 20, "credits": 20, "status": "PENDIENTE"}},
        {"id": None, "status": "CREADO", "reward": {}},
        "uppercase-uuid",
        "float-points",
        "bool-credits",
        "extra-key",
        "wrong-status",
        "wrong-reward-status",
        [],
    ],
)
def test_contract_body_is_strict(body):
    valid = json.loads(created_body("load-request-01"))
    if body == "uppercase-uuid":
        body = {**valid, "id": valid["id"].upper()}
    elif body == "float-points":
        body = {**valid, "reward": {**valid["reward"], "impact_points": 20.0}}
    elif body == "bool-credits":
        body = {**valid, "reward": {**valid["reward"], "credits": True}}
    elif body == "extra-key":
        body = {**valid, "extra": 1}
    elif body == "wrong-status":
        body = {**valid, "status": "CLASIFICADO"}
    elif body == "wrong-reward-status":
        body = {**valid, "reward": {**valid["reward"], "status": "ACREDITADO"}}
    assert ex10.contract_body(json.dumps(body).encode()) is None
    assert ex10.contract_body(json.dumps(valid).encode()) == valid


def test_uncorrelated_or_incomplete_reconciliation_is_missing_evidence(plan):
    run = synthetic_run(plan)
    item, attempt = plan.items[0], run.attempts[0]
    evidence = persisted_for(plan, run)[item.request_key]
    for changed, reason in (
        (replace(evidence, report_rows=None), "evidence-incomplete"),
        (replace(evidence, report_id=report_uuid("otro")), "evidence-not-correlated"),
        (replace(evidence, request_key="load-request-02"), "evidence-not-correlated"),
    ):
        result = ex10.classify_success(item, attempt, changed)
        assert (result.status, result.reason) == (
            ex10.SuccessStatus.RECONCILIATION_MISSING,
            reason,
        )


def test_apparatus_invalidity_recomputes_what_it_reads(plan):
    run = synthetic_run(plan)
    assert ex10.apparatus_invalidity(plan, run) == []
    attempts = list(run.attempts)
    attempts[1] = replace(attempts[1], t_start_ns=attempts[0].t_end_ns - 1)
    assert "closed-loop:load-request-02" in ex10.apparatus_invalidity(
        plan, replace(run, attempts=tuple(attempts))
    )
    attempts = list(run.attempts)
    attempts[2] = replace(attempts[2], request_id="ajeno")
    attempts[3] = replace(attempts[3], started_in_window=False)
    attempts[4] = replace(attempts[4], duration_ns=1)
    reasons = ex10.apparatus_invalidity(plan, replace(run, attempts=tuple(attempts)))
    assert "attempt-request-id:load-request-03" in reasons
    assert "attempt-derived-timing:load-request-04" in reasons
    assert "attempt-derived-timing:load-request-05" in reasons


def test_http_201_alone_is_never_a_reconciled_success(plan):
    run = synthetic_run(plan)
    successes = ex10.classify_successes(plan, run.attempts, None)
    assert {s.status for s in successes} == {ex10.SuccessStatus.RECONCILIATION_MISSING}
    assert ex10.evaluate_throughput(run.attempts, successes).status is Criterion.NOT_EVALUABLE


# --- Percentiles e histograma ----------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "p50", "p99"),
    [(1, 1, 1), (2, 1, 2), (10, 5, 10), (50, 25, 50), (100, 50, 99), (101, 51, 100)],
)
def test_nearest_rank_is_ceil_in_integer_arithmetic(n, p50, p99):
    assert ex10.nearest_rank(n, ex10.P50) == p50
    assert ex10.nearest_rank(n, ex10.P99) == p99


def test_p50_nearest_rank_without_interpolation():
    result = percentiles_of([d * MS for d in (10, 9, 8, 7, 6, 5, 4, 3, 2, 1)])
    assert (result.p50_rank, result.p50_ns) == (5, 5 * MS)
    assert percentiles_of([d * MS for d in range(1, 12)]).p50_ns == 6 * MS


def test_p99_nearest_rank_keeps_outliers():
    durations = [MS] * 99 + [59_000 * MS]
    result = percentiles_of(durations)
    assert (result.p99_rank, result.p99_ns) == (99, MS)
    result = percentiles_of([MS] * 49 + [59_000 * MS])
    assert (result.p99_rank, result.p99_ns) == (50, 59_000 * MS)


def no_201_run(plan):
    return synthetic_run(plan, responses={i.request_key: ("status", 503, b"") for i in plan.items})


def test_no_201_makes_percentiles_not_computable(plan):
    # G0: con N = 0, P50 y P99 son NOT_COMPUTABLE (1, 2) y no hay ningún valor inventado (3).
    result = ex10.latency_percentiles(no_201_run(plan).attempts)
    assert result.n == 0
    assert (result.p50_ns, result.p99_ns, result.p50_rank, result.p99_rank) == (None,) * 4
    assert (result.ht1_02, result.ht1_03) == (Criterion.NOT_COMPUTABLE,) * 2
    latency = result.to_json()
    assert latency["ht1_02"] == {
        "p50_ns": "NOT_COMPUTABLE",
        "rank": None,
        "threshold_ns": ex10.P50_THRESHOLD_NS,
        "status": "NOT_COMPUTABLE",
    }
    assert latency["ht1_03"] == {
        "p99_ns": "NOT_COMPUTABLE",
        "rank": None,
        "threshold_ns": ex10.P99_THRESHOLD_NS,
        "status": "NOT_COMPUTABLE",
    }
    assert latency["durations_ns_sorted"] == []


def test_a_valid_run_without_201_fails_by_ht1_01_only(plan):
    # (4, 5) Ni PASS ni un FAIL por un percentil: el FAIL lo da HT1-01 (0 éxitos).
    evaluation = evaluate_complete(plan, no_201_run(plan))
    result = evaluation.result
    assert evaluation.decision is constants.Decision.FAIL
    assert result.failed_criteria == ("HT1-01",)
    assert (result.invalidity_reasons, result.not_demonstrable) == ((), ())
    assert result.criteria["HT1-01"] == "FAIL"
    assert (result.criteria["HT1-02"], result.criteria["HT1-03"]) == ("NOT_COMPUTABLE",) * 2


def test_an_invalid_apparatus_without_201_is_not_demonstrated(plan):
    # (6)
    run = replace(no_201_run(plan), faults=(ex10.ApparatusFault(ex10.FaultKind.THREAD_DIED, vu=3),))
    evaluation = evaluate_complete(plan, run)
    assert evaluation.decision is constants.Decision.NOT_DEMONSTRATED
    assert "apparatus:thread_died:vu-03" in evaluation.result.invalidity_reasons


def test_not_computable_percentiles_never_yield_pass():
    # (4) Entradas incoherentes (N = 0 con HT1-01 PASS): nunca PASS, tampoco un FAIL inventado.
    passed = ex10.EvidenceResult(ex10.EvidenceStatus.PASS)
    result = ex10.decide(
        invalidity_reasons=(),
        throughput=ex10.ThroughputResult(Criterion.PASS, 50, 50),
        percentiles=ex10.latency_percentiles([]),
        ht1_04=passed,
        audit=passed,
    )
    assert result.decision is constants.Decision.NOT_DEMONSTRATED
    assert result.failed_criteria == ()
    assert result.not_demonstrable == ("HT1-02:NOT_COMPUTABLE", "HT1-03:NOT_COMPUTABLE")
    assert result.invalidity_reasons == ()


@pytest.mark.parametrize(
    ("duration", "status"),
    [(800 * MS, Criterion.PASS), (800 * MS + 1, Criterion.FAIL)],
)
def test_a_single_201_still_computes_both_percentiles(duration, status):
    # (7) N = 1: rango 1 para P50 y P99, sin NOT_COMPUTABLE.
    result = percentiles_of([duration])
    assert (result.p50_rank, result.p99_rank) == (1, 1)
    assert result.p50_ns == result.p99_ns == duration
    assert result.ht1_02 is status and result.ht1_03 is Criterion.PASS


@pytest.mark.parametrize(
    ("durations", "p50_rank", "p99_rank"),
    [([3, 1, 2], 2, 3), ([4, 3, 2, 1], 2, 4), (list(range(1, 101)), 50, 99)],
)
def test_more_than_one_201_stays_nearest_rank(durations, p50_rank, p99_rank):
    # (8)
    result = percentiles_of([d * MS for d in durations])
    assert (result.p50_rank, result.p99_rank) == (p50_rank, p99_rank)
    assert result.p50_ns == sorted(durations)[p50_rank - 1] * MS
    assert result.p99_ns == sorted(durations)[p99_rank - 1] * MS
    assert Criterion.NOT_COMPUTABLE not in (result.ht1_02, result.ht1_03)


def test_not_computable_serializes_canonically_and_deterministically(plan):
    # (9) Mismo JSON canónico, mismas versiones de esquema, sin ningún valor numérico nuevo.
    def payloads():
        evaluation = evaluate_complete(plan, no_201_run(plan))
        return evaluation.calculation_payload(), evaluation.decision_payload()

    first, second = payloads(), payloads()
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    calculation, decision = first
    assert calculation["schema_version"] == ex10.CALCULATION_SCHEMA_VERSION
    assert decision["schema_version"] == ex10.DECISION_SCHEMA_VERSION
    assert json.loads(canonical_json_bytes(decision))["criteria"]["HT1-02"] == "NOT_COMPUTABLE"
    assert calculation["latency"]["n"] == 0


def test_every_serialized_criterion_status_still_parses():
    # (10) Los valores anteriores siguen siendo válidos; el nuevo es aditivo. No existe
    # ningún artefacto EX-10 persistido que reinterpretar (EX-10 nunca se ejecutó).
    for value in ("PASS", "FAIL", "NOT_EVALUABLE", "NOT_COMPUTABLE"):
        assert str(Criterion(value)) == value
    assert Criterion.NOT_COMPUTABLE == ex10.NOT_COMPUTABLE


def test_p50_of_exactly_800_ms_passes():
    assert percentiles_of([800 * MS] * 50).ht1_02 is Criterion.PASS
    assert percentiles_of([800 * MS + 1] * 50).ht1_02 is Criterion.FAIL


def test_p99_of_exactly_2000_ms_passes():
    assert percentiles_of([MS] * 49 + [2_000 * MS]).ht1_03 is Criterion.PASS


def test_p99_above_2000_ms_fails():
    assert percentiles_of([MS] * 49 + [2_000 * MS + 1]).ht1_03 is Criterion.FAIL


def test_histogram_bins_are_half_open_and_diagnostic_only():
    durations = [0, 99_999_999, 100 * MS, 1_999_999_999, 2_000 * MS, 9_999 * MS, 60_000 * MS]
    bins = ex10.latency_histogram(durations)
    assert len(bins) == 24
    assert bins[0] == {"lower_ms": 0, "upper_ms": 100, "count": 2}
    assert bins[1] == {"lower_ms": 100, "upper_ms": 200, "count": 1}
    assert bins[19] == {"lower_ms": 1900, "upper_ms": 2000, "count": 1}
    assert bins[20] == {"lower_ms": 2000, "upper_ms": 5000, "count": 1}
    assert bins[21] == {"lower_ms": 5000, "upper_ms": 10000, "count": 1}
    assert bins[22] == {"lower_ms": 10000, "upper_ms": 60000, "count": 0}
    assert bins[23] == {"lower_ms": 60000, "upper_ms": None, "count": 1}
    assert sum(b["count"] for b in bins) == len(durations)
    with pytest.raises(ValueError):
        ex10.latency_histogram([-1])


def test_calculation_keeps_raw_durations_and_counts_failures_apart(plan):
    run = synthetic_run(plan, responses={"load-request-02": ("status", 429, b"{}")})
    calculation = evaluate_complete(plan, run).calculation_payload()
    assert calculation["latency"]["durations_ns_sorted"] == [50 * MS] * 49
    assert calculation["attempts"]["failures_by_class"] == {"http_4xx": 1}
    assert sum(b["count"] for b in calculation["histogram"]["bins"]) == 49
    assert calculation["histogram"]["diagnostic_only"] is True


# --- HT1-04 ----------------------------------------------------------------------------


@pytest.fixture
def ids(plan):
    return [item.request_id for item in plan.items[:3]]


@pytest.fixture
def evidence(ids):
    return async_evidence_for(ids)


def ht104(ids, evidence):
    return ex10.evaluate_ht1_04(ids, evidence)


def codes(result):
    return {(f.status, f.code) for f in result.result.findings}


def test_ht1_04_perfect_evidence_passes_without_comparing_task_end(ids, evidence):
    facts = evidence.facts[ids[0]]
    finished = next(e for e in evidence.events if e.event == "celery.finished")
    assert finished.finish_at_utc_ns > facts.http_finished_at_utc_ns
    result = ht104(ids, evidence)
    assert result.status is Evidence.PASS
    assert result.result.findings == ()
    assert result.queue_latency_ns[ids[0]] == 5 * MS
    assert result.task_duration_ns[ids[0]] == 2 * MS


def test_ht1_04_queued_missing_with_independent_evidence_is_not_demonstrable(ids, evidence):
    result = ht104(ids, without_events(evidence, ids[0], "celery.queued"))
    assert result.status is Evidence.EVIDENCE_MISSING
    assert (Evidence.EVIDENCE_MISSING, "queued:missing") in codes(result)
    in_broker = with_facts(
        without_events(evidence, ids[0], "celery.queued", "celery.started", "celery.finished"),
        ids[0],
        ai_classification_models=(),
        task_in_broker=True,
    )
    assert ht104(ids, in_broker).status is Evidence.EVIDENCE_MISSING


def test_ht1_04_queued_missing_without_any_trace_is_a_failed_fact(ids, evidence):
    silent = with_facts(
        without_events(evidence, ids[0], "celery.queued", "celery.started", "celery.finished"),
        ids[0],
        ai_classification_models=(),
        final_status="CREADO",
        ai_classified_audit_rows=0,
    )
    result = ht104(ids, silent)
    assert result.status is Evidence.FACT_FAILED
    assert (Evidence.FACT_FAILED, "queued:not-processed") in codes(result)
    logged = with_facts(silent, ids[0], enqueue_failure_logged=True)
    assert (Evidence.FACT_FAILED, "queued:enqueue-failed") in codes(ht104(ids, logged))


def test_ht1_04_uninspected_sources_are_never_taken_as_absent(ids, evidence):
    no_events = without_events(
        evidence, ids[0], "celery.queued", "celery.started", "celery.finished"
    )
    broker_unknown = with_facts(no_events, ids[0], ai_classification_models=(), task_in_broker=None)
    result = ht104(ids, broker_unknown)
    assert result.status is Evidence.EVIDENCE_MISSING
    assert (Evidence.EVIDENCE_MISSING, "queued:missing-uninspected") in codes(result)
    assert (Evidence.FACT_FAILED, "queued:not-processed") not in codes(result)
    classification_unknown = with_facts(no_events, ids[0], ai_classification_models=None)
    assert (Evidence.EVIDENCE_MISSING, "queued:missing-uninspected") in codes(
        ht104(ids, classification_unknown)
    )
    # El log sólo distingue entre dos hechos fallidos: sin inspeccionarlo, sigue siendo FAIL.
    log_unknown = with_facts(
        no_events, ids[0], ai_classification_models=(), enqueue_failure_logged=None
    )
    assert (Evidence.FACT_FAILED, "queued:not-processed") in codes(ht104(ids, log_unknown))
    started_unknown = with_facts(
        without_events(evidence, ids[1], "celery.started", "celery.finished"),
        ids[1],
        ai_classification_models=None,
    )
    assert (Evidence.EVIDENCE_MISSING, "started:missing-uninspected") in codes(
        ht104(ids, started_unknown)
    )
    finished_unknown = with_facts(
        without_events(evidence, ids[2], "celery.finished"), ids[2], ai_classification_models=None
    )
    assert (Evidence.EVIDENCE_MISSING, "finished:missing-uninspected") in codes(
        ht104(ids, finished_unknown)
    )


def test_ht1_04_started_missing(ids, evidence):
    classified = ht104(ids, without_events(evidence, ids[1], "celery.started"))
    assert classified.status is Evidence.EVIDENCE_MISSING
    assert (Evidence.EVIDENCE_MISSING, "started:missing") in codes(classified)
    unprocessed = with_facts(
        without_events(evidence, ids[1], "celery.started", "celery.finished"),
        ids[1],
        ai_classification_models=(),
        final_status="CREADO",
        ai_classified_audit_rows=0,
    )
    result = ht104(ids, unprocessed)
    assert result.status is Evidence.FACT_FAILED
    assert (Evidence.FACT_FAILED, "started:not-processed") in codes(result)


def test_ht1_04_finished_missing(ids, evidence):
    classified = ht104(ids, without_events(evidence, ids[2], "celery.finished"))
    assert classified.status is Evidence.EVIDENCE_MISSING
    assert (Evidence.EVIDENCE_MISSING, "finished:missing") in codes(classified)
    unfinished = with_facts(
        without_events(evidence, ids[2], "celery.finished"),
        ids[2],
        ai_classification_models=(),
    )
    assert (Evidence.FACT_FAILED, "finished:not-processed") in codes(ht104(ids, unfinished))


def test_ht1_04_celery_failed_is_a_failed_fact(ids, evidence):
    failed = with_event(evidence, ids[0], "celery.finished", event="celery.failed")
    failed = with_facts(failed, ids[0], ai_classification_models=())
    result = ht104(ids, failed)
    assert result.status is Evidence.FACT_FAILED
    assert (Evidence.FACT_FAILED, "finished:celery-failed") in codes(result)


def test_ht1_04_task_in_the_api_process_is_a_failed_fact(ids, evidence):
    result = ht104(ids, with_event(evidence, ids[0], "celery.started", process=API))
    assert result.status is Evidence.FACT_FAILED
    assert (Evidence.FACT_FAILED, "started:process-is-api") in codes(result)
    queued_elsewhere = with_event(evidence, ids[0], "celery.queued", process=WORKER)
    assert (Evidence.FACT_FAILED, "queued:process-not-api") in codes(ht104(ids, queued_elsewhere))


def test_ht1_04_open_transaction_at_enqueue_is_a_failed_fact(ids, evidence):
    result = ht104(ids, with_event(evidence, ids[0], "celery.queued", db_transaction_open=True))
    assert result.status is Evidence.FACT_FAILED
    assert (Evidence.FACT_FAILED, "queued:db-transaction-open") in codes(result)
    unknown = with_event(evidence, ids[0], "celery.queued", db_transaction_open=None)
    assert ht104(ids, unknown).status is Evidence.EVIDENCE_MISSING


def test_ht1_04_wrong_model_name_is_a_failed_fact(ids, evidence):
    wrong = with_facts(evidence, ids[0], ai_classification_models=("gemini-3.6-flash",))
    result = ht104(ids, wrong)
    assert result.status is Evidence.FACT_FAILED
    assert (Evidence.FACT_FAILED, "classification:model-name") in codes(result)


def test_ht1_04_missing_classification(ids, evidence):
    absent = ht104(ids, with_facts(evidence, ids[0], ai_classification_models=()))
    assert absent.status is Evidence.FACT_FAILED
    assert (Evidence.FACT_FAILED, "classification:count:0") in codes(absent)
    twice = with_facts(evidence, ids[0], ai_classification_models=(ex10.AI_MODEL_NAME,) * 2)
    assert (Evidence.FACT_FAILED, "classification:count:2") in codes(ht104(ids, twice))
    not_inspected = with_facts(evidence, ids[0], ai_classification_models=None)
    assert ht104(ids, not_inspected).status is Evidence.EVIDENCE_MISSING


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"final_status": "CREADO"}, "final-status"),
        ({"ai_classified_audit_rows": 0}, "audit-ai-classified:count:0"),
        ({"ai_classified_audit_rows": 2}, "audit-ai-classified:count:2"),
    ],
)
def test_ht1_04_final_state_and_audit_are_required(ids, evidence, changes, code):
    result = ht104(ids, with_facts(evidence, ids[0], **changes))
    assert (Evidence.FACT_FAILED, code) in codes(result)


def test_ht1_04_duplicates_and_wrong_task_name_are_failed_facts(ids, evidence):
    queued = next(e for e in evidence.events if e.event == "celery.queued")
    doubled = replace(evidence, events=(*evidence.events, replace(queued, task_id="task-xx")))
    assert (Evidence.FACT_FAILED, "queued:count:2") in codes(ht104(ids, doubled))
    renamed = with_event(evidence, ids[0], "celery.queued", task_name="otra.tarea")
    assert (Evidence.FACT_FAILED, "queued:task-name") in codes(ht104(ids, renamed))


def test_ht1_04_temporal_violation_with_correct_structure_is_not_demonstrable(ids, evidence):
    facts = evidence.facts[ids[0]]
    early = with_event(
        evidence, ids[0], "celery.queued", enqueue_at_utc_ns=facts.report_created_at_utc_ns - 1
    )
    result = ht104(ids, early)
    assert result.status is Evidence.INCONSISTENT
    assert ex10.EVIDENCE_DECISION[result.status] is constants.Decision.NOT_DEMONSTRATED
    before_enqueue = with_event(evidence, ids[0], "celery.started", start_at_utc_ns=0)
    assert (Evidence.INCONSISTENT, "started:before-enqueue") in codes(ht104(ids, before_enqueue))
    # Con un indicador estructural fallido, la violación temporal no salva el FAIL,
    # pero queda registrada como diagnóstico.
    structural = ht104(ids, with_event(early, ids[0], "celery.queued", db_transaction_open=True))
    assert structural.status is Evidence.FACT_FAILED
    assert structural.temporal_violations == {ids[0]: ("queued:temporal-order",)}
    assert structural.to_json()["temporal_violations"] == {ids[0]: ["queued:temporal-order"]}


@pytest.mark.parametrize(
    ("changes", "status"),
    [
        ({"api_task_always_eager": True}, Evidence.INCONSISTENT),
        ({"worker_task_always_eager": None}, Evidence.EVIDENCE_MISSING),
        ({"broker_url": "redis://redis:6379/0"}, Evidence.INCONSISTENT),
        ({"worker_max_concurrency_after": 1}, Evidence.INCONSISTENT),
        ({"worker_nodes_before": 2}, Evidence.INCONSISTENT),
        ({"worker_fake_ai_enabled": 1}, Evidence.INCONSISTENT),
    ],
)
def test_ht1_04_requires_the_frozen_async_configuration(ids, evidence, changes, status):
    config = replace(CONFIG, **changes)
    assert ht104(ids, replace(evidence, config=config)).status is status
    assert ht104(ids, replace(evidence, config=None)).status is Evidence.EVIDENCE_MISSING


def test_ht1_04_missing_evidence_prevails_over_a_failed_fact(ids, evidence):
    mixed = with_facts(
        without_events(evidence, ids[0], "celery.started"),
        ids[1],
        ai_classification_models=(),
    )
    result = ht104(ids, mixed)
    statuses = {f.status for f in result.result.findings}
    assert {Evidence.EVIDENCE_MISSING, Evidence.FACT_FAILED} <= statuses
    assert result.status is Evidence.EVIDENCE_MISSING


def test_ht1_04_without_evidence_or_facts_is_not_demonstrable(ids, evidence):
    assert ex10.evaluate_ht1_04(ids, None).status is Evidence.EVIDENCE_MISSING
    no_facts = replace(evidence, facts={})
    assert (Evidence.EVIDENCE_MISSING, "facts:missing") in codes(ht104(ids, no_facts))
    assert ex10.evaluate_ht1_04([], replace(evidence, facts={})).status is Evidence.PASS


def test_evidence_statuses_map_to_the_g0_decision():
    assert ex10.EVIDENCE_DECISION == {
        Evidence.PASS: constants.Decision.PASS,
        Evidence.FACT_FAILED: constants.Decision.FAIL,
        Evidence.EVIDENCE_MISSING: constants.Decision.NOT_DEMONSTRATED,
        Evidence.INCONSISTENT: constants.Decision.NOT_DEMONSTRATED,
    }


# --- Decisión --------------------------------------------------------------------------


def test_decision_pass_for_a_complete_generator_run(plan, prepared):
    run = run_generator(plan, prepared, FakeTransport())
    evaluation = evaluate_complete(plan, run)
    assert evaluation.decision is constants.Decision.PASS
    decision = evaluation.decision_payload()
    assert decision["decision"] == "PASS"
    assert decision["manifest_status"] == "COMPLETED"
    assert decision["criteria"] == {
        "HT1-01": "PASS",
        "HT1-02": "PASS",
        "HT1-03": "PASS",
        "HT1-04": "PASS",
        "audit-postcondition": "PASS",
    }
    assert decision["invalidity_reasons"] == decision["not_demonstrable"] == []
    assert decision["plan_sha256"] == ex10.PLAN_SHA256


@pytest.mark.parametrize(
    ("scenario", "failed"),
    [
        ("slow-p99", ("HT1-03",)),
        ("audit-missing-row", ("audit-postcondition",)),
        ("ht1-04-fact", ("HT1-04",)),
    ],
)
def test_decision_fail_for_a_valid_run(plan, scenario, failed):
    run = synthetic_run(plan)
    persisted = persisted_for(plan, run)
    async_evidence = async_evidence_for(accepted_request_ids(plan, run))
    if scenario == "slow-p99":
        run = synthetic_run(plan, timings={"load-request-05": (400 * MS, 2_500 * MS)})
    elif scenario == "audit-missing-row":
        persisted["load-request-01"] = replace(
            persisted["load-request-01"], report_created_audit_rows=0
        )
    else:
        request_id = plan.items[0].request_id
        async_evidence = with_event(async_evidence, request_id, "celery.started", process=API)
    evaluation = ex10.evaluate_run(plan, run, persisted=persisted, async_evidence=async_evidence)
    assert evaluation.decision is constants.Decision.FAIL
    assert evaluation.result.failed_criteria == failed
    assert evaluation.decision_payload()["manifest_status"] == "COMPLETED"


def test_decision_not_demonstrated_without_reconciliation(plan):
    run = synthetic_run(plan)
    evaluation = ex10.evaluate_run(plan, run)
    assert evaluation.decision is constants.Decision.NOT_DEMONSTRATED
    assert "HT1-01:NOT_EVALUABLE" in evaluation.result.not_demonstrable
    assert evaluation.decision_payload()["manifest_status"] == "INVALID"
    # Basta con que falte la reconciliación de un solo 201.
    persisted = persisted_for(plan, run)
    persisted.pop("load-request-33")
    partial = evaluate_complete(plan, run, persisted=persisted)
    assert partial.decision is constants.Decision.NOT_DEMONSTRATED
    assert partial.throughput.reconciliation_missing == ("load-request-33",)


@pytest.mark.parametrize(
    "cause",
    ["environment", "ht1-04-evidence", "audit-evidence", "plan-sha", "missing-attempt"],
)
def test_decision_not_demonstrated_for_invalid_runs_or_missing_evidence(plan, cause):
    run = synthetic_run(plan)
    kwargs = {}
    if cause == "environment":
        kwargs["environment_invalidity"] = ["runserver-reload"]
    elif cause == "ht1-04-evidence":
        kwargs["async_evidence"] = None
        kwargs["persisted"] = persisted_for(plan, run)
    elif cause == "audit-evidence":
        persisted = persisted_for(plan, run)
        persisted["load-request-01"] = replace(
            persisted["load-request-01"], report_created_audit_rows=None
        )
        kwargs["persisted"] = persisted
    elif cause == "plan-sha":
        run = replace(run, plan_sha256="0" * 64)
    else:
        run = replace(run, attempts=run.attempts[:-1])
    if "async_evidence" in kwargs:
        evaluation = ex10.evaluate_run(plan, run, **kwargs)
    else:
        evaluation = evaluate_complete(plan, run, **kwargs)
    assert evaluation.decision is constants.Decision.NOT_DEMONSTRATED


def test_not_demonstrated_prevails_over_failed_criteria(plan):
    run = synthetic_run(plan, responses={"load-request-01": ("status", 500, b"")})
    evaluation = evaluate_complete(plan, run, environment_invalidity=["container-restarted"])
    assert evaluation.result.failed_criteria == ("HT1-01",)
    assert evaluation.decision is constants.Decision.NOT_DEMONSTRATED


def test_decide_is_pure_over_its_inputs():
    passing = ex10.ThroughputResult(Criterion.PASS, 50, 50)
    percentiles = percentiles_of([MS] * 50)
    ok = ex10.EvidenceResult(Evidence.PASS)
    arguments = {"throughput": passing, "percentiles": percentiles, "ht1_04": ok, "audit": ok}
    assert ex10.decide(invalidity_reasons=[], **arguments).decision is constants.Decision.PASS
    assert (
        ex10.decide(invalidity_reasons=["x"], **arguments).decision
        is constants.Decision.NOT_DEMONSTRATED
    )
    failed = {**arguments, "ht1_04": ex10.EvidenceResult(Evidence.FACT_FAILED)}
    assert ex10.decide(invalidity_reasons=[], **failed).decision is constants.Decision.FAIL
    missing = {**arguments, "audit": ex10.EvidenceResult(Evidence.EVIDENCE_MISSING)}
    assert (
        ex10.decide(invalidity_reasons=[], **missing).decision
        is constants.Decision.NOT_DEMONSTRATED
    )


# --- Contenido sensible ----------------------------------------------------------------


@pytest.fixture
def artifacts(plan, prepared):
    run = run_generator(plan, prepared, FakeTransport())
    evaluation = evaluate_complete(plan, run)
    return {
        "plan": plan.to_json(),
        "raw": ex10.raw_results_payload(plan, run),
        "calculation": evaluation.calculation_payload(),
        "decision": evaluation.decision_payload(),
        "reprs": [repr(run), repr(prepared), repr(evaluation)],
    }


def test_no_artifact_contains_a_token(artifacts, dataset, tokens):
    for name, payload in artifacts.items():
        text = canonical_json_bytes(payload).decode()
        assert not any(token in text for token in tokens.values()), name
        assert "Bearer" not in text, name
        assert ex10.sensitive_findings(payload, dataset=dataset, secrets=tokens.values()) == []


def test_no_serialized_result_contains_coordinates(artifacts, dataset):
    for payload in artifacts.values():
        text = canonical_json_bytes(payload).decode()
        for row in dataset.payload["requests"]:
            assert repr(row["latitude"]) not in text
            assert repr(row["longitude"]) not in text
        assert '"lat"' not in text and '"latitude"' not in text


def test_no_result_contains_image_bytes(artifacts, dataset):
    for payload in artifacts.values():
        text = canonical_json_bytes(payload).decode()
        assert "iVBORw0KGgo" not in text and "\x89PNG" not in text
        for row in dataset.payload["requests"]:
            assert row["image"]["base64"] not in text


def test_sensitive_detector_is_not_vacuous(dataset, tokens):
    row = dataset.payload["requests"][0]
    token = tokens["load-citizen-01"]
    assert "secret-pattern" in ex10.sensitive_findings({"x": token}, dataset=dataset)
    assert "secret-value" in ex10.sensitive_findings(
        {"x": "opaque-token-value"}, dataset=dataset, secrets=["opaque-token-value"]
    )
    found = ex10.sensitive_findings({"x": [row["latitude"]]}, dataset=dataset)
    assert "coordinate:load-request-01" in found
    # Una clave de coordenada a cualquier profundidad se marca; su nombre como dato, no.
    found = ex10.sensitive_findings({"a": [{"lng": 0}]}, dataset=dataset)
    assert found == ["sensitive-key:lng"]
    assert ex10.sensitive_findings({"json_keys": ["lat", "lng"]}, dataset=dataset) == []
    found = ex10.sensitive_findings({"x": row["image"]["base64"]}, dataset=dataset)
    assert "png-content" in found and "image:load-request-01" in found


# --- Transporte stdlib de extremo a extremo --------------------------------------------


class ServerSocket(FakeSocket):
    """Socket falso por conexión: decide la respuesta al leer el `X-S11-Request-ID`."""

    def __init__(self, server):
        super().__init__()
        self.server = server

    def sendall(self, data):
        request_id = re.search(rb"\r\nX-S11-Request-ID: ([^\r]+)\r\n", data).group(1).decode()
        key = self.server.keys[request_id]
        with self.server.lock:
            self.server.connections[key] += 1
        action = self.server.script.get(key, http_response(201, "Created", created_body(key)))
        if isinstance(action, tuple) and action[0] == "send":
            raise action[1]
        super().sendall(data)
        if isinstance(action, bytes):
            self.chunks = [action]
        elif isinstance(action, tuple) and action[0] == "partial":
            self.chunks, self.recv_error = [action[1]], action[2]
        else:
            self.recv_error = action


class ScriptedServer:
    """Connector que abre un `ServerSocket` nuevo por conexión y las cuenta."""

    def __init__(self, plan, script=None):
        self.keys = {item.request_id: item.request_key for item in plan.items}
        self.script = script or {}
        self.connections = Counter()
        self.lock = threading.Lock()

    def __call__(self, address, timeout):
        assert address == ("192.0.2.10", 8000) and 0 < timeout <= 60
        return ServerSocket(self)


def run_over_stdlib(plan, prepared, script=None):
    server = ScriptedServer(plan, script)
    transport = ex10.StdlibHttpTransport(("192.0.2.10", 8000), connect=server)
    return run_generator(plan, prepared, transport), server


def test_generator_over_the_stdlib_transport_passes_end_to_end(plan, prepared):
    run, server = run_over_stdlib(plan, prepared)
    # Una conexión TCP por request y ninguna más: 0 reintentos.
    assert server.connections == {item.request_key: 1 for item in plan.items}
    assert all(a.outcome is Outcome.HTTP_ACCEPTED for a in run.attempts)
    assert ex10.apparatus_invalidity(plan, run) == []
    assert evaluate_complete(plan, run).decision is constants.Decision.PASS


def _truncated_201(key):
    body = created_body(key)
    return http_response(201, "Created", body[:10], length=len(body))


def test_valid_request_failures_end_to_end_make_a_valid_fail(plan, prepared):
    script = {
        "load-request-03": OSError(errno.EHOSTUNREACH, "No route to host"),
        "load-request-08": ("send", OSError(errno.ENETUNREACH, "Network is unreachable")),
        "load-request-13": ConnectionAbortedError(errno.ECONNABORTED, "aborted"),
        "load-request-18": _truncated_201("load-request-18"),
        "load-request-23": http_response(200, "OK", b"{}"),
        "load-request-28": b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n",
        "load-request-33": http_response(302, "Found", b""),
        "load-request-38": http_response(429, "Too Many Requests", b'{"detail":"throttled"}'),
        "load-request-43": http_response(500, "Internal Server Error", b"<html>trace</html>"),
        "load-request-48": http_response(201, "Created", b'{"id": "no-contractual"}'),
    }
    run, server = run_over_stdlib(plan, prepared, script)
    assert server.connections == {item.request_key: 1 for item in plan.items}
    by_key = {a.request_key: a for a in run.attempts}
    expected = {
        "load-request-03": (Outcome.TRANSPORT_FAILED, "network_unreachable", None, "receive"),
        "load-request-08": (Outcome.TRANSPORT_FAILED, "network_unreachable", None, "send"),
        "load-request-13": (Outcome.TRANSPORT_FAILED, "connection_aborted", None, "receive"),
        "load-request-18": (Outcome.TRANSPORT_FAILED, "incomplete_read", 201, "receive"),
        "load-request-23": (Outcome.HTTP_REJECTED, "http_2xx", 200, None),
        "load-request-28": (Outcome.HTTP_REJECTED, "http_2xx", 204, None),
        "load-request-33": (Outcome.HTTP_REJECTED, "http_3xx", 302, None),
        "load-request-38": (Outcome.HTTP_REJECTED, "http_4xx", 429, None),
        "load-request-43": (Outcome.HTTP_REJECTED, "http_5xx", 500, None),
        "load-request-48": (Outcome.CONTRACT_VIOLATION, "contract_violation", 201, None),
    }
    for key, (outcome, error_class, status, phase) in expected.items():
        attempt = by_key[key]
        assert (attempt.outcome, attempt.error_class, attempt.http_status) == (
            outcome,
            error_class,
            status,
        ), key
        assert attempt.error_phase == phase, key
    assert run.faults == ()
    assert ex10.apparatus_invalidity(plan, run) == []
    evaluation = evaluate_complete(plan, run)
    # 40 éxitos + el 201 no contractual; el 201 truncado no entra.
    assert evaluation.percentiles.n == 41
    assert evaluation.throughput.n_ok_window == 40
    assert evaluation.decision is constants.Decision.FAIL
    assert evaluation.result.failed_criteria == ("HT1-01",)
    raw = canonical_json_bytes(ex10.raw_results_payload(plan, run)).decode()
    assert "throttled" not in raw and "trace" not in raw and "No route" not in raw


def test_unattributable_errors_end_to_end_are_not_demonstrated(plan, prepared):
    script = {
        "load-request-05": OSError(errno.EMFILE, "Too many open files"),
        "load-request-10": KeyError("fallo interno"),
    }
    run, server = run_over_stdlib(plan, prepared, script)
    assert server.connections == {item.request_key: 1 for item in plan.items}
    assert run.attempts[4].outcome is Outcome.APPARATUS_FAILURE
    assert run.attempts[4].error_detail_sanitized == "OSError:EMFILE"
    assert run.attempts[9].outcome is Outcome.APPARATUS_FAILURE
    assert {(f.kind, f.request_key) for f in run.faults} == {
        (Fault.UNATTRIBUTABLE_ERROR, "load-request-05"),
        (Fault.INTERNAL_EXCEPTION, "load-request-10"),
    }
    assert evaluate_complete(plan, run).decision is constants.Decision.NOT_DEMONSTRATED


# --- Terminología de G0-A1: APPARATUS_FAILURE -----------------------------------------


def test_apparatus_failure_is_the_only_public_apparatus_outcome(plan, prepared):
    enums = (
        Outcome,
        ex10.ItemState,
        Fault,
        ex10.SuccessStatus,
        Criterion,
        Evidence,
    )
    assert Outcome.APPARATUS_FAILURE.value == "APPARATUS_FAILURE"
    assert ex10.ItemState.APPARATUS_FAILURE.value == "APPARATUS_FAILURE"
    assert not any("APPARATUS_FAILED" in {*e.__members__, *map(str, e)} for e in enums)

    def hook(request):
        if request.item.request_key == "load-request-13":
            raise ThreadKill
        if request.item.request_key == "load-request-22":
            raise RuntimeError("fallo interno del generador")

    responses = {"load-request-31": ("error", "unattributable_error", "OSError:EMFILE")}
    run = run_generator(plan, prepared, FakeTransport(responses, hook=hook))
    by_key = {a.request_key: a for a in run.attempts}
    # El outcome es la clasificación pública; la clase concreta queda en `error_class`.
    expected = {
        "load-request-13": ("APPARATUS_FAILURE", "result_lost"),
        "load-request-22": ("APPARATUS_FAILURE", "internal_exception"),
        "load-request-31": ("APPARATUS_FAILURE", "unattributable_error"),
    }
    for key, (outcome, error_class) in expected.items():
        serialized = by_key[key].to_json()
        assert by_key[key].outcome is Outcome.APPARATUS_FAILURE
        assert (serialized["outcome"], serialized["error_class"]) == (outcome, error_class)
    assert by_key["load-request-13"].to_json()["state"] == "APPARATUS_FAILURE"
    evaluation = evaluate_complete(plan, run)
    assert evaluation.decision is constants.Decision.NOT_DEMONSTRATED
    calculation = evaluation.calculation_payload()
    assert calculation["attempts"]["by_outcome"]["APPARATUS_FAILURE"] == 3
    assert "APPARATUS_FAILURE" not in calculation["attempts"]["failures_by_class"]
    payloads = (
        ex10.raw_results_payload(plan, run),
        calculation,
        evaluation.decision_payload(),
    )
    for payload in payloads:
        assert "APPARATUS_FAILED" not in canonical_json_bytes(payload).decode()
    assert "APPARATUS_FAILED" not in repr(run) + repr(evaluation)


# --- Decisión A: errores de transporte no enumerados ----------------------------------


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (TimeoutError("timed out"), "timeout"),
        (TimeoutError("deadline"), "timeout"),
        (OSError(errno.ETIMEDOUT, "timed out"), "timeout"),
        (ConnectionRefusedError(errno.ECONNREFUSED, "x"), "connection_refused"),
        (ConnectionResetError(errno.ECONNRESET, "x"), "connection_reset"),
        (http.client.RemoteDisconnected("closed"), "connection_reset"),
        (OSError(errno.ENETRESET, "x"), "connection_reset"),
        (ConnectionAbortedError(errno.ECONNABORTED, "x"), "connection_aborted"),
        (BrokenPipeError(errno.EPIPE, "x"), "broken_pipe"),
        (ConnectionError("x"), "connection_error"),
        (OSError(errno.EHOSTUNREACH, "x"), "network_unreachable"),
        (OSError(errno.ENETUNREACH, "x"), "network_unreachable"),
        (OSError(errno.EHOSTDOWN, "x"), "network_unreachable"),
        (OSError(errno.ENETDOWN, "x"), "network_unreachable"),
        (http.client.BadStatusLine("garbage"), "protocol_error"),
        (http.client.LineTooLong("header line"), "protocol_error"),
        (http.client.UnknownProtocol("HTTP/9.9"), "protocol_error"),
        (http.client.IncompleteRead(b"", 10), "incomplete_read"),
    ],
)
def test_decision_a_classifies_transport_errors_by_family(exc, expected):
    assert ex10.transport_error_class(exc) == expected
    assert expected in ex10.TRANSPORT_ERROR_CLASSES


def test_socket_timeout_is_the_builtin_timeout():
    # `socket.timeout` es un alias de `TimeoutError` desde Python 3.10: misma clase.
    assert vars(socket)["timeout"] is TimeoutError


@pytest.mark.parametrize(
    "exc",
    [
        OSError(errno.EMFILE, "x"),
        OSError(errno.ENFILE, "x"),
        OSError(errno.ENOBUFS, "x"),
        OSError(errno.EADDRNOTAVAIL, "x"),
        OSError(errno.EINVAL, "x"),
        OSError("sin errno"),
        PermissionError(errno.EACCES, "x"),
        FileNotFoundError(errno.ENOENT, "x"),
        socket.gaierror(socket.EAI_NONAME, "x"),
        ValueError("bug"),
        KeyError("bug"),
        RuntimeError("bug"),
        AssertionError("bug"),
    ],
)
def test_decision_a_unattributable_errors_are_never_transport_failures(exc):
    assert ex10.transport_error_class(exc) is None


@pytest.mark.parametrize(
    ("sock", "error", "phase", "error_class"),
    [
        (None, OSError(errno.ENETUNREACH, "net"), "connect", "network_unreachable"),
        (None, OSError(errno.EHOSTUNREACH, "host"), "connect", "network_unreachable"),
        (None, ConnectionAbortedError(errno.ECONNABORTED, "x"), "connect", "connection_aborted"),
        (
            FakeSocket(send_error=OSError(errno.EHOSTUNREACH, "host")),
            None,
            "send",
            "network_unreachable",
        ),
        (
            FakeSocket(recv_error=ConnectionAbortedError(errno.ECONNABORTED, "x")),
            None,
            "receive",
            "connection_aborted",
        ),
        (
            FakeSocket(recv_error=OSError(errno.ENETDOWN, "x")),
            None,
            "receive",
            "network_unreachable",
        ),
    ],
)
def test_decision_a_network_errors_are_valid_failures_with_their_phase(
    prepared, sock, error, phase, error_class
):
    exchange, _ = send_with(prepared, sock, error=error)
    assert (exchange.error_class, exchange.error_phase) == (error_class, phase)
    record = ex10.attempt_record(prepared["load-request-01"].item, exchange, t0_ns=0)
    assert (record.outcome, record.error_class, record.error_phase) == (
        Outcome.TRANSPORT_FAILED,
        error_class,
        phase,
    )


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (OSError(errno.EMFILE, "Too many open files"), "OSError:EMFILE"),
        (OSError(errno.EADDRNOTAVAIL, "no address"), "OSError:EADDRNOTAVAIL"),
        (socket.gaierror(socket.EAI_NONAME, "Name unknown"), "gaierror"),
    ],
)
def test_decision_a_local_os_errors_are_apparatus_failures(prepared, error, detail):
    exchange, _ = send_with(prepared, error=error)
    assert (exchange.error_class, exchange.error_detail, exchange.error_phase) == (
        "unattributable_error",
        detail,
        "connect",
    )
    record = ex10.attempt_record(prepared["load-request-01"].item, exchange, t0_ns=0)
    assert record.outcome is Outcome.APPARATUS_FAILURE


# --- Decisión B: NOT_DEMONSTRATED prevalece sobre FAIL -------------------------------


def _evaluate_with_async(plan, run, mutate):
    request_ids = accepted_request_ids(plan, run)
    evidence = mutate(async_evidence_for(request_ids), request_ids)
    return ex10.evaluate_run(plan, run, persisted=persisted_for(plan, run), async_evidence=evidence)


def test_decision_b_failed_fact_plus_missing_evidence_is_not_demonstrated(plan):
    def mutate(evidence, ids):
        evidence = with_event(evidence, ids[0], "celery.queued", db_transaction_open=True)
        return without_events(evidence, ids[1], "celery.started")

    evaluation = _evaluate_with_async(plan, synthetic_run(plan), mutate)
    statuses = {f.status for f in evaluation.ht1_04.result.findings}
    assert {Evidence.FACT_FAILED, Evidence.EVIDENCE_MISSING} <= statuses
    assert evaluation.decision is constants.Decision.NOT_DEMONSTRATED
    assert evaluation.result.not_demonstrable == ("HT1-04:EVIDENCE_MISSING",)


def test_decision_b_failed_fact_plus_inconsistency_is_not_demonstrated(plan):
    def mutate(evidence, ids):
        evidence = with_event(evidence, ids[0], "celery.started", process=API)
        created = evidence.facts[ids[1]].report_created_at_utc_ns
        return with_event(evidence, ids[1], "celery.queued", enqueue_at_utc_ns=created - 1)

    evaluation = _evaluate_with_async(plan, synthetic_run(plan), mutate)
    statuses = {f.status for f in evaluation.ht1_04.result.findings}
    assert {Evidence.FACT_FAILED, Evidence.INCONSISTENT} <= statuses
    assert evaluation.decision is constants.Decision.NOT_DEMONSTRATED
    assert evaluation.result.not_demonstrable == ("HT1-04:INCONSISTENT",)


def test_decision_b_several_failed_facts_without_missing_evidence_fail(plan):
    def mutate(evidence, ids):
        evidence = with_event(evidence, ids[0], "celery.queued", db_transaction_open=True)
        evidence = with_event(evidence, ids[1], "celery.started", process=API)
        return with_facts(evidence, ids[2], ai_classification_models=("otro-modelo",))

    evaluation = _evaluate_with_async(plan, synthetic_run(plan), mutate)
    assert {f.status for f in evaluation.ht1_04.result.findings} == {Evidence.FACT_FAILED}
    assert evaluation.decision is constants.Decision.FAIL
    assert evaluation.result.failed_criteria == ("HT1-04",)
    assert evaluation.result.not_demonstrable == ()


def test_decision_b_holds_across_criteria(plan):
    # HT1-01 falla, pero falta la auditoría de otro éxito: no hay FAIL demostrable.
    run = synthetic_run(plan, responses={"load-request-01": ("status", 500, b"")})
    persisted = persisted_for(plan, run)
    persisted["load-request-02"] = replace(
        persisted["load-request-02"], report_created_audit_rows=None
    )
    evaluation = evaluate_complete(plan, run, persisted=persisted)
    assert evaluation.result.failed_criteria == ("HT1-01",)
    assert evaluation.decision is constants.Decision.NOT_DEMONSTRATED

    # HT1-03 falla y HT1-04 es inconsistente en otro intento: tampoco.
    slow = synthetic_run(plan, timings={"load-request-05": (400 * MS, 2_500 * MS)})

    def inconsistent(evidence, ids):
        return with_event(evidence, ids[1], "celery.started", start_at_utc_ns=0)

    evaluation = _evaluate_with_async(plan, slow, inconsistent)
    assert "HT1-03" in evaluation.result.failed_criteria
    assert evaluation.decision is constants.Decision.NOT_DEMONSTRATED

    # Varios criterios fallidos en un run completamente evaluable: FAIL.
    failing = synthetic_run(
        plan,
        responses={"load-request-01": ("status", 500, b"")},
        timings={"load-request-05": (400 * MS, 2_500 * MS)},
    )

    def one_fact(evidence, ids):
        return with_event(evidence, ids[0], "celery.queued", db_transaction_open=True)

    evaluation = _evaluate_with_async(plan, failing, one_fact)
    assert evaluation.decision is constants.Decision.FAIL
    assert evaluation.result.failed_criteria == ("HT1-01", "HT1-03", "HT1-04")


# --- HTTP ≠ 201, conjunto P50/P99 y 201 truncado --------------------------------------


@pytest.mark.parametrize(
    ("status", "error_class"),
    [
        (101, "http_1xx"),
        (200, "http_2xx"),
        (202, "http_2xx"),
        (204, "http_2xx"),
        (302, "http_3xx"),
        (304, "http_3xx"),
        (400, "http_4xx"),
        (429, "http_4xx"),
        (500, "http_5xx"),
        (503, "http_5xx"),
        (600, "http_nonstandard_status"),
    ],
)
def test_any_complete_non_201_response_is_a_valid_request_failure(prepared, status, error_class):
    exchange, _ = send_with(prepared, FakeSocket([http_response(status, "X", b"")]))
    assert (exchange.http_status, exchange.error_class) == (status, None)
    record = ex10.attempt_record(prepared["load-request-01"].item, exchange, t0_ns=0)
    assert (record.outcome, record.error_class, record.http_status) == (
        Outcome.HTTP_REJECTED,
        error_class,
        status,
    )
    assert record.error_detail_sanitized == f"http_status={status}"
    assert ex10.latency_set([record]) == ()


@pytest.mark.parametrize("status", [200, 204, 302, 400, 429, 500])
def test_non_201_statuses_keep_the_run_valid_and_fail_it(plan, prepared, status):
    run = run_generator(plan, prepared, FakeTransport({"load-request-17": ("status", status, b"")}))
    assert run.attempts[16].outcome is Outcome.HTTP_REJECTED
    assert ex10.apparatus_invalidity(plan, run) == []
    evaluation = evaluate_complete(plan, run)
    assert evaluation.decision is constants.Decision.FAIL
    assert evaluation.result.failed_criteria == ("HT1-01",)


def test_interim_100_continue_is_not_the_final_response(prepared):
    body = created_body("load-request-01")
    interim = b"HTTP/1.1 100 Continue\r\n\r\n" + http_response(201, "Created", body)
    exchange, _ = send_with(prepared, FakeSocket([interim]))
    assert (exchange.http_status, exchange.body) == (201, body)


def test_interim_100_continue_then_500_is_a_valid_5xx_failure(dataset, plan, prepared, tokens):
    body = b'{"detail": "Bearer x", "trace": "boom"}'
    final = http_response(500, "Internal Server Error", body)
    head, _, tail = final.partition(b"\r\n\r\n")
    # El cuerpo llega en el último fragmento: sólo se lee al terminar la respuesta final.
    sock = FakeSocket([b"HTTP/1.1 100 Continue\r\n\r\n", head + b"\r\n\r\n", tail[:10], tail[10:]])
    connector = Connector(sock)
    step = StepClock(1_000_000_000)
    readings = []

    def clock():
        readings.append(len(sock.chunks))
        return step()

    request = prepared["load-request-01"]
    transport = ex10.StdlibHttpTransport(("192.0.2.10", 8000), connect=connector)
    exchange = transport.send(request, clock=clock, wall_clock=lambda: T0_UTC)

    # Un único envío, sin reintento y sin `Expect: 100-continue`.
    assert len(connector.calls) == 1
    assert sock.sent == [request.wire]
    assert b"expect:" not in request.wire.partition(b"\r\n\r\n")[0].lower()
    assert sock.closed is True
    # El 100 se consumió como intermedio: la respuesta final observada es el 500.
    assert (exchange.http_status, exchange.body, exchange.error_class) == (500, body, None)
    # `t_start` antes de leer nada y `t_end` tras el último byte del cuerpo final.
    assert (readings[0], readings[-1]) == (4, 0)
    assert exchange.t_end_ns > exchange.t_start_ns

    record = ex10.attempt_record(request.item, exchange, t0_ns=0)
    assert (record.outcome, record.error_class) == (Outcome.HTTP_REJECTED, "http_5xx")
    assert (record.http_status, record.error_detail_sanitized) == (500, "http_status=500")
    assert (record.send_count, record.error_phase) == (1, None)
    assert (record.t_start_ns, record.t_end_ns) == (exchange.t_start_ns, exchange.t_end_ns)
    assert record.duration_ns == exchange.t_end_ns - exchange.t_start_ns
    assert (record.started_in_window, record.completed_in_window) == (True, True)
    assert ex10.latency_set([record]) == ()
    assert record.to_json()["http_status"] == 500

    # De extremo a extremo, el run sigue válido y evaluable, y termina en FAIL.
    interim = b"HTTP/1.1 100 Continue\r\n\r\n" + final
    run, server = run_over_stdlib(plan, prepared, {"load-request-43": interim})
    assert server.connections == {item.request_key: 1 for item in plan.items}
    attempt = run.attempts[42]
    assert (attempt.outcome, attempt.error_class, attempt.http_status) == (
        Outcome.HTTP_REJECTED,
        "http_5xx",
        500,
    )
    assert attempt.send_count == 1
    assert run.faults == ()
    assert ex10.apparatus_invalidity(plan, run) == []
    evaluation = evaluate_complete(plan, run)
    assert evaluation.result.not_demonstrable == ()
    assert evaluation.result.failed_criteria == ("HT1-01",)
    assert evaluation.decision is constants.Decision.FAIL
    raw = ex10.raw_results_payload(plan, run)
    text = canonical_json_bytes(raw).decode()
    for forbidden in ("Continue", "Bearer", "boom", "Internal Server Error", "192.0.2.10"):
        assert forbidden not in text
    assert ex10.sensitive_findings(raw, dataset=dataset, secrets=tokens.values()) == []


def test_non_contractual_201_enters_the_latency_set_in_and_out_of_the_window(plan):
    bad = ("status", 201, b'{"id": "no-contractual"}')
    late_end = ex10.WINDOW_NS + 5 * MS
    run = synthetic_run(
        plan,
        responses={"load-request-10": bad, "load-request-50": bad},
        timings={"load-request-50": (400 * MS, late_end)},
    )
    by_key = {a.request_key: a for a in run.attempts}
    assert by_key["load-request-10"].outcome is Outcome.CONTRACT_VIOLATION
    assert by_key["load-request-50"].outcome is Outcome.CONTRACT_VIOLATION
    assert by_key["load-request-50"].completed_in_window is False
    evaluation = evaluate_complete(plan, run)
    assert evaluation.percentiles.n == 50
    assert late_end - 400 * MS in evaluation.percentiles.durations_ns
    status = {s.request_key: s.status for s in evaluation.successes}
    assert status["load-request-10"] is ex10.SuccessStatus.NOT_SUCCESS
    assert status["load-request-50"] is ex10.SuccessStatus.NOT_SUCCESS
    assert evaluation.throughput.n_ok_window == 48
    assert evaluation.decision is constants.Decision.FAIL


@pytest.mark.parametrize(
    ("recv_error", "error_class"),
    [
        (None, "incomplete_read"),
        (ConnectionResetError(errno.ECONNRESET, "reset"), "connection_reset"),
        (TimeoutError("timed out"), "timeout"),
    ],
)
def test_truncated_201_is_a_transport_failure_outside_the_latency_set(
    plan, prepared, recv_error, error_class
):
    sock = FakeSocket([_truncated_201("load-request-01")], recv_error=recv_error)
    exchange, connector = send_with(prepared, sock)
    assert (exchange.http_status, exchange.error_class) == (201, error_class)
    assert len(connector.calls) == 1 and len(sock.sent) == 1
    item = plan.items[0]
    record = ex10.attempt_record(item, replace(exchange, t_start_ns=T0, t_end_ns=T0 + MS), t0_ns=T0)
    assert record.outcome is Outcome.TRANSPORT_FAILED
    run = synthetic_run(plan)
    run = replace(run, attempts=(record, *run.attempts[1:]))
    assert ex10.apparatus_invalidity(plan, run) == []
    evaluation = evaluate_complete(plan, run)
    assert evaluation.percentiles.n == 49
    assert evaluation.decision is constants.Decision.FAIL


# --- Sensibilidad ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "detail",
    [
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln",
        "token=abc",
        "Bearer abc",
        "Authorization:Bearer",
        "http://192.0.2.10:8000/api/v1/reports/",
        "Connection refused by 192.0.2.10",
        "x" * 200,
    ],
)
def test_error_details_from_any_transport_are_sanitized(plan, detail):
    exchange = exchange_for(plan.items[0], 0, MS, ("error", "timeout", detail))
    record = ex10.attempt_record(plan.items[0], exchange, t0_ns=T0)
    assert record.error_detail_sanitized == "[REDACTED]"


def test_stdlib_error_details_never_keep_exception_messages(prepared, tokens):
    message = f"refused by 192.0.2.10 Bearer {tokens['load-citizen-01']} token=abc"
    exchange, _ = send_with(prepared, error=ConnectionRefusedError(errno.ECONNREFUSED, message))
    assert exchange.error_detail == "ConnectionRefusedError:ECONNREFUSED"


def test_public_reprs_hold_no_secrets(dataset, plan, prepared, tokens):
    run, _ = run_over_stdlib(plan, prepared)
    evaluation = evaluate_complete(plan, run)
    exchange = ex10.Exchange(0, 1, 0, 1, http_status=201, body=b"Bearer secreto")
    generator = ex10.LoadGenerator(plan, prepared, FakeTransport())
    transport = ex10.StdlibHttpTransport(("192.0.2.10", 8000))
    text = " ".join(
        repr(value)
        for value in (dataset, plan, prepared, exchange, generator, transport, run, evaluation)
    )
    assert not any(token in text for token in tokens.values())
    assert not any(request.boundary in text for request in prepared.values())
    for forbidden in ("Bearer", "Authorization", "Content-Disposition", "iVBORw0KGgo", "\\x89PNG"):
        assert forbidden not in text
    for row in dataset.payload["requests"]:
        assert repr(row["latitude"]) not in text and repr(row["longitude"]) not in text


def test_persistable_payloads_hold_no_authorization_or_multipart(
    artifacts, dataset, plan, prepared
):
    failing, _ = run_over_stdlib(
        plan,
        prepared,
        {
            "load-request-02": http_response(
                400, "Bad Request", b'{"photo": ["Bearer x"], "lat": ["-25.29"], "lng": ["x"]}'
            ),
            "load-request-03": OSError(errno.EHOSTUNREACH, "No route to 192.0.2.10"),
        },
    )
    persistable = ("plan", "raw", "calculation", "decision")
    payloads = [*(artifacts[name] for name in persistable), ex10.raw_results_payload(plan, failing)]
    boundaries = [request.boundary for request in prepared.values()]
    for payload in payloads:
        text = canonical_json_bytes(payload).decode()
        for forbidden in ("Authorization", "Bearer", "Content-Disposition", "multipart/form-data"):
            assert forbidden not in text
        assert not any(boundary in text for boundary in boundaries)
        assert "192.0.2.10" not in text
        assert ex10.sensitive_findings(payload, dataset=dataset) == []
