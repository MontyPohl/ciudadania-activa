from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.core.urls")),
    path("api/v1/auth/", include("apps.auth_app.urls")),
    path("api/v1/identity/", include("apps.auth_app.urls_identity")),
    path("api/v1/profile/", include("apps.auth_app.urls_profile")),
    path("api/v1/admin/identity/", include("apps.admin_app.urls")),
]
