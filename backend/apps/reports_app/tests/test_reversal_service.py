import uuid

import pytest
from django.db.models import Sum

from apps.admin_app.models import AuditLog
from apps.core.enums import ActorType, ReportStatus
from apps.notifications_app.models import Notification
from apps.reports_app.models import ReportConfirmation, ReportStatusHistory, ResolutionEvidence
from apps.reports_app.services import lifecycle, reversal, validation
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import Reward, RewardRedemption, RewardTransaction
from apps.rewards_app.periods import current_period
from apps.rewards_app.services import ledger

pytestmark = pytest.mark.django_db


def _evidence(report, operator_user):
    return ResolutionEvidence.objects.create(
        report=report,
        description="Trabajo concluido.",
        evidence_url=f"resolution-evidence/{report.pk}/1/{uuid.uuid4()}.jpg",
        uploaded_by=operator_user,
        resolution_cycle=report.resolution_cycle,
        idempotency_key=uuid.uuid4(),
        content_hash="c" * 64,
    )


def _transition(report, operator_user, institution, status):
    return lifecycle.transition_report_status(
        report_id=report.pk,
        operator_id=operator_user.pk,
        institution_id=institution.pk,
        target_status=status,
        expected_cycle=report.resolution_cycle,
    )


def _reach_status(report, operator_user, institution, target_status):
    ledger.on_report_created(report)
    validation.validate_report(
        report_id=report.pk,
        operator=operator_user,
        institution=institution,
    )
    report.refresh_from_db()
    evidence = None
    if target_status != ReportStatus.ASIGNADO:
        _transition(report, operator_user, institution, ReportStatus.EN_PROCESO)
        report.refresh_from_db()
    if target_status in {ReportStatus.RESUELTO, ReportStatus.CERRADO}:
        evidence = _evidence(report, operator_user)
        _transition(report, operator_user, institution, ReportStatus.RESUELTO)
        report.refresh_from_db()
    if target_status == ReportStatus.CERRADO:
        _transition(report, operator_user, institution, ReportStatus.CERRADO)
        report.refresh_from_db()
    return evidence


def _reverse(report, admin_user, *, cycle=1, reason="Corrección administrativa fundada."):
    return reversal.revert_report_validation(
        report_id=report.pk,
        admin_id=admin_user.pk,
        expected_cycle=cycle,
        reason=reason,
    )


@pytest.mark.parametrize("actor", ["citizen", "operator", "admin_inactivo"])
def test_solo_un_admin_activo_puede_revertir(
    actor,
    report,
    citizen,
    operator_user,
    institution,
    admin_user,
):
    _reach_status(report, operator_user, institution, ReportStatus.ASIGNADO)
    if actor == "admin_inactivo":
        admin_user.is_active = False
        admin_user.save(update_fields=["is_active"])
    actors = {
        "citizen": citizen,
        "operator": operator_user,
        "admin_inactivo": admin_user,
    }
    counts = (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    )

    with pytest.raises(reversal.AdminRequired) as exc_info:
        reversal.revert_report_validation(
            report_id=report.pk,
            admin_id=actors[actor].pk,
            expected_cycle=report.resolution_cycle,
            reason="Corrección administrativa fundada.",
        )

    assert exc_info.value.code == "ADMIN_REQUIRED"
    assert not RewardTransaction.objects.filter(event_type=RewardEventType.REVERSAL).exists()
    assert (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    ) == counts


@pytest.mark.parametrize(
    "source_status",
    [
        ReportStatus.ASIGNADO,
        ReportStatus.EN_PROCESO,
        ReportStatus.RESUELTO,
        ReportStatus.CERRADO,
    ],
)
def test_reversal_is_allowed_from_every_approved_status_and_clears_projections(
    source_status,
    report,
    operator_user,
    institution,
    admin_user,
):
    category_id = report.category_id
    priority = report.priority
    evidence = _reach_status(report, operator_user, institution, source_status)

    result = _reverse(report, admin_user)

    report.refresh_from_db()
    assert result.changed is True
    assert report.status == ReportStatus.CLASIFICADO
    assert report.resolution_cycle == 1
    assert report.institution == institution
    assert report.category_id == category_id
    assert report.priority == priority
    assert report.validated_at is None
    assert report.validated_by is None
    assert report.resolved_at is None
    assert report.closed_at is None
    if evidence is not None:
        assert ResolutionEvidence.objects.filter(pk=evidence.pk, resolution_cycle=1).exists()


def test_reversal_writes_exact_history_audit_and_compensating_ledger(
    report,
    other_citizen,
    operator_user,
    institution,
    admin_user,
):
    evidence = _reach_status(report, operator_user, institution, ReportStatus.CERRADO)
    originals = tuple(
        RewardTransaction.objects.filter(status=RewardTxStatus.ACREDITADO)
        .exclude(event_type=RewardEventType.REDEMPTION)
        .order_by("created_at", "id")
    )
    original_values = {
        reward.pk: (reward.impact_delta, reward.credit_delta, reward.credited_at)
        for reward in originals
    }
    reward = Reward.objects.create(institution=institution, name="Vale simulado")
    voucher = RewardRedemption.objects.create(
        user=report.citizen,
        reward=reward,
        institution=institution,
        period_month=current_period(report.validated_at),
        code="CA-0000-0000-0000-0001",
        credits_spent=1000,
    )
    redemption = RewardTransaction.objects.create(
        user=report.citizen,
        institution=institution,
        redemption=voucher,
        event_type=RewardEventType.REDEMPTION,
        impact_delta=0,
        credit_delta=-1000,
        status=RewardTxStatus.ACREDITADO,
        credited_at=report.validated_at,
    )
    confirmation = ReportConfirmation.objects.create(
        report=report,
        citizen=other_citizen,
    )
    pending = RewardTransaction.objects.create(
        user=other_citizen,
        confirmation=confirmation,
        event_type=RewardEventType.CONFIRMATION_CREATED,
        impact_delta=10,
        credit_delta=10,
        status=RewardTxStatus.PENDIENTE,
    )
    reason = "  Reparacio\u0301n administrativa\r\nconfirmada.  "

    # El armado avisó por correo la validación y la resolución (S13); la reversión no avisa.
    avisos_previos = Notification.objects.count()
    result = _reverse(report, admin_user, reason=reason)

    normalized = "Reparación administrativa\nconfirmada."
    history = ReportStatusHistory.objects.get(
        previous_status=ReportStatus.CERRADO,
        new_status=ReportStatus.CLASIFICADO,
    )
    entry = AuditLog.objects.get(action="report.validation_reverted")
    pending.refresh_from_db()
    assert history.changed_by == admin_user
    assert history.reason == normalized
    assert history.resolution_cycle == 1
    assert entry.actor_user == admin_user
    assert entry.actor_type == ActorType.HUMAN
    assert entry.payload == {
        "report_id": str(report.pk),
        "institution_id": str(institution.pk),
        "cycle": 1,
        "previous_status": ReportStatus.CERRADO,
        "new_status": ReportStatus.CLASIFICADO,
        "reason": normalized,
        "reverted_transaction_ids": [
            str(reward.pk) for reward in result.ledger_result.reversed_transactions
        ],
        "reversal_transaction_ids": [
            str(reward.pk) for reward in result.ledger_result.reversal_transactions
        ],
        "voided_transaction_ids": [str(pending.pk)],
    }
    assert result.ledger_result.voided_transactions == (pending,)
    assert pending.status == RewardTxStatus.ANULADO
    redemption.refresh_from_db()
    assert redemption.status == RewardTxStatus.ACREDITADO
    assert redemption.credit_delta == -1000
    assert ResolutionEvidence.objects.filter(pk=evidence.pk).exists()
    for original in originals:
        original.refresh_from_db()
        assert original.status == RewardTxStatus.REVERTIDO
        assert (
            original.impact_delta,
            original.credit_delta,
            original.credited_at,
        ) == original_values[original.pk]
    assert [
        (reward.impact_delta, reward.credit_delta, reward.related_transaction_id)
        for reward in result.ledger_result.reversal_transactions
    ] == [(-original.impact_delta, -original.credit_delta, original.pk) for original in originals]
    balance = RewardTransaction.objects.filter(
        user=report.citizen,
        institution=institution,
        status=RewardTxStatus.ACREDITADO,
    ).aggregate(total=Sum("credit_delta"))["total"]
    assert balance == -1000
    assert Notification.objects.count() == avisos_previos


def test_repeated_reversal_same_cycle_is_noop_without_duplicates(
    report,
    operator_user,
    institution,
    admin_user,
):
    _reach_status(report, operator_user, institution, ReportStatus.RESUELTO)
    first = _reverse(report, admin_user)
    counts = (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    )

    second = _reverse(report, admin_user, reason="Otro motivo igualmente válido.")

    assert first.changed is True
    assert second.changed is False
    assert second.ledger_result is None
    assert (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    ) == counts


def test_old_cycle_cannot_reverse_a_revalidated_report(
    report,
    operator_user,
    institution,
    admin_user,
):
    _reach_status(report, operator_user, institution, ReportStatus.ASIGNADO)
    _reverse(report, admin_user)
    validation.validate_report(
        report_id=report.pk,
        operator=operator_user,
        institution=institution,
    )
    report.refresh_from_db()
    counts = (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    )

    with pytest.raises(reversal.StaleReportCycle):
        _reverse(report, admin_user, cycle=1)

    report.refresh_from_db()
    assert report.status == ReportStatus.ASIGNADO
    assert report.resolution_cycle == 2
    assert (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    ) == counts


@pytest.mark.parametrize(
    "reason",
    [
        "corto",
        "x" * 1001,
        "motivo con\x00 nul",
        "motivo con\t tabulación",
        "motivo con\u200b formato",
        "motivo con\r retorno",
    ],
)
def test_reason_rejects_invalid_length_and_control_characters(reason):
    with pytest.raises(reversal.InvalidReversalReason):
        reversal.normalize_reason(reason)


def test_reversal_rejects_unvalidated_and_inconsistent_states(
    report,
    admin_user,
    operator_user,
    institution,
):
    with pytest.raises(reversal.ReportStateNotAllowed):
        _reverse(report, admin_user, cycle=0)

    _reach_status(report, operator_user, institution, ReportStatus.RESUELTO)
    report.resolved_at = None
    report.save(update_fields=["resolved_at", "updated_at"])
    with pytest.raises(reversal.ReportStateNotAllowed):
        _reverse(report, admin_user)


def test_audit_failure_rolls_back_report_history_and_ledger(
    report,
    operator_user,
    institution,
    admin_user,
    monkeypatch,
):
    _reach_status(report, operator_user, institution, ReportStatus.ASIGNADO)
    original = RewardTransaction.objects.get(event_type=RewardEventType.REPORT_CREATED)
    counts = (ReportStatusHistory.objects.count(), AuditLog.objects.count())

    def fail(*args, **kwargs):
        raise RuntimeError("fallo de auditoría inyectado")

    monkeypatch.setattr(reversal.audit, "log", fail)
    with pytest.raises(RuntimeError, match="fallo de auditoría inyectado"):
        _reverse(report, admin_user)

    report.refresh_from_db()
    original.refresh_from_db()
    assert report.status == ReportStatus.ASIGNADO
    assert report.validated_at is not None
    assert original.status == RewardTxStatus.ACREDITADO
    assert not RewardTransaction.objects.filter(event_type=RewardEventType.REVERSAL).exists()
    assert (ReportStatusHistory.objects.count(), AuditLog.objects.count()) == counts
