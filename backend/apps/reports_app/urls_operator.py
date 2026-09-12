from django.urls import path

from apps.reports_app.operator_views import (
    OperatorReportDetailView,
    OperatorReportEvidenceView,
    OperatorReportHistoryView,
    OperatorReportListView,
    OperatorReportRejectView,
    OperatorReportStatusView,
    OperatorReportValidateView,
)

urlpatterns = [
    path("reports/", OperatorReportListView.as_view(), name="operator-report-list"),
    path(
        "reports/<uuid:report_id>/status/",
        OperatorReportStatusView.as_view(),
        name="operator-report-status",
    ),
    path(
        "reports/<uuid:report_id>/history/",
        OperatorReportHistoryView.as_view(),
        name="operator-report-history",
    ),
    path(
        "reports/<uuid:report_id>/evidence/",
        OperatorReportEvidenceView.as_view(),
        name="operator-report-evidence",
    ),
    path(
        "reports/<uuid:report_id>/validate/",
        OperatorReportValidateView.as_view(),
        name="operator-report-validate",
    ),
    path(
        "reports/<uuid:report_id>/reject/",
        OperatorReportRejectView.as_view(),
        name="operator-report-reject",
    ),
    path(
        "reports/<uuid:report_id>/",
        OperatorReportDetailView.as_view(),
        name="operator-report-detail",
    ),
]
