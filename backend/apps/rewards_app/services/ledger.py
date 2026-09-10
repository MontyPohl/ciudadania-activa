"""Contrato de dominio entre `reports_app` y `rewards_app` (§8 del plan).

El Sprint 2 declaró las funciones iniciales para fijar el contrato: cada app invoca
el ledger dentro de su propia `transaction.atomic()` y nunca escribe directamente
sus modelos. Los Sprints 6, 7 y 9 implementan creación, confirmación, validación y
rechazo; las funciones de sprints posteriores siguen siendo declarativas.

Reglas económicas que estas funciones implementarán en su sprint (§6.5, ADR-0004):

===================== ============ ============ =============== =======
`event_type`          impact_delta credit_delta estado inicial   sprint
===================== ============ ============ =============== =======
`REPORT_CREATED`      +20          +20          `PENDIENTE`     S6
`CONFIRMATION_CREATED`+10          +10          `PENDIENTE`     S7
`VALIDATION_BONUS`    +30          +30          `ACREDITADO`    S9
`RESOLUTION_BONUS`    +50          +50          `ACREDITADO`    S10
`REDEMPTION`          0            −1.000       `ACREDITADO`    S12
`REVERSAL`            negativo     negativo     `REVERTIDO`     S10
===================== ============ ============ =============== =======

Ninguna escritura del ledger puede delegarse a Celery: son síncronas y
transaccionales (§6.3 del plan, ADR-0009).
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q
from django.utils import timezone


class LedgerInvariantError(RuntimeError):
    """El agregado económico no coincide con el estado que exige el dominio."""


@dataclass(frozen=True)
class ValidationLedgerResult:
    credited_transactions: tuple
    validation_bonus: object | None


@dataclass(frozen=True)
class RejectionLedgerResult:
    voided_transactions: tuple


@dataclass(frozen=True)
class ResolutionLedgerResult:
    resolution_bonus: object | None


@dataclass(frozen=True)
class ReversalLedgerResult:
    reversed_transactions: tuple
    reversal_transactions: tuple
    voided_transactions: tuple


def on_report_created(report):
    """Registra `REPORT_CREATED` (+20/+20, `PENDIENTE`) para el autor. Sprint 6."""
    from apps.rewards_app.enums import RewardEventType, RewardTxStatus
    from apps.rewards_app.models import RewardTransaction

    if report.citizen_id is None:
        raise ValueError("REPORT_CREATED exige un reporte con ciudadano.")

    return RewardTransaction.objects.create(
        user=report.citizen,
        report=report,
        event_type=RewardEventType.REPORT_CREATED,
        impact_delta=20,
        credit_delta=20,
        status=RewardTxStatus.PENDIENTE,
    )


def on_confirmation_created(confirmation):
    """Registra los 10/10 según la validación vigente del reporte bloqueado."""
    from apps.rewards_app.enums import RewardEventType, RewardTxStatus
    from apps.rewards_app.models import RewardTransaction

    report = confirmation.report
    if report.validated_at is not None and report.institution_id is None:
        raise LedgerInvariantError("Un reporte validado debe tener institución definitiva.")

    accredited = report.validated_at is not None
    if accredited and report.resolution_cycle < 1:
        raise LedgerInvariantError("Un reporte validado debe tener un ciclo vigente.")
    return RewardTransaction.objects.create(
        user=confirmation.citizen,
        confirmation=confirmation,
        institution=report.institution if accredited else None,
        event_type=RewardEventType.CONFIRMATION_CREATED,
        impact_delta=10,
        credit_delta=10,
        status=RewardTxStatus.ACREDITADO if accredited else RewardTxStatus.PENDIENTE,
        credited_at=confirmation.created_at if accredited else None,
        resolution_cycle=report.resolution_cycle if accredited else None,
    )


def on_report_validated(report, institution, actor, operation_at):
    """Acredita participaciones pendientes e inserta el bonus humano de validación."""
    from apps.rewards_app.enums import RewardEventType, RewardTxStatus
    from apps.rewards_app.models import RewardTransaction

    if operation_at is None:
        raise LedgerInvariantError("La acreditación exige un instante de operación.")
    cycle = report.resolution_cycle + 1
    if cycle < 1:
        raise LedgerInvariantError("La validación debe iniciar un ciclo positivo.")

    transactions = tuple(
        _participation_transactions(report)
        .filter(status=RewardTxStatus.PENDIENTE)
        .select_related("confirmation")
        .order_by("created_at", "id")
    )
    report_created = tuple(
        reward for reward in transactions if reward.event_type == RewardEventType.REPORT_CREATED
    )

    if report.citizen_id is not None and cycle == 1:
        if len(report_created) != 1:
            raise LedgerInvariantError(
                "Un reporte con autor debe tener un REPORT_CREATED pendiente único."
            )
        created = report_created[0]
        if (
            created.user_id != report.citizen_id
            or created.impact_delta != 20
            or created.credit_delta != 20
        ):
            raise LedgerInvariantError("El REPORT_CREATED no coincide con el autor o su valor.")
    elif report_created:
        if report.citizen_id is None:
            raise LedgerInvariantError(
                "Un reporte sin autor no puede tener REPORT_CREATED pendiente."
            )
        raise LedgerInvariantError("Una revalidación no reactiva REPORT_CREATED histórico.")

    for reward in transactions:
        _validate_participation_transaction(reward)

    if RewardTransaction.objects.filter(
        report=report,
        event_type=RewardEventType.VALIDATION_BONUS,
        resolution_cycle=cycle,
    ).exists():
        raise LedgerInvariantError("El ciclo ya tiene un VALIDATION_BONUS.")

    for reward in transactions:
        reward.status = RewardTxStatus.ACREDITADO
        reward.institution = institution
        reward.credited_at = operation_at
        reward.resolution_cycle = cycle
        reward.save(
            update_fields=[
                "status",
                "institution",
                "credited_at",
                "resolution_cycle",
                "updated_at",
            ]
        )

    bonus = None
    if report.citizen_id is not None:
        bonus = RewardTransaction.objects.create(
            user=report.citizen,
            report=report,
            institution=institution,
            event_type=RewardEventType.VALIDATION_BONUS,
            impact_delta=30,
            credit_delta=30,
            status=RewardTxStatus.ACREDITADO,
            credited_at=operation_at,
            resolution_cycle=cycle,
        )

    return ValidationLedgerResult(
        credited_transactions=transactions,
        validation_bonus=bonus,
    )


def on_report_rejected(report, operation_at):
    """Anula participaciones pendientes; nunca compensa ni revierte acreditaciones."""
    from apps.rewards_app.enums import RewardTxStatus

    if operation_at is None:
        raise LedgerInvariantError("La anulación exige un instante de operación.")

    related = tuple(
        _participation_transactions(report)
        .select_related("confirmation")
        .order_by("created_at", "id")
    )
    if any(reward.status == RewardTxStatus.ACREDITADO for reward in related):
        raise LedgerInvariantError("El rechazo S9 no puede revertir transacciones acreditadas.")

    pending = tuple(reward for reward in related if reward.status == RewardTxStatus.PENDIENTE)
    for reward in pending:
        _validate_participation_transaction(reward)
        reward.status = RewardTxStatus.ANULADO
        reward.voided_at = operation_at
        reward.save(update_fields=["status", "voided_at", "updated_at"])

    return RejectionLedgerResult(voided_transactions=pending)


def _participation_transactions(report):
    from apps.rewards_app.enums import RewardEventType
    from apps.rewards_app.models import RewardTransaction

    return RewardTransaction.objects.select_for_update(of=("self",)).filter(
        Q(event_type=RewardEventType.REPORT_CREATED, report=report)
        | Q(
            event_type=RewardEventType.CONFIRMATION_CREATED,
            confirmation__report=report,
        )
    )


def _validate_participation_transaction(reward) -> None:
    from apps.rewards_app.enums import RewardEventType

    if reward.event_type == RewardEventType.REPORT_CREATED:
        if reward.impact_delta == 20 and reward.credit_delta == 20:
            return
        raise LedgerInvariantError("REPORT_CREATED debe conservar su valor 20/20.")

    confirmation = reward.confirmation
    if (
        confirmation is not None
        and reward.user_id == confirmation.citizen_id
        and reward.impact_delta == 10
        and reward.credit_delta == 10
    ):
        return
    raise LedgerInvariantError("CONFIRMATION_CREATED no coincide con su confirmación o valor.")


def on_report_resolved(report, actor):
    """Inserta una sola vez el bonus de resolución del ciclo vigente."""
    from apps.rewards_app.enums import RewardEventType, RewardTxStatus
    from apps.rewards_app.models import RewardTransaction

    if report.resolution_cycle < 1 or report.institution_id is None:
        raise LedgerInvariantError("La resolución exige ciclo e institución vigentes.")
    existing = RewardTransaction.objects.filter(
        report=report,
        event_type=RewardEventType.RESOLUTION_BONUS,
        resolution_cycle=report.resolution_cycle,
    ).first()
    if existing is not None:
        raise LedgerInvariantError("El ciclo ya tiene un RESOLUTION_BONUS.")

    bonus = None
    if report.citizen_id is not None:
        bonus = RewardTransaction.objects.create(
            user=report.citizen,
            report=report,
            institution=report.institution,
            event_type=RewardEventType.RESOLUTION_BONUS,
            impact_delta=50,
            credit_delta=50,
            status=RewardTxStatus.ACREDITADO,
            credited_at=timezone.now(),
            resolution_cycle=report.resolution_cycle,
        )
    return ResolutionLedgerResult(resolution_bonus=bonus)


def revert_validation(report, actor, reason):
    """Revierte efectos del ciclo sin borrar ni modificar deltas históricos."""
    from apps.rewards_app.enums import RewardEventType, RewardTxStatus
    from apps.rewards_app.models import RewardTransaction

    if report.resolution_cycle < 1:
        raise LedgerInvariantError("La reversión exige un ciclo validado.")
    operation_at = timezone.now()
    related = tuple(
        RewardTransaction.objects.select_for_update(of=("self",))
        .filter(
            Q(report=report) | Q(confirmation__report=report),
            resolution_cycle=report.resolution_cycle,
        )
        .exclude(event_type__in=[RewardEventType.REDEMPTION, RewardEventType.REVERSAL])
        .order_by("created_at", "id")
    )
    reversed_transactions = tuple(
        reward for reward in related if reward.status == RewardTxStatus.ACREDITADO
    )
    voided_transactions = tuple(
        reward for reward in related if reward.status == RewardTxStatus.PENDIENTE
    )
    reversals = []
    for reward in reversed_transactions:
        reward.status = RewardTxStatus.REVERTIDO
        reward.reversed_at = operation_at
        reward.save(update_fields=["status", "reversed_at", "updated_at"])
        reversals.append(
            RewardTransaction.objects.create(
                user=reward.user,
                report=report,
                institution=reward.institution,
                event_type=RewardEventType.REVERSAL,
                impact_delta=-reward.impact_delta,
                credit_delta=-reward.credit_delta,
                status=RewardTxStatus.REVERTIDO,
                related_transaction=reward,
                reversed_at=operation_at,
                resolution_cycle=report.resolution_cycle,
            )
        )

    for reward in voided_transactions:
        reward.status = RewardTxStatus.ANULADO
        reward.voided_at = operation_at
        reward.save(update_fields=["status", "voided_at", "updated_at"])

    return ReversalLedgerResult(
        reversed_transactions=reversed_transactions,
        reversal_transactions=tuple(reversals),
        voided_transactions=voided_transactions,
    )
