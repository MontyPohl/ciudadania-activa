"""Validación y rechazo institucional transaccionales de Sprint 9-B1."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.admin_app.models import Category, Institution
from apps.core.enums import ActorType, RejectionReason, ReportStatus
from apps.core.services import audit
from apps.map_app.cache import invalidate_public_reports_cache
from apps.reports_app.enums import OPERABLE_REPORT_STATUSES
from apps.reports_app.models import Report, ReportStatusHistory
from apps.rewards_app.services import ledger

ALREADY_VALIDATED = "ALREADY_VALIDATED"
ALREADY_REJECTED = "ALREADY_REJECTED"
VALIDATION_REVERSAL_REQUIRED = "VALIDATION_REVERSAL_REQUIRED"
REPORT_STATE_NOT_ALLOWED = "REPORT_STATE_NOT_ALLOWED"

logger = logging.getLogger(__name__)


class ReportActionConflict(Exception):
    """El estado bloqueado del reporte no admite la acción solicitada."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class InvalidRejectionReason(ValueError):
    """El motivo no pertenece al catálogo cerrado de rechazo."""


class InactiveInstitution(ValueError):
    """Una institución inactiva no puede validar ni rechazar."""


class ReportInstitutionMismatch(ValueError):
    """El reporte ya no pertenece al scope institucional esperado."""


@dataclass(frozen=True)
class ValidationResult:
    report: Report
    ledger_result: ledger.ValidationLedgerResult


@dataclass(frozen=True)
class RejectionResult:
    report: Report
    ledger_result: ledger.RejectionLedgerResult


def validate_report(*, report_id, operator, institution) -> ValidationResult:
    """Valida una incidencia una sola vez y acredita todo su agregado económico."""
    with transaction.atomic():
        report = _lock_report(report_id)
        _ensure_validate_allowed(report)
        expected_institution_id = _lock_expected_institution_id(report)
        institution = _lock_active_institution(institution)
        _ensure_institution_scope(
            expected_institution_id=expected_institution_id,
            institution=institution,
        )

        operation_at = timezone.now()
        next_cycle = report.resolution_cycle + 1
        ledger_result = ledger.on_report_validated(
            report,
            institution,
            operator,
            operation_at,
        )

        previous_status = report.status
        report.status = ReportStatus.ASIGNADO
        report.validated_at = operation_at
        report.validated_by = operator
        report.institution = institution
        report.resolution_cycle = next_cycle
        report.resolved_at = None
        report.save(
            update_fields=[
                "status",
                "validated_at",
                "validated_by",
                "institution",
                "resolution_cycle",
                "resolved_at",
                "updated_at",
            ]
        )
        ReportStatusHistory.objects.create(
            report=report,
            previous_status=previous_status,
            new_status=ReportStatus.ASIGNADO,
            changed_by=operator,
            resolution_cycle=next_cycle,
        )
        audit.log(
            operator,
            "report.validated",
            {
                "report_id": str(report.pk),
                "institution_id": str(institution.pk),
                "cycle": next_cycle,
                "credited_transaction_ids": [
                    str(reward.pk) for reward in ledger_result.credited_transactions
                ],
                "validation_bonus_transaction_id": (
                    str(ledger_result.validation_bonus.pk)
                    if ledger_result.validation_bonus is not None
                    else None
                ),
            },
            actor_type=ActorType.HUMAN,
        )
        transaction.on_commit(_invalidate_public_map_cache, robust=True)

    return ValidationResult(report=report, ledger_result=ledger_result)


def reject_report(*, report_id, operator, institution, reason) -> RejectionResult:
    """Rechaza antes de validar y anula todos los incentivos todavía pendientes."""
    if reason not in RejectionReason.values:
        raise InvalidRejectionReason("El motivo de rechazo no pertenece al catálogo permitido.")

    with transaction.atomic():
        report = _lock_report(report_id)
        _ensure_reject_allowed(report)
        expected_institution_id = _lock_expected_institution_id(report)
        institution = _lock_active_institution(institution)
        _ensure_institution_scope(
            expected_institution_id=expected_institution_id,
            institution=institution,
        )

        operation_at = timezone.now()
        ledger_result = ledger.on_report_rejected(report, operation_at)

        previous_status = report.status
        report.status = ReportStatus.RECHAZADO
        report.rejection_reason = reason
        report.save(update_fields=["status", "rejection_reason", "updated_at"])
        ReportStatusHistory.objects.create(
            report=report,
            previous_status=previous_status,
            new_status=ReportStatus.RECHAZADO,
            changed_by=operator,
            reason=reason,
        )
        audit.log(
            operator,
            "report.rejected",
            {
                "report_id": str(report.pk),
                "institution_id": str(institution.pk),
                "reason": reason,
                "voided_transaction_ids": [
                    str(reward.pk) for reward in ledger_result.voided_transactions
                ],
            },
            actor_type=ActorType.HUMAN,
        )
        transaction.on_commit(_invalidate_public_map_cache, robust=True)

    return RejectionResult(report=report, ledger_result=ledger_result)


def _invalidate_public_map_cache() -> None:
    try:
        invalidate_public_reports_cache()
    except Exception:  # noqa: BLE001 - la caché nunca revierte una decisión persistida.
        logger.warning("No se pudo invalidar el mapa público después de la decisión.")


def _lock_report(report_id) -> Report:
    return Report.objects.select_for_update().get(pk=report_id)


def _lock_expected_institution_id(report: Report):
    if report.institution_id is not None:
        return report.institution_id
    category = (
        Category.objects.select_for_update()
        .only("default_institution_id")
        .get(pk=report.category_id)
    )
    return category.default_institution_id


def _lock_active_institution(institution) -> Institution:
    locked = Institution.objects.select_for_update().get(pk=institution.pk)
    if not locked.is_active:
        raise InactiveInstitution("La institución no está activa.")
    return locked


def _ensure_institution_scope(*, expected_institution_id, institution) -> None:
    if expected_institution_id != institution.pk:
        raise ReportInstitutionMismatch("El reporte no pertenece a la institución indicada.")


def _ensure_validate_allowed(report: Report) -> None:
    if report.validated_at is not None:
        raise ReportActionConflict(ALREADY_VALIDATED)
    if report.status not in OPERABLE_REPORT_STATUSES:
        raise ReportActionConflict(REPORT_STATE_NOT_ALLOWED)


def _ensure_reject_allowed(report: Report) -> None:
    if report.validated_at is not None:
        raise ReportActionConflict(VALIDATION_REVERSAL_REQUIRED)
    if report.status == ReportStatus.RECHAZADO:
        raise ReportActionConflict(ALREADY_REJECTED)
    if report.status not in OPERABLE_REPORT_STATUSES:
        raise ReportActionConflict(REPORT_STATE_NOT_ALLOWED)
