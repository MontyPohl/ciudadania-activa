"""Carreras S10 reales sobre PostgreSQL READ COMMITTED.

Cada operación usa una conexión independiente. El ``Barrier`` sincroniza el inicio y
los servicios de dominio deciden el orden efectivo mediante sus bloqueos de fila.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections
from django.utils import timezone
from rest_framework.test import APIClient

from apps.admin_app.models import AuditLog, Category, Institution, InstitutionOperator
from apps.auth_app.enums import UserRole
from apps.core.enums import ReportStatus
from apps.notifications_app.models import Notification
from apps.reports_app.models import Report, ReportStatusHistory, ResolutionEvidence
from apps.reports_app.services import (
    auto_close,
    lifecycle,
    resolution_evidence,
    reversal,
    validation,
)
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import RewardTransaction
from apps.rewards_app.services import ledger

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def s10_context():
    institution = Institution.objects.create(
        name="Institución de concurrencia S10",
        slug="institucion-concurrencia-s10",
    )
    category = Category.objects.create(
        name="Categoría de concurrencia S10",
        slug="categoria-concurrencia-s10",
        default_institution=institution,
    )
    users = get_user_model().objects
    citizen = users.create_user(email="autor-s10-concurrencia@example.test")
    operator = users.create_user(
        email="operador-s10-concurrencia@example.test",
        role=UserRole.OPERATOR,
    )
    secondary_operator = users.create_user(
        email="operador-secundario-s10-concurrencia@example.test",
        role=UserRole.OPERATOR,
    )
    admin = users.create_user(
        email="admin-s10-concurrencia@example.test",
        role=UserRole.ADMIN,
    )
    InstitutionOperator.objects.create(
        user=operator,
        institution=institution,
        active=True,
    )
    InstitutionOperator.objects.create(
        user=secondary_operator,
        institution=institution,
        active=True,
    )
    report = Report.objects.create(
        citizen=citizen,
        category=category,
        description="Reporte aislado para concurrencia S10.",
        location=Point(-57.6359, -25.2637, srid=4326),
    )
    return SimpleNamespace(
        report=report,
        operator=operator,
        secondary_operator=secondary_operator,
        admin=admin,
        institution=institution,
    )


@pytest.fixture
def report(s10_context):
    return s10_context.report


@pytest.fixture
def operator_user(s10_context):
    return s10_context.operator


@pytest.fixture
def secondary_operator(s10_context):
    return s10_context.secondary_operator


@pytest.fixture
def admin_user(s10_context):
    return s10_context.admin


@pytest.fixture
def institution(s10_context):
    return s10_context.institution


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


def _transition(report, operator_user, institution, target_status):
    return lifecycle.transition_report_status(
        report_id=report.pk,
        operator_id=operator_user.pk,
        institution_id=institution.pk,
        target_status=target_status,
        expected_cycle=1,
    )


def _prepare_in_process(report, operator_user, institution):
    ledger.on_report_created(report)
    validation.validate_report(
        report_id=report.pk,
        operator=operator_user,
        institution=institution,
    )
    report.refresh_from_db()
    _transition(report, operator_user, institution, ReportStatus.EN_PROCESO)
    report.refresh_from_db()
    return report


def _persist_evidence(report, operator_user):
    return ResolutionEvidence.objects.create(
        report=report,
        description="Trabajo concluido para la prueba concurrente.",
        evidence_url=f"resolution-evidence/{report.pk}/1/{uuid.uuid4()}.jpg",
        uploaded_by=operator_user,
        resolution_cycle=1,
        idempotency_key=uuid.uuid4(),
        content_hash="e" * 64,
    )


def _prepare_resolved(report, operator_user, institution):
    _prepare_in_process(report, operator_user, institution)
    _persist_evidence(report, operator_user)
    _transition(report, operator_user, institution, ReportStatus.RESUELTO)
    report.refresh_from_db()
    return report


def _duplicate_resolution(report, operator_user, secondary_operator, institution):
    _prepare_in_process(report, operator_user, institution)
    _persist_evidence(report, operator_user)

    def resolve_as(operator):
        return _transition(report, operator, institution, ReportStatus.RESUELTO).changed

    outcomes = _run_concurrently(
        lambda: resolve_as(operator_user),
        lambda: resolve_as(secondary_operator),
    )
    report.refresh_from_db()
    assert sorted(outcomes) == [False, True]
    assert report.status == ReportStatus.RESUELTO
    return report


def test_race_1_evidence_and_resolution_serializes_on_report(
    report,
    operator_user,
    secondary_operator,
    institution,
    storage_config,
    fake_storage,
    jpeg_bytes,
):
    _prepare_in_process(report, operator_user, institution)
    idempotency_key = uuid.uuid4()

    def add_evidence():
        uploaded = SimpleUploadedFile("evidencia.jpg", jpeg_bytes, content_type="image/jpeg")
        result = resolution_evidence.create_resolution_evidence(
            report_id=report.pk,
            operator_id=operator_user.pk,
            institution_id=institution.pk,
            expected_cycle=1,
            idempotency_key=idempotency_key,
            description="Reparación terminada bajo carrera real.",
            uploaded_file=uploaded,
        )
        return result.created

    def resolve():
        try:
            return _transition(
                report,
                secondary_operator,
                institution,
                ReportStatus.RESUELTO,
            ).changed
        except lifecycle.ResolutionEvidenceRequired:
            return "EVIDENCE_REQUIRED"

    evidence_result, resolution_result = _run_concurrently(add_evidence, resolve)

    report.refresh_from_db()
    assert evidence_result is True
    assert resolution_result in {True, "EVIDENCE_REQUIRED"}
    assert ResolutionEvidence.objects.filter(report=report, resolution_cycle=1).count() == 1
    assert AuditLog.objects.filter(action="report.resolution_evidence_created").count() == 1
    assert len(fake_storage.uploads) == 1
    assert fake_storage.deletes == []
    if resolution_result is True:
        assert report.status == ReportStatus.RESUELTO
        assert (
            RewardTransaction.objects.filter(
                report=report,
                event_type=RewardEventType.RESOLUTION_BONUS,
                resolution_cycle=1,
            ).count()
            == 1
        )
    else:
        assert report.status == ReportStatus.EN_PROCESO
        assert not RewardTransaction.objects.filter(
            report=report,
            event_type=RewardEventType.RESOLUTION_BONUS,
        ).exists()


def test_race_2_resolution_and_auto_close_are_equivalent_to_a_serial_order(
    report,
    operator_user,
    institution,
):
    _prepare_in_process(report, operator_user, institution)
    _persist_evidence(report, operator_user)
    operation_at = timezone.now()

    def resolve():
        return _transition(report, operator_user, institution, ReportStatus.RESUELTO).changed

    def automatically_close():
        return auto_close.close_report(report_id=report.pk, operation_at=operation_at).changed

    resolved, automatically_closed = _run_concurrently(resolve, automatically_close)

    report.refresh_from_db()
    assert resolved is True
    assert automatically_closed is False
    assert report.status == ReportStatus.RESUELTO
    assert (
        ReportStatusHistory.objects.filter(
            report=report,
            new_status=ReportStatus.RESUELTO,
            resolution_cycle=1,
        ).count()
        == 1
    )
    assert AuditLog.objects.filter(action="report.resolved").count() == 1
    assert (
        RewardTransaction.objects.filter(
            report=report,
            event_type=RewardEventType.RESOLUTION_BONUS,
            resolution_cycle=1,
        ).count()
        == 1
    )
    assert not ReportStatusHistory.objects.filter(
        report=report,
        new_status=ReportStatus.CERRADO,
        resolution_cycle=1,
    ).exists()
    assert not AuditLog.objects.filter(action="report.closed").exists()


def test_race_3_manual_and_auto_close_create_one_closure(
    report,
    operator_user,
    institution,
):
    _prepare_resolved(report, operator_user, institution)
    operation_at = timezone.now()
    report.resolved_at = operation_at - timedelta(hours=73)
    report.save(update_fields=["resolved_at", "updated_at"])
    # El armado avisó la validación y la resolución (S13); ningún cierre avisa.
    avisos_previos = Notification.objects.count()

    def manually_close():
        return _transition(report, operator_user, institution, ReportStatus.CERRADO).changed

    def automatically_close():
        return auto_close.close_report(report_id=report.pk, operation_at=operation_at).changed

    outcomes = _run_concurrently(manually_close, automatically_close)

    report.refresh_from_db()
    assert sorted(outcomes) == [False, True]
    assert report.status == ReportStatus.CERRADO
    assert (
        ReportStatusHistory.objects.filter(
            report=report,
            new_status=ReportStatus.CERRADO,
            resolution_cycle=1,
        ).count()
        == 1
    )
    assert AuditLog.objects.filter(action="report.closed").count() == 1
    assert Notification.objects.count() == avisos_previos


def test_race_4_reversal_and_revalidation_preserve_cycle_monotonicity(
    report,
    operator_user,
    institution,
    admin_user,
):
    ledger.on_report_created(report)
    validation.validate_report(
        report_id=report.pk,
        operator=operator_user,
        institution=institution,
    )
    report.refresh_from_db()

    def reverse():
        return reversal.revert_report_validation(
            report_id=report.pk,
            admin_id=admin_user.pk,
            expected_cycle=1,
            reason="Reversión concurrente con fundamento válido.",
        ).changed

    def revalidate():
        user = get_user_model().objects.get(pk=operator_user.pk)
        client = APIClient()
        client.force_authenticate(user)
        response = client.post(
            f"/api/v1/operator/reports/{report.pk}/validate/" f"?institution_id={institution.pk}",
            {},
            format="json",
        )
        if response.status_code == 200:
            return "REVALIDATED"
        assert response.status_code == 409
        return response.json()["code"]

    reversed_report, validation_outcome = _run_concurrently(reverse, revalidate)

    report.refresh_from_db()
    assert reversed_report is True
    assert validation_outcome in {"REVALIDATED", validation.ALREADY_VALIDATED}
    if validation_outcome == "REVALIDATED":
        assert (report.status, report.resolution_cycle) == (ReportStatus.ASIGNADO, 2)
    else:
        assert (report.status, report.resolution_cycle) == (ReportStatus.CLASIFICADO, 1)
    assert (
        ReportStatusHistory.objects.filter(
            report=report,
            new_status=ReportStatus.CLASIFICADO,
            resolution_cycle=1,
        ).count()
        == 1
    )
    assert AuditLog.objects.filter(action="report.validation_reverted").count() == 1
    assert (
        RewardTransaction.objects.filter(
            report=report,
            event_type=RewardEventType.REVERSAL,
            resolution_cycle=1,
        ).count()
        == 2
    )
    assert RewardTransaction.objects.filter(
        report=report,
        event_type=RewardEventType.VALIDATION_BONUS,
        resolution_cycle=2,
    ).count() == int(report.resolution_cycle == 2)


def test_race_5_reversal_and_close_end_reversed_without_duplicate_effects(
    report,
    operator_user,
    institution,
    admin_user,
):
    _prepare_resolved(report, operator_user, institution)

    def reverse():
        return reversal.revert_report_validation(
            report_id=report.pk,
            admin_id=admin_user.pk,
            expected_cycle=1,
            reason="Reversión concurrente frente al cierre manual.",
        ).changed

    def close():
        try:
            return _transition(report, operator_user, institution, ReportStatus.CERRADO).changed
        except lifecycle.ReportStateNotAllowed:
            return "STATE_NOT_ALLOWED"

    reversed_report, close_outcome = _run_concurrently(reverse, close)

    report.refresh_from_db()
    assert reversed_report is True
    assert close_outcome in {True, "STATE_NOT_ALLOWED"}
    assert report.status == ReportStatus.CLASIFICADO
    assert report.resolution_cycle == 1
    assert (
        ReportStatusHistory.objects.filter(
            report=report,
            new_status=ReportStatus.CLASIFICADO,
            resolution_cycle=1,
        ).count()
        == 1
    )
    assert AuditLog.objects.filter(action="report.validation_reverted").count() == 1
    assert ReportStatusHistory.objects.filter(
        report=report,
        new_status=ReportStatus.CERRADO,
        resolution_cycle=1,
    ).count() == int(close_outcome is True)


def test_race_6_duplicate_resolution_has_one_state_transition(
    report,
    operator_user,
    secondary_operator,
    institution,
):
    _duplicate_resolution(report, operator_user, secondary_operator, institution)

    assert (
        ReportStatusHistory.objects.filter(
            report=report,
            new_status=ReportStatus.RESUELTO,
            resolution_cycle=1,
        ).count()
        == 1
    )


def test_race_7_duplicate_bonus_has_one_economic_effect(
    report,
    operator_user,
    secondary_operator,
    institution,
):
    _duplicate_resolution(report, operator_user, secondary_operator, institution)

    bonus = RewardTransaction.objects.get(
        report=report,
        event_type=RewardEventType.RESOLUTION_BONUS,
        resolution_cycle=1,
    )
    assert (bonus.impact_delta, bonus.credit_delta, bonus.status) == (
        50,
        50,
        RewardTxStatus.ACREDITADO,
    )


def test_race_8_duplicate_audit_has_one_immutable_event(
    report,
    operator_user,
    secondary_operator,
    institution,
):
    _duplicate_resolution(report, operator_user, secondary_operator, institution)

    entries = AuditLog.objects.filter(action="report.resolved")
    assert entries.count() == 1
    assert entries.get().payload["cycle"] == 1
