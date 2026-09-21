"""Reversión administrativa de una validación conforme a ADR-0013."""

import unicodedata
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.admin_app.models import Category, Institution
from apps.auth_app.enums import UserRole
from apps.core.enums import ActorType, ReportStatus
from apps.core.services import audit
from apps.map_app.cache import invalidate_public_reports_cache
from apps.reports_app.models import Report, ReportStatusHistory
from apps.rewards_app.services import ledger

REVERSIBLE_STATUSES = {
    ReportStatus.ASIGNADO,
    ReportStatus.EN_PROCESO,
    ReportStatus.RESUELTO,
    ReportStatus.CERRADO,
}


class ReversalError(Exception):
    code = "REPORT_STATE_NOT_ALLOWED"


class AdminRequired(ReversalError):
    code = "ADMIN_REQUIRED"


class ReportNotFound(ReversalError):
    code = "REPORT_NOT_FOUND"


class StaleReportCycle(ReversalError):
    code = "STALE_REPORT_CYCLE"


class ReportStateNotAllowed(ReversalError):
    code = "REPORT_STATE_NOT_ALLOWED"


class InvalidReversalReason(ReversalError):
    code = "INVALID_REQUEST"


@dataclass(frozen=True)
class ReversalResult:
    report: Report
    changed: bool
    ledger_result: ledger.ReversalLedgerResult | None


def normalize_reason(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidReversalReason("El motivo debe ser texto plano.")
    normalized = unicodedata.normalize("NFC", value).replace("\r\n", "\n").strip()
    if any(_forbidden_control(character) for character in normalized):
        raise InvalidReversalReason("El motivo contiene caracteres de control.")
    if not 10 <= len(normalized) <= 1000:
        raise InvalidReversalReason("El motivo debe tener entre 10 y 1000 caracteres.")
    return normalized


def revert_report_validation(
    *,
    report_id,
    admin_id,
    expected_cycle,
    reason,
) -> ReversalResult:
    normalized_reason = normalize_reason(reason)

    with transaction.atomic():
        report, admin = _locked_context(report_id=report_id, admin_id=admin_id)
        if expected_cycle != report.resolution_cycle:
            raise StaleReportCycle
        if _is_completed_reversal(report, expected_cycle):
            return ReversalResult(report=report, changed=False, ledger_result=None)
        _ensure_reversible_projection(report)

        previous_status = report.status
        ledger_result = ledger.revert_validation(report, admin, normalized_reason)
        report.status = ReportStatus.CLASIFICADO
        report.validated_at = None
        report.validated_by = None
        report.resolved_at = None
        report.closed_at = None
        report.save(
            update_fields=[
                "status",
                "validated_at",
                "validated_by",
                "resolved_at",
                "closed_at",
                "updated_at",
            ]
        )
        ReportStatusHistory.objects.create(
            report=report,
            previous_status=previous_status,
            new_status=ReportStatus.CLASIFICADO,
            changed_by=admin,
            reason=normalized_reason,
            resolution_cycle=report.resolution_cycle,
        )
        audit.log(
            admin,
            "report.validation_reverted",
            {
                "report_id": str(report.pk),
                "institution_id": str(report.institution_id),
                "cycle": report.resolution_cycle,
                "previous_status": previous_status,
                "new_status": ReportStatus.CLASIFICADO,
                "reason": normalized_reason,
                "reverted_transaction_ids": [
                    str(reward.pk) for reward in ledger_result.reversed_transactions
                ],
                "reversal_transaction_ids": [
                    str(reward.pk) for reward in ledger_result.reversal_transactions
                ],
                "voided_transaction_ids": [
                    str(reward.pk) for reward in ledger_result.voided_transactions
                ],
            },
            actor_type=ActorType.HUMAN,
        )
        transaction.on_commit(invalidate_public_reports_cache, robust=True)
        return ReversalResult(report=report, changed=True, ledger_result=ledger_result)


def _locked_context(*, report_id, admin_id):
    user_model = get_user_model()
    try:
        admin = user_model.objects.select_for_update().get(pk=admin_id)
    except user_model.DoesNotExist as exc:
        raise AdminRequired from exc
    if not admin.is_active or admin.role != UserRole.ADMIN:
        raise AdminRequired

    try:
        report = Report.objects.select_for_update().get(pk=report_id)
    except Report.DoesNotExist as exc:
        raise ReportNotFound from exc
    Category.objects.select_for_update().only("id").get(pk=report.category_id)
    if report.institution_id is not None:
        Institution.objects.select_for_update().only("id").get(pk=report.institution_id)
    return report, admin


def _is_completed_reversal(report, expected_cycle):
    if (
        report.status != ReportStatus.CLASIFICADO
        or report.validated_at is not None
        or report.validated_by_id is not None
        or report.resolved_at is not None
        or report.closed_at is not None
        or report.institution_id is None
        or expected_cycle < 1
    ):
        return False
    return ReportStatusHistory.objects.filter(
        report=report,
        previous_status__in=REVERSIBLE_STATUSES,
        new_status=ReportStatus.CLASIFICADO,
        resolution_cycle=expected_cycle,
    ).exists()


def _ensure_reversible_projection(report):
    if (
        report.status not in REVERSIBLE_STATUSES
        or report.validated_at is None
        or report.institution_id is None
        or report.resolution_cycle < 1
    ):
        raise ReportStateNotAllowed
    if report.status in {ReportStatus.ASIGNADO, ReportStatus.EN_PROCESO} and (
        report.resolved_at is not None or report.closed_at is not None
    ):
        raise ReportStateNotAllowed
    if report.status == ReportStatus.RESUELTO and (
        report.resolved_at is None or report.closed_at is not None
    ):
        raise ReportStateNotAllowed
    if report.status == ReportStatus.CERRADO and (
        report.resolved_at is None or report.closed_at is None
    ):
        raise ReportStateNotAllowed


def _forbidden_control(character):
    return character != "\n" and unicodedata.category(character) in {"Cc", "Cf"}
