"""Pruebas de la tarea `ai.classify_report` (S8-C).

Dos garantías distintas conviven aquí. La primera es de seguridad: **sin
proveedor configurado la tarea no hace nada**, ni siquiera una consulta. La
segunda es funcional: cuando se inyecta un proveedor, la tarea recorre el camino
completo hasta la auditoría.

La primera importa más de lo que parece. La creación de reportes encola esta
tarea en producción desde el Sprint 6; si el adaptador falso quedara activo por
defecto, integrar S8-C bastaría para clasificar incidencias reales con datos
inventados.
"""

import pytest

from apps.admin_app.models import AuditLog
from apps.ai_client_app import tasks as ai_tasks
from apps.ai_client_app.adapters import FakeAiClassificationAdapter
from apps.ai_client_app.adapters.runtime import RuntimeAdapter, get_runtime_adapter
from apps.ai_client_app.models import AiClassification
from apps.ai_client_app.orchestration import AUDIT_ACTION
from apps.ai_client_app.tasks import classify_report
from apps.core.enums import ActorType, ReportStatus
from apps.reports_app.models import ReportStatusHistory
from config.celery import app

MODEL_NAME = "fake-s8-c"


@pytest.fixture
def runtime_fake(monkeypatch):
    """Inyecta el adaptador falso solo durante la prueba que lo pide."""
    monkeypatch.setattr(
        ai_tasks,
        "get_runtime_adapter",
        lambda: RuntimeAdapter(adapter=FakeAiClassificationAdapter(), model_name=MODEL_NAME),
    )


# --------------------------------------------------------------------------- #
# Registro en Celery
# --------------------------------------------------------------------------- #


def test_celery_autodiscover_registra_la_tarea_sin_broker():
    app.autodiscover_tasks(lambda: ["apps.ai_client_app"], force=True)

    discovered = app.tasks["ai.classify_report"]
    assert discovered.name == classify_report.name == "ai.classify_report"


def test_el_nombre_de_la_tarea_no_cambia():
    """`create_report` encola por este nombre desde S6; no puede moverse."""
    assert classify_report.name == "ai.classify_report"


# --------------------------------------------------------------------------- #
# Sin proveedor configurado: la garantía de seguridad
# --------------------------------------------------------------------------- #


def test_el_runtime_no_trae_proveedor_por_defecto():
    """S8-C no habilita ninguno: eso es trabajo de S8-D."""
    assert get_runtime_adapter() is None


def test_sin_proveedor_la_tarea_retorna_sin_hacer_nada():
    assert classify_report("id-que-no-necesita-existir") is None


@pytest.mark.django_db
def test_sin_proveedor_no_ejecuta_ni_una_consulta(django_assert_num_queries):
    """Sale antes de mirar el reporte: no gasta base de datos ni cuota."""
    with django_assert_num_queries(0):
        assert classify_report("id-inexistente") is None


@pytest.mark.django_db
def test_sin_proveedor_el_reporte_sigue_creado(report):
    assert classify_report(str(report.pk)) is None

    report.refresh_from_db()
    assert report.status == ReportStatus.CREADO
    assert AiClassification.objects.count() == 0
    assert AuditLog.objects.filter(action=AUDIT_ACTION).count() == 0


@pytest.mark.django_db
def test_el_adaptador_falso_no_esta_activo_por_defecto(report):
    """Integrar S8-C no puede empezar a clasificar producción con el fake."""
    classify_report(str(report.pk))

    report.refresh_from_db()
    assert report.status == ReportStatus.CREADO
    assert AiClassification.objects.count() == 0


# --------------------------------------------------------------------------- #
# Con proveedor inyectado: el camino completo
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_extremo_a_extremo_con_el_adaptador_falso(report, runtime_fake, institution):
    """Tarea → runtime → petición → fake → parser → mapeo → policy → persistencia."""
    assert classify_report(str(report.pk)) is None

    report.refresh_from_db()
    assert report.status == ReportStatus.CLASIFICADO

    clasificacion = AiClassification.objects.get()
    assert clasificacion.report_id == report.pk
    assert clasificacion.model_name == MODEL_NAME
    assert clasificacion.justification
    assert clasificacion.confidence >= 0

    historial = ReportStatusHistory.objects.get(new_status=ReportStatus.CLASIFICADO)
    assert historial.previous_status == ReportStatus.CREADO
    assert historial.changed_by is None

    entrada = AuditLog.objects.get(action=AUDIT_ACTION)
    assert entrada.actor_type == ActorType.AI
    assert entrada.payload["model_name"] == MODEL_NAME
    assert entrada.payload["prompt_version"] == "s8-classification-v1"


@pytest.mark.django_db
def test_el_fake_confirma_la_categoria_y_propone_institucion(report, runtime_fake):
    """El fake conserva la categoría del ciudadano porque es válida.

    La institución sí cambia: el reporte nace sin ninguna —se fija al validar, en
    el Sprint 9— y el adaptador propone la primera admisible. `has_changes` recoge
    esa diferencia, que es justo lo que el backend deriva en vez de preguntarle al
    modelo.
    """
    original = report.category_id
    assert report.institution_id is None

    classify_report(str(report.pk))

    report.refresh_from_db()
    assert report.category_id == original
    assert report.institution_id is not None
    assert AuditLog.objects.get(action=AUDIT_ACTION).payload["has_changes"] is True


@pytest.mark.django_db
def test_una_segunda_entrega_de_la_tarea_no_duplica_nada(report, runtime_fake):
    """Celery no garantiza entrega única; la tarea sí garantiza efecto único."""
    classify_report(str(report.pk))
    classify_report(str(report.pk))

    assert AiClassification.objects.count() == 1
    assert ReportStatusHistory.objects.filter(new_status=ReportStatus.CLASIFICADO).count() == 1
    assert AuditLog.objects.filter(action=AUDIT_ACTION).count() == 1

    report.refresh_from_db()
    assert report.status == ReportStatus.CLASIFICADO


@pytest.mark.django_db
def test_la_tarea_no_propaga_el_fallo_del_proveedor(report, monkeypatch):
    """Relanzar solo marcaría la tarea como fallida sin nada que reintentar."""

    class AdapterCaido:
        def classify(self, request):
            raise RuntimeError("proveedor caído")

    monkeypatch.setattr(
        ai_tasks,
        "get_runtime_adapter",
        lambda: RuntimeAdapter(adapter=AdapterCaido(), model_name=MODEL_NAME),
    )

    assert classify_report(str(report.pk)) is None

    report.refresh_from_db()
    assert report.status == ReportStatus.CREADO
    assert AiClassification.objects.count() == 0


@pytest.mark.django_db
def test_la_tarea_no_configura_reintentos_automaticos():
    """La política de reintentos del contrato se cablea en S8-D (D9)."""
    tarea = app.tasks["ai.classify_report"]

    assert getattr(tarea, "max_retries", None) in (None, 3)
    assert not getattr(tarea, "autoretry_for", ())


def test_tarea_propaga_version_del_runtime(monkeypatch):
    runtime = RuntimeAdapter(
        adapter=FakeAiClassificationAdapter(),
        model_name=MODEL_NAME,
        prompt_version="classification-v2",
    )
    calls = []
    monkeypatch.setattr(ai_tasks, "get_runtime_adapter", lambda: runtime)
    monkeypatch.setattr(
        ai_tasks,
        "process_report_classification",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    classify_report.run("reporte-de-prueba")
    assert calls == [
        (
            ("reporte-de-prueba",),
            {
                "adapter": runtime.adapter,
                "model_name": MODEL_NAME,
                "prompt_version": "classification-v2",
            },
        )
    ]
