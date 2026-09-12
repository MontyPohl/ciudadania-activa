from django.urls import path

from apps.reports_app.evidence_views import AdminReportEvidenceView
from apps.reports_app.reversal_views import AdminReportReversalView

urlpatterns = [
    path(
        "reports/<uuid:report_id>/evidence/",
        AdminReportEvidenceView.as_view(),
        name="admin-report-evidence",
    ),
    path(
        "reports/<uuid:report_id>/revert-validation/",
        AdminReportReversalView.as_view(),
        name="admin-report-revert-validation",
    ),
]
