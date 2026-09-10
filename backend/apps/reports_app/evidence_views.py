"""Lecturas privadas de evidencia para autor y administrador global."""

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdmin, IsCitizen
from apps.core.services import storage
from apps.reports_app.models import Report
from apps.reports_app.operator_serializers import ResolutionEvidenceOutputSerializer

REPORT_NOT_FOUND = "No se encontró el reporte solicitado."
EVIDENCE_UNAVAILABLE = "No se pudo obtener la evidencia en este momento."


class _EvidenceListBase(APIView):
    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        response["Cache-Control"] = "private, no-store"
        return response

    def report_queryset(self, request):
        raise NotImplementedError

    def get(self, request, report_id):
        try:
            report = self.report_queryset(request).get(pk=report_id)
            evidences = report.resolution_evidences.order_by("-created_at", "-id")
            response = Response(ResolutionEvidenceOutputSerializer(evidences, many=True).data)
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
    permission_classes = [IsAuthenticated, IsCitizen]

    def report_queryset(self, request):
        return Report.objects.filter(citizen=request.user)


class AdminReportEvidenceView(_EvidenceListBase):
    permission_classes = [IsAuthenticated, IsAdmin]

    def report_queryset(self, request):
        return Report.objects.all()
