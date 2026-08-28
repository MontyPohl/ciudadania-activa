"""Endpoints privados de creación y consulta de reportes ciudadanos."""

from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.permissions import IsVerifiedCitizen
from apps.core.services import storage
from apps.reports_app.selectors import find_possible_duplicates
from apps.reports_app.serializers import (
    MineQuerySerializer,
    PossibleDuplicatesQuerySerializer,
    ReportCreateInputSerializer,
    ReportCreateResponseSerializer,
    ReportSummarySerializer,
)
from apps.reports_app.services.photos import InvalidReportPhotoError
from apps.reports_app.services.reports import (
    DuplicatePhotoError,
    InvalidReportDataError,
    create_report,
)

REPORTS_UNAVAILABLE = "No se pudo guardar el reporte en este momento. Intentá más tarde."


class ReportCreateView(APIView):
    permission_classes = [IsVerifiedCitizen]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "report_create"
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        serializer = ReportCreateInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            result = create_report(
                citizen=request.user,
                category=data["category"],
                description=data["description"],
                photo=data["photo"],
                latitude=data["lat"],
                longitude=data["lng"],
            )
        except InvalidReportPhotoError as exc:
            return Response(
                {"detail": {"photo": [str(exc)]}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except InvalidReportDataError as exc:
            return Response(
                {"detail": {exc.field: [str(exc)]}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except DuplicatePhotoError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except storage.StorageError:
            return Response(
                {"detail": REPORTS_UNAVAILABLE},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        payload = {
            "id": result.report.id,
            "status": result.report.status,
            "reward": {
                "impact_points": result.reward.impact_delta,
                "credits": result.reward.credit_delta,
                "status": result.reward.status,
            },
        }
        response = ReportCreateResponseSerializer(payload)
        return Response(response.data, status=status.HTTP_201_CREATED)


class PossibleDuplicatesView(APIView):
    permission_classes = [IsVerifiedCitizen]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "possible_duplicates"

    def get(self, request):
        serializer = PossibleDuplicatesQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        reports = find_possible_duplicates(
            category=data["category"],
            latitude=data["lat"],
            longitude=data["lng"],
        )
        response = Response(ReportSummarySerializer(reports, many=True).data)
        response["Cache-Control"] = "private, no-store"
        return response


class MyReportsView(ListAPIView):
    permission_classes = [IsVerifiedCitizen]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "reports_mine"
    serializer_class = ReportSummarySerializer

    def get_queryset(self):
        return (
            self.request.user.reports.select_related("category", "institution")
            .all()
            .order_by("-created_at", "-id")
        )

    def list(self, request, *args, **kwargs):
        query = MineQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return super().list(request, *args, **kwargs)
