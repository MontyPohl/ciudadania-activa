"""Consultas propias y canje simulado del ciudadano verificado."""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.permissions import IsVerifiedCitizen
from apps.rewards_app import periods, selectors
from apps.rewards_app.models import Reward, RewardRedemption
from apps.rewards_app.serializers import (
    CatalogEntrySerializer,
    MovementSerializer,
    RedeemInputSerializer,
    RedeemResponseSerializer,
    RedemptionSerializer,
    SummarySerializer,
)
from apps.rewards_app.services import redemptions


class PrivateNoStoreMixin:
    """Evita guardar datos propios, incluso cuando la respuesta es un error."""

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response


class _CitizenRewardsMixin(PrivateNoStoreMixin):
    permission_classes = [IsVerifiedCitizen]


class RewardsSummaryView(_CitizenRewardsMixin, APIView):
    def get(self, request):
        now = timezone.now()
        payload = {
            "impact": selectors.impact_summary(user_id=request.user.pk, now=now),
            "credits_by_institution": selectors.institution_balances(user_id=request.user.pk),
            "period": periods.format_period(periods.current_period(now)),
        }
        return Response(SummarySerializer(payload).data)


class RewardsCatalogView(_CitizenRewardsMixin, APIView):
    def get(self, request):
        now = timezone.now()
        entries = selectors.catalog_for(user=request.user, now=now)
        return Response(
            {
                "period": periods.format_period(periods.current_period(now)),
                "results": CatalogEntrySerializer(entries, many=True).data,
            }
        )


class LedgerMovementsView(_CitizenRewardsMixin, ListAPIView):
    serializer_class = MovementSerializer

    def get_queryset(self):
        return selectors.ledger_movements(user_id=self.request.user.pk)


class RedemptionListView(_CitizenRewardsMixin, ListAPIView):
    serializer_class = RedemptionSerializer

    def get_queryset(self):
        return (
            RewardRedemption.objects.filter(user=self.request.user)
            .select_related("reward", "institution")
            .order_by("-created_at")
        )


class RedemptionDetailView(_CitizenRewardsMixin, RetrieveAPIView):
    serializer_class = RedemptionSerializer
    lookup_url_kwarg = "redemption_id"

    def get_queryset(self):
        return RewardRedemption.objects.filter(user=self.request.user).select_related(
            "reward", "institution"
        )


class RedeemView(_CitizenRewardsMixin, APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "reward_redeem"

    def post(self, request, reward_id):
        serializer = RedeemInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = redemptions.redeem(user_id=request.user.pk, reward_id=reward_id)
        except Reward.DoesNotExist as exc:
            raise NotFound("No se encontró la recompensa solicitada.") from exc
        except redemptions.NotVerifiedCitizen as exc:
            raise PermissionDenied(IsVerifiedCitizen.message) from exc
        except redemptions.RedemptionConflict as exc:
            return self._conflict_response(exc, reward_id)
        return Response(RedeemResponseSerializer(result).data, status=status.HTTP_201_CREATED)

    @staticmethod
    def _conflict_response(exc, reward_id):
        if exc.code == "REWARD_INACTIVE":
            payload = {"detail": "La recompensa no está disponible.", "code": exc.code}
        else:
            reward = get_object_or_404(Reward.objects.select_related("institution"), pk=reward_id)
            institution_name = reward.institution.name
            if exc.code == "MONTHLY_LIMIT_REACHED":
                payload = {
                    "detail": f"Ya canjeaste la recompensa de {institution_name} este mes.",
                    "code": exc.code,
                    "redemption_id": str(exc.extra["redemption_id"]),
                }
            else:
                missing = exc.extra["missing_credits"]
                payload = {
                    "detail": f"Te faltan {missing} créditos de {institution_name} para canjear.",
                    "code": exc.code,
                    "balance": exc.extra["balance"],
                    "missing_credits": missing,
                }
        return Response(payload, status=status.HTTP_409_CONFLICT)
