from dataclasses import dataclass
from datetime import date, datetime

from django.db.models import Q, QuerySet, Sum
from django.db.models.functions import Coalesce

from apps.admin_app.models import Institution
from apps.rewards_app import periods
from apps.rewards_app.constants import MVP_CREDIT_COST
from apps.rewards_app.enums import RewardTxStatus
from apps.rewards_app.models import Reward, RewardRedemption, RewardTransaction


@dataclass(frozen=True)
class InstitutionBalance:
    institution: Institution
    balance: int


@dataclass(frozen=True)
class ImpactSummary:
    historical: int
    current_month: int
    pending: int


@dataclass(frozen=True)
class CatalogEntry:
    reward: Reward
    institution: Institution
    balance: int
    can_redeem: bool
    blocked_reason: str | None
    missing_credits: int
    redemption_this_month: RewardRedemption | None


def institution_balance(*, user_id, institution_id) -> int:
    """Suma los créditos acreditados, incluyendo los débitos de canje."""
    return (
        RewardTransaction.objects.filter(user_id=user_id, institution_id=institution_id)
        .order_by()
        .aggregate(
            balance=Coalesce(Sum("credit_delta", filter=Q(status=RewardTxStatus.ACREDITADO)), 0)
        )["balance"]
    )


def institution_balances(*, user_id) -> list[InstitutionBalance]:
    """Devuelve solo instituciones con movimientos acreditados, por nombre."""
    institutions = (
        Institution.objects.filter(
            reward_transactions__user_id=user_id,
            reward_transactions__status=RewardTxStatus.ACREDITADO,
        )
        .order_by()
        .annotate(balance=Sum("reward_transactions__credit_delta"))
        .order_by("name")
    )
    return [
        InstitutionBalance(institution=institution, balance=institution.balance)
        for institution in institutions
    ]


def impact_summary(*, user_id, now: datetime | None = None) -> ImpactSummary:
    """Separa el impacto acreditado histórico, mensual y pendiente."""
    start, end = periods.month_bounds(periods.current_period(now))
    accredited = Q(status=RewardTxStatus.ACREDITADO)
    values = (
        RewardTransaction.objects.filter(user_id=user_id)
        .order_by()
        .aggregate(
            historical=Coalesce(Sum("impact_delta", filter=accredited), 0),
            current_month=Coalesce(
                Sum(
                    "impact_delta",
                    filter=accredited & Q(credited_at__gte=start, credited_at__lt=end),
                ),
                0,
            ),
            pending=Coalesce(Sum("impact_delta", filter=Q(status=RewardTxStatus.PENDIENTE)), 0),
        )
    )
    return ImpactSummary(**values)


def ledger_movements(*, user_id) -> QuerySet[RewardTransaction]:
    return (
        RewardTransaction.objects.filter(user_id=user_id)
        .select_related("institution", "confirmation")
        .order_by("-created_at", "-id")
    )


def redemption_in_period(*, user_id, institution_id, period: date) -> RewardRedemption | None:
    return RewardRedemption.objects.filter(
        user_id=user_id, institution_id=institution_id, period_month=period
    ).first()


def catalog_for(*, user) -> list[CatalogEntry]:
    """Prioriza el límite mensual sobre la falta de saldo en el catálogo."""
    balances = {row.institution.pk: row.balance for row in institution_balances(user_id=user.pk)}
    redemptions = {
        row.institution_id: row
        for row in RewardRedemption.objects.filter(
            user_id=user.pk, period_month=periods.current_period()
        )
    }
    rewards = (
        Reward.objects.filter(active=True, institution__is_active=True)
        .select_related("institution")
        .order_by("institution__name")
    )
    entries = []
    for reward in rewards:
        balance = balances.get(reward.institution_id, 0)
        redemption = redemptions.get(reward.institution_id)
        if redemption is not None:
            blocked_reason = "MONTHLY_LIMIT_REACHED"
            missing_credits = 0
        elif balance < MVP_CREDIT_COST:
            blocked_reason = "INSUFFICIENT_BALANCE"
            missing_credits = MVP_CREDIT_COST - balance
        else:
            blocked_reason = None
            missing_credits = 0
        entries.append(
            CatalogEntry(
                reward=reward,
                institution=reward.institution,
                balance=balance,
                can_redeem=blocked_reason is None,
                blocked_reason=blocked_reason,
                missing_credits=missing_credits,
                redemption_this_month=redemption,
            )
        )
    return entries
