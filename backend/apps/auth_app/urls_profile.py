"""Rutas de preferencias del perfil ciudadano (Sprint 4).

Se montan bajo `/api/v1/profile/`. Van en un módulo aparte del de identidad porque son
otro recurso: la preferencia de ranking no forma parte del flujo de verificación,
aunque comparta la tabla `citizen_profiles`.
"""

from django.urls import path

from .views import RankingVisibilityView

urlpatterns = [
    path(
        "ranking-visibility/",
        RankingVisibilityView.as_view(),
        name="profile-ranking-visibility",
    ),
]
