from django.urls import path

from apps.rewards_app.ranking_views import MonthlyRankingView

urlpatterns = [
    path("monthly/", MonthlyRankingView.as_view()),
]
