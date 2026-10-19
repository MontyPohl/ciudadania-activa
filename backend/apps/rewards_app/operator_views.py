"""Administración de recompensas y consulta de canjes de la propia institución."""

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.admin_app.selectors import resolve_operator_institution
from apps.core.permissions import IsInstitutionOperator
from apps.rewards_app.models import Reward, RewardRedemption
from apps.rewards_app.operator_serializers import (
    OperatorRedemptionListQuerySerializer,
    OperatorRedemptionSerializer,
    OperatorRewardContextQuerySerializer,
    OperatorRewardContextSerializer,
    OperatorRewardInputSerializer,
    OperatorRewardPatchInputSerializer,
    OperatorRewardSerializer,
)
from apps.rewards_app.services import rewards
from apps.rewards_app.views import PrivateNoStoreMixin

INSTITUTION_OPERATOR_REQUIRED = "Se requiere ser operador de la institución solicitada."


class _OperatorRewardsMixin(PrivateNoStoreMixin):
    permission_classes = [IsInstitutionOperator]
    query_serializer_class = OperatorRewardContextQuerySerializer

    def _institution(self):
        if not hasattr(self, "_operator_institution"):
            serializer = self.query_serializer_class(data=self.request.query_params)
            serializer.is_valid(raise_exception=True)
            self._operator_institution = resolve_operator_institution(
                operator=self.request.user,
                requested_id=serializer.validated_data.get("institution_id"),
            )
        return self._operator_institution

    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        if isinstance(exc, PermissionDenied):
            response.data = {
                "detail": INSTITUTION_OPERATOR_REQUIRED,
                "code": "INSTITUTION_OPERATOR_REQUIRED",
            }
        return response


class OperatorRewardView(_OperatorRewardsMixin, APIView):
    def get(self, request):
        institution = self._institution()
        reward = (
            Reward.objects.filter(institution=institution).select_related("institution").first()
        )
        return Response(
            OperatorRewardContextSerializer({"institution": institution, "reward": reward}).data
        )

    def post(self, request):
        institution = self._institution()
        serializer = OperatorRewardInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            reward = rewards.create_reward(
                operator=request.user, institution=institution, **serializer.validated_data
            )
        except rewards.RewardConflict as exc:
            return Response(
                {"code": exc.code, "reward_id": str(exc.reward_id)},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(OperatorRewardSerializer(reward).data, status=status.HTTP_201_CREATED)


class OperatorRewardDetailView(_OperatorRewardsMixin, APIView):
    def patch(self, request, reward_id):
        reward = get_object_or_404(Reward.objects.select_related("institution"), pk=reward_id)
        if reward.institution_id != self._institution().pk:
            raise PermissionDenied(INSTITUTION_OPERATOR_REQUIRED)
        serializer = OperatorRewardPatchInputSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        reward = rewards.update_reward(
            operator=request.user, reward=reward, changes=serializer.validated_data
        )
        return Response(OperatorRewardSerializer(reward).data)


class OperatorRedemptionListView(_OperatorRewardsMixin, ListAPIView):
    serializer_class = OperatorRedemptionSerializer
    query_serializer_class = OperatorRedemptionListQuerySerializer

    def get_queryset(self):
        return (
            RewardRedemption.objects.filter(institution=self._institution())
            .select_related("reward", "user")
            .order_by("-created_at")
        )
