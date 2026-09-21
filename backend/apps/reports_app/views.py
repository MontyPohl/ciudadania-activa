"""Endpoints privados de creación y consulta de reportes ciudadanos."""

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.enums import ReportStatus
from apps.core.permissions import IsVerifiedCitizen
from apps.core.services import storage
from apps.reports_app.models import Report, ReportConfirmation, ReportStatusHistory
from apps.reports_app.selectors import contextual_report_queryset, find_possible_duplicates
from apps.reports_app.serializers import (
    CitizenSummarySerializer,
    ConfirmReportInputSerializer,
    ConfirmReportResponseSerializer,
    ContextualReportDetailSerializer,
    MineQuerySerializer,
    PossibleDuplicatesQuerySerializer,
    ReportCreateInputSerializer,
    ReportCreateResponseSerializer,
    ReportStatusHistorySerializer,
    ReportSummarySerializer,
)
from apps.reports_app.services.confirmations import ConfirmationConflict, confirm_report
from apps.reports_app.services.photos import InvalidReportPhotoError
from apps.reports_app.services.reports import (
    DuplicatePhotoError,
    InvalidReportDataError,
    create_report,
)
from apps.rewards_app.enums import RewardTxStatus
from apps.rewards_app.models import RewardTransaction

REPORTS_UNAVAILABLE = "No se pudo guardar el reporte en este momento. Intentá más tarde."

CONFIRMATION_CONFLICT_MESSAGES = {
    "REPORT_NOT_ACTIVE": "El reporte no está activo.",
    "SELF_CONFIRMATION_NOT_ALLOWED": "No podés confirmar tu propio reporte.",
    "ALREADY_CONFIRMED": "Ya confirmaste este reporte.",
}


class _PrivateNoStoreMixin:
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response


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


class ConfirmReportView(APIView):
    permission_classes = [IsVerifiedCitizen]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "report_confirm"

    def post(self, request, report_id):
        request_serializer = ConfirmReportInputSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        try:
            result = confirm_report(report_id=report_id, citizen=request.user)
        except Report.DoesNotExist as exc:
            raise Http404 from exc
        except ConfirmationConflict as exc:
            return Response(
                {
                    "detail": CONFIRMATION_CONFLICT_MESSAGES[exc.code],
                    "code": exc.code,
                },
                status=status.HTTP_409_CONFLICT,
            )

        payload = {
            "report_id": result.confirmation.report_id,
            "confirmed_by_me": True,
            "confirmation_count": result.confirmation_count,
            "reward": {
                "impact_points": result.reward.impact_delta,
                "status": result.reward.status,
            },
        }
        return Response(
            ConfirmReportResponseSerializer(payload).data,
            status=status.HTTP_201_CREATED,
        )


class ContextualReportDetailView(_PrivateNoStoreMixin, RetrieveAPIView):
    permission_classes = [IsVerifiedCitizen]
    serializer_class = ContextualReportDetailSerializer
    lookup_url_kwarg = "report_id"

    def get_queryset(self):
        return contextual_report_queryset(citizen=self.request.user)


class ReportHistoryView(_PrivateNoStoreMixin, ListAPIView):
    permission_classes = [IsVerifiedCitizen]
    serializer_class = ReportStatusHistorySerializer

    def get_queryset(self):
        report = get_object_or_404(Report, pk=self.kwargs["report_id"], citizen=self.request.user)
        return ReportStatusHistory.objects.filter(report=report).order_by("-created_at", "-id")

    def list(self, request, *args, **kwargs):
        query = MineQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return super().list(request, *args, **kwargs)


class CitizenSummaryView(APIView):
    permission_classes = [IsVerifiedCitizen]

    def get(self, request):
        status_counts = dict(
            Report.objects.filter(citizen=request.user)
            .order_by()
            .values_list("status")
            .annotate(total=Count("id"))
        )
        reports_by_status = {
            report_status: status_counts.get(report_status, 0)
            for report_status in ReportStatus.values
        }
        confirmations_performed = ReportConfirmation.objects.filter(citizen=request.user).count()
        point_totals = RewardTransaction.objects.filter(user=request.user).aggregate(
            pending=Coalesce(
                Sum("impact_delta", filter=Q(status=RewardTxStatus.PENDIENTE)),
                0,
            ),
            accredited=Coalesce(
                Sum("impact_delta", filter=Q(status=RewardTxStatus.ACREDITADO)),
                0,
            ),
        )
        payload = {
            "reports_by_status": reports_by_status,
            "confirmations_performed": confirmations_performed,
            "points": point_totals,
        }
        return Response(CitizenSummarySerializer(payload).data)
