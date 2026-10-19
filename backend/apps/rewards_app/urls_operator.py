from django.urls import path

from apps.rewards_app.operator_views import (
    OperatorRedemptionListView,
    OperatorRewardDetailView,
    OperatorRewardView,
)

urlpatterns = [
    path("", OperatorRewardView.as_view(), name="operator-reward"),
    path("redemptions/", OperatorRedemptionListView.as_view(), name="operator-redemptions"),
    path("<uuid:reward_id>/", OperatorRewardDetailView.as_view(), name="operator-reward-detail"),
]
