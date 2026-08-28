"""Consultas públicas de solo lectura sobre ``reports_app``."""

from django.db.models import Case, IntegerField, Value, When

from apps.core.enums import ReportStatus
from apps.reports_app.models import Report
from apps.reports_app.selectors import ACTIVE_REPORT_STATUSES

ACTIVE_STATUSES = ACTIVE_REPORT_STATUSES
RESOLVED_STATUSES = (ReportStatus.RESUELTO, ReportStatus.CERRADO)
PUBLIC_STATUSES = (*ACTIVE_STATUSES, *RESOLVED_STATUSES)

FILTER_STATUSES = {
    "active": ACTIVE_STATUSES,
    "resolved": RESOLVED_STATUSES,
    "all": PUBLIC_STATUSES,
}


def public_reports(params):
    """Queryset público, espacialmente indexable y con orden de página estable."""
    state_filter = params["state_filter"]
    bbox = params["bbox"]
    envelope = bbox.envelope()

    queryset = Report.objects.filter(
        status__in=FILTER_STATUSES[state_filter],
        # ``&&`` conserva el prefiltro GIST. Sus cajas son deliberadamente
        # aproximadas, así que ``intersects`` hace el recheck exacto necesario para
        # respetar los bordes abiertos de ROUND_HALF_UP en puntos de empate.
        location__bboverlaps=envelope,
        location__intersects=envelope,
    ).select_related("category", "institution")

    category = params.get("category")
    if category is not None:
        queryset = queryset.filter(category=category)

    if state_filter == "all":
        return queryset.annotate(
            _public_state_group=Case(
                When(status__in=ACTIVE_STATUSES, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        ).order_by("_public_state_group", "-created_at", "-id")

    return queryset.order_by("-created_at", "-id")


def public_report_detail_queryset():
    """Misma lista positiva que el listado: rechazado e inexistente son un 404."""
    return Report.objects.filter(status__in=PUBLIC_STATUSES).select_related(
        "category", "institution"
    )
