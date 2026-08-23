"""Rutas del panel administrativo de identidades (Sprint 4).

Se montan bajo `/api/v1/admin/identity/`. Nada que ver con `/admin/`, que es el admin
de Django: este prefijo es API y su autorización es el rol de dominio `admin`, no
`is_staff` (ADR-0005).
"""

from django.urls import path

from .views import IdentityDetailView, IdentityQueueView, IdentityReviewView

urlpatterns = [
    path("", IdentityQueueView.as_view(), name="admin-identity-queue"),
    path("<uuid:profile_id>/", IdentityDetailView.as_view(), name="admin-identity-detail"),
    path(
        "<uuid:profile_id>/review/",
        IdentityReviewView.as_view(),
        name="admin-identity-review",
    ),
]
