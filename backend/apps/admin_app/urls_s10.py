from django.urls import path

from apps.admin_app.audit_views import AdminAuditLogListView
from apps.reports_app.evidence_views import AdminReportEvidenceView
from apps.reports_app.reversal_views import AdminReportReversalView

urlpatterns = [
    path(
        "audit-logs/",
        AdminAuditLogListView.as_view(),
        name="admin-audit-log-list",
    ),
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
