"""Contrato HTTP de la reversión administrativa de reportes."""

from rest_framework import serializers

from apps.reports_app.serializers import StrictInputMixin
from apps.reports_app.services import reversal


class StrictPlainTextField(serializers.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class AdminReportReversalInputSerializer(StrictInputMixin, serializers.Serializer):
    reason = StrictPlainTextField(trim_whitespace=False)
    cycle = serializers.IntegerField(min_value=1)

    def validate_reason(self, value):
        try:
            return reversal.normalize_reason(value)
        except reversal.InvalidReversalReason as exc:
            raise serializers.ValidationError(str(exc)) from exc


class ReversalInstitutionSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    slug = serializers.SlugField(read_only=True)


class AdminReportReversalResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(read_only=True)
    cycle = serializers.IntegerField(source="resolution_cycle", read_only=True)
    institution = ReversalInstitutionSerializer(read_only=True)
    validated_at = serializers.DateTimeField(read_only=True, allow_null=True)
    resolved_at = serializers.DateTimeField(read_only=True, allow_null=True)
    closed_at = serializers.DateTimeField(read_only=True, allow_null=True)
