"""Reglas de canje simulado y administración de recompensas de Sprint 12."""

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from unittest.mock import Mock

import pytest
from django.contrib.gis.geos import Point
from django.db import connection, connections
from django.utils import timezone

from apps.admin_app.models import AuditLog, Category, Institution
from apps.auth_app.enums import VerificationStatus
from apps.notifications_app.enums import NotificationType
from apps.notifications_app.models import Notification
from apps.reports_app.models import Report
from apps.reports_app.services import reversal, validation
from apps.rewards_app import periods, selectors
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import Reward, RewardRedemption, RewardTransaction
from apps.rewards_app.services import ledger, redemptions, rewards

pytestmark = pytest.mark.django_db


@pytest.fixture
def institution(db):
    return Institution.objects.create(name="Institución S12", slug="institucion-s12")


@pytest.fixture
def verified_citizen(user_factory):
    return user_factory(
        email="canje-s12@example.test", verification_status=VerificationStatus.APROBADO
    )


def _credit(user, institution, amount, when):
    return RewardTransaction.objects.create(
        user=user,
        institution=institution,
        event_type=RewardEventType.REPORT_CREATED,
        impact_delta=amount,
        credit_delta=amount,
        status=RewardTxStatus.ACREDITADO,
        credited_at=when,
    )


def _run_concurrently(*operations):
    barrier = threading.Barrier(len(operations))

    def run(operation):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SHOW transaction_isolation")
                assert cursor.fetchone()[0] == "read committed"
            barrier.wait(timeout=10)
            return operation()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        futures = [pool.submit(run, operation) for operation in operations]
        return [future.result(timeout=20) for future in futures]


def test_s08_canje_valido(
    verified_citizen, institution, django_capture_on_commit_callbacks, monkeypatch
):
    now = timezone.now()
    reward = Reward.objects.create(institution=institution, name="Vale simulado")
    _credit(verified_citizen, institution, 1130, now)
    impact_before = selectors.impact_summary(user_id=verified_citizen.pk, now=now)
    enqueue = Mock()
    monkeypatch.setattr("apps.notifications_app.tasks.send_notification.delay", enqueue)

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        result = redemptions.redeem(
            user_id=verified_citizen.pk, reward_id=reward.pk, operation_at=now
        )

    assert RewardRedemption.objects.count() == 1
    assert result.transaction.redemption == result.redemption
    assert result.transaction.event_type == RewardEventType.REDEMPTION
    assert result.transaction.impact_delta == 0
    assert result.transaction.credit_delta == -1000
    assert result.transaction.status == RewardTxStatus.ACREDITADO
    assert result.transaction.credited_at == now
    assert result.redemption.credits_spent == 1000
    assert result.redemption.institution == institution
    assert result.redemption.period_month == periods.current_period(now)
    assert re.fullmatch(r"CA(-[0-9A-HJKMNP-TV-Z]{4}){4}", result.redemption.code)
    assert result.balance_after == 130
    assert (
        selectors.institution_balance(user_id=verified_citizen.pk, institution_id=institution.pk)
        == 130
    )
    assert selectors.impact_summary(user_id=verified_citizen.pk, now=now) == impact_before
    entry = AuditLog.objects.get(action="reward.redeemed")
    assert entry.actor_user == verified_citizen
    assert entry.payload == {
        "redemption_id": str(result.redemption.pk),
        "reward_id": str(reward.pk),
        "institution_id": str(institution.pk),
        "period": periods.format_period(periods.current_period(now)),
        "credits_spent": 1000,
        "balance_before": 1130,
        "balance_after": 130,
        "ledger_transaction_id": str(result.transaction.pk),
    }
    assert result.redemption.code not in str(entry.payload)
    notification = Notification.objects.get(notification_type=NotificationType.CANJE_REALIZADO)
    assert notification.user == verified_citizen
    assert notification.redemption == result.redemption
    assert len(callbacks) == 1
    enqueue.assert_called_once_with(notification.pk)


@pytest.mark.parametrize("scenario", ["mismo_mes", "otra_institucion", "mes_siguiente"])
def test_s09_limite_mensual(verified_citizen, institution, scenario):
    _, next_month = periods.month_bounds(date(2026, 10, 1))
    now = next_month - timedelta(seconds=1)
    reward = Reward.objects.create(institution=institution, name="Vale simulado")
    _credit(verified_citizen, institution, 1000, now)
    first = redemptions.redeem(user_id=verified_citizen.pk, reward_id=reward.pk, operation_at=now)

    if scenario == "mismo_mes":
        with pytest.raises(redemptions.RedemptionConflict) as error:
            redemptions.redeem(user_id=verified_citizen.pk, reward_id=reward.pk, operation_at=now)
        assert error.value.code == "MONTHLY_LIMIT_REACHED"
        assert error.value.extra == {"redemption_id": first.redemption.pk}
        assert (
            selectors.institution_balance(
                user_id=verified_citizen.pk, institution_id=institution.pk
            )
            == 0
        )
        assert RewardRedemption.objects.count() == 1
    else:
        target = institution
        operation_at = next_month
        if scenario == "otra_institucion":
            target = Institution.objects.create(name="Otra institución", slug="otra-s12")
            reward = Reward.objects.create(institution=target, name="Otro vale simulado")
            operation_at = now
        _credit(verified_citizen, target, 1000, operation_at)
        second = redemptions.redeem(
            user_id=verified_citizen.pk, reward_id=reward.pk, operation_at=operation_at
        )
        assert second.balance_after == 0
        assert second.redemption.institution == target
        assert second.redemption.period_month == (
            date(2026, 10, 1) if scenario == "otra_institucion" else date(2026, 11, 1)
        )
        assert RewardRedemption.objects.count() == 2


@pytest.mark.parametrize(
    "scenario", ["saldo_insuficiente", "recompensa_inactiva", "institucion_inactiva"]
)
def test_rechazos_simples(verified_citizen, institution, scenario):
    now = timezone.now()
    reward = Reward.objects.create(
        institution=institution, name="Vale simulado", active=scenario != "recompensa_inactiva"
    )
    if scenario == "institucion_inactiva":
        institution.is_active = False
        institution.save(update_fields=["is_active", "updated_at"])
    _credit(verified_citizen, institution, 999, now)

    with pytest.raises(redemptions.RedemptionConflict) as error:
        redemptions.redeem(user_id=verified_citizen.pk, reward_id=reward.pk, operation_at=now)

    if scenario == "saldo_insuficiente":
        assert error.value.code == "INSUFFICIENT_BALANCE"
        assert error.value.extra == {"balance": 999, "missing_credits": 1}
    else:
        assert error.value.code == "REWARD_INACTIVE"
    assert not RewardRedemption.objects.exists()


def test_s12_reversion_tras_canje(verified_citizen, institution, operator_user, admin_user):
    category = Category.objects.create(
        name="Categoría S12", slug="categoria-s12", default_institution=institution
    )
    report = Report.objects.create(
        citizen=verified_citizen,
        category=category,
        description="Reporte real para reversión tras un canje.",
        location=Point(-57.6359, -25.2637, srid=4326),
    )
    ledger.on_report_created(report)
    validation.validate_report(report_id=report.pk, operator=operator_user, institution=institution)
    now = timezone.now()
    _credit(verified_citizen, institution, 950, now)
    reward = Reward.objects.create(institution=institution, name="Vale simulado")
    result = redemptions.redeem(user_id=verified_citizen.pk, reward_id=reward.pk, operation_at=now)
    assert result.balance_after == 0
    code = result.redemption.code

    reversed_report = reversal.revert_report_validation(
        report_id=report.pk,
        admin_id=admin_user.pk,
        expected_cycle=1,
        reason="Corrección administrativa de una validación posterior al canje.",
    )

    assert reversed_report.changed is True
    result.redemption.refresh_from_db()
    assert result.redemption.code == code
    assert (
        selectors.institution_balance(user_id=verified_citizen.pk, institution_id=institution.pk)
        == -50
    )
    # El mes siguiente evita que el límite mensual tenga prioridad sobre el saldo.
    _, next_month = periods.month_bounds(periods.current_period(now))
    with pytest.raises(redemptions.RedemptionConflict) as error:
        redemptions.redeem(
            user_id=verified_citizen.pk, reward_id=reward.pk, operation_at=next_month
        )
    assert error.value.code == "INSUFFICIENT_BALANCE"
    assert error.value.extra == {"balance": -50, "missing_credits": 1050}


@pytest.mark.django_db(transaction=True)
def test_concurrencia_un_solo_canje(verified_citizen, institution):
    now = timezone.now()
    reward = Reward.objects.create(institution=institution, name="Vale simulado")
    _credit(verified_citizen, institution, 2000, now)

    def redeem():
        try:
            result = redemptions.redeem(
                user_id=verified_citizen.pk, reward_id=reward.pk, operation_at=now
            )
            return "ok", result.redemption.pk
        except redemptions.RedemptionConflict as exc:
            return "conflict", exc.code

    outcomes = _run_concurrently(redeem, redeem)

    assert sum(outcome[0] == "ok" for outcome in outcomes) == 1
    assert [outcome for outcome in outcomes if outcome[0] == "conflict"] == [
        ("conflict", "MONTHLY_LIMIT_REACHED")
    ]
    assert RewardRedemption.objects.count() == 1
    assert RewardTransaction.objects.filter(event_type=RewardEventType.REDEMPTION).count() == 1


def test_operador_crea_y_edita_recompensa(operator_user, institution):
    reward = rewards.create_reward(
        operator=operator_user,
        institution=institution,
        name="Vale simulado",
        description="Descripción inicial",
    )
    assert reward.credit_cost == 1000
    assert reward.simulated is True
    created = AuditLog.objects.get(action="reward.created")
    assert created.actor_user == operator_user
    assert created.payload == {
        "reward_id": str(reward.pk),
        "institution_id": str(institution.pk),
        "name": "Vale simulado",
        "active": True,
    }
    with pytest.raises(rewards.RewardConflict) as error:
        rewards.create_reward(
            operator=operator_user,
            institution=institution,
            name="Otra recompensa",
            description="",
        )
    assert error.value.code == "REWARD_ALREADY_EXISTS"
    assert error.value.reward_id == reward.pk

    changes = {"name": "Vale actualizado", "description": "Descripción nueva", "active": False}
    updated = rewards.update_reward(operator=operator_user, reward=reward, changes=changes)
    assert updated.name == changes["name"]
    assert updated.description == changes["description"]
    assert updated.active is False
    entry = AuditLog.objects.get(action="reward.updated")
    assert entry.actor_user == operator_user
    assert entry.payload == {
        "reward_id": str(reward.pk),
        "institution_id": str(institution.pk),
        "changes": {
            "name": ["Vale simulado", "Vale actualizado"],
            "description": ["Descripción inicial", "Descripción nueva"],
            "active": [True, False],
        },
    }
    rewards.update_reward(operator=operator_user, reward=updated, changes=changes)
    assert AuditLog.objects.filter(action="reward.updated").count() == 1
