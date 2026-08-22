"""Rutas de verificación de identidad del lado ciudadano (Sprint 4).

Se montan bajo `/api/v1/identity/`. Las de administración viven en `admin_app` y se
montarán bajo `/api/v1/admin/identity/` en la fase siguiente.
"""

from django.urls import path

from .views import IdentityStatusView, IdentitySubmitView

urlpatterns = [
    path("submit/", IdentitySubmitView.as_view(), name="identity-submit"),
    path("status/", IdentityStatusView.as_view(), name="identity-status"),
]
