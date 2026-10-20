"""Entrada estricta de las métricas institucionales y públicas."""

from rest_framework import serializers

from apps.analytics_app.periods import parse_period
from apps.reports_app.serializers import StrictInputMixin


class OperatorMetricsQuerySerializer(StrictInputMixin, serializers.Serializer):
    institution_id = serializers.UUIDField(required=False)
    period = serializers.CharField(required=False, trim_whitespace=False)

    def validate_period(self, value):
        try:
            return parse_period(value, now=self.context.get("now"))
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class AdminMetricsQuerySerializer(OperatorMetricsQuerySerializer):
    pass


class PublicMetricsQuerySerializer(StrictInputMixin, serializers.Serializer):
    pass
