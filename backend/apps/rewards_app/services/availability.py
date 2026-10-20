"""Aviso diario de recompensa disponible (Sprint 13, S13-D10).

Un lote de Beat a las 09:00 locales busca a los ciudadanos que **hoy** podrían canjear
y les avisa por correo **a lo sumo una vez por institución y por mes**. La garantía la da
la base: la `dedup_key` de la notificación es única. Repetir el lote el mismo mes, o
que un ciudadano cruce el umbral de nuevo tras una reversión, no produce otro correo.

Solo lee: agrega el ledger con `Sum`, igual que los selectores de saldo de S12, y no
toma locks. El canje real sigue validando todo bajo lock en `redemptions.redeem`.
"""

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum

from apps.admin_app.models import Institution
from apps.auth_app.enums import UserRole, VerificationStatus
from apps.notifications_app.enums import NotificationType
from apps.notifications_app.services import dispatch
from apps.rewards_app import periods
from apps.rewards_app.constants import MVP_CREDIT_COST
from apps.rewards_app.enums import RewardTxStatus
from apps.rewards_app.models import Reward, RewardRedemption, RewardTransaction

logger = logging.getLogger(__name__)


def notify_available_rewards(*, now=None) -> int:
    """Crea un `RECOMPENSA_DISPONIBLE` por cada par elegible. Devuelve cuántos creó.

    Es elegible el par (ciudadano, institución) cuando:

    - el ciudadano está activo y con identidad aprobada;
    - su saldo `ACREDITADO` en la institución —canjes incluidos— llega al costo fijo;
    - la institución está activa y tiene su recompensa activa;
    - todavía no canjeó en esa institución este mes.
    """
    period = periods.current_period(now)
    institutions_with_reward = set(
        Reward.objects.filter(active=True, institution__is_active=True).values_list(
            "institution_id", flat=True
        )
    )
    if not institutions_with_reward:
        return 0

    balances = (
        RewardTransaction.objects.filter(
            status=RewardTxStatus.ACREDITADO,
            institution_id__in=institutions_with_reward,
            user__role=UserRole.CITIZEN,
            user__is_active=True,
            user__verification_status=VerificationStatus.APROBADO,
        )
        .order_by()
        .values("user_id", "institution_id")
        .annotate(balance=Sum("credit_delta"))
        .filter(balance__gte=MVP_CREDIT_COST)
    )
    redeemed = set(
        RewardRedemption.objects.filter(period_month=period).values_list(
            "user_id", "institution_id"
        )
    )
    pairs = [
        (row["user_id"], row["institution_id"])
        for row in balances
        if (row["user_id"], row["institution_id"]) not in redeemed
    ]
    if not pairs:
        return 0

    users = get_user_model().objects.in_bulk({user_id for user_id, _ in pairs})
    institutions = Institution.objects.in_bulk({inst_id for _, inst_id in pairs})
    created = 0
    for user_id, institution_id in pairs:
        with transaction.atomic():
            notification = dispatch.create(
                users[user_id],
                NotificationType.RECOMPENSA_DISPONIBLE,
                institution=institutions[institution_id],
                dedup_key=(
                    f"{NotificationType.RECOMPENSA_DISPONIBLE.value}:{user_id}:"
                    f"{institution_id}:{periods.format_period(period)}"
                ),
            )
            if notification is not None:
                dispatch.enqueue_on_commit(notification.pk)
                created += 1
    if created:
        logger.info("Se registraron %s avisos de recompensa disponible.", created)
    return created
