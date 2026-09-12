"""Endpoint administrativo para revertir la validación de un reporte."""

from rest_framework import status
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    ParseError,
    ValidationError,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdmin
from apps.reports_app.operator_serializers import EmptyStrictQuerySerializer
from apps.reports_app.reversal_serializers import (
    AdminReportReversalInputSerializer,
    AdminReportReversalResponseSerializer,
)
from apps.reports_app.services import reversal


class AdminReportReversalView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    @staticmethod
    def _private(response):
        response["Cache-Control"] = "private, no-store"
        return response

    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        if isinstance(exc, NotAuthenticated | AuthenticationFailed):
            response.data = {
                "detail": "Se requiere autenticación.",
                "code": "AUTHENTICATION_REQUIRED",
            }
        elif response.status_code == status.HTTP_403_FORBIDDEN:
            response.data = {
                "detail": "Se requiere rol administrador.",
                "code": "ADMIN_REQUIRED",
            }
        elif isinstance(exc, ValidationError | ParseError):
            response.data = {"detail": response.data, "code": "INVALID_REQUEST"}
        return self._private(response)

    def post(self, request, report_id):
        query = EmptyStrictQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        serializer = AdminReportReversalInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = reversal.revert_report_validation(
                report_id=report_id,
                admin_id=request.user.pk,
                expected_cycle=serializer.validated_data["cycle"],
                reason=serializer.validated_data["reason"],
            )
            response = Response(AdminReportReversalResponseSerializer(result.report).data)
        except reversal.AdminRequired:
            response = Response(
                {"detail": "Se requiere rol administrador.", "code": "ADMIN_REQUIRED"},
                status=status.HTTP_403_FORBIDDEN,
            )
        except reversal.ReportNotFound:
            response = Response(
                {"detail": "No se encontró el reporte solicitado.", "code": "REPORT_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except reversal.InvalidReversalReason as exc:
            response = Response(
                {"detail": str(exc), "code": exc.code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except (reversal.StaleReportCycle, reversal.ReportStateNotAllowed) as exc:
            response = Response(
                {"detail": str(exc) or exc.code, "code": exc.code},
                status=status.HTTP_409_CONFLICT,
            )
        return self._private(response)
