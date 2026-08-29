"""Contratos explícitos y listas blancas del API de reportes."""

from __future__ import annotations

import math

from rest_framework import serializers

from apps.admin_app.models import Category
from apps.map_app.serializers import PublicReportDetailSerializer
from apps.reports_app.services.confirmations import get_confirmation_eligibility
from apps.reports_app.services.reports import (
    InvalidReportDataError,
    validate_report_description,
)


class StrictInputMixin:
    """Rechaza claves desconocidas y valores repetidos en QueryDict/multipart."""

    def to_internal_value(self, data):
        allowed = set(self.fields)
        errors = {}
        for key in data.keys():
            if key not in allowed:
                errors[key] = ["Este campo no está permitido."]

        if hasattr(data, "lists"):
            for key, values in data.lists():
                if key in allowed and len(values) != 1:
                    errors[key] = ["Este campo debe aparecer exactamente una vez."]

        if errors:
            raise serializers.ValidationError(errors)
        return super().to_internal_value(data)


class FiniteCoordinateField(serializers.FloatField):
    def __init__(self, *, minimum: float, maximum: float, **kwargs):
        self.minimum = minimum
        self.maximum = maximum
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        if isinstance(data, bool):
            self.fail("invalid")
        value = super().to_internal_value(data)
        if not math.isfinite(value):
            raise serializers.ValidationError("Debe ser un número finito.")
        if not self.minimum <= value <= self.maximum:
            raise serializers.ValidationError(
                f"Debe estar entre {self.minimum:g} y {self.maximum:g}."
            )
        return value


class ReportCreateInputSerializer(StrictInputMixin, serializers.Serializer):
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.filter(is_active=True),
    )
    description = serializers.CharField(
        allow_blank=True,
        trim_whitespace=False,
    )
    photo = serializers.FileField(allow_empty_file=False)
    lat = FiniteCoordinateField(minimum=-90, maximum=90)
    lng = FiniteCoordinateField(minimum=-180, maximum=180)

    def validate_description(self, value):
        try:
            return validate_report_description(value)
        except InvalidReportDataError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class PossibleDuplicatesQuerySerializer(StrictInputMixin, serializers.Serializer):
    lat = FiniteCoordinateField(minimum=-90, maximum=90)
    lng = FiniteCoordinateField(minimum=-180, maximum=180)
    category_id = serializers.PrimaryKeyRelatedField(
        source="category",
        queryset=Category.objects.filter(is_active=True),
    )


class MineQuerySerializer(StrictInputMixin, serializers.Serializer):
    page = serializers.IntegerField(min_value=1, required=False)


class ReportCategorySummarySerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    slug = serializers.SlugField(read_only=True)


class ReportInstitutionSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    slug = serializers.SlugField(read_only=True)


class ReportSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    category = ReportCategorySummarySerializer(read_only=True)
    institution = ReportInstitutionSummarySerializer(read_only=True, allow_null=True)
    description = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    priority = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class CreatedRewardSerializer(serializers.Serializer):
    impact_points = serializers.IntegerField(read_only=True)
    credits = serializers.IntegerField(read_only=True)
    status = serializers.CharField(read_only=True)


class ReportCreateResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(read_only=True)
    reward = CreatedRewardSerializer(read_only=True)


class ConfirmReportInputSerializer(StrictInputMixin, serializers.Serializer):
    """Contrato intencionalmente vacío: el reporte y ciudadano vienen de la ruta/auth."""


class ConfirmationRewardSerializer(serializers.Serializer):
    impact_points = serializers.IntegerField(read_only=True)
    status = serializers.CharField(read_only=True)


class ConfirmReportResponseSerializer(serializers.Serializer):
    report_id = serializers.UUIDField(read_only=True)
    confirmed_by_me = serializers.BooleanField(read_only=True)
    confirmation_count = serializers.IntegerField(read_only=True)
    reward = ConfirmationRewardSerializer(read_only=True)


class ConfirmationEligibilitySerializer(serializers.Serializer):
    can_confirm = serializers.BooleanField(read_only=True)
    code = serializers.CharField(read_only=True, allow_null=True)


class ContextualReportDetailSerializer(PublicReportDetailSerializer):
    confirmed_by_me = serializers.BooleanField(read_only=True)
    confirmation_eligibility = serializers.SerializerMethodField()

    def get_confirmation_eligibility(self, report):
        eligibility = get_confirmation_eligibility(
            report=report,
            citizen=self.context["request"].user,
            already_confirmed=report.confirmed_by_me,
        )
        return ConfirmationEligibilitySerializer(eligibility).data


class ReportStatusHistorySerializer(serializers.Serializer):
    previous_status = serializers.CharField(read_only=True)
    new_status = serializers.CharField(read_only=True)
    reason = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class CitizenSummarySerializer(serializers.Serializer):
    reports_by_status = serializers.DictField(
        child=serializers.IntegerField(),
        read_only=True,
    )
    confirmations_performed = serializers.IntegerField(read_only=True)
    points = serializers.DictField(
        child=serializers.IntegerField(),
        read_only=True,
    )
