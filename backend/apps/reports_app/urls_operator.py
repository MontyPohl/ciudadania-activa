from django.urls import path

from apps.reports_app.operator_views import (
    OperatorReportDetailView,
    OperatorReportListView,
    OperatorReportRejectView,
    OperatorReportValidateView,
)

urlpatterns = [
    path("reports/", OperatorReportListView.as_view(), name="operator-report-list"),
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
