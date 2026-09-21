from django.urls import path

from apps.reports_app.evidence_views import (
    CitizenReportEvidenceView,
    ResolutionEvidenceDownloadView,
)
from apps.reports_app.views import (
    ConfirmReportView,
    ContextualReportDetailView,
    MyReportsView,
    PossibleDuplicatesView,
    ReportCreateView,
    ReportHistoryView,
)

urlpatterns = [
    path(
        "evidence-access/<str:token>/",
        ResolutionEvidenceDownloadView.as_view(),
        name="resolution-evidence-download",
    ),
    path("", ReportCreateView.as_view(), name="report-create"),
    path(
        "possible-duplicates/",
        PossibleDuplicatesView.as_view(),
        name="report-possible-duplicates",
    ),
    path("mine/", MyReportsView.as_view(), name="report-mine"),
    path(
        "<uuid:report_id>/evidence/",
        CitizenReportEvidenceView.as_view(),
        name="report-evidence",
    ),
    path(
        "<uuid:report_id>/confirm/",
        ConfirmReportView.as_view(),
        name="report-confirm",
    ),
    path(
        "<uuid:report_id>/history/",
        ReportHistoryView.as_view(),
        name="report-history",
    ),
    path(
        "<uuid:report_id>/",
        ContextualReportDetailView.as_view(),
        name="report-detail",
    ),
]
