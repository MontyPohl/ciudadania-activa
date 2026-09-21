"""Lecturas privadas de evidencia para autor, administrador y URL efímera."""

from django.core import signing
from django.http import HttpResponse
from rest_framework import status
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdmin
from apps.core.services import storage
from apps.reports_app.models import Report, ResolutionEvidence
from apps.reports_app.operator_serializers import (
    EmptyStrictQuerySerializer,
    ResolutionEvidenceOutputSerializer,
)
from apps.reports_app.services import evidence_access

REPORT_NOT_FOUND = "No se encontró el reporte solicitado."
EVIDENCE_UNAVAILABLE = "No se pudo obtener la evidencia en este momento."


class _EvidenceListBase(APIView):
    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        if isinstance(exc, NotAuthenticated | AuthenticationFailed):
            response.data = {
                "detail": "Se requiere autenticación.",
                "code": "AUTHENTICATION_REQUIRED",
            }
        elif isinstance(exc, PermissionDenied):
            response.data = {"detail": "Se requiere rol administrador.", "code": "ADMIN_REQUIRED"}
        elif isinstance(exc, ValidationError):
            response.data = {"detail": response.data, "code": "INVALID_REQUEST"}
        response["Cache-Control"] = "private, no-store"
        return response

    def report_queryset(self, request):
        raise NotImplementedError

    def get(self, request, report_id):
        query = EmptyStrictQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            report = self.report_queryset(request).get(pk=report_id)
            evidences = report.resolution_evidences.order_by("-created_at", "-id")
            response = Response(
                ResolutionEvidenceOutputSerializer(
                    evidences,
                    many=True,
                    context={"request": request},
                ).data
            )
        except Report.DoesNotExist:
            raise NotFound({"detail": REPORT_NOT_FOUND, "code": "REPORT_NOT_FOUND"}) from None
        except storage.StorageError:
            response = Response(
                {"detail": EVIDENCE_UNAVAILABLE, "code": "STORAGE_UNAVAILABLE"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        response["Cache-Control"] = "private, no-store"
        return response


class CitizenReportEvidenceView(_EvidenceListBase):
    permission_classes = [IsAuthenticated]

    def report_queryset(self, request):
        return Report.objects.filter(citizen=request.user)


class AdminReportEvidenceView(_EvidenceListBase):
    permission_classes = [IsAuthenticated, IsAdmin]

    def report_queryset(self, request):
        return Report.objects.all()


class ResolutionEvidenceDownloadView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, token):
        try:
            evidence_id = evidence_access.evidence_id_from_token(token)
            evidence = ResolutionEvidence.objects.only("evidence_url").get(pk=evidence_id)
            content = storage.download_private(storage.reports_bucket(), evidence.evidence_url)
        except (
            signing.BadSignature,
            signing.SignatureExpired,
            KeyError,
            ResolutionEvidence.DoesNotExist,
            storage.StorageObjectNotFoundError,
        ):
            response = Response(
                {"detail": "No se encontró la evidencia.", "code": "EVIDENCE_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except storage.StorageError:
            response = Response(
                {"detail": EVIDENCE_UNAVAILABLE, "code": "STORAGE_UNAVAILABLE"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        else:
            content_type = (
                "image/png" if evidence.evidence_url.lower().endswith(".png") else "image/jpeg"
            )
            response = HttpResponse(content, content_type=content_type)
        response["Cache-Control"] = "private, no-store"
        return response
