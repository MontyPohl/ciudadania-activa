from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .constants import Decision

EXPECTED: dict[str, dict[str, set[str]]] = {
    "EX-02": {
        "audit": {"identity.submitted", "identity.approved", "report.created"},
        "reward": {"REPORT_CREATED"},
        "states": {"CREADO", "CLASIFICADO"},
    },
    "EX-03": {
        "audit": {"report.confirmed", "report.validated"},
        "reward": {"REPORT_CREATED", "CONFIRMATION_CREATED", "VALIDATION_BONUS"},
        "states": {"ASIGNADO"},
    },
    "EX-04": {
        "audit": {
            "report.processing_started",
            "report.resolution_evidence_created",
            "report.resolved",
        },
        "reward": {"RESOLUTION_BONUS"},
        "states": {"RESUELTO"},
    },
    "EX-05": {
        "audit": {"report.closed"},
        "reward": set(),
        "states": {"CERRADO"},
    },
    "EX-06": {
        "audit": {"report.validation_reverted", "report.validated", "report.resolved"},
        "reward": {"VALIDATION_BONUS", "RESOLUTION_BONUS", "REVERSAL"},
        "states": {"RESUELTO"},
    },
    "EX-07": {
        "audit": {"report.rejected"},
        "reward": {"REPORT_CREATED"},
        "states": {"RECHAZADO"},
    },
    "EX-08": {
        "audit": {
            "report.created",
            "report.confirmed",
            "report.validated",
            "report.resolved",
            "report.validation_reverted",
        },
        "reward": {
            "REPORT_CREATED",
            "CONFIRMATION_CREATED",
            "VALIDATION_BONUS",
            "RESOLUTION_BONUS",
            "REVERSAL",
        },
        "states": {"CLASIFICADO"},
    },
}


@dataclass(frozen=True, slots=True)
class TraceabilityResult:
    status: str
    complete_input: bool
    missing: tuple[str, ...]
    details: dict[str, Any]


def reconcile(export: dict[str, Any], *, complete_input: bool) -> TraceabilityResult:
    scenario_id = export.get("scenario_id")
    expected = EXPECTED.get(scenario_id)
    source = export.get("source")
    if expected is None or not isinstance(source, dict) or not complete_input:
        return TraceabilityResult(
            status=Decision.NOT_DEMONSTRATED.value,
            complete_input=False,
            missing=("complete_export",),
            details={"scenario_id": scenario_id},
        )

    audits = {row.get("action") for row in source.get("audit_logs", [])}
    rewards = {row.get("event_type") for row in source.get("reward_transactions", [])}
    states = {row.get("status") for row in source.get("reports", [])}
    missing = sorted(
        [f"audit:{value}" for value in expected["audit"] - audits]
        + [f"reward:{value}" for value in expected["reward"] - rewards]
        + ([f"state:{sorted(expected['states'])}"] if not (expected["states"] & states) else [])
    )
    return TraceabilityResult(
        status=Decision.FAIL.value if missing else Decision.PASS.value,
        complete_input=True,
        missing=tuple(missing),
        details={
            "scenario_id": scenario_id,
            "observed_audit": sorted(value for value in audits if value),
            "observed_rewards": sorted(value for value in rewards if value),
            "observed_states": sorted(value for value in states if value),
        },
    )
