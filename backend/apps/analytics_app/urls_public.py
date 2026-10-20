"""Rutas de métricas: public."""

from django.urls import path

from apps.analytics_app.views_public import PublicMetricsView

urlpatterns = [path("", PublicMetricsView.as_view(), name="metrics")]
