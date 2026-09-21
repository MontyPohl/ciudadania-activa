import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATE_FROM = [("rewards_app", "0003_enforce_resolution_cycle_constraints")]
MIGRATE_TO = [("rewards_app", "0004_enforce_reward_event_linkage")]


def _executor(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    return executor


def test_migracion_aborta_antes_de_constraints_si_hay_bonus_historico_invalido(
    citizen,
):
    executor = _executor(MIGRATE_FROM)
    old_apps = executor.loader.project_state(MIGRATE_FROM).apps
    RewardTransaction = old_apps.get_model("rewards_app", "RewardTransaction")
    invalid = RewardTransaction.objects.create(
        user_id=citizen.pk,
        report_id=None,
        institution_id=None,
        event_type="VALIDATION_BONUS",
        impact_delta=30,
        credit_delta=30,
        status="ACREDITADO",
        credited_at=timezone.now(),
        resolution_cycle=None,
    )

    try:
        with pytest.raises(RuntimeError, match="aborted before schema changes"):
            _executor(MIGRATE_TO)
        invalid.refresh_from_db()
        assert invalid.resolution_cycle is None
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM pg_constraint WHERE conname = %s",
                ["reward_tx_bonus_cycle_required"],
            )
            assert cursor.fetchone()[0] == 0
    finally:
        RewardTransaction.objects.filter(pk=invalid.pk).delete()
        _executor(MIGRATE_TO)
