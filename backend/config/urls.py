from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.core.urls")),
    path("api/v1/auth/", include("apps.auth_app.urls")),
    path("api/v1/identity/", include("apps.auth_app.urls_identity")),
    path("api/v1/profile/", include("apps.auth_app.urls_profile")),
    path("api/v1/admin/identity/", include("apps.admin_app.urls")),
    path("api/v1/admin/", include("apps.admin_app.urls_s10")),
    path("api/v1/public/", include("apps.map_app.urls")),
    path("api/v1/reports/", include("apps.reports_app.urls")),
    path("api/v1/citizen/", include("apps.reports_app.urls_citizen")),
    path("api/v1/operator/", include("apps.reports_app.urls_operator")),
    path("api/v1/rewards/", include("apps.rewards_app.urls")),
    path("api/v1/operator/rewards/", include("apps.rewards_app.urls_operator")),
    path("api/v1/ranking/", include("apps.rewards_app.urls_ranking")),
    path("api/v1/operator/metrics/", include("apps.analytics_app.urls_operator")),
    path("api/v1/admin/metrics/", include("apps.analytics_app.urls_admin")),
    path("api/v1/public/metrics/", include("apps.analytics_app.urls_public")),
]
