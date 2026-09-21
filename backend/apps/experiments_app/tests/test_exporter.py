import pytest
from django.contrib.gis.geos import Point
from django.core.management import call_command
from django.db import connection

from apps.admin_app.models import AuditLog, Category
from apps.core.enums import ActorType, ReportStatus
from apps.experiments_app.canonical import canonical_json_bytes
from apps.experiments_app.exporter import ExportError, ExportScope, ReadOnlySqlGuard, export_scope
from apps.reports_app.models import Report, ReportStatusHistory


@pytest.fixture
def export_report(db, citizen):
    call_command("seed_catalogs", verbosity=0)
    category = Category.objects.get(slug="baches")
    return Report.objects.create(
        citizen=citizen,
        category=category,
        description="SYNTHETIC S11 export test",
        location=Point(-57.6359, -25.2637, srid=4326),
    )


def test_read_only_guard_allows_select():
    guard = ReadOnlySqlGuard()
    called = []

    def execute(sql, params, many, context):
        called.append(sql)
        return "ok"

    assert guard(execute, "SELECT 1", (), False, {}) == "ok"
    assert called == ["SELECT 1"]


@pytest.mark.parametrize(
    "sql", ["INSERT INTO x VALUES (1)", "UPDATE x SET y=1", "DELETE FROM x", "TRUNCATE x"]
)
def test_read_only_guard_blocks_writes(sql):
    with pytest.raises(ExportError, match="read-only"):
        ReadOnlySqlGuard()(lambda *args: None, sql, (), False, {})


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_database_wrapper_actually_blocks_a_write():
    with connection.execute_wrapper(ReadOnlySqlGuard()), pytest.raises(ExportError):
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM reports")


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_export_is_deterministic_minimized_and_contains_required_entities(export_report, settings):
    report = export_report
    settings.S11_EXPERIMENTS_ENABLED = True
    settings.S11_ENVIRONMENT = "isolated-experiment"
    settings.S11_DATABASE_URL = "postgis://ciudadania:ciudadania@db:5432/ciudadania_s11_test"
    settings.REDIS_URL = "redis://redis:6379/11"
    settings.S11_OBJECT_ROOT = settings.REPO_ROOT / "var" / "s11-test-objects"
    ReportStatusHistory.objects.create(
        report=report,
        previous_status="",
        new_status=ReportStatus.CREADO,
        changed_by=report.citizen,
        resolution_cycle=0,
    )
    AuditLog.objects.create(
        actor_user=report.citizen,
        actor_type=ActorType.HUMAN,
        action="report.created",
        payload={
            "report_id": str(report.id),
            "Authorization": "Bearer secret.token.value",
        },
    )
    scope = ExportScope(
        run_id="s11-00000000-0000-5000-8000-000000000001",
        scenario_id="EX-02",
        report_ids=(str(report.id),),
        user_ids=(str(report.citizen_id),),
    )
    first = export_scope(scope)
    second = export_scope(scope)
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert set(first) == {
        "schema_version",
        "run_id",
        "scenario_id",
        "classification",
        "source",
        "derived",
        "interpretation",
        "redactions",
    }
    assert first["source"]["reports"][0]["id"] == report.id
    assert first["source"]["status_history"][0]["resolution_cycle"] == 0
    serialized = canonical_json_bytes(first).decode()
    assert "secret.token.value" not in serialized
    assert "location" not in first["source"]["reports"][0]
