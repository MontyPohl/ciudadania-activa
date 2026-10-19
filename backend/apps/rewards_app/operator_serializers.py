"""Contratos de administración institucional con entrada estricta."""

import unicodedata

from rest_framework import serializers

from apps.reports_app.serializers import StrictInputMixin
from apps.rewards_app.display import citizen_short_name
from apps.rewards_app.serializers import (
    InstitutionRefSerializer,
    RedemptionPeriodSerializer,
    RewardRefSerializer,
)


class OperatorRewardContextQuerySerializer(StrictInputMixin, serializers.Serializer):
    institution_id = serializers.UUIDField(required=False)


class OperatorRedemptionListQuerySerializer(OperatorRewardContextQuerySerializer):
    page = serializers.IntegerField(min_value=1, required=False)


class OperatorRewardInputSerializer(StrictInputMixin, serializers.Serializer):
    name = serializers.CharField(min_length=3, max_length=160, trim_whitespace=True)
    description = serializers.CharField(max_length=1000, allow_blank=True, default="")
    active = serializers.BooleanField(default=True)
    credit_cost = serializers.IntegerField(read_only=True)
    simulated = serializers.BooleanField(read_only=True)

    def validate_name(self, value):
        if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
            raise serializers.ValidationError("El nombre no puede contener caracteres de control.")
        return value


class OperatorRewardPatchInputSerializer(OperatorRewardInputSerializer):
    """La vista lo instancia con partial=True para preservar los campos omitidos."""


class OperatorRewardSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    institution = InstitutionRefSerializer(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    active = serializers.BooleanField(read_only=True)
    credit_cost = serializers.IntegerField(read_only=True)
    simulated = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class OperatorRewardContextSerializer(serializers.Serializer):
    institution = InstitutionRefSerializer(read_only=True)
    reward = OperatorRewardSerializer(read_only=True, allow_null=True)


class OperatorRedemptionSerializer(RedemptionPeriodSerializer):
    id = serializers.UUIDField(read_only=True)
    code = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    reward = RewardRefSerializer(read_only=True)
    citizen_display = serializers.SerializerMethodField()

    def get_citizen_display(self, obj):
        return citizen_short_name(obj.user)
