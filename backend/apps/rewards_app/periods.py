from datetime import date, datetime, time

from django.utils import timezone


def current_period(now: datetime | None = None) -> date:
    """Devuelve el primer día del mes en la zona del proyecto."""
    return timezone.localdate(now, timezone.get_default_timezone()).replace(day=1)


def month_bounds(period: date) -> tuple[datetime, datetime]:
    """Devuelve inicio inclusivo y fin exclusivo del mes, con zona horaria."""
    start = period.replace(day=1)
    end = date(start.year + 1, 1, 1) if start.month == 12 else start.replace(month=start.month + 1)
    project_timezone = timezone.get_default_timezone()
    return (
        timezone.make_aware(datetime.combine(start, time.min), project_timezone),
        timezone.make_aware(datetime.combine(end, time.min), project_timezone),
    )


def format_period(period: date) -> str:
    """Representa el período mensual como YYYY-MM."""
    return period.strftime("%Y-%m")
