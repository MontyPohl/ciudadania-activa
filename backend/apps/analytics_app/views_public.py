"""Transparencia pública sin autenticación ni datos personales."""

from django.conf import settings
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analytics_app import selectors
from apps.analytics_app.cache import cached
from apps.analytics_app.selectors import LOCAL_TZ
from apps.analytics_app.serializers import PublicMetricsQuerySerializer
from apps.core.throttling import FailOpenScopedRateThrottle


class PublicMetricsView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [FailOpenScopedRateThrottle]
    throttle_scope = "public_metrics"

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        # Un error (400, 429) no debe quedar guardado en una caché compartida.
        response["Cache-Control"] = (
            "public, max-age=60" if response.status_code == 200 else "no-store"
        )
        return response

    def get(self, request):
        query = PublicMetricsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        now = timezone.now()
        value = cached(
            f"public-metrics:v1:{now.astimezone(LOCAL_TZ).date().isoformat()}",
            settings.PUBLIC_METRICS_CACHE_TTL_SECONDS,
            lambda: selectors.public_metrics(now=now),
        )
        return Response(value)
