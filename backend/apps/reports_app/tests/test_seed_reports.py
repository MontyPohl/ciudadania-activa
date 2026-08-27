import uuid
from collections import Counter
from unittest.mock import Mock

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework.test import APIClient

from apps.admin_app.models import Category
from apps.core.enums import ReportSource, ReportStatus
from apps.map_app.cache import GENERATION_KEY
from apps.reports_app.management.commands import seed_reports as seed_module
from apps.reports_app.models import Report

pytestmark = pytest.mark.django_db


def _run_seed(django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        call_command("seed_reports", verbosity=0)


def test_seed_crea_14_dos_por_estado_y_conteos_publicos(
    django_capture_on_commit_callbacks,
):
    _run_seed(django_capture_on_commit_callbacks)
    reports = Report.objects.filter(pk__in=[item.report_id for item in seed_module.SEED_REPORTS])
    counts = Counter(reports.values_list("status", flat=True))

    assert reports.count() == 14
    assert counts == Counter({status: 2 for status in ReportStatus})
    assert sum(counts[status] for status in ReportStatus.values[:4]) == 8
    assert counts[ReportStatus.RESUELTO] + counts[ReportStatus.CERRADO] == 4
    assert reports.exclude(status=ReportStatus.RECHAZADO).count() == 12


def test_seed_es_idempotente_uuid_v5_y_preserva_filas_ajenas(
    django_capture_on_commit_callbacks, category
):
    external = Report.objects.create(
        citizen=None,
        category=category,
        description="Fila externa que debe conservarse.",
        location=seed_module.Point(-57.4, -25.2, srid=4326),
    )
    _run_seed(django_capture_on_commit_callbacks)
    first_ids = set(Report.objects.exclude(pk=external.pk).values_list("pk", flat=True))
    _run_seed(django_capture_on_commit_callbacks)
    second_ids = set(Report.objects.exclude(pk=external.pk).values_list("pk", flat=True))

    expected_ids = {
        uuid.uuid5(seed_module.SEED_NAMESPACE, item.code) for item in seed_module.SEED_REPORTS
    }
    assert first_ids == second_ids == expected_ids
    assert Report.objects.filter(pk=external.pk, description=external.description).exists()
    assert Report.objects.count() == 15


def test_seed_usa_catalogos_existentes_campos_sinteticos_y_puntos_publicos_distintos(
    django_capture_on_commit_callbacks,
):
    _run_seed(django_capture_on_commit_callbacks)
    reports = list(
        Report.objects.filter(pk__in=[item.report_id for item in seed_module.SEED_REPORTS])
        .select_related("category")
        .order_by("id")
    )

    assert all(report.citizen_id is None and report.validated_by_id is None for report in reports)
    assert all(report.source == ReportSource.PLATFORM for report in reports)
    assert all(report.validated_at is None and report.resolved_at is None for report in reports)
    assert all(
        report.institution_id == report.category.default_institution_id for report in reports
    )
    assert all(report.priority == report.category.default_priority for report in reports)
    assert all("sintético" in report.description for report in reports)
    assert all(report.location.srid == 4326 for report in reports)
    assert (
        len({(round(report.location.x, 3), round(report.location.y, 3)) for report in reports})
        == 14
    )
    assert not any(report.status_history.exists() for report in reports)


def test_seed_falla_atomicamente_si_falta_categoria(
    django_capture_on_commit_callbacks, monkeypatch
):
    Category.objects.filter(slug="baches").delete()
    invalidation = Mock()
    monkeypatch.setattr(seed_module, "invalidate_public_reports_cache", invalidation)

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        with pytest.raises(CommandError):
            call_command("seed_reports", verbosity=0)

    assert Report.objects.count() == 0
    assert callbacks == []
    invalidation.assert_not_called()


def test_seed_invalida_una_vez_solo_despues_del_commit(
    django_capture_on_commit_callbacks, monkeypatch
):
    invalidation = Mock()
    monkeypatch.setattr(seed_module, "invalidate_public_reports_cache", invalidation)

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        call_command("seed_reports", verbosity=0)
        assert invalidation.call_count == 0

    assert len(callbacks) == 1
    callbacks[0]()
    invalidation.assert_called_once_with()


def test_seed_repara_reporte_y_hace_inalcanzable_el_listado_cacheado(
    django_capture_on_commit_callbacks,
):
    _run_seed(django_capture_on_commit_callbacks)
    target = seed_module.SEED_REPORTS[0]
    Report.objects.filter(pk=target.report_id).update(description="Snapshot sintético obsoleto")
    client = APIClient()
    params = {
        "bbox": "-57.8,-25.5,-57.3,-25.0",
        "state_filter": "all",
    }
    stale = client.get("/api/v1/public/reports/", params)
    generation_before_seed = cache.get(GENERATION_KEY)
    stale_row = next(row for row in stale.json()["results"] if row["id"] == str(target.report_id))
    assert stale_row["description"] == "Snapshot sintético obsoleto"

    _run_seed(django_capture_on_commit_callbacks)
    generation_after_seed = cache.get(GENERATION_KEY)
    fresh = client.get("/api/v1/public/reports/", params)
    fresh_row = next(row for row in fresh.json()["results"] if row["id"] == str(target.report_id))

    assert generation_after_seed != generation_before_seed
    assert fresh_row["description"] == target.description
