from django.urls import path

from apps.rewards_app.views import (
    LedgerMovementsView,
    RedeemView,
    RedemptionDetailView,
    RedemptionListView,
    RewardsCatalogView,
    RewardsSummaryView,
)

urlpatterns = [
    path("summary/", RewardsSummaryView.as_view(), name="rewards-summary"),
    path("transactions/", LedgerMovementsView.as_view(), name="rewards-transactions"),
    path("catalog/", RewardsCatalogView.as_view(), name="rewards-catalog"),
    path("redemptions/", RedemptionListView.as_view(), name="rewards-redemptions"),
    path(
        "redemptions/<uuid:redemption_id>/",
        RedemptionDetailView.as_view(),
        name="rewards-redemption-detail",
    ),
    path("<uuid:reward_id>/redeem/", RedeemView.as_view(), name="rewards-redeem"),
]
