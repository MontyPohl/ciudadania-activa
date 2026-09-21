"""Listado privado, paginado y filtrable de auditoría."""

from datetime import datetime, time, timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated

from apps.admin_app.audit_serializers import AuditLogOutputSerializer, AuditLogQuerySerializer
from apps.admin_app.models import AuditLog
from apps.core.permissions import IsAdmin


class AuditLogPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = None


class AdminAuditLogListView(ListAPIView):
    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = AuditLogOutputSerializer
    pagination_class = AuditLogPagination

    def _params(self):
        if not hasattr(self, "_validated_params"):
            serializer = AuditLogQuerySerializer(data=self.request.query_params)
            serializer.is_valid(raise_exception=True)
            self._validated_params = serializer.validated_data
        return self._validated_params

    def get_queryset(self):
        params = self._params()
        queryset = AuditLog.objects.all()
        if action := params.get("action"):
            queryset = queryset.filter(action=action)
        if actor_type := params.get("actor_type"):
            queryset = queryset.filter(actor_type=actor_type)
        if actor_user_id := params.get("actor_user_id"):
            queryset = queryset.filter(actor_user_id=actor_user_id)
        if report_id := params.get("report_id"):
            queryset = queryset.filter(payload__report_id=str(report_id))
        if institution_id := params.get("institution_id"):
            queryset = queryset.filter(payload__institution_id=str(institution_id))

        current_timezone = timezone.get_current_timezone()
        if created_from := params.get("created_from"):
            queryset = queryset.filter(
                created_at__gte=timezone.make_aware(
                    datetime.combine(created_from, time.min),
                    current_timezone,
                )
            )
        if created_to := params.get("created_to"):
            next_date = created_to + timedelta(days=1)
            queryset = queryset.filter(
                created_at__lt=timezone.make_aware(
                    datetime.combine(next_date, time.min),
                    current_timezone,
                )
            )
        return queryset.order_by("-created_at", "-id")

    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        if isinstance(exc, NotAuthenticated | AuthenticationFailed):
            response.data = {
                "detail": "Se requiere autenticación.",
                "code": "AUTHENTICATION_REQUIRED",
            }
        elif isinstance(exc, PermissionDenied):
            response.data = {
                "detail": "Se requiere rol administrador.",
                "code": "ADMIN_REQUIRED",
            }
        elif isinstance(exc, ValidationError | NotFound):
            response.data = {"detail": response.data, "code": "INVALID_REQUEST"}
            response.status_code = status.HTTP_400_BAD_REQUEST
        return response

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response
