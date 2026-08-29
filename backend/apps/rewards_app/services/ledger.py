"""Contrato de dominio entre `reports_app` y `rewards_app` (§8 del plan).

El Sprint 2 declaró las cinco funciones para fijar el contrato: cada app invoca
el ledger dentro de su propia `transaction.atomic()` y nunca escribe directamente
sus modelos. Sprint 6 implementa `on_report_created`; las funciones de sprints
posteriores continúan levantando `NotImplementedError` de forma deliberada.

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

_PENDIENTE_DE_SPRINT = (
    "El ledger de incentivos no está implementado en el Sprint 2, que solo crea el "
    "modelo de datos. Ver {sprint} en docs/PLAN_DESARROLLO.md."
)


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
    """Registra `CONFIRMATION_CREATED` (+10/+10, `PENDIENTE`). Sprint 7."""
    from apps.rewards_app.enums import RewardEventType, RewardTxStatus
    from apps.rewards_app.models import RewardTransaction

    return RewardTransaction.objects.create(
        user=confirmation.citizen,
        confirmation=confirmation,
        event_type=RewardEventType.CONFIRMATION_CREATED,
        impact_delta=10,
        credit_delta=10,
        status=RewardTxStatus.PENDIENTE,
    )


def on_report_validated(report, institution, actor):
    """Acredita las filas pendientes del reporte e inserta `VALIDATION_BONUS`. Sprint 9."""
    raise NotImplementedError(_PENDIENTE_DE_SPRINT.format(sprint="Sprint 9"))


def on_report_resolved(report, actor):
    """Inserta `RESOLUTION_BONUS` (+50/+50, `ACREDITADO`) para el autor. Sprint 10."""
    raise NotImplementedError(_PENDIENTE_DE_SPRINT.format(sprint="Sprint 10"))


def revert_validation(report, actor, reason):
    """Revierte una validación acreditada dejando rastro completo. Sprint 10."""
    raise NotImplementedError(_PENDIENTE_DE_SPRINT.format(sprint="Sprint 10"))
