import uuid
from datetime import UTC, datetime, timedelta

import pytest

from apps.admin_app.models import AuditLog
from apps.core.enums import ActorType, ReportStatus
from apps.notifications_app.models import Notification
from apps.reports_app.models import ReportStatusHistory, ResolutionEvidence
from apps.reports_app.services import auto_close, lifecycle, reversal, validation
from apps.rewards_app.enums import RewardEventType
from apps.rewards_app.models import RewardTransaction
from apps.rewards_app.services import ledger

pytestmark = pytest.mark.django_db

OPERATION_AT = datetime(2026, 9, 11, 15, 0, tzinfo=UTC)


def _transition(report, operator_user, institution, target):
    result = lifecycle.transition_report_status(
        report_id=report.pk,
        operator_id=operator_user.pk,
        institution_id=institution.pk,
        target_status=target,
        expected_cycle=report.resolution_cycle,
    )
    report.refresh_from_db()
    return result


def _resolved(report, operator_user, institution):
    ledger.on_report_created(report)
    validation.validate_report(
        report_id=report.pk,
        operator=operator_user,
        institution=institution,
    )
    report.refresh_from_db()
    _transition(report, operator_user, institution, ReportStatus.EN_PROCESO)
    ResolutionEvidence.objects.create(
        report=report,
        description="Trabajo concluido.",
        evidence_url=f"resolution-evidence/{report.pk}/1/{uuid.uuid4()}.jpg",
        uploaded_by=operator_user,
        resolution_cycle=1,
        idempotency_key=uuid.uuid4(),
        content_hash="d" * 64,
    )
    _transition(report, operator_user, institution, ReportStatus.RESUELTO)
    return report


def _age_resolution(report, *, delta):
    report.resolved_at = OPERATION_AT - delta
    report.save(update_fields=["resolved_at", "updated_at"])


def test_exact_cutoff_closes_with_system_history_audit_and_no_side_effects(
    report,
    operator_user,
    institution,
    django_capture_on_commit_callbacks,
    monkeypatch,
):
    _resolved(report, operator_user, institution)
    _age_resolution(report, delta=timedelta(hours=72))
    reward_count = RewardTransaction.objects.count()
    # El armado valida y resuelve por servicio, que desde S13 avisan por correo; el cierre
    # automático no debe agregar ningún aviso.
    avisos_previos = Notification.objects.count()
    invalidations = []
    monkeypatch.setattr(
        auto_close,
        "invalidate_public_reports_cache",
        lambda: invalidations.append("called"),
    )

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        result = auto_close.close_report(report_id=report.pk, operation_at=OPERATION_AT)

    report.refresh_from_db()
    history = ReportStatusHistory.objects.get(new_status=ReportStatus.CERRADO)
    entry = AuditLog.objects.get(action="report.closed")
    assert result.changed is True
    assert report.status == ReportStatus.CERRADO
    assert report.closed_at == OPERATION_AT
    assert history.previous_status == ReportStatus.RESUELTO
    assert history.changed_by is None
    assert history.resolution_cycle == 1
    assert entry.actor_user is None
    assert entry.actor_type == ActorType.SYSTEM
    assert entry.payload == {
        "report_id": str(report.pk),
        "institution_id": str(institution.pk),
        "cycle": 1,
        "previous_status": ReportStatus.RESUELTO,
        "new_status": ReportStatus.CERRADO,
        "closure_mode": "automatic",
    }
    assert RewardTransaction.objects.count() == reward_count
    assert (
        RewardTransaction.objects.filter(event_type=RewardEventType.RESOLUTION_BONUS).count() == 1
    )
    assert Notification.objects.count() == avisos_previos
    assert invalidations == []
    assert len(callbacks) == 1
    callbacks[0]()
    assert invalidations == ["called"]


def test_never_closes_before_72_elapsed_hours(report, operator_user, institution):
    _resolved(report, operator_user, institution)
    _age_resolution(report, delta=timedelta(hours=72) - timedelta(microseconds=1))
    counts = (ReportStatusHistory.objects.count(), AuditLog.objects.count())

    result = auto_close.close_report(report_id=report.pk, operation_at=OPERATION_AT)

    report.refresh_from_db()
    assert result.changed is False
    assert report.status == ReportStatus.RESUELTO
    assert report.closed_at is None
    assert (ReportStatusHistory.objects.count(), AuditLog.objects.count()) == counts


@pytest.mark.parametrize("winner", ["manual", "reversal"])
def test_manual_close_or_reversal_winner_makes_automatic_close_a_noop(
    winner,
    report,
    operator_user,
    institution,
    admin_user,
):
    _resolved(report, operator_user, institution)
    _age_resolution(report, delta=timedelta(hours=73))
    if winner == "manual":
        _transition(report, operator_user, institution, ReportStatus.CERRADO)
    else:
        reversal.revert_report_validation(
            report_id=report.pk,
            admin_id=admin_user.pk,
            expected_cycle=1,
            reason="Corrección administrativa fundada.",
        )
        report.refresh_from_db()
    counts = (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    )

    result = auto_close.close_report(report_id=report.pk, operation_at=OPERATION_AT)

    assert result.changed is False
    assert (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    ) == counts


def test_missing_report_is_idempotent_noop():
    result = auto_close.close_report(report_id=uuid.uuid4(), operation_at=OPERATION_AT)

    assert result.report is None
    assert result.changed is False


def test_resolved_cycle_zero_snapshot_fails_closed_without_writes(
    report,
    institution,
):
    report.status = ReportStatus.RESUELTO
    report.institution = institution
    report.resolved_at = OPERATION_AT - timedelta(hours=73)
    report.save(update_fields=["status", "institution", "resolved_at", "updated_at"])

    with pytest.raises(auto_close.AutoCloseProjectionError):
        auto_close.close_report(report_id=report.pk, operation_at=OPERATION_AT)

    report.refresh_from_db()
    assert report.status == ReportStatus.RESUELTO
    assert report.closed_at is None
    assert ReportStatusHistory.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_nonpositive_configuration_fails_closed(report, settings):
    settings.REPORT_AUTO_CLOSE_HOURS = 0

    with pytest.raises(auto_close.AutoCloseConfigurationError):
        auto_close.close_report(report_id=report.pk, operation_at=OPERATION_AT)

    report.refresh_from_db()
    assert report.status == ReportStatus.CREADO


def test_transaction_rolls_back_if_audit_write_fails(
    report,
    operator_user,
    institution,
    monkeypatch,
):
    _resolved(report, operator_user, institution)
    _age_resolution(report, delta=timedelta(hours=73))
    counts = (ReportStatusHistory.objects.count(), AuditLog.objects.count())

    def fail(*args, **kwargs):
        raise RuntimeError("fallo de auditoría inyectado")

    monkeypatch.setattr(auto_close.audit, "log", fail)
    with pytest.raises(RuntimeError, match="fallo de auditoría inyectado"):
        auto_close.close_report(report_id=report.pk, operation_at=OPERATION_AT)

    report.refresh_from_db()
    assert report.status == ReportStatus.RESUELTO
    assert report.closed_at is None
    assert (ReportStatusHistory.objects.count(), AuditLog.objects.count()) == counts


def test_eligible_ids_use_inclusive_cutoff_and_stable_order(
    report,
    operator_user,
    institution,
):
    _resolved(report, operator_user, institution)
    _age_resolution(report, delta=timedelta(hours=72))

    assert auto_close.eligible_report_ids(operation_at=OPERATION_AT) == (report.pk,)

    _age_resolution(report, delta=timedelta(hours=72) - timedelta(microseconds=1))
    assert auto_close.eligible_report_ids(operation_at=OPERATION_AT) == ()
