"""Consultas de lectura del dominio de reportes."""

from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.contrib.gis.db.models import GeometryField, PointField
from django.contrib.gis.db.models.functions import Distance, Envelope
from django.contrib.gis.geos import Point
from django.db.models import (
    Case,
    Count,
    Exists,
    Func,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Value,
    When,
)
from django.db.models.functions import Cast
from django.utils import timezone

from apps.admin_app.models import AuditLog
from apps.ai_client_app.models import AiClassification
from apps.core.enums import ReportPriority, ReportStatus
from apps.reports_app.enums import OPERABLE_REPORT_STATUSES, MediaType
from apps.reports_app.models import Report, ReportConfirmation, ReportMedia

ACTIVE_REPORT_STATUSES = (
    ReportStatus.CREADO,
    ReportStatus.CLASIFICADO,
    ReportStatus.ASIGNADO,
    ReportStatus.EN_PROCESO,
)

VISIBLE_REPORT_STATUSES = (
    *ACTIVE_REPORT_STATUSES,
    ReportStatus.RESUELTO,
    ReportStatus.CERRADO,
)

_OPERATOR_PRIORITY_ORDER = Case(
    When(priority=ReportPriority.ALTA, then=Value(0)),
    When(priority=ReportPriority.MEDIA, then=Value(1)),
    When(priority=ReportPriority.BAJA, then=Value(2)),
    default=Value(3),
    output_field=IntegerField(),
)

_AI_REPORT_CLASSIFIED_ACTION = "ai.report.classified"


def contextual_report_queryset(*, citizen):
    """Detalle visible con contexto calculado sin exponer identidades."""
    confirmed_by_citizen = ReportConfirmation.objects.filter(
        report_id=OuterRef("pk"),
        citizen=citizen,
    )
    return (
        Report.objects.filter(status__in=VISIBLE_REPORT_STATUSES)
        .select_related("category", "institution")
        .annotate(
            confirmation_count=Count("confirmations"),
            confirmed_by_me=Exists(confirmed_by_citizen),
        )
    )


def operator_reports_for_institution(*, institution):
    """Reportes visibles en el contexto institucional de lectura de S9-A.

    Una asignación persistida siempre prevalece. Solo cuando ``Report.institution``
    es nula se usa ``Category.default_institution`` como ruta provisional de lectura.
    La consulta no considera sugerencias de IA ni modifica el reporte.
    """
    return Report.objects.filter(
        Q(institution_id=institution.pk)
        | Q(
            institution__isnull=True,
            category__default_institution_id=institution.pk,
        )
    ).select_related("category", "institution", "category__default_institution")


def operator_report_list(*, institution, params):
    """Bandeja institucional filtrada y con orden total entre páginas."""
    queryset = operator_reports_for_institution(institution=institution).filter(
        status__in=OPERABLE_REPORT_STATUSES
    )

    if status := params.get("status"):
        queryset = queryset.filter(status=status)
    if priority := params.get("priority"):
        queryset = queryset.filter(priority=priority)
    if category := params.get("category"):
        queryset = queryset.filter(category=category)

    current_timezone = timezone.get_current_timezone()
    if created_from := params.get("created_from"):
        queryset = queryset.filter(
            created_at__gte=_start_of_local_date(created_from, current_timezone)
        )
    if created_to := params.get("created_to"):
        next_date = _next_date(created_to)
        if next_date is not None:
            queryset = queryset.filter(
                created_at__lt=_start_of_local_date(next_date, current_timezone)
            )

    return queryset.annotate(_operator_priority_order=_OPERATOR_PRIORITY_ORDER).order_by(
        "_operator_priority_order",
        "created_at",
        "id",
    )


def operator_report_detail(*, institution):
    """Detalle bajo el mismo scope de bandeja, sin joins que inflen confirmaciones."""
    photos = ReportMedia.objects.filter(media_type=MediaType.FOTO).order_by("created_at", "id")
    classifications = AiClassification.objects.select_related(
        "suggested_category",
        "suggested_institution",
    ).order_by("-created_at", "-id")

    return (
        operator_reports_for_institution(institution=institution)
        .annotate(confirmation_count=Count("confirmations"))
        .prefetch_related(
            Prefetch("media", queryset=photos, to_attr="operator_photos"),
            Prefetch(
                "ai_classifications",
                queryset=classifications,
                to_attr="operator_ai_classifications",
            ),
        )
    )


def attach_operator_historical_human_review(*, report):
    """Adjunta la decisión histórica S8 a la clasificación mostrada en detalle.

    La ausencia o ambigüedad de evidencia no equivale a ``False``. Solo una fila
    de auditoría inequívoca, ligada por acción, reporte y clasificación exactos,
    puede acreditar el booleano. El atributo es transitorio y nunca se persiste.
    """
    classifications = report.operator_ai_classifications
    if not classifications:
        return report

    classification = classifications[0]
    historical_values = list(
        AuditLog.objects.filter(
            action=_AI_REPORT_CLASSIFIED_ACTION,
            payload__report_id=str(report.pk),
            payload__classification_id=str(classification.pk),
        )
        .order_by("created_at", "id")
        .values_list("payload__requires_human_review", flat=True)[:2]
    )

    historical_value = None
    if len(historical_values) == 1 and type(historical_values[0]) is bool:
        historical_value = historical_values[0]

    classification.operator_requires_human_review = historical_value
    return report


def _start_of_local_date(value: date, current_timezone) -> datetime:
    return timezone.make_aware(datetime.combine(value, time.min), current_timezone)


def _next_date(value: date) -> date | None:
    if value == date.max:
        return None
    return value + timedelta(days=1)


def find_possible_duplicates(
    *,
    category,
    latitude: float,
    longitude: float,
    radius_meters: int | None = None,
):
    """Devuelve hasta cinco reportes activos cercanos, sin exponer la distancia."""
    radius = settings.REPORT_DUPLICATE_RADIUS_METERS if radius_meters is None else radius_meters
    point = Point(longitude, latitude, srid=4326)
    geography_field = PointField(srid=4326, geography=True)
    point_geography = Value(point, output_field=geography_field)
    buffered_geography = Func(
        point_geography,
        Value(radius),
        function="ST_Buffer",
        output_field=GeometryField(srid=4326, geography=True),
    )
    envelope = Envelope(Cast(buffered_geography, GeometryField(srid=4326)))

    return (
        Report.objects.filter(category=category, status__in=ACTIVE_REPORT_STATUSES)
        # El envelope del buffer geography es un superset barato: habilita el GIST
        # geometry existente. ST_DWithin hace después el recheck métrico autoritativo.
        .filter(location__bboverlaps=envelope)
        .annotate(_location_geography=Cast("location", geography_field))
        .filter(_location_geography__dwithin=(point_geography, radius))
        .annotate(_duplicate_distance=Distance("_location_geography", point_geography))
        .select_related("category", "institution")
        .order_by("_duplicate_distance", "id")[:5]
    )
