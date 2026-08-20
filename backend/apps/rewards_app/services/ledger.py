"""Contrato de dominio entre `reports_app` y `rewards_app` (§8 del plan).

**Esqueleto declarativo del Sprint 2.** Las cinco funciones existen para fijar el
contrato —`reports_app` nunca escribe en los modelos de `rewards_app`, sino que
invoca estas funciones dentro de su propia `transaction.atomic()`— pero ninguna
está implementada todavía, tal como exige el Sprint 2: *"esqueleto de
`rewards_app.services.ledger` con las funciones de dominio declaradas y sus
reglas todavía sin conectar a `reports_app`"*.

Cada una levanta `NotImplementedError` de forma deliberada: preferimos un fallo
explícito a una función que aparente estar implementada y devuelva `None`.

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
    raise NotImplementedError(_PENDIENTE_DE_SPRINT.format(sprint="Sprint 6"))


def on_confirmation_created(confirmation):
    """Registra `CONFIRMATION_CREATED` (+10/+10, `PENDIENTE`). Sprint 7."""
    raise NotImplementedError(_PENDIENTE_DE_SPRINT.format(sprint="Sprint 7"))


def on_report_validated(report, institution, actor):
    """Acredita las filas pendientes del reporte e inserta `VALIDATION_BONUS`. Sprint 9."""
    raise NotImplementedError(_PENDIENTE_DE_SPRINT.format(sprint="Sprint 9"))


def on_report_resolved(report, actor):
    """Inserta `RESOLUTION_BONUS` (+50/+50, `ACREDITADO`) para el autor. Sprint 10."""
    raise NotImplementedError(_PENDIENTE_DE_SPRINT.format(sprint="Sprint 10"))


def revert_validation(report, actor, reason):
    """Revierte una validación acreditada dejando rastro completo. Sprint 10."""
    raise NotImplementedError(_PENDIENTE_DE_SPRINT.format(sprint="Sprint 10"))
