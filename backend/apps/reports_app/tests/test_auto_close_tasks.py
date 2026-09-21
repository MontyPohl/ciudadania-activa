import contextlib
import logging
from datetime import timedelta

import pytest
from celery.exceptions import Retry
from django.db import OperationalError
from django.utils import timezone

from apps.admin_app.models import AuditLog
from apps.core.enums import ReportStatus
from apps.reports_app import tasks
from apps.reports_app.models import ResolutionEvidence
from apps.reports_app.services import lifecycle, validation
from apps.rewards_app.services import ledger
from config.celery import app

pytestmark = pytest.mark.django_db


@contextlib.contextmanager
def _worker_context(monkeypatch, retries=0):
    scheduled = []

    def retry(*, exc, countdown, max_retries):
        scheduled.append((exc, countdown, max_retries))
        raise Retry(exc=exc)

    tasks.auto_close_reports.push_request(retries=retries)
    monkeypatch.setattr(tasks.auto_close_reports, "retry", retry)
    try:
        yield scheduled
    finally:
        tasks.auto_close_reports.pop_request()


def _resolved(report, operator_user, institution):
    import uuid

    ledger.on_report_created(report)
    validation.validate_report(
        report_id=report.pk,
        operator=operator_user,
        institution=institution,
    )
    report.refresh_from_db()
    lifecycle.transition_report_status(
        report_id=report.pk,
        operator_id=operator_user.pk,
        institution_id=institution.pk,
        target_status=ReportStatus.EN_PROCESO,
        expected_cycle=1,
    )
    ResolutionEvidence.objects.create(
        report=report,
        description="Trabajo concluido.",
        evidence_url=f"resolution-evidence/{report.pk}/1/{uuid.uuid4()}.jpg",
        uploaded_by=operator_user,
        resolution_cycle=1,
        idempotency_key=uuid.uuid4(),
        content_hash="e" * 64,
    )
    lifecycle.transition_report_status(
        report_id=report.pk,
        operator_id=operator_user.pk,
        institution_id=institution.pk,
        target_status=ReportStatus.RESUELTO,
        expected_cycle=1,
    )
    report.refresh_from_db()
    report.resolved_at = timezone.now() - timedelta(hours=73)
    report.save(update_fields=["resolved_at", "updated_at"])


def test_task_is_registered_and_beat_runs_every_fifteen_minutes(settings):
    app.autodiscover_tasks(lambda: ["apps.reports_app"], force=True)

    assert app.tasks["reports.auto_close"].name == "reports.auto_close"
    schedule = settings.CELERY_BEAT_SCHEDULE["reports-auto-close-every-15-minutes"]
    assert schedule["task"] == "reports.auto_close"
    assert schedule["schedule"].minute == {0, 15, 30, 45}
    assert settings.REPORT_AUTO_CLOSE_HOURS == 72
    assert settings.USE_TZ is True
    assert settings.TIME_ZONE == "America/Asuncion"


def test_task_closes_eligible_report_once(report, operator_user, institution):
    _resolved(report, operator_user, institution)

    assert tasks.auto_close_reports() == 1
    assert tasks.auto_close_reports() == 0

    report.refresh_from_db()
    assert report.status == ReportStatus.CERRADO
    assert AuditLog.objects.filter(action="report.closed").count() == 1


@pytest.mark.parametrize("retries", [0, 1, 2])
def test_transient_database_error_retries_with_exponential_backoff_and_jitter(
    retries,
    monkeypatch,
):
    error = OperationalError("detalle sensible de conexión")
    monkeypatch.setattr(
        tasks.auto_close,
        "eligible_report_ids",
        lambda: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(tasks.random, "uniform", lambda start, end: end)

    with _worker_context(monkeypatch, retries=retries) as scheduled, pytest.raises(Retry):
        tasks.auto_close_reports.run()

    assert scheduled == [(error, 2 * (2**retries), 3)]


def test_fourth_transient_failure_stops_retrying(monkeypatch, caplog):
    error = OperationalError("detalle sensible de conexión")
    monkeypatch.setattr(
        tasks.auto_close,
        "eligible_report_ids",
        lambda: (_ for _ in ()).throw(error),
    )

    with caplog.at_level(logging.WARNING), _worker_context(monkeypatch, retries=3) as scheduled:
        assert tasks.auto_close_reports.run() == 0

    assert scheduled == []
    assert "agotado tras 3 reintentos" in caplog.text
    assert "detalle sensible" not in caplog.text


def test_permanent_error_is_redacted_and_later_sweep_can_retry(
    report,
    operator_user,
    institution,
    monkeypatch,
    caplog,
):
    _resolved(report, operator_user, institution)
    real_close = tasks.auto_close.close_report

    def fail_once(*args, **kwargs):
        monkeypatch.setattr(tasks.auto_close, "close_report", real_close)
        raise ValueError("descripción privada que no debe registrarse")

    monkeypatch.setattr(tasks.auto_close, "close_report", fail_once)
    with caplog.at_level(logging.ERROR):
        assert tasks.auto_close_reports() == 0

    report.refresh_from_db()
    assert report.status == ReportStatus.RESUELTO
    assert "ValueError" in caplog.text
    assert str(report.pk) in caplog.text
    assert "descripción privada" not in caplog.text

    assert tasks.auto_close_reports() == 1
    report.refresh_from_db()
    assert report.status == ReportStatus.CERRADO


def test_retry_countdown_has_bounded_jitter(monkeypatch):
    monkeypatch.setattr(tasks.random, "uniform", lambda start, end: start)
    assert [tasks.compute_retry_countdown(index) for index in range(3)] == [1, 2, 4]
    monkeypatch.setattr(tasks.random, "uniform", lambda start, end: end)
    assert [tasks.compute_retry_countdown(index) for index in range(3)] == [2, 4, 8]
