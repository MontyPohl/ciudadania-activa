"""Contratos explícitos del listado administrativo de auditoría."""

from datetime import date

from rest_framework import serializers

from apps.core.enums import ActorType
from apps.reports_app.serializers import StrictInputMixin


class AuditLogQuerySerializer(StrictInputMixin, serializers.Serializer):
    action = serializers.CharField(max_length=120, required=False)
    actor_type = serializers.ChoiceField(choices=ActorType.values, required=False)
    actor_user_id = serializers.UUIDField(required=False)
    report_id = serializers.UUIDField(required=False)
    institution_id = serializers.UUIDField(required=False)
    created_from = serializers.DateField(required=False)
    created_to = serializers.DateField(required=False)
    page = serializers.IntegerField(min_value=1, required=False)

    def validate_created_to(self, value):
        if value == date.max:
            raise serializers.ValidationError(
                "Debe permitir calcular el límite exclusivo del día siguiente."
            )
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        created_from = attrs.get("created_from")
        created_to = attrs.get("created_to")
        if created_from is not None and created_to is not None and created_from > created_to:
            raise serializers.ValidationError(
                {"created_to": ["Debe ser igual o posterior a created_from."]}
            )
        return attrs


class AuditLogOutputSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    actor_user_id = serializers.UUIDField(read_only=True, allow_null=True)
    actor_type = serializers.CharField(read_only=True)
    action = serializers.CharField(read_only=True)
    payload = serializers.JSONField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
