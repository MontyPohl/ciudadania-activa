"""Concurrencia de la clasificación (S8-C).

La garantía es una sola: **el estado autoritativo es el que se relee dentro del
lock**, no el que se leyó antes de llamar al proveedor. Sin eso, dos entregas
simultáneas de la misma tarea pasarían las dos la comprobación previa y la
segunda duplicaría clasificación, historial y auditoría.

Se prueba con dos hilos que compiten de verdad contra PostgreSQL, siguiendo el
patrón ya establecido en `auth_app/tests/test_identity_concurrency.py`.
"""

import json
import threading

import pytest
from django.db import connection

from apps.admin_app.models import AuditLog
from apps.ai_client_app.models import AiClassification
from apps.ai_client_app.orchestration import AUDIT_ACTION, process_report_classification
from apps.core.enums import ReportStatus
from apps.reports_app.models import ReportStatusHistory

ESPERA_MAXIMA = 15
MODEL_NAME = "fake-s8-c"

PAYLOAD = {
    "category_slug": "semaforos",
    "institution_slug": "municipalidad-de-asuncion",
    "priority": "ALTA",
    "confidence": 0.95,
    "inconsistency": False,
    "justification": "Semáforo fuera de servicio.",
}


class AdapterSincronizado:
    """Espera en la barrera antes de responder, para solapar las dos ejecuciones."""

    def __init__(self, barrera):
        self._barrera = barrera

    def classify(self, request) -> str:
        self._barrera.wait()
        return json.dumps(PAYLOAD)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_dos_ejecuciones_simultaneas_producen_un_solo_efecto(report):
    barrera = threading.Barrier(2, timeout=ESPERA_MAXIMA)
    resultados: dict[str, object] = {}

    def clasificar(nombre: str) -> None:
        try:
            resultados[nombre] = process_report_classification(
                report.pk,
                adapter=AdapterSincronizado(barrera),
                model_name=MODEL_NAME,
            )
        except Exception as exc:  # pragma: no cover - solo si algo se rompe de verdad
            resultados[nombre] = f"inesperado: {exc!r}"
        finally:
            connection.close()

    hilos = [
        threading.Thread(target=clasificar, args=("a",)),
        threading.Thread(target=clasificar, args=("b",)),
    ]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=ESPERA_MAXIMA)

    assert sorted(resultados.values(), key=str) == [False, True], resultados

    assert AiClassification.objects.filter(report=report).count() == 1
    assert (
        ReportStatusHistory.objects.filter(
            report=report, new_status=ReportStatus.CLASIFICADO
        ).count()
        == 1
    )
    assert AuditLog.objects.filter(action=AUDIT_ACTION).count() == 1

    report.refresh_from_db()
    assert report.status == ReportStatus.CLASIFICADO
    assert report.category.slug == "semaforos"
