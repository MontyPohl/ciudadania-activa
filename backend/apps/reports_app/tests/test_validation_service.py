import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.db import connections
from django.utils import timezone

from apps.admin_app.models import AuditLog, Category, Institution
from apps.ai_client_app.models import AiClassification
from apps.auth_app.enums import UserRole
from apps.core.enums import RejectionReason, ReportStatus
from apps.reports_app.models import Report, ReportConfirmation, ReportStatusHistory
from apps.reports_app.services import confirmations, validation
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import RewardTransaction
from apps.rewards_app.services import ledger

pytestmark = pytest.mark.django_db


def _creation_reward(report):
    return ledger.on_report_created(report)


def _confirmation_reward(report, citizen):
    confirmation = ReportConfirmation.objects.create(report=report, citizen=citizen)
    return confirmation, ledger.on_confirmation_created(confirmation)


def _report_without_author(category):
    return Report.objects.create(
        citizen=None,
        category=category,
        description="Reporte sin autor verificable.",
        location=Point(-57.63, -25.26, srid=4326),
    )


@pytest.mark.parametrize("source_status", [ReportStatus.CREADO, ReportStatus.CLASIFICADO])
def test_validate_aplica_reporte_historial_ledger_y_auditoria_exactos(
    source_status,
    report,
    operator_user,
    institution,
):
    report.status = source_status
    report.save(update_fields=["status", "updated_at"])
    created = _creation_reward(report)

    result = validation.validate_report(
        report_id=report.pk,
        operator=operator_user,
        institution=institution,
    )

    report.refresh_from_db()
    created.refresh_from_db()
    bonus = RewardTransaction.objects.get(event_type=RewardEventType.VALIDATION_BONUS)
    history = ReportStatusHistory.objects.get()
    entry = AuditLog.objects.get(action="report.validated")

    assert result.report == report
    assert report.status == ReportStatus.ASIGNADO
    assert report.validated_by == operator_user
    assert report.institution == institution
    assert report.validated_at is not None
    assert (created.impact_delta, created.credit_delta) == (20, 20)
    assert created.status == RewardTxStatus.ACREDITADO
    assert created.institution == institution
    assert created.credited_at == report.validated_at
    assert (bonus.impact_delta, bonus.credit_delta) == (30, 30)
    assert bonus.status == RewardTxStatus.ACREDITADO
    assert bonus.user == report.citizen
    assert bonus.report == report
    assert bonus.institution == institution
    assert bonus.credited_at == report.validated_at
    assert (history.previous_status, history.new_status) == (
        source_status,
        ReportStatus.ASIGNADO,
    )
    assert history.changed_by == operator_user
    assert history.reason == ""
    assert entry.actor_user == operator_user
    assert entry.payload == {
        "report_id": str(report.pk),
        "institution_id": str(institution.pk),
        "credited_transaction_ids": [str(created.pk)],
        "validation_bonus_transaction_id": str(bonus.pk),
    }


@pytest.mark.parametrize(
    "source_status",
    [
        ReportStatus.ASIGNADO,
        ReportStatus.EN_PROCESO,
        ReportStatus.RESUELTO,
        ReportStatus.CERRADO,
        ReportStatus.RECHAZADO,
    ],
)
def test_validate_rechaza_estados_incompatibles_sin_efectos(
    source_status,
    report,
    operator_user,
    institution,
):
    report.status = source_status
    report.institution = institution
    report.save(update_fields=["status", "institution", "updated_at"])

    with pytest.raises(validation.ReportActionConflict) as excinfo:
        validation.validate_report(
            report_id=report.pk,
            operator=operator_user,
            institution=institution,
        )

    assert excinfo.value.code == validation.REPORT_STATE_NOT_ALLOWED
    assert ReportStatusHistory.objects.count() == 0
    assert RewardTransaction.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_validate_repetido_prioriza_already_validated(
    report,
    operator_user,
    institution,
):
    _creation_reward(report)
    validation.validate_report(
        report_id=report.pk,
        operator=operator_user,
        institution=institution,
    )

    with pytest.raises(validation.ReportActionConflict) as excinfo:
        validation.validate_report(
            report_id=report.pk,
            operator=operator_user,
            institution=institution,
        )

    assert excinfo.value.code == validation.ALREADY_VALIDATED
    assert ReportStatusHistory.objects.count() == 1
    assert RewardTransaction.objects.count() == 2
    assert AuditLog.objects.filter(action="report.validated").count() == 1


@pytest.mark.parametrize("source_status", [ReportStatus.CREADO, ReportStatus.CLASIFICADO])
@pytest.mark.parametrize("reason", RejectionReason.values)
def test_reject_aplica_motivo_historial_anulacion_y_auditoria(
    source_status,
    reason,
    report,
    other_citizen,
    operator_user,
    institution,
):
    report.status = source_status
    report.save(update_fields=["status", "updated_at"])
    created = _creation_reward(report)
    _, confirmed = _confirmation_reward(report, other_citizen)

    result = validation.reject_report(
        report_id=report.pk,
        operator=operator_user,
        institution=institution,
        reason=reason,
    )

    report.refresh_from_db()
    created.refresh_from_db()
    confirmed.refresh_from_db()
    history = ReportStatusHistory.objects.get()
    entry = AuditLog.objects.get(action="report.rejected")

    assert result.report == report
    assert report.status == ReportStatus.RECHAZADO
    assert report.rejection_reason == reason
    assert report.institution_id is None
    assert created.status == confirmed.status == RewardTxStatus.ANULADO
    assert created.voided_at == confirmed.voided_at
    assert created.voided_at is not None
    assert created.institution_id is None
    assert confirmed.institution_id is None
    assert (created.impact_delta, created.credit_delta) == (20, 20)
    assert (confirmed.impact_delta, confirmed.credit_delta) == (10, 10)
    assert (history.previous_status, history.new_status) == (
        source_status,
        ReportStatus.RECHAZADO,
    )
    assert history.changed_by == operator_user
    assert history.reason == reason
    assert entry.payload == {
        "report_id": str(report.pk),
        "institution_id": str(institution.pk),
        "reason": reason,
        "voided_transaction_ids": [str(created.pk), str(confirmed.pk)],
    }
    assert not RewardTransaction.objects.filter(
        event_type=RewardEventType.VALIDATION_BONUS
    ).exists()


def test_reject_rechaza_motivo_fuera_del_dominio(
    report,
    operator_user,
    institution,
):
    with pytest.raises(validation.InvalidRejectionReason):
        validation.reject_report(
            report_id=report.pk,
            operator=operator_user,
            institution=institution,
            reason="otro",
        )

    assert Report.objects.get(pk=report.pk).status == ReportStatus.CREADO
    assert AuditLog.objects.count() == 0


def test_reject_repetido_prioriza_already_rejected(
    report,
    operator_user,
    institution,
):
    _creation_reward(report)
    validation.reject_report(
        report_id=report.pk,
        operator=operator_user,
        institution=institution,
        reason=RejectionReason.DUPLICADO,
    )

    with pytest.raises(validation.ReportActionConflict) as excinfo:
        validation.reject_report(
            report_id=report.pk,
            operator=operator_user,
            institution=institution,
            reason=RejectionReason.DUPLICADO,
        )

    assert excinfo.value.code == validation.ALREADY_REJECTED
    assert ReportStatusHistory.objects.count() == 1
    assert AuditLog.objects.filter(action="report.rejected").count() == 1


def test_reject_post_validation_exige_reversion(
    report,
    operator_user,
    institution,
):
    _creation_reward(report)
    validation.validate_report(
        report_id=report.pk,
        operator=operator_user,
        institution=institution,
    )

    with pytest.raises(validation.ReportActionConflict) as excinfo:
        validation.reject_report(
            report_id=report.pk,
            operator=operator_user,
            institution=institution,
            reason=RejectionReason.DUPLICADO,
        )

    assert excinfo.value.code == validation.VALIDATION_REVERSAL_REQUIRED
    assert ReportStatusHistory.objects.count() == 1
    assert AuditLog.objects.count() == 1


def test_validate_acredita_confirmaciones_por_confirmation_report(
    report,
    user_factory,
    operator_user,
    institution,
):
    created = _creation_reward(report)
    confirmation_rewards = []
    for index in range(3):
        citizen = user_factory(email=f"confirmador-{index}@example.test")
        _, reward = _confirmation_reward(report, citizen)
        confirmation_rewards.append(reward)

    validation.validate_report(
        report_id=report.pk,
        operator=operator_user,
        institution=institution,
    )

    created.refresh_from_db()
    assert created.status == RewardTxStatus.ACREDITADO
    for reward in confirmation_rewards:
        reward.refresh_from_db()
        assert reward.report_id is None
        assert reward.status == RewardTxStatus.ACREDITADO
        assert reward.institution == institution


def test_validate_reporte_sin_autor_solo_acredita_confirmadores(
    category,
    other_citizen,
    operator_user,
    institution,
):
    report = _report_without_author(category)
    _, confirmed = _confirmation_reward(report, other_citizen)

    validation.validate_report(
        report_id=report.pk,
        operator=operator_user,
        institution=institution,
    )

    confirmed.refresh_from_db()
    entry = AuditLog.objects.get(action="report.validated")
    assert confirmed.status == RewardTxStatus.ACREDITADO
    assert confirmed.institution == institution
    assert not RewardTransaction.objects.filter(
        event_type=RewardEventType.VALIDATION_BONUS
    ).exists()
    assert entry.payload["validation_bonus_transaction_id"] is None


def test_institucion_inactiva_falla_cerrado_para_ambas_acciones(
    report,
    operator_user,
    institution,
):
    institution.is_active = False
    institution.save(update_fields=["is_active", "updated_at"])

    for action, kwargs in [
        (validation.validate_report, {}),
        (validation.reject_report, {"reason": RejectionReason.DUPLICADO}),
    ]:
        with pytest.raises(validation.InactiveInstitution):
            action(
                report_id=report.pk,
                operator=operator_user,
                institution=institution,
                **kwargs,
            )

    assert ReportStatusHistory.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_scope_persistido_debe_coincidir(
    report,
    operator_user,
    institution,
):
    other = Institution.objects.get(slug="ande")
    report.institution = other
    report.save(update_fields=["institution", "updated_at"])

    with pytest.raises(validation.ReportInstitutionMismatch):
        validation.validate_report(
            report_id=report.pk,
            operator=operator_user,
            institution=institution,
        )


def test_scope_fallback_vigente_debe_coincidir(
    report,
    operator_user,
):
    other = Institution.objects.get(slug="ande")

    with pytest.raises(validation.ReportInstitutionMismatch):
        validation.validate_report(
            report_id=report.pk,
            operator=operator_user,
            institution=other,
        )


def test_sugerencia_ia_no_cambia_scope_de_dominio(
    report,
    operator_user,
    institution,
):
    other = Institution.objects.get(slug="ande")
    AiClassification.objects.create(
        report=report,
        suggested_category=report.category,
        suggested_institution=other,
        suggested_priority="ALTA",
        confidence="0.900",
        inconsistency_flag=False,
        justification="Sugerencia distinta.",
        model_name="fake-offline",
    )
    _creation_reward(report)

    validation.validate_report(
        report_id=report.pk,
        operator=operator_user,
        institution=institution,
    )

    report.refresh_from_db()
    assert report.institution == institution


@pytest.mark.parametrize("failure", ["ledger", "history", "audit"])
def test_validate_revierte_todo_si_falla_una_pieza(
    failure,
    monkeypatch,
    report,
    operator_user,
    institution,
):
    created = _creation_reward(report)

    def fail(*args, **kwargs):
        raise RuntimeError(f"fallo-{failure}")

    if failure == "ledger":
        monkeypatch.setattr(validation.ledger, "on_report_validated", fail)
    elif failure == "history":
        monkeypatch.setattr(ReportStatusHistory.objects, "create", fail)
    else:
        monkeypatch.setattr(validation.audit, "log", fail)

    with pytest.raises(RuntimeError, match=f"fallo-{failure}"):
        validation.validate_report(
            report_id=report.pk,
            operator=operator_user,
            institution=institution,
        )

    report.refresh_from_db()
    created.refresh_from_db()
    assert report.status == ReportStatus.CREADO
    assert report.validated_at is None
    assert report.validated_by_id is None
    assert report.institution_id is None
    assert created.status == RewardTxStatus.PENDIENTE
    assert RewardTransaction.objects.count() == 1
    assert ReportStatusHistory.objects.count() == 0
    assert AuditLog.objects.count() == 0


@pytest.mark.parametrize("failure", ["ledger", "history", "audit"])
def test_reject_revierte_todo_si_falla_una_pieza(
    failure,
    monkeypatch,
    report,
    operator_user,
    institution,
):
    created = _creation_reward(report)

    def fail(*args, **kwargs):
        raise RuntimeError(f"fallo-{failure}")

    if failure == "ledger":
        monkeypatch.setattr(validation.ledger, "on_report_rejected", fail)
    elif failure == "history":
        monkeypatch.setattr(ReportStatusHistory.objects, "create", fail)
    else:
        monkeypatch.setattr(validation.audit, "log", fail)

    with pytest.raises(RuntimeError, match=f"fallo-{failure}"):
        validation.reject_report(
            report_id=report.pk,
            operator=operator_user,
            institution=institution,
            reason=RejectionReason.DUPLICADO,
        )

    report.refresh_from_db()
    created.refresh_from_db()
    assert report.status == ReportStatus.CREADO
    assert report.rejection_reason == ""
    assert created.status == RewardTxStatus.PENDIENTE
    assert created.voided_at is None
    assert RewardTransaction.objects.count() == 1
    assert ReportStatusHistory.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_confirm_report_revierte_ante_validacion_sin_institucion(
    report,
    other_citizen,
):
    report.validated_at = timezone.now()
    report.save(update_fields=["validated_at", "updated_at"])

    with pytest.raises(ledger.LedgerInvariantError):
        confirmations.confirm_report(report_id=report.pk, citizen=other_citizen)

    assert ReportConfirmation.objects.count() == 0
    assert RewardTransaction.objects.count() == 0
    assert AuditLog.objects.count() == 0


def _thread_user(model, user_id):
    return model.objects.get(pk=user_id)


def _concurrency_context():
    institution = Institution.objects.create(
        name="Institución para concurrencia",
        slug="institucion-para-concurrencia",
    )
    category = Category.objects.create(
        name="Categoría para concurrencia",
        slug="categoria-para-concurrencia",
        default_institution=institution,
    )
    users = get_user_model().objects
    author = users.create_user(email="autor-concurrencia@example.test")
    confirmer = users.create_user(email="confirmador-concurrencia@example.test")
    operator = users.create_user(
        email="operador-concurrencia@example.test",
        role=UserRole.OPERATOR,
    )
    report = Report.objects.create(
        citizen=author,
        category=category,
        description="Reporte para probar concurrencia.",
        location=Point(-57.63, -25.26, srid=4326),
    )
    _creation_reward(report)
    return report, confirmer, operator, institution


@pytest.mark.django_db(transaction=True)
def test_validate_validate_concurrente_deja_un_solo_resultado():
    report, _, operator, institution = _concurrency_context()
    barrier = threading.Barrier(2)

    def attempt():
        connections.close_all()
        barrier.wait(timeout=5)
        try:
            validation.validate_report(
                report_id=report.pk,
                operator=_thread_user(type(operator), operator.pk),
                institution=Institution.objects.get(pk=institution.pk),
            )
        except validation.ReportActionConflict as exc:
            return exc.code
        finally:
            connections.close_all()
        return "VALIDATED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))

    assert sorted(outcomes) == [validation.ALREADY_VALIDATED, "VALIDATED"]
    assert (
        RewardTransaction.objects.filter(event_type=RewardEventType.VALIDATION_BONUS).count() == 1
    )
    assert ReportStatusHistory.objects.count() == 1
    assert AuditLog.objects.filter(action="report.validated").count() == 1


@pytest.mark.django_db(transaction=True)
def test_validate_reject_concurrente_serializa_decision():
    report, _, operator, institution = _concurrency_context()
    barrier = threading.Barrier(2)

    def attempt(action):
        connections.close_all()
        barrier.wait(timeout=5)
        try:
            kwargs = {
                "report_id": report.pk,
                "operator": _thread_user(type(operator), operator.pk),
                "institution": Institution.objects.get(pk=institution.pk),
            }
            if action == "reject":
                kwargs["reason"] = RejectionReason.DUPLICADO
                validation.reject_report(**kwargs)
            else:
                validation.validate_report(**kwargs)
        except validation.ReportActionConflict as exc:
            return exc.code
        finally:
            connections.close_all()
        return action.upper()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, ["validate", "reject"]))

    assert len([outcome for outcome in outcomes if outcome in {"VALIDATE", "REJECT"}]) == 1
    assert len([outcome for outcome in outcomes if outcome not in {"VALIDATE", "REJECT"}]) == 1
    assert ReportStatusHistory.objects.count() == 1
    assert AuditLog.objects.filter(action__in=["report.validated", "report.rejected"]).count() == 1


@pytest.mark.django_db(transaction=True)
def test_confirm_validate_concurrente_no_deja_confirmacion_pendiente():
    report, confirmer, operator, institution = _concurrency_context()
    barrier = threading.Barrier(2)

    def confirm():
        connections.close_all()
        barrier.wait(timeout=5)
        try:
            confirmations.confirm_report(
                report_id=report.pk,
                citizen=_thread_user(type(confirmer), confirmer.pk),
            )
        finally:
            connections.close_all()
        return "CONFIRMED"

    def validate():
        connections.close_all()
        barrier.wait(timeout=5)
        try:
            validation.validate_report(
                report_id=report.pk,
                operator=_thread_user(type(operator), operator.pk),
                institution=Institution.objects.get(pk=institution.pk),
            )
        finally:
            connections.close_all()
        return "VALIDATED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [pool.submit(confirm), pool.submit(validate)]
        assert sorted(future.result() for future in outcomes) == ["CONFIRMED", "VALIDATED"]

    confirmation_reward = RewardTransaction.objects.get(
        event_type=RewardEventType.CONFIRMATION_CREATED
    )
    assert confirmation_reward.status == RewardTxStatus.ACREDITADO
    assert confirmation_reward.institution == institution
    assert not RewardTransaction.objects.filter(status=RewardTxStatus.PENDIENTE).exists()
