"""Contratos de solo lectura para la bandeja institucional de reportes."""

from rest_framework import serializers

from apps.admin_app.models import Category
from apps.core.enums import ReportPriority, ReportStatus
from apps.core.services import storage
from apps.reports_app.serializers import StrictInputMixin

OPERATOR_REPORT_PHOTO_SIGNED_URL_TTL_SECONDS = 300


class OperatorReportContextQuerySerializer(StrictInputMixin, serializers.Serializer):
    institution_id = serializers.UUIDField(required=False)


class OperatorReportListQuerySerializer(OperatorReportContextQuerySerializer):
    status = serializers.ChoiceField(choices=ReportStatus.values, required=False)
    priority = serializers.ChoiceField(choices=ReportPriority.values, required=False)
    category = serializers.SlugRelatedField(
        slug_field="slug",
        queryset=Category.objects.filter(is_active=True),
        required=False,
    )
    created_from = serializers.DateField(required=False)
    created_to = serializers.DateField(required=False)
    page = serializers.IntegerField(min_value=1, required=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        created_from = attrs.get("created_from")
        created_to = attrs.get("created_to")
        if created_from is not None and created_to is not None and created_from > created_to:
            raise serializers.ValidationError(
                {"created_to": ["Debe ser igual o posterior a created_from."]}
            )
        return attrs


class OperatorCategorySerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    slug = serializers.SlugField(read_only=True)


class OperatorInstitutionSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    slug = serializers.SlugField(read_only=True)


class OperatorReportListSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(read_only=True)
    priority = serializers.CharField(read_only=True)
    category = OperatorCategorySerializer(read_only=True)
    institution = OperatorInstitutionSerializer(read_only=True, allow_null=True)
    effective_read_institution = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(read_only=True)

    def get_effective_read_institution(self, report):
        institution = report.institution or report.category.default_institution
        if institution is None:
            return None
        return OperatorInstitutionSerializer(institution).data


class OperatorAiClassificationSerializer(serializers.Serializer):
    suggested_category = OperatorCategorySerializer(read_only=True, allow_null=True)
    suggested_institution = OperatorInstitutionSerializer(read_only=True, allow_null=True)
    suggested_priority = serializers.CharField(read_only=True, allow_blank=True)
    confidence = serializers.FloatField(read_only=True, allow_null=True)
    inconsistency_flag = serializers.BooleanField(read_only=True)
    justification = serializers.CharField(read_only=True, allow_blank=True)
    model_name = serializers.CharField(read_only=True, allow_blank=True)
    created_at = serializers.DateTimeField(read_only=True)


class OperatorReportDetailSerializer(OperatorReportListSerializer):
    description = serializers.CharField(read_only=True)
    latitude = serializers.SerializerMethodField()
    longitude = serializers.SerializerMethodField()
    photo_signed_url = serializers.SerializerMethodField()
    confirmation_count = serializers.IntegerField(read_only=True)
    ai_classification = serializers.SerializerMethodField()

    def get_latitude(self, report) -> float:
        return report.location.y

    def get_longitude(self, report) -> float:
        return report.location.x

    def get_photo_signed_url(self, report) -> str | None:
        photos = report.operator_photos
        if not photos:
            return None
        return storage.create_signed_url(
            storage.reports_bucket(),
            photos[0].file_url,
            expires_in=OPERATOR_REPORT_PHOTO_SIGNED_URL_TTL_SECONDS,
        )

    def get_ai_classification(self, report):
        classifications = report.operator_ai_classifications
        if not classifications:
            return None
        return OperatorAiClassificationSerializer(classifications[0]).data
