from django.urls import path

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
    path("categories/", PublicCategoryListView.as_view(), name="public-category-list"),
]
