from django.urls import path

from apps.reports_app.operator_views import OperatorReportDetailView, OperatorReportListView

urlpatterns = [
    path("reports/", OperatorReportListView.as_view(), name="operator-report-list"),
    path(
        "reports/<uuid:report_id>/",
        OperatorReportDetailView.as_view(),
        name="operator-report-detail",
    ),
]
