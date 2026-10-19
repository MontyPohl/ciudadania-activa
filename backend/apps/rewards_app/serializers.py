"""Contratos explícitos de consulta y canje de recompensas."""

from rest_framework import serializers

from apps.reports_app.serializers import StrictInputMixin
from apps.rewards_app import periods
from apps.rewards_app.enums import RewardEventType


class RedeemInputSerializer(StrictInputMixin, serializers.Serializer):
    """Contrato vacío: el ciudadano y el costo nunca vienen del body."""


class InstitutionRefSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    slug = serializers.CharField(read_only=True)


class RewardRefSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)


class RewardPublicSerializer(RewardRefSerializer):
    description = serializers.CharField(read_only=True)
    credit_cost = serializers.IntegerField(read_only=True)
    simulated = serializers.BooleanField(read_only=True)


class RedemptionPeriodSerializer(serializers.Serializer):
    period = serializers.SerializerMethodField()

    def get_period(self, obj):
        return periods.format_period(obj.period_month)


class RedemptionSerializer(RedemptionPeriodSerializer):
    id = serializers.UUIDField(read_only=True)
    code = serializers.CharField(read_only=True)
    credits_spent = serializers.IntegerField(read_only=True)
    simulated = serializers.BooleanField(source="reward.simulated", read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    reward = RewardRefSerializer(read_only=True)
    institution = InstitutionRefSerializer(read_only=True)


class ImpactSummarySerializer(serializers.Serializer):
    historical = serializers.IntegerField(read_only=True)
    current_month = serializers.IntegerField(read_only=True)
    pending = serializers.IntegerField(read_only=True)


class InstitutionBalanceSerializer(serializers.Serializer):
    institution = InstitutionRefSerializer(read_only=True)
    balance = serializers.IntegerField(read_only=True)


class SummarySerializer(serializers.Serializer):
    impact = ImpactSummarySerializer(read_only=True)
    credits_by_institution = InstitutionBalanceSerializer(many=True, read_only=True)
    period = serializers.CharField(read_only=True)


class MovementSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    event_type = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    impact_delta = serializers.IntegerField(read_only=True)
    credit_delta = serializers.IntegerField(read_only=True)
    institution = InstitutionRefSerializer(read_only=True, allow_null=True)
    report_id = serializers.SerializerMethodField()
    redemption_id = serializers.UUIDField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    credited_at = serializers.DateTimeField(read_only=True, allow_null=True)
    voided_at = serializers.DateTimeField(read_only=True, allow_null=True)
    reversed_at = serializers.DateTimeField(read_only=True, allow_null=True)

    def get_report_id(self, obj):
        if obj.event_type == RewardEventType.CONFIRMATION_CREATED and obj.confirmation_id:
            return str(obj.confirmation.report_id)
        return str(obj.report_id) if obj.report_id is not None else None


class RedemptionThisMonthSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    code = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class CatalogEntrySerializer(serializers.Serializer):
    reward = RewardPublicSerializer(read_only=True)
    institution = InstitutionRefSerializer(read_only=True)
    balance = serializers.IntegerField(read_only=True)
    can_redeem = serializers.BooleanField(read_only=True)
    blocked_reason = serializers.CharField(read_only=True, allow_null=True)
    missing_credits = serializers.IntegerField(read_only=True)
    redemption_this_month = RedemptionThisMonthSerializer(read_only=True, allow_null=True)


class RedeemResponseSerializer(serializers.Serializer):
    redemption = RedemptionSerializer(read_only=True)
    balance_after = serializers.IntegerField(read_only=True)
