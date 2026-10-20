from django.urls import path

from apps.map_app.media_views import PublicReportEvidenceView, PublicReportPhotoView
from apps.map_app.views import (
    PublicCategoryListView,
    PublicReportDetailView,
    PublicReportListView,
)

urlpatterns = [
    path("reports/", PublicReportListView.as_view(), name="public-report-list"),
    path(
        "reports/<uuid:report_id>/",
        PublicReportDetailView.as_view(),
        name="public-report-detail",
    ),
    path(
        "reports/<uuid:report_id>/photo/",
        PublicReportPhotoView.as_view(),
        name="public-report-photo",
    ),
    path(
        "reports/<uuid:report_id>/evidence/<uuid:evidence_id>/",
        PublicReportEvidenceView.as_view(),
        name="public-report-evidence",
    ),
    path("categories/", PublicCategoryListView.as_view(), name="public-category-list"),
]
