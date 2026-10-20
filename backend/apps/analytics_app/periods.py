"""Períodos de métricas en el mes local del proyecto."""

import re
from datetime import date

from django.utils import timezone

from apps.analytics_app.constants import MIN_PERIOD, PUBLIC_WINDOW_MONTHS
from apps.rewards_app.periods import current_period, format_period, month_bounds

__all__ = [
    "current_period",
    "format_period",
    "month_range",
    "parse_period",
    "public_window",
    "trailing_months",
]


def parse_period(value: str | None, *, now=None) -> date:
    current = current_period(now)
    if value is None:
        return current
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", value):
        raise ValueError("Usá un período con formato YYYY-MM.")
    period = date.fromisoformat(f"{value}-01")
    if not MIN_PERIOD <= period <= current:
        raise ValueError("El período debe estar entre 2026-01 y el mes actual.")
    return period


def month_range(period: date):
    return month_bounds(period)


def trailing_months(period: date, n: int) -> list[date]:
    index = period.year * 12 + period.month - 1
    return [date(i // 12, i % 12 + 1, 1) for i in range(index - n + 1, index + 1)]


def public_window(now=None):
    now = now if now is not None else timezone.now()
    first = trailing_months(current_period(now), PUBLIC_WINDOW_MONTHS)[0]
    return month_range(first)[0], now
