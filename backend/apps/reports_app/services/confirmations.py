"""Confirmación social de reportes y reglas de elegibilidad de Sprint 7."""

from __future__ import annotations

from dataclasses import dataclass

from django.db import IntegrityError, transaction

from apps.core.enums import ActorType
from apps.core.services import audit
from apps.reports_app.models import Report, ReportConfirmation
from apps.reports_app.selectors import ACTIVE_REPORT_STATUSES
from apps.rewards_app.services import ledger

REPORT_NOT_ACTIVE = "REPORT_NOT_ACTIVE"
SELF_CONFIRMATION_NOT_ALLOWED = "SELF_CONFIRMATION_NOT_ALLOWED"
ALREADY_CONFIRMED = "ALREADY_CONFIRMED"

CONFIRMATION_CONSTRAINT = "report_confirmation_unica_por_ciudadano"


class ConfirmationConflict(Exception):
    """Conflicto esperado cuyas claves forman parte del contrato HTTP 409."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ConfirmationEligibility:
    can_confirm: bool
    code: str | None


@dataclass(frozen=True)
class ConfirmationResult:
    confirmation: ReportConfirmation
    reward: object
    confirmation_count: int


def get_confirmation_eligibility(
    *,
    report: Report,
    citizen,
    already_confirmed: bool | None = None,
) -> ConfirmationEligibility:
    """Aplica las mismas reglas y la misma prioridad que la escritura."""
    if report.status not in ACTIVE_REPORT_STATUSES:
        return ConfirmationEligibility(False, REPORT_NOT_ACTIVE)
    if report.citizen_id == citizen.pk:
        return ConfirmationEligibility(False, SELF_CONFIRMATION_NOT_ALLOWED)
    if already_confirmed is None:
        already_confirmed = ReportConfirmation.objects.filter(
            report=report,
            citizen=citizen,
        ).exists()
    if already_confirmed:
        return ConfirmationEligibility(False, ALREADY_CONFIRMED)
    return ConfirmationEligibility(True, None)


def confirm_report(*, report_id, citizen) -> ConfirmationResult:
    """Confirma un reporte bajo lock y persiste un único agregado transaccional."""
    with transaction.atomic():
        report = Report.objects.select_for_update().get(pk=report_id)
        eligibility = get_confirmation_eligibility(report=report, citizen=citizen)
        if not eligibility.can_confirm:
            raise ConfirmationConflict(eligibility.code)

        try:
            # El savepoint permite inspeccionar el constraint sin dejar rota la
            # transacción exterior. La unicidad SQL es la última defensa de carrera.
            with transaction.atomic():
                confirmation = ReportConfirmation.objects.create(
                    report=report,
                    citizen=citizen,
                )
        except IntegrityError as exc:
            if _constraint_name(exc) == CONFIRMATION_CONSTRAINT:
                raise ConfirmationConflict(ALREADY_CONFIRMED) from exc
            raise

        reward = ledger.on_confirmation_created(confirmation)
        audit.log(
            citizen,
            "report.confirmed",
            {
                "report_id": str(report.id),
                "confirmation_id": str(confirmation.id),
            },
            actor_type=ActorType.HUMAN,
        )
        confirmation_count = ReportConfirmation.objects.filter(report=report).count()

    return ConfirmationResult(
        confirmation=confirmation,
        reward=reward,
        confirmation_count=confirmation_count,
    )


def _constraint_name(exc: IntegrityError) -> str | None:
    cause = exc.__cause__
    diagnostic = getattr(cause, "diag", None)
    return getattr(diagnostic, "constraint_name", None)
