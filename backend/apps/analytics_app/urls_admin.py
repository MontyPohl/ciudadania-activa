"""Rutas de métricas: admin."""

from django.urls import path

from apps.analytics_app.views_admin import AdminMetricsView

urlpatterns = [path("", AdminMetricsView.as_view(), name="metrics")]
