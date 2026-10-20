"""Métricas del contexto institucional del operador."""

from django.conf import settings
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.admin_app.selectors import resolve_operator_institution
from apps.analytics_app import periods, selectors
from apps.analytics_app.cache import cached
from apps.analytics_app.serializers import OperatorMetricsQuerySerializer
from apps.core.permissions import IsInstitutionOperator


class OperatorMetricsView(APIView):
    permission_classes = [IsInstitutionOperator]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "dashboard_metrics"

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response

    def get(self, request):
        now = timezone.now()
        query = OperatorMetricsQuerySerializer(data=request.query_params, context={"now": now})
        query.is_valid(raise_exception=True)
        institution = resolve_operator_institution(
            operator=request.user, requested_id=query.validated_data.get("institution_id")
        )
        period = query.validated_data.get("period", periods.current_period(now))
        value = cached(
            f"metrics:v1:inst:{institution.pk}:{periods.format_period(period)}",
            settings.DASHBOARD_METRICS_CACHE_TTL_SECONDS,
            lambda: selectors.institution_metrics(institution=institution, period=period, now=now),
        )
        return Response(value)
