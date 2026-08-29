"""Consultas de lectura del dominio de reportes."""

from django.conf import settings
from django.contrib.gis.db.models import GeometryField, PointField
from django.contrib.gis.db.models.functions import Distance, Envelope
from django.contrib.gis.geos import Point
from django.db.models import Count, Exists, Func, OuterRef, Value
from django.db.models.functions import Cast

from apps.core.enums import ReportStatus
from apps.reports_app.models import Report, ReportConfirmation

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
