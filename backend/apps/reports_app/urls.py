from django.urls import path

from apps.reports_app.views import MyReportsView, PossibleDuplicatesView, ReportCreateView

urlpatterns = [
    path("", ReportCreateView.as_view(), name="report-create"),
    path(
        "possible-duplicates/",
        PossibleDuplicatesView.as_view(),
        name="report-possible-duplicates",
    ),
    path("mine/", MyReportsView.as_view(), name="report-mine"),
]
