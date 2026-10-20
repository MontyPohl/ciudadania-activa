"""Rutas de métricas: operator."""

from django.urls import path

from apps.analytics_app.views_operator import OperatorMetricsView

urlpatterns = [path("", OperatorMetricsView.as_view(), name="metrics")]
