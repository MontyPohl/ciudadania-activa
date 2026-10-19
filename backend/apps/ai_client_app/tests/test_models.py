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


def test_el_conjunto_de_campos_esta_congelado(report):
    """Los del Anexo A, los del Sprint 8 y los contadores de uso del proveedor.

    La comparación sigue siendo por igualdad exacta: cualquier columna nueva rompe
    esta prueba a propósito, para que ningún campo entre sin una decisión escrita.
    `justification` y `model_name` se incorporaron en S8-C conforme al contrato
    S8-A (D17); `raw_response` y el prompt siguen fuera, porque viajan en el
    payload de `audit_logs` y no como columnas (D15 y D16).
    """
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
        "justification",
        "model_name",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    }


def test_los_campos_nuevos_admiten_las_filas_previas(report):
    """Aditivos y con default vacío: la migración no invalida el histórico."""
    clasificacion = AiClassification.objects.create(report=report)

    assert clasificacion.justification == ""
    assert clasificacion.model_name == ""


def test_raw_response_y_prompt_no_son_columnas(report):
    """D15 y D16: no se duplica el texto del ciudadano en cada reproceso."""
    campos = {field.name for field in AiClassification._meta.fields}

    assert campos.isdisjoint({"raw_response", "prompt", "prompt_version", "hubo_cambio"})
