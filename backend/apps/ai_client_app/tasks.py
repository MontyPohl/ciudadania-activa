"""Punto de extensión de clasificación de reportes.

Sprint 6 solo declara la tarea. La implementación real y cualquier integración
con Gemini pertenecen al Sprint 8.
"""

from celery import shared_task


@shared_task(name="ai.classify_report")
def classify_report(report_id):
    """No-op deliberado de S6; conserva la interfaz que implementará S8."""
    return None
