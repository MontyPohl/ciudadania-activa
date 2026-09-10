import uuid

import pytest
from django.contrib.gis.geos import Point
from django.db.models import ProtectedError

from apps.reports_app.models import (
    Report,
    ReportConfirmation,
    ReportMedia,
    ReportStatusHistory,
    ResolutionEvidence,
)

pytestmark = pytest.mark.django_db


def test_borrar_al_autor_conserva_el_reporte(report, citizen):
    citizen.delete()
    report.refresh_from_db()

    assert report.citizen is None
    assert Report.objects.count() == 1


def test_no_se_puede_borrar_una_categoria_con_reportes(report, category):
    with pytest.raises(ProtectedError):
        category.delete()


def test_no_se_puede_borrar_una_institucion_asignada_a_un_reporte(report, institution):
    report.institution = institution
    report.save()

    with pytest.raises(ProtectedError):
        institution.delete()


def test_borrar_al_validador_conserva_el_reporte(report, other_citizen):
    report.validated_by = other_citizen
    report.save()
    other_citizen.delete()
    report.refresh_from_db()

    assert report.validated_by is None


def test_borrar_el_reporte_arrastra_su_material_asociado(report, other_citizen):
    ReportMedia.objects.create(report=report, file_url="u", file_hash="a" * 64)
    ReportStatusHistory.objects.create(report=report, new_status="CLASIFICADO")
    ResolutionEvidence.objects.create(
        report=report,
        description="d",
        evidence_url="u",
        resolution_cycle=1,
        idempotency_key=uuid.uuid4(),
        content_hash="a" * 64,
    )
    ReportConfirmation.objects.create(report=report, citizen=other_citizen)

    report.delete()

    assert ReportMedia.objects.count() == 0
    assert ReportStatusHistory.objects.count() == 0
    assert ResolutionEvidence.objects.count() == 0
    assert ReportConfirmation.objects.count() == 0


def test_no_se_puede_borrar_a_quien_confirmo(report, other_citizen):
    ReportConfirmation.objects.create(report=report, citizen=other_citizen)

    with pytest.raises(ProtectedError):
        other_citizen.delete()


def test_borrar_a_quien_subio_material_conserva_el_archivo(report, other_citizen):
    media = ReportMedia.objects.create(
        report=report, file_url="u", file_hash="b" * 64, uploaded_by=other_citizen
    )
    other_citizen.delete()
    media.refresh_from_db()

    assert media.uploaded_by is None


def test_el_reporte_ingestado_sin_autor_se_puede_crear_y_borrar(category):
    ingestado = Report.objects.create(
        category=category, description="d", location=Point(-57.6, -25.3, srid=4326)
    )
    ingestado.delete()

    assert Report.objects.count() == 0
