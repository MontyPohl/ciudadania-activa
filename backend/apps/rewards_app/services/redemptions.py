"""Canje simulado transaccional de créditos institucionales (RF-43)."""

import logging
from dataclasses import dataclass
from functools import partial

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.auth_app.enums import UserRole, VerificationStatus
from apps.core.services import audit
from apps.notifications_app.enums import NotificationStatus, NotificationType
from apps.notifications_app.models import Notification
from apps.notifications_app.services import dispatch
from apps.rewards_app import codes, periods, selectors
from apps.rewards_app.constants import MVP_CREDIT_COST
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import Reward, RewardRedemption, RewardTransaction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RedemptionResult:
    redemption: RewardRedemption
    transaction: RewardTransaction
    balance_after: int


class NotVerifiedCitizen(Exception):
    """El canje exige un ciudadano activo con identidad aprobada."""


class RedemptionConflict(Exception):
    """La recompensa o el saldo no permiten emitir otro vale."""

    def __init__(self, code: str, **extra):
        self.code = code
        self.extra = extra
        super().__init__(code)


def redeem(*, user_id, reward_id, operation_at=None) -> RedemptionResult:
    """Emite el vale, el débito y sus registros con User como primer lock.

    No bloquea Reward ni Institution. La carrera H7 con una reversión se acepta:
    equivale al orden serial canje → reversión y el vale conserva su validez aunque
    el saldo quede negativo (D-01).
    """
    operation_at = operation_at or timezone.now()
    with transaction.atomic():
        user = get_user_model().objects.select_for_update().get(pk=user_id)
        if not (
            user.is_active
            and user.role == UserRole.CITIZEN
            and user.verification_status == VerificationStatus.APROBADO
        ):
            raise NotVerifiedCitizen

        reward = Reward.objects.select_related("institution").get(pk=reward_id)
        if not reward.active or not reward.institution.is_active:
            raise RedemptionConflict("REWARD_INACTIVE")
        period = periods.current_period(operation_at)
        existing = selectors.redemption_in_period(
            user_id=user.pk, institution_id=reward.institution_id, period=period
        )
        if existing is not None:
            raise RedemptionConflict("MONTHLY_LIMIT_REACHED", redemption_id=existing.pk)
        balance = selectors.institution_balance(
            user_id=user.pk, institution_id=reward.institution_id
        )
        if balance < MVP_CREDIT_COST:
            raise RedemptionConflict(
                "INSUFFICIENT_BALANCE", balance=balance, missing_credits=MVP_CREDIT_COST - balance
            )

        try:
            with transaction.atomic():
                redemption = RewardRedemption.objects.create(
                    user=user,
                    reward=reward,
                    institution=reward.institution,
                    period_month=period,
                    code=codes.generate_voucher_code(),
                    credits_spent=MVP_CREDIT_COST,
                )
        except IntegrityError as exc:
            if _constraint_name(exc) != "redemption_una_por_mes_institucion":
                raise
            existing = selectors.redemption_in_period(
                user_id=user.pk, institution_id=reward.institution_id, period=period
            )
            if existing is None:
                raise
            raise RedemptionConflict("MONTHLY_LIMIT_REACHED", redemption_id=existing.pk) from exc

        tx = RewardTransaction.objects.create(
            user=user,
            institution=reward.institution,
            redemption=redemption,
            event_type=RewardEventType.REDEMPTION,
            impact_delta=0,
            credit_delta=-MVP_CREDIT_COST,
            status=RewardTxStatus.ACREDITADO,
            credited_at=operation_at,
        )
        audit.log(
            user,
            "reward.redeemed",
            {
                "redemption_id": str(redemption.pk),
                "reward_id": str(reward.pk),
                "institution_id": str(reward.institution_id),
                "period": periods.format_period(period),
                "credits_spent": MVP_CREDIT_COST,
                "balance_before": balance,
                "balance_after": balance - MVP_CREDIT_COST,
                "ledger_transaction_id": str(tx.pk),
            },
        )
        notification = dispatch.create(
            user, NotificationType.CANJE_REALIZADO, redemption=redemption
        )
        transaction.on_commit(partial(_enqueue, notification.pk), robust=True)
        return RedemptionResult(redemption, tx, balance - MVP_CREDIT_COST)


def _constraint_name(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.__cause__, "diag", None), "constraint_name", None)


def _enqueue(notification_id) -> None:
    """Encola después del commit; un fallo del broker no revierte el canje."""
    from apps.notifications_app.tasks import send_notification

    try:
        send_notification.delay(notification_id)
    except Exception as exc:
        logger.warning(
            "No se pudo encolar el aviso de canje %s (%s).", notification_id, type(exc).__name__
        )
        Notification.objects.filter(pk=notification_id).update(
            status=NotificationStatus.FALLIDO, updated_at=timezone.now()
        )
