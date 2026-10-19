from types import SimpleNamespace

from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from apps.experiments_app import celery_signals
from apps.experiments_app.context import TraceContext, reset_trace, set_trace
from apps.experiments_app.middleware import HttpInstrumentationMiddleware

RUN_ID = "s11-00000000-0000-5000-8000-000000000001"


@override_settings(S11_EXPERIMENTS_ENABLED=True)
def test_http_records_duration_status_and_safe_endpoint(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        "apps.experiments_app.middleware.record_event", lambda **kwargs: recorded.append(kwargs)
    )
    request = RequestFactory().post(
        "/api/v1/reports/?token=must-not-appear",
        HTTP_X_S11_RUN_ID=RUN_ID,
        HTTP_X_S11_SCENARIO_ID="EX-10",
        HTTP_X_S11_REQUEST_ID="request-01",
    )
    response = HttpInstrumentationMiddleware(lambda req: HttpResponse(status=201))(request)
    assert response.status_code == 201
    event = recorded[0]["event"]
    assert event["endpoint"] == "/api/v1/reports/"
    assert event["http_status"] == 201
    assert event["duration_ms"] >= 0
    assert event["db_duration_ms"] >= 0
    assert event["success"] is True
    assert "token" not in str(event)


@override_settings(S11_EXPERIMENTS_ENABLED=True)
def test_http_ignores_unidentified_request(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        "apps.experiments_app.middleware.record_event", lambda **kwargs: recorded.append(kwargs)
    )
    request = RequestFactory().get("/api/v1/health/")
    HttpInstrumentationMiddleware(lambda req: HttpResponse(status=200))(request)
    assert recorded == []


@override_settings(S11_EXPERIMENTS_ENABLED=True)
def test_celery_records_enqueue_start_finish_and_correlation(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        "apps.experiments_app.celery_signals.record_event", lambda **kwargs: recorded.append(kwargs)
    )
    trace = TraceContext(RUN_ID, "EX-10", "request-02")
    token = set_trace(trace)
    try:
        headers = {"id": "task-01"}
        celery_signals.before_task_publish(sender="ai.classify_report", headers=headers)
    finally:
        reset_trace(token)
    assert headers["s11_run_id"] == RUN_ID
    assert headers["s11_request_id"] == "request-02"

    task = SimpleNamespace(name="ai.classify_report", request=headers)
    celery_signals.task_prerun(task_id="task-01", task=task)
    celery_signals.task_postrun(task_id="task-01", task=task, state="SUCCESS")
    events = [item["event"]["event"] for item in recorded]
    assert events == ["celery.queued", "celery.started", "celery.finished"]
    assert all(item["event"]["request_id"] == "request-02" for item in recorded)
    assert recorded[-1]["event"]["status"] == "finished"
    assert recorded[-1]["event"]["celery_state"] == "success"
    assert recorded[-1]["event"]["duration_ms"] >= 0


@override_settings(S11_EXPERIMENTS_ENABLED=True)
def test_celery_failure_is_not_hidden(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        "apps.experiments_app.celery_signals.record_event", lambda **kwargs: recorded.append(kwargs)
    )
    request = {
        "s11_run_id": RUN_ID,
        "s11_scenario_id": "EX-02",
        "s11_request_id": "request-failure",
    }
    task = SimpleNamespace(name="ai.classify_report", request=request)
    celery_signals.task_prerun(task_id="task-failure", task=task)
    celery_signals.task_failure(task_id="task-failure", sender=task)
    assert recorded[-1]["event"]["event"] == "celery.failed"
    assert recorded[-1]["event"]["status"] == "failed"
