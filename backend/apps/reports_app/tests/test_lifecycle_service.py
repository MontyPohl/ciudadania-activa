import uuid

import pytest
from django.utils import timezone

from apps.admin_app.models import AuditLog
from apps.core.enums import ActorType, ReportStatus
from apps.notifications_app.models import Notification
from apps.reports_app.models import ReportStatusHistory, ResolutionEvidence
from apps.reports_app.services import lifecycle
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import RewardTransaction
from apps.rewards_app.services import ledger

pytestmark = pytest.mark.django_db


def _validated(report, operator_user, institution):
    report.status = ReportStatus.ASIGNADO
    report.institution = institution
    report.validated_at = timezone.now()
    report.validated_by = operator_user
    report.resolution_cycle = 1
    report.save(
        update_fields=[
            "status",
            "institution",
            "validated_at",
            "validated_by",
            "resolution_cycle",
            "updated_at",
        ]
    )
    return report


def _transition(report, operator_user, institution, status, cycle=1):
    return lifecycle.transition_report_status(
        report_id=report.pk,
        operator_id=operator_user.pk,
        institution_id=institution.pk,
        target_status=status,
        expected_cycle=cycle,
    )


def _evidence(report, operator_user, cycle=1):
    return ResolutionEvidence.objects.create(
        report=report,
        description="Trabajo concluido.",
        evidence_url=f"resolution-evidence/{report.pk}/{cycle}/{uuid.uuid4()}.jpg",
        uploaded_by=operator_user,
        resolution_cycle=cycle,
        idempotency_key=uuid.uuid4(),
        content_hash="a" * 64,
    )


def test_processing_transition_writes_exact_history_and_audit(
    report,
    operator_user,
    institution,
):
    _validated(report, operator_user, institution)

    result = _transition(
        report,
        operator_user,
        institution,
        ReportStatus.EN_PROCESO,
    )

    report.refresh_from_db()
    history = ReportStatusHistory.objects.get()
    entry = AuditLog.objects.get()
    assert result.changed is True
    assert report.status == ReportStatus.EN_PROCESO
    assert history.previous_status == ReportStatus.ASIGNADO
    assert history.new_status == ReportStatus.EN_PROCESO
    assert history.changed_by == operator_user
    assert history.resolution_cycle == 1
    assert entry.actor_user == operator_user
    assert entry.actor_type == ActorType.HUMAN
    assert entry.action == "report.processing_started"
    assert entry.payload == {
        "report_id": str(report.pk),
        "institution_id": str(institution.pk),
        "cycle": 1,
        "previous_status": ReportStatus.ASIGNADO,
        "new_status": ReportStatus.EN_PROCESO,
    }


def test_resolution_requires_current_cycle_evidence_without_side_effects(
    report,
    operator_user,
    institution,
):
    _validated(report, operator_user, institution)
    _transition(report, operator_user, institution, ReportStatus.EN_PROCESO)
    _evidence(report, operator_user, cycle=2)
    counts = (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    )

    with pytest.raises(lifecycle.ResolutionEvidenceRequired):
        _transition(report, operator_user, institution, ReportStatus.RESUELTO)

    report.refresh_from_db()
    assert report.status == ReportStatus.EN_PROCESO
    assert (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    ) == counts


def test_resolution_creates_projection_history_audit_and_single_cycle_bonus(
    report,
    operator_user,
    institution,
):
    _validated(report, operator_user, institution)
    _transition(report, operator_user, institution, ReportStatus.EN_PROCESO)
    evidence = _evidence(report, operator_user)

    result = _transition(report, operator_user, institution, ReportStatus.RESUELTO)

    report.refresh_from_db()
    bonus = RewardTransaction.objects.get(event_type=RewardEventType.RESOLUTION_BONUS)
    history = ReportStatusHistory.objects.get(new_status=ReportStatus.RESUELTO)
    entry = AuditLog.objects.get(action="report.resolved")
    assert result.changed is True
    assert report.status == ReportStatus.RESUELTO
    assert report.resolved_at is not None
    assert report.closed_at is None
    assert history.previous_status == ReportStatus.EN_PROCESO
    assert history.resolution_cycle == 1
    assert bonus.user == report.citizen
    assert bonus.institution == institution
    assert bonus.status == RewardTxStatus.ACREDITADO
    assert bonus.resolution_cycle == 1
    assert (bonus.impact_delta, bonus.credit_delta) == (50, 50)
    assert entry.payload == {
        "report_id": str(report.pk),
        "institution_id": str(institution.pk),
        "cycle": 1,
        "previous_status": ReportStatus.EN_PROCESO,
        "new_status": ReportStatus.RESUELTO,
        "evidence_ids": [str(evidence.pk)],
        "resolution_bonus_transaction_id": str(bonus.pk),
    }
    assert Notification.objects.count() == 0


def test_manual_close_is_idempotent_and_has_no_economic_or_notification_effect(
    report,
    operator_user,
    institution,
):
    _validated(report, operator_user, institution)
    _transition(report, operator_user, institution, ReportStatus.EN_PROCESO)
    _evidence(report, operator_user)
    _transition(report, operator_user, institution, ReportStatus.RESUELTO)

    first = _transition(report, operator_user, institution, ReportStatus.CERRADO)
    counts = (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    )
    second = _transition(report, operator_user, institution, ReportStatus.CERRADO)

    report.refresh_from_db()
    history = ReportStatusHistory.objects.get(new_status=ReportStatus.CERRADO)
    entry = AuditLog.objects.get(action="report.closed")
    assert first.changed is True
    assert second.changed is False
    assert report.status == ReportStatus.CERRADO
    assert report.closed_at is not None
    assert history.previous_status == ReportStatus.RESUELTO
    assert history.resolution_cycle == 1
    assert entry.actor_user == operator_user
    assert entry.actor_type == ActorType.HUMAN
    assert entry.payload == {
        "report_id": str(report.pk),
        "institution_id": str(institution.pk),
        "cycle": 1,
        "previous_status": ReportStatus.RESUELTO,
        "new_status": ReportStatus.CERRADO,
        "closure_mode": "manual",
    }
    assert (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    ) == counts
    assert Notification.objects.count() == 0


def test_repeated_resolution_is_noop_without_duplicate_bonus_history_or_audit(
    report,
    operator_user,
    institution,
):
    _validated(report, operator_user, institution)
    _transition(report, operator_user, institution, ReportStatus.EN_PROCESO)
    _evidence(report, operator_user)
    first = _transition(report, operator_user, institution, ReportStatus.RESUELTO)
    counts = (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    )

    second = _transition(report, operator_user, institution, ReportStatus.RESUELTO)

    assert first.changed is True
    assert second.changed is False
    assert (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    ) == counts
    assert (
        RewardTransaction.objects.filter(
            event_type=RewardEventType.RESOLUTION_BONUS,
            report=report,
            resolution_cycle=1,
        ).count()
        == 1
    )


def test_stale_cycle_and_invalid_jump_are_rejected(report, operator_user, institution):
    _validated(report, operator_user, institution)

    with pytest.raises(lifecycle.StaleReportCycle):
        _transition(report, operator_user, institution, ReportStatus.EN_PROCESO, cycle=2)
    with pytest.raises(lifecycle.ReportStateNotAllowed):
        _transition(report, operator_user, institution, ReportStatus.CERRADO)

    report.refresh_from_db()
    assert report.status == ReportStatus.ASIGNADO
    assert ReportStatusHistory.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_synthetic_cycle_zero_closed_snapshot_is_not_accepted_as_noop(
    report,
    operator_user,
    institution,
):
    report.status = ReportStatus.CERRADO
    report.institution = institution
    report.save(update_fields=["status", "institution", "updated_at"])

    with pytest.raises(lifecycle.ReportStateNotAllowed):
        _transition(report, operator_user, institution, ReportStatus.CERRADO, cycle=0)

    assert ReportStatusHistory.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_inconsistent_closed_projection_is_not_accepted_as_noop(
    report,
    operator_user,
    institution,
):
    _validated(report, operator_user, institution)
    report.status = ReportStatus.CERRADO
    report.closed_at = timezone.now()
    report.save(update_fields=["status", "closed_at", "updated_at"])

    with pytest.raises(lifecycle.ReportStateNotAllowed):
        _transition(report, operator_user, institution, ReportStatus.CERRADO)

    assert ReportStatusHistory.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_deleted_validator_does_not_block_another_active_institution_operator(
    report,
    operator_user,
    institution,
    user_factory,
):
    from apps.admin_app.models import InstitutionOperator
    from apps.auth_app.enums import UserRole

    _validated(report, operator_user, institution)
    replacement = user_factory(
        email="operador-reemplazo@example.com",
        role=UserRole.OPERATOR,
    )
    InstitutionOperator.objects.create(
        user=replacement,
        institution=institution,
        active=True,
    )
    operator_user.delete()
    report.refresh_from_db()
    assert report.validated_by is None

    _transition(report, replacement, institution, ReportStatus.EN_PROCESO)
    _evidence(report, replacement)
    _transition(report, replacement, institution, ReportStatus.RESUELTO)
    result = _transition(report, replacement, institution, ReportStatus.CERRADO)

    report.refresh_from_db()
    assert result.changed is True
    assert report.status == ReportStatus.CERRADO
    assert report.closed_at is not None


def test_cross_tenant_and_non_operator_are_indistinguishable_or_forbidden(
    report,
    operator_user,
    institution,
    citizen,
):
    from apps.admin_app.models import Institution, InstitutionOperator

    _validated(report, operator_user, institution)
    other = Institution.objects.get(slug="ande")
    InstitutionOperator.objects.create(
        user=operator_user,
        institution=other,
        active=True,
    )

    with pytest.raises(lifecycle.ReportNotFound):
        lifecycle.transition_report_status(
            report_id=report.pk,
            operator_id=operator_user.pk,
            institution_id=other.pk,
            target_status=ReportStatus.EN_PROCESO,
            expected_cycle=1,
        )
    with pytest.raises(lifecycle.OperatorRequired):
        lifecycle.transition_report_status(
            report_id=report.pk,
            operator_id=citizen.pk,
            institution_id=institution.pk,
            target_status=ReportStatus.EN_PROCESO,
            expected_cycle=1,
        )


def test_ledger_failure_rolls_back_resolution_history_and_audit(
    report,
    operator_user,
    institution,
    monkeypatch,
):
    _validated(report, operator_user, institution)
    _transition(report, operator_user, institution, ReportStatus.EN_PROCESO)
    _evidence(report, operator_user)
    counts = (ReportStatusHistory.objects.count(), AuditLog.objects.count())

    def fail(*args, **kwargs):
        raise ledger.LedgerInvariantError("fallo inyectado")

    monkeypatch.setattr(ledger, "on_report_resolved", fail)
    with pytest.raises(ledger.LedgerInvariantError, match="fallo inyectado"):
        _transition(report, operator_user, institution, ReportStatus.RESUELTO)

    report.refresh_from_db()
    assert report.status == ReportStatus.EN_PROCESO
    assert report.resolved_at is None
    assert (ReportStatusHistory.objects.count(), AuditLog.objects.count()) == counts
    assert not RewardTransaction.objects.filter(
        event_type=RewardEventType.RESOLUTION_BONUS
    ).exists()
