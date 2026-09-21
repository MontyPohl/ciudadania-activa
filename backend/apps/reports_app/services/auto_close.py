"""Cierre automático idempotente de reportes resueltos."""

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.enums import ActorType, ReportStatus
from apps.core.services import audit
from apps.map_app.cache import invalidate_public_reports_cache
from apps.reports_app.models import Report, ReportStatusHistory


class AutoCloseConfigurationError(RuntimeError):
    pass


class AutoCloseProjectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class AutoCloseResult:
    report: Report | None
    changed: bool


def cutoff_for(operation_at=None):
    operation_at = operation_at or timezone.now()
    hours = getattr(settings, "REPORT_AUTO_CLOSE_HOURS", 72)
    if isinstance(hours, bool) or not isinstance(hours, int) or hours <= 0:
        raise AutoCloseConfigurationError("REPORT_AUTO_CLOSE_HOURS debe ser positivo.")
    return operation_at - timedelta(hours=hours)


def eligible_report_ids(*, operation_at=None):
    cutoff = cutoff_for(operation_at)
    return tuple(
        Report.objects.filter(
            status=ReportStatus.RESUELTO,
            resolved_at__lte=cutoff,
        )
        .order_by("resolved_at", "id")
        .values_list("id", flat=True)
    )


def close_report(*, report_id, operation_at=None) -> AutoCloseResult:
    operation_at = operation_at or timezone.now()
    cutoff = cutoff_for(operation_at)
    with transaction.atomic():
        try:
            report = Report.objects.select_for_update().get(pk=report_id)
        except Report.DoesNotExist:
            return AutoCloseResult(report=None, changed=False)

        if report.status != ReportStatus.RESUELTO:
            return AutoCloseResult(report=report, changed=False)
        if report.resolved_at is None or report.resolved_at > cutoff:
            return AutoCloseResult(report=report, changed=False)
        _ensure_resolved_projection(report)

        report.status = ReportStatus.CERRADO
        report.closed_at = operation_at
        report.save(update_fields=["status", "closed_at", "updated_at"])
        ReportStatusHistory.objects.create(
            report=report,
            previous_status=ReportStatus.RESUELTO,
            new_status=ReportStatus.CERRADO,
            changed_by=None,
            resolution_cycle=report.resolution_cycle,
        )
        audit.log(
            None,
            "report.closed",
            {
                "report_id": str(report.pk),
                "institution_id": str(report.institution_id),
                "cycle": report.resolution_cycle,
                "previous_status": ReportStatus.RESUELTO,
                "new_status": ReportStatus.CERRADO,
                "closure_mode": "automatic",
            },
            actor_type=ActorType.SYSTEM,
        )
        transaction.on_commit(invalidate_public_reports_cache, robust=True)
        return AutoCloseResult(report=report, changed=True)


def _ensure_resolved_projection(report):
    if (
        report.validated_at is None
        or report.institution_id is None
        or report.resolution_cycle < 1
        or report.closed_at is not None
    ):
        raise AutoCloseProjectionError("El reporte resuelto tiene una proyección inconsistente.")
