import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from apps.core.enums import ReportStatus
from apps.reports_app.models import ReportStatusHistory, ResolutionEvidence
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import RewardTransaction

pytestmark = pytest.mark.django_db


def test_new_report_and_history_begin_at_cycle_zero(report):
    history = ReportStatusHistory.objects.create(report=report, new_status=ReportStatus.CREADO)

    assert report.resolution_cycle == 0
    assert history.resolution_cycle == 0


def test_resolution_evidence_requires_positive_cycle(report, operator_user):
    with pytest.raises(IntegrityError), transaction.atomic():
        ResolutionEvidence.objects.create(
            report=report,
            description="Evidencia inválida",
            evidence_url="resolution-evidence/invalid.jpg",
            uploaded_by=operator_user,
            resolution_cycle=0,
        )


def test_reward_cycle_rejects_negative_value(citizen):
    with pytest.raises(IntegrityError), transaction.atomic():
        RewardTransaction.objects.create(
            user=citizen,
            event_type=RewardEventType.REPORT_CREATED,
            impact_delta=20,
            credit_delta=20,
            status=RewardTxStatus.PENDIENTE,
            resolution_cycle=-1,
        )


def test_postgresql_trigger_rejects_cycle_decrease(report):
    report.resolution_cycle = 1
    report.validated_at = report.created_at
    report.status = ReportStatus.ASIGNADO
    report.save(update_fields=["resolution_cycle", "validated_at", "status", "updated_at"])

    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE reports SET resolution_cycle = 0 WHERE id = %s",
            [report.pk],
        )


def test_postgresql_trigger_only_advances_one_cycle_during_validation(report):
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE reports SET resolution_cycle = 2, validated_at = NOW(), status = 'ASIGNADO' "
            "WHERE id = %s",
            [report.pk],
        )
