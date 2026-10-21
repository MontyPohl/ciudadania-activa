"""Proxies públicos revocables; nunca aceptan claves ni tokens del cliente."""

from django.http import HttpResponse
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.services import storage
from apps.core.throttling import FailOpenScopedRateThrottle
from apps.map_app.selectors import PHOTO_STATUSES, RESOLVED_STATUSES
from apps.reports_app.enums import MediaType
from apps.reports_app.models import ReportMedia, ResolutionEvidence


class _PublicMediaView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [FailOpenScopedRateThrottle]
    throttle_scope = "public_report_media"

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    def object_key(self, report_id, **kwargs):
        raise NotImplementedError

    def get(self, request, report_id, **kwargs):
        try:
            key = self.object_key(report_id, **kwargs)
            if not key or not key.lower().endswith((".jpg", ".jpeg", ".png")):
                raise NotFound("No se encontró la imagen.")
            content = storage.download_private(storage.reports_bucket(), key)
        except storage.StorageObjectNotFoundError:
            raise NotFound("No se encontró la imagen.") from None
        except storage.StorageError:
            return Response({"detail": "No se pudo obtener la imagen en este momento."}, status=503)
        content_type = "image/png" if key.lower().endswith(".png") else "image/jpeg"
        return HttpResponse(content, content_type=content_type)


class PublicReportPhotoView(_PublicMediaView):
    def object_key(self, report_id, **kwargs):
        return (
            ReportMedia.objects.filter(
                report_id=report_id, report__status__in=PHOTO_STATUSES, media_type=MediaType.FOTO
            )
            .order_by("created_at", "id")
            .values_list("file_url", flat=True)
            .first()
        )


class PublicReportEvidenceView(_PublicMediaView):
    def object_key(self, report_id, evidence_id):
        return (
            ResolutionEvidence.objects.filter(
                pk=evidence_id, report_id=report_id, report__status__in=RESOLVED_STATUSES
            )
            .values_list("evidence_url", flat=True)
            .first()
        )
