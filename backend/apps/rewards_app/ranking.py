"""Top mensual con caché de puntajes y resolución de privacidad por petición."""

import logging
from datetime import datetime
from typing import NamedTuple
from uuid import UUID

from django.conf import settings
from django.core.cache import cache
from django.db.models import Max, Sum
from django.utils import timezone
from redis.exceptions import RedisError

from apps.auth_app.enums import UserRole
from apps.auth_app.models import User
from apps.rewards_app import display, periods
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import RewardTransaction

logger = logging.getLogger(__name__)
RANKING_SIZE = 10


class RankingRow(NamedTuple):
    user_id: UUID
    points: int


def monthly_top(*, now: datetime | None = None) -> list[RankingRow]:
    period = periods.current_period(now)
    start, end = periods.month_bounds(period)
    rows = (
        RewardTransaction.objects.filter(
            status=RewardTxStatus.ACREDITADO,
            credited_at__gte=start,
            credited_at__lt=end,
            user__role=UserRole.CITIZEN,
            user__is_active=True,
        )
        .exclude(event_type=RewardEventType.REDEMPTION)
        .values("user_id")
        .annotate(points=Sum("impact_delta"), reached_at=Max("credited_at"))
        .filter(points__gt=0)
        .order_by("-points", "reached_at", "user_id")[:RANKING_SIZE]
    )
    return [RankingRow(row["user_id"], row["points"]) for row in rows]


def cached_monthly_top(*, now: datetime | None = None) -> list[RankingRow]:
    # El mismo instante fija la clave y la consulta incluso al cambiar de mes.
    if now is None:
        now = timezone.now()
    period = periods.current_period(now)
    key = f"ranking:v1:{period:%Y-%m}"
    try:
        rows = cache.get(key)
        if rows is not None:
            return [RankingRow(*row) for row in rows]
    except (RedisError, OSError):
        _warn_cache_unavailable()
        return monthly_top(now=now)

    rows = monthly_top(now=now)
    try:
        # Nunca se guardan nombres ni preferencias de visibilidad en Redis.
        cache.set(key, rows, timeout=settings.PUBLIC_RANKING_CACHE_TTL_SECONDS)
    except (RedisError, OSError):
        _warn_cache_unavailable()
    return rows


def resolve_entries(rows: list[RankingRow], *, viewer_id: UUID | None = None) -> list[dict]:
    users = {
        user.pk: user
        for user in User.objects.filter(pk__in=[row.user_id for row in rows]).select_related(
            "citizen_profile"
        )
    }
    entries = []
    for row in rows:
        user = users.get(row.user_id)
        if user is None:
            continue
        display_name, is_private = display.ranking_display_name(user)
        entries.append(
            {
                "position": len(entries) + 1,
                "display_name": display_name,
                "points": row.points,
                "is_private": is_private,
                "is_me": viewer_id == row.user_id,
            }
        )
    return entries


def _warn_cache_unavailable() -> None:
    logger.warning("La caché del ranking no está disponible; se consulta la base de datos.")
