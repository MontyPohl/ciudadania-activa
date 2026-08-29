"""Contrato explícito de lectura para el mapa público."""

from decimal import ROUND_HALF_UP, Decimal

from rest_framework import serializers

from apps.admin_app.models import Category
from apps.map_app.bbox import (
    PUBLIC_COORDINATE_QUANTUM,
    InvalidBoundingBox,
    parse_and_canonicalize_bbox,
)


class PublicBoundingBoxField(serializers.Field):
    """Convierte la entrada pública en un bbox validado y ya canonizado."""

    def to_internal_value(self, data):
        try:
            return parse_and_canonicalize_bbox(data)
        except InvalidBoundingBox as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def to_representation(self, value):
        return value.as_cache_value()


class PublicReportQuerySerializer(serializers.Serializer):
    bbox = PublicBoundingBoxField(required=True)
    category = serializers.SlugRelatedField(
        slug_field="slug",
        queryset=Category.objects.filter(is_active=True),
        required=False,
    )
    state_filter = serializers.ChoiceField(
        choices=("active", "resolved", "all"),
        default="active",
    )
    page = serializers.IntegerField(min_value=1, default=1)


class PublicCategorySerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    slug = serializers.SlugField(read_only=True)


class PublicInstitutionSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    slug = serializers.SlugField(read_only=True)


class PublicReportSerializer(serializers.Serializer):
    """Lista blanca única para listado y detalle; nunca serializa ``location``."""

    id = serializers.UUIDField(read_only=True)
    category = PublicCategorySerializer(read_only=True)
    institution = PublicInstitutionSerializer(read_only=True, allow_null=True)
    description = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    priority = serializers.CharField(read_only=True)
    latitude = serializers.SerializerMethodField()
    longitude = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(read_only=True)

    def get_latitude(self, report) -> float:
        return round_public_coordinate(report.location.y)

    def get_longitude(self, report) -> float:
        return round_public_coordinate(report.location.x)


class PublicReportDetailSerializer(PublicReportSerializer):
    """Extensión mínima del detalle; el listado cacheado conserva su contrato."""

    confirmation_count = serializers.IntegerField(read_only=True)


def round_public_coordinate(value: float) -> float:
    """Reduce precisión en presentación sin modificar la geometría persistida."""
    return float(public_coordinate_decimal(value))


def public_coordinate_decimal(value) -> Decimal:
    """Redondeo exacto desde la representación decimal del ``double`` de GEOS.

    ``Decimal(float)`` queda deliberadamente descartado: incorporaría la expansión
    binaria completa del double y podría mover un empate decimal al lado equivocado.
    """
    return Decimal(str(value)).quantize(PUBLIC_COORDINATE_QUANTUM, rounding=ROUND_HALF_UP)
