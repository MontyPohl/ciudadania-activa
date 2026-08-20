import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.ai_client_app.models import AiClassification
from apps.core.enums import ReportPriority

pytestmark = pytest.mark.django_db


def test_clasificacion_minima_valida(report):
    clasificacion = AiClassification.objects.create(report=report)

    assert clasificacion.suggested_category is None
    assert clasificacion.suggested_institution is None
    assert clasificacion.suggested_priority == ""
    assert clasificacion.confidence is None
    assert clasificacion.inconsistency_flag is False
    assert str(clasificacion) == f"IA de {report.pk}"


def test_clasificacion_completa(report, category, institution):
    clasificacion = AiClassification.objects.create(
        report=report,
        suggested_category=category,
        suggested_institution=institution,
        suggested_priority=ReportPriority.ALTA,
        confidence="0.925",
        inconsistency_flag=True,
    )
    clasificacion.refresh_from_db()

    assert float(clasificacion.confidence) == pytest.approx(0.925)


@pytest.mark.parametrize("valor", ["-0.001", "1.001"])
def test_la_confianza_debe_estar_entre_cero_y_uno(report, valor):
    with pytest.raises(IntegrityError), transaction.atomic():
        AiClassification.objects.create(report=report, confidence=valor)


def test_el_reporte_admite_varias_clasificaciones(report):
    """El Sprint 8 permite reprocesar la clasificación de un reporte."""
    AiClassification.objects.create(report=report)
    AiClassification.objects.create(report=report)

    assert report.ai_classifications.count() == 2


def test_no_se_puede_borrar_una_categoria_sugerida(report, category):
    AiClassification.objects.create(report=report, suggested_category=category)

    with pytest.raises(ProtectedError):
        category.delete()


def test_borrar_el_reporte_arrastra_su_clasificacion(report):
    AiClassification.objects.create(report=report)
    report.delete()

    assert AiClassification.objects.count() == 0


def test_solo_estan_los_campos_del_anexo_a(report):
    """D2-14: nada de `raw_response` ni `justification` en el Sprint 2."""
    campos = {field.name for field in AiClassification._meta.fields}

    assert campos == {
        "id",
        "created_at",
        "updated_at",
        "report",
        "suggested_category",
        "suggested_institution",
        "suggested_priority",
        "confidence",
        "inconsistency_flag",
    }
