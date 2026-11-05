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
from apps.reports_app.models import Report, ReportReassignment, ReportStatusHistory
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


class InvalidReassignmentTarget(ValueError):
    """La categoría de destino no existe, no está activa o no cambia de institución."""


@dataclass(frozen=True)
class ValidationResult:
    report: Report
    ledger_result: ledger.ValidationLedgerResult


@dataclass(frozen=True)
class RejectionResult:
    report: Report
    ledger_result: ledger.RejectionLedgerResult


@dataclass(frozen=True)
class ReassignmentResult:
    report: Report


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
        _notify(report, "VALIDACION_INSTITUCIONAL", institution=institution, cycle=next_cycle)

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
        _notify(report, "REPORTE_RECHAZADO", institution=institution)

    return RejectionResult(report=report, ledger_result=ledger_result)


def reassignment_targets(*, institution):
    """Categorías activas de otras instituciones activas: la institución sale de la categoría."""
    return (
        Category.objects.filter(is_active=True, default_institution__is_active=True)
        .exclude(default_institution_id=institution.pk)
        .select_related("default_institution")
        .order_by("name")
    )


def reassign_report(*, report_id, operator, institution, category_id, reason) -> ReassignmentResult:
    """Pasa un reporte sin validar a la institución de otra categoría. Posterior a S11.

    No toca estado, prioridad ni puntos, y no avisa al ciudadano (decisión de D3).
    """
    with transaction.atomic():
        report = _lock_report(report_id)
        # Mismas condiciones que el rechazo: solo mientras el reporte sigue sin decidir.
        _ensure_reject_allowed(report)
        # Un ciclo previo es una validación revertida: las métricas atribuyen ese historial a
        # la institución actual, así que traspasarlo se lo adjudicaría a otra.
        if report.resolution_cycle > 0:
            raise ReportActionConflict(REPORT_STATE_NOT_ALLOWED)

        # Filas ordenadas: dos traspasos cruzados (A→B y B→A) no se bloquean entre sí, y el
        # destino no puede desactivarse ni cambiar de institución antes del commit.
        categories = _lock_ordered(Category, {report.category_id, category_id})
        target = categories.get(category_id)
        expected_institution_id = (
            report.institution_id or categories[report.category_id].default_institution_id
        )
        target_institution_id = target.default_institution_id if target else None
        institutions = _lock_ordered(Institution, {institution.pk, target_institution_id} - {None})
        institution = institutions[institution.pk]
        if not institution.is_active:
            raise InactiveInstitution("La institución no está activa.")
        _ensure_institution_scope(
            expected_institution_id=expected_institution_id,
            institution=institution,
        )
        if (
            target is None
            or not target.is_active
            or target_institution_id == institution.pk
            or not institutions[target_institution_id].is_active
        ):
            raise InvalidReassignmentTarget("La categoría de destino no es válida.")

        previous_category_id = report.category_id
        report.category = target
        report.institution_id = target_institution_id
        report.save(update_fields=["category", "institution", "updated_at"])
        reassignment = ReportReassignment.objects.create(
            report=report,
            from_category_id=previous_category_id,
            to_category=target,
            from_institution=institution,
            to_institution_id=target_institution_id,
            reassigned_by=operator,
            reason=reason,
        )
        # El motivo queda en su tabla: la auditoría guarda solo referencias.
        audit.log(
            operator,
            "report.reassigned",
            {
                "report_id": str(report.pk),
                "reassignment_id": str(reassignment.pk),
                "from_institution_id": str(institution.pk),
                "to_institution_id": str(target_institution_id),
                "from_category_id": str(previous_category_id),
                "to_category_id": str(target.pk),
            },
            actor_type=ActorType.HUMAN,
        )
        transaction.on_commit(_invalidate_public_map_cache, robust=True)

    return ReassignmentResult(report=report)


def _notify(report, notification_type: str, **kwargs) -> None:
    """Aviso por correo al autor (S13).

    Importación diferida: `notifications_app` depende de `reports_app`.
    """
    from apps.notifications_app.services.events import notify_report_event

    notify_report_event(report, notification_type, **kwargs)


def _invalidate_public_map_cache() -> None:
    try:
        invalidate_public_reports_cache()
    except Exception:  # noqa: BLE001 - la caché nunca revierte una decisión persistida.
        logger.warning("No se pudo invalidar el mapa público después de la decisión.")


def _lock_report(report_id) -> Report:
    return Report.objects.select_for_update().get(pk=report_id)


def _lock_ordered(model, pks) -> dict:
    rows = model.objects.select_for_update().filter(pk__in=pks).order_by("pk")
    return {row.pk: row for row in rows}


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
