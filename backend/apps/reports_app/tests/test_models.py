import pytest
from django.contrib.gis.geos import Point
from django.db import IntegrityError, transaction

from apps.core.enums import RejectionReason, ReportPriority, ReportSource, ReportStatus
from apps.reports_app.enums import MediaType
from apps.reports_app.models import (
    Report,
    ReportConfirmation,
    ReportMedia,
    ReportStatusHistory,
    ResolutionEvidence,
)

pytestmark = pytest.mark.django_db


def test_reporte_nuevo_nace_creado_con_prioridad_media_y_origen_plataforma(report):
    assert report.status == ReportStatus.CREADO
    assert report.priority == ReportPriority.MEDIA
    assert report.source == ReportSource.PLATFORM
    assert report.institution is None
    assert report.validated_at is None
    assert report.validated_by is None
    assert report.resolved_at is None
    assert report.rejection_reason == ""


def test_el_reporte_exige_categoria(citizen):
    with pytest.raises(IntegrityError), transaction.atomic():
        Report.objects.create(
            citizen=citizen,
            category=None,
            description="Sin categoría.",
            location=Point(-57.63, -25.26, srid=4326),
        )


def test_el_reporte_admite_no_tener_ciudadano_ni_institucion(category):
    ingestado = Report.objects.create(
        category=category,
        description="Pre-reporte ingestado.",
        source=ReportSource.TELEGRAM,
        location=Point(-57.63, -25.26, srid=4326),
    )

    assert ingestado.citizen is None
    assert ingestado.institution is None


def test_motivo_de_rechazo_tipificado(report):
    report.status = ReportStatus.RECHAZADO
    report.rejection_reason = RejectionReason.DUPLICADO
    report.save()
    report.refresh_from_db()

    assert report.rejection_reason == "duplicado"


def test_str_del_reporte(report):
    assert str(report) == f"CREADO · {report.category_id}"


def test_media_del_reporte(report, citizen):
    media = ReportMedia.objects.create(
        report=report,
        file_url="https://example.test/f.jpg",
        file_hash="a" * 64,
        uploaded_by=citizen,
    )

    assert media.media_type == MediaType.FOTO
    assert str(media) == f"FOTO de {report.pk}"


def test_historial_de_estados(report, citizen):
    asiento = ReportStatusHistory.objects.create(
        report=report, new_status=ReportStatus.CLASIFICADO, changed_by=citizen
    )

    assert asiento.previous_status == ""
    assert str(asiento) == "- -> CLASIFICADO"


def test_evidencia_de_resolucion(report):
    evidencia = ResolutionEvidence.objects.create(
        report=report, description="Bache reparado.", evidence_url="https://example.test/e.jpg"
    )

    assert str(evidencia) == f"Evidencia de {report.pk}"


def test_una_sola_confirmacion_por_ciudadano_y_reporte(report, other_citizen):
    ReportConfirmation.objects.create(report=report, citizen=other_citizen)

    with pytest.raises(IntegrityError), transaction.atomic():
        ReportConfirmation.objects.create(report=report, citizen=other_citizen)


def test_la_confirmacion_no_tiene_columna_confirmed_at(report, other_citizen):
    """D2-10: `created_at` es el instante de la confirmación."""
    confirmacion = ReportConfirmation.objects.create(report=report, citizen=other_citizen)
    campos = {field.name for field in ReportConfirmation._meta.fields}

    assert "confirmed_at" not in campos
    assert confirmacion.created_at is not None


def test_la_base_de_datos_no_impide_confirmar_el_propio_reporte(report, citizen):
    """C-02 / CT-16: la regla vive en el servicio de dominio del Sprint 7, no en SQL."""
    confirmacion = ReportConfirmation.objects.create(report=report, citizen=citizen)

    assert confirmacion.citizen == report.citizen
