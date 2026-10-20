"""Métricas consolidadas o de una institución para administración."""

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.admin_app.models import Institution
from apps.analytics_app import periods, selectors
from apps.analytics_app.cache import cached
from apps.analytics_app.serializers import AdminMetricsQuerySerializer
from apps.core.permissions import IsAdmin


class AdminMetricsView(APIView):
    permission_classes = [IsAdmin]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "dashboard_metrics"

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response

    def get(self, request):
        now = timezone.now()
        query = AdminMetricsQuerySerializer(data=request.query_params, context={"now": now})
        query.is_valid(raise_exception=True)
        period = query.validated_data.get("period", periods.current_period(now))
        requested_id = query.validated_data.get("institution_id")
        if requested_id is None:
            key = f"metrics:v1:all:{periods.format_period(period)}"

            def compute():
                return selectors.consolidated_metrics(period=period, now=now)
        else:
            institution = Institution.objects.filter(pk=requested_id).first()
            if institution is None:
                raise NotFound("No se encontró la institución.")
            key = f"metrics:v1:inst:{institution.pk}:{periods.format_period(period)}"

            def compute():
                return selectors.institution_metrics(
                    institution=institution, period=period, now=now
                )

        return Response(cached(key, settings.DASHBOARD_METRICS_CACHE_TTL_SECONDS, compute))
