"""Transiciones institucionales S10 serializadas por el Report agregado."""

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.admin_app.models import Category, Institution, InstitutionOperator
from apps.auth_app.enums import UserRole
from apps.core.enums import ActorType, ReportStatus
from apps.core.services import audit
from apps.map_app.cache import invalidate_public_reports_cache
from apps.reports_app.models import Report, ReportStatusHistory, ResolutionEvidence
from apps.rewards_app.services import ledger


class LifecycleError(Exception):
    code = "REPORT_STATE_NOT_ALLOWED"


class OperatorRequired(LifecycleError):
    code = "INSTITUTION_OPERATOR_REQUIRED"


class ReportNotFound(LifecycleError):
    code = "REPORT_NOT_FOUND"


class StaleReportCycle(LifecycleError):
    code = "STALE_REPORT_CYCLE"


class ReportStateNotAllowed(LifecycleError):
    code = "REPORT_STATE_NOT_ALLOWED"


class ResolutionEvidenceRequired(LifecycleError):
    code = "RESOLUTION_EVIDENCE_REQUIRED"


@dataclass(frozen=True)
class TransitionResult:
    report: Report
    changed: bool


ALLOWED_PREVIOUS_STATUS = {
    ReportStatus.EN_PROCESO: ReportStatus.ASIGNADO,
    ReportStatus.RESUELTO: ReportStatus.EN_PROCESO,
    ReportStatus.CERRADO: ReportStatus.RESUELTO,
}


def transition_report_status(
    *,
    report_id,
    operator_id,
    institution_id,
    target_status,
    expected_cycle,
) -> TransitionResult:
    if target_status not in ALLOWED_PREVIOUS_STATUS:
        raise ReportStateNotAllowed

    with transaction.atomic():
        report, operator = _locked_context(
            report_id=report_id,
            operator_id=operator_id,
            institution_id=institution_id,
        )
        if expected_cycle != report.resolution_cycle:
            raise StaleReportCycle
        _ensure_active_validation(report)
        if report.status == target_status:
            _ensure_noop_projection(report, target_status)
            return TransitionResult(report=report, changed=False)
        if report.status != ALLOWED_PREVIOUS_STATUS[target_status]:
            raise ReportStateNotAllowed

        if target_status == ReportStatus.EN_PROCESO:
            _start_processing(report, operator)
        elif target_status == ReportStatus.RESUELTO:
            _resolve(report, operator)
        else:
            _close(report, operator, closure_mode="manual")
        transaction.on_commit(invalidate_public_reports_cache, robust=True)
        if target_status == ReportStatus.RESUELTO:
            _notify_resolved(report)
        return TransitionResult(report=report, changed=True)


def _notify_resolved(report):
    """Aviso de resolución al autor (S13).

    Importación diferida: `notifications_app` depende de `reports_app`.
    """
    from apps.notifications_app.services.events import notify_report_event

    notify_report_event(
        report,
        "REPORTE_RESUELTO",
        institution=report.institution,
        cycle=report.resolution_cycle,
    )


def _start_processing(report, operator):
    previous_status = report.status
    report.status = ReportStatus.EN_PROCESO
    report.save(update_fields=["status", "updated_at"])
    _history(report, previous_status, ReportStatus.EN_PROCESO, operator)
    audit.log(
        operator,
        "report.processing_started",
        _transition_payload(report, previous_status, ReportStatus.EN_PROCESO),
        actor_type=ActorType.HUMAN,
    )


def _resolve(report, operator):
    evidence_ids = tuple(
        ResolutionEvidence.objects.filter(
            report=report,
            resolution_cycle=report.resolution_cycle,
        )
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )
    if not evidence_ids:
        raise ResolutionEvidenceRequired
    operation_at = timezone.now()
    ledger_result = ledger.on_report_resolved(report, operator)
    previous_status = report.status
    report.status = ReportStatus.RESUELTO
    report.resolved_at = operation_at
    report.closed_at = None
    report.save(update_fields=["status", "resolved_at", "closed_at", "updated_at"])
    _history(report, previous_status, ReportStatus.RESUELTO, operator)
    payload = _transition_payload(report, previous_status, ReportStatus.RESUELTO)
    payload.update(
        {
            "evidence_ids": [str(evidence_id) for evidence_id in evidence_ids],
            "resolution_bonus_transaction_id": (
                str(ledger_result.resolution_bonus.pk)
                if ledger_result.resolution_bonus is not None
                else None
            ),
        }
    )
    audit.log(operator, "report.resolved", payload, actor_type=ActorType.HUMAN)


def _close(report, operator, *, closure_mode):
    if report.resolved_at is None:
        raise ReportStateNotAllowed
    operation_at = timezone.now()
    previous_status = report.status
    report.status = ReportStatus.CERRADO
    report.closed_at = operation_at
    report.save(update_fields=["status", "closed_at", "updated_at"])
    _history(report, previous_status, ReportStatus.CERRADO, operator)
    payload = _transition_payload(report, previous_status, ReportStatus.CERRADO)
    payload["closure_mode"] = closure_mode
    audit.log(operator, "report.closed", payload, actor_type=ActorType.HUMAN)


def _history(report, previous_status, new_status, operator):
    ReportStatusHistory.objects.create(
        report=report,
        previous_status=previous_status,
        new_status=new_status,
        changed_by=operator,
        resolution_cycle=report.resolution_cycle,
    )


def _transition_payload(report, previous_status, new_status):
    return {
        "report_id": str(report.pk),
        "institution_id": str(report.institution_id),
        "cycle": report.resolution_cycle,
        "previous_status": previous_status,
        "new_status": new_status,
    }


def _locked_context(*, report_id, operator_id, institution_id):
    user_model = get_user_model()
    try:
        operator = user_model.objects.select_for_update().get(pk=operator_id)
    except user_model.DoesNotExist as exc:
        raise OperatorRequired from exc
    if not operator.is_active or operator.role != UserRole.OPERATOR:
        raise OperatorRequired

    membership = (
        InstitutionOperator.objects.select_for_update()
        .filter(user=operator, institution_id=institution_id, active=True)
        .first()
    )
    if membership is None:
        raise OperatorRequired
    try:
        report = Report.objects.select_for_update().get(
            pk=report_id,
            institution_id=institution_id,
        )
    except Report.DoesNotExist as exc:
        raise ReportNotFound from exc
    Category.objects.select_for_update().only("id").get(pk=report.category_id)
    institution = Institution.objects.select_for_update().only("is_active").get(pk=institution_id)
    if not institution.is_active:
        raise OperatorRequired
    return report, operator


def _ensure_active_validation(report):
    if report.validated_at is None or report.institution_id is None or report.resolution_cycle < 1:
        raise ReportStateNotAllowed


def _ensure_noop_projection(report, target_status):
    if target_status == ReportStatus.RESUELTO and report.resolved_at is None:
        raise ReportStateNotAllowed
    if target_status == ReportStatus.CERRADO and (
        report.resolved_at is None or report.closed_at is None
    ):
        raise ReportStateNotAllowed
