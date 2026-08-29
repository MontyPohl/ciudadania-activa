import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from django.db import IntegrityError, connections

from apps.admin_app.models import AuditLog
from apps.ai_client_app.models import AiClassification
from apps.auth_app.enums import VerificationStatus
from apps.core.enums import ActorType, ReportStatus
from apps.notifications_app.models import Notification
from apps.reports_app.models import ReportConfirmation, ReportStatusHistory
from apps.reports_app.services import confirmations as confirmation_service
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import RewardTransaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def verified_confirmer(other_citizen):
    other_citizen.verification_status = VerificationStatus.APROBADO
    other_citizen.save(update_fields=["verification_status"])
    return other_citizen


def test_confirm_report_persiste_confirmacion_reward_y_audit_exactos(report, verified_confirmer):
    initial_status = report.status
    initial_history = ReportStatusHistory.objects.count()

    result = confirmation_service.confirm_report(report_id=report.id, citizen=verified_confirmer)

    confirmation = ReportConfirmation.objects.get()
    reward = RewardTransaction.objects.get()
    audit = AuditLog.objects.get()
    report.refresh_from_db()

    assert result.confirmation == confirmation
    assert result.reward == reward
    assert result.confirmation_count == 1
    assert confirmation.report == report
    assert confirmation.citizen == verified_confirmer
    assert reward.user == verified_confirmer
    assert reward.report is None
    assert reward.confirmation == confirmation
    assert reward.event_type == RewardEventType.CONFIRMATION_CREATED
    assert (reward.impact_delta, reward.credit_delta, reward.status) == (
        10,
        10,
        RewardTxStatus.PENDIENTE,
    )
    assert audit.actor_user == verified_confirmer
    assert audit.actor_type == ActorType.HUMAN
    assert audit.action == "report.confirmed"
    assert audit.payload == {
        "report_id": str(report.id),
        "confirmation_id": str(confirmation.id),
    }
    assert report.status == initial_status
    assert ReportStatusHistory.objects.count() == initial_history
    assert Notification.objects.count() == 0
    assert AiClassification.objects.count() == 0


@pytest.mark.parametrize("active_status", confirmation_service.ACTIVE_REPORT_STATUSES)
def test_todos_los_estados_activos_admiten_confirmacion(report, verified_confirmer, active_status):
    report.status = active_status
    report.save(update_fields=["status", "updated_at"])

    result = confirmation_service.confirm_report(
        report_id=report.id,
        citizen=verified_confirmer,
    )

    assert result.confirmation.report_id == report.id


@pytest.mark.parametrize(
    "inactive_status",
    [ReportStatus.RESUELTO, ReportStatus.CERRADO, ReportStatus.RECHAZADO],
)
def test_inactivo_tiene_prioridad_sobre_self_y_duplicate(report, citizen, inactive_status):
    report.status = inactive_status
    report.save(update_fields=["status", "updated_at"])
    ReportConfirmation.objects.create(report=report, citizen=citizen)

    with pytest.raises(confirmation_service.ConfirmationConflict) as excinfo:
        confirmation_service.confirm_report(report_id=report.id, citizen=citizen)

    assert excinfo.value.code == "REPORT_NOT_ACTIVE"


def test_self_tiene_prioridad_sobre_duplicate(report, citizen):
    ReportConfirmation.objects.create(report=report, citizen=citizen)

    with pytest.raises(confirmation_service.ConfirmationConflict) as excinfo:
        confirmation_service.confirm_report(report_id=report.id, citizen=citizen)

    assert excinfo.value.code == "SELF_CONFIRMATION_NOT_ALLOWED"


def test_duplicate_no_es_idempotente(report, verified_confirmer):
    confirmation_service.confirm_report(report_id=report.id, citizen=verified_confirmer)

    with pytest.raises(confirmation_service.ConfirmationConflict) as excinfo:
        confirmation_service.confirm_report(report_id=report.id, citizen=verified_confirmer)

    assert excinfo.value.code == "ALREADY_CONFIRMED"
    assert ReportConfirmation.objects.count() == 1
    assert RewardTransaction.objects.count() == 1
    assert AuditLog.objects.filter(action="report.confirmed").count() == 1


@pytest.mark.parametrize("failure", ["ledger", "audit"])
def test_fallo_transaccional_revierte_todo(failure, monkeypatch, report, verified_confirmer):
    def fail(*args, **kwargs):
        raise RuntimeError(f"fallo-{failure}")

    target = confirmation_service.ledger if failure == "ledger" else confirmation_service.audit
    method = "on_confirmation_created" if failure == "ledger" else "log"
    monkeypatch.setattr(target, method, fail)

    with pytest.raises(RuntimeError, match=f"fallo-{failure}"):
        confirmation_service.confirm_report(report_id=report.id, citizen=verified_confirmer)

    assert ReportConfirmation.objects.count() == 0
    assert RewardTransaction.objects.count() == 0
    assert AuditLog.objects.filter(action="report.confirmed").count() == 0


def test_integrity_error_no_relacionado_no_se_convierte_en_duplicate(
    monkeypatch, report, verified_confirmer
):
    class OtherConstraintError(Exception):
        class diag:
            constraint_name = "otro_constraint"

    def fail(*args, **kwargs):
        raise IntegrityError("otro") from OtherConstraintError()

    monkeypatch.setattr(ReportConfirmation.objects, "create", fail)

    with pytest.raises(IntegrityError):
        confirmation_service.confirm_report(report_id=report.id, citizen=verified_confirmer)


def test_integrity_error_del_constraint_se_convierte_en_duplicate(
    monkeypatch, report, verified_confirmer
):
    class DatabaseConstraintError(RuntimeError):
        pass

    database_error = DatabaseConstraintError("database constraint")
    database_error.diag = SimpleNamespace(
        constraint_name=confirmation_service.CONFIRMATION_CONSTRAINT
    )

    def fail(*args, **kwargs):
        raise IntegrityError("duplicada") from database_error

    monkeypatch.setattr(ReportConfirmation.objects, "create", fail)

    with pytest.raises(confirmation_service.ConfirmationConflict) as excinfo:
        confirmation_service.confirm_report(report_id=report.id, citizen=verified_confirmer)

    assert excinfo.value.code == "ALREADY_CONFIRMED"
    assert ReportConfirmation.objects.count() == 0
    assert RewardTransaction.objects.count() == 0
    assert AuditLog.objects.filter(action="report.confirmed").count() == 0


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_concurrencia_real_deja_un_solo_agregado(report, verified_confirmer):
    barrier = threading.Barrier(2)

    def attempt():
        connections.close_all()
        barrier.wait(timeout=5)
        try:
            confirmation_service.confirm_report(
                report_id=report.id,
                citizen=type(verified_confirmer).objects.get(pk=verified_confirmer.pk),
            )
        except confirmation_service.ConfirmationConflict as exc:
            return exc.code
        finally:
            connections.close_all()
        return "CREATED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))

    assert sorted(outcomes) == ["ALREADY_CONFIRMED", "CREATED"]
    assert ReportConfirmation.objects.count() == 1
    assert RewardTransaction.objects.count() == 1
    assert AuditLog.objects.filter(action="report.confirmed").count() == 1
