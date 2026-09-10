from django.urls import path

from apps.reports_app.evidence_views import AdminReportEvidenceView

urlpatterns = [
    path(
        "reports/<uuid:report_id>/evidence/",
        AdminReportEvidenceView.as_view(),
        name="admin-report-evidence",
    ),
]
