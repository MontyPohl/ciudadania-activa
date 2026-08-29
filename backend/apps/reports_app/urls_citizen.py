from django.urls import path

from apps.reports_app.views import CitizenSummaryView

urlpatterns = [
    path("summary/", CitizenSummaryView.as_view(), name="citizen-summary"),
]
