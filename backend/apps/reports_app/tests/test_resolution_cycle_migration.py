from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
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
    ("reports_app", "0007_report_closed_at"),
    ("rewards_app", "0004_enforce_reward_event_linkage"),
]
PRE_EVIDENCE = [
    ("reports_app", "0005_enforce_resolution_cycles"),
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


def _create_approved_universe(apps):
    Report = apps.get_model("reports_app", "Report")
    History = apps.get_model("reports_app", "ReportStatusHistory")
    categories = _ensure_categories(apps)
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

    real_reports = []
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
        real_reports.append(report)
    return real_reports


def test_backfill_aprobado_clasifica_16_reportes_como_cycle_zero():
    executor = _executor(MIGRATE_FROM)
    old_apps = _historical_apps(executor, MIGRATE_FROM)
    _create_approved_universe(old_apps)

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
    _create_approved_universe(old_apps)
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


def test_backfill_rechaza_ownership_contradictorio_y_deja_cero_escrituras():
    executor = _executor(MIGRATE_FROM)
    old_apps = _historical_apps(executor, MIGRATE_FROM)
    Confirmation = old_apps.get_model("reports_app", "ReportConfirmation")
    History = old_apps.get_model("reports_app", "ReportStatusHistory")
    Evidence = old_apps.get_model("reports_app", "ResolutionEvidence")
    Reward = old_apps.get_model("rewards_app", "RewardTransaction")
    AuditLog = old_apps.get_model("admin_app", "AuditLog")
    first, second = _create_approved_universe(old_apps)
    user = get_user_model().objects.create_user(email="migration-owner@example.test")
    confirmation = Confirmation.objects.create(report=second, citizen_id=user.pk)
    contradictory = Reward.objects.create(
        user_id=user.pk,
        report=first,
        confirmation=confirmation,
        event_type="CONFIRMATION_CREATED",
        impact_delta=10,
        credit_delta=10,
        status="PENDIENTE",
        resolution_cycle=None,
    )
    before = {
        "reports": tuple(
            old_apps.get_model("reports_app", "Report")
            .objects.order_by("id")
            .values_list("id", "resolution_cycle")
        ),
        "histories": tuple(History.objects.order_by("id").values_list("id", "resolution_cycle")),
        "evidences": tuple(Evidence.objects.order_by("id").values_list("id", "resolution_cycle")),
        "rewards": tuple(Reward.objects.order_by("id").values_list("id", "resolution_cycle")),
        "audits": tuple(AuditLog.objects.order_by("id").values_list("id", flat=True)),
    }

    try:
        with pytest.raises(RuntimeError, match="REWARD_OWNERSHIP_CONTRADICTORY"):
            _executor(MIGRATE_TO)
        after = {
            "reports": tuple(
                old_apps.get_model("reports_app", "Report")
                .objects.order_by("id")
                .values_list("id", "resolution_cycle")
            ),
            "histories": tuple(
                History.objects.order_by("id").values_list("id", "resolution_cycle")
            ),
            "evidences": tuple(
                Evidence.objects.order_by("id").values_list("id", "resolution_cycle")
            ),
            "rewards": tuple(Reward.objects.order_by("id").values_list("id", "resolution_cycle")),
            "audits": tuple(AuditLog.objects.order_by("id").values_list("id", flat=True)),
        }
        assert after == before
    finally:
        Reward.objects.filter(pk=contradictory.pk).delete()
        Confirmation.objects.filter(pk=confirmation.pk).delete()
        get_user_model().objects.filter(pk=user.pk).delete()
        _executor(MIGRATE_TO)


def test_backfill_falla_tarde_sin_escribir_clasificaciones_previas():
    executor = _executor(MIGRATE_FROM)
    old_apps = _historical_apps(executor, MIGRATE_FROM)
    Report = old_apps.get_model("reports_app", "Report")
    History = old_apps.get_model("reports_app", "ReportStatusHistory")
    real_reports = _create_approved_universe(old_apps)
    invalid = real_reports[-1]
    history = History.objects.get(report=invalid)
    original_created_at = history.created_at
    History.objects.filter(pk=history.pk).update(
        created_at=invalid.created_at - timedelta(seconds=1)
    )

    try:
        with pytest.raises(RuntimeError, match="HISTORY_BEFORE_REPORT"):
            _executor(MIGRATE_TO)
        assert Report.objects.filter(resolution_cycle__isnull=True).count() == 16
        assert History.objects.filter(resolution_cycle__isnull=True).count() == 2
    finally:
        History.objects.filter(pk=history.pk).update(created_at=original_created_at)
        _executor(MIGRATE_TO)


def test_evidence_migration_aborts_when_historical_rows_exist():
    executor = _executor(PRE_EVIDENCE)
    old_apps = _historical_apps(executor, PRE_EVIDENCE)
    Report = old_apps.get_model("reports_app", "Report")
    Evidence = old_apps.get_model("reports_app", "ResolutionEvidence")
    category = _ensure_categories(old_apps)["baches"]
    report = Report.objects.create(
        citizen=None,
        category=category,
        description="Reporte inesperado con evidencia histórica",
        status="CREADO",
        priority="MEDIA",
        source="platform",
        location=Point(-57.63, -25.26, srid=4326),
        resolution_cycle=0,
    )
    evidence = Evidence.objects.create(
        report=report,
        description="Fila no autorizada",
        evidence_url="legacy/key.jpg",
        resolution_cycle=1,
    )

    try:
        with pytest.raises(RuntimeError, match="approved baseline has no historical evidence"):
            _executor(MIGRATE_TO)
        columns = {
            column.name
            for column in connection.introspection.get_table_description(
                connection.cursor(),
                "resolution_evidences",
            )
        }
        assert "idempotency_key" not in columns
        assert "content_hash" not in columns
    finally:
        Evidence.objects.filter(pk=evidence.pk).delete()
        Report.objects.filter(pk=report.pk).delete()
        _executor(MIGRATE_TO)
