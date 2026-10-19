from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.reports_app.serializers import StrictInputMixin
from apps.rewards_app import periods, ranking


class RankingQuerySerializer(StrictInputMixin, serializers.Serializer):
    """El ranking del mes en curso no admite parámetros."""


class MonthlyRankingView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_ranking"

    def get(self, request):
        RankingQuerySerializer(data=request.query_params).is_valid(raise_exception=True)
        now = timezone.now()
        viewer_id = request.user.pk if request.user.is_authenticated else None
        entries = ranking.resolve_entries(ranking.cached_monthly_top(now=now), viewer_id=viewer_id)
        return Response(
            {"period": periods.format_period(periods.current_period(now)), "entries": entries}
        )

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response
