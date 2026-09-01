"""Endpoints institucionales de solo lectura de Sprint 9-A."""

from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response

from apps.admin_app.models import InstitutionOperator
from apps.core.permissions import IsInstitutionOperator
from apps.core.services import storage
from apps.reports_app.operator_serializers import (
    OperatorReportContextQuerySerializer,
    OperatorReportDetailSerializer,
    OperatorReportListQuerySerializer,
    OperatorReportListSerializer,
)
from apps.reports_app.selectors import operator_report_detail, operator_report_list

INSTITUTION_CONTEXT_FORBIDDEN = "No tenés acceso al contexto institucional solicitado."
INSTITUTION_CONTEXT_REQUIRED = (
    "Este campo es obligatorio cuando el operador tiene varias instituciones activas."
)
REPORT_PHOTO_UNAVAILABLE = "No se pudo obtener la fotografía en este momento."


class _OperatorInstitutionContextMixin:
    # ``IsOperatorOfReportInstitution`` no se usa aquí: su contrato general deniega
    # objetos sin institución, mientras S9-A autoriza el fallback de categoría solo
    # para estos GET. El queryset efectivo aplica después el scope por objeto.
    permission_classes = [IsInstitutionOperator]
    query_serializer_class = OperatorReportContextQuerySerializer

    def _params(self):
        if not hasattr(self, "_validated_operator_params"):
            serializer = self.query_serializer_class(data=self.request.query_params)
            serializer.is_valid(raise_exception=True)
            self._validated_operator_params = serializer.validated_data
        return self._validated_operator_params

    def _institution(self):
        if not hasattr(self, "_operator_institution"):
            self._operator_institution = _resolve_operator_institution(
                operator=self.request.user,
                requested_id=self._params().get("institution_id"),
            )
        return self._operator_institution

    @staticmethod
    def _private_no_store(response):
        response["Cache-Control"] = "private, no-store"
        return response


class OperatorReportListView(_OperatorInstitutionContextMixin, ListAPIView):
    serializer_class = OperatorReportListSerializer
    query_serializer_class = OperatorReportListQuerySerializer

    def get_queryset(self):
        return operator_report_list(
            institution=self._institution(),
            params=self._params(),
        )

    def list(self, request, *args, **kwargs):
        return self._private_no_store(super().list(request, *args, **kwargs))


class OperatorReportDetailView(_OperatorInstitutionContextMixin, RetrieveAPIView):
    serializer_class = OperatorReportDetailSerializer
    lookup_url_kwarg = "report_id"

    def get_queryset(self):
        return operator_report_detail(institution=self._institution())

    def retrieve(self, request, *args, **kwargs):
        try:
            response = super().retrieve(request, *args, **kwargs)
        except storage.StorageError:
            response = Response(
                {"detail": REPORT_PHOTO_UNAVAILABLE},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return self._private_no_store(response)


def _resolve_operator_institution(*, operator, requested_id):
    memberships = list(
        InstitutionOperator.objects.filter(user=operator, active=True)
        .select_related("institution")
        .order_by("institution__name", "institution_id")
    )

    if requested_id is not None:
        for membership in memberships:
            if membership.institution_id == requested_id:
                return membership.institution
        raise PermissionDenied(INSTITUTION_CONTEXT_FORBIDDEN)

    if len(memberships) == 1:
        return memberships[0].institution
    if len(memberships) > 1:
        raise ValidationError({"institution_id": [INSTITUTION_CONTEXT_REQUIRED]})
    raise PermissionDenied(INSTITUTION_CONTEXT_FORBIDDEN)
