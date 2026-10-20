import uuid

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
    assert report.closed_at is None
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
        source=ReportSource.X_MOCK,
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


def test_hash_de_foto_es_unico_en_base_de_datos(report, citizen, category):
    ReportMedia.objects.create(
        report=report, file_url="reports/uno.jpg", file_hash="f" * 64, uploaded_by=citizen
    )
    other = Report.objects.create(
        citizen=citizen,
        category=category,
        description="Otro reporte.",
        location=Point(-57.64, -25.27, srid=4326),
    )

    with pytest.raises(IntegrityError) as excinfo:
        with transaction.atomic():
            ReportMedia.objects.create(
                report=other,
                file_url="reports/dos.jpg",
                file_hash="f" * 64,
                uploaded_by=citizen,
            )

    assert excinfo.value.__cause__.diag.constraint_name == "report_media_foto_hash_unico"


def test_fotos_con_hashes_distintos_coexisten(report, citizen, category):
    other = Report.objects.create(
        citizen=citizen,
        category=category,
        description="Otro reporte.",
        location=Point(-57.64, -25.27, srid=4326),
    )
    ReportMedia.objects.create(
        report=report, file_url="reports/a.jpg", file_hash="a" * 64, uploaded_by=citizen
    )
    ReportMedia.objects.create(
        report=other, file_url="reports/b.jpg", file_hash="b" * 64, uploaded_by=citizen
    )

    assert ReportMedia.objects.count() == 2


def test_hash_no_es_unico_para_tipo_futuro_distinto(report, citizen, category):
    other = Report.objects.create(
        citizen=citizen,
        category=category,
        description="Otro reporte.",
        location=Point(-57.64, -25.27, srid=4326),
    )
    for current_report in (report, other):
        ReportMedia.objects.create(
            report=current_report,
            file_url=f"reports/{current_report.id}.bin",
            file_hash="e" * 64,
            media_type="FUTURO",
            uploaded_by=citizen,
        )

    assert ReportMedia.objects.filter(file_hash="e" * 64).count() == 2


def test_foto_y_tipo_futuro_pueden_compartir_hash(report, citizen, category):
    other = Report.objects.create(
        citizen=citizen,
        category=category,
        description="Otro reporte.",
        location=Point(-57.64, -25.27, srid=4326),
    )
    ReportMedia.objects.create(
        report=report,
        file_url="reports/foto.jpg",
        file_hash="c" * 64,
        media_type=MediaType.FOTO,
        uploaded_by=citizen,
    )
    ReportMedia.objects.create(
        report=other,
        file_url="reports/futuro.bin",
        file_hash="c" * 64,
        media_type="FUTURO",
        uploaded_by=citizen,
    )

    assert ReportMedia.objects.filter(file_hash="c" * 64).count() == 2


def test_historial_de_estados(report, citizen):
    asiento = ReportStatusHistory.objects.create(
        report=report, new_status=ReportStatus.CLASIFICADO, changed_by=citizen
    )

    assert asiento.previous_status == ""
    assert str(asiento) == "- -> CLASIFICADO"


def test_evidencia_de_resolucion(report):
    evidencia = ResolutionEvidence.objects.create(
        report=report,
        description="Bache reparado.",
        evidence_url="https://example.test/e.jpg",
        resolution_cycle=1,
        idempotency_key=uuid.uuid4(),
        content_hash="a" * 64,
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
