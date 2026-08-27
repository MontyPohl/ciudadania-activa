"""Endpoints anónimos y de solo lectura del mapa público."""

import json

from django.http import QueryDict
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.admin_app.models import Category
from apps.map_app.cache import (
    build_public_list_cache_key,
    get_public_list,
    set_public_list,
)
from apps.map_app.selectors import public_report_detail_queryset, public_reports
from apps.map_app.serializers import (
    PublicCategorySerializer,
    PublicReportQuerySerializer,
    PublicReportSerializer,
)


class _PublicReportPagination(PageNumberPagination):
    """Paginación DRF global con enlaces construidos desde filtros canónicos."""

    public_params = None

    def get_next_link(self):
        if not self.page.has_next():
            return None
        return self._link(self.page.next_page_number())

    def get_previous_link(self):
        if not self.page.has_previous():
            return None
        return self._link(self.page.previous_page_number())

    def _link(self, page_number: int) -> str:
        query = QueryDict(mutable=True)
        query["bbox"] = self.public_params["bbox"].as_public_query_value()
        query["state_filter"] = self.public_params["state_filter"]
        category = self.public_params.get("category")
        if category is not None:
            query["category"] = category.slug
        if page_number != 1:
            query["page"] = page_number
        return self.request.build_absolute_uri(f"{self.request.path}?{query.urlencode(safe=',')}")


class PublicReportListView(ListAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_map"
    serializer_class = PublicReportSerializer
    pagination_class = _PublicReportPagination

    def _params(self):
        if not hasattr(self, "_validated_public_params"):
            serializer = PublicReportQuerySerializer(data=self.request.query_params)
            serializer.is_valid(raise_exception=True)
            self._validated_public_params = serializer.validated_data
        return self._validated_public_params

    def get_queryset(self):
        return public_reports(self._params())

    def list(self, request, *args, **kwargs):
        params = self._params()
        cache_key = build_public_list_cache_key(request, params)
        cached = get_public_list(cache_key)
        if cached is not None:
            return Response(cached)

        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        self.paginator.public_params = params
        serialized = self.get_serializer(page, many=True).data
        response = self.get_paginated_response(serialized)

        # El round-trip JSON deja solo diccionarios/listas/primitivos del contrato
        # público; nunca entran modelos, querysets ni la geometría exacta.
        payload = json.loads(json.dumps(response.data))
        set_public_list(cache_key, payload)
        return Response(payload)


class PublicReportDetailView(RetrieveAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_map"
    serializer_class = PublicReportSerializer
    lookup_url_kwarg = "report_id"
    queryset = public_report_detail_queryset()


class PublicCategoryListView(ListAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_map"
    serializer_class = PublicCategorySerializer
    pagination_class = None
    queryset = Category.objects.filter(is_active=True).order_by("name", "id")
