from importlib import import_module

import pytest
from django.contrib.gis.geos import Point
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.reports_app.management.commands.seed_reports import SEED_REPORTS

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATE_FROM = [
    ("reports_app", "0003_add_resolution_cycle_columns"),
    ("rewards_app", "0002_add_resolution_cycle_column"),
]
MIGRATE_TO = [
    ("reports_app", "0006_resolution_evidence_idempotency"),
    ("rewards_app", "0003_enforce_resolution_cycle_constraints"),
]


def _executor(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    return executor


def _historical_apps(executor, targets):
    return executor.loader.project_state(targets).apps


def _ensure_categories(apps):
    Institution = apps.get_model("admin_app", "Institution")
    Category = apps.get_model("admin_app", "Category")
    institution, _ = Institution.objects.get_or_create(
        slug="migration-test-institution",
        defaults={"name": "Institución para migración"},
    )
    slugs = {item.category_slug for item in SEED_REPORTS} | {"baches"}
    for slug in slugs:
        Category.objects.get_or_create(
            slug=slug,
            defaults={
                "name": f"Categoría {slug}",
                "default_institution": institution,
                "default_priority": "MEDIA",
            },
        )
    return {
        category.slug: category
        for category in Category.objects.select_related("default_institution").filter(
            slug__in=slugs
        )
    }


def test_backfill_aprobado_clasifica_16_reportes_como_cycle_zero():
    executor = _executor(MIGRATE_FROM)
    old_apps = _historical_apps(executor, MIGRATE_FROM)
    Report = old_apps.get_model("reports_app", "Report")
    History = old_apps.get_model("reports_app", "ReportStatusHistory")
    categories = _ensure_categories(old_apps)
    for item in SEED_REPORTS:
        category = categories[item.category_slug]
        Report.objects.create(
            pk=item.report_id,
            citizen=None,
            category=category,
            institution=category.default_institution,
            description=item.description,
            status=item.status,
            priority=category.default_priority,
            source="platform",
            location=Point(item.longitude, item.latitude, srid=4326),
            resolution_cycle=None,
        )

    for index in range(2):
        report = Report.objects.create(
            citizen=None,
            category=categories["baches"],
            description=f"Reporte histórico real {index}",
            status="CREADO",
            priority="MEDIA",
            source="platform",
            location=Point(-57.63 + index / 100, -25.26, srid=4326),
            resolution_cycle=None,
        )
        History.objects.create(
            report=report,
            previous_status="",
            new_status="CREADO",
            resolution_cycle=None,
        )

    _executor(MIGRATE_TO)

    from apps.reports_app.models import Report as CurrentReport
    from apps.reports_app.models import ReportStatusHistory

    assert CurrentReport.objects.count() == 16
    assert CurrentReport.objects.filter(resolution_cycle=0).count() == 16
    assert ReportStatusHistory.objects.filter(resolution_cycle=0).count() == 2


def test_backfill_rechaza_universo_desconocido_sin_escribir():
    executor = _executor(MIGRATE_FROM)
    old_apps = _historical_apps(executor, MIGRATE_FROM)
    Report = old_apps.get_model("reports_app", "Report")
    category = _ensure_categories(old_apps)["baches"]
    unknown = Report.objects.create(
        citizen=None,
        category=category,
        description="Universo nuevo no autorizado",
        status="CLASIFICADO",
        priority="MEDIA",
        source="platform",
        location=Point(-57.63, -25.26, srid=4326),
        resolution_cycle=None,
    )

    try:
        with pytest.raises(RuntimeError, match="aborted before writes"):
            _executor(MIGRATE_TO)
        unknown.refresh_from_db()
        assert unknown.resolution_cycle is None
    finally:
        Report.objects.filter(pk=unknown.pk).delete()
        _executor(MIGRATE_TO)


def test_classifier_identifica_seed_alterado_como_inconsistente():
    migration = import_module("apps.reports_app.migrations.0004_backfill_resolution_cycle")
    report = type(
        "ReportSnapshot",
        (),
        {
            "pk": next(iter(migration.SEED_IDS)),
            "citizen_id": None,
            "status": "CREADO",
            "validated_at": None,
            "validated_by_id": None,
            "resolved_at": None,
            "description": "contenido alterado",
        },
    )()

    assert migration._classify(report, [], [], []) == "INCONSISTENT"
