"""EX-10 fase 2A: telemetría para HT1-04. LOCAL TEST / NON-EXPERIMENTAL.

Ninguna prueba usa el stack S11, `var/s11-runs`, `s11-load-v1` ni un run oficial. Los eventos
van a un almacén en memoria, salvo una prueba con el `EvidenceStore` real en
`.s11-test-artifacts/`. Las publicaciones Celery se simulan enviando la señal
`before_task_publish`, que es lo que hace Celery al publicar; ninguna llega a un broker.
"""

import json
import multiprocessing
import os
import shutil
import socket
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from celery import signals
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.test import RequestFactory
from django.test.client import BOUNDARY
from django.urls import path

from apps.experiments_app import celery_signals, events
from apps.experiments_app.canonical import canonical_json_bytes
from apps.experiments_app.context import TraceContext, reset_trace, set_trace
from apps.experiments_app.custody_policy import publication_public_key_from_private
from apps.experiments_app.evidence import EvidenceStore
from apps.experiments_app.management.commands.s11_validate_run import Command as ValidateRun
from apps.experiments_app.middleware import HttpInstrumentationMiddleware

RUN_ID = "s11-00000000-0000-5000-8000-0000000000e1"
SCENARIO = "EX-10"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJsb2NhbC10ZXN0In0.TE9DQUwtVEVTVC1TSUdOQVRVUkU"
PNG = b"\x89PNG\r\n\x1a\n" + b"LOCAL-TEST-IMAGE" * 8
LAT, LNG = "-25.2867003", "-57.3333112"
API = {"hostname": socket.gethostname(), "pid": os.getpid()}


class FakeStore:
    """Almacén en memoria con la firma de `EvidenceStore.append_jsonl`."""

    def __init__(self):
        self.lock = threading.Lock()
        self.lines = []
        self.calls = 0
        self.error = None

    def append_jsonl(self, *, run_id, scenario_id, stream, payload):
        with self.lock:
            self.calls += 1
            if self.error is not None:
                raise self.error
            self.lines.append({"run_id": run_id, "stream": stream, "payload": payload})

    def events(self):
        return [(line["payload"]["event"], line["payload"]["request_id"]) for line in self.lines]

    def payloads(self, name):
        return [line["payload"] for line in self.lines if line["payload"]["event"] == name]


class FakeConnection:
    def __init__(self, in_atomic_block=False, autocommit=True):
        self.in_atomic_block = in_atomic_block
        self.autocommit = autocommit

    def get_autocommit(self):
        return self.autocommit


@pytest.fixture
def enabled_store(settings, monkeypatch):
    settings.S11_EXPERIMENTS_ENABLED = True
    store = FakeStore()
    monkeypatch.setattr(events.runtime, "evidence_store", lambda **kwargs: store)
    return store


@pytest.fixture
def store(enabled_store, monkeypatch):
    # Sin base: el estado transaccional real se prueba aparte, con `transactional_db`.
    monkeypatch.setattr(celery_signals, "connection", FakeConnection())
    return enabled_store


def s11_headers(request_id):
    return {
        "HTTP_X_S11_RUN_ID": RUN_ID,
        "HTTP_X_S11_SCENARIO_ID": SCENARIO,
        "HTTP_X_S11_REQUEST_ID": request_id,
    }


def call(view, request_id="req-01", **extra):
    request = RequestFactory().post("/api/v1/reports/", **s11_headers(request_id), **extra)
    return HttpInstrumentationMiddleware(view)(request)


def publish(task_id, name="ai.classify_report"):
    headers = {"id": task_id}
    signals.before_task_publish.send(sender=name, headers=headers)
    return headers


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@contextmanager
def traced(request_id):
    token = set_trace(TraceContext(RUN_ID, SCENARIO, request_id))
    try:
        yield
    finally:
        reset_trace(token)


def publishing_view(*task_ids):
    def view(request):
        for task_id in task_ids:
            publish(task_id)
        return HttpResponse(b'{"ok": true}', status=201, content_type="application/json")

    return view


# --- Vistas para la pila completa de middlewares ---------------------------------------


def _publish_view(request):
    publish(f"task-{request.META['HTTP_X_S11_REQUEST_ID']}")
    return HttpResponse(b'{"ok": true}', status=201, content_type="application/json")


def _stream_view(request):
    return StreamingHttpResponse(iter([b"part-1;", b"part-2;", b"part-3"]))


urlpatterns = [
    path("s11-telemetry/publish/", _publish_view),
    path("s11-telemetry/stream/", _stream_view),
]


class ProbeMiddleware:
    """Middleware exterior: mide qué se persistió cuando corre su `process_response`."""

    seen: list[int] = []

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        ProbeMiddleware.seen.append(len(events.runtime.evidence_store().lines))
        return response


# --- Proceso -------------------------------------------------------------------------


def test_api_events_carry_the_server_process_and_ignore_client_values(store):
    view = publishing_view("task-process")
    call(view, "req-process", HTTP_X_S11_PROCESS="spoofed", HTTP_X_S11_PID="1").close()
    assert [line["payload"]["process"] for line in store.lines] == [API, API]
    assert "spoofed" not in json.dumps(store.lines)


def test_invalid_hostname_leaves_the_process_unknown(monkeypatch):
    monkeypatch.setattr(events.socket, "gethostname", lambda: "bad host;rm")
    assert events.process_identity() is None
    monkeypatch.setattr(events.socket, "gethostname", lambda: "9cc4ca932ad2")
    assert events.process_identity() == {"hostname": "9cc4ca932ad2", "pid": os.getpid()}


class QueueStore:
    def __init__(self, queue):
        self.queue = queue

    def append_jsonl(self, *, run_id, scenario_id, stream, payload):
        self.queue.put(("event", payload))


def _run_worker_signals(queue, headers):
    task = SimpleNamespace(name="ai.classify_report", request=headers)
    celery_signals.task_prerun(task_id="task-worker", task=task)
    celery_signals.task_postrun(task_id="task-worker", task=task, state="SUCCESS")
    queue.put(("pid", os.getpid()))


def test_worker_events_carry_the_worker_process_not_the_api(settings, monkeypatch):
    settings.S11_EXPERIMENTS_ENABLED = True
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    monkeypatch.setattr(events.runtime, "evidence_store", lambda **kwargs: QueueStore(queue))
    headers = {"s11_run_id": RUN_ID, "s11_scenario_id": SCENARIO, "s11_request_id": "req-w"}
    worker = context.Process(target=_run_worker_signals, args=(queue, headers))
    worker.start()
    received = [queue.get(timeout=30) for _ in range(3)]
    worker.join(30)
    assert worker.exitcode == 0
    child_pid = next(value for kind, value in received if kind == "pid")
    payloads = [value for kind, value in received if kind == "event"]
    assert [p["event"] for p in payloads] == ["celery.started", "celery.finished"]
    assert child_pid != os.getpid()
    assert all(p["process"] == {"hostname": API["hostname"], "pid": child_pid} for p in payloads)
    assert all(p["request_id"] == "req-w" and p["task_id"] == "task-worker" for p in payloads)


# --- db_transaction_open ----------------------------------------------------------------


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_db_transaction_open_reflects_atomic_blocks_and_on_commit(enabled_store):
    with traced("req-db"):
        publish("task-outside")
        with transaction.atomic():
            publish("task-inside")
            transaction.on_commit(lambda: publish("task-on-commit"))
    queued = enabled_store.payloads("celery.queued")
    state = {p["task_id"]: p["db_transaction_open"] for p in queued}
    assert state == {"task-outside": False, "task-inside": True, "task-on-commit": False}


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_db_transaction_open_is_true_without_autocommit(enabled_store):
    with traced("req-autocommit"):
        transaction.set_autocommit(False)
        try:
            publish("task-manual")
        finally:
            transaction.rollback()
            transaction.set_autocommit(True)
    assert enabled_store.payloads("celery.queued")[0]["db_transaction_open"] is True


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_db_transaction_open_is_captured_at_publish_not_at_flush(enabled_store):
    def view(request):
        with transaction.atomic():
            publish("task-atomic")
        return HttpResponse(status=201)

    response = call(view, "req-captured")
    assert enabled_store.lines == []
    response.close()  # la transacción ya se cerró; el valor no se recalcula
    assert enabled_store.payloads("celery.queued")[0]["db_transaction_open"] is True


def test_unreadable_transaction_state_is_unknown_not_closed(store, monkeypatch):
    class Broken:
        in_atomic_block = False

        def get_autocommit(self):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(celery_signals, "connection", Broken())
    with traced("req-unknown"):
        publish("task-unknown")
    assert store.payloads("celery.queued")[0]["db_transaction_open"] is None


def test_celery_queued_carries_the_frozen_correlation_fields(store):
    call(publishing_view("task-fields"), "req-fields").close()
    queued = store.payloads("celery.queued")[0]
    assert {k: queued[k] for k in ("run_id", "scenario_id", "request_id", "task_id")} == {
        "run_id": RUN_ID,
        "scenario_id": SCENARIO,
        "request_id": "req-fields",
        "task_id": "task-fields",
    }
    assert queued["task_name"] == "ai.classify_report"
    assert queued["enqueue_at"].endswith("Z")
    assert (queued["process"], queued["db_transaction_open"]) == (API, False)


def test_http_finished_keeps_the_historical_endpoint_without_query(store):
    request = RequestFactory().post(
        "/api/v1/reports/?token=must-not-appear&lat=1", **s11_headers("req-endpoint")
    )
    HttpInstrumentationMiddleware(publishing_view())(request).close()
    (finished,) = store.payloads("http.finished")
    assert (finished["endpoint"], finished["method"]) == ("/api/v1/reports/", "POST")
    assert "normalized_path" not in finished and "must-not-appear" not in json.dumps(finished)


# --- Persistencia al cerrar la respuesta ----------------------------------------------


def test_request_events_are_persisted_only_after_the_response_close(store):
    during = []

    def view(request):
        publish("task-close")
        during.append(len(store.lines))
        return HttpResponse(status=201)

    response = call(view, "req-close")
    assert during == [0]  # celery.queued no se escribe dentro del request
    assert store.lines == []  # ni http.finished al devolver la respuesta
    response.close()
    assert store.events() == [("celery.queued", "req-close"), ("http.finished", "req-close")]


@pytest.mark.urls(__name__)
def test_nothing_is_persisted_when_outer_middlewares_process_the_response(store, settings, client):
    ProbeMiddleware.seen = []
    settings.MIDDLEWARE = [f"{__name__}.ProbeMiddleware", *settings.MIDDLEWARE]
    response = client.post("/s11-telemetry/publish/", **s11_headers("req-stack"))
    assert response.status_code == 201
    assert ProbeMiddleware.seen == [0]  # `process_response` no es el fin de la respuesta
    assert store.events() == [("celery.queued", "req-stack"), ("http.finished", "req-stack")]


def test_flush_happens_exactly_once(store):
    response = call(publishing_view("task-once"), "req-once")
    response.close()
    response.close()
    response.close()
    assert store.events() == [("celery.queued", "req-once"), ("http.finished", "req-once")]
    assert store.calls == 2


def test_existing_closers_are_preserved(store, tmp_path):
    source = tmp_path / "download.bin"
    source.write_bytes(b"synthetic")
    handle = source.open("rb")
    chained = []

    def view(request):
        response = FileResponse(handle)
        previous = response.close

        def close():
            chained.append("previous-close")
            previous()

        response.close = close
        return response

    response = call(view, "req-closers")
    response.close()
    assert chained == ["previous-close"]
    assert handle.closed is True
    assert store.events() == [("http.finished", "req-closers")]


def test_an_exception_from_the_original_close_propagates_after_flushing(store):
    def view(request):
        publish("task-raising-close")
        response = HttpResponse(status=201)

        def close():
            raise RuntimeError("closer failed")

        response.close = close
        return response

    response = call(view, "req-raising-close")
    with pytest.raises(RuntimeError, match="closer failed"):
        response.close()
    assert store.events() == [
        ("celery.queued", "req-raising-close"),
        ("http.finished", "req-raising-close"),
    ]


def test_an_exception_without_response_flushes_immediately(store):
    def view(request):
        publish("task-boom")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        call(view, "req-boom")
    assert store.events() == [("celery.queued", "req-boom"), ("http.finished", "req-boom")]
    assert store.payloads("http.finished")[0]["http_status"] == 500
    assert events.active_buffer() is None


def test_flush_order_is_the_capture_order(store):
    call(publishing_view("task-a", "task-b", "task-c"), "req-order").close()
    assert [(p["event"], p.get("task_id")) for p in (x["payload"] for x in store.lines)] == [
        ("celery.queued", "task-a"),
        ("celery.queued", "task-b"),
        ("celery.queued", "task-c"),
        ("http.finished", None),
    ]


def test_flush_keeps_the_capture_timestamps(store):
    response = call(publishing_view("task-time"), "req-time")
    time.sleep(0.05)
    flush_started = datetime.now(UTC)
    response.close()
    queued, finished = store.payloads("celery.queued")[0], store.payloads("http.finished")[0]
    for value in (queued["recorded_at"], queued["enqueue_at"], finished["recorded_at"]):
        assert utc(value) < flush_started
    assert utc(finished["recorded_at"]) >= utc(finished["finished_at"]) >= utc(queued["enqueue_at"])


@pytest.mark.urls(__name__)
def test_streaming_responses_flush_after_the_last_chunk(store, client):
    response = client.get("/s11-telemetry/stream/", **s11_headers("req-stream"))
    chunks = iter(response.streaming_content)
    assert next(chunks) == b"part-1;"
    assert store.lines == []  # a mitad del cuerpo no se escribe nada
    assert b"".join(chunks) == b"part-2;part-3"
    assert store.events() == [("http.finished", "req-stream")]


def test_middleware_does_not_change_status_headers_or_body(store):
    def view(request):
        response = HttpResponse(b'{"id": 1}', status=201, content_type="application/json")
        response["X-Test"] = "value"
        return response

    def streaming(request):
        return StreamingHttpResponse(iter([b"a", b"b"]), status=202)

    plain = view(RequestFactory().post("/api/v1/reports/"))
    instrumented = call(view, "req-same")
    assert (instrumented.status_code, list(instrumented.items()), instrumented.content) == (
        plain.status_code,
        list(plain.items()),
        plain.content,
    )
    instrumented.close()
    stream = call(streaming, "req-same-stream")
    assert (stream.status_code, b"".join(stream.streaming_content)) == (202, b"ab")
    stream.close()


# --- Aislamiento --------------------------------------------------------------------------


def test_requests_without_s11_headers_create_no_buffer(store):
    seen = []

    def view(request):
        seen.append(events.active_buffer())
        publish("task-anonymous")
        return HttpResponse()

    request = RequestFactory().post("/api/v1/reports/")
    response = HttpInstrumentationMiddleware(view)(request)
    response.close()
    assert seen == [None]
    assert "close" not in vars(response)
    assert store.lines == []


def test_events_of_one_request_never_reappear_in_the_next(store):
    call(publishing_view("task-first"), "req-first").close()
    assert events.active_buffer() is None
    call(publishing_view("task-second"), "req-second").close()
    assert store.events() == [
        ("celery.queued", "req-first"),
        ("http.finished", "req-first"),
        ("celery.queued", "req-second"),
        ("http.finished", "req-second"),
    ]


def test_a_thread_started_inside_a_request_does_not_share_its_buffer(store):
    seen = {}

    def other_thread():
        seen["buffer"] = events.active_buffer()
        with traced("req-other-thread"):
            publish("task-other-thread")

    def view(request):
        publish("task-owner")
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join(5)
        seen["during"] = store.events()
        return HttpResponse()

    call(view, "req-owner").close()
    assert seen["buffer"] is None
    assert seen["during"] == [("celery.queued", "req-other-thread")]  # sin buffer: en el acto
    assert store.events() == [
        ("celery.queued", "req-other-thread"),
        ("celery.queued", "req-owner"),
        ("http.finished", "req-owner"),
    ]


@pytest.mark.parametrize("repeat", range(20))
def test_concurrent_requests_never_mix_buffers(store, repeat):
    count = 8
    barrier = threading.Barrier(count, timeout=10)
    responses, errors = {}, []

    def view(request):
        request_id = request.META["HTTP_X_S11_REQUEST_ID"]
        publish(f"{request_id}-a")
        barrier.wait()  # los ocho requests tienen su buffer abierto a la vez
        publish(f"{request_id}-b")
        return HttpResponse(request_id.encode())

    def client(number):
        request_id = f"req-{number:02d}"
        try:
            responses[request_id] = call(view, request_id)
            assert events.active_buffer() is None
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=client, args=(n,)) for n in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert errors == [] and len(responses) == count
    assert store.lines == []
    for request_id in sorted(responses, reverse=True):
        start = len(store.lines)
        responses[request_id].close()
        flushed = [line["payload"] for line in store.lines[start:]]
        assert [(p["event"], p.get("task_id")) for p in flushed] == [
            ("celery.queued", f"{request_id}-a"),
            ("celery.queued", f"{request_id}-b"),
            ("http.finished", None),
        ]
        assert {p["request_id"] for p in flushed} == {request_id}


def test_celery_queued_outside_a_request_is_persisted_immediately(store):
    with traced("req-no-http"):
        publish("task-no-http")
    assert store.events() == [("celery.queued", "req-no-http")]


# --- Contenido sensible ---------------------------------------------------------------------


def test_buffered_payloads_hold_no_token_authorization_multipart_image_or_coordinates(store):
    reprs = []

    def view(request):
        publish("task-sensitive")
        reprs.append(repr(events.active_buffer()))
        return HttpResponse(status=201)

    photo = SimpleUploadedFile("photo.png", PNG, content_type="image/png")
    response = call(
        view,
        "req-sensitive",
        data={"lat": LAT, "lng": LNG, "description": "LOCAL-TEST", "photo": photo},
        HTTP_AUTHORIZATION=f"Bearer {JWT}",
        HTTP_COOKIE="sessionid=local-test-session-secret",
    )
    response.close()
    text = canonical_json_bytes([line["payload"] for line in store.lines]).decode()
    text += " ".join(reprs)
    for forbidden in (
        JWT,
        "eyJ",
        "Bearer",
        "Authorization",
        "local-test-session-secret",
        "multipart",
        BOUNDARY,
        "Content-Disposition",
        "\\x89PNG",
        "\x89PNG",
        "LOCAL-TEST-IMAGE",
        "iVBORw0KGgo",
        LAT,
        LNG,
        "LOCAL-TEST",
    ):
        assert forbidden not in text, forbidden
    assert reprs == ["RequestEventBuffer(pending=1, flushed=False)"]


def test_persistence_failure_logs_a_sanitized_error_without_retry(store, caplog):
    store.error = OSError(
        f"write failed: /s11-evidence/runs/{RUN_ID}/logs/EX-10/http.jsonl Bearer {JWT}"
    )
    response = call(publishing_view("task-lost"), "req-lost")
    with caplog.at_level("ERROR", logger="apps.experiments_app.events"):
        response.close()
    assert response.status_code == 201
    assert store.calls == 2  # un intento por evento, sin reintentos
    assert store.lines == []
    errors = [r for r in caplog.records if r.name == "apps.experiments_app.events"]
    assert [r.levelname for r in errors] == ["ERROR", "ERROR"]
    messages = [r.getMessage() for r in errors]
    assert messages[0] == (
        f"{events.PERSIST_FAILED} event=celery.queued stream=celery run_id={RUN_ID} "
        "scenario_id=EX-10 request_id=req-lost task_id=task-lost error=OSError"
    )
    assert messages[1].startswith(f"{events.PERSIST_FAILED} event=http.finished stream=http")
    for record, message in zip(errors, messages, strict=True):
        assert record.exc_info is None
        assert "/s11-evidence" not in message and "Bearer" not in message and JWT not in message


# --- Worker ----------------------------------------------------------------------------------


def test_worker_events_are_persisted_immediately_even_inside_a_request(store):
    # Como en eager: la tarea corre dentro del request del API y su evidencia muestra ese
    # proceso, sin inventar un worker. Es compatibilidad técnica, no asincronía.
    buffer = events.RequestEventBuffer()
    token = events.activate_buffer(buffer)
    try:
        headers = {"s11_run_id": RUN_ID, "s11_scenario_id": SCENARIO, "s11_request_id": "req-e"}
        task = SimpleNamespace(name="ai.classify_report", request=headers)
        celery_signals.task_prerun(task_id="task-eager", task=task)
        assert store.events() == [("celery.started", "req-e")]
        celery_signals.task_failure(task_id="task-eager", sender=task)
    finally:
        events.deactivate_buffer(token)
    assert len(buffer) == 0
    assert store.events() == [("celery.started", "req-e"), ("celery.failed", "req-e")]
    failed = store.payloads("celery.failed")[0]
    assert (failed["task_id"], failed["status"], failed["process"]) == ("task-eager", "failed", API)
    assert utc(failed["start_at"]) <= utc(failed["finish_at"])


def test_worker_events_without_correlation_are_not_invented(store):
    task = SimpleNamespace(name="ai.classify_report", request={"s11_run_id": RUN_ID})
    celery_signals.task_prerun(task_id="task-orphan", task=task)
    celery_signals.task_postrun(task_id="task-orphan", task=task, state="SUCCESS")
    assert store.lines == []


# --- Compatibilidad --------------------------------------------------------------------------


@pytest.fixture
def evidence_root(request):
    root = Path.cwd() / ".s11-test-artifacts" / f"telemetry-{request.node.name}"
    shutil.rmtree(root, ignore_errors=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def test_new_fields_go_through_the_real_evidence_store_and_old_events_still_parse(
    settings, monkeypatch, evidence_root
):
    settings.S11_EXPERIMENTS_ENABLED = True
    store = EvidenceStore(
        evidence_root,
        custody_hmac_key=b"synthetic-telemetry-test-key-32-bytes-local",
        publication_verification_key=publication_public_key_from_private(bytes(range(32))),
    )
    monkeypatch.setattr(events.runtime, "evidence_store", lambda **kwargs: store)
    monkeypatch.setattr(celery_signals, "connection", FakeConnection())
    legacy = {
        "schema_version": "s11-technical-event-v1",
        "run_id": RUN_ID,
        "scenario_id": SCENARIO,
        "event": "celery.queued",
        "request_id": "req-legacy",
        "task_id": "task-legacy",
        "status": "queued",
    }
    store.append_jsonl(run_id=RUN_ID, scenario_id=SCENARIO, stream="celery", payload=legacy)
    call(publishing_view("task-real"), "req-real").close()

    def read(stream):
        log = next(evidence_root.rglob(f"{stream}.jsonl"))
        return [json.loads(line) for line in log.read_text().splitlines()]

    old, new = read("celery")
    assert old.get("process") is None and "db_transaction_open" not in old
    assert (new["process"], new["db_transaction_open"]) == (API, False)
    (http,) = read("http")
    assert http["process"] == API
    legacy_http = {k: v for k, v in http.items() if k != "process"}
    errors = []
    ValidateRun._validate_http([legacy_http, http], errors)
    lifecycle = [
        {"event": name, "task_id": "task-legacy"}
        for name in ("celery.queued", "celery.started", "celery.finished")
    ]
    ValidateRun._validate_celery([*lifecycle, {**lifecycle[0], "process": API}], errors)
    assert errors == []
