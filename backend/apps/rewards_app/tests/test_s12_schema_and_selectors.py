from datetime import date, timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.admin_app.models import Institution
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import Reward, RewardRedemption, RewardTransaction
from apps.rewards_app.periods import current_period, month_bounds
from apps.rewards_app.selectors import ImpactSummary, impact_summary, institution_balances

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "scenario",
    ["sin_vale", "con_impacto", "canje_mensual_duplicado", "recompensa_duplicada"],
)
def test_s12_constraints_rechazan_filas_invalidas(citizen, institution, scenario):
    reward = Reward.objects.create(institution=institution, name="Vale simulado")
    redemption = RewardRedemption.objects.create(
        user=citizen,
        reward=reward,
        institution=institution,
        period_month=date(2026, 10, 1),
        code="CA-0000-0000-0000-0001",
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        if scenario in {"sin_vale", "con_impacto"}:
            RewardTransaction.objects.create(
                user=citizen,
                institution=institution,
                redemption=None if scenario == "sin_vale" else redemption,
                event_type=RewardEventType.REDEMPTION,
                impact_delta=10 if scenario == "con_impacto" else 0,
                credit_delta=-1000,
                status=RewardTxStatus.ACREDITADO,
                credited_at=timezone.now(),
            )
        elif scenario == "canje_mensual_duplicado":
            RewardRedemption.objects.create(
                user=citizen,
                reward=reward,
                institution=institution,
                period_month=date(2026, 10, 1),
                code="CA-0000-0000-0000-0002",
            )
        else:
            Reward.objects.create(institution=institution, name="Otra recompensa")


def test_selectores_con_ledger_mixto(citizen, institution, report):
    now = timezone.now()
    period = current_period(now)
    start, _ = month_bounds(period)
    other_institution = Institution.objects.create(name="Otra institución", slug="otra-s12")

    RewardTransaction.objects.create(
        user=citizen,
        event_type=RewardEventType.REPORT_CREATED,
        impact_delta=20,
        credit_delta=20,
        status=RewardTxStatus.PENDIENTE,
    )
    for target, amount, credited_at in [
        (institution, 300, start),
        (other_institution, 400, start - timedelta(seconds=1)),
    ]:
        RewardTransaction.objects.create(
            user=citizen,
            institution=target,
            event_type=RewardEventType.REPORT_CREATED,
            impact_delta=amount,
            credit_delta=amount,
            status=RewardTxStatus.ACREDITADO,
            credited_at=credited_at,
        )
    RewardTransaction.objects.create(
        user=citizen,
        event_type=RewardEventType.REPORT_CREATED,
        impact_delta=80,
        credit_delta=80,
        status=RewardTxStatus.ANULADO,
        voided_at=now,
    )
    original = RewardTransaction.objects.create(
        user=citizen,
        report=report,
        institution=institution,
        event_type=RewardEventType.VALIDATION_BONUS,
        impact_delta=30,
        credit_delta=30,
        status=RewardTxStatus.REVERTIDO,
        credited_at=start,
        reversed_at=now,
        resolution_cycle=1,
    )
    RewardTransaction.objects.create(
        user=citizen,
        report=report,
        institution=institution,
        event_type=RewardEventType.REVERSAL,
        impact_delta=-30,
        credit_delta=-30,
        status=RewardTxStatus.REVERTIDO,
        reversed_at=now,
        related_transaction=original,
        resolution_cycle=1,
    )
    expected_impact = ImpactSummary(historical=700, current_month=300, pending=20)
    assert impact_summary(user_id=citizen.pk, now=now) == expected_impact

    reward = Reward.objects.create(institution=institution, name="Vale simulado")
    redemption = RewardRedemption.objects.create(
        user=citizen,
        reward=reward,
        institution=institution,
        period_month=period,
        code="CA-0000-0000-0000-0003",
    )
    RewardTransaction.objects.create(
        user=citizen,
        institution=institution,
        redemption=redemption,
        event_type=RewardEventType.REDEMPTION,
        impact_delta=0,
        credit_delta=-1000,
        status=RewardTxStatus.ACREDITADO,
        credited_at=now,
    )

    assert impact_summary(user_id=citizen.pk, now=now) == expected_impact
    balances = institution_balances(user_id=citizen.pk)
    assert {row.institution.pk: row.balance for row in balances} == {
        institution.pk: -700,
        other_institution.pk: 400,
    }
    assert [row.institution.name for row in balances] == sorted(
        [institution.name, other_institution.name]
    )
