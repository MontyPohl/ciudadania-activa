import pytest

from apps.ai_client_app.tasks import classify_report
from config.celery import app


def test_classify_report_es_noop_sin_acceso_a_db():
    assert classify_report("id-que-no-necesita-existir") is None


@pytest.mark.django_db
def test_classify_report_no_ejecuta_queries(django_assert_num_queries):
    with django_assert_num_queries(0):
        assert classify_report("id-inexistente") is None


def test_celery_autodiscover_registra_la_tarea_sin_broker():
    app.autodiscover_tasks(lambda: ["apps.ai_client_app"], force=True)

    discovered = app.tasks["ai.classify_report"]
    assert discovered.name == classify_report.name == "ai.classify_report"
    assert discovered.run("id-inexistente") is None
