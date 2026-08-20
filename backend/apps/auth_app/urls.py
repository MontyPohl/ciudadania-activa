from django.urls import path

from .views import GoogleLoginView, MeView, TokenRefreshView

urlpatterns = [
    path("google/", GoogleLoginView.as_view(), name="auth-google"),
    path("refresh/", TokenRefreshView.as_view(), name="auth-refresh"),
    path("me/", MeView.as_view(), name="auth-me"),
]
